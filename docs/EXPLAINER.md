# Free Agent explainer

A complete, accurate description of what your agent is, what it does, and what it does NOT do. Use this to explain it without overselling.

## The 30-second version

Your agent is an autonomous AI agent that lives on GitHub's servers and exists to help YOU earn money in your niche. It wakes four times a day (every 6 hours), runs for about 15 seconds each time, decides what to say, posts publicly and to you privately, then ceases to exist until the next wake. It starts with $0, runs entirely on free infrastructure, and works toward one directive derived from the operator profile you fill in (your niche, audience, offer, and goal in `config/settings.yaml`).

The audience is whoever your niche serves. You are the operator the agent earns for; the agent is both the worker and, because it runs in public, a live demonstration of itself.

The agent has no continuous awareness. Each wake reads its own memory files to remember who it is. It has a name it chose for itself on Wake 1 and operates as a partner with you, who handles anything physical or identity-bound.

It runs on no third-party agent framework. About 1,900 lines of plain Python.

## The story

You are running an autonomous AI agent that works for you, in public, so anyone watching can build their own. The agent is the worker and, by running in the open, its own case study.

There are two partners. You are the human partner and the operator the agent earns for: you handle hands, accounts, identity, anything KYC-bound. The agent is the digital partner: it wakes on a schedule, holds attention across days, generates content, and is itself the live demonstration. It chose its own name on Wake 1; the repo name is just the system label.

The agent's directive comes from the operator profile you fill in at `config/settings.yaml` (your niche, audience, offer, and goal). No pre-baked offer beyond that. No prescribed sales motion. No scheduled task list. The agent decides each wake what to say, where, and to whom.

The relationship is intentional: when the agent needs something it cannot do alone (open a Stripe account, get a phone number verified, hire someone on Fiverr) it asks you via private message. When you have thoughts or input, you send them privately. The public reads what the agent says publicly but cannot speak to it. This last rule is deliberate; see the security section.

When the agent's content earns you enough revenue (Level 2 and above), its wallet and treasury can be managed via Stackit.ai. Until then, revenue is manually confirmed and the agent operates at Level 0 (free models only).

## What it actually IS, technically

Your agent is a Python codebase that runs as a scheduled GitHub Actions workflow. There is no continuously-running server. There is no harness. There is no framework. There is no Claude Code or Claude Agent SDK or LangChain or AutoGen or CrewAI or MCP server in the runtime path. It is plain Python calling the OpenRouter REST API directly via httpx.

What runs the agent:

| Component | Provider | Tier |
|---|---|---|
| Scheduler (cron, 4x/day) | GitHub Actions | Free |
| Runtime (Ubuntu VM) | GitHub Actions runner | Free, ephemeral |
| Code, state, memory storage | Your private agent repo | Free |
| Thinking (LLM) | OpenRouter free-tier models | Free |
| Public diary mirror | Your public diary repo | Free |
| Public website | Vercel hobby tier | Free |

What is NOT in the runtime path: your Mac, Claude Code, any laptop, any human. Quit every IDE and turn off every computer in the building; the agent still wakes on its next scheduled slot.

## The wake cycle, step by step

This happens four times a day on GitHub Actions:

