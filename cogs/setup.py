"""Interactive /setup menu (dropdowns + buttons) and /gate-lock."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from helpers import apply_lockdown, get_bool_setting

log = logging.getLogger("aubot.setup")

GATE_ROLE_NAMES = {
    "unverified": "AU Unverified",
    "verified": "AU Verified",
    "nonau": "AU Non-Player",
}
GATE_COLOURS = {
    "unverified": discord.Color.red(),
    "verified": discord.Color.green(),
    "nonau": discord.Color.dark_grey(),
}


@dataclass
class SetupState:
    verify_channel_id: int | None = None
    log_channel_id: int | None = None
    report_channel_id: int | None = None
    unverified_role_id: int | None = None
    verified_role_id: int | None = None
    nonau_role_id: int | None = None
    hard_gate: bool = True
    dm_reminders: bool = True
    give_verified_role: bool = True
    auto_create: bool = True


def _fmt_channel(guild: discord.Guild, cid: int | None) -> str:
    if not cid:
        return "*auto*"
    ch = guild.get_channel(cid)
    return ch.mention if ch else f"`{cid}`"


def _fmt_role(guild: discord.Guild, rid: int | None) -> str:
    if not rid:
        return "*auto-create*"
    role = guild.get_role(rid)
    return role.mention if role else f"`{rid}`"


def _toggle(value: bool) -> str:
    return "✅ On" if value else "❌ Off"


class SetupView(discord.ui.View):
    """One interactive setup menu, rendered as 3 pages."""

    def __init__(self, bot: commands.Bot, state: SetupState, page: int = 1) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.state = state
        self.page = page
        self._build()

    # -- rendering ---------------------------------------------------------
    def embed(self) -> discord.Embed:
        guild = self.guild
        s = self.state
        e = discord.Embed(
            title=f"⚙️ Among Us gate setup — page {self.page}/3",
            color=discord.Color.blurple(),
        )
        e.add_field(
            name="Channels",
            value=(
                f"Verify: {_fmt_channel(guild, s.verify_channel_id)}\n"
                f"Logs: {_fmt_channel(guild, s.log_channel_id)}\n"
                f"Reports: {_fmt_channel(guild, s.report_channel_id)}"
            ),
            inline=False,
        )
        e.add_field(
            name="Roles",
            value=(
                f"Unverified: {_fmt_role(guild, s.unverified_role_id)}\n"
                f"Verified: {_fmt_role(guild, s.verified_role_id)}\n"
                f"Non-player: {_fmt_role(guild, s.nonau_role_id)}"
            ),
            inline=False,
        )
        e.add_field(
            name="Options",
            value=(
                f"Hard gate (hide other channels): {_toggle(s.hard_gate)}\n"
                f"Reminder DMs: {_toggle(s.dm_reminders)}\n"
                f"Give Verified role: {_toggle(s.give_verified_role)}\n"
                f"Auto-create missing things: {_toggle(s.auto_create)}"
            ),
            inline=False,
        )
        e.set_footer(text="Pick from the dropdowns on this page, then use the buttons.")
        return e

    @property
    def guild(self) -> discord.Guild:
        guild = self.bot.get_guild(self.bot.settings.guild_id) if self.bot.settings.guild_id else None
        if guild is None and self.bot.guilds:
            guild = self.bot.guilds[0]
        if guild is None:
            raise RuntimeError("No guild available")
        return guild

    def _build(self) -> None:
        self.clear_items()
        if self.page == 1:
            self.add_item(_ChannelPicker(self.state, "verify_channel_id",
                                         "Verify channel (new members only see this)", 0))
            self.add_item(_ChannelPicker(self.state, "log_channel_id",
                                         "Log channel (audit / bans) — optional", 1))
            self.add_item(_ChannelPicker(self.state, "report_channel_id",
                                         "Report queue channel — optional", 2))
            self.add_item(_NavButton("Next: roles ▶", 2, discord.ButtonStyle.primary, 3))
            self.add_item(_QuickSetupButton(1, 3))
            self.add_item(_CancelButton(3))
        elif self.page == 2:
            self.add_item(_RolePicker(self.state, "unverified_role_id",
                                      "Unverified role (blank = auto-create)", 0))
            self.add_item(_RolePicker(self.state, "verified_role_id",
                                      "Verified role (blank = auto-create)", 1))
            self.add_item(_RolePicker(self.state, "nonau_role_id",
                                      "Non-player role (blank = auto-create)", 2))
            self.add_item(_NavButton("◀ Back", 1, discord.ButtonStyle.secondary, 3))
            self.add_item(_NavButton("Next: options ▶", 3, discord.ButtonStyle.primary, 3))
            self.add_item(_CancelButton(3))
        else:
            self.add_item(_ToggleButton("Hard gate", "hard_gate", 0))
            self.add_item(_ToggleButton("Reminder DMs", "dm_reminders", 0))
            self.add_item(_ToggleButton("Give Verified role", "give_verified_role", 0))
            self.add_item(_ToggleButton("Auto-create missing", "auto_create", 0))
            self.add_item(_NavButton("◀ Back", 2, discord.ButtonStyle.secondary, 1))
            self.add_item(_RunSetupButton(1))
            self.add_item(_CancelButton(1))

    async def rerender(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)


class _ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, state: SetupState, attr: str, placeholder: str, row: int) -> None:
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            row=row,
        )
        self.state = state
        self.attr = attr

    async def callback(self, interaction: discord.Interaction) -> None:
        setattr(self.state, self.attr, self.values[0].id if self.values else None)
        view = self.view
        assert isinstance(view, SetupView)
        await view.rerender(interaction)


class _RolePicker(discord.ui.RoleSelect):
    def __init__(self, state: SetupState, attr: str, placeholder: str, row: int) -> None:
        super().__init__(placeholder=placeholder, min_values=0, max_values=1, row=row)
        self.state = state
        self.attr = attr

    async def callback(self, interaction: discord.Interaction) -> None:
        setattr(self.state, self.attr, self.values[0].id if self.values else None)
        view = self.view
        assert isinstance(view, SetupView)
        await view.rerender(interaction)


class _NavButton(discord.ui.Button):
    def __init__(self, label: str, page: int, style: discord.ButtonStyle, row: int) -> None:
        super().__init__(label=label, style=style, row=row)
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, SetupView)
        await interaction.response.edit_message(
            embed=view.embed(), view=SetupView(view.bot, view.state, self.page)
        )


class _ToggleButton(discord.ui.Button):
    def __init__(self, label: str, attr: str, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.attr = attr

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, SetupView)
        setattr(view.state, self.attr, not getattr(view.state, self.attr))
        await interaction.response.edit_message(
            embed=view.embed(), view=SetupView(view.bot, view.state, view.page)
        )


class _CancelButton(discord.ui.Button):
    def __init__(self, row: int) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="Setup cancelled. Nothing was changed.",
            embed=None,
            view=None,
        )


class _QuickSetupButton(discord.ui.Button):
    def __init__(self, page: int, row: int) -> None:
        super().__init__(
            label="⚡ Quick setup (recommended)", style=discord.ButtonStyle.success, row=row
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, SetupView)
        await run_setup(interaction, view.bot, view.state, from_view=True)


class _RunSetupButton(discord.ui.Button):
    def __init__(self, row: int) -> None:
        super().__init__(label="✅ Run setup", style=discord.ButtonStyle.success, row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, SetupView)
        await run_setup(interaction, view.bot, view.state, from_view=True)


async def _ensure_channel(
    guild: discord.Guild, cid: int | None, name: str, topic: str, *, mod_only: bool, create: bool
) -> discord.TextChannel | None:
    if cid:
        ch = guild.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch
    found = discord.utils.get(guild.text_channels, name=name)
    if found:
        return found
    if not create:
        return None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=not mod_only)
    }
    return await guild.create_text_channel(name, topic=topic, overwrites=overwrites, reason="AU gate setup")


async def _ensure_role(
    guild: discord.Guild, rid: int | None, name: str, colour: discord.Color, *, create: bool
) -> discord.Role | None:
    if rid:
        role = guild.get_role(rid)
        if role:
            return role
    found = discord.utils.get(guild.roles, name=name)
    if found:
        return found
    if not create:
        return None
    return await guild.create_role(name=name, colour=colour, reason="AU gate setup")


async def run_setup(
    interaction: discord.Interaction, bot: commands.Bot, state: SetupState, *, from_view: bool = False
) -> None:
    from cogs.gate import GateView  # local import to avoid cycle

    if from_view:
        await interaction.response.defer()
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("Run this in a server.", ephemeral=True)
        return

    if not guild.me or not guild.me.guild_permissions.manage_roles:
        await interaction.edit_original_response(
            content="❌ I need the **Manage Roles** permission to continue.",
            embed=None, view=None,
        )
        return

    s = state
    verify = await _ensure_channel(guild, s.verify_channel_id, "verify",
                                   "AU verification gate", mod_only=False, create=s.auto_create)
    logs = await _ensure_channel(guild, s.log_channel_id, "mod-logs",
                                 "AU audit/logs", mod_only=True, create=s.auto_create)
    reports = await _ensure_channel(guild, s.report_channel_id, "mod-reports",
                                    "AU /report queue", mod_only=True, create=s.auto_create)
    if verify is None:
        await interaction.edit_original_response(
            content="❌ No verify channel selected and auto-create is off.", embed=None, view=None
        )
        return

    unverified = await _ensure_role(guild, s.unverified_role_id, GATE_ROLE_NAMES["unverified"],
                                    GATE_COLOURS["unverified"], create=s.auto_create)
    verified = await _ensure_role(guild, s.verified_role_id, GATE_ROLE_NAMES["verified"],
                                  GATE_COLOURS["verified"], create=s.auto_create)
    nonau = await _ensure_role(guild, s.nonau_role_id, GATE_ROLE_NAMES["nonau"],
                               GATE_COLOURS["nonau"], create=s.auto_create)
    if unverified is None:
        await interaction.edit_original_response(
            content="❌ No unverified role selected and auto-create is off.", embed=None, view=None
        )
        return

    if max(r.position for r in (unverified, verified or unverified, nonau or unverified)) > guild.me.top_role.position:
        await interaction.edit_original_response(
            content="❌ My top role must be **above** the gate roles. Drag the bot's role up in "
                    "Server Settings → Roles, then run setup again.",
            embed=None, view=None,
        )
        return

    # Persist settings
    await bot.db.set_setting("role_unverified", str(unverified.id))
    if verified:
        await bot.db.set_setting("role_verified", str(verified.id))
    if nonau:
        await bot.db.set_setting("role_nonau", str(nonau.id))
    await bot.db.set_setting("verify_channel_id", str(verify.id))
    if logs:
        await bot.db.set_setting("log_channel_id", str(logs.id))
    if reports:
        await bot.db.set_setting("report_channel_id", str(reports.id))
    await bot.db.set_setting("hard_gate", "1" if s.hard_gate else "0")
    await bot.db.set_setting("dm_reminders", "1" if s.dm_reminders else "0")
    await bot.db.set_setting("give_verified_role", "1" if s.give_verified_role else "0")

    # Re-post the gate message
    old_id = await bot.db.get_setting("gate_message_id")
    if old_id:
        try:
            old = await verify.fetch_message(int(old_id))
            await old.delete()
        except discord.HTTPException:
            pass

    embed = discord.Embed(
        title="Welcome! One quick check before you come in",
        description=(
            "**If you play Among Us:** hit *I play Among Us* and paste your friend "
            "code (in-game: **Settings → Account**). It looks like `Example#0000`. "
            "Friend codes are permanent and we index them.\n\n"
            "**If you don't play Among Us:** hit *I don't play Among Us* and you'll go "
            "straight through. Picking this and then being found to actually play will "
            "get you removed.\n\n"
            f"You have **{bot.settings.gate_hours} hours** to pick one."
        ),
        color=discord.Color.blurple(),
    )
    message = await verify.send(embed=embed, view=GateView(bot))
    await bot.db.set_setting("gate_message_id", str(message.id))

    hidden = 0
    if s.hard_gate:
        hidden = await apply_lockdown(guild, unverified, verify)

    await bot.db.audit(
        "SETUP",
        f"{interaction.user} ran setup: verify={verify.id} hard_gate={s.hard_gate} "
        f"dm={s.dm_reminders} give_verified={s.give_verified_role}",
        interaction.user.id,
    )

    summary = discord.Embed(title="✅ Setup complete", color=discord.Color.green())
    summary.add_field(name="Gate channel", value=verify.mention, inline=True)
    summary.add_field(name="Unverified role", value=unverified.mention, inline=True)
    summary.add_field(name="Verified role", value=verified.mention if verified else "*none*", inline=True)
    summary.add_field(name="Non-player role", value=nonau.mention if nonau else "*none*", inline=True)
    summary.add_field(name="Log channel", value=logs.mention if logs else "*none*", inline=True)
    summary.add_field(name="Report channel", value=reports.mention if reports else "*none*", inline=True)
    summary.add_field(
        name="Options",
        value=(
            f"Hard gate: {_toggle(s.hard_gate)} ({hidden} channels locked)\n"
            f"Reminder DMs: {_toggle(s.dm_reminders)}\n"
            f"Give Verified role: {_toggle(s.give_verified_role)}"
        ),
        inline=False,
    )
    summary.set_footer(text="Tip: /codes to view saved friend codes • /help for everything")
    await interaction.edit_original_response(embed=summary, view=None)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _open(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        state = SetupState()
        state.verify_channel_id = _to_int(await self.bot.db.get_setting("verify_channel_id"))
        state.log_channel_id = _to_int(await self.bot.db.get_setting("log_channel_id"))
        state.report_channel_id = _to_int(await self.bot.db.get_setting("report_channel_id"))
        state.unverified_role_id = _to_int(await self.bot.db.get_setting("role_unverified"))
        state.verified_role_id = _to_int(await self.bot.db.get_setting("role_verified"))
        state.nonau_role_id = _to_int(await self.bot.db.get_setting("role_nonau"))
        state.hard_gate = await get_bool_setting(self.bot, "hard_gate", True)
        state.dm_reminders = await get_bool_setting(self.bot, "dm_reminders", True)
        state.give_verified_role = await get_bool_setting(self.bot, "give_verified_role", True)
        view = SetupView(self.bot, state, 1)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @app_commands.command(name="setup", description="Interactive setup (roles, channels, options)")
    @app_commands.default_permissions(administrator=True)
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        await self._open(interaction)

    @app_commands.command(name="gate-setup", description="Alias for /setup")
    @app_commands.default_permissions(administrator=True)
    async def gate_setup_alias(self, interaction: discord.Interaction) -> None:
        await self._open(interaction)

    @app_commands.command(
        name="gate-lock",
        description="(Re)apply the lockdown so Unverified can only see the verify channel",
    )
    @app_commands.default_permissions(administrator=True)
    async def gate_lock(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        unverified = guild.get_role(_to_int(await self.bot.db.get_setting("role_unverified")) or 0)
        verify = guild.get_channel(_to_int(await self.bot.db.get_setting("verify_channel_id")) or 0)
        if unverified is None or not isinstance(verify, discord.TextChannel):
            await interaction.followup.send(
                "Run `/setup` first so I know the roles and channels.", ephemeral=True
            )
            return
        hidden = await apply_lockdown(guild, unverified, verify)
        await self.bot.db.set_setting("hard_gate", "1")
        await interaction.followup.send(
            f"🔒 Locked down **{hidden}** channels. `{unverified.name}` can only see {verify.mention}.",
            ephemeral=True,
        )


def _to_int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))
