"""Among Us friend-code verification bot — entrypoint."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import load_settings
from db import Database

log = logging.getLogger("aubot")

COGS = (
    "cogs.gate",
    "cogs.setup",
    "cogs.reports",
    "cogs.moderation",
    "cogs.help",
)


class AUVerifyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # required: join gate + ban-on-join lookups
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = load_settings()
        self.db = Database(self.settings.db_path)

    async def setup_hook(self) -> None:
        await self.db.connect()
        for cog in COGS:
            await self.load_extension(cog)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced app commands to guild %s", self.settings.guild_id)
        else:
            await self.tree.sync()
            log.info("Synced app commands globally")

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user and self.user.id)
        await self.change_presence(activity=discord.Game(name="Among Us verification"))


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    bot = AUVerifyBot()
    async with bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
