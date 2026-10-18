#!/usr/bin/env python3
"""Office Bot watchdog — alert when the bot is down or its event loop has wedged.

Run from a systemd timer (see ``au-verify-watchdog.timer``). Deliberately
standard-library only: if the virtualenv is broken, the watchdog still runs.

The bot is considered unhealthy when either:

* ``systemctl --user is-active au-verify`` is not ``active``, or
* the heartbeat file the bot rewrites every minute is older than
  ``WATCHDOG_STALE_SECONDS`` (default 600). This second check is the point —
  ``Restart=on-failure`` cannot see a process that is alive but stuck.

Alerts go to the first target that is configured:

1. ``WATCHDOG_WEBHOOK_URL`` — a channel webhook, which keeps working even if
   the bot token is rotated or the bot cannot log in.
2. ``DISCORD_TOKEN`` plus the log/report channel stored in the database.
3. nowhere — the failure is written to the journal only.

To avoid spam it only messages on a *state change* (up -> down, down -> up),
tracked in a small JSON file next to the database.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SERVICE = os.environ.get("WATCHDOG_SERVICE", "au-verify").strip() or "au-verify"
STALE_SECONDS = int(os.environ.get("WATCHDOG_STALE_SECONDS", "600") or 600)
RESTART = os.environ.get("WATCHDOG_RESTART", "0").strip().lower() in {"1", "true", "yes", "on"}

DB_PATH = Path(os.environ.get("DB_PATH", "data/au_verify.db"))
HEARTBEAT = DB_PATH.parent / "heartbeat"
STATE_FILE = Path(os.environ.get("WATCHDOG_STATE", str(DB_PATH.parent / "watchdog.state")))

USER_AGENT = "office-bot-watchdog/1.0"


# --- helpers ---------------------------------------------------------------
def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def human(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def service_active() -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"could not query systemd: {exc!r}")
        return False
    return proc.stdout.strip() == "active"


def heartbeat_age() -> float | None:
    """Seconds since the bot last touched its heartbeat file, or None."""
    try:
        return max(time.time() - HEARTBEAT.stat().st_mtime, 0.0)
    except OSError:
        return None


def channel_from_db() -> int | None:
    """Reuse the configured log/report channel for alerts, if we can read it."""
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        log(f"could not open database: {exc!r}")
        return None
    try:
        for key in ("log_channel_id", "report_channel_id"):
            row = con.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if row and row[0]:
                try:
                    return int(row[0])
                except (TypeError, ValueError):
                    continue
    except sqlite3.Error as exc:
        log(f"could not read settings: {exc!r}")
    finally:
        con.close()
    return None


# --- alerting --------------------------------------------------------------
def _post(url: str, payload: dict, headers: dict) -> bool:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError) as exc:
        log(f"alert delivery failed: {exc!r}")
        return False


def notify(content: str) -> bool:
    webhook = (os.environ.get("WATCHDOG_WEBHOOK_URL") or "").strip()
    if webhook:
        return _post(
            webhook,
            {"content": content[:1900], "username": "Office Bot watchdog"},
            {"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )

    token = (os.environ.get("DISCORD_TOKEN") or "").strip()
    channel = channel_from_db()
    if token and channel:
        return _post(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            {"content": content[:1900]},
            {
                "Content-Type": "application/json",
                "Authorization": f"Bot {token}",
                "User-Agent": USER_AGENT,
            },
        )

    log("no alert target configured (set WATCHDOG_WEBHOOK_URL) — journal only")
    return False


# --- state -----------------------------------------------------------------
def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        log(f"could not write state: {exc!r}")


def restart_service() -> None:
    log(f"WATCHDOG_RESTART is set — restarting {SERVICE}")
    try:
        subprocess.run(
            ["systemctl", "--user", "restart", SERVICE],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"restart failed: {exc!r}")


def main() -> int:
    host = socket.gethostname()
    now = datetime.now(timezone.utc)

    problems: list[str] = []
    if not service_active():
        problems.append(f"service `{SERVICE}` is not active")
    age = heartbeat_age()
    if age is None:
        problems.append("no heartbeat file — the bot has never reported in")
    elif age > STALE_SECONDS:
        problems.append(f"heartbeat is {human(age)} old (limit {human(STALE_SECONDS)})")

    healthy = not problems
    previous = read_state()
    was = previous.get("state")

    if was is None:
        # First ever run: record where we are but announce nothing, so a fresh
        # install cannot fire a false alarm before the bot's first heartbeat.
        log(f"first run: {'up' if healthy else 'down'} {problems or ''}".rstrip())
        write_state({
            "state": "up" if healthy else "down",
            "since": now.isoformat(),
            "notified": False,
        })
        return 0 if healthy else 1

    if healthy:
        if was == "down" and previous.get("notified"):
            downtime = ""
            since = previous.get("since")
            if since:
                try:
                    started = datetime.fromisoformat(since)
                    downtime = f" Down for **{human((now - started).total_seconds())}**."
                except ValueError:
                    pass
            notify(f"🟢 **Office Bot is back up** on `{host}`.{downtime}")
            log("recovery notice sent")
        write_state({"state": "up", "since": previous.get("since"), "notified": False})
        return 0

    if was == "up" or not previous.get("notified"):
        notify(
            f"🔴 **Office Bot looks down** on `{host}`.\n"
            + "\n".join(f"• {problem}" for problem in problems)
            + f"\nLast heartbeat: {human(age) + ' ago' if age is not None else 'never'}."
        )
        log(f"down notice sent: {problems}")
        write_state({"state": "down", "since": now.isoformat(), "notified": True})
        if RESTART:
            restart_service()
    else:
        # Still down and already announced — stay quiet, keep the original time.
        write_state({"state": "down", "since": previous.get("since"), "notified": True})

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
