"""SQLite persistence for verification, blacklist index, reports and audit."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    discord_id     INTEGER PRIMARY KEY,
    username       TEXT NOT NULL,
    friend_code    TEXT,
    status         TEXT NOT NULL DEFAULT 'unverified', -- unverified|verified|nonau|banned
    joined_at      TEXT NOT NULL DEFAULT (datetime('now')),
    verified_at    TEXT,
    reminder_stage INTEGER NOT NULL DEFAULT 0,
    last_username  TEXT
);

CREATE TABLE IF NOT EXISTS code_links (
    friend_code  TEXT NOT NULL,
    discord_id   INTEGER NOT NULL,
    first_linked TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (friend_code, discord_id)
);
CREATE INDEX IF NOT EXISTS idx_links_discord ON code_links(discord_id);

CREATE TABLE IF NOT EXISTS blacklist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL, -- friend_code|discord_id
    value       TEXT NOT NULL,
    reason      TEXT NOT NULL,
    evidence    TEXT,
    added_by    INTEGER NOT NULL,
    report_id   INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id    INTEGER NOT NULL,
    category       TEXT NOT NULL,
    target_user_id INTEGER,
    target_code    TEXT,
    details        TEXT NOT NULL,
    evidence       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
    reviewed_by    INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    action    TEXT NOT NULL,
    details   TEXT NOT NULL,
    actor_id  INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _norm_code(raw: str) -> str:
    """Canonical form of a friend code: no spaces, uppercase."""
    return "".join(raw.split()).upper()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Additive migrations for databases created by older versions."""
        cur = await self._db.execute("PRAGMA table_info(members)")
        columns = {row["name"] for row in await cur.fetchall()}
        if "reminder_stage" not in columns:
            await self._db.execute(
                "ALTER TABLE members ADD COLUMN reminder_stage INTEGER NOT NULL DEFAULT 0"
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected")
        return self._db

    # -- settings ----------------------------------------------------------
    async def get_setting(self, key: str) -> str | None:
        cur = await self.db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.db.commit()

    async def get_settings(self, keys: Iterable[str]) -> dict[str, str]:
        keys = list(keys)
        if not keys:
            return {}
        q = ",".join("?" * len(keys))
        cur = await self.db.execute(f"SELECT key,value FROM settings WHERE key IN ({q})", keys)
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    # -- members -----------------------------------------------------------
    async def record_join(self, discord_id: int, username: str) -> None:
        await self.db.execute(
            "INSERT INTO members(discord_id, username) VALUES(?,?) "
            "ON CONFLICT(discord_id) DO UPDATE SET "
            "  last_username=excluded.username, joined_at=datetime('now'), "
            "  reminder_stage=0",
            (discord_id, username),
        )
        await self.db.commit()

    async def get_member(self, discord_id: int) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM members WHERE discord_id=?", (discord_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_status(
        self, discord_id: int, status: str, *, username: str | None = None,
        friend_code: str | None = None,
    ) -> None:
        code = _norm_code(friend_code) if friend_code else None
        await self.db.execute(
            "INSERT INTO members(discord_id, username, friend_code, status, verified_at) "
            "VALUES(?,?,?,?,CASE WHEN ?='verified' THEN datetime('now') END) "
            "ON CONFLICT(discord_id) DO UPDATE SET "
            "  status=excluded.status, "
            "  username=COALESCE(?, members.username), "
            "  friend_code=COALESCE(?, members.friend_code), "
            "  verified_at=COALESCE(excluded.verified_at, members.verified_at)",
            (discord_id, username or str(discord_id), code, status,
             status, username, code),
        )
        await self.db.execute(
            "UPDATE members SET last_username=? WHERE discord_id=?",
            (username or "", discord_id),
        )
        await self.db.commit()

    async def members_with_code(self, friend_code: str) -> list[int]:
        code = _norm_code(friend_code)
        cur = await self.db.execute(
            "SELECT discord_id FROM members WHERE friend_code=?", (code,)
        )
        return [r["discord_id"] for r in await cur.fetchall()]

    async def ensure_member(
        self, discord_id: int, username: str, joined_at: str | None = None
    ) -> None:
        """Backfill a members row for someone who has a gate role but never hit on_join.

        Does nothing if the row already exists, so it never clobbers join/verify data.
        """
        await self.db.execute(
            "INSERT OR IGNORE INTO members(discord_id, username, joined_at) "
            "VALUES(?,?, COALESCE(?, datetime('now')))",
            (discord_id, username, joined_at),
        )
        await self.db.commit()

    async def set_reminder_stage(self, discord_id: int, stage: int) -> None:
        await self.db.execute(
            "UPDATE members SET reminder_stage=? WHERE discord_id=?", (stage, discord_id)
        )
        await self.db.commit()

    async def reminder_stage(self, discord_id: int) -> int:
        cur = await self.db.execute(
            "SELECT reminder_stage FROM members WHERE discord_id=?", (discord_id,)
        )
        row = await cur.fetchone()
        return int(row["reminder_stage"]) if row else 0

    async def members_by_status(self, status: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT * FROM members WHERE status=? ORDER BY verified_at DESC", (status,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- saved friend codes (view / export) --------------------------------
    async def saved_codes(
        self, *, limit: int = 15, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Codes that members have verified with, newest first, plus index status."""
        cur = await self.db.execute(
            "SELECT m.friend_code, m.discord_id, m.username, m.status, m.verified_at, "
            "  EXISTS(SELECT 1 FROM blacklist b WHERE b.kind='friend_code' "
            "         AND b.value=m.friend_code) AS indexed "
            "FROM members m WHERE m.friend_code IS NOT NULL "
            "ORDER BY m.verified_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def saved_codes_count(self) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM members WHERE friend_code IS NOT NULL"
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def accounts_for_code(self, friend_code: str) -> list[dict[str, Any]]:
        """Every Discord account ever linked to a code (alt history)."""
        cur = await self.db.execute(
            "SELECT l.discord_id, l.first_linked, m.username, m.status "
            "FROM code_links l LEFT JOIN members m ON m.discord_id=l.discord_id "
            "WHERE l.friend_code=? ORDER BY l.first_linked",
            (_norm_code(friend_code),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def export_rows(self) -> list[dict[str, Any]]:
        """Flat rows for CSV export: one row per code↔account link."""
        cur = await self.db.execute(
            "SELECT l.friend_code, l.discord_id, l.first_linked, m.username, m.status, "
            "  EXISTS(SELECT 1 FROM blacklist b WHERE b.kind='friend_code' "
            "         AND b.value=l.friend_code) AS indexed "
            "FROM code_links l LEFT JOIN members m ON m.discord_id=l.discord_id "
            "ORDER BY l.friend_code, l.first_linked"
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- code links --------------------------------------------------------
    async def link_code(self, friend_code: str, discord_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO code_links(friend_code, discord_id) VALUES(?,?)",
            (_norm_code(friend_code), discord_id),
        )
        await self.db.commit()

    async def discord_ids_for_code(self, friend_code: str) -> list[int]:
        cur = await self.db.execute(
            "SELECT discord_id FROM code_links WHERE friend_code=?",
            (_norm_code(friend_code),),
        )
        return [r["discord_id"] for r in await cur.fetchall()]

    async def codes_for_discord(self, discord_id: int) -> list[str]:
        cur = await self.db.execute(
            "SELECT friend_code FROM code_links WHERE discord_id=?", (discord_id,)
        )
        return [r["friend_code"] for r in await cur.fetchall()]

    # -- blacklist ---------------------------------------------------------
    async def blacklist_add(
        self, kind: str, value: str, reason: str, added_by: int,
        evidence: str | None = None, report_id: int | None = None,
    ) -> int:
        if kind == "friend_code":
            value = _norm_code(value)
        cur = await self.db.execute(
            "INSERT INTO blacklist(kind, value, reason, evidence, added_by, report_id) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(kind, value) DO UPDATE SET "
            "  reason=excluded.reason, evidence=COALESCE(excluded.evidence, blacklist.evidence) "
            "RETURNING id",
            (kind, value, reason, evidence, added_by, report_id),
        )
        row = await cur.fetchone()
        await self.db.commit()
        return int(row["id"])

    async def is_blacklisted(
        self, *, friend_code: str | None = None, discord_id: int | None = None
    ) -> dict[str, Any] | None:
        clauses: list[str] = []
        params: list[Any] = []
        if friend_code:
            clauses.append("(kind='friend_code' AND value=?)")
            params.append(_norm_code(friend_code))
        if discord_id is not None:
            clauses.append("(kind='discord_id' AND value=?)")
            params.append(str(discord_id))
        if not clauses:
            return None
        cur = await self.db.execute(
            f"SELECT * FROM blacklist WHERE ({' OR '.join(clauses)}) "
            "ORDER BY id LIMIT 1",
            params,
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def blacklist_remove(self, entry_id: int) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "DELETE FROM blacklist WHERE id=? RETURNING *", (entry_id,)
        )
        row = await cur.fetchone()
        await self.db.commit()
        return dict(row) if row else None

    async def blacklist_list(self, limit: int = 25) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT * FROM blacklist ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- reports -----------------------------------------------------------
    async def report_create(
        self, reporter_id: int, category: str, details: str,
        target_user_id: int | None, target_code: str | None, evidence: str | None,
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO reports(reporter_id, category, details, target_user_id, "
            "target_code, evidence) VALUES(?,?,?,?,?,?) RETURNING id",
            (reporter_id, category, details, target_user_id,
             _norm_code(target_code) if target_code else None, evidence),
        )
        row = await cur.fetchone()
        await self.db.commit()
        return int(row["id"])

    async def report_get(self, report_id: int) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM reports WHERE id=?", (report_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def report_resolve(self, report_id: int, status: str, reviewer: int) -> None:
        await self.db.execute(
            "UPDATE reports SET status=?, reviewed_by=?, resolved_at=datetime('now') "
            "WHERE id=? AND status='pending'",
            (status, reviewer, report_id),
        )
        await self.db.commit()

    async def reports_pending(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT * FROM reports WHERE status='pending' ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- audit -------------------------------------------------------------
    async def audit(self, action: str, details: str, actor_id: int | None = None) -> None:
        await self.db.execute(
            "INSERT INTO audit_log(action, details, actor_id) VALUES(?,?,?)",
            (action, details, actor_id),
        )
        await self.db.commit()

    async def last_audit(self) -> dict[str, Any] | None:
        """Most recent audit entry, for /stats."""
        cur = await self.db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
        return dict(row) if row else None

    async def stats(self) -> dict[str, int]:
        """Counter snapshot for /stats: members, codes, index, reports."""
        out: dict[str, int] = {}

        cur = await self.db.execute(
            "SELECT status, COUNT(*) AS n FROM members GROUP BY status"
        )
        for row in await cur.fetchall():
            out[f"members_{row['status']}"] = int(row["n"])
        out.setdefault("members_unverified", 0)
        out.setdefault("members_verified", 0)
        out.setdefault("members_nonau", 0)
        out.setdefault("members_banned", 0)

        counters = {
            "members_total": "SELECT COUNT(*) AS n FROM members",
            "codes": "SELECT COUNT(*) AS n FROM members WHERE friend_code IS NOT NULL",
            "links": "SELECT COUNT(*) AS n FROM code_links",
            "index_total": "SELECT COUNT(*) AS n FROM blacklist",
            "index_codes": "SELECT COUNT(*) AS n FROM blacklist WHERE kind='friend_code'",
            "index_accounts": "SELECT COUNT(*) AS n FROM blacklist WHERE kind='discord_id'",
            "reports_pending": "SELECT COUNT(*) AS n FROM reports WHERE status='pending'",
            "reports_approved": "SELECT COUNT(*) AS n FROM reports WHERE status='approved'",
            "reports_rejected": "SELECT COUNT(*) AS n FROM reports WHERE status='rejected'",
            "audit_entries": "SELECT COUNT(*) AS n FROM audit_log",
        }
        for key, sql in counters.items():
            cur = await self.db.execute(sql)
            row = await cur.fetchone()
            out[key] = int(row["n"]) if row else 0
        return out
