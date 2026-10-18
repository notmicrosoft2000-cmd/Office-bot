"""Moderator tools: blacklist index management, lookups, manual nukes."""
from __future__ import annotations

import csv
import io

import discord
from discord import app_commands
from discord.ext import commands

from helpers import is_mod, mask_code, send_audit


async def _mod_ok(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return False
    if not is_mod(interaction.user):
        await interaction.response.send_message("Moderators only.", ephemeral=True)
        return False
    return True


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    blacklist = app_commands.Group(
        name="blacklist",
        description="Manage the shared index of known-bad Among Us accounts",
    )

    @blacklist.command(name="add", description="Add a friend code and/or user to the index")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(
        friend_code="Their Among Us friend code (permanent identifier)",
        user="The Discord account, if known",
        reason="Why they belong on the index (shown to mods)",
        evidence="Link(s) to proof",
    )
    async def bl_add(
        self,
        interaction: discord.Interaction,
        reason: str,
        friend_code: str | None = None,
        user: discord.User | None = None,
        evidence: str | None = None,
    ) -> None:
        if not await _mod_ok(interaction):
            return
        if not friend_code and not user:
            await interaction.response.send_message(
                "Provide a friend code, a user, or both.", ephemeral=True
            )
            return
        added: list[str] = []
        if friend_code:
            code = "".join(friend_code.split()).upper()
            entry_id = await self.bot.db.blacklist_add(
                "friend_code", code, reason, interaction.user.id, evidence
            )
            added.append(f"friend_code `{mask_code(code)}` → index #{entry_id}")
        if user:
            entry_id = await self.bot.db.blacklist_add(
                "discord_id", str(user.id), reason, interaction.user.id, evidence
            )
            added.append(f"user {user} → index #{entry_id}")

        # Ban them right now if they're in this server, plus any account
        # sharing the indexed friend code (alt detection).
        banned: list[str] = []
        guild = interaction.guild
        if guild:
            ids = {user.id} if user else set()
            if friend_code:
                code = "".join(friend_code.split()).upper()
                ids.update(await self.bot.db.members_with_code(code))
                ids.update(await self.bot.db.discord_ids_for_code(code))
            for uid in ids:
                try:
                    member = guild.get_member(uid) or await guild.fetch_member(uid)
                except discord.HTTPException:
                    continue
                try:
                    await member.ban(reason=f"Blacklisted: {reason}", delete_message_seconds=0)
                    banned.append(str(member))
                except discord.HTTPException:
                    continue

        await self.bot.db.audit(
            "BLACKLIST", f"{interaction.user} indexed: {'; '.join(added)} | {reason}",
            interaction.user.id,
        )
        await send_audit(
            self.bot,
            discord.Embed(
                title="INDEX UPDATED",
                description=f"{interaction.user} added:\n" + "\n".join(f"• {a}" for a in added)
                + f"\nReason: {reason}"
                + (f"\nBanned now: {', '.join(banned)}" if banned else ""),
                color=discord.Color.red(),
            ),
        )
        await interaction.response.send_message(
            "Added:\n" + "\n".join(f"• {a}" for a in added)
            + (f"\nBanned now: {', '.join(banned)}" if banned else "\n(They weren't in the server — "
               "they'll be banned on join.)"),
            ephemeral=True,
        )

    @blacklist.command(name="remove", description="Remove an entry from the index")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(entry_id="The index entry number (see /blacklist list)")
    async def bl_remove(self, interaction: discord.Interaction, entry_id: int) -> None:
        if not await _mod_ok(interaction):
            return
        entry = await self.bot.db.blacklist_remove(entry_id)
        if entry is None:
            await interaction.response.send_message(f"No entry #{entry_id}.", ephemeral=True)
            return
        await self.bot.db.audit(
            "BLACKLIST-DEL", f"{interaction.user} removed index #{entry_id} "
            f"({entry['kind']}: {entry['value']})", interaction.user.id,
        )
        await send_audit(
            self.bot,
            discord.Embed(
                title="INDEX ENTRY REMOVED",
                description=f"{interaction.user} removed #{entry_id} "
                f"({entry['kind']}: {entry['value']})",
                color=discord.Color.orange(),
            ),
        )
        await interaction.response.send_message(
            f"Removed #{entry_id} ({entry['kind']}: `{entry['value']}`).", ephemeral=True
        )

    @blacklist.command(name="list", description="Show recent index entries")
    @app_commands.default_permissions(ban_members=True)
    async def bl_list(self, interaction: discord.Interaction) -> None:
        if not await _mod_ok(interaction):
            return
        rows = await self.bot.db.blacklist_list()
        if not rows:
            await interaction.response.send_message("Index is empty.", ephemeral=True)
            return
        embed = discord.Embed(title="Blacklist index", color=discord.Color.red())
        for row in rows:
            value = row["value"]
            if row["kind"] == "friend_code":
                value = f"`{mask_code(value)}`"
            embed.add_field(
                name=f"#{row['id']} · {row['kind']}",
                value=f"{value}\n{row['reason'][:100]}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="whois", description="Look up a user's linked friend codes")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(user="The Discord user to investigate")
    async def whois(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await _mod_ok(interaction):
            return
        member = await self.bot.db.get_member(user.id)
        codes = await self.bot.db.codes_for_discord(user.id)
        hit = await self.bot.db.is_blacklisted(discord_id=user.id)
        for code in codes:
            hit = hit or await self.bot.db.is_blacklisted(friend_code=code)

        embed = discord.Embed(title=f"{user}", color=discord.Color.blurple())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Discord ID", value=str(user.id), inline=True)
        if member:
            embed.add_field(name="Status", value=member["status"], inline=True)
            embed.add_field(
                name="Joined", value=member["joined_at"] or "?", inline=True
            )
            if member["friend_code"]:
                embed.add_field(
                    name="Current code", value=f"`{mask_code(member['friend_code'])}`",
                    inline=True,
                )
        if codes:
            embed.add_field(
                name="All linked codes",
                value="\n".join(f"`{mask_code(c)}`" for c in codes[:15]) or "—",
                inline=False,
            )
        if hit:
            embed.add_field(
                name="⚠️ INDEXED",
                value=f"Entry #{hit['id']} ({hit['kind']}): {hit['reason'][:150]}",
                inline=False,
            )
            embed.colour = discord.Color.red()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="codes",
        description="View every saved friend code (and who it's linked to), or export a CSV",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(
        export="Attach a CSV of every code↔account link instead of paging",
        page="Page number (10 codes per page)",
    )
    async def codes(
        self, interaction: discord.Interaction, export: bool = False, page: int = 1
    ) -> None:
        if not await _mod_ok(interaction):
            return

        if export:
            rows = await self.bot.db.export_rows()
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["friend_code", "discord_id", "username", "status", "first_linked", "indexed"]
            )
            for r in rows:
                writer.writerow(
                    [
                        r["friend_code"],
                        r["discord_id"],
                        r["username"] or "",
                        r["status"] or "",
                        r["first_linked"] or "",
                        "yes" if r["indexed"] else "no",
                    ]
                )
            data = io.BytesIO(buf.getvalue().encode("utf-8"))
            file = discord.File(data, filename="au_friend_codes.csv")
            await interaction.response.send_message(
                f"Exported {len(rows)} code↔account row(s).", file=file, ephemeral=True
            )
            return

        page = max(page, 1)
        per_page = 10
        total = await self.bot.db.saved_codes_count()
        if total == 0:
            await interaction.response.send_message(
                "No friend codes saved yet.", ephemeral=True
            )
            return
        rows = await self.bot.db.saved_codes(limit=per_page, offset=(page - 1) * per_page)
        pages = max((total + per_page - 1) // per_page, 1)
        embed = discord.Embed(
            title="Saved friend codes",
            description=f"{total} code(s) on file • page {page}/{pages}",
            color=discord.Color.blurple(),
        )
        for r in rows:
            accounts = await self.bot.db.accounts_for_code(r["friend_code"])
            alt = f" • +{len(accounts) - 1} alt account(s)" if len(accounts) > 1 else ""
            flag = "  ⚠️ INDEXED" if r["indexed"] else ""
            embed.add_field(
                name=f"{r['friend_code']}{flag}",
                value=f"<@{r['discord_id']}> · `{r['status']}`{alt}\n"
                f"verified {r['verified_at'] or '?'}",
                inline=False,
            )
        embed.set_footer(text="Use /codes export:True for a CSV • /whois for one user")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="nuke",
        description="Ban a user NOW and index their linked friend codes (e.g. caught lying)",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(user="The offender", reason="Shown in the ban log")
    async def nuke(self, interaction: discord.Interaction, user: discord.User, reason: str) -> None:
        if not await _mod_ok(interaction):
            return
        guild = interaction.guild
        codes = await self.bot.db.codes_for_discord(user.id)
        member_rec = await self.bot.db.get_member(user.id)
        if member_rec and member_rec["friend_code"]:
            codes.append(member_rec["friend_code"])
        codes = list(dict.fromkeys(codes))

        banned = False
        if guild:
            try:
                member = guild.get_member(user.id) or await guild.fetch_member(user.id)
                await member.ban(reason=reason, delete_message_seconds=0)
                banned = True
            except (discord.HTTPException, AttributeError):
                banned = False

        indexed: list[str] = []
        await self.bot.db.blacklist_add("discord_id", str(user.id), reason, interaction.user.id)
        indexed.append(str(user.id))
        for code in codes:
            await self.bot.db.blacklist_add("friend_code", code, reason, interaction.user.id)
            indexed.append(mask_code(code))
        await self.bot.db.set_status(user.id, "banned", username=str(user))

        await self.bot.db.audit(
            "NUKE", f"{interaction.user} nuked {user}: {reason}", interaction.user.id
        )
        await send_audit(
            self.bot,
            discord.Embed(
                title="NUKED",
                description=f"{user} ({user.id}) banned by {interaction.user}.\n"
                f"Reason: {reason}\nIndexed codes: "
                + (", ".join(f"`{c}`" for c in indexed) or "none"),
                color=discord.Color.dark_red(),
            ),
        )
        await interaction.response.send_message(
            f"{'Banned' if banned else 'Indexed (not currently in this server)'}: {user}\n"
            f"Indexed identifiers: {', '.join(indexed)}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
