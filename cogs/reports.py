"""Friend-code reports: /report -> moderator approval queue -> blacklist index.

Reports target an Among Us **friend code**, not a Discord account. Friend codes
are permanent and account-bound, so indexing a code is what actually catches a
bad actor (and every alt they have ever linked).
"""
from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from helpers import flag_suspect, is_mod, load_config, send_audit

log = logging.getLogger("aubot.reports")

# Ordered most-serious first: the whole point of this server is keeping
# predators and other bad actors out of the lobbies.
REASONS = [
    app_commands.Choice(
        name="Predatory behaviour (grooming / sexual advances)", value="predatory"
    ),
    app_commands.Choice(name="Inappropriate sexual behaviour", value="sexual"),
    app_commands.Choice(name="Harassment / bullying", value="harassment"),
    app_commands.Choice(name="Hate speech / slurs", value="hate"),
    app_commands.Choice(name="Scamming / stealing", value="scam"),
    app_commands.Choice(name="Ban evasion (rejoined on another account)", value="evasion"),
    app_commands.Choice(name="Other (explain below)", value="other"),
]

REASON_LABELS = {choice.value: choice.name for choice in REASONS}

# Labels for categories stored by older versions of the bot, so pending reports
# filed before this change still render readably.
LEGACY_REASON_LABELS = {
    "harassment": "Harassment / bullying",
    "hate": "Hate speech / slurs",
    "sexual": "Inappropriate sexual behaviour",
    "scam": "Scamming / stealing",
    "evasion": "Ban evasion (rejoined on another account)",
    "other": "Other (explain below)",
}


def reason_label(value: str) -> str:
    """Human-readable label for a stored report category."""
    return REASON_LABELS.get(value) or LEGACY_REASON_LABELS.get(value, value)


def normalize_code(raw: str) -> str:
    """Canonical form of a friend code: no spaces, uppercase."""
    return "".join(raw.split()).upper()


