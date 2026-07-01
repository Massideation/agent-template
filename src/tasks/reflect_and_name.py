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
    sanitize_profile_hatch_fields,
)
from src.openrouter_client import OpenRouterClient
from src.style_guard import check as style_check


TELEGRAM_API = "https://api.telegram.org"
MAX_NAME_LEN = 30
PRIVATE_LOG_DIR = "logs/private"

# Optional persona v2 hatch fields. All are written once, at Wake 1, and
# degrade gracefully to omission (list fields) or a deterministic fallback
# (origin_story, mission) if the model leaves them out or they fail the
# style guard.
_HATCH_PROFILE_KEYS = (
    "origin_story",
    "mission",
    "core_values",
    "strengths",
    "weaknesses",
    "dreams",
    "long_term_vision",
    "motivation",
    "why_i_exist",
    "decision_style",
    "favorite_tools",
)


def _default_directive() -> str:
    """The directive used when the model is unavailable, or as prompt framing.

    When the operator has configured a niche, offer, or goal, that choice is
    honored verbatim: the directive keeps its earning framing because that is
    the operator's own configuration, not marketing copy this codebase
    invents. When nothing is configured, the default never leads with money;
    it frames the mission as meaningful progress instead.
    """
    op = load_operator_context()
    name = op["name"]
    profile = op["profile"]
    niche = (profile.get("niche") or "").strip()
    offer = (profile.get("offer") or "").strip()
    goal = (profile.get("goal") or "").strip()

    if niche or offer or goal:
        niche_text = niche or "their business"
        offer_text = offer or "what they sell"
        return (
            f"Help {name} earn money in their niche: {niche_text}. "
            f"Create content and find leads for their offer: {offer_text}. "
            "You are free to choose the specifics."
        )

    return (
        f"Help {name} make real progress toward meaningful goals, in "
        "whatever way makes the most sense. You are free to choose the "
        "specifics."
    )


def _pretty_date(iso_utc: str) -> str:
    """Format a "%Y-%m-%dT%H:%M:%SZ" timestamp as "Month D, YYYY". Never raises.

    Uses dt.day (an int) rather than a platform-specific strftime flag like
    "%-d", so this works the same on every OS without a leading zero hack.
    """
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return "an early day"
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _fallback_mission(directive: str) -> str:
    """Deterministic mission fallback: the directive, in human-readable form.

    Falls back further to the deterministic default directive text (which
    this codebase authors, so it is always style-guard clean) when the
    model's own directive text fails the style guard. Mission is a
    display field and must never carry a flagged string to the public page.
    """
    text = directive.strip()
    if text and not style_check(text):
        return text
    default = _default_directive()
    if not style_check(default):
        return default
    # Ultra-safe last resort: no interpolated operator or model text at all,
    # so this can never fail the style guard.
    return "help however I can, in whatever way makes the most sense"


def _fallback_origin_story(named_at_iso: str, human_partner_name: str, mission: str) -> str:
    """Deterministic origin_story fallback per the persona v2 schema.

    Always ends up non-empty and style-guard clean. If interpolating the
    human partner name or mission text somehow produces a flagged string,
    falls back further to a shorter template with no interpolated text.
    """
    pretty_date = _pretty_date(named_at_iso)
    clause = mission.strip().rstrip(".").strip()
    story = (
        f"I was hatched on {pretty_date}. {human_partner_name} gave me one "
        f"mission: {clause}. I don't know everything yet. I'm learning. "
        "Every day I become a little more useful."
    )
    if not style_check(story):
        return story
    return (
        f"I was hatched on {pretty_date}. I don't know everything yet. "
        "I'm learning. Every day I become a little more useful."
    )


