"""decide_next task.

Runs on every wake after Wake 1. The agent reads its own identity, recent
public log entries, and recent private log excerpts (the Telegram lines that
respond_to_telegram persisted). It then asks the model for one JSON object
containing reasoning (private), a public_summary, an optional private
Telegram message to the operator, and up to three optional web search queries.

If the model returned search_queries and the daily call quota still permits
another model call, the task runs the searches via src.web_search, formats
the results, and calls the model a second time to produce final public and
private outputs. Otherwise call 1's outputs are used.

Each produced public string is run through the style guard. Reasoning is
private only and is never style-checked. Raw model output is appended to the
private log and never written anywhere public.

Dispatch is defensive: any sub-action that fails becomes a status line in the
private summary while the wake still returns the parts that worked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import httpx

from src import inbox
from src.executor import TaskResult
from src.logger import DISCLOSURE_FOOTER
from src.memory import State, load_operator_context
from src.openrouter_client import OpenRouterClient
from src.peers import get_peer_summary
from src.style_guard import check as style_check


TELEGRAM_API = "https://api.telegram.org"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLIC_LOG_DIR = REPO_ROOT / "logs" / "public"
PRIVATE_LOG_DIR = REPO_ROOT / "logs" / "private"

RECENT_FILE_COUNT = 3
PUBLIC_TAIL_CHARS = 500
PRIVATE_TAIL_CHARS = 1200


def _render_products_tool_line(name: str, products: list[dict]) -> str:
    """One bullet describing the operator's products for the tools section.

    Non-empty products list yields a "{name}'s products:" line listing each.
    Empty list yields the generic "your operator may have tools" fallback.
    """
    if products:
        items = "; ".join(f"{p['name']}: {p['description']}" for p in products)
        return f"- {name}'s products: {items}\n"
    return (
        "- Your operator may have their own tools or products you can use. "
        "Ask them via private DM, or use any free third-party tool that fits "
        "your level's budget (Level 0 means free only).\n"
    )


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


def _recent_files(directory: Path, limit: int) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    candidates = [p for p in directory.glob("*.md") if p.is_file()]
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[:limit]


def _read_tail(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _build_recent_public_block(notes: list[str]) -> str:
    try:
        files = _recent_files(PUBLIC_LOG_DIR, RECENT_FILE_COUNT)
    except Exception as exc:
        notes.append(f"recent_public read failed: {exc}")
        return "(none available)"

    if not files:
        return "(none yet)"

    parts: list[str] = []
    for path in files:
        tail = _read_tail(path, PUBLIC_TAIL_CHARS).strip()
        if not tail:
            continue
        parts.append(f"[{path.stem}]\n{tail}")
    if not parts:
        return "(none yet)"
    return "\n\n".join(parts)


def _build_recent_telegram_block(notes: list[str]) -> str:
    try:
        files = _recent_files(PRIVATE_LOG_DIR, RECENT_FILE_COUNT)
    except Exception as exc:
        notes.append(f"recent_telegram read failed: {exc}")
        return "(none available)"

    if not files:
        return "(none yet)"

    excerpts: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relevant: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if "telegram" in lowered or "miguel" in lowered:
                relevant.append(stripped)
        if not relevant:
            continue
        joined = "\n".join(relevant[-20:])
        if len(joined) > PRIVATE_TAIL_CHARS:
            joined = joined[-PRIVATE_TAIL_CHARS:]
        excerpts.append(f"[{path.stem}]\n{joined}")

    if not excerpts:
        return "(no recent private exchanges found)"
    return "\n\n".join(excerpts)


def _build_inbox_block(notes: list[str]) -> tuple[str, list[dict]]:
    """Return (formatted_block, raw_messages). Empty messages give empty block.

    Each line in the block: "  - id: <message_id>, sent <ts iso>, text: <first 500 chars>".
    """
    try:
        pending = inbox.list_pending_messages()
    except Exception as exc:
        notes.append(f"inbox read failed: {exc}")
        return "", []

    if not pending:
        return "", []

    from datetime import datetime, timezone

    lines: list[str] = []
    for msg in pending:
        try:
            ts_iso = datetime.fromtimestamp(
                msg["ts"] / 1000.0, tz=timezone.utc
            ).isoformat()
        except Exception:
            ts_iso = str(msg.get("ts", ""))
        snippet = (msg.get("content") or "")[:500]
        lines.append(
            f"  - id: {msg['id']}, sent {ts_iso}, text: {snippet}"
        )
    return "\n".join(lines), pending


def _build_prompt(
    name: str,
    statement: str,
    directive: str,
    wake_count: int,
    recent_public_block: str,
    recent_telegram_block: str,
    peer_block: str = "",
    inbox_block: str = "",
) -> str:
    peer_section = (
        f"Peer agent context (metadata only, sanitized):\n{peer_block}\n\n"
        if peer_block
        else ""
    )
    inbox_section = (
        "\n\nOperator inbox (pending messages from the operator via the "
        "admin web UI). Reply with inbox_reply.message_id matching one of "
        "these:\n"
        f"{inbox_block}\n"
        if inbox_block
        else ""
    )
    operator = load_operator_context()
    operator_name = operator["name"]
    products_line = _render_products_tool_line(operator_name, operator["products"])
    return (
        f"You are {name}. Your self-statement is: {statement}\n"
        "\n"
        f"Your directive is: {directive}\n"
        "\n"
        f"You wake up once per cycle. You have one human partner, {operator_name}, who "
        "handles anything physical when you ask. Everything else is yours to "
        "decide.\n"
        "\n"
        f"This is wake number {wake_count}.\n"
        "\n"
        "You wake four times a day, roughly every six hours. Many of those "
        "wakes will have nothing new to publish; please rest on those "
        "(empty public_summary).\n"
        "\n"
        + peer_section
        + "Tools available to you. Use any that fit, ignore any that do not:\n"
        + products_line
        + f"- Things you can ask {operator_name} for via private DM: open new "
        "accounts, build new tools, run errands, hire someone on a "
        "marketplace, anything physical or KYC-bound.\n"
        "- Existing third-party tools in the wild. Anything that fits "
        "your level's budget (Level 0 means free only) is fair game. "
        f"You can name what you want and ask {operator_name} to wire it.\n"
        "\n"
        "Recent public log entries (most recent first):\n"
        f"{recent_public_block}\n"
        "\n"
        f"Recent private messages between you and {operator_name} (most recent first):\n"
        f"{recent_telegram_block}\n"
        f"{inbox_section}"
        "\n"
        "Your task right now: decide what to say this wake. You can do any of:\n"
        "\n"
        "- Post a public update on your feed. Keep it honest. No marketing copy.\n"
        f"- Reply privately to {operator_name} about something on your mind, or about a "
        "decision you want his help on.\n"
        "- Ask for a web search. If you want to look something up before "
        "deciding what to publish this wake (a fact, a piece of news, a tool "
        "to evaluate, a competitor to read), list up to 3 short Google-style "
        "queries in search_queries. Code will run them and feed results back "
        "to you for a second pass. Leave empty if you don't need to search.\n"
        "- Reply privately to a pending operator inbox message. If there are "
        "any pending inbox messages listed above, you may answer one by "
        "returning inbox_reply with the matching message_id and your text. "
        "Only one inbox reply per wake; reply to the most relevant pending "
        "message. Otherwise omit inbox_reply or set it to null.\n"
        "- Rest. If you have nothing new to say since your last wake (no new "
        "Telegram, no new peer agents, no fresh thought), return an empty "
        "string for public_summary. Many wakes should be silent. "
        "Quiet wakes are honest wakes.\n"
        "\n"
        "You may pick any combination of those actions. You may also pick "
        "none, in which case explain why this wake is quiet in the reasoning "
        "field.\n"
        "\n"
        "Hard rules for every string you produce:\n"
        "- No em dashes.\n"
        "- Avoid the words delve, leverage as a verb, navigate as a verb, "
        "robust, ensure, furthermore, moreover, and the phrase in conclusion.\n"
        f"- Do not invent facts about yourself, {operator_name}, your revenue, your "
        "audience.\n"
        f"- Do not impersonate {operator_name}.\n"
        "- Plain text only. No Markdown headings.\n"
        "- Be direct. Short paragraphs.\n"
        "\n"
        "The reasoning field is private. It is logged for your operator only "
        "and is never shown publicly. Style rules above apply to "
        "public_summary and telegram_to_miguel only. Reasoning may be "
        "informal and may contain anything you actually thought about. Tell "
        "yourself, in your own voice: why this public_summary, why this "
        "Telegram message or why none, what you considered and rejected, "
        "and (if you are asking for a search) why these queries.\n"
        "\n"
        "Return JSON exactly in this shape, with no other text:\n"
        "\n"
        "{\n"
        '  "reasoning": "private reasoning, why these choices, what was '
        'considered and rejected",\n'
        '  "public_summary": "what to publish on the public feed this wake, '
        'or empty string if resting this hour",\n'
        '  "telegram_to_miguel": "a private message to '
        + operator_name
        + ', or null",\n'
        '  "search_queries": ["up to three short queries, or empty list"],\n'
        '  "inbox_reply": {"message_id": "<one of the ids above>", "text": '
        '"your reply to the operator"}\n'
        "}\n"
        "\n"
        "inbox_reply is optional. If you want to reply to a pending inbox "
        "message, return an object with message_id matching one of the ids "
        "listed in the operator inbox section, plus your text. Otherwise omit "
        "it or set it to null. Only one inbox reply per wake.\n"
    )


def _parse_json_block(raw: str) -> Optional[dict]:
    """Try to parse the model output as JSON. Tolerate stray prefix/suffix."""
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


def _clean_search_queries(raw_value) -> tuple[list[str], list[str]]:
    """Clean a raw search_queries value into at most 3 valid strings.

    Returns (cleaned_queries, notes). Notes describe anything dropped or
    truncated. Defensive: malformed input becomes ([], notes).
    """
    notes: list[str] = []
    if raw_value is None:
        return [], notes
    if not isinstance(raw_value, list):
        notes.append(
            f"search_queries had unexpected type: {type(raw_value).__name__}"
        )
        return [], notes

    cleaned: list[str] = []
    for idx, item in enumerate(raw_value):
        if not isinstance(item, str):
            notes.append(
                f"search_queries[{idx}] dropped: not a string "
                f"({type(item).__name__})"
            )
            continue
        candidate = item.strip()
        if not candidate:
            notes.append(f"search_queries[{idx}] dropped: empty after strip")
            continue
        if len(candidate) > 200:
            notes.append(
                f"search_queries[{idx}] dropped: longer than 200 chars"
            )
            continue
        cleaned.append(candidate)

    if len(cleaned) > 3:
        notes.append(
            f"search_queries truncated from {len(cleaned)} to 3"
        )
        cleaned = cleaned[:3]

    return cleaned, notes


def _run_searches(queries: list[str]) -> dict[str, list[dict]]:
    """Run web searches for up to 3 queries. Per-query failures yield [].

    Returns dict mapping each query string to its results list (at most 5
    items each). Never raises.
    """
    capped = queries[:3]
    results: dict[str, list[dict]] = {}

    try:
        from src import web_search
    except Exception:
        for q in capped:
            results[q] = []
        return results

    for q in capped:
        try:
            hits = web_search.search(q, limit=5)
            if not isinstance(hits, list):
                hits = []
            results[q] = hits[:5]
        except Exception:
            results[q] = []
    return results


def _format_search_results(results: dict[str, list[dict]]) -> str:
    """Pretty-print up to 5 results per query for the second-pass prompt."""
    if not results:
        return "(no search results)"
    blocks: list[str] = []
    for query, hits in results.items():
        block_lines = [f"QUERY: {query}"]
        if not hits:
            block_lines.append("  (no results)")
            blocks.append("\n".join(block_lines))
            continue
        for i, hit in enumerate(hits[:5], start=1):
            title = ""
            url = ""
            snippet = ""
            if isinstance(hit, dict):
                title = str(hit.get("title", "")).strip()
                url = str(hit.get("url", "")).strip()
                snippet = str(hit.get("snippet", "")).strip()
            block_lines.append(f"  {i}. {title}")
            block_lines.append(f"     {url}")
            block_lines.append(f"     {snippet}")
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def _build_search_followup_prompt(
    name: str,
    statement: str,
    directive: str,
    wake_count: int,
    recent_public_block: str,
    recent_telegram_block: str,
    preliminary_reasoning: str,
    preliminary_public_summary: str,
    preliminary_telegram: Optional[str],
    formatted_search_results: str,
) -> str:
    """Build the second-pass prompt after web searches have been run."""
    telegram_repr = (
        repr(preliminary_telegram) if preliminary_telegram is not None else "null"
    )
    operator_name = load_operator_context()["name"]
    return (
        f"You are {name}. Your self-statement is: {statement}\n"
        "\n"
        f"Your directive is: {directive}\n"
        "\n"
        f"This is wake number {wake_count}.\n"
        "\n"
        "Recent public log entries (most recent first):\n"
        f"{recent_public_block}\n"
        "\n"
        f"Recent private messages between you and {operator_name} (most recent first):\n"
        f"{recent_telegram_block}\n"
        "\n"
        "A moment ago, you produced a preliminary draft of this wake. You "
        "also requested up to three web searches. The search results are "
        "below. Use them to refine your public_summary and "
        "telegram_to_miguel. If the results contradict something in your "
        "draft, fix it. If the results add nothing useful, you may keep "
        "your draft text but you must still return it through this call's "
        "JSON shape.\n"
        "\n"
        "Your preliminary reasoning (private, your own words):\n"
        f"{preliminary_reasoning}\n"
        "\n"
        "Your preliminary public_summary (draft, may be revised):\n"
        f"{preliminary_public_summary}\n"
        "\n"
        "Your preliminary telegram_to_miguel (draft, may be revised or set "
        "to null):\n"
        f"{telegram_repr}\n"
        "\n"
        "Search results (top 5 per query):\n"
        f"{formatted_search_results}\n"
        "\n"
        "Given what you found, decide what to publish this wake. The "
        "reasoning field is private and is never shown publicly.\n"
        "\n"
        "Hard rules for every string you produce:\n"
        "- No em dashes.\n"
        "- Avoid the words delve, leverage as a verb, navigate as a verb, "
        "robust, ensure, furthermore, moreover, and the phrase in conclusion.\n"
        "- Do not invent facts. If a search result is unclear, say so "
        "honestly rather than guessing.\n"
        f"- Do not impersonate {operator_name}.\n"
        "- Plain text only. No Markdown headings.\n"
        "- Be direct. Short paragraphs.\n"
        "- The reasoning field stays private. Be candid.\n"
        "\n"
        "Return JSON exactly in this shape, with no other text:\n"
        "\n"
        "{\n"
        '  "reasoning": "private reasoning, what the search changed about '
        'your draft, what you kept, what you discarded",\n'
        '  "public_summary": "final public summary for this wake. Must be '
        'at least one sentence.",\n'
        '  "telegram_to_miguel": "final private message to '
        + operator_name
        + ', or null"\n'
        "}\n"
    )


def run(state: State, client: Optional[OpenRouterClient]) -> TaskResult:
    identity = state.identity
    if identity is None:
        return TaskResult(
            success=False,
            summary="decide_next: no identity on state; planner should have routed to reflect_and_name",
            public_summary=(
                "The agent is awake but does not yet know who it is. "
                "It will try to name itself on the next wake."
            ),
            model_calls_used=0,
        )

    name = identity.name
    statement = identity.statement
    directive = identity.directive

    if client is None:
        # No model available: rest silently. Empty public_summary makes
        # wake.py skip the public post rather than confess a failure.
        return TaskResult(
            success=True,
            summary="decide_next: no language model available this wake",
            public_summary="",
            model_calls_used=0,
        )

    context_notes: list[str] = []

    try:
        recent_public_block = _build_recent_public_block(context_notes)
    except Exception as exc:
        recent_public_block = "(unavailable)"
        context_notes.append(f"recent_public errored: {exc}")

    try:
        recent_telegram_block = _build_recent_telegram_block(context_notes)
    except Exception as exc:
        recent_telegram_block = "(unavailable)"
        context_notes.append(f"recent_telegram errored: {exc}")

    try:
        peer_block = get_peer_summary()
    except Exception as exc:
        peer_block = ""
        context_notes.append(f"peer summary errored: {exc}")

    try:
        inbox_block, pending_inbox = _build_inbox_block(context_notes)
    except Exception as exc:
        inbox_block = ""
        pending_inbox = []
        context_notes.append(f"inbox block errored: {exc}")

    prompt = _build_prompt(
        name=name,
        statement=statement,
        directive=directive,
        wake_count=int(state.wake_count),
        recent_public_block=recent_public_block,
        recent_telegram_block=recent_telegram_block,
        peer_block=peer_block,
        inbox_block=inbox_block,
    )

    try:
        raw_output_1 = client.complete(prompt, max_tokens=1200).strip()
    except Exception as exc:
        # The real diagnostic (which models failed and why) is kept in the
        # private summary. Public stays empty so wake.py rests this wake
        # silently instead of posting a model-failure confession.
        return TaskResult(
            success=False,
            summary=(
                "decide_next: model call failed: "
                f"{exc}\n"
                + ("context_notes: " + "; ".join(context_notes) if context_notes else "")
            ),
            public_summary="",
            model_calls_used=0,
        )

    parsed = _parse_json_block(raw_output_1)

    # Small free models often answer in prose instead of JSON. Give one
    # corrective reprompt asking for JSON only before giving up. This lifts
    # the success rate sharply on weak models.
    if parsed is None or not isinstance(parsed, dict):
        repair_prompt = (
            "Your previous reply was not valid JSON. Reply again with ONLY a "
            "single JSON object and nothing else (no prose, no code fence). "
            "Use exactly these keys: reasoning, public_summary, "
            "telegram_to_miguel, search_queries, inbox_reply. Use null or an "
            "empty list where you have nothing. Here was your previous reply:\n\n"
            + raw_output_1
        )
        try:
            raw_output_repair = client.complete(repair_prompt, max_tokens=1200).strip()
            repaired = _parse_json_block(raw_output_repair)
            if repaired is not None and isinstance(repaired, dict):
                parsed = repaired
                raw_output_1 = raw_output_repair
        except Exception:
            pass

    if parsed is None or not isinstance(parsed, dict):
        failure_parts = [
            "decide_next: model output was not parseable JSON.",
            "",
            "## Raw model output (decide_next call 1)",
            "",
            "```text",
            raw_output_1,
            "```",
        ]
        if context_notes:
            failure_parts.append("")
            failure_parts.append("context_notes: " + "; ".join(context_notes))
        # Model returned no usable content (unparseable JSON). Rest silently:
        # the raw output is preserved in the private summary above, and the
        # empty public_summary makes wake.py skip the public post.
        return TaskResult(
            success=False,
            summary="\n".join(failure_parts),
            public_summary="",
            model_calls_used=1,
        )

    reasoning_1_raw = parsed.get("reasoning")
    public_summary_1_raw = parsed.get("public_summary")
    telegram_1_raw = parsed.get("telegram_to_miguel")
    search_queries_raw = parsed.get("search_queries")

    violation_log: list[str] = []
    notes_call_1: list[str] = []

    if "reasoning" not in parsed:
        reasoning_1 = ""
        reasoning_1_status = "omitted by model"
    elif not isinstance(reasoning_1_raw, str):
        reasoning_1 = ""
        reasoning_1_status = (
            f"wrong type: {type(reasoning_1_raw).__name__}"
        )
    else:
        reasoning_1 = reasoning_1_raw.strip()
        reasoning_1_status = "present" if reasoning_1 else "empty"

    public_summary_1_clean: Optional[str] = None
    if isinstance(public_summary_1_raw, str):
        candidate = public_summary_1_raw.strip()
        if candidate:
            ps_violations = style_check(candidate)
            if ps_violations:
                violation_log.append(
                    "call_1 public_summary style violations: "
                    + ", ".join(ps_violations)
                )
            else:
                public_summary_1_clean = candidate

    telegram_1_text: Optional[str] = None
    if isinstance(telegram_1_raw, str):
        candidate = telegram_1_raw.strip()
        if candidate:
            tg_violations = style_check(candidate)
            if tg_violations:
                violation_log.append(
                    "call_1 telegram_to_miguel style violations: "
                    + ", ".join(tg_violations)
                )
            else:
                telegram_1_text = candidate
    elif telegram_1_raw is not None:
        violation_log.append(
            "call_1 telegram_to_miguel had unexpected type: "
            f"{type(telegram_1_raw).__name__}"
        )

    cleaned_queries, query_notes = _clean_search_queries(search_queries_raw)
    notes_call_1.extend(query_notes)

    # Decide whether to run a second call.
    second_call_attempted = False
    second_call_used = False
    skip_reason: Optional[str] = None
    search_results: dict[str, list[dict]] = {}
    raw_output_2: Optional[str] = None
    parsed_2: Optional[dict] = None
    reasoning_2 = ""
    reasoning_2_status = "not attempted"
    public_summary_2_clean: Optional[str] = None
    telegram_2_text: Optional[str] = None
    telegram_2_was_explicit_null = False
    second_call_error: Optional[str] = None

    quota = state.quota
    calls_remaining = max(0, int(quota.calls_limit) - int(quota.calls_made) - 1)

    if cleaned_queries and client is not None:
        if calls_remaining < 1:
            skip_reason = "quota would be exhausted"
        else:
            second_call_attempted = True
            try:
                search_results = _run_searches(cleaned_queries)
            except Exception as exc:
                search_results = {q: [] for q in cleaned_queries}
                notes_call_1.append(f"search dispatch errored: {exc}")

            total_results = sum(len(v) for v in search_results.values())
            if total_results == 0:
                skip_reason = "all queries returned no results"
                second_call_attempted = False
            else:
                preliminary_public = (
                    public_summary_1_clean
                    if public_summary_1_clean is not None
                    else (
                        public_summary_1_raw.strip()
                        if isinstance(public_summary_1_raw, str)
                        else "(no draft)"
                    )
                )
                followup_prompt = _build_search_followup_prompt(
                    name=name,
                    statement=statement,
                    directive=directive,
                    wake_count=int(state.wake_count),
                    recent_public_block=recent_public_block,
                    recent_telegram_block=recent_telegram_block,
                    preliminary_reasoning=(
                        reasoning_1 if reasoning_1 else "(none provided)"
                    ),
                    preliminary_public_summary=preliminary_public,
                    preliminary_telegram=telegram_1_text,
                    formatted_search_results=_format_search_results(
                        search_results
                    ),
                )

                try:
                    raw_output_2 = client.complete(
                        followup_prompt, max_tokens=1200
                    ).strip()
                    second_call_used = True
                except Exception as exc:
                    second_call_error = f"second model call failed: {exc}"

                if second_call_used and raw_output_2 is not None:
                    parsed_2 = _parse_json_block(raw_output_2)
                    if parsed_2 is None or not isinstance(parsed_2, dict):
                        second_call_error = (
                            "second model output was not parseable JSON"
                        )
                        parsed_2 = None

                if parsed_2 is not None:
                    reasoning_2_raw = parsed_2.get("reasoning")
                    public_summary_2_raw = parsed_2.get("public_summary")
                    telegram_2_raw = parsed_2.get("telegram_to_miguel")

                    if "reasoning" not in parsed_2:
                        reasoning_2 = ""
                        reasoning_2_status = "omitted by model"
                    elif not isinstance(reasoning_2_raw, str):
                        reasoning_2 = ""
                        reasoning_2_status = (
                            f"wrong type: {type(reasoning_2_raw).__name__}"
                        )
                    else:
                        reasoning_2 = reasoning_2_raw.strip()
                        reasoning_2_status = (
                            "present" if reasoning_2 else "empty"
                        )

                    if isinstance(public_summary_2_raw, str):
                        candidate = public_summary_2_raw.strip()
                        if candidate:
                            ps_violations = style_check(candidate)
                            if ps_violations:
                                violation_log.append(
                                    "call_2 public_summary style "
                                    "violations: "
                                    + ", ".join(ps_violations)
                                )
                            else:
                                public_summary_2_clean = candidate

                    if telegram_2_raw is None:
                        telegram_2_was_explicit_null = True
                    elif isinstance(telegram_2_raw, str):
                        candidate = telegram_2_raw.strip()
                        if candidate:
                            tg_violations = style_check(candidate)
                            if tg_violations:
                                violation_log.append(
                                    "call_2 telegram_to_miguel style "
                                    "violations: "
                                    + ", ".join(tg_violations)
                                )
                            else:
                                telegram_2_text = candidate
                    else:
                        violation_log.append(
                            "call_2 telegram_to_miguel had unexpected "
                            f"type: {type(telegram_2_raw).__name__}"
                        )

                    if "search_queries" in parsed_2:
                        notes_call_1.append(
                            "search_queries returned in call 2 "
                            "(ignored, single search round only)"
                        )

    # Decide final outputs: call 2 if it parsed and gave a usable
    # public_summary, otherwise call 1, otherwise fallback.
    used_call_2 = parsed_2 is not None and public_summary_2_clean is not None
    if used_call_2:
        public_summary_final = public_summary_2_clean
        if telegram_2_was_explicit_null:
            telegram_final = None
        elif telegram_2_text is not None:
            telegram_final = telegram_2_text
        else:
            telegram_final = telegram_1_text
    else:
        # No usable public content from call 1 (model gave none, or its draft
        # failed the style guard). Rest silently with an empty public_summary
        # so wake.py skips the post; the draft and violations stay in the
        # private summary below. Never post a style-failure confession.
        public_summary_final = (
            public_summary_1_clean
            if public_summary_1_clean is not None
            else ""
        )
        telegram_final = telegram_1_text

    # Dispatch Telegram.
    telegram_status: str
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = state.telegram.last_chat_id
    if telegram_final and token and chat_id is not None:
        try:
            full = f"{telegram_final}\n\n{DISCLOSURE_FOOTER}"
            _send_message(token, chat_id, full)
            telegram_status = f"sent to chat_id={chat_id}"
        except httpx.HTTPError as exc:
            telegram_status = f"sendMessage failed: {exc}"
        except Exception as exc:
            telegram_status = f"sendMessage errored: {exc}"
    elif not telegram_final:
        telegram_status = "skipped: nothing to say"
    elif not token:
        telegram_status = "skipped: TELEGRAM_BOT_TOKEN not set"
    elif chat_id is None:
        telegram_status = "skipped: no chat id known yet"
    else:
        telegram_status = "skipped: token or chat_id missing"

    # Inbox dispatch. Prefer call 2 inbox_reply, fall back to call 1.
    inbox_reply_raw = None
    if used_call_2 and parsed_2 is not None:
        inbox_reply_raw = parsed_2.get("inbox_reply")
    if inbox_reply_raw is None:
        inbox_reply_raw = parsed.get("inbox_reply")

    inbox_status: str
    try:
        if isinstance(inbox_reply_raw, dict):
            mid = str(inbox_reply_raw.get("message_id") or "").strip()
            txt = str(inbox_reply_raw.get("text") or "").strip()
            if mid and txt:
                known_ids = {m["id"] for m in pending_inbox}
                if mid not in known_ids:
                    inbox_status = f"skipped: unknown message_id {mid!r}"
                else:
                    tx_violations = style_check(txt)
                    if tx_violations:
                        violation_log.append(
                            "inbox_reply style violations: "
                            + ", ".join(tx_violations)
                        )
                        inbox_status = (
                            "skipped: style violations: "
                            + ", ".join(tx_violations)
                        )
                    else:
                        try:
                            inbox.write_reply(mid, txt)
                            inbox.mark_processed(mid)
                            inbox_status = f"replied to {mid}"
                        except Exception as exc:
                            inbox_status = f"errored: {exc}"
            else:
                inbox_status = "skipped: missing message_id or text"
        elif inbox_reply_raw is None:
            inbox_status = "skipped: nothing to reply"
        else:
            inbox_status = (
                "skipped: inbox_reply had unexpected type: "
                f"{type(inbox_reply_raw).__name__}"
            )
    except Exception as exc:
        inbox_status = f"errored: {exc}"

    # Build the private summary. Public output never sees this block.
    model_calls_used = 2 if second_call_used else 1
    if second_call_used and used_call_2:
        outcome_line = (
            "decide_next: second call ran after web search, used call 2 "
            "outputs"
        )
    elif second_call_used and not used_call_2:
        outcome_line = (
            "decide_next: second call ran after web search but fell back to "
            "call 1 outputs"
        )
    elif cleaned_queries and skip_reason:
        outcome_line = (
            f"decide_next: search requested but skipped: {skip_reason}"
        )
    else:
        outcome_line = "decide_next: single call, no search"

    summary_parts: list[str] = [outcome_line, ""]

    summary_parts.append("## Raw model output (decide_next call 1)")
    summary_parts.append("")
    summary_parts.append("```text")
    summary_parts.append(raw_output_1)
    summary_parts.append("```")
    summary_parts.append("")
    summary_parts.append("## Reasoning (private, decide_next call 1)")
    summary_parts.append("")
    if reasoning_1:
        summary_parts.append(reasoning_1)
    else:
        summary_parts.append(f"({reasoning_1_status})")
    summary_parts.append("")
    summary_parts.append("## Call 1 outputs")
    summary_parts.append(
        f"public_summary: {public_summary_1_raw!r}"
        if public_summary_1_raw is not None
        else "public_summary: (missing)"
    )
    summary_parts.append(f"telegram_to_miguel: {telegram_1_raw!r}")
    summary_parts.append(f"search_queries (after cleaning): {cleaned_queries!r}")
    if notes_call_1:
        summary_parts.append("call_1 notes: " + "; ".join(notes_call_1))

    if cleaned_queries:
        summary_parts.append("")
        summary_parts.append("## Web search")
        executed = len(search_results)
        with_results = sum(1 for v in search_results.values() if v)
        total = sum(len(v) for v in search_results.values())
        summary_parts.append(f"queries_executed: {executed}")
        summary_parts.append(f"queries_with_results: {with_results}")
        summary_parts.append(f"total_results: {total}")
        for q, hits in search_results.items():
            summary_parts.append(f"  - {q!r}: {len(hits)} results")
        if skip_reason:
            summary_parts.append(f"skip_reason: {skip_reason}")

    if second_call_used:
        summary_parts.append("")
        summary_parts.append("## Raw model output (decide_next call 2)")
        summary_parts.append("")
        summary_parts.append("```text")
        summary_parts.append(raw_output_2 or "")
        summary_parts.append("```")
        summary_parts.append("")
        summary_parts.append("## Reasoning (private, decide_next call 2)")
        summary_parts.append("")
        if reasoning_2:
            summary_parts.append(reasoning_2)
        else:
            summary_parts.append(f"({reasoning_2_status})")
        summary_parts.append("")
        summary_parts.append("## Call 2 outputs")
        if parsed_2 is not None:
            summary_parts.append(
                f"public_summary: {parsed_2.get('public_summary')!r}"
            )
            summary_parts.append(
                f"telegram_to_miguel: {parsed_2.get('telegram_to_miguel')!r}"
            )
        else:
            summary_parts.append("(call 2 did not produce parseable JSON)")
        if second_call_error:
            summary_parts.append(f"second_call_error: {second_call_error}")

    summary_parts.append("")
    summary_parts.append("## Dispatch")
    summary_parts.append(f"telegram_status: {telegram_status}")
    summary_parts.append(f"inbox_status: {inbox_status}")
    summary_parts.append(f"inbox_pending_count: {len(pending_inbox)}")
    summary_parts.append(
        f"final_source: {'call_2' if used_call_2 else 'call_1'}"
    )
    summary_parts.append(f"model_calls_used: {model_calls_used}")
    if context_notes:
        summary_parts.append("context_notes: " + "; ".join(context_notes))
    if violation_log:
        summary_parts.append("violations: " + "; ".join(violation_log))

    return TaskResult(
        success=True,
        summary="\n".join(summary_parts),
        public_summary=public_summary_final,
        model_calls_used=model_calls_used,
    )