class ReportModal(discord.ui.Modal, title="Report a friend code"):
    def __init__(
        self, bot: commands.Bot, reason: str, target_code: str, reporter_id: int
    ) -> None:
        super().__init__()
        self.bot = bot
        self.reason = reason
        self.target_code = target_code
        self.reporter_id = reporter_id
        self.details = discord.ui.TextInput(
            label="What did they do?",
            style=discord.TextStyle.paragraph,
            placeholder="Be specific: what happened, when, in which lobby — in-game name, too.",
            max_length=1000,
            required=True,
        )
        self.evidence = discord.ui.TextInput(
            label="Evidence link(s) (optional)",
            placeholder="https://imgur.com/… or a Discord message link",
            max_length=300,
            required=False,
        )
        self.add_item(self.details)
        self.add_item(self.evidence)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return
        report_id = await self.bot.db.report_create(
            reporter_id=interaction.user.id,
            category=self.reason,
            details=str(self.details.value),
            target_user_id=None,
            target_code=self.target_code,
            evidence=str(self.evidence.value) or None,
        )
        await interaction.response.send_message(
            f"✅ Report `#{report_id}` filed for `{self.target_code}`. "
            "Moderators will review it — only approved reports enter the index.",
            ephemeral=True,
        )
        cfg = await load_config(self.bot, interaction.guild)
        channel_id = cfg.get("report_channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await send_audit(
                self.bot,
                discord.Embed(
                    title="⚠️ Report filed but no queue",
                    description=f"Report #{report_id} has nowhere to go — run `/gate-setup` "
                    "and set a report channel.",
                    color=discord.Color.orange(),
                ),
            )
            return
        await channel.send(
            embed=self._embed(report_id), view=ReportReviewView(self.bot, report_id)
        )

    def _embed(self, report_id: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"Report #{report_id} — friend code",
            description=self.details.value,
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Friend code", value=f"`{self.target_code}`", inline=True)
        embed.add_field(name="Reason", value=reason_label(self.reason), inline=True)
        if self.evidence.value:
            embed.add_field(name="Evidence", value=str(self.evidence.value)[:1000], inline=False)
        embed.set_footer(text=f"Reporter ID: {self.reporter_id}")
        return embed


class ReportReviewView(discord.ui.View):
    def __init__(self, bot: commands.Bot, report_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.report_id = report_id
        self.yes = discord.ui.Button(
            label="Approve → blacklist",
            style=discord.ButtonStyle.red,
            custom_id=f"rpt:{report_id}:approve",
        )
        self.yes.callback = self._approve
        self.no = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.grey,
            custom_id=f"rpt:{report_id}:reject",
        )
        self.no.callback = self._reject
        self.add_item(self.yes)
        self.add_item(self.no)

    async def _approve(self, interaction: discord.Interaction) -> None:
        if not is_mod(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message("Moderators only.", ephemeral=True)
            return
        report = await self.bot.db.report_get(self.report_id)
        if report is None:
            await interaction.response.send_message("Report not found.", ephemeral=True)
            return
        if report["status"] != "pending":
            await interaction.response.send_message(
                f"Already {report['status']}.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        entries: list[tuple[str, str]] = []
        if report["target_code"]:
            entries.append(("friend_code", report["target_code"]))
        # Legacy reports (filed before /report went code-only) may name a user.
        if report["target_user_id"]:
            entries.append(("discord_id", str(report["target_user_id"])))

        label = reason_label(report["category"])
        reason = f"Report #{report['id']} ({label}): {report['details'][:180]}"
        for kind, value in entries:
            await self.bot.db.blacklist_add(
                kind, value, reason,
                added_by=interaction.user.id,
                evidence=report["evidence"],
                report_id=report["id"],
            )
        await self.bot.db.report_resolve(self.report_id, "approved", interaction.user.id)
        await self.bot.db.audit(
            "BLACKLIST",
            f"Report #{self.report_id} approved by {interaction.user}; "
            f"entries: {', '.join(f'{k}={v}' for k, v in entries) or '(none)'}",
            interaction.user.id,
        )

        flagged = await self._flag_linked(interaction.guild, entries)
        await self._edit_message(interaction, approved=True, reviewer=interaction.user)
        await send_audit(
            self.bot,
            discord.Embed(
                title="REPORT APPROVED",
                description=f"Report `#{self.report_id}` approved by {interaction.user}.\n"
                f"Indexed. Flagged for review: "
                f"{', '.join(flagged) or 'nobody currently in the server'}\n"
                f"**Nothing was banned** — automated bans are off. A moderator decides "
                f"what to do next.",
                color=discord.Color.gold(),
            ),
        )
        await interaction.followup.send(
            f"Report #{self.report_id} approved and indexed. Flagged for review: "
            f"{', '.join(flagged) or 'nobody is currently in the server'}. "
            f"Nothing was banned.",
            ephemeral=True,
        )

    async def _reject(self, interaction: discord.Interaction) -> None:
        if not is_mod(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message("Moderators only.", ephemeral=True)
            return
        report = await self.bot.db.report_get(self.report_id)
        if report is None or report["status"] != "pending":
            await interaction.response.send_message("Nothing to do.", ephemeral=True)
            return
        await self.bot.db.report_resolve(self.report_id, "rejected", interaction.user.id)
        await self.bot.db.audit(
            "REPORT-REJECT", f"Report #{self.report_id} rejected by {interaction.user}",
            interaction.user.id,
        )
        await self._edit_message(interaction, approved=False, reviewer=interaction.user)
        await interaction.response.send_message(
            f"Report #{self.report_id} rejected.", ephemeral=True
        )

    async def _flag_linked(
        self,
        guild: discord.Guild | None,
        entries: list[tuple[str, str]],
    ) -> list[str]:
        """Flag everyone in the server connected to the new index entries.

        Approving a report used to ban them on the spot. It no longer bans
        anything — the accounts are flagged and warned, and a moderator acts.
        """
        if guild is None:
            return []
        flagged: list[str] = []
        ids: set[int] = {int(v) for k, v in entries if k == "discord_id"}
        for kind, value in entries:
            if kind == "friend_code":
                ids.update(await self.bot.db.members_with_code(value))
                ids.update(await self.bot.db.discord_ids_for_code(value))
        for uid in ids:
            try:
                member = guild.get_member(uid) or await guild.fetch_member(uid)
            except discord.HTTPException:
                continue
            await flag_suspect(
                self.bot,
                member,
                f"friend code indexed via report #{self.report_id}",
                guild=guild,
            )
            flagged.append(str(member))
        return flagged

    async def _edit_message(
        self, interaction: discord.Interaction, *, approved: bool, reviewer: discord.User
    ) -> None:
        if interaction.message is None:
            return
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed = embed.copy()
            embed.colour = discord.Color.red() if approved else discord.Color.dark_grey()
            embed.add_field(
                name="Resolved",
                value=f"{'Approved' if approved else 'Rejected'} by {reviewer}",
                inline=False,
            )
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        try:
            await interaction.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


class ReportCommand(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Re-register review buttons for reports that were pending when the bot restarted.
        for report in await self.bot.db.reports_pending():
            self.bot.add_view(ReportReviewView(self.bot, report["id"]))

    @app_commands.command(
        name="report", description="Report an Among Us friend code for misconduct"
    )
    @app_commands.describe(
        friend_code="The friend code you're reporting — it looks like Example#0000",
        reason="Why you're reporting it",
    )
    @app_commands.choices(reason=REASONS)
    async def report(
        self,
        interaction: discord.Interaction,
        friend_code: str,
        reason: app_commands.Choice[str],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return

        code = normalize_code(friend_code)
        if not re.fullmatch(self.bot.settings.fc_pattern, code):
            await interaction.response.send_message(
                f"`{friend_code.strip()}` doesn't look like an Among Us friend code. "
                "It should look like `Example#0000` (in-game: **Settings → Account**).",
                ephemeral=True,
            )
            return

        if code in await self.bot.db.codes_for_discord(interaction.user.id):
            await interaction.response.send_message(
                "That's your own friend code — you can't report yourself.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            ReportModal(self.bot, reason.value, code, interaction.user.id)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReportCommand(bot))
