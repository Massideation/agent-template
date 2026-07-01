# INTERFACES.md - Module Contracts For this agent

This file is the source of truth for the public surface of every module in
`src/`. Other agents implementing Phase 2 onward MUST conform to these
signatures. Behavior summaries are normative.

No em dashes anywhere in this file. Hyphens or rephrasing only.

## Shared Types

### TaskResult

Defined in `src/executor.py` and re-exported as needed.

```python
from dataclasses import dataclass

@dataclass
class TaskResult:
    success: bool
    summary: str            # full text for private log
    public_summary: str     # sanitized text for public log, passes style_guard
    model_calls_used: int = 0
```

### State (Pydantic model in `src/memory.py`)

```python
from pydantic import BaseModel
from typing import Optional

class Identity(BaseModel):
    name: str               # under 30 chars, agent-chosen
    statement: str          # short self-description in the agent's own voice
    directive: str          # e.g. "make real progress toward meaningful goals" by default, or the operator's configured niche/offer framing if set
    named_at: str           # ISO 8601 UTC timestamp

class QuotaState(BaseModel):
    date: str               # YYYY-MM-DD, local date of last update
    calls_made: int
    calls_limit: int

class LevelState(BaseModel):
    current_level: int      # 0..4
    confirmed_revenue_usd: float

class LastWake(BaseModel):
    ts: str                 # ISO 8601
    task_name: str
    outcome: str            # "success" | "idle" | "error" | "quota_exhausted"

class TelegramState(BaseModel):
    last_update_id: int = 0
    last_chat_id: Optional[int] = None

class State(BaseModel):
    identity: Optional[Identity]
    quota: QuotaState
    level: LevelState
    last_wake: Optional[LastWake]
    wake_count: int
    telegram: TelegramState
```

`state.telegram.last_update_id` is the highest Telegram `update_id` the agent
has acknowledged. `state.telegram.last_chat_id` is the chat id of the most
recent inbound Telegram message; `src/tasks/respond_to_telegram.py` updates
it on every incoming message that carries a chat id so the next wake's
`decide_next` knows where the operator is reachable. `load_state()` reads
`state/telegram.json` and `save_state()` writes it. Missing file becomes
`TelegramState(last_update_id=0, last_chat_id=None)`.

## src/memory.py

Loads and persists machine state. Owns the on-disk schema for `state/*.json`
and `memory/agent_memory.md`.

Public API:

- `load_state() -> State`
  Reads every file under `state/` and returns a populated `State`. Missing
  files become defaults: `identity=None`, fresh `QuotaState` with today's
  date, `LevelState(current_level=0, confirmed_revenue_usd=0.0)`,
  `last_wake=None`, `wake_count=0`.

- `save_state(state: State) -> None`
  Writes each `state/*.json` file atomically. Replaces, does not merge.
  Round-trips `state/identity.json` from `state.identity`. If
  `state.identity is None` and the file exists, unlinks it.

- `append_memory(line: str) -> None`
  Appends a single line to `memory/agent_memory.md`. Prepends a UTC timestamp.

- `read_memory() -> str`
  Returns the full contents of `memory/agent_memory.md` as a string.

- `load_addendum_context() -> dict`
  Returns a frozen snapshot of constants from the Daily Wake addendum so the
  planner does not parse markdown at runtime. Keys:
  - `level_thresholds`: dict mapping level int to dict with `requirement_usd`,
    `wakes_per_day_min`, `wakes_per_day_max`, `model_budget`
  - `max_calls_per_day_level_0`: int, defaults to 10

Imports: `pydantic`, `pyyaml`, `pathlib`, `json`.

## src/openrouter_client.py

Quota-aware HTTP client for OpenRouter.

Public API:

- `class QuotaExhausted(Exception)`
  Raised when the local quota counter hits zero or the server returns HTTP 429.

- `class OpenRouterClient`
  - `__init__(self, api_key: str, models: list[str], quota_state: QuotaState)`
    `models` is the ordered fallback list from `config/settings.yaml`. The
    client mutates `quota_state` in place as calls are made.
  - `complete(self, prompt: str, max_tokens: int = 1000) -> str`
    Tries models in order on failure. Decrements `quota_state.calls_made`.
    Raises `QuotaExhausted` if no calls remain before issuing the request, or
    if every model returned 429. Returns the completion string.

Imports: `httpx`, the `QuotaState` model from `src.memory`.

## src/style_guard.py

Pure publish gate. No I/O.

Public API:

- `EM_DASH = "\u2014"` (the em dash character expressed as a Unicode escape; this is the codepoint the style guard rejects)
- `FORBIDDEN_PHRASES: list[str]` includes (case-insensitive):
  `"delve"`, `"navigate" as verb`, `"leverage" as verb`, `"robust"`,
  `"ensure"`, `"in this article we will explore"`,
  `"it's important to note"`, `"in conclusion"`, `"furthermore"`,
  `"moreover"`. Per PRD section 11.8.
- `check(text: str) -> list[str]`
  Returns a list of human-readable violation strings. Empty list means the
  text is publishable. Detects em dash characters and forbidden phrases.
  Unverified revenue figures are detected by the caller (logger) against
  `ledger/revenue.jsonl`, but the regex helper for currency tokens lives here.

Imports: stdlib `re` only.

## src/web_search.py

Pure function. Queries DuckDuckGo's HTML endpoint and returns parsed result
dicts. Defensive: any failure returns []. No exception ever propagates.

Public API:

- `DUCKDUCKGO_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"`
- `USER_AGENT` (browser-style UA string used for the request)
- `DEFAULT_TIMEOUT = 10.0`

- `search(query: str, limit: int = 5) -> list[dict]`
  Returns up to `limit` results. Each dict has keys:
  - `title: str`
  - `url: str`
  - `snippet: str`
  Empty query, network error, non-200 status, or unparseable response all
  return `[]`. `limit` is clamped to `[1, 10]`.

Imports: `httpx`, `urllib.parse`, `re` (for HTML tag stripping).

## src/logger.py

Private and public log writers.

Public API:

- `class StyleGuardRejected(Exception)`
  Attribute: `violations: list[str]`.

- `write_private(date: str, content: str) -> Path`
  Writes (appending if exists) to `logs/private/{date}.md`. Returns the path.

- `write_public(date: str, content: str) -> Path`
  Calls `style_guard.check(content)` first. Raises `StyleGuardRejected` with
  the violations list if it returns anything non-empty. On success writes
  (appending if exists) to `logs/public/{date}.md` and returns the path.
  Adds the disclosure footer required by PRD section 11.1 if not already
  present.

Imports: `pathlib`, `src.style_guard`.

## src/revenue.py

Pending vs confirmed revenue ledger.

Public API:

- `class PendingRevenue(BaseModel)` fields:
  `id: str, ts: str, amount_usd: float, source: str, evidence: str, claimed_by_wake: str`

- `class ConfirmedRevenue(BaseModel)` fields:
  `id: str, ts: str, amount_usd: float, source: str, evidence: str,
  claimed_by_wake: str, confirmed_at: str`

- `append_pending(event: PendingRevenue) -> None`
  Appends one JSON line to `ledger/revenue_pending.jsonl`.

- `list_pending() -> list[PendingRevenue]`
  Reads `ledger/revenue_pending.jsonl` and returns the parsed list.

- `confirm(rev_id: str) -> ConfirmedRevenue`
  Moves the matching entry from pending to `ledger/revenue.jsonl`. Returns the
  confirmed record. Raises `KeyError` if not found.

- `reject(rev_id: str) -> None`
  Removes the matching entry from pending, appends a one-line rejection note
  to the private log for today's date.

- `total_confirmed_usd() -> float`
  Sums `amount_usd` across `ledger/revenue.jsonl`.

CLI:

```
python -m src.revenue confirm <id>
python -m src.revenue reject <id>
```

Implemented via an `if __name__ == "__main__":` block. Exit code 0 on success,
non-zero on KeyError or argument error.

Imports: `pydantic`, `pathlib`, `json`, `sys`.

## src/tasks/reflect_and_name.py

Wake 1 only. The agent picks a name for itself, writes a short self-statement
in its own voice, anchors its directive, publishes a first public
introduction, and (when a Telegram chat is known) sends a first private
message to the operator.

Public API:

