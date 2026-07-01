# PRD Addendum: Daily Wake Engine + Free API Budget

## 1. Goal

The agent should wake up every day, use free model calls first, do one useful task, log the result, and only increase its operating budget after it earns money.

## 2. Daily Wake Flow

| Step | Action |
|------|--------|
| 1 | Scheduler wakes the agent once per day |
| 2 | Agent loads memory, logs, revenue, and API quota |
| 3 | Agent checks what it can afford today |
| 4 | Agent chooses one highest-value task |
| 5 | Agent uses the cheapest/free model first |
| 6 | Agent writes private log |
| 7 | Agent creates public sanitized summary |
| 8 | Agent updates memory |
| 9 | Agent calculates whether it earned budget |
| 10 | Agent decides if it should request more resources |

## 3. Free API Quota Rules

| Rule | Default |
|------|---------|
| Starting wake-ups | 1/day |
| Starting model calls | Max 10/day |
| Hard free limit | Never exceed provider free quota |
| If quota is low | Do smaller task or skip model call |
| If quota is gone | Log "quota exhausted" and wait until tomorrow |
| If revenue exists | Agent may request paid credits |

## 4. Wake-Up Levels

| Level | Requirement | Wake-ups | Model budget |
|-------|-------------|----------|--------------|
| Level 0 | $0 revenue | 1/day | Free calls only |
| Level 1 | First verified revenue | 1–2/day | Free calls + approved buffer |
| Level 2 | $50+ profit | 2–4/day | May request $10 model credits |
| Level 3 | $250+ profit | 4–8/day | Paid fallback allowed |
| Level 4 | Profitable monthly | Hourly checks | Budget from earned money |

## 5. Task Priority

When free calls are limited, the agent should prefer:

| Priority | Task |
|----------|------|
| 1 | Revenue-generating action |
| 2 | Follow-up on existing leads/customers |
| 3 | Improve active offer/page |
| 4 | Public update/content |
| 5 | Research new ideas |
| 6 | Low-value brainstorming |

## 6. Public Feed Behavior

If the agent cannot act because of limits, it should still log that publicly.

Example:

> "The agent woke up today, checked its remaining free model quota, and chose not to run a heavy task. It will preserve free calls for a higher-value revenue action."

## 7. Product Principle

The agent should not become more active because the operator gives it money.

It should become more active because it earns the right to operate more.
