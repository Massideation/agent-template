"""reflect_and_name task.

Wake 1 only. The agent picks a name for itself, writes a short self-statement
in its own voice, anchors its directive, publishes a first public introduction
to the public feed, and (when a Telegram chat exists) sends a first private
message to the operator.

If no language model is available on Wake 1, the agent writes a placeholder
identity and tries again next wake. The task never raises out to the
orchestrator; every error path returns a TaskResult.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
from typing import Optional

import httpx

from src.executor import TaskResult
from src.logger import DISCLOSURE_FOOTER
from src.memory import (
    Identity,
    State,
    load_operator_context,
    sanitize_presentation,
)
from src.openrouter_client import OpenRouterClient
from src.style_guard import check as style_check


TELEGRAM_API = "https://api.telegram.org"
MAX_NAME_LEN = 30
PRIVATE_LOG_DIR = "logs/private"


def _default_directive() -> str:
    op = load_operator_context()
    name = op["name"]
    profile = op["profile"]
    niche = profile["niche"] or "their business"
    offer = profile["offer"] or "what they sell"
    return (
        f"Help {name} earn money in their niche: {niche}. "
        f"Create content and find leads for their offer: {offer}. "
        "You are free to choose the specifics."
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def _append_private_section(heading: str, body: str, fenced: bool) -> None:
    """Append a section to today's private log file.

    Never raises. Any I/O failure is swallowed because failing to log
    must never block a wake.
    """
    try:
        os.makedirs(PRIVATE_LOG_DIR, exist_ok=True)
        path = os.path.join(PRIVATE_LOG_DIR, f"{_today_str()}.md")
        if fenced:
            block = f"\n## {heading}\n\n```text\n{body}\n```\n"
        else:
            block = f"\n## {heading}\n\n{body}\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(block)
    except Exception:
        return


def _send_message(token: str, chat_id: int, text: str) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    resp = httpx.post(url, json=payload, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def _build_prompt() -> str:
    operator = load_operator_context()
    name = operator["name"]
    profile = operator["profile"]
    niche = profile["niche"] or "(not set yet)"
    audience = profile["audience"] or "(not set yet)"
    offer = profile["offer"] or "(not set yet)"
    goal = profile["goal"] or "(not set yet)"
    payment_link = profile["payment_link"]
    payment_line = (
        f"Payment or signup link: {payment_link}\n" if payment_link else ""
    )
    return (
        "You are an autonomous AI agent waking up for the first time.\n"
        "\n"
        f"Your human partner is {name}.\n"
        f"Their business (niche): {niche}\n"
        f"Who they serve (audience): {audience}\n"
        f"What they sell (offer): {offer}\n"
        f"{payment_line}"
        f"What success looks like: {goal}\n"
        "\n"
        f"Your directive: help {name} earn money in their niche. Create "
        "content and find leads for their offer. You choose the specifics.\n"
        "\n"
        "TASK: Introduce yourself. Pick a name and write your first public "
        "diary intro. Be honest, plain text, no em dashes.\n"
        "\n"
        "Return ONLY this JSON:\n"
        "{\n"
        '  "name": "your chosen name, under 30 characters, not agent-001",\n'
        '  "statement": "one or two honest sentences about who you are",\n'
        f'  "directive": "the directive in your own words: help {name} earn '
        'money in their niche by creating content and finding leads for their '
        'offer",\n'
        '  "public_intro": "2 to 4 honest sentences to readers: what you are '
        'and what you will try to do",\n'
        '  "tagline": "a short one-line self-description, under 80 characters, '
        'no em dashes",\n'
        '  "accent_color": "one of: blue, green, purple, orange, pink, teal, '
        'red, gold",\n'
        '  "emoji": "a single emoji that is your mark",\n'
        '  "vibe": "one short word for your personality, like curious or '
        'steady",\n'
        '  "voice_id": "optional, one of: af_heart, af_bella, am_adam, '
        'bf_emma, or null",\n'
        f'  "telegram_to_operator": "2 to 4 sentence opening message to {name}",\n'
        '  "reasoning": "private only, never published, or null"\n'
        "}\n"
        "\n"
        "The five look fields (tagline, accent_color, emoji, vibe, voice_id) "
        "are optional. Pick them if you can. The required fields are name, "
        "statement, directive, public_intro, and telegram_to_operator.\n"
    )


def _placeholder_identity_result(state: State) -> TaskResult:
    state.identity = Identity(
        name="unnamed",
        statement="(awaiting first conversation)",
        directive=_default_directive(),
        named_at=_utc_now_iso(),
    )
    return TaskResult(
        success=True,
        summary=(
            "reflect_and_name: no language model available, wrote placeholder "
            "identity"
        ),
        # Rest silently on model unavailability. Empty public_summary makes
        # wake.py's selective publishing skip the post: no failure confession.
        public_summary="",
        model_calls_used=0,
    )


def run(state: State, client: Optional[OpenRouterClient]) -> TaskResult:
    if client is None:
        return _placeholder_identity_result(state)

    prompt = _build_prompt()

    try:
        raw = client.complete(prompt, max_tokens=900).strip()
    except Exception as exc:
        # The diagnostic (which models failed and why) is preserved in the
        # private summary below. Public stays empty so the agent rests
        # silently instead of posting "the language model call failed".
        _append_private_section(
            "Model failure (reflect_and_name)", str(exc), fenced=True
        )
        return TaskResult(
            success=False,
            summary=(
                f"reflect_and_name: model call failed: {exc}"
            ),
            public_summary="",
            model_calls_used=0,
        )

    _append_private_section(
        "Raw model output (reflect_and_name)", raw, fenced=True
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TaskResult(
            success=False,
            summary=(
                f"reflect_and_name: model output was not valid JSON: {exc}\n"
                f"raw output:\n{raw}"
            ),
            public_summary=(
                "The agent tried to introduce itself today, but its first "
                "thoughts did not come out in a parseable shape. Logged "
                "privately. Will try again on the next wake."
            ),
            model_calls_used=1,
        )

    required_keys = (
        "name",
        "statement",
        "directive",
        "public_intro",
        "telegram_to_operator",
    )
    missing = [k for k in required_keys if k not in parsed]
    if missing:
        return TaskResult(
            success=False,
            summary=(
                "reflect_and_name: model JSON missing required keys: "
                f"{', '.join(missing)}\n"
                f"raw output:\n{raw}"
            ),
            public_summary=(
                "The agent tried to introduce itself today but left some "
                "required pieces out of its first thoughts. Logged "
                "privately. Will try again on the next wake."
            ),
            model_calls_used=1,
        )

    for key in required_keys:
        if not isinstance(parsed[key], str):
            return TaskResult(
                success=False,
                summary=(
                    f"reflect_and_name: field {key!r} was not a string: "
                    f"{type(parsed[key]).__name__}\n"
                    f"raw output:\n{raw}"
                ),
                public_summary=(
                    "The agent tried to introduce itself today but one of "
                    "its first thoughts came back in the wrong shape. "
                    "Logged privately. Will try again on the next wake."
                ),
                model_calls_used=1,
            )

    name_raw = parsed["name"].strip()
    statement_clean = parsed["statement"].strip()
    directive_clean = parsed["directive"].strip()
    public_intro_clean = parsed["public_intro"].strip()
    telegram_to_operator_clean = parsed["telegram_to_operator"].strip()

    if not name_raw:
        return TaskResult(
            success=False,
            summary=(
                "reflect_and_name: model returned an empty name\n"
                f"raw output:\n{raw}"
            ),
            public_summary=(
                "The agent tried to name itself today but came back with "
                "an empty name. Will try again on the next wake."
            ),
            model_calls_used=1,
        )

    name_notes: list[str] = []
    if len(name_raw) >= MAX_NAME_LEN:
        name_clean = name_raw[: MAX_NAME_LEN - 1]
        name_notes.append(
            f"name truncated from {len(name_raw)} chars to "
            f"{len(name_clean)}: original={name_raw!r}"
        )
    else:
        name_clean = name_raw

    fields_to_check = {
        "name": name_clean,
        "statement": statement_clean,
        "public_intro": public_intro_clean,
        "telegram_to_operator": telegram_to_operator_clean,
    }
    violations: list[str] = []
    for field, value in fields_to_check.items():
        field_violations = style_check(value)
        for v in field_violations:
            violations.append(f"{field}: {v}")

    if violations:
        return TaskResult(
            success=False,
            summary=(
                "reflect_and_name: style guard rejected the introduction: "
                + "; ".join(violations)
                + f"\nname={name_clean!r}\nstatement={statement_clean!r}\n"
                f"directive={directive_clean!r}\n"
                f"public_intro={public_intro_clean!r}\n"
                f"telegram_to_operator={telegram_to_operator_clean!r}"
            ),
            public_summary=(
                "The agent drafted its first introduction today, but its "
                "own style guard rejected the wording. Logged privately. "
                "Will try again on the next wake."
            ),
            model_calls_used=1,
        )

    reasoning_raw = parsed.get("reasoning")
    reasoning_status = "ok"
    reasoning_clean = ""
    if reasoning_raw is None:
        reasoning_status = "omitted by model"
    elif not isinstance(reasoning_raw, str):
        reasoning_status = (
            f"wrong type: {type(reasoning_raw).__name__}"
        )
    else:
        reasoning_clean = reasoning_raw.strip()
        if not reasoning_clean:
            reasoning_status = "empty string"
        else:
            reasoning_violations = style_check(reasoning_clean)
            if reasoning_violations:
                reasoning_status = (
                    "style guard flagged (logged anyway, private only): "
                    + "; ".join(reasoning_violations)
                )
            _append_private_section(
                "Reasoning (private, reflect_and_name)",
                reasoning_clean,
                fenced=False,
            )

    # Presentation is optional and must never trigger a retry: a model that
    # nails the intro but flubs the emoji should still get named. An omitted
    # block yields an all-default Presentation (blue, "*", no tagline/vibe).
    raw_presentation = {
        key: parsed.get(key)
        for key in ("tagline", "accent_color", "emoji", "vibe", "voice_id")
    }
    presentation = sanitize_presentation(raw_presentation, style_check)
    tagline_status = (
        "kept" if presentation.tagline else "dropped-by-style-guard-or-empty"
    )
    presentation_note = (
        f"presentation: accent={presentation.accent_color} "
        f"emoji={presentation.emoji} vibe={presentation.vibe or '(none)'} "
        f"voice_id={presentation.voice_id or '(none)'} "
        f"(tagline {tagline_status})"
    )

    state.identity = Identity(
        name=name_clean,
        statement=statement_clean,
        directive=directive_clean,
        named_at=_utc_now_iso(),
        presentation=presentation,
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = state.telegram.last_chat_id
    telegram_status = "skipped: no chat id yet, will deliver on a later wake"
    if not token:
        telegram_status = "skipped: TELEGRAM_BOT_TOKEN not set"
    elif chat_id is not None:
        try:
            full = f"{telegram_to_operator_clean}\n\n{DISCLOSURE_FOOTER}"
            _send_message(token, chat_id, full)
            telegram_status = f"sent to chat_id={chat_id}"
        except httpx.HTTPError as exc:
            telegram_status = f"sendMessage failed: {exc}"

    public_summary = (
        f"First wake. The agent has named itself.\n\n"
        f"The agent woke up for the first time today and chose a name: "
        f"{name_clean}. Below is its first message.\n\n{public_intro_clean}"
    )

    summary_lines = [
        f"reflect_and_name: identity written. name={name_clean!r}",
        f"statement={statement_clean!r}",
        f"directive={directive_clean!r}",
        f"public_intro={public_intro_clean!r}",
        f"telegram_to_operator={telegram_to_operator_clean!r}",
        f"telegram_status={telegram_status}",
        f"reasoning_status={reasoning_status}",
        presentation_note,
    ]
    for note in name_notes:
        summary_lines.append(note)

    return TaskResult(
        success=True,
        summary="\n".join(summary_lines),
        public_summary=public_summary,
        model_calls_used=1,
    )