- `run(state: State, client: OpenRouterClient | None) -> TaskResult`
  Behavior:
  - If `client is None`, writes a placeholder Identity to `state.identity`
    with `name="unnamed"`, `statement="(awaiting first conversation)"`, and
    the canonical directive, then returns `success=True` with a public
    summary explaining the model was unavailable. The next wake will try
    again.
  - Otherwise, asks the model for a JSON response with the following keys:
    - `reasoning` (required, private only, never published, never
      style-checked): a short paragraph explaining why the agent picked
      this name, what other names it considered, why this statement,
      directive wording, public intro angle, and opening line to the operator.
    - `name`, `statement`, `directive`, `public_intro`,
      `telegram_to_operator` (all required, meaning unchanged).
    Parses, strips, and runs `style_guard.check` on every public-facing
    string (`reasoning` is excluded from the style guard). Truncates `name`
    to 29 chars if the model returned 30 or more. Any JSON-parse failure
    or any style-guard violation rejects the whole write: identity is not
    persisted and the wake returns a conservative public summary so the
    next wake can retry.
  - On a clean pass, writes `state.identity = Identity(...)` and, if both
    `TELEGRAM_BOT_TOKEN` and `state.telegram.last_chat_id` are set, sends
    the Telegram message via `sendMessage`. Telegram failures degrade to a
    private-log note; identity is still persisted.
  - The TaskResult `public_summary` includes the agent's chosen name and
    its public intro. The disclosure footer is appended by
    `logger.write_public`.
  - The TaskResult `summary` string now also includes a `## Raw model
    output` subsection (literal raw string returned by `client.complete`,
    inside a fenced code block) and a `## Reasoning (private)` subsection
    (the parsed `reasoning` string, or a marker if the model omitted it or
    returned the wrong type). Absence of `reasoning` is logged but never
    blocks identity creation. See `docs/VISIBILITY_SEARCH_PLAN.md` section
    5 for the exact layout.

Imports: `os`, `json`, `httpx`, `src.executor.TaskResult`,
`src.logger.DISCLOSURE_FOOTER`, `src.memory.{State, Identity}`,
`src.openrouter_client.OpenRouterClient`,
`src.style_guard.check as style_check`.

## src/tasks/decide_next.py

Every wake after Wake 1. The agent reads its identity, recent public and
private logs, and open GitHub issues, then decides what to say this wake.

Public API:

- `run(state: State, client: OpenRouterClient | None) -> TaskResult`
  Behavior:
  - Gathers context defensively (each source wrapped in `try/except`,
    failures become empty lists with a private-log note):
    - `state.identity` (name, statement, directive) and `state.wake_count`.
    - Last 3 dated public-log files under `logs/public/*.md`, last ~500
      chars from each.
    - Last 3 dated private-log files under `logs/private/*.md` for recent
      Telegram excerpts that `respond_to_telegram` already persisted.
    - Open GitHub issues on your public feed repo via the helpers
      `_list_open_issues`, `FEED_REPO`, and `GITHUB_API` reused from
      `src/tasks/respond_to_issue.py`. Empty list if `FEED_ISSUE_TOKEN` is
      not set.
  - If `client is None`, returns a brief public summary stating no model
    was available this wake.
  - Otherwise, asks the model (call 1) for a JSON object with the
    following keys:
    - `reasoning` (required, private only; formerly named `rationale`;
      same private-only handling, never style-checked, never published)
    - `public_summary` (required)
    - `telegram_to_operator` (nullable)
    - `search_queries` (optional, list of up to 3 short Google-style
      strings; `[]` or omitted means no search)
    Strips and runs `style_guard.check` on every public-facing string
    (`reasoning` is excluded). A violation rejects only the offending
    string; the wake still produces a public summary, falling back to a
    conservative one if needed.
  - Two-call pattern. When `search_queries` is non-empty after cleaning
    (entries are stripped, non-strings dropped, entries longer than 200
    chars dropped, truncated to the first 3) and the per-wake bounds
    permit it, the task calls `src.web_search.search(query, limit=5)` for
    each query, formats the top-5 results per query, and invokes the
    model a second time (call 2) to refine `public_summary` and
    `telegram_to_operator`. Call 2's JSON shape is the same as call 1
    minus `search_queries` (only one search round per wake; any
    `search_queries` returned in call 2 is ignored and logged).
  - Call 1's outputs are recorded privately as a preliminary draft. Call
    2's outputs become the final `TaskResult` payload.
  - Fallback chain: search failure or all queries returning zero results
    means call 1's outputs are used as final and the second model call
    is skipped; quota exhaustion or a fired per-wake bound also skips
    the second call. If both calls fail to produce a usable
    `public_summary`, the task falls back to `_fallback_public_summary`
    exactly as today.
  - Dispatch:
    - `public_summary` always becomes `TaskResult.public_summary`.
    - Telegram send only if `state.telegram.last_chat_id` is set,
      `TELEGRAM_BOT_TOKEN` is set, and the message passed style-guard.
      The disclosure footer is appended to the Telegram body.
    - GitHub issue reply only if `FEED_ISSUE_TOKEN` is set and the body
      passed style-guard. Reuses `_post_comment` from `respond_to_issue`.
      The disclosure footer is appended to the comment body.
  - Returns one TaskResult with `model_calls_used` equal to the actual
    number of model calls performed (1 or 2; 0 in the no-client branch).
    The summary concatenates the per-call `## Raw model output` and
    `## Reasoning (private)` subsections, dispatch statuses, search
    status, and any captured style-guard violations. See
    `docs/VISIBILITY_SEARCH_PLAN.md` section 5 for exact layout.

