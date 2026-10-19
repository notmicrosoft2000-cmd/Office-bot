"""Environment-based configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Among Us friend codes are account-bound and unchangeable, but there is no
# official API to verify them, so we only validate their *format*.  Two shapes
# are known to exist: "WordWord#1234" (account-era codes) and bare numeric
# codes.  Override with FC_PATTERN if Innersloth changes the format.
DEFAULT_FC_PATTERN = r"^[A-Za-z]{2,16}#[0-9]{4}$"


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int | None
    db_path: str
    # How long a new member has to act before the reminder escalations land.
    # Nothing is ever kicked for timing out: the bot just keeps nudging.
    gate_hours: int
    # After the final reminder, how often to nudge unverified members again.
    reminder_repeat_hours: int
    fc_pattern: str
    log_level: str
    # What to do when someone who clicked "I don't play Among Us" submits a code:
    #   warn -> verify them, flag for review, and DM them about it (default)
    #   flag -> the same, but only moderators are told
    #   off  -> just verify them normally
    # There is no "ban" option and never will be: nothing in this bot bans.
    lie_punishment: str

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent


def _int_or_none(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    lie = (os.getenv("LIE_PUNISHMENT", "warn").strip().lower() or "warn")
    if lie == "ban":  # historical value — automated bans were removed
        lie = "warn"
    if lie not in {"warn", "flag", "off"}:
        lie = "warn"
    return Settings(
        token=token,
        guild_id=_int_or_none(os.getenv("GUILD_ID", "").strip()),
        db_path=os.getenv("DB_PATH", "data/au_verify.db"),
        gate_hours=int(os.getenv("GATE_HOURS", "48")),
        reminder_repeat_hours=int(os.getenv("REMINDER_REPEAT_HOURS", "12")),
        fc_pattern=os.getenv("FC_PATTERN", DEFAULT_FC_PATTERN),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        lie_punishment=lie,
    )
