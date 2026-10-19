"""Health and info: /ping, /stats, and the heartbeat the watchdog watches.

The heartbeat is the important part: systemd's ``Restart=on-failure`` can only
see a process that *exits*. A bot whose event loop has wedged still looks alive
to systemd, so we write a timestamped file every minute and let
``deploy/watchdog.py`` alert when it goes stale.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from helpers import is_mod

log = logging.getLogger("aubot.info")


def format_duration(seconds: float) -> str:
    """Human-readable duration, e.g. ``2d 3h 14m``."""
    seconds = max(int(seconds), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _heartbeat_path(bot: commands.Bot) -> Path:
    """Where the watchdog looks for us: beside the database file."""
    return Path(bot.settings.db_path).parent / "heartbeat"


def _latency_ms(bot: commands.Bot) -> float | None:
    value = bot.latency
    if value is None or math.isnan(value) or value < 0:
        return None
    return value * 1000


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)

    async def cog_load(self) -> None:
        self.heartbeat.start()

    async def cog_unload(self) -> None:
        self.heartbeat.cancel()

    # -- watchdog heartbeat -------------------------------------------------
    @tasks.loop(seconds=60)
    async def heartbeat(self) -> None:
        path = _heartbeat_path(self.bot)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError as exc:
            log.warning("heartbeat write failed (%s): %s", path, exc)

    @heartbeat.before_loop
    async def before_heartbeat(self) -> None:
        await self.bot.wait_until_ready()

    @heartbeat.error
    async def heartbeat_error(self, exc: BaseException) -> None:
        log.error("heartbeat loop crashed: %r", exc)

    # -- commands -----------------------------------------------------------
    @app_commands.command(name="ping", description="Check the bot's connection to Discord")
    async def ping(self, interaction: discord.Interaction) -> None:
        ms = _latency_ms(self.bot)
        if ms is None:
            value, dot = "unknown", "⚪"
        else:
            value = f"{ms:.0f} ms"
            dot = "🟢" if ms < 200 else "🟡" if ms < 500 else "🔴"

        uptime = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        embed = discord.Embed(title="🏓 Pong", color=discord.Color.green())
        embed.add_field(name=f"{dot} Gateway", value=value, inline=True)
        embed.add_field(name="Uptime", value=format_duration(uptime), inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="stats", description="Gate, index and report statistics (moderators)"
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not is_mod(interaction.user):  # type: ignore[arg-type]
            await interaction.response.send_message("Moderators only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        s = await self.bot.db.stats()
        last = await self.bot.db.last_audit()

        embed = discord.Embed(title="📊 Office Bot statistics", color=discord.Color.blurple())
        embed.add_field(
            name="Gate",
            value=(
                f"Verified: **{s['members_verified']}**\n"
                f"Unverified: **{s['members_unverified']}**\n"
                f"Non-players: **{s['members_nonau']}**\n"
                f"Apps (skipped): **{s.get('members_app', 0)}**\n"
                f"Tracked: **{s['members_total']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Index",
            value=(
                f"Friend codes: **{s['index_codes']}**\n"
                f"Accounts: **{s['index_accounts']}**\n"
                f"Entries: **{s['index_total']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Reports",
            value=(
                f"Pending: **{s['reports_pending']}**\n"
                f"Approved: **{s['reports_approved']}**\n"
                f"Rejected: **{s['reports_rejected']}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Data",
            value=(
                f"Codes stored: **{s['codes']}**\n"
                f"Code↔account links: **{s['links']}**\n"
                f"Audit entries: **{s['audit_entries']}**"
            ),
            inline=True,
        )

        ms = _latency_ms(self.bot)
        uptime = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        embed.add_field(
            name="Runtime",
            value=(
                f"Uptime: **{format_duration(uptime)}**\n"
                f"Gateway: **{f'{ms:.0f} ms' if ms is not None else 'unknown'}**\n"
                f"Guilds: **{len(self.bot.guilds)}** · "
                f"cached members: **{sum(g.member_count or 0 for g in self.bot.guilds)}**"
            ),
            inline=True,
        )

        if last:
            details = str(last["details"])
            if len(details) > 140:
                details = details[:137] + "…"
            try:
                when = datetime.strptime(last["created_at"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                stamp = f"<t:{int(when.timestamp())}:R>"
            except (TypeError, ValueError):
                stamp = "?"
            embed.add_field(
                name="Last event",
                value=f"`{last['action']}` {discord.utils.escape_markdown(details)}\n{stamp}",
                inline=False,
            )

        embed.set_footer(text="Office Bot · Office#6118")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
