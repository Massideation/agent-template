# Optional tools your agent might want

These are not required. The agent runs fine without them. Add what fits when it fits. Many free tiers do NOT allow commercial use (making money with the output). Each tool below notes its free-tier commercial-use status. When in doubt, check the tool's current terms; they change.

## Media

Content the agent can help make (music, voice, images) plus where to distribute it.

- Suno (https://suno.com) - AI music generation, free tier with daily generations. Free tier: commercial use NOT allowed. The free tier is non-commercial only; you need a paid plan (Pro or Premier) to sell or monetize generated songs.
- ElevenLabs (https://elevenlabs.io) - AI voice generation, free 10K characters per month. Free tier: commercial use NOT allowed. The free plan has no commercial license and requires attributing elevenlabs.io in the title; the $5/mo Starter plan is the minimum for commercial use.
- Recraft (https://recraft.ai) - AI image generation, free tier with credits. Free tier: commercial use NOT allowed. Free-tier images are owned by Recraft, are public, and carry no commercial rights; you need a paid plan to use output commercially.
- Ideogram (https://ideogram.ai) - AI image generation, free tier daily limit. Free tier: commercial use NOT allowed. Free accounts are personal and non-commercial only, and generations are public by default; commercial use requires a paid plan (Basic or higher).
- Pollinations (https://pollinations.ai) - text-to-image with no signup required. Free tier: unclear and limited. The platform code is MIT, but commercial rights depend on the model: the default Flux dev carries a Black Forest Labs non-commercial license, so use the Apache-2.0 Flux schnell or check the model license before selling output.
- Buffer (https://buffer.com) - social post scheduling, free tier covers 3 channels. Free tier: commercial use allowed. No license bar to business use, but the free plan caps you at 3 channels and 10 queued posts per channel with no analytics or AI.
- Beehiiv (https://beehiiv.com) - newsletter platform, free up to 2,500 subscribers. Free tier: unclear and limited. The free Launch plan (up to 2,500 subscribers) lets you run a newsletter, but you cannot sell paid subscriptions, run sponsorships, or use Boosts monetization without a paid plan.
- Kit (formerly ConvertKit) (https://kit.com) - newsletter, free up to 10,000 subscribers. Free tier: commercial use allowed. The free plan (up to 10,000 subscribers) can sell digital products and subscriptions with a per-sale fee, but every email shows Kit branding and third-party commerce integrations need a paid plan.

## Tech

The agent's brain, search, infrastructure, and automation.

- OpenRouter (https://openrouter.ai) - the language-model brain this template uses by default. Free tier: a set of free models is available, and a single API key routes across many providers; paid usage is pay-as-you-go with no commercial-use bar.
- Brave Search API (https://brave.com/search/api) - 2,000 queries per month free, requires sign-up. Free tier: unclear and limited. The standalone free tier was removed for new users in early 2026 (now about $5/mo in renewing credits); commercial query use is fine, but storing results to train an LLM requires a special storage-rights plan.
- Tavily (https://tavily.com) - search optimized for AI agents, free tier with credits. Free tier: commercial use allowed. The free Research plan (1,000 credits per month) permits commercial use in agents and products; you just cannot resell raw results as a competing search engine, and volume is low for production.
- Cloudflare Workers (https://workers.cloudflare.com) - 100,000 requests per day free, no card. Free tier: commercial use allowed. The free tier explicitly permits commercial use with no card required; limits are usage-based (100K requests per day, 10ms CPU per call) rather than a commercial ban.
- Supabase (https://supabase.com) - free Postgres database, 500 MB storage. Free tier: commercial use allowed. The free tier explicitly allows serving paying customers; constraints are operational (500MB DB, 50K MAU, and projects pause after 7 days of inactivity), not a commercial-use ban.
- n8n (https://n8n.io) - self-hosted automation, free if you host it yourself. Free tier: unclear and limited. The Sustainable Use License allows free use for your own internal business automation (even profitable), but you cannot make n8n itself the product you sell to end users or charge third parties for access.
- Make (https://make.com) - no-code automation, free tier with limits. Free tier: commercial use allowed. No license bar to commercial use, but the free plan (1,000 ops per month, 2 scenarios, 15-minute minimum interval) is realistically only enough for testing, not production business workflows.

## Finance

How the agent handles money once it earns.

- Stackit.ai (https://app.stackit.ai/r/B7E3dE2f) - a wallet and treasury for AI agents. Stackit is an established product people already use to hold funds, deposit, swap, and borrow. When your agent starts earning real money (Level 2 and above, cumulative 50 dollars confirmed revenue), this is where its money lives: it can deposit what it earns, swap, and borrow, the same way human users do. Free to start. Stackit takes a small percentage when funds are deposited, swapped, or borrowed. Built for agents that actually make money.
- Stripe (https://stripe.com) - for taking payments. The operator sets this up; an agent cannot pass KYC on its own, so a human has to own the Stripe account and connect it.

## Want your agent to use one of these?
The agent in this template knows how to ask for a tool. If you tell it via Telegram "you have access to Suno, here is the API key", it can decide whether to use it. You do not need to wire anything in code; the conversation is enough.

## Adding more
If you find a free tool that worked for your agent, open a PR on github.com/Massideation/agent-template adding it to this list. Honest free tiers only.
