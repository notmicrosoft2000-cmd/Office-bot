"""Shared helpers for cogs."""
from __future__ import annotations

import re

import discord

SETTINGS_KEYS = (
    "verify_channel_id",
    "log_channel_id",
    "role_verified",
    "role_unverified",
    "role_nonau",
)

# Community bots/apps join with Discord's legacy `Name#1234` usernames. They
# have nothing to verify and would sit at the gate forever, so the bot leaves
# them alone. Human accounts on the new username system never match this.
_LEGACY_NAME = re.compile(r"^[^#]{1,32}#[0-9]{4}$")


def is_app_account(member: discord.abc.User) -> bool:
    """True for bot accounts and for legacy `Name#1234` usernames (apps)."""
    if getattr(member, "bot", False):
        return True
    if getattr(member, "system", False):
        return True
    if getattr(member, "discriminator", "0") not in ("0", "", None):
        return True
    return bool(_LEGACY_NAME.match(str(member)))


async def load_config(bot, guild: discord.Guild) -> dict[str, int]:
    """Return int-valued settings for a guild (missing/invalid keys skipped)."""
    raw = await bot.db.get_settings(SETTINGS_KEYS)
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[key] = int(value)
        except ValueError:
            continue
    return out


def get_roles(guild: discord.Guild, cfg: dict[str, int]) -> dict[str, discord.Role | None]:
    return {
        name: guild.get_role(cfg[key])
        for name, key in (
            ("verified", "role_verified"),
            ("unverified", "role_unverified"),
            ("nonau", "role_nonau"),
        )
    }


async def send_audit(bot, embed: discord.Embed) -> None:
    """Post an embed to the configured log channel, if any."""
    channel_id = await bot.db.get_setting("log_channel_id")
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass


def mask_code(code: str) -> str:
    """Show only the first/last chars of a friend code in public-facing text."""
    if len(code) <= 6:
        return code[:2] + "•" * max(len(code) - 2, 1)
    return code[:3] + "•" * (len(code) - 5) + code[-2:]


def is_mod(member: discord.abc.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild or perms.ban_members)


async def get_bool_setting(bot, key: str, default: bool) -> bool:
    raw = await bot.db.get_setting(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def apply_lockdown(
    guild: discord.Guild,
    unverified_role: discord.Role,
    verify_channel: discord.TextChannel,
) -> int:
    """Deny 'View Channel' for the unverified role everywhere except the gate channel.

    Also guarantees @everyone and the unverified role can see the gate channel.
    Returns the number of channels locked down.
    """
    hidden = 0
    for channel in guild.channels:
        if channel.id == verify_channel.id:
            continue
        try:
            await channel.set_permissions(
                unverified_role,
                overwrite=discord.PermissionOverwrite(view_channel=False),
                reason="AU gate lockdown",
            )
            hidden += 1
        except (discord.Forbidden, discord.HTTPException):
            continue
    try:
        await verify_channel.set_permissions(
            guild.default_role,
            overwrite=discord.PermissionOverwrite(view_channel=True),
            reason="AU gate: gate channel is public",
        )
        await verify_channel.set_permissions(
            unverified_role,
            overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True),
            reason="AU gate: unverified can use the gate",
        )
    except (discord.Forbidden, discord.HTTPException):
        pass
    return hidden
