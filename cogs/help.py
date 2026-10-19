"""Help & info command."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from helpers import is_mod


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How this bot works")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="How the AU Verification Bot works",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="1) New joiners hit the gate",
            value="They get `AU Unverified` and can see **only** the verify channel (the bot locks every other channel away from them). They must either submit their Among Us friend code (permanent, unchangeable) or click **I don't play Among Us**. Nobody is kicked for taking their time — the bot nudges them by DM (intro, halfway, final, then periodically) until they act. Community apps (bot accounts and `Name#1234` usernames) are skipped entirely.",
            inline=False,
        )
        embed.add_field(
            name="2) What happens when they verify",
            value="Verified players get the `AU Verified` role (this is optional — admins can turn it off so they simply lose `AU Unverified`). `I don't play AU` members get `AU Non-Player`. Submitting a valid friend code records it and links it to their Discord account. If that code (or the account) is on the index, they're **banned on-the-spot**. If someone claimed 'I don't play AU' and later submits a code, the bot bans/flags them per `LIE_PUNISHMENT`.",
            inline=False,
        )
        embed.add_field(
            name="3) Alt detection",
            value="Every friend code is linked to every Discord account that has used it. If any code is indexed, **all linked accounts** are banned (so ban evasion gets caught at join/verify).",
            inline=False,
        )
        embed.add_field(
            name="4) Reporting a friend code",
            value="Use `/report` to report an **Among Us friend code** (`Example#0000`) — pick a reason (predatory behaviour, harassment, ban evasion, …), then describe what happened and attach evidence. It goes to the mod queue with **Approve → blacklist** / **Reject** buttons. **Only approved reports enter the index** (this prevents framing).",
            inline=False,
        )
        embed.add_field(
            name="5) Moderation tools",
            value="Mods can use `/blacklist add/remove/list`, `/whois`, `/nuke` (ban now + index all linked codes), and `/codes` to **view every saved friend code** (with linked accounts) or export a CSV. Approving a report also bans anyone currently linked to it.",
            inline=False,
        )
        embed.add_field(
            name="6) Setup (admins, one server)",
            value="Run `/setup` for an interactive menu — pick channels and roles from dropdowns and flip options with buttons. `/gate-lock` re-applies the lockdown any time. `/codes export:True` gives you a CSV backup of the friend-code index.",
            inline=False,
        )
        embed.add_field(
            name="7) Staying online",
            value=f"Use `/ping` for gateway latency and `/stats` for gate/index/report counters. The bot also writes a heartbeat file every minute so an external watchdog can tell a hung process apart from a healthy one.",
            inline=False,
        )
        embed.add_field(
            name="8) Honest limitations",
            value="No official Among Us API, so we can't prove ownership — we validate format and record the claim. Treat evidence carefully; mod approval is the safeguard.",
            inline=False,
        )
        embed.set_footer(
            text=f"Reminders: {self.bot.settings.gate_hours}h schedule, then every "
            f"{self.bot.settings.reminder_repeat_hours}h • LIE_PUNISHMENT: {self.bot.settings.lie_punishment}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
