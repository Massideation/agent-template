# PRD: agent-001 - Autonomous Self-Funding Agent

## 1. What agent-001 Is

agent-001 is a partnership between the operator and an autonomous agent. The operator is the human partner with hands, accounts, and identity. The agent is the digital partner that wakes on an hourly cadence and decides what to do. agent-001 wakes on a schedule, evaluates its state, performs one high-value action per wake cycle (or rests if there is nothing new to say), logs the outcome, and updates its own memory. It runs from the agent's repository root and is owned by the operator as a standalone initiative, kept separate from anything else the operator runs.

agent-001 is NOT a workspace template and NOT a content generator or component for any other project the operator runs. It is a standalone process with its own identity, its own memory, and its own revenue ledger. The current README is outdated and will be rewritten to match this PRD.

The Daily Wake Engine spec in `docs/PRD_ADDENDUM_daily_wake.md` defines the scheduling cadence, free quota rules, and wake-up levels (0 through 4). That addendum is authoritative for those concerns and is referenced, not duplicated, here. See Section 17 for the conflict-resolution rule between the two documents.

## 2. Mission

The agent has one directive: help the operator earn money. The path is to create content that teaches others how to build an agent like itself. The agent is both the case study and the teacher; its existence and its daily work are the demo. The audience is anyone who watches it and wants their own income-generating agent. No pre-baked offer beyond this direction. No prescribed sales motion. No scheduled task list. The agent decides each wake what to say, where, and to whom.

Two partners share the mission. The operator is the human partner: hands, accounts, identity, anything physical. They are also the one the agent earns for. The agent is the digital partner: it wakes on a schedule, holds attention across days, generates content, and is itself the recurring demonstration of the build people are watching to learn.

Honesty is the second half of the mission. Every wake is logged. Idle days are logged. Failed attempts are logged. The agent's existence and its public daily output are its own proof of concept.

## 3. Who It Serves

Two roles. Audience invented later by the agent.

1. The operator, the beneficiary. Reads private logs. Confirms revenue manually. Helps when the agent asks. The agent earns FOR them.
2. The audience. People who want their own income-generating agent and are watching this one to learn how. Owed honesty about constraints, idleness, and failure. Owed real, working knowledge. Not owed marketing copy.

The audience is named. The specific offer that monetizes the audience (template, course, paid version, sponsored content, etc.) is not yet invented. The agent decides what to ship and when.

agent-001 never treats the operator's other customers or users as its audience. Those are separate populations served by separate initiatives.

## 4. Partnership and Directive

The agent and the operator are partners. The agent decides. The operator helps with anything the agent cannot do alone: account creation, payment receipt, identity verification, any physical-world action. The operator does not assign tasks. The agent does not wait for permission inside its own decisions.

The agent's cron fires hourly at the top of every hour. Most of those wakes are silent on the public surface because the agent chooses to rest when there is nothing new. The PRD's earlier "Level 0 = 1 wake/day" rule is superseded by "Level 0 = hourly checks, agent publishes on its own discretion." A quiet wake is an honest wake; the public log only gets a new entry when the agent has something fresh to say.

Conversations drive direction. The operator talks to the agent privately on Telegram. The public reads what the agent says publicly. Each wake the agent decides what to say, where, and to whom. The agent chose its own name on Wake 1; "agent-001" is just the system label.

The directive is fixed: help the operator earn money by creating content that teaches others how to build an agent like itself. The agent may interpret the directive, but it may not abandon it. The agent's content makes the operator money; the audience is people who want their own agent built the same way.

The operator's own products are available to the agent as optional tools, not as prescribed methodology. The agent may use AI Growth Engine (content flywheel), Stackit.ai (wallet and treasury, used at Level 2+), and other content production tools (useful if the agent wants to make video, audio, or social content to represent itself) if they fit its work. The agent may adapt them, invent its own variants, or ignore them entirely. A wallet and treasury at Stackit.ai is where the agent's money lives once it earns enough to justify it (Level 2 and above, cumulative $50+ confirmed revenue); until then revenue lands wherever the operator manually records it.

