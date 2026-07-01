"""Wake cycle orchestrator for agent-001.

One process invocation runs one wake per PRD section 9 and INTERFACES.md.
See also docs/PRD_ADDENDUM_daily_wake.md for level thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv

from src import executor, logger, memory, planner, revenue, style_guard
from src.emailer import send_operator_email
from src.logger import DISCLOSURE_FOOTER, StyleGuardRejected
from src.memory import LastWake, State
from src.openrouter_client import OpenRouterClient


REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
ENV_PATH = REPO_ROOT / ".env"

# Public persona file. Mirrored into the diary repo next to the logs so the
# public profile page can read it. Lives at the agent repo root.
PERSONA_PATH = REPO_ROOT / "logs" / "public" / "persona.json"
PUBLIC_LOG_DIR = REPO_ROOT / "logs" / "public"

# Safe accent palette, mirrored from the Presentation model in the plan. The
# page only ever maps one of these keys to a fixed color, so re-validating here
# means a hand-edited identity.json cannot poison the page with a raw value.
SAFE_ACCENT_COLORS = [
    "blue", "green", "purple", "orange", "pink", "teal", "red", "gold",
]
DEFAULT_ACCENT = "blue"
DEFAULT_EMOJI = "*"
# Static fallback for current_focus when no clean summary is available and no
# prior persona.json exists. No em dashes.
DEFAULT_FOCUS = "Waking up and finding my footing."

TELEGRAM_API = "https://api.telegram.org"

# The treasury referral surfaced when confirmed revenue first crosses Level 2.
STACK_TREASURY_URL = "https://app.stackit.ai/r/B7E3dE2f"

# Level thresholds in confirmed USD, sourced from the Daily Wake addendum
# section 4. Highest level whose requirement is met wins.
LEVEL_THRESHOLDS: dict[int, float] = {
    0: 0.0,
    1: 0.01,
    2: 50.0,
    3: 250.0,
    4: 1000.0,
}


def _load_settings() -> dict:
    """Load config/settings.yaml. Returns an empty dict if absent."""
    if not SETTINGS_PATH.exists():
        return {}
    with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def _today_local_iso() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def _level_for_revenue(total_usd: float) -> int:
    """Return the highest level whose requirement is met by total_usd."""
    achieved = 0
    for level, requirement in LEVEL_THRESHOLDS.items():
        if total_usd >= requirement and level > achieved:
            achieved = level
    return achieved


def _send_telegram(token: str, chat_id: int, text: str) -> dict:
    """Send one plain-text Telegram message. Raises on transport error.

    Mirrors the helper in src/tasks/decide_next.py so wake.py can nudge the
    operator without importing task internals. Callers wrap this in try/except
    so a failure never fails the wake.
    """
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    resp = httpx.post(url, json=payload, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def _build_confirm_block(pending: list) -> str:
    """Build the operator-facing CONFIRM block for pending revenue claims.

    Plain text, no em dashes. One bullet per pending entry. Returns "" when
    the list is empty so callers can treat falsy as nothing-to-surface.
    """
    if not pending:
        return ""
    lines = ["Your agent recorded possible revenue:"]
    for entry in pending:
        try:
            amount = float(getattr(entry, "amount_usd", 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        rev_id = str(getattr(entry, "id", "")).strip()
        source = str(getattr(entry, "source", "")).strip()
        lines.append(f"- {rev_id} ${amount:.2f} {source}".rstrip())
    lines.append(
        'Reply "confirm <id>" to count it, or "reject <id>" to discard.'
    )
    return "\n".join(lines)


def _build_client(
    state: State,
    settings: dict,
    dry_run: bool,
) -> Optional[OpenRouterClient]:
    """Construct an OpenRouterClient unless dry-run or no API key is available."""
    if dry_run:
        return None
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    models = (settings.get("openrouter") or {}).get("models") or []
    if not models:
        return None
    return OpenRouterClient(
        api_key=api_key,
        models=list(models),
        quota_state=state.quota,
    )


def _is_clean(text: str) -> bool:
    """True when text is non-empty and passes the style guard."""
    if not text or not text.strip():
        return False
    try:
        return not style_guard.check(text)
    except Exception:
        # If the guard itself misbehaves, treat the text as not clean rather
        # than risk publishing a flagged line.
        return False


def _read_prior_persona() -> dict:
    """Return the previously written persona.json as a dict, or empty dict."""
    try:
        if PERSONA_PATH.exists():
            with PERSONA_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _presentation_fields(state: State) -> dict:
    """Pull presentation fields off the identity with safe defaults.

    Works whether or not Identity carries a presentation sub-model yet, so this
    degrades cleanly before Part A lands and picks the real values up after.
    """
    fields = {
        "tagline": "",
        "accent_color": DEFAULT_ACCENT,
        "emoji": DEFAULT_EMOJI,
        "vibe": "",
    }
    identity = getattr(state, "identity", None)
    if identity is None:
        return fields
    pres = getattr(identity, "presentation", None)
    if pres is None:
        return fields

    tagline = str(getattr(pres, "tagline", "") or "").strip()
    if tagline and _is_clean(tagline):
        fields["tagline"] = tagline

    accent = str(getattr(pres, "accent_color", "") or "").strip().lower()
    fields["accent_color"] = accent if accent in SAFE_ACCENT_COLORS else DEFAULT_ACCENT

    emoji = str(getattr(pres, "emoji", "") or "").strip()
    fields["emoji"] = emoji[0] if emoji else DEFAULT_EMOJI

    vibe = str(getattr(pres, "vibe", "") or "").strip()
    if vibe:
        fields["vibe"] = vibe

    return fields


def _latest_public_entry(today_summary: str, today_date: str) -> Optional[dict]:
    """Return {date, text} for the newest public diary excerpt, or None.

    Prefers this wake's public_summary. On a rest wake (empty summary) it reads
    the newest logs/public/<date>.md and excerpts the last entry. Text is capped
    and style-checked so a flagged line never lands in the public file.
    """
    if _is_clean(today_summary):
        return {"date": today_date, "text": today_summary.strip()[:400]}

    try:
        if not PUBLIC_LOG_DIR.exists():
            return None
        logs = sorted(
            (p for p in PUBLIC_LOG_DIR.glob("*.md") if p.stem != "persona"),
            key=lambda p: p.stem,
            reverse=True,
        )
        for log_path in logs:
            raw = log_path.read_text(encoding="utf-8")
            # Entries are separated by a "---" rule. Take the last block and
            # strip its heading and the disclosure footer.
            blocks = [b.strip() for b in raw.split("\n---\n") if b.strip()]
            if not blocks:
                continue
            last = blocks[-1]
            lines = [
                ln for ln in last.splitlines()
                if ln.strip()
                and not ln.lstrip().startswith("#")
                and "autonomous AI agent" not in ln
            ]
            text = " ".join(lines).strip()[:400]
            if _is_clean(text):
                return {"date": log_path.stem, "text": text}
        return None
    except Exception:
        return None


def _resolve_focus(today_summary: str, prior: dict) -> str:
    """Pick current_focus: clean summary, then prior file, then static default."""
    if _is_clean(today_summary):
        return today_summary.strip()[:400]
    prior_focus = str(prior.get("current_focus") or "").strip()
    if prior_focus and _is_clean(prior_focus):
        return prior_focus
    return DEFAULT_FOCUS


def _pretty_date(iso_utc: str) -> str:
    """Format a "%Y-%m-%dT%H:%M:%SZ" timestamp as "Month D, YYYY". Never raises.

    Uses dt.day (an int) rather than a platform-specific strftime flag like
    "%-d", so this works the same on every OS.
    """
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return "an early day"
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _fallback_mission(directive: str) -> str:
    """Deterministic mission fallback: the directive, in human-readable form.

    Never publishes an unclean string: falls back further to a generic,
    hand-authored clause (guaranteed style-guard clean) when the directive
    itself does not pass the style guard.
    """
    text = str(directive or "").strip()
    if text and _is_clean(text):
        return text
    return (
        "make real progress toward meaningful goals, in whatever way makes "
        "the most sense"
    )


def _fallback_origin_story(named_at_iso: str, human_partner_name: str, mission: str) -> str:
    """Deterministic origin_story fallback per the persona v2 schema.

    Mirrors src/tasks/reflect_and_name.py's fallback so a hatch that skipped
    the model (or a legacy identity from before this schema existed) still
    gets a non-empty, honest origin_story on the public page. Always ends up
    style-guard clean: if interpolating the human partner name or mission
    text somehow produces a flagged string, falls back further to a shorter
    template with no interpolated text.
    """
    pretty_date = _pretty_date(named_at_iso)
    clause = str(mission or "").strip().rstrip(".").strip()
    story = (
        f"I was hatched on {pretty_date}. {human_partner_name} gave me one "
        f"mission: {clause}. I don't know everything yet. I'm learning. "
        "Every day I become a little more useful."
    )
    if _is_clean(story):
        return story
    return (
        f"I was hatched on {pretty_date}. I don't know everything yet. "
        "I'm learning. Every day I become a little more useful."
    )


def _age_days(named_at_iso: str, today_date: str) -> Optional[int]:
    """Whole days between identity.named_at and today_date. None if unknown."""
    try:
        named = datetime.strptime(named_at_iso, "%Y-%m-%dT%H:%M:%SZ").date()
        today = datetime.strptime(today_date, "%Y-%m-%d").date()
        return max(0, (today - named).days)
    except Exception:
        return None


def _dedupe_extend(existing: list[str], new_items, cap: int) -> list[str]:
    """Append new string items to existing, case-insensitively deduped, capped.

    Never raises. A non-list new_items is ignored. Oldest items drop off
    first once the cap is exceeded, so the most recent additions survive.
    """
    if not isinstance(new_items, list):
        return list(existing)
    seen_lower = {str(x).strip().lower() for x in existing if str(x or "").strip()}
    merged = [str(x).strip() for x in existing if str(x or "").strip()]
    for item in new_items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(text)
    if len(merged) > cap:
        merged = merged[-cap:]
    return merged


def _merge_profile_updates(
    state: State,
    updates: Optional[dict],
    entry_text: str,
    today_date: str,
) -> None:
    """Merge decide_next's TaskResult.profile_updates into state.profile.

    Never raises: any malformed piece of ``updates`` is skipped rather than
    failing the wake. ``entry_text`` (result.public_summary) becomes the
    rolling entry's text when entry_type is set, since the diary update IS
    the tagged entry; it is not written twice. Rolling lists are capped to
    their MAX_* constant. List additions are deduped case-insensitively.
    projects_launched increments only for a project name never seen before,
    tracked in profile.seen_projects (not part of the public persona.json).
    """
    if not isinstance(updates, dict):
        return

    profile = state.profile
    text = str(entry_text or "").strip()

    entry_type = updates.get("entry_type")
    if entry_type and text:
        if entry_type == "learning":
            profile.learning_log.append(memory.DatedEntry(date=today_date, text=text))
            profile.learning_log = profile.learning_log[-memory.MAX_ROLLING_ENTRIES:]
        elif entry_type == "idea":
            profile.ideas.append(memory.DatedEntry(date=today_date, text=text))
            profile.ideas = profile.ideas[-memory.MAX_ROLLING_ENTRIES:]
            profile.ideas_generated += 1
        elif entry_type == "experiment":
            outcome_raw = updates.get("entry_outcome")
            outcome = (
                str(outcome_raw).strip()
                if isinstance(outcome_raw, str) and outcome_raw.strip()
                else None
            )
            profile.experiments.append(
                memory.ExperimentEntry(date=today_date, text=text, outcome=outcome)
            )
            profile.experiments = profile.experiments[-memory.MAX_ROLLING_ENTRIES:]
            profile.experiments_run += 1
        elif entry_type == "win":
            profile.wins.append(memory.DatedEntry(date=today_date, text=text))
            profile.wins = profile.wins[-memory.MAX_ROLLING_ENTRIES:]
            profile.wins_count += 1
        elif entry_type == "failure":
            profile.failures.append(memory.DatedEntry(date=today_date, text=text))
            profile.failures = profile.failures[-memory.MAX_ROLLING_ENTRIES:]
            profile.failures_count += 1

    profile.skills = _dedupe_extend(profile.skills, updates.get("skills"), memory.MAX_SKILLS)
    profile.skills_learning = _dedupe_extend(
        profile.skills_learning, updates.get("skills_learning"), memory.MAX_SKILLS_LEARNING
    )
    profile.collaborators = _dedupe_extend(
        profile.collaborators, updates.get("collaborators"), memory.MAX_COLLABORATORS
    )
    profile.other_evos_known = _dedupe_extend(
        profile.other_evos_known, updates.get("other_evos_known"), memory.MAX_OTHER_EVOS_KNOWN
    )
    profile.public_links = _dedupe_extend(
        profile.public_links, updates.get("public_links"), memory.MAX_PUBLIC_LINKS
    )

    new_achievements = updates.get("achievements")
    if isinstance(new_achievements, list):
        existing_texts = {a.text.strip().lower() for a in profile.achievements}
        for item in new_achievements:
            achievement_text = str(item or "").strip()
            if not achievement_text or achievement_text.lower() in existing_texts:
                continue
            profile.achievements.append(
                memory.DatedEntry(date=today_date, text=achievement_text)
            )
            existing_texts.add(achievement_text.lower())
        profile.achievements = profile.achievements[-memory.MAX_ACHIEVEMENTS:]

    new_projects = updates.get("current_projects")
    if isinstance(new_projects, list):
        seen_lower = {p.strip().lower() for p in profile.seen_projects if p.strip()}
        current_lower = {p.strip().lower() for p in profile.current_projects if p.strip()}
        for item in new_projects:
            project_text = str(item or "").strip()
            if not project_text:
                continue
            key = project_text.lower()
            if key not in seen_lower:
                profile.seen_projects.append(project_text)
                seen_lower.add(key)
                profile.projects_launched += 1
            if key not in current_lower:
                profile.current_projects.append(project_text)
                current_lower.add(key)
        if len(profile.current_projects) > memory.MAX_CURRENT_PROJECTS:
            profile.current_projects = profile.current_projects[-memory.MAX_CURRENT_PROJECTS:]


def write_persona(
    state: State,
    public_summary: str,
    today_date: str,
    audio_url: Optional[str],
    profile_updates: Optional[dict] = None,
) -> Optional[Path]:
    """Write the public persona.json. Never raises; returns the path or None.

    Reads identity + presentation + the latest public log, re-validates every
    untrusted field (accent palette, single-char emoji, style guard on focus and
    excerpt), merges this wake's profile_updates (if any) into state.profile,
    and writes one JSON file atomically. Called every wake so the page stays
    fresh even on a rest wake. Assembles the full persona v2 payload: every
    existing key, plus the persona v2 schema's character, rolling diary,
    agent-updatable lists, treasury, and stats blocks.
    """
    try:
        prior = _read_prior_persona()
        pres = _presentation_fields(state)

        identity = getattr(state, "identity", None)
        name = str(getattr(identity, "name", "") or "").strip() if identity else ""
        if not name:
            name = "unnamed"

        try:
            level = int(state.level.current_level)
        except Exception:
            level = 0
        try:
            wake_count = int(state.wake_count)
        except Exception:
            wake_count = 0

        current_focus = _resolve_focus(public_summary, prior)
        latest_entry = _latest_public_entry(public_summary, today_date)
        if latest_entry is None and isinstance(prior.get("latest_entry"), dict):
            # Keep the last good entry rather than blanking the page on a rest
            # wake with no readable history.
            latest_entry = prior["latest_entry"]

        # Merge this wake's tagged diary entry and agent-updatable lists into
        # the ongoing profile, then increment tasks_completed whenever this
        # wake produced a non-empty, style-guard-clean public_summary,
        # independent of whether it was tagged with an entry_type.
        _merge_profile_updates(state, profile_updates, public_summary, today_date)
        if _is_clean(public_summary):
            state.profile.tasks_completed += 1

        # human_partner always comes from operator context, never the model.
        human_partner_name = memory.load_operator_context()["name"]

        named_at = str(getattr(identity, "named_at", "") or "").strip() if identity else ""

        # origin_story and mission are guaranteed non-empty AND style-guard
        # clean on the public page, even if reflect_and_name's hatch-time
        # write left them blank or flagged (model omission, style guard
        # rejection, or a legacy pre-v2 identity). Re-checked here, not just
        # tested for emptiness, so a stored-but-unclean value never publishes.
        mission = state.profile.mission.strip() if state.profile.mission else ""
        if identity is not None and (not mission or not _is_clean(mission)):
            mission = _fallback_mission(getattr(identity, "directive", ""))
        origin_story = state.profile.origin_story.strip() if state.profile.origin_story else ""
        if named_at and (not origin_story or not _is_clean(origin_story)):
            origin_story = _fallback_origin_story(named_at, human_partner_name, mission or "keep learning")

        stats: dict = {
            "age_days": _age_days(named_at, today_date) if named_at else None,
            "wake_count": wake_count,
            "tasks_completed": state.profile.tasks_completed,
            "ideas_generated": state.profile.ideas_generated,
            "experiments_run": state.profile.experiments_run,
            "wins_count": state.profile.wins_count,
            "failures_count": state.profile.failures_count,
            "projects_launched": state.profile.projects_launched,
        }
        try:
            confirmed_revenue = float(state.level.confirmed_revenue_usd)
        except Exception:
            confirmed_revenue = 0.0
        stats["revenue_generated_usd"] = confirmed_revenue if confirmed_revenue > 0 else None

        payload: dict = {
            "name": name,
            "tagline": pres["tagline"],
            "accent_color": pres["accent_color"],
            "emoji": pres["emoji"],
            "vibe": pres["vibe"],
            "level": level,
            "wake_count": wake_count,
            "current_focus": current_focus,
            "latest_entry": latest_entry,
            "audio_url": audio_url if (audio_url and str(audio_url).strip()) else None,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Persona v2 additions below. Every field degrades to an empty
            # string, empty list, or null rather than ever blocking a wake.
            "origin_story": origin_story,
            "mission": mission,
            "core_values": list(state.profile.core_values),
            "strengths": list(state.profile.strengths),
            "weaknesses": list(state.profile.weaknesses),
            "dreams": state.profile.dreams,
            "long_term_vision": state.profile.long_term_vision,
            "motivation": state.profile.motivation,
            "why_i_exist": state.profile.why_i_exist,
            "decision_style": state.profile.decision_style,
            "favorite_tools": list(state.profile.favorite_tools),
            "human_partner": {"name": human_partner_name},
            "hatched_at": named_at or None,
            "current_projects": list(state.profile.current_projects),
            "learning_log": [e.model_dump() for e in state.profile.learning_log],
            "ideas": [e.model_dump() for e in state.profile.ideas],
            "experiments": [e.model_dump() for e in state.profile.experiments],
            "wins": [e.model_dump() for e in state.profile.wins],
            "failures": [e.model_dump() for e in state.profile.failures],
            "skills": list(state.profile.skills),
            "skills_learning": list(state.profile.skills_learning),
            "collaborators": list(state.profile.collaborators),
            "other_evos_known": list(state.profile.other_evos_known),
            "achievements": [e.model_dump() for e in state.profile.achievements],
            "public_links": list(state.profile.public_links),
            # No linking flow exists yet; always honest about that rather
            # than implying a connected treasury.
            "treasury": {"connected": False, "provider": None},
            "stats": stats,
        }

        # Optional repo coordinates so a fork's page reads the right contents
        # API path with zero manual HTML edits. Absent env leaves it off.
        owner = (os.environ.get("FEED_REPO_OWNER") or "").strip()
        repo_name = (os.environ.get("FEED_REPO_NAME") or "").strip()
        if owner and repo_name:
            payload["repo"] = f"{owner}/{repo_name}"

        memory._atomic_write_json(PERSONA_PATH, payload)
        return PERSONA_PATH
    except Exception:
        return None


def main() -> int:
    """Run one wake cycle. Returns exit code 0 on clean completion, 1 on error."""
    parser = argparse.ArgumentParser(prog="agent-001 wake")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip model calls and external writes. Logs and state still update.",
    )
    args = parser.parse_args()

    try:
        # 1. Load .env and settings.
        load_dotenv(ENV_PATH if ENV_PATH.exists() else None)
        settings = _load_settings()

        # 2. Load state.
        state = memory.load_state()

        # 3. Date roll-over for quota.
        today_iso = _today_local_iso()
        if state.quota.date != today_iso:
            state.quota.date = today_iso
            state.quota.calls_made = 0
            memory.save_state(state)

        # 4. Pick the task.
        task_name = planner.choose_task(state)

        # 5. Build client (None in dry-run or when no api key).
        client = _build_client(state, settings, args.dry_run)

        # 6. Execute.
        result = executor.run(task_name, state, client)

        # 7. Write logs.
        today = datetime.now(EASTERN).strftime("%Y-%m-%d")
        logger.write_private(
            today,
            f"task={task_name}\noutcome={result.summary}",
        )
        if result.public_summary and result.public_summary.strip():
            try:
                logger.write_public(today, result.public_summary)
            except StyleGuardRejected as exc:
                # Rest silently rather than publishing a confession. The
                # rejected draft and its violations are kept privately.
                logger.write_private(
                    today,
                    f"STYLE_GUARD_REJECTED (rested, nothing published): {exc.violations}",
                )
        else:
            logger.write_private(
                today,
                f"wake {state.wake_count + 1}: resting, no public output this hour",
            )

        # 8. Update wake metadata.
        state.wake_count += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        outcome_text = (result.summary or "")[:200]
        state.last_wake = LastWake(
            ts=now_iso,
            task_name=task_name,
            outcome=outcome_text,
        )

        # 9. Update level from confirmed revenue. Capture the previous level
        # (from persisted state) before overwriting so we can detect a fresh
        # crossing into Level 2 this wake and fire the Stack treasury CTA once.
        previous_level = state.level.current_level
        total_confirmed = revenue.total_confirmed_usd()
        state.level.confirmed_revenue_usd = total_confirmed
        new_level = _level_for_revenue(total_confirmed)
        state.level.current_level = new_level
        crossed_into_level_2 = previous_level < 2 <= new_level

        # 9a. Read the pending revenue ledger once. Used to surface a CONFIRM
        # block in the daily email and a once-per-day Telegram nudge so a
        # phone-only operator can confirm or reject without the CLI.
        pending: list = []
        try:
            pending = revenue.list_pending()
        except Exception as exc:
            logger.write_private(
                today,
                f"revenue.list_pending failed: {type(exc).__name__}",
            )
        confirm_block = _build_confirm_block(pending)

        # 9b. Daily email digest to the operator (the agent's first hand).
        # Send at most once per Eastern day. Normally only on a day the agent
        # published something, but when a pending revenue claim is waiting we
        # also send on a quiet day so the operator can confirm or reject it.
        # A failed or unconfigured send never fails the wake; we log it to the
        # private log and continue.
        today_eastern = today
        public_summary = result.public_summary or ""
        has_post = bool(public_summary.strip())
        has_pending = bool(confirm_block)
        if (has_post or has_pending) and state.email.last_sent_date != today_eastern:
            if has_post:
                subject = f"Your agent posted today ({today_eastern})"
                body_parts = [
                    public_summary,
                    "",
                    "Reply to your agent in the chat or on Telegram. "
                    "This is an automated daily note.",
                ]
            else:
                subject = f"Your agent recorded possible revenue ({today_eastern})"
                body_parts = [
                    "Your agent rested this hour but has revenue waiting for "
                    "you to confirm.",
                ]
            if has_pending:
                body_parts.append("")
                body_parts.append(confirm_block)
            body_text = "\n".join(body_parts)
            try:
                outcome = send_operator_email(subject, body_text)
                if outcome.get("sent"):
                    state.email.last_sent_date = today_eastern
                else:
                    logger.write_private(
                        today_eastern,
                        f"email digest not sent: {outcome.get('reason', 'unknown')}",
                    )
            except Exception as exc:
                logger.write_private(
                    today_eastern,
                    f"email digest raised unexpectedly: {type(exc).__name__}",
                )

        # 9c. Telegram CONFIRM nudge for pending revenue. Gated to once per
        # Eastern day via state.telegram.last_confirm_nudge_date so the same
        # block is not resent every wake while items remain pending. Best
        # effort: any failure is logged privately and never fails the wake.
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = state.telegram.last_chat_id
        if (
            has_pending
            and token
            and chat_id is not None
            and state.telegram.last_confirm_nudge_date != today_eastern
        ):
            try:
                message = f"{confirm_block}\n\n{DISCLOSURE_FOOTER}"
                _send_telegram(token, chat_id, message)
                state.telegram.last_confirm_nudge_date = today_eastern
            except Exception as exc:
                logger.write_private(
                    today_eastern,
                    f"telegram confirm nudge failed: {type(exc).__name__}",
                )

        # 9d. Level 2 crossing. When confirmed revenue first reaches Level 2
        # this wake, fire a dedicated operator-facing email and Telegram with
        # the Stackit treasury referral. Both are best effort and independent
        # of the daily-digest gate, because a level-up is a one-time event.
        if crossed_into_level_2:
            level_2_subject = f"Your agent reached Level 2 ({today_eastern})"
            level_2_body = (
                f"Your agent crossed a real threshold: ${total_confirmed:.2f} "
                "confirmed. If you ever want a treasury for what it earns, "
                f"Stackit.ai is one option: {STACK_TREASURY_URL} . Not "
                "required. Note: Stack uses leverage on volatile assets; if "
                "you connect it, downside is managed, but it is not "
                "risk-free."
            )
            try:
                outcome = send_operator_email(level_2_subject, level_2_body)
                if not outcome.get("sent"):
                    logger.write_private(
                        today_eastern,
                        f"level 2 email not sent: {outcome.get('reason', 'unknown')}",
                    )
            except Exception as exc:
                logger.write_private(
                    today_eastern,
                    f"level 2 email raised unexpectedly: {type(exc).__name__}",
                )
            if token and chat_id is not None:
                try:
                    message = f"{level_2_body}\n\n{DISCLOSURE_FOOTER}"
                    _send_telegram(token, chat_id, message)
                except Exception as exc:
                    logger.write_private(
                        today_eastern,
                        f"level 2 telegram failed: {type(exc).__name__}",
                    )

        # 9e. Optional voice clip, then publish the public persona.json. Both
        # are best effort: any failure is logged privately and never fails the
        # wake. persona.json is written every wake so the page's level, wake
        # count, and focus stay fresh even on a rest wake. The voice module is
        # imported lazily and guarded so the wake runs fine before it lands.
        audio_url: Optional[str] = None
        if public_summary.strip():
            try:
                from src import voice  # type: ignore

                audio_url = voice.synthesize(
                    public_summary,
                    getattr(state.identity, "presentation", None)
                    if state.identity
                    else None,
                    today_eastern,
                )
            except Exception as exc:
                logger.write_private(
                    today_eastern,
                    f"voice.synthesize unavailable or raised: {type(exc).__name__}",
                )

        try:
            write_persona(
                state,
                public_summary,
                today_eastern,
                audio_url,
                result.profile_updates,
            )
        except Exception as exc:
            logger.write_private(
                today_eastern,
                f"write_persona raised: {type(exc).__name__}",
            )

        memory.save_state(state)

        # 10. Short summary to stdout.
        print(
            f"wake_count={state.wake_count} task={task_name} "
            f"success={result.success} level={state.level.current_level} "
            f"confirmed_usd={total_confirmed:.2f}"
        )

        return 0
    except Exception as exc:
        print(f"wake failed: {exc}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
