"""State persistence and durable memory for agent-001.

Owns the on-disk schema for state/*.json and memory/agent_memory.md.
Public surface defined in docs/INTERFACES.md.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
MEMORY_DIR = REPO_ROOT / "memory"
MEMORY_FILE = MEMORY_DIR / "agent_memory.md"
SETTINGS_FILE = REPO_ROOT / "config" / "settings.yaml"

QUOTA_FILE = STATE_DIR / "quota.json"
LEVEL_FILE = STATE_DIR / "level.json"
LAST_WAKE_FILE = STATE_DIR / "last_wake.json"
WAKE_COUNT_FILE = STATE_DIR / "wake_count.json"
TELEGRAM_FILE = STATE_DIR / "telegram.json"
EMAIL_FILE = STATE_DIR / "email.json"
IDENTITY_FILE = STATE_DIR / "identity.json"

DEFAULT_DAILY_CALL_LIMIT = 40


class QuotaState(BaseModel):
    date: str
    calls_made: int
    calls_limit: int


class LevelState(BaseModel):
    current_level: int
    confirmed_revenue_usd: float


class LastWake(BaseModel):
    ts: str
    task_name: str
    outcome: str


class TelegramState(BaseModel):
    last_update_id: int = 0
    last_chat_id: Optional[int] = None
    operator_telegram_user_id: Optional[int] = None
    # Eastern YYYY-MM-DD of the last pending-revenue confirm nudge sent on
    # Telegram. Used to keep the nudge to once per day while items are pending.
    last_confirm_nudge_date: str = ""


class EmailState(BaseModel):
    last_sent_date: str = ""  # Eastern YYYY-MM-DD of last digest sent.


class Identity(BaseModel):
    name: str
    statement: str
    directive: str
    named_at: str


class State(BaseModel):
    identity: Optional[Identity] = None
    quota: QuotaState
    level: LevelState
    last_wake: Optional[LastWake]
    wake_count: int
    telegram: TelegramState = Field(default_factory=TelegramState)
    email: EmailState = Field(default_factory=EmailState)


def _today_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_state() -> State:
    """Load State from state/*.json. Missing files become typed defaults."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    identity_raw = _read_json(IDENTITY_FILE)
    identity = Identity(**identity_raw) if identity_raw else None

    quota_raw = _read_json(QUOTA_FILE)
    if quota_raw:
        quota = QuotaState(**quota_raw)
    else:
        quota = QuotaState(
            date=_today_local(),
            calls_made=0,
            calls_limit=DEFAULT_DAILY_CALL_LIMIT,
        )

    level_raw = _read_json(LEVEL_FILE)
    if level_raw:
        level = LevelState(**level_raw)
    else:
        level = LevelState(current_level=0, confirmed_revenue_usd=0.0)

    last_wake_raw = _read_json(LAST_WAKE_FILE)
    last_wake = LastWake(**last_wake_raw) if last_wake_raw else None

    wake_count_raw = _read_json(WAKE_COUNT_FILE)
    wake_count = int(wake_count_raw["count"]) if wake_count_raw else 0

    telegram_raw = _read_json(TELEGRAM_FILE)
    telegram = TelegramState(**telegram_raw) if telegram_raw else TelegramState()

    email_raw = _read_json(EMAIL_FILE)
    email = EmailState(**email_raw) if email_raw else EmailState()

    return State(
        identity=identity,
        quota=quota,
        level=level,
        last_wake=last_wake,
        wake_count=wake_count,
        telegram=telegram,
        email=email,
    )


def save_state(state: State) -> None:
    """Persist State to state/*.json atomically. Replaces, does not merge."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if state.identity is not None:
        _atomic_write_json(IDENTITY_FILE, state.identity.model_dump())
    elif IDENTITY_FILE.exists():
        IDENTITY_FILE.unlink()

    legacy_offer_file = STATE_DIR / "offer.json"
    if legacy_offer_file.exists():
        legacy_offer_file.unlink()

    _atomic_write_json(QUOTA_FILE, state.quota.model_dump())
    _atomic_write_json(LEVEL_FILE, state.level.model_dump())

    if state.last_wake is not None:
        _atomic_write_json(LAST_WAKE_FILE, state.last_wake.model_dump())
    elif LAST_WAKE_FILE.exists():
        LAST_WAKE_FILE.unlink()

    _atomic_write_json(WAKE_COUNT_FILE, {"count": int(state.wake_count)})

    _atomic_write_json(TELEGRAM_FILE, state.telegram.model_dump())

    _atomic_write_json(EMAIL_FILE, state.email.model_dump())


def append_memory(line: str) -> None:
    """Append one timestamped line to memory/agent_memory.md."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clean = line.rstrip("\n")
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts} {clean}\n")


def read_memory() -> str:
    """Return the full contents of memory/agent_memory.md, or empty string."""
    if not MEMORY_FILE.exists():
        return ""
    with MEMORY_FILE.open("r", encoding="utf-8") as f:
        return f.read()


def _empty_profile() -> dict:
    """Return the all-empty operator profile dict (safe fallback)."""
    return {
        "niche": "",
        "audience": "",
        "offer": "",
        "payment_link": "",
        "goal": "",
    }


def load_operator_context() -> dict:
    """Return operator identity and profile for prompts and disclosures.

    Returns a dict with these keys, all always present:

      - "name": str. From the OPERATOR_NAME environment variable, default
        "your operator".
      - "products": list[dict]. From config/settings.yaml under
        operator.products, where each item is a {name, description} mapping.
      - "profile": dict with the five string fields niche, audience, offer,
        payment_link, and goal. Read from config/settings.yaml under
        operator.profile. Each field is coerced to a stripped string, so a
        missing key, None, or non-string value collapses to "".

    Any read or parse failure yields an empty products list and the all-empty
    profile dict, so a half-filled or deleted block never crashes a wake.
    """
    name = os.environ.get("OPERATOR_NAME", "your operator")

    products: list[dict] = []
    profile = _empty_profile()
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}
        operator = settings.get("operator") or {}

        raw_products = operator.get("products") or []
        if isinstance(raw_products, list):
            products = [p for p in raw_products if isinstance(p, dict)]

        raw_profile = operator.get("profile")
        if isinstance(raw_profile, dict):
            for key in profile:
                profile[key] = str(raw_profile.get(key) or "").strip()
    except Exception:
        products = []
        profile = _empty_profile()

    return {"name": name, "products": products, "profile": profile}


def load_addendum_context() -> dict:
    """Return a frozen snapshot of constants from the Daily Wake addendum.

    Hardcoded so the planner does not parse markdown at runtime.
    Sourced from docs/PRD_ADDENDUM_daily_wake.md sections 3, 4, and 5.
    """
    level_thresholds = {
        0: {
            "requirement_usd": 0.0,
            "wakes_per_day_min": 1,
            "wakes_per_day_max": 1,
            "model_budget": "free_only",
        },
        1: {
            "requirement_usd": 0.01,
            "wakes_per_day_min": 1,
            "wakes_per_day_max": 2,
            "model_budget": "free_plus_buffer",
        },
        2: {
            "requirement_usd": 50.0,
            "wakes_per_day_min": 2,
            "wakes_per_day_max": 4,
            "model_budget": "ten_usd_credits",
        },
        3: {
            "requirement_usd": 250.0,
            "wakes_per_day_min": 4,
            "wakes_per_day_max": 8,
            "model_budget": "paid_fallback",
        },
        4: {
            "requirement_usd": 1000.0,
            "wakes_per_day_min": 24,
            "wakes_per_day_max": 24,
            "model_budget": "earned_budget",
        },
    }

    return {
        "level_thresholds": level_thresholds,
        "max_calls_per_day_level_0": DEFAULT_DAILY_CALL_LIMIT,
    }