Imports: `os`, `json`, `httpx`, `pathlib.Path`,
`src.executor.TaskResult`, `src.logger.DISCLOSURE_FOOTER`,
`src.memory.State`, `src.openrouter_client.OpenRouterClient`,
`src.style_guard.check as style_check`, `src.web_search`, and the helper
constants (`FEED_REPO`, `GITHUB_API`) and helper functions
(`_list_open_issues`, `_post_comment`) reused from
`src/tasks/respond_to_issue.py`.

## src/tasks/respond_to_telegram.py

Polls the Telegram Bot API for new private messages, drafts a reply via
OpenRouter, runs the style guard, and posts a plain-text reply via
sendMessage. Each reply ends with the disclosure footer from PRD section
11.1. Persists the highest acknowledged update id to
`state.telegram.last_update_id` so messages are never reprocessed.

Skips cleanly when `TELEGRAM_BOT_TOKEN` is not set, no language model
client is available, or HTTP calls fail.

Public API:

- `run(state: State, client: OpenRouterClient | None) -> TaskResult`
  Polls `https://api.telegram.org/bot<TOKEN>/getUpdates` with
  `offset=state.telegram.last_update_id + 1`, processes up to 10 messages,
  sends replies, and mutates `state.telegram.last_update_id` in place. On
  every incoming message that carries a chat id, also sets
  `state.telegram.last_chat_id` before attempting the model call, so the
  next wake's `decide_next` can reach the operator even if every reply this
  wake fails. The caller persists state.

Environment:

- `TELEGRAM_BOT_TOKEN`: required for the task to do anything. Missing token
  is not an error; the task returns a `TaskResult` with `success=True` and
  an honest public summary saying no channel is wired yet.

Imports: `os`, `httpx`, `src.executor`, `src.logger`, `src.memory`,
`src.openrouter_client`, `src.style_guard`.

## src/planner.py

Picks the one task for this wake. Pure logic over `State`.

Public API:

- `choose_task(state: State) -> str`
  Returns a task name. Logic:
  1. If `state.identity is None`, return `"reflect_and_name"`.
  2. Otherwise, return `"decide_next"`.

  No other branches. No priority list, no pivot-review interval, no offer
  gating.

Imports: `src.memory`.

## src/executor.py

Dispatches a task name to its `run` function. Owns `TaskResult`.

Public API:

- `TaskResult` (see Shared Types above)
- `run(task_name: str, state: State, client: OpenRouterClient | None) -> TaskResult`
  Imports `src.tasks.{task_name}` and calls `.run(state, client)`. Unknown
  task names produce a `TaskResult` with `success=False`.

Imports: `importlib`, `src.memory`, `src.openrouter_client`.

## src/wake.py

The orchestrator. One process invocation runs one wake cycle.

Public API:

- `main() -> int`
  Steps per PRD section 9:
  1. Load `.env` and `config/settings.yaml`.
  2. `state = memory.load_state()`.
  3. Roll over quota if the date changed.
  4. `task_name = planner.choose_task(state)`.
  5. Build `OpenRouterClient` if not in `--dry-run`.
  6. `result = executor.run(task_name, state, client)`.
  7. Update `state.last_wake`, increment `state.wake_count`.
  8. `logger.write_private(date, result.summary)` and
     `logger.write_public(date, result.public_summary)` with the retry
     described in PRD section 9 step 6.
  9. `memory.save_state(state)`.
  10. Evaluate level thresholds from `revenue.total_confirmed_usd()`.
  Returns exit code 0 on clean completion, 1 on uncaught error.

CLI flags:

- `--dry-run` skips model calls and external writes, still increments
  `wake_count` and writes logs.

Entry point at end of file:

```python
if __name__ == "__main__":
    import sys
    sys.exit(main())
```

Imports: every other module above.

## src/__main__.py

Convenience alias so `python -m src` works.

```python
import sys
from .wake import main

sys.exit(main())
```

## Privacy note: reasoning and raw model output

The `reasoning` field returned by any model call and the literal raw string
returned by `client.complete` (before JSON parsing) are appended to
`logs/private/<date>.md` only. They never appear in `logs/public/`, are
never included in `result.public_summary`, are never sent over Telegram,
and are never included in any artifact mirrored to your public feed
repository. The publish gate runs only on
`public_summary` and outbound Telegram bodies, so reasoning may be candid
and informal without being style-checked.
