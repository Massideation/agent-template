"""Wake cycle orchestrator for agent-001.

One process invocation runs one wake per PRD section 9 and INTERFACES.md.
See also docs/PRD_ADDENDUM_daily_wake.md for level thresholds.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv

from src import executor, logger, memory, planner, revenue
from src.emailer import send_operator_email
from src.logger import DISCLOSURE_FOOTER, StyleGuardRejected
from src.memory import LastWake, State
from src.openrouter_client import OpenRouterClient


REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
ENV_PATH = REPO_ROOT / ".env"

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
                "Your agent reached Level 2 with real revenue. Now reinvest "
                "what it earned: a smarter brain via paid API credits, and a "
                "treasury via Stackit.ai, where your earnings are invested and "
                "actively managed, protected from liquidation. Open its wallet "
                f"and treasury: {STACK_TREASURY_URL} . Note: Stack uses leverage "
                "on volatile assets; you are protected from liquidation and "
                "downside is managed, but it is not risk-free."
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
