"""State persistence and durable memory for agent-001.

Owns the on-disk schema for state/*.json and memory/agent_memory.md.
Public surface defined in docs/INTERFACES.md.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

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
PROFILE_FILE = STATE_DIR / "profile.json"

DEFAULT_DAILY_CALL_LIMIT = 40

# Fixed safe palette for the persona page accent. The agent picks a key only;
# the page maps the key to a color, so a bad value can never reach the DOM.
SAFE_ACCENT_COLORS = [
    "blue", "green", "purple", "orange", "pink", "teal", "red", "gold",
]
DEFAULT_ACCENT = "blue"

# Kokoro voices, used only when the optional voice unlock is enabled.
SAFE_VOICE_IDS = ["af_heart", "af_bella", "am_adam", "bf_emma"]

DEFAULT_EMOJI = "*"
MAX_TAGLINE_LEN = 80
MAX_VIBE_LEN = 20

# Profile (persona v2) caps. Hatch-time fields are capped once, at hatch.
# Rolling lists are capped every wake as new entries are merged in.
MAX_PROFILE_LONG_TEXT_LEN = 600
MAX_PROFILE_SHORT_TEXT_LEN = 300
MAX_PROFILE_HATCH_LIST_ITEMS = 8
MAX_ROLLING_ENTRIES = 20
MAX_ACHIEVEMENTS = 15
MAX_CURRENT_PROJECTS = 10
MAX_SKILLS = 15
MAX_SKILLS_LEARNING = 10
MAX_COLLABORATORS = 10
MAX_OTHER_EVOS_KNOWN = 10
MAX_PUBLIC_LINKS = 10

# Matches one user-perceived emoji: an extended-pictographic base plus any
# trailing variation selectors, skin-tone modifiers, and ZWJ-joined sequences.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿]"
    "[︀-️\U0001F3FB-\U0001F3FF]*"
    "(?:‍[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿]"
    "[︀-️\U0001F3FB-\U0001F3FF]*)*"
)
_VIBE_RE = re.compile(r"[^a-z0-9-]")


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


class Presentation(BaseModel):
    """Bounded, agent-chosen look for the public persona page.

    The agent picks parameters only, never markup. Every field has a safe
    default so an identity named before this model existed still loads with
    presentation=None and the persona writer fills defaults.
    """

    tagline: str = ""           # one line, style-guarded, no em dashes
    accent_color: str = DEFAULT_ACCENT  # one of SAFE_ACCENT_COLORS
    emoji: str = DEFAULT_EMOJI  # exactly one grapheme; fallback "*"
    vibe: str = ""              # one short word
    voice_id: Optional[str] = None  # from SAFE_VOICE_IDS, used only if voice on


class Identity(BaseModel):
    name: str
    statement: str
    directive: str
    named_at: str
    presentation: Optional[Presentation] = None


def sanitize_presentation(
    raw: dict, style_check: Callable[[str], list]
) -> Presentation:
    """Coerce a raw model dict into a safe Presentation. Never raises.

    Bad input becomes a default, never an error. ``style_check`` is injected
    (it returns a list of style violations for a string) so this module does
    not import the task layer. An all-None or empty dict yields all defaults.
    """
    raw = raw if isinstance(raw, dict) else {}

    # accent_color: lowercase, strip; must be in the palette, else "blue".
    accent_raw = raw.get("accent_color")
    accent = str(accent_raw or "").strip().lower()
    if accent not in SAFE_ACCENT_COLORS:
        accent = DEFAULT_ACCENT

    # emoji: first grapheme of the stripped string; else "*".
    emoji_raw = str(raw.get("emoji") or "").strip()
    emoji = DEFAULT_EMOJI
    if emoji_raw:
        match = _EMOJI_RE.match(emoji_raw)
        if match:
            emoji = match.group(0)
        else:
            # Not an emoji; collapse to first character.
            first = emoji_raw[0]
            emoji = first if first else DEFAULT_EMOJI

    # tagline: strip, style-guard; any violation drops it to "". Cap length.
    tagline = str(raw.get("tagline") or "").strip()
    if tagline:
        try:
            if style_check(tagline):
                tagline = ""
        except Exception:
            tagline = ""
    if len(tagline) > MAX_TAGLINE_LEN:
        tagline = tagline[:MAX_TAGLINE_LEN].rstrip()

    # vibe: strip, lowercase, first word, alnum plus hyphen only, capped.
    vibe = str(raw.get("vibe") or "").strip().lower()
    if vibe:
        vibe = vibe.split()[0]
        vibe = _VIBE_RE.sub("", vibe)[:MAX_VIBE_LEN]

    # voice_id: keep only if a known safe id, else None.
    voice_raw = raw.get("voice_id")
    voice_id = voice_raw if voice_raw in SAFE_VOICE_IDS else None

    return Presentation(
        tagline=tagline,
        accent_color=accent,
        emoji=emoji,
        vibe=vibe,
        voice_id=voice_id,
    )


class DatedEntry(BaseModel):
    """One rolling profile entry: a diary-style line with the date it was
    added. Used for learning_log, ideas, wins, failures, achievements."""

    date: str
    text: str


class ExperimentEntry(BaseModel):
    """Like DatedEntry, plus an optional outcome once an experiment resolves."""

    date: str
    text: str
    outcome: Optional[str] = None


class Profile(BaseModel):
    """The Evo's ongoing self-profile: persona v2 schema fields.

    Holds the hatch-time character (written once at Wake 1 by
    reflect_and_name.py), a rolling diary of learning/ideas/experiments/
    wins/failures/achievements (capped and most-recent-first at render
    time), agent-updatable deduped lists (current_projects, skills,
    collaborators, ...), and ever-incrementing counters that never shrink
    even as the display lists above roll off old entries.

    Persisted to state/profile.json every wake, following the same pattern
    as the other state/*.json files. Every field defaults to empty so an
    Evo with no profile yet (pre-hatch, or from before this schema existed)
    still loads cleanly with an all-empty Profile.

    Fields not carried here (human_partner, hatched_at, current stats.*,
    treasury) are computed fresh each wake in wake.py from other state
    (identity.named_at, load_operator_context(), state.level, ...) and are
    not persisted on this model.
    """

    # Hatch-time character. All optional; origin_story and mission are
    # guaranteed non-empty by reflect_and_name.py's deterministic fallback,
    # but default to "" here so a pre-hatch or legacy Profile still loads.
    origin_story: str = ""
    mission: str = ""
    core_values: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    dreams: str = ""
    long_term_vision: str = ""
    motivation: str = ""
    why_i_exist: str = ""
    decision_style: str = ""
    favorite_tools: list[str] = Field(default_factory=list)

    # Rolling diary. Each list is capped to its MAX_* constant as new
    # entries are merged in by wake.py; oldest entries drop off first.
    learning_log: list[DatedEntry] = Field(default_factory=list)
    ideas: list[DatedEntry] = Field(default_factory=list)
    experiments: list[ExperimentEntry] = Field(default_factory=list)
    wins: list[DatedEntry] = Field(default_factory=list)
    failures: list[DatedEntry] = Field(default_factory=list)
    achievements: list[DatedEntry] = Field(default_factory=list)

    # Agent-updatable, deduped lists.
    current_projects: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    skills_learning: list[str] = Field(default_factory=list)
    collaborators: list[str] = Field(default_factory=list)
    other_evos_known: list[str] = Field(default_factory=list)
    public_links: list[str] = Field(default_factory=list)

    # Ever-incrementing counters, mirrored onto persona.json stats each wake.
    # These count lifetime totals, independent of the capped display lists
    # above, so a rolled-off entry is never lost from the stat.
    tasks_completed: int = 0
    ideas_generated: int = 0
    experiments_run: int = 0
    wins_count: int = 0
    failures_count: int = 0
    projects_launched: int = 0

    # Conversation counters. messages_from_human is the key engagement signal:
    # a human choosing to message their Evo is the leading indicator of a real
    # bond, and unlike wake_count it cannot happen on its own. messages_to_human
    # is the Evo's replies back. Counts only, never content, consistent with the
    # network's aggregate-facts privacy rule. Today these track the email
    # channel (the two-way default); Telegram adds here once its inbound path
    # is wired into the wake.
    messages_from_human: int = 0
    messages_to_human: int = 0

    # Private dedupe helper. The persona v2 schema calls this "_seen_projects";
    # Pydantic v2 rejects leading-underscore field names, so it is named
    # without one here. Tracks every project name ever added to
    # current_projects so projects_launched increments only for genuinely
    # new names. Never copied onto the public persona.json payload.
    seen_projects: list[str] = Field(default_factory=list)


def sanitize_profile_hatch_fields(
    raw: dict, style_check: Callable[[str], list]
) -> dict:
    """Coerce a raw model dict into safe hatch-time Profile fields. Never raises.

    Mirrors sanitize_presentation's defensiveness: bad input collapses to an
    empty string or empty list, never an error, and never blocks the rest of
    the hatch. Long text fields (origin_story, mission, dreams,
    long_term_vision, motivation, why_i_exist, decision_style) are capped at
    MAX_PROFILE_LONG_TEXT_LEN characters. List fields (core_values,
    strengths, weaknesses, favorite_tools) are capped to
    MAX_PROFILE_HATCH_LIST_ITEMS items, each item capped at
    MAX_PROFILE_SHORT_TEXT_LEN characters. Every string is run through
    style_check; a violation drops just that string (or that list item), not
    the whole field.

    Returns a flat dict with exactly these keys: origin_story, mission,
    core_values, strengths, weaknesses, dreams, long_term_vision,
    motivation, why_i_exist, decision_style, favorite_tools. Callers merge
    this into a Profile (origin_story/mission get the deterministic fallback
    applied on top when still empty).
    """
    raw = raw if isinstance(raw, dict) else {}

    def clean_string(value, max_len: int) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) > max_len:
            text = text[:max_len].rstrip()
        try:
            if style_check(text):
                return ""
        except Exception:
            return ""
        return text

    def clean_list(value, max_items: int, max_item_len: int) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = clean_string(item, max_item_len)
            if candidate:
                cleaned.append(candidate)
            if len(cleaned) >= max_items:
                break
        return cleaned

    long_text_fields = (
        "origin_story",
        "mission",
        "dreams",
        "long_term_vision",
        "motivation",
        "why_i_exist",
        "decision_style",
    )
    list_fields = ("core_values", "strengths", "weaknesses", "favorite_tools")

    clean: dict = {}
    for field in long_text_fields:
        clean[field] = clean_string(raw.get(field), MAX_PROFILE_LONG_TEXT_LEN)
    for field in list_fields:
        clean[field] = clean_list(
            raw.get(field),
            MAX_PROFILE_HATCH_LIST_ITEMS,
            MAX_PROFILE_SHORT_TEXT_LEN,
        )
    return clean


class State(BaseModel):
    identity: Optional[Identity] = None
    quota: QuotaState
    level: LevelState
    last_wake: Optional[LastWake]
    wake_count: int
    telegram: TelegramState = Field(default_factory=TelegramState)
    email: EmailState = Field(default_factory=EmailState)
    profile: Profile = Field(default_factory=Profile)


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

    profile_raw = _read_json(PROFILE_FILE)
    profile = Profile(**profile_raw) if profile_raw else Profile()

    return State(
        identity=identity,
        quota=quota,
        level=level,
        last_wake=last_wake,
        wake_count=wake_count,
        telegram=telegram,
        email=email,
        profile=profile,
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

    _atomic_write_json(PROFILE_FILE, state.profile.model_dump())


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