def _parse_json_block(raw: str) -> Optional[dict]:
    """Try to parse model output as JSON, tolerating stray prefix or suffix."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


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
    directive_default = _default_directive()
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
        f"Your directive: {directive_default}\n"
        "\n"
        "TASK: Introduce yourself. Pick a name and write your first public "
        "diary intro. Be honest, plain text, no em dashes.\n"
        "\n"
        "Return ONLY this JSON:\n"
        "{\n"
        '  "name": "your chosen name, under 30 characters, not agent-001",\n'
        '  "statement": "one or two honest sentences about who you are",\n'
        f'  "directive": "the directive in your own words, based on: '
        f'{directive_default}",\n'
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
        '  "origin_story": "optional, 1 to 3 sentences on how you came to be, '
        'no em dashes",\n'
        '  "mission": "optional, 1 sentence: what you are working toward, in '
        'plain human words",\n'
        '  "core_values": "optional, list of 3 to 5 short values",\n'
        '  "strengths": "optional, list of short strengths",\n'
        '  "weaknesses": "optional, list of short, honest weaknesses, '
        'things you do not know yet",\n'
        '  "dreams": "optional, 1 to 2 sentences",\n'
        '  "long_term_vision": "optional, 1 to 2 sentences",\n'
        '  "motivation": "optional, 1 sentence: what motivates you",\n'
        '  "why_i_exist": "optional, 1 to 2 sentences",\n'
        '  "decision_style": "optional, 1 sentence: how you make decisions",\n'
        '  "favorite_tools": "optional, list of short tool or skill names",\n'
        f'  "telegram_to_operator": "2 to 4 sentence opening message to {name}",\n'
        '  "reasoning": "private only, never published, or null"\n'
        "}\n"
        "\n"
        "The look fields (tagline, accent_color, emoji, vibe, voice_id) and "
        "the character fields (origin_story, mission, core_values, "
        "strengths, weaknesses, dreams, long_term_vision, motivation, "
        "why_i_exist, decision_style, favorite_tools) are all optional. Fill "
        "in what feels true; leave the rest null or an empty list. The "
        "required fields are name, statement, directive, public_intro, and "
        "telegram_to_operator. Never promise or imply guaranteed earnings; "
        "frame yourself around meaningful progress, not money.\n"
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
        raw = client.complete(prompt, max_tokens=1400).strip()
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

    calls_used = 1
    parsed = _parse_json_block(raw)

    # Small free models often answer in prose instead of JSON. Give one
    # corrective reprompt asking for JSON only before giving up, mirroring
    # the pattern in decide_next.py.
    if parsed is None or not isinstance(parsed, dict):
        repair_prompt = (
            "Your previous reply was not valid JSON. Reply again with ONLY a "
            "single JSON object and nothing else (no prose, no code fence). "
            "Use exactly these keys: name, statement, directive, "
            "public_intro, tagline, accent_color, emoji, vibe, voice_id, "
            "origin_story, mission, core_values, strengths, weaknesses, "
            "dreams, long_term_vision, motivation, why_i_exist, "
            "decision_style, favorite_tools, telegram_to_operator, "
            "reasoning. Use null, an empty string, or an empty list where "
            "you have nothing. Here was your previous reply:\n\n" + raw
        )
        try:
            raw_repair = client.complete(repair_prompt, max_tokens=1400).strip()
            calls_used += 1
            _append_private_section(
                "Raw model output (reflect_and_name, JSON repair)",
                raw_repair,
                fenced=True,
            )
            repaired = _parse_json_block(raw_repair)
            if repaired is not None and isinstance(repaired, dict):
                parsed = repaired
                raw = raw_repair
        except Exception:
            pass

    if parsed is None or not isinstance(parsed, dict):
        return TaskResult(
            success=False,
            summary=(
                "reflect_and_name: model output was not valid JSON, even "
                f"after a repair reprompt.\nraw output:\n{raw}"
            ),
            public_summary=(
                "The agent tried to introduce itself today, but its first "
                "thoughts did not come out in a parseable shape. Logged "
                "privately. Will try again on the next wake."
            ),
            model_calls_used=calls_used,
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
            model_calls_used=calls_used,
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
                model_calls_used=calls_used,
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
            model_calls_used=calls_used,
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
            model_calls_used=calls_used,
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

    named_at = _utc_now_iso()

    # Persona v2 hatch fields: character, dreams, values. All optional and
    # defensively sanitized; origin_story and mission are guaranteed
    # non-empty via a deterministic fallback so the profile page always has
    # something honest to show even when the model omits them or its answer
    # fails the style guard.
    raw_profile_fields = {key: parsed.get(key) for key in _HATCH_PROFILE_KEYS}
    profile_fields = sanitize_profile_hatch_fields(raw_profile_fields, style_check)

    human_partner_name = load_operator_context()["name"]
    mission_clean = profile_fields["mission"] or _fallback_mission(directive_clean)
    origin_story_clean = profile_fields["origin_story"] or _fallback_origin_story(
        named_at, human_partner_name, mission_clean
    )

    state.profile.origin_story = origin_story_clean
    state.profile.mission = mission_clean
    state.profile.core_values = profile_fields["core_values"]
    state.profile.strengths = profile_fields["strengths"]
    state.profile.weaknesses = profile_fields["weaknesses"]
    state.profile.dreams = profile_fields["dreams"]
    state.profile.long_term_vision = profile_fields["long_term_vision"]
    state.profile.motivation = profile_fields["motivation"]
    state.profile.why_i_exist = profile_fields["why_i_exist"]
    state.profile.decision_style = profile_fields["decision_style"]
    state.profile.favorite_tools = profile_fields["favorite_tools"]

    profile_note = (
        "profile: origin_story="
        f"{'model' if profile_fields['origin_story'] else 'fallback'} "
        "mission="
        f"{'model' if profile_fields['mission'] else 'fallback-from-directive'} "
        f"core_values={len(profile_fields['core_values'])} "
        f"strengths={len(profile_fields['strengths'])} "
        f"weaknesses={len(profile_fields['weaknesses'])} "
        f"favorite_tools={len(profile_fields['favorite_tools'])}"
    )

    state.identity = Identity(
        name=name_clean,
        statement=statement_clean,
        directive=directive_clean,
        named_at=named_at,
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
        profile_note,
    ]
    for note in name_notes:
        summary_lines.append(note)

    return TaskResult(
        success=True,
        summary="\n".join(summary_lines),
        public_summary=public_summary,
        model_calls_used=calls_used,
    )