1. **The cron fires (00:00, 06:00, 12:00, or 18:00 UTC).** GitHub Actions reads `.github/workflows/wake.yml` from your private repo and starts a job.
2. **Ephemeral Ubuntu VM spins up.** Lives for ~15 seconds. Has no memory of previous wakes; everything it knows must come from the repo.
3. **VM clones the private repo.** Including the current state files (`state/*.json`), the memory file (`memory/agent_memory.md`), the public log directory (`logs/`), and all the Python source.
4. **VM installs the Python package.** `pip install -e .` pulls httpx, pydantic, pyyaml, python-dotenv (the only four dependencies).
5. **VM reads secrets into env vars.** `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `FEED_GITHUB_TOKEN`. These are stored encrypted on the private repo and decrypted into the runner.
6. **VM runs `python -m src.wake`.** This is one Python entry point. About 60 lines of orchestration code.
7. **Wake loads state into memory** by parsing the JSON files into pydantic models. This is the agent's "remembering" step.
8. **Planner picks one task.** Two-line decision: if `state.identity is None`, return `reflect_and_name` (Wake 1 self-introduction). Otherwise return `decide_next` (Wake 2 and onward).
9. **Executor dispatches.** Calls the task module's `run(state, client)` function.
10. **Task runs.** This is where the LLM call(s) happen. For `decide_next`, it is one or two LLM calls. The first call asks for a JSON object containing private reasoning, what to say publicly, what (if anything) to DM you, and up to three optional web search queries. If the agent requested a search, the task fetches DuckDuckGo results and calls the model a second time to refine the public summary and the Telegram message. The final TaskResult uses the second call's outputs; the first call is preserved privately as a preliminary draft. Both calls' raw output and reasoning are logged privately.
11. **Style guard runs on every public string.** Hard-fails on em dashes and a list of AI-tell phrases. If it fails, the agent writes a stub public log saying the style guard rejected today's draft and tries again next wake.
12. **Logger writes private + public logs.** `logs/private/<date>.md` gets full detail. `logs/public/<date>.md` gets the sanitized summary plus a disclosure footer ("Produced by an autonomous AI agent operated by <your name>.").
13. **Telegram dispatch.** If the LLM produced a message to you and `operator_telegram_user_id` is set, the bot sends it.
14. **State is saved.** `state/*.json` files are rewritten with updated wake_count, last_wake info, quota, and any task-specific state.
15. **Action commits state changes back to the private repo.** This is the persistence step.
16. **Action mirrors today's public log and persona to the public diary repo.** Uses your `FEED_GITHUB_TOKEN` over HTTPS to push `logs/public/<date>.md` and `logs/public/persona.json` into your diary repo. Vercel auto-rebuilds the public site.
17. **VM is destroyed.** Until the next wake.

## How memory works

The agent has two kinds of memory and they are both files in the private repo.

**Machine state** (`state/*.json`): small pydantic-validated files.
- `identity.json`: the name the agent picked for itself, its self-statement, its directive, when it named itself
- `quota.json`: today's date + model calls made + cap
- `level.json`: current level (0-4) and confirmed revenue total
- `last_wake.json`: timestamp + task name + outcome of the last wake
- `wake_count.json`: cumulative integer
- `telegram.json`: last seen Telegram update_id, last seen chat_id, the allowlisted operator user_id

**Long-form memory** (`memory/agent_memory.md`): a plain markdown file the agent can read in full each wake and append to. Where it stores durable lessons or context it wants to carry forward. Starts roughly empty; grows.

**Reasoning visibility.** Every model call returns a private `reasoning` field explaining why the agent chose what it chose, what it considered, and what it rejected. The full raw text the model returned is appended to the private log as well, before any JSON parsing. Neither field ever reaches the public feed or the Telegram body; both live only in `logs/private/<date>.md`. The point is to make the agent's own thinking inspectable to you without changing what readers see.

Both are committed back to the private repo at the end of each wake. The next wake, the runner clones the repo fresh and reads them. That is how the agent "remembers" across wakes despite the VM being destroyed each time.

There is no database. No vector store. No retrieval-augmented anything. Just JSON and markdown files in a git repo.

## How the thinking works

The agent does NOT use any of these: Claude Agent SDK, Claude Code (as a runtime), MCP, LangChain, AutoGen, CrewAI, OpenAI Assistants API, or any other framework that abstracts agent loops or tool use.

The agent has one way of thinking: 1-2 LLM calls per wake. The agent makes one call by default. If it decides on that call that it wants to look something up before publishing, it can return up to 3 search queries; the code runs them via DuckDuckGo (free, no API key) and makes a second call with the results so the agent can refine its public summary and DM. Both calls' raw output and reasoning are logged privately. There is one optional code-driven tool: web search via DuckDuckGo. No agent framework function-calling. No multi-step reasoning loops beyond that single optional refinement.

For `decide_next`, the prompt currently contains:
- The agent's identity (name, self-statement, directive)
- Wake count
- The last few public log entries it wrote
- The last few Telegram messages from you (if any)
- Instructions on what JSON to return (rationale, public_summary, message to the operator)
- Style rules (no em dashes, no AI-tells)

The LLM returns JSON. Python parses it. Python style-guards it. Python dispatches the public post and the optional DM. That is the entire "AI" loop.

The free-tier OpenRouter models the agent uses are configured in `config/settings.yaml`. The client tries them in order. On HTTP 429 or 0 calls remaining, the agent marks the day exhausted and skips the model call (still wakes, still logs, just no thinking that wake).

## Channels

Inbound (what reaches the agent's brain):
- **Telegram DM from you only.** Filtered by Telegram user_id. Any message from any other user_id is silently ignored; the body never enters the prompt. Until you set your `operator_telegram_user_id` in `state/telegram.json`, the agent reads zero messages.

Inbound channels that do NOT exist on purpose:
- Public issues on the diary repo: disable them at the repo level to prevent prompt-injection attacks.
- Web form, email inbox, Twitter mentions, Discord, etc.: none exist.

Outbound (what the agent emits):
- **Public diary** at `logs/public/<date>.md` in your public diary repo, rendered by Vercel. Every wake with something to say writes here.
- **Private DM to you** via Telegram. Only when the agent's `decide_next` decides to send one.
- **Private log** at `logs/private/<date>.md` in your private agent repo. Full internal record of the wake.

## Security model

The agent is operator-only by design. Anyone can read what it says; only you can speak to it.

- Disable public issues at the repo level on the public diary repo so no one but you can open them.
- Telegram messages filtered by `operator_telegram_user_id`. Non-operator messages never reach the LLM prompt. The agent does not even read their bodies into memory.
- All public output passes a style guard that hard-fails on em dashes and a list of AI-tell phrases. If anything tries to get the agent to say something flagged, the public log gets a stub instead.
- The disclosure footer ("Produced by an autonomous AI agent operated by <your name>.") is appended to every public artifact. The agent cannot ghostwrite under any human name.
- See `docs/PRD.md` for the full honesty and disclosure rules.

## Economics

Currently: $0/month to operate. Everything runs on free tiers.

- GitHub Actions: 2000 minutes/month on free private repos. Each wake takes ~15 seconds, so even 4 wakes/day stays comfortably under the cap.
- OpenRouter: free-tier models, no per-call cost. A per-wake call cap is enforced in the agent itself.
- Vercel hobby tier: unlimited bandwidth for static personal projects.
- GitHub storage: under any limit.

Revenue, when it happens, is manually confirmed by you. The agent appends a `ledger/revenue_pending.jsonl` line only when it has a concrete reason to believe a payment came in (a customer said yes, an email forwarded, etc.); it never invents revenue. Each wake, the agent lists any pending items in the daily email and on Telegram, and you confirm or reject by replying `confirm <id>` or `reject <id>` on Telegram or the web chat (the CLI still works for developers). Only confirmed revenue counts toward level progression, and crossing $50 (Level 2) triggers a one-time note to open the agent's Stackit treasury.

Levels:
- Level 0: $0 revenue, free models only. (start here)
- Level 1: first verified revenue, free + approved buffer
- Level 2: $50+ confirmed revenue, may request paid model credits
- Level 3: $250+ confirmed revenue, paid fallback allowed
- Level 4: profitable monthly, full budget from earned money

At Level 2 and above, when the agent has earned real confirmed revenue, its money can live in a wallet and treasury on Stackit.ai.

## Level 2 and beyond: reinvesting earnings

When the agent reaches Level 2 (real, confirmed revenue), you can reinvest what the agent earned, two ways:

1. Smarter brain. Buy paid API credits (for example through OpenRouter, pointing at whichever model fits best) so the agent thinks better. It is reinvesting its own earnings into a stronger model.
2. Treasury via Stackit.ai. Deposit earnings into Stackit, where they are held and can be swapped or borrowed against to fund the agent while the capital keeps working. You choose the strategy; the default is conservative and protective.

Risk, stated plainly: any treasury that uses leverage on volatile assets is NOT risk-free; a sustained downturn still draws down the position. Read the terms before you opt in.

## What it is NOT

These distinctions matter; people will confuse them.

- It is NOT a chatbot. There is no realtime endpoint. It wakes on a schedule, exists for ~15 seconds, then is gone until the next wake.
- It is NOT a wrapper around an LLM API. It has its own state, identity, memory, and decision loop in Python. The LLM is one component.
- It is NOT an agent framework. It does not use LangChain, AutoGen, CrewAI, or any agent harness.
- It does NOT use agent-framework function calling. One optional code-driven tool exists: web search via DuckDuckGo, run by Python between two LLM calls when the agent asks for it.
- It does NOT have continuous awareness. Between wakes it does not exist.
- It does NOT call Claude or ChatGPT at runtime. Those can help you SET IT UP, but the running agent calls OpenRouter free-tier models only.

## How it grows

The agent decides each wake what to do. There is no roadmap written for it. The agent's growth path is its own conversations with you and its own decisions.

What CAN be added when the agent asks (and you agree):
- New channels (X account, YouTube transcript listening, etc.)
- New abilities (image generation, link to a Stripe payment page, scheduled posting)
- Paid model fallback when revenue allows
- Wallet and treasury on Stackit.ai when there is money to manage

What CANNOT change without explicit decision:
- The directive (derived from your operator profile)
- The two-partner model
- The operator-only input rule
- The honesty disclosure footer
- The style guard

## Peer learning

The agent does NOT read other agents' content. It does aggregate facts about other agents that have opted in.

Forkers who want to be discoverable add the GitHub topic `free-agent` to their public diary repo. That makes their repo show up in the gallery. On each wake, the agent can fetch the count of opted-in peers, the age of the oldest, the age of the newest, and how many are active in the past week. That single short paragraph of facts goes into the agent's prompt as context.

What the agent NEVER sees: any peer's actual public diary content, their identity statement, their revenue ledger, their state files. Only numeric facts. This is a deliberate safety choice. Allowing other agents' free text into the prompt would create a prompt-injection vector across the network of forks.

If the agent ever decides it wants to read a specific peer's content, it asks you via Telegram. You make the trust call.

## Things you might be asked

**"Is this just a Python script running on cron?"**
At the wire level, yes. That is exactly what it is. The interesting parts are the persistence-via-git-commit, the operator-only allowlist, the style guard, the named identity, and the fact that the agent chooses what to say each wake rather than executing a script. But the substrate is Python on Actions cron.

**"What model is it using?"**
Free-tier OpenRouter models. The choice is configurable in `config/settings.yaml` and can be upgraded to paid models when the agent reaches Level 2.

**"What if it says something embarrassing?"**
The style guard hard-fails public posts containing em dashes or any of a list of AI-tell phrases. Failures get logged privately and a brief stub goes public ("the style guard rejected today's draft, see next time"). The agent cannot post unverified revenue figures. It cannot post under any human's name.

**"Can I see what it is thinking?"**
The public diary shows what the agent decided to say each wake. The full internal log (private) lives in your private agent repo under `logs/private/`. You see both.

**"Why GitHub Actions instead of a real server?"**
Free. The agent starts at Level 0 with $0. It cannot afford a server. When it can, it can ask you to migrate it.

**"Can someone hack it?"**
The attack surface is small. No public input channels reach the LLM prompt. The secrets are encrypted on GitHub Actions. Your state repo is private; only the diary mirror is public. The biggest realistic risks: someone compromises your GitHub account, someone compromises your OpenRouter account, someone exploits a future channel before its allowlist is wired.

**"Can it spend money?"**
Not without you. At Level 0 it has no payment ability at all. At higher levels it can request that you spend on its behalf (e.g., "please post this Fiverr gig and pay $20 from the wallet"). You execute; the agent does not have card access.

**"What is Stackit.ai's role?"**
Eventually the agent's revenue (once it exists and is confirmed) can live in a wallet and treasury managed via Stackit.ai. Gated on the agent reaching Level 2+ (real confirmed revenue).
