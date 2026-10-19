"""Local smoke tests: DB behaviour + cog/command loading. No Discord token needed.

Run:  .venv/bin/python -m tests.smoke
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Database  # noqa: E402
from helpers import is_app_account, mask_code  # noqa: E402
from cogs.reports import REASONS, normalize_code, reason_label  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


async def db_tests() -> None:
    print("database:")
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "test.db"))
    await db.connect()

    # settings
    await db.set_setting("role_verified", "111")
    check("setting round-trip", await db.get_setting("role_verified") == "111")
    check("missing setting is None", await db.get_setting("nope") is None)
    check("multi-get", await db.get_settings(["role_verified", "nope"]) == {"role_verified": "111"})

    # members + codes
    await db.record_join(1, "alice")
    await db.set_status(1, "verified", username="alice", friend_code="randomcode#1234")
    m = await db.get_member(1)
    check("member stored verified", m and m["status"] == "verified")
    check("code normalized uppercase", m and m["friend_code"] == "RANDOMCODE#1234")
    await db.link_code("randomcode#1234", 1)
    check("code link", await db.discord_ids_for_code("RandomCode#1234") == [1])
    check("codes for discord", await db.codes_for_discord(1) == ["RANDOMCODE#1234"])
    check("members_with_code", await db.members_with_code("randomcode#1234") == [1])

    # second account linking the same code (alt)
    await db.record_join(2, "bob")
    await db.link_code("RANDOMCODE#1234", 2)
    check("alt detected via code", sorted(await db.discord_ids_for_code("RANDOMCODE#1234")) == [1, 2])

    # blacklist
    eid = await db.blacklist_add("friend_code", "randomcode#1234", "griefing", 99)
    check("blacklist add returns id", isinstance(eid, int) and eid > 0)
    hit = await db.is_blacklisted(friend_code="randomcode#1234")
    check("blacklist hit by code", hit and hit["id"] == eid)
    check("blacklist miss", await db.is_blacklisted(friend_code="nope#0000") is None)
    eid2 = await db.blacklist_add("discord_id", "2", "harassment", 99)
    check("blacklist hit by discord id", (await db.is_blacklisted(discord_id=2))["id"] == eid2)
    check("idempotent upsert (same id)", await db.blacklist_add(
        "friend_code", "RANDOMCODE#1234", "updated", 99) == eid)
    rows = await db.blacklist_list()
    check("blacklist list length", len(rows) == 2)
    removed = await db.blacklist_remove(eid2)
    check("blacklist remove", removed and removed["id"] == eid2)
    check("removed entry gone", await db.is_blacklisted(discord_id=2) is None)

    # reminder tracking + saved-code views/exports
    check("reminder stage default", await db.reminder_stage(1) == 0)
    await db.set_reminder_stage(1, 2)
    check("reminder stage set", await db.reminder_stage(1) == 2)
    await db.record_join(1, "alice")
    check("rejoin resets reminder", await db.reminder_stage(1) == 0)
    await db.ensure_member(3, "carol", "2026-01-02 03:04:05")
    check("ensure_member inserts", await db.reminder_stage(3) == 0)
    await db.set_reminder_stage(3, 1)
    check("stage sticks for backfilled member", await db.reminder_stage(3) == 1)
    check("last_dm starts empty", await db.last_dm(3) is None)
    await db.set_last_dm(3)
    check("last_dm stamped", await db.last_dm(3) is not None)
    await db.record_join(3, "carol")
    check("rejoin clears last_dm", await db.last_dm(3) is None)
    check("rejoin resets the stage", await db.reminder_stage(3) == 0)

    # suspicion flags — the bot's only response to a bad actor
    check("suspect starts off", not (await db.get_member(3))["suspect"])
    await db.mark_suspect(3, "index match", "carol")
    m = await db.get_member(3)
    check("suspect flag set", m["suspect"] == 1)
    check("suspect reason saved", m["suspect_reason"] == "index match")
    check("suspect_at stamped", bool(m["suspect_at"]))
    check("suspect never changes gate status", m["status"] == "unverified")
    check("suspect_list includes them", [r["discord_id"] for r in await db.suspect_list()] == [3])
    check("stats counts suspects", (await db.stats())["suspects"] == 1)
    await db.clear_suspect(3)
    m = await db.get_member(3)
    check("clear_suspect resets flag", m["suspect"] == 0 and m["suspect_reason"] is None)
    check("clear_suspect clears time", m["suspect_at"] is None)
    check("suspect_list empty after clear", await db.suspect_list() == [])
    await db.mark_suspect(99_999, "flag before they joined")
    check("mark_suspect creates a missing row", (await db.get_member(99_999))["suspect"] == 1)
    await db.ensure_member(3, "carol-renamed")
    check("ensure_member doesn't clobber existing", (await db.get_member(3))["username"] == "carol")
    check("saved_codes_count", await db.saved_codes_count() == 1)
    sc = await db.saved_codes()
    check("saved_codes row", bool(sc) and sc[0]["friend_code"] == "RANDOMCODE#1234")
    check("saved_codes indexed flag", sc and sc[0]["indexed"] == 1)
    check("accounts_for_code", len(await db.accounts_for_code("RANDOMCODE#1234")) == 2)
    check("export rows", len(await db.export_rows()) == 2)

    # reports
    rid = await db.report_create(5, "harassment", "was awful", 2, "randomcode#1234", "http://x")
    rep = await db.report_get(rid)
    check("report stored", rep and rep["status"] == "pending")
    check("report target code normalized", rep and rep["target_code"] == "RANDOMCODE#1234")
    check("pending list", any(r["id"] == rid for r in await db.reports_pending()))
    await db.report_resolve(rid, "approved", 99)
    check("report resolved", (await db.report_get(rid))["status"] == "approved")
    check("no longer pending", all(r["id"] != rid for r in await db.reports_pending()))
    await db.report_resolve(rid, "rejected", 99)  # must not overwrite a resolved row
    check("resolved reports are immutable", (await db.report_get(rid))["status"] == "approved")

    await db.audit("TEST", "hello", 7)
    s = await db.stats()
    check("stats index_total", s["index_total"] == 1)
    check("stats index codes", s["index_codes"] == 1)
    check("stats index accounts", s["index_accounts"] == 0)
    check("stats reports approved", s["reports_approved"] == 1)
    check("stats reports pending is zero", s["reports_pending"] == 0)
    check("stats member keys present", {
        "members_unverified", "members_verified", "members_nonau", "members_banned",
    } <= set(s))
    check("stats counters non-negative", all(v >= 0 for v in s.values()))
    last = await db.last_audit()
    check("last_audit returns the latest entry", last and last["action"] == "TEST")
    await db.close()
    check("db closes cleanly", True)


async def migration_tests() -> None:
    print("migrations:")
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    con = sqlite3.connect(path)
    # A members table as it looked before reminder_stage/last_dm_at existed.
    con.execute(
        "CREATE TABLE members (discord_id INTEGER PRIMARY KEY, username TEXT NOT NULL, "
        "friend_code TEXT, status TEXT NOT NULL DEFAULT 'unverified', "
        "joined_at TEXT NOT NULL DEFAULT (datetime('now')), verified_at TEXT, "
        "last_username TEXT)"
    )
    con.commit()
    con.close()

    db = Database(path)
    await db.connect()
    cur = await db.db.execute("PRAGMA table_info(members)")
    cols = {row["name"] for row in await cur.fetchall()}
    check("migration adds reminder_stage", "reminder_stage" in cols)
    check("migration adds last_dm_at", "last_dm_at" in cols)
    check("migration adds suspect", "suspect" in cols)
    check("migration adds suspect_reason", "suspect_reason" in cols)
    check("migration adds suspect_at", "suspect_at" in cols)
    await db.mark_suspect(1, "old row flagged")
    check("suspect works on a migrated row", (await db.get_member(1))["suspect"] == 1)
    await db.set_status(1, "app", username="SomeApp#0001")
    check("app status persists", (await db.get_member(1))["status"] == "app")
    await db.close()


async def cog_tests() -> None:
    print("cogs / commands:")
    os.environ.setdefault("DISCORD_TOKEN", "dummy-token-for-import-test")
    os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "cogtest.db")
    from bot import AUVerifyBot

    bot = AUVerifyBot()
    check("reminder repeat default is 12h", bot.settings.reminder_repeat_hours == 12)
    check("gate schedule default is 48h", bot.settings.gate_hours == 48)
    await bot.db.connect()
    loaded, errors = [], []
    for ext in (
        "cogs.gate",
        "cogs.setup",
        "cogs.reports",
        "cogs.moderation",
        "cogs.help",
        "cogs.info",
    ):
        try:
            await bot.load_extension(ext)
            loaded.append(ext)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ext}: {exc!r}")
    check("all cogs load", not errors)
    for err in errors:
        print(f"       {err}")

    names = {cmd.qualified_name for cmd in bot.tree.walk_commands()}
    for expected in (
        "setup",
        "gate-setup",
        "gate-lock",
        "verify",
        "report",
        "codes",
        "whois",
        "nuke",
        "blacklist",
        "suspect",
        "suspect list",
        "suspect clear",
        "help",
        "ping",
        "stats",
    ):
        check(f"command registered: {expected}", expected in names)

    # The whole point of this change: nothing in the bot may call .ban(.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for root, _dirs, files in os.walk(here):
        if ".venv" in root or "__pycache__" in root or "tests" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                if ".ban(" in fh.read():
                    offenders.append(os.path.relpath(path, here))
    check("no .ban( calls anywhere in the bot", not offenders)
    if offenders:
        print(f"       found in: {', '.join(offenders)}")

    for ext in loaded:
        await bot.unload_extension(ext)
    await bot.db.close()


def helper_tests() -> None:
    print("helpers:")

    class _U:
        """Duck-typed user for is_app_account."""

        def __init__(self, name: str, disc: str = "0", bot: bool = False) -> None:
            self.name, self.discriminator, self.bot, self.system = name, disc, bot, False

        def __str__(self) -> str:
            return self.name if self.discriminator == "0" else f"{self.name}#{self.discriminator}"

    check("app: legacy Name#1234 username", is_app_account(_U("Ballsdex", "0677")))
    check("app: bot flag", is_app_account(_U("HelperBot", "0", bot=True)))
    check("app: legacy shape without discriminator field", is_app_account(_U("SomeApp#0001")))
    check("not app: modern username", not is_app_account(_U("junaid_43664")))
    check("not app: name with digits", not is_app_account(_U("sniper_123_29245")))
    check("not app: real friend code as name", not is_app_account(_U("MERRYFROST")))

    check("mask hides middle", mask_code("RANDOMCODE#1234") == "RAN" + "•" * 10 + "34")
    check("mask short code", mask_code("AB12") == "AB••")
    check("normalize_code strips + uppercases", normalize_code(" ex ample#0000 ") == "EXAMPLE#0000")
    check("reason label for new category", reason_label("predatory") == REASONS[0].name)
    check("reason label for legacy category", reason_label("hate") == "Hate speech / slurs")
    check("reason label falls back to raw", reason_label("mystery") == "mystery")


def main() -> int:
    helper_tests()
    asyncio.run(db_tests())
    asyncio.run(migration_tests())
    asyncio.run(cog_tests())
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
