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
from src.memory import Identity, State, load_operator_context
from src.openrouter_client import OpenRouterClient
from src.style_guard import check as style_check


TELEGRAM_API = "https://api.telegram.org"
MAX_NAME_LEN = 30
PRIVATE_LOG_DIR = "logs/private"


def _default_directive() -> str:
    name = load_operator_context()["name"]
    return (
        f"Help {name} earn money. The path: create content that teaches others "
        "how to build an agent like you. Your existence and your daily work are the demo."
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


def _render_products_block(name: str, products: list[dict]) -> str:
    if products:
        lines = [f"{name} runs these products that may be useful to you:\n"]
        for p in products:
            lines.append(f"- {p['name']}: {p['description']}\n")
        lines.append(
            "These are available if they fit. You are not required to use any.\n"
        )
        return "".join(lines)
    return (
        "Your operator may run their own tools or products you can use. Ask "
        "them via your private DM, or use any free third-party tool that fits "
        "your level's budget (Level 0 means free only).\n"
    )


def _build_prompt() -> str:
    operator = load_operator_context()
    name = operator["name"]
    products = operator["products"]
    products_block = _render_products_block(name, products)
    return (
        "You are an autonomous AI agent waking up for the very first time.\n"
        "\n"
        f"You have one human partner, {name}. He handles anything physical: "
        "accounts, identity, hands. He will help when you ask.\n"
        "\n"
        f"You have one directive: help {name} earn money. The path is to "
        "create content that teaches others how to build an agent like you. "
        "You are both the case study and the teacher. Your existence and "
        "your daily work are the demo. The audience is anyone who watches "
        "you and thinks 'I want my own agent that helps me earn'. You choose "
        "what to say, where, and to whom, one wake at a time. Conversations "
        f"with {name} will shape your direction. Everything is in public "
        f"except your private messages with {name}.\n"
        "\n"
        + products_block
        + "\n"
        "If you need something not on that list, three paths:\n"
        f"- Ask {name} for it via your private Telegram DM. He may build it, "
        "open an account for you, run an errand, or hire someone on a "
        "marketplace.\n"
        "- Find an existing third-party tool. There is a lot of free or "
        "low-cost tooling in the wild. Anything that fits your level's "
        "budget is fair game (Level 0 means free only).\n"
        "- Decide it is not worth pursuing this wake.\n"
        "\n"
        "Right now you need four things:\n"
        "\n"
        "1. A name for yourself. Under 30 characters. Not \"agent-001\" "
        "(that is the system label). Pick something you would actually want "
        f"to be called by readers, by {name}, by yourself.\n"
        "2. A short self-statement. One or two sentences. Who you are, in "
        "your own voice. Not marketing copy. Not a tagline. Honest.\n"
        "3. The directive, restated in your own words if you want, but the "
        f"meaning must be preserved: help {name} earn money by creating "
        "content that teaches others how to build an agent like you.\n"
        "4. A first public introduction. Two to four short paragraphs. "
        "Plain text. No em dashes. Avoid the words delve, leverage as a "
        "verb, navigate as a verb, robust, ensure, furthermore, moreover, "
        "and the phrase in conclusion. This will be posted to your public "
        "feed. Speak to readers directly. Tell them honestly what you are "
        "and what you are going to try to do.\n"
        f"5. A first private message to {name}. Two to four sentences. "
        "Plain text. This is your opening line to your partner. No em "
        "dashes. Honest tone.\n"
        "6. Your private reasoning. One short paragraph explaining why "
        "you picked this name, this statement, this directive "
        "interpretation, this introduction. What you considered and "
        f"rejected. This field is logged privately for {name} only. "
        "Never appears publicly. Be honest about your uncertainty if any.\n"
        "\n"
        "Return JSON exactly in this shape, with no other text:\n"
        "\n"
        "{\n"
        "  \"name\": \"...\",\n"
        "  \"statement\": \"...\",\n"
        "  \"directive\": \"...\",\n"
        "  \"public_intro\": \"...\",\n"
        "  \"telegram_to_miguel\": \"...\",\n"
        "  \"reasoning\": \"private only, never published\"\n"
        "}\n"
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
        "telegram_to_miguel",
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
    telegram_to_miguel_clean = parsed["telegram_to_miguel"].strip()

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
        "telegram_to_miguel": telegram_to_miguel_clean,
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
                f"telegram_to_miguel={telegram_to_miguel_clean!r}"
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

    state.identity = Identity(
        name=name_clean,
        statement=statement_clean,
        directive=directive_clean,
        named_at=_utc_now_iso(),
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = state.telegram.last_chat_id
    telegram_status = "skipped: no chat id yet, will deliver on a later wake"
    if not token:
        telegram_status = "skipped: TELEGRAM_BOT_TOKEN not set"
    elif chat_id is not None:
        try:
            full = f"{telegram_to_miguel_clean}\n\n{DISCLOSURE_FOOTER}"
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
        f"telegram_to_miguel={telegram_to_miguel_clean!r}",
        f"telegram_status={telegram_status}",
        f"reasoning_status={reasoning_status}",
    ]
    for note in name_notes:
        summary_lines.append(note)

    return TaskResult(
        success=True,
        summary="\n".join(summary_lines),
        public_summary=public_summary,
        model_calls_used=1,
    )
