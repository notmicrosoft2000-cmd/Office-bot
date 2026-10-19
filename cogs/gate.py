"""Join gate: unverified role, friend-code modal, 'I don't play AU' button, reminders.

There is no timeout kick any more: members who never verify stay locked out but
keep getting reminder DMs until they act. Community apps (`Name#1234` legacy
usernames) are skipped entirely.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from helpers import (
    flag_suspect,
    get_bool_setting,
    get_roles,
    is_app_account,
    load_config,
    send_audit,
)

log = logging.getLogger("aubot.gate")


def _dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class VerifyModal(discord.ui.Modal, title="Among Us verification"):
    code = discord.ui.TextInput(
        label="Your friend code",
        placeholder="e.g. Example#0000",
        min_length=7,
        max_length=20,
        required=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return

        db = self.bot.db
        raw = str(self.code.value).strip()
        code = "".join(raw.split()).upper()

        if not re.fullmatch(self.bot.settings.fc_pattern, code):
            await interaction.response.send_message(
                "That doesn't look like an Among Us friend code. "
                "It should look like `Example#0000` (from in-game Settings → Account).",
                ephemeral=True,
            )
            return

        cfg = await load_config(self.bot, interaction.guild)
        roles = get_roles(interaction.guild, cfg)

        # Someone who claimed "I don't play AU" showing up with a code. Nobody is
        # banned for it: they're verified normally and the lie is flagged instead.
        prior = await db.get_member(interaction.user.id)
        if prior and prior["status"] == "nonau" and self.bot.settings.lie_punishment != "off":
            await flag_suspect(
                self.bot,
                interaction.user,
                "said they don't play Among Us, then submitted a friend code",
                guild=interaction.guild,
                detail=f"Friend code submitted: `{code}`",
                dm=self.bot.settings.lie_punishment == "warn",
            )
            await db.link_code(code, interaction.user.id)
            await db.set_status(
                interaction.user.id, "verified",
                username=str(interaction.user), friend_code=code,
            )
            await self._respond_verified(interaction, code, roles, flagged=True)
            return

        # Index lookups: the code itself, this Discord account, and any other
        # Discord accounts previously linked to the same code (alt detection).
        linked_ids = await db.discord_ids_for_code(code)
        hit = (
            await db.is_blacklisted(friend_code=code)
            or await db.is_blacklisted(discord_id=interaction.user.id)
        )
        if not hit:
            for alt_id in linked_ids:
                hit = await db.is_blacklisted(discord_id=alt_id)
                if hit:
                    break

        if hit:
            # Not a ban. They're verified like anyone else, and moderators get
            # an alert plus a flag they can act on (or clear) themselves.
            await flag_suspect(
                self.bot,
                interaction.user,
                "friend code or Discord account is on the index",
                guild=interaction.guild,
                detail=f"Matched `{hit['kind']}` `{hit['value']}` — {hit['reason']}",
                index_id=hit["id"],
            )
            await db.link_code(code, interaction.user.id)
            await db.set_status(
                interaction.user.id, "verified",
                username=str(interaction.user), friend_code=code,
            )
            await self._respond_verified(interaction, code, roles, flagged=True)
            return

        await db.link_code(code, interaction.user.id)
        await db.set_status(
            interaction.user.id, "verified",
            username=str(interaction.user), friend_code=code,
        )
        await self._respond_verified(interaction, code, roles, flagged=False)

    async def _respond_verified(
        self,
        interaction: discord.Interaction,
        code: str,
        roles: dict[str, discord.Role | None],
        *,
        flagged: bool = False,
    ) -> None:
        if flagged:
            text = (
                "✅ Verified — but your account or friend code is **flagged for moderator "
                "review**. Nothing has been removed; a moderator will look into it."
            )
        else:
            text = (
                "✅ Verified! Your friend code is linked to this account. "
                "Welcome aboard."
            )
        await interaction.response.send_message(text, ephemeral=True)
        await self._swap_roles(interaction, roles, "verified")
        note = " (flagged for review)" if flagged else ""
        await self._audit("VERIFY", f"{interaction.user} verified{note}")

    async def _swap_roles(
        self,
        interaction: discord.Interaction,
        roles: dict[str, discord.Role | None],
        target: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        member = guild.get_member(interaction.user.id)
        if member is None:
            return
        wanted = roles.get(target)
        # Option: some servers don't want a separate "verified" badge — just
        # remove the unverified role and let them inherit @everyone access.
        if target == "verified" and not await get_bool_setting(
            self.bot, "give_verified_role", True
        ):
            wanted = None
        to_add = [wanted] if wanted else []
        to_remove = [
            r for name, r in roles.items()
            if r and name != target and r in member.roles
        ]
        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="AU gate")
            if to_add:
                await member.add_roles(*to_add, reason="AU gate")
        except discord.Forbidden:
            await send_audit(
                self.bot,
                discord.Embed(
                    title="⚠️ Role swap failed",
                    description=f"Missing permissions to move {member.mention}. "
                    "Check that my top role is above the gate roles.",
                    color=discord.Color.orange(),
                ),
            )

    async def _audit(self, action: str, details: str) -> None:
        await self.bot.db.audit(action, details)
        colors = {"VERIFY": discord.Color.green(), "AUTO-BAN": discord.Color.red()}
        await send_audit(
            self.bot,
            discord.Embed(
                title=action, description=details, color=colors.get(action, discord.Color.blurple())
            ),
        )


class GateView(discord.ui.View):
    """Persistent buttons under the gate message."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="I play Among Us",
        style=discord.ButtonStyle.green,
        emoji="🚀",
        custom_id="au_gate:verify",
    )
    async def verify(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(VerifyModal(self.bot))

    @discord.ui.button(
        label="I don't play Among Us",
        style=discord.ButtonStyle.grey,
        emoji="🚫",
        custom_id="au_gate:nonau",
    )
    async def nonau(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return
        cfg = await load_config(self.bot, interaction.guild)
        roles = get_roles(interaction.guild, cfg)
        prior = await self.bot.db.get_member(interaction.user.id)
        if prior and prior["status"] == "verified" and roles.get("verified"):
            await interaction.response.send_message(
                "Your account is already verified with a friend code.", ephemeral=True
            )
            return

        await self.bot.db.set_status(
            interaction.user.id, "nonau", username=str(interaction.user)
        )
        await interaction.response.send_message(
            "Understood — you're marked as a non-player. You'll get access to the "
            "general channels shortly. Note: if you're later found to actually play "
            "Among Us, that's grounds for removal.",
            ephemeral=True,
        )
        member = interaction.guild.get_member(interaction.user.id)
        if member and roles.get("unverified") and roles["unverified"] in member.roles:
            try:
                await member.remove_roles(roles["unverified"], reason="AU gate: non-player")
                if roles.get("nonau"):
                    await member.add_roles(roles["nonau"], reason="AU gate: non-player")
            except discord.Forbidden:
                pass
        await self.bot.db.audit("NON-AU", f"{interaction.user} declared they don't play AU")
        await send_audit(
            self.bot,
            discord.Embed(
                title="NON-AU",
                description=f"{interaction.user} declared they don't play Among Us",
                color=discord.Color.dark_grey(),
            ),
        )


class Gate(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._view = GateView(bot)

    async def cog_load(self) -> None:
        self.bot.add_view(self._view)

    async def cog_unload(self) -> None:
        self.dm_reminders.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.dm_reminders.is_running():
            self.dm_reminders.start()
        log.info("gate: reminder task running=%s", self.dm_reminders.is_running())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        db = self.bot.db

        # Bots and community apps arrive with legacy `Name#1234` usernames. They
        # have nothing to verify and would sit at the gate forever, so they are
        # recorded for the stats but never gated, reminded or kicked.
        if is_app_account(member):
            await db.set_status(member.id, "app", username=str(member))
            await db.audit("APP", f"{member} skipped the gate — app account")
            log.info("on_member_join: %s is an app account, gate skipped", member)
            return

        await db.record_join(member.id, str(member))

        hit = await db.is_blacklisted(discord_id=member.id)
        if not hit:
            # Rejoin by someone whose linked code was blacklisted meanwhile.
            for code in await db.codes_for_discord(member.id):
                hit = await db.is_blacklisted(friend_code=code)
                if hit:
                    break
        if hit:
            # Nothing is banned. Moderators get an alert and the account is
            # flagged for review, but the member still goes through the normal
            # gate — a human decides what to do about them.
            await flag_suspect(
                self.bot,
                member,
                "account or linked friend code is on the index",
                guild=member.guild,
                detail=f"Matched `{hit['kind']}` `{hit['value']}` — {hit['reason']}",
                index_id=hit["id"],
            )

        cfg = await load_config(self.bot, member.guild)
        role_id = cfg.get("role_unverified")
        if role_id and (role := member.guild.get_role(role_id)):
            try:
                await member.add_roles(role, reason="AU gate")
            except discord.Forbidden:
                log.warning("Could not assign unverified role to %s", member)

        await self._greet_new_member(member, cfg)

    def _intro_dm(
        self, guild: discord.Guild, verify: discord.abc.GuildChannel | None = None
    ) -> str:
        where = f"**#{verify.name}**" if verify is not None else "the verification channel"
        return (
            f"👋 Welcome to **{guild.name}**!\n\n"
            f"You're locked out of the rest of the server until you verify in {where}:\n"
            "• **Play Among Us?** Submit your friend code — in-game: **Settings → Account** "
            "(it looks like `Example#0000`).\n"
            "• **Don't play?** Just say so and you're through.\n\n"
            "It takes about 10 seconds. There's no deadline — we'll just keep "
            "reminding you until you do."
        )

    async def _greet_new_member(
        self, member: discord.Member, cfg: dict[str, int]
    ) -> None:
        """DM a new member and drop a short-lived @ping in the gate channel.

        The ping makes sure they get a notification pointing at the gate channel;
        it's deleted right after so the channel stays clean.
        """
        guild = member.guild
        verify_id = cfg.get("verify_channel_id")
        verify = guild.get_channel(verify_id) if verify_id else None

        # 1) Welcome DM. Mark stage 1 so the reminder task doesn't immediately re-DM.
        dm_ok = False
        try:
            await member.send(self._intro_dm(guild, verify))
            await self.bot.db.set_reminder_stage(member.id, 1)
            dm_ok = True
        except discord.HTTPException as exc:
            log.info("greet: DM to %s failed (%s); relying on the ping", member, exc)

        # 2) Ping in the gate channel, then delete it.
        pinged = False
        if isinstance(verify, discord.TextChannel):
            try:
                ping = await verify.send(
                    f"{member.mention} 👋 welcome! Please verify here — "
                    "this message will disappear in a moment."
                )
                pinged = True
            except discord.HTTPException as exc:
                log.info("greet: ping in %s failed (%s)", verify, exc)
            else:
                await asyncio.sleep(3)
                try:
                    await ping.delete()
                except discord.HTTPException:
                    pass
        log.info("greet: %s — dm=%s ping=%s", member, dm_ok, pinged)

    @tasks.loop(minutes=30)
    async def dm_reminders(self) -> None:
        """Nudge unverified members: intro, half-time, final, then on a repeat timer."""
        if not await get_bool_setting(self.bot, "dm_reminders", True):
            return
        gate_hours = self.bot.settings.gate_hours
        now = datetime.now(timezone.utc)
        log.info("dm_reminders: scanning %d guild(s)", len(self.bot.guilds))
        for guild in self.bot.guilds:
            cfg = await load_config(self.bot, guild)
            role = guild.get_role(cfg["role_unverified"]) if "role_unverified" in cfg else None
            if role is None:
                log.info("dm_reminders: no unverified role configured/found in %s", guild)
                continue
            log.info(
                "dm_reminders: %s has %d member(s) with %s",
                guild, len(role.members), role.name,
            )
            verify_id = cfg.get("verify_channel_id")
            verify = guild.get_channel(verify_id) if verify_id else None
            for member in list(role.members):
                if is_app_account(member) or member.joined_at is None:
                    continue
                # People can carry the role without ever being recorded (joined while
                # the bot was offline). Backfill so reminder stages actually stick.
                await self.bot.db.ensure_member(
                    member.id,
                    str(member),
                    member.joined_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
                expires = member.joined_at + timedelta(hours=gate_hours)
                remaining = (expires - now).total_seconds() / 3600
                stage = await self.bot.db.reminder_stage(member.id)
                body: str | None = None
                if stage == 0:
                    # Catches members who were already unverified before this existed.
                    body = self._intro_dm(guild, verify)
                    stage = 1
                elif stage == 1 and remaining <= gate_hours / 2:
                    body = (
                        f"⏳ Heads up — you're still unverified in **{guild.name}**. "
                        "Head to the verification channel and either submit your Among Us "
                        "friend code or say *I don't play Among Us*."
                    )
                    stage = 2
                elif stage == 2 and remaining <= 6:
                    body = (
                        f"⚠️ You're still locked out of the rest of **{guild.name}**. "
                        "Verifying takes about 10 seconds — submit your friend code, "
                        "or say you don't play Among Us."
                    )
                    stage = 3
                elif stage >= 3:
                    # Nothing is ever kicked for timing out, so keep nudging until
                    # they act or leave — at most once every REMINDER_REPEAT_HOURS.
                    repeat = self.bot.settings.reminder_repeat_hours
                    last_dm = _dt(await self.bot.db.last_dm(member.id))
                    if last_dm is not None and (now - last_dm) < timedelta(hours=repeat):
                        continue
                    body = (
                        f"🔁 Friendly nudge — you're still unverified in **{guild.name}**. "
                        "Submit your Among Us friend code (or say you don't play) and "
                        "you'll get the rest of the server. We'll keep checking in."
                    )
                if body is None:
                    continue
                try:
                    await member.send(body)
                    log.info("dm_reminders: DM sent to %s (stage %d)", member, stage)
                except discord.HTTPException as exc:
                    # DMs closed — still advance the stage so we don't retry every loop.
                    log.info("dm_reminders: DM to %s failed: %s", member, exc)
                await self.bot.db.set_reminder_stage(member.id, stage)
                await self.bot.db.set_last_dm(member.id)

    @dm_reminders.before_loop
    async def before_dm_reminders(self) -> None:
        await self.bot.wait_until_ready()

    # -- setup -------------------------------------------------------------
    @app_commands.command(name="verify", description="Open the friend-code verification form")
    async def verify_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this inside the server.", ephemeral=True)
            return
        await interaction.response.send_modal(VerifyModal(self.bot))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Gate(bot))
