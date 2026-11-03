# Cost Model (measured where possible, assumptions marked)

The question: what do our rails actually cost per task, and how does
that compare to flat subscriptions (ChatGPT Max, Claude, Grok)?

All "measured" numbers come from `fleet/evals/eval_results.jsonl`
(tracked) on 2026-11-03. Flat-plan prices are **assumptions to
confirm** — they are not billing data we can read from the machines.

## Measured unit costs (metered rails only)

| rail | rows | pass | total | $/task | $/pass |
|---|---|---|---|---|---|
| gateway, DS-flash (`vercel/deepseek/deepseek-v4.1-flash`) | 234 | 228 | $0.0324 | **$0.000138** | $0.000142 |
| gateway, glm-5.3 (`vercel/zai/glm-5.3`) | 23 | 21 | $0.0407 | **$0.001771** | $0.001940 |
| gateway, all rows | 257 | 249 | $0.0731 | $0.000284 | $0.000294 |
| **agentic** (opencode + DS-flash, tool loop) | 10 | 10 | $0.8118 | **$0.081181** | $0.081181 |

Read that again: **glm-5.3 costs ~12.8x per single-shot task what
DS-flash costs** — the measured basis for the code-class routing
migration already applied. And **an agentic task through the metered
rail costs ~600x a single-shot task** ($0.081 vs $0.000138): the agent
loop's context grows every step, and every step is billed.

Subscription rails (claude/codex/grok) record no per-task cost in eval
rows — their value is flat-rate; `$/pass` for them is honestly
**n/a (plan)**, never invented.

## Monthly projection at our cadence

Nightly single-shot battery = 22 tasks x 30 days = **660 tasks/month**.
Agentic cadence is a choice; at 660 agentic tasks/month:

| rail | single-shot 660/mo | agentic 660/mo | note |
|---|---|---|---|
| DS-flash metered | **~$0.09** | **~$53.6** | measured unit costs |
| glm metered | ~$1.17 | (not measured agentic) | measured single-shot |
| ChatGPT Max (Codex included) | ~$200 flat | ~$200 flat | assumption |
| Claude plan | unknown | unknown | confirm tier/price |
| Grok plan | unknown | unknown | confirm tier/price |

The agentic projection changes the verdict shape: **metered is
effectively free for measurement, but not for agentic volume.** $53/mo
of metered agentic is still below a $200 flat plan — yet the flat plan
also buys interactive sessions, while its agentic usage sits inside
quota windows. The honest rule: single-shot measurement -> metered;
agentic volume -> flat plan when you already pay for it, metered when
you don't.

## Break-even vs a $200/mo flat plan

| rail | tasks/month to equal $200 | multiple of our nightly volume |
|---|---|---|
| DS-flash metered | ~1,450,000 | ~2,200x |
| glm metered | ~113,000 | ~171x |

At our measurement volume the metered rail is effectively **free**
($0.09/mo). The flat plans buy something else.

## What the $200/mo actually buys (and doesn't)

**Buys:** interactive agent sessions with high usage limits — the
long tool-loop work where you (a human) sit with the model for hours.
Those sessions are not battery tasks; they are the work itself.

**Doesn't buy:** cheaper measurement. Per task, metered DS-flash is
~2,200x cheaper than a $200 plan at our nightly volume.

**Rate limits are real (measured):** the opencode free tier hit its
**daily usage limit** mid-probe (0/3 recorded); subscription plans have
their own windows. Metered pay-as-you-go has no quota window — it
scales with spend, not with windows.

## Measured latency (same probes, same prompts)

| harness | single-shot avg | agentic avg |
|---|---|---|
| opencode + DS-flash | **2.4 s** | **18.5 s** |
| grok (plan) | 13.8 s | 28.5 s |
| claude (plan) | 17.4 s | 39.3 s |
| codex (plan) | 17.8 s | ~70 s (2/3 pass; one fixture-location failure) |

Latency is a real cost line for interactive routing: at equal measured
quality, DS-flash finishes the same agentic task in **half** the time
of the subscription rails.

## Other things to consider

1. **Quality is measured, not assumed**: on the single-shot probe all
   four harnesses scored 100%; on agentic probes DS-flash/grok/claude
   3/3 and codex 2/3 (its one failure was locating a fixture, recorded
   verbatim). More tasks will sharpen this.
2. **Portability / lock-in**: Token Pass keys work across every
   harness (opencode, direct API, any OpenAI-compatible client).
   Subscription quotas bind to one vendor's CLI.
3. **Privacy**: the routing schema claims ZDR by default for Token
   Pass (glm) - that claim should be verified against the provider
   before routing sensitive work. Subscription data policies differ
   per vendor.
4. **Capacity windows**: subscriptions throttle by plan window; metered
   scales by spend. For unattended nightly work, metered avoids
   "quota exhausted at 03:30" surprises.
5. **Fixed vs variable**: $200/mo is a fixed cost you must justify
   with interactive hours; metered is variable and tracks use. Our
   variable spend today is $0.073 lifetime.
6. **The free rail is bursty**: daily caps make it a bonus, not a
   foundation (measured).

## Verdict

- **Single-shot measurement**: metered DS-flash (measured cheapest and
  fastest; ~$0.09/mo at nightly volume).
- **Agentic volume**: metered costs ~$0.081/task (~$54/mo at nightly
  cadence) — real money; flat plans you already pay for absorb it
  inside quotas (codex 7/7 on its plan; claude 10/10). Use metered
  agentic where you don't already hold a plan, or for latency-critical
  loops.
- **Interactive deep work**: keep the flat plans — they buy sessions;
  their marginal battery cost is zero but their quota windows are not.
- **glm**: only where its quality edge justifies ~13x the per-task
  cost; today the probe shows no such edge.
- Next steps: receipts join shipped this wave (window+model, tight
  window); next refinement is step-level accounting if the agent loop
  cost matters more (context-size optimization beats skipping steps).