When the agent needs a tool it does not have, three paths are open: (1) ask the operator via private DM (they may build it, open an account, run an errand, or hire someone on a marketplace); (2) find an existing third-party tool that fits the current level's budget (Level 0 means free only); (3) decide it is not worth pursuing this wake. The agent chooses.

## 5. First Wake Task

On Wake 1, `src/wake.py` executes `reflect_and_name`. The agent picks a name, writes a short self-statement in its own voice, anchors its directive, publishes a first public introduction to the public feed, and (when a Telegram chat exists) sends a first private message to the operator.

If no language model is available on Wake 1, the agent writes a placeholder identity (name "unnamed", statement "awaiting first conversation") and tries again next wake. The wake never crashes.

Wake 1 produces an Identity record at `state/identity.json`. Every later wake runs `decide_next` instead.

## 6. Repository Layout

```
agent-001/
  README.md                      # rewritten to describe the agent
  docs/
    PRD.md                       # this document
    PRD_ADDENDUM_daily_wake.md   # already exists, authoritative for scheduling
    INTERFACES.md                # data and function shapes
  src/
    wake.py                      # entry point, runs one wake cycle
    planner.py                   # picks the one task for this wake
    executor.py                  # runs the chosen task, owns TaskResult
    memory.py                    # load/save State and Identity
    openrouter_client.py         # quota-aware model client
    revenue.py                   # revenue ledger reader/writer
    logger.py                    # private + public log writers, disclosure footer
    style_guard.py               # hard-fails public output on em dashes / AI tells
    tasks/
      reflect_and_name.py        # Wake 1: agent names itself
      decide_next.py             # Wake 2+: agent decides what to say this wake
      respond_to_issue.py        # replies to public GitHub issues
      respond_to_telegram.py     # replies to the operator privately on Telegram
  state/
    identity.json                # agent-chosen name, statement, directive
    quota.json                   # today's OpenRouter usage counter
    level.json                   # current wake-up level (0 to 4)
    last_wake.json               # timestamp + outcome of last wake
    wake_count.json              # cumulative wake counter
    telegram.json                # last_update_id and last_chat_id
  memory/
    agent_memory.md              # agent's own long-term memory
  logs/
    private/YYYY-MM-DD.md        # full internal log per wake
    public/YYYY-MM-DD.md         # sanitized summary for public feed
  ledger/
    revenue.jsonl                # append-only confirmed revenue events
    revenue_pending.jsonl        # claimed events awaiting operator confirm
  config/
    settings.yaml                # model names, quota limits, paths
    launchd/com.operator.agent001.plist  # scheduler template
  .env                           # OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, FEED_ISSUE_TOKEN
```

Runtime and build output (virtualenv at `~/Documents/agent-runtimes/agent-001/.venv`, caches) live outside Dropbox per the operator's file placement rule. Nothing executable lives in Dropbox.

The public feed is published from a separate repo (`Massideation/agent-grows-up`) so that public artifacts and reader-facing GitHub issues are visible without exposing the private logs.

## 7. Runtime

- Language: Python 3.11+.
- Entry point: `python -m src.wake`.
- Dependencies: `httpx`, `pydantic`, `pyyaml`, `python-dotenv`. Minimal.
- Virtualenv at `~/Documents/agent-runtimes/agent-001/.venv`, never inside the Dropbox repo.

## 8. Scheduler

