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

import yaml
from dotenv import load_dotenv

from src import executor, logger, memory, planner, revenue
from src.emailer import send_operator_email
from src.logger import StyleGuardRejected
from src.memory import LastWake, State
from src.openrouter_client import OpenRouterClient


REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "config" / "settings.yaml"
ENV_PATH = REPO_ROOT / ".env"

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

        # 9. Update level from confirmed revenue.
        total_confirmed = revenue.total_confirmed_usd()
        state.level.confirmed_revenue_usd = total_confirmed
        state.level.current_level = _level_for_revenue(total_confirmed)

        # 9b. Daily email digest to the operator (the agent's first hand).
        # Send at most once per Eastern day, and only on a day the agent
        # actually published something. A failed or unconfigured send never
        # fails the wake; we log it to the private log and continue.
        today_eastern = today
        public_summary = result.public_summary or ""
        if public_summary.strip() and state.email.last_sent_date != today_eastern:
            subject = f"Your agent posted today ({today_eastern})"
            body_text = (
                f"{public_summary}\n\n"
                "Reply to your agent in the chat or on Telegram. "
                "This is an automated daily note."
            )
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
