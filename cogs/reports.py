"""User reports: /report modal -> moderator approval queue -> blacklist index."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers import is_mod, load_config, mask_code, send_audit

log = logging.getLogger("aubot.reports")

CATEGORIES = [
    app_commands.Choice(name="Harassment / bullying", value="harassment"),
    app_commands.Choice(name="Hate speech / slurs", value="hate"),
    app_commands.Choice(name="Sexual content / predation", value="sexual"),
    app_commands.Choice(name="Scamming / stealing", value="scam"),
    app_commands.Choice(name="Ban evasion (new account)", value="evasion"),
    app_commands.Choice(name="Other (explain below)", value="other"),
]


class ReportModal(discord.ui.Modal, title="Report a user"):
    def __init__(self, bot: commands.Bot, category: str, target: discord.User | None,
                 target_code: str | None, reporter_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.category = category
        self.target = target
        self.target_code = target_code
        self.reporter_id = reporter_id
        self.details = discord.ui.TextInput(
            label="What did they do?",
            style=discord.TextStyle.paragraph,
            placeholder="Be specific: what happened, when, in which lobby…",
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
            category=self.category,
            details=str(self.details.value),
            target_user_id=self.target.id if self.target else None,
            target_code=self.target_code,
            evidence=str(self.evidence.value) or None,
        )
        await interaction.response.send_message(
            f"✅ Report `#{report_id}` filed. Moderators will review it.",
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
            title=f"Report #{report_id}",
            description=self.details.value,
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Category", value=self.category, inline=True)
        if self.target:
            embed.add_field(
                name="Reported user",
                value=f"{self.target} ({self.target.id})",
                inline=True,
            )
        if self.target_code:
            embed.add_field(
                name="Friend code", value=f"`{mask_code(self.target_code)}`", inline=True
            )
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
        if report["target_user_id"]:
            entries.append(("discord_id", str(report["target_user_id"])))

        reason = f"Report #{report['id']} ({report['category']}): {report['details'][:180]}"
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

        banned = await self._ban_linked(interaction.guild, report, entries)
        await self._edit_message(interaction, approved=True, reviewer=interaction.user)
        await send_audit(
            self.bot,
            discord.Embed(
                title="REPORT APPROVED",
                description=f"Report `#{self.report_id}` approved by {interaction.user}.\n"
                f"Banned now: {banned or 'none (they may rejoin later — they are indexed)'}",
                color=discord.Color.red(),
            ),
        )
        await interaction.followup.send(
            f"Report #{self.report_id} approved. Banned: {banned or 'not currently in the server'}.",
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

    async def _ban_linked(
        self,
        guild: discord.Guild | None,
        report: dict,
        entries: list[tuple[str, str]],
    ) -> list[str]:
        """Ban everyone currently in the server connected to the new index entries."""
        if guild is None:
            return []
        banned: list[str] = []
        ids_to_ban: set[int] = {
            int(v) for k, v in entries if k == "discord_id"
        }
        for kind, value in entries:
            if kind == "friend_code":
                ids_to_ban.update(await self.bot.db.members_with_code(value))
                ids_to_ban.update(await self.bot.db.discord_ids_for_code(value))
        for uid in ids_to_ban:
            try:
                member = guild.get_member(uid) or await guild.fetch_member(uid)
            except discord.HTTPException:
                continue
            try:
                await member.ban(
                    reason=f"Blacklisted via report #{report['id']}",
                    delete_message_seconds=0,
                )
                banned.append(str(member))
            except discord.HTTPException:
                continue
        return banned

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

    @app_commands.command(name="report", description="Report a user for misconduct")
    @app_commands.describe(
        user="Discord user to report (optional if you provide friend code)",
        friend_code="Their Among Us friend code (optional if you provide user)",
        category="Type of misconduct",
    )
    @app_commands.choices(category=CATEGORIES)
    async def report(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        user: discord.User | None = None,
        friend_code: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return
        if user is None and not friend_code:
            await interaction.response.send_message(
                "Give me at least a **user** or an **Among Us friend code** to report.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ReportModal(self.bot, category.value, user,
                        "".join(friend_code.split()).upper() if friend_code else None,
                        interaction.user.id)
        )


@app_commands.context_menu(name="Report User")
async def report_context_menu(interaction: discord.Interaction, user: discord.User):
    if interaction.guild is None:
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    bot: commands.Bot = interaction.client  # type: ignore[assignment]
    modal = ReportModal(bot, "other", user, None, interaction.user.id)
    await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReportCommand(bot))
    bot.tree.add_command(report_context_menu)