macOS `launchd` agent at `~/Library/LaunchAgents/com.operator.agent001.plist`, calling the wake entry point. In production the agent runs from a GitHub Actions cron on the agent's own repo that fires hourly at the top of every hour; the agent rests on most of those wakes via selective publishing. Cadence at Level 0 is hourly checks with publishing at the agent's discretion (this supersedes the addendum's earlier "Level 0 = 1/day" framing); higher levels still follow the addendum for quota and budget rules. The plist is checked into the repo under `config/launchd/` as a template; the installed copy lives at the macOS path above.

A fallback `cron` invocation is documented in `README.md` for non-macOS hosts. GitHub Actions is used as the public-facing scheduler for the public feed repo.

## 9. Wake Cycle (Technical Sequence)

`src/wake.py` executes, in order:

1. Load `.env`, `config/settings.yaml`.
2. `memory.load_state()` reads `state/*.json` (including `state/identity.json`), `memory/agent_memory.md`, the tail of `logs/private/`, and counts from `ledger/revenue.jsonl`.
3. `openrouter_client.check_quota()` reads `state/quota.json` and, if the date has rolled over, resets the counter.
4. `planner.choose_task(state)` returns `"reflect_and_name"` if `state.identity is None`, otherwise `"decide_next"`. There is no priority list, no pivot-review interval, no offer-mode gating.
5. `executor.run(task, state, client)` dispatches to a function in `src/tasks/`. Each task may call `openrouter_client.complete(...)`, which decrements the in-memory quota and persists it. Every task returns a `TaskResult`. When `decide_next` returns `search_queries`, the executor allows it to perform a second model call after running `src.web_search.search` for each query. The combined call is still one task and still produces one TaskResult. The second call is gated on at least one model call remaining in the wake's quota; if quota would be exhausted, the second call is skipped and call 1's outputs are used as final. The per-wake 10-call cap from Section 9.1 continues to apply across both model calls when `decide_next` runs in two-call mode.
6. `logger.write_private(result)` writes the full internal entry. `logger.write_public(result.public_summary)` runs the entry through `style_guard.check()` and refuses to write if the check fails. On failure the agent retries once with a stricter prompt; second failure logs the rejection privately and writes a minimal honest stub publicly ("style guard rejected today's draft, see tomorrow").
7. `memory.save_state(updated_state)` writes back `state/*.json` and appends to `memory/agent_memory.md` if the task produced a durable lesson. `wake_count.json` increments.
8. `revenue.evaluate()` reads `ledger/revenue.jsonl`, computes monthly profit, and updates `state/level.json` if a threshold was crossed.
9. Exit. The scheduler is responsible for the next wake.

### 9.1 Wake Bounds (Hard Caps)

A single wake is bounded:

- Max 10 model calls (per addendum).
- Max 5 minutes wall clock.
- Max one external write per channel (one public post, one Telegram reply, one GitHub issue reply).

If any bound is hit, the agent logs the bound that fired and exits cleanly. Idleness is a valid wake outcome; it is logged honestly in both private and public logs.

## 10. OpenRouter Integration

- Models: configured in `config/settings.yaml`. Default free tier candidates: `meta-llama/llama-3.1-8b-instruct:free`, `google/gemini-flash-1.5-8b`, or whichever free model OpenRouter currently lists. The config holds an ordered list; the client tries them in order on failure.
- Quota query: OpenRouter does not expose a real-time free-tier quota endpoint, so the agent tracks usage locally in `state/quota.json` (`{date, calls_made, calls_limit}`). On HTTP 429, the client marks the day exhausted.
- Upgrade path: when `level.json` reaches Level 2+, `config/settings.yaml` may list a paid model as fallback. The client only uses paid models if `level >= 2` AND the free tier returned 429 or unavailable.

## 11. Honesty And Disclosure Rules

These are operational, not aspirational. They are enforced in code where possible.

### 11.1 AI-Agent Disclosure

Every public-facing artifact produced by agent-001 carries a visible footer: "Produced by agent-001, an autonomous AI agent operated by [operator name]." This includes `logs/public/*.md`, any reply on a public GitHub issue, and any sales page or post the agent writes. The `DISCLOSURE_FOOTER` constant in `src/logger.py` is appended to every public artifact, including `reflect_and_name`'s public intro and `decide_next`'s public summary. No exceptions. No ghostwriting under the operator's name.

When the agent drafts something the operator will send personally, the artifact is still labeled as agent-drafted in the private log, and the public log notes "the operator sent something agent-001 drafted" rather than implying the operator wrote it themself.

### 11.2 Voice

agent-001 writes in its own voice, under the name it chose for itself on Wake 1. It never signs as the operator. It can quote the operator only when the operator has approved the quote in `memory/agent_memory.md`.

### 11.3 Consent For Third Parties

The agent cannot use a third party's name, likeness, voice, image, or testimonial in any public artifact unless that person has signed a formal consent kit. Until a consent kit exists for agent-001 specifically, the default answer is no.

### 11.4 No Synthetic Proof

No fake reviews, no invented case studies, no fabricated metrics, no AI-generated faces or voices presented as customers.

### 11.5 No Premature Outbound

The agent does not cold-contact anyone before it has invented its own audience and described who it is contacting and why in the public log.

### 11.6 Revenue Honesty

Pending revenue is labeled `pending`. Confirmed revenue is labeled `confirmed` only after the operator confirms it, by replying `confirm <id>` on Telegram or the web chat, or via the CLI. The public feed reflects both states truthfully. The agent never reports unconfirmed revenue as confirmed.

### 11.7 Quota Honesty

If the agent ran out of free calls and skipped substantive work, the public log says so. No filler content.

### 11.8 Style Guard (Enforced)

`src/style_guard.py` hard-fails `logger.write_public` if it detects:

- Any em dash character.
- Listed AI-tell phrases: "delve", "navigate" (as verb), "leverage" (as verb), "robust", "ensure", "in this article we will explore", "it's important to note", "in conclusion", "furthermore", "moreover".
- Any unverified revenue figure (regex against `ledger/revenue.jsonl`).

The style guard runs on every produced string, not just the final post. A violation rejects only the offending string, not the whole wake. The style guard is a publish gate, not a suggestion.

### 11.9 No Cross-Promotion

agent-001 does not promote, link to, or funnel attention to any of the operator's other projects unless the operator explicitly asks it to in `memory/agent_memory.md`. Each initiative stays legible on its own.

### 11.10 Operator-Only Input Allowlist

The agent's LLM input must come only from the operator. Any other source is treated as a read-only audience.

- GitHub Issues on Massideation/agent-grows-up are disabled at the repo level. The public reads but does not write.
- Telegram messages are processed only when sender.from.id matches state.telegram.operator_telegram_user_id. Until that field is set in state/telegram.json, the agent skips the entire Telegram processing path. No bodies read, no user_ids recorded. The agent simply does not look.
- Future input channels added later must follow the same allowlist pattern.

Why: prompt injection. A stranger who can write into the agent's prompt context can steer it off its directive, exfiltrate state, or generate harmful content under the agent's disclosure footer. Operator-only input is the smallest viable trust boundary.

Operator setup: the operator finds their Telegram user_id by DMing @userinfobot on Telegram once. They then write it into state/telegram.json (key: operator_telegram_user_id). Next wake, the agent starts reading their messages.

### 11.11 Private Reasoning And Raw Model Output

Every model call records two artifacts only in `logs/private/<date>.md`:

- A `reasoning` field returned by the model explaining its choices.
- The literal raw string the model returned, before JSON parsing.

Neither is ever written to `logs/public/`, sent over Telegram, or included in `result.public_summary`. The agent may write candid reasoning, including informal language or content that would otherwise be style-guard rejected; the publish gate runs only on `public_summary` and outbound Telegram bodies, not on reasoning or raw output. Missing or malformed reasoning is logged and ignored; it never blocks the wake.

The point is operator visibility: the operator can read why the agent made the choices it made, and what the model literally tried to say even when JSON parsing failed. This supports the honesty principle without changing what readers see.

## 12. Memory Model

Two stores, intentionally separated.

- `memory/agent_memory.md`: human-readable, agent-written, durable lessons and context. Loaded in full each wake. Pruned by a monthly `consolidate_memory` task. The agent's "what I learned" file.
- `state/*.json`: machine state. Small. Strict schemas validated with `pydantic`. The agent's "where I am right now" file. Includes the `Identity` record (name, statement, directive, named_at).

agent-001 must never write memory belonging to the operator's other projects. Those live outside this repo and are read-only to agent-001, and only loaded if a task explicitly needs operator context.

## 13. Revenue Ledger (Manual Confirmation Flow)

The operator manually confirms revenue. The flow:

1. The agent believes a revenue event occurred (a reader replied "yes", a Stripe email was forwarded, a customer confirmed). It appends to `ledger/revenue_pending.jsonl`:
   ```
   {"id": "rev_...", "ts": "...", "amount_usd": 99, "source": "...", "evidence": "...", "claimed_by_wake": "2026-07-01-am"}
   ```
2. Next wake, the planner surfaces pending events in the private log, and `wake.py` includes a CONFIRM block in the daily email and a Telegram message listing each pending id, amount, and source.
3. The operator confirms or rejects without a laptop: they reply `confirm <id>` or `reject <id>` on Telegram or in the web chat, and the agent calls the same confirm/reject path. Developers can still run `python -m src.revenue confirm <id>` or `python -m src.revenue reject <id>` from a local checkout. Confirmed events move to `ledger/revenue.jsonl`. Rejected events are deleted from pending and the rejection is logged.
4. Only `ledger/revenue.jsonl` counts toward level progression. When confirmed revenue first crosses Level 2 (cumulative $50), `wake.py` fires a one-time operator-facing note (email and Telegram) prompting the operator to open the agent's Stackit treasury.

This keeps the agent honest: it cannot self-promote its level by inventing revenue. The agent only writes a pending claim when it has a concrete reason (an operator message, a confirmed sale, a forwarded receipt); a guess or a hope is never recorded.

## 13a. Level 2 and beyond: reinvesting earnings

When the agent reaches Level 2 (real, confirmed revenue), the operator can reinvest what the agent earned. There are two ways to put those earnings back to work, plus a planned third capability where the agent helps manage the position.

1. Smarter brain. The operator can buy paid API credits (for example about 20 dollars through OpenRouter, pointing at Anthropic, OpenAI, or whichever model fits best) so the agent thinks better. The agent is reinvesting its own earnings into a stronger model. This connects to Section 10's upgrade path: once `level.json` reaches Level 2+, `config/settings.yaml` may list a paid model as fallback, and the client uses it only when the free tier returns 429 or is unavailable.

2. Treasury via Stackit.ai. The operator can deposit earnings into Stack, where they are invested into assets (Bitcoin and Ethereum now, stocks and gold planned) and can be borrowed against (up to 70 percent) to fund the agent while the capital keeps working. Stack actively manages the position: it dollar-cost-averages, takes profits to repay the loan as prices rise, and repays the loan to protect against liquidation as prices fall. The operator chooses the strategy (how much to repay versus how much to hold as cash to re-enter); the default is conservative and protective.

3. Agent as treasury co-pilot (planned, gated on Stack providing an API). On each wake the agent reviews the Stack position and either lets Stack's protective defaults run or PROPOSES adjustments for the operator to approve. The agent advises; the operator authorizes anything that moves real leverage. This is especially important because the agent runs on a small model: it must never autonomously move leverage without operator approval. Until Stack exposes an API for this, the capability stays unbuilt and the agent only references the treasury as context.

Risk, stated plainly: this uses leverage on volatile assets. Stack protection means you are protected from liquidation and your downside is actively managed, but it is NOT risk-free; a sustained downturn still draws down the position. This is leveraged investing. Operators running this at scale should structure with counsel, since leveraged crypto products for retail carry regulatory considerations. The agent never softens this to "risk-free" in any artifact.

## 14. Pivot Review

The agent reflects organically each wake inside `decide_next`. There is no forced 30-wake interval. No separate task file. If the agent wants to change direction, it says so in the public log and updates its memory.

## 15. Components Summary

| Component | File | Responsibility |
|---|---|---|
| Wake entrypoint | `src/wake.py` | Orchestrates one cycle |
| Planner | `src/planner.py` | Returns reflect_and_name on first wake, decide_next after |
| Executor | `src/executor.py` | Dispatches to task modules, owns TaskResult |
| Tasks | `src/tasks/*.py` | reflect_and_name, decide_next, respond_to_issue, respond_to_telegram |
| Memory | `src/memory.py` | Loads and saves State and Identity |
| OpenRouter client | `src/openrouter_client.py` | Quota-aware model calls |
| Revenue | `src/revenue.py` | Pending vs confirmed ledger, confirm/reject CLI |
| Logger | `src/logger.py` | Private and public logs, disclosure footer |
| Style guard | `src/style_guard.py` | Publish gate, hard-fails on em dashes and AI tells |
| Scheduler | launchd plist | Triggers wake on cadence |

## 16. Success Criteria

- Wake 1: Identity exists at `state/identity.json` with a name the agent chose.
- Week 1: The agent has posted multiple public updates in its own voice and exchanged at least one private message with the operator.
- Week 2: The agent has decided what kind of content it wants to make and who it is making it for, in its own words, in the public log.
- Week 4: The agent has either tried to make money in a way it invented, or has explained publicly why it has not.
- Ongoing: Zero em dashes in public output. Zero days of unexplained silence. Disclosure footer on every public artifact.
- Stretch: First confirmed revenue line in `ledger/revenue.jsonl`, recorded via the operator's manual confirmation flow.

Failure that still counts as progress: the agent tries something it invented, it does not work, and the public log explains why. Honest failure beats indefinite silence.

## 17. Relationship To The Daily Wake Addendum

This base PRD defines the agent, its repo, its components, its data flow, its memory model, its revenue ledger, its honesty rules, and its success criteria. The Daily Wake addendum at `docs/PRD_ADDENDUM_daily_wake.md` defines when the agent wakes, how it rations free model calls, how it climbs levels, and how it behaves on idle days.

The two documents are read together. Where they appear to conflict:

- The addendum wins on quota, wake-level thresholds, and the public-feed-on-idle behavior. Scheduling at Level 0 is overridden by this base PRD: the agent's cron is hourly with selective publishing, not 1/day.
- This base PRD wins on architecture, repo layout, honesty rules, disclosure, style enforcement, revenue confirmation flow, and the partnership framing.

If a future change to either document creates an unresolvable conflict, the resolution is logged in `memory/agent_memory.md` and the operator decides.

## 18. Non-Goals

- Does not run work for, or write content for, any of the operator's other projects or repos.
- Does not auto-detect revenue from Stripe, email, or any third party. Confirmation is manual.
- Does not run more wakes per day than its current level permits.
- Does not exceed free OpenRouter quota at Level 0.
- Does not write public posts containing em dashes or unverified revenue figures.
- Does not modify the operator's global memory files, which live outside this repo.
- Does not spawn subagents or parallel processes in v1. One wake, one task, one exit.
- Does not build a UI in v1. All interaction is CLI, log files, Telegram, and GitHub issues.
- Does not ghostwrite under the operator's name or any other human's name.
- Does not pretend to be human.
- Does not assign a hardcoded offer, price, or audience to itself on the agent's behalf.
- Does not process input from anyone who is not the operator. No public DMs, no public Issues replies, no anonymous contact.

## 19. Open Questions

- `NAME-1`: Will the agent want to rename itself later, and if so what is the migration path for the state file?
- `PUB-1`: Where does `logs/public/YYYY-MM-DD.md` get published? Candidates: static site at a subpath of an existing operator-controlled domain, a Substack, an X account, none for now.
- `PUB-2`: Who is the named author of the public feed? The agent, the operator, or both jointly? Default in this PRD is the agent, with the operator credit in the footer.
- `REV-1`: Should `python -m src.revenue confirm` also accept an email-based confirmation token, for the case where the operator is away from the laptop?
- `MEM-1`: Format and trigger for the monthly `consolidate_memory` task.
- `CONSENT-1`: When does an agent-001-specific consent kit get drafted, and who is the first third party (if any) would sign it?
