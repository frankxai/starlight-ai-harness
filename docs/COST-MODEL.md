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

Read that again: **glm-5.3 costs ~12.8x per task what DS-flash costs.**
This ledger is the measured basis for the code-class routing migration
already applied (primary -> DS-flash, glm first fallback).

Subscription rails (claude/codex/grok) and the opencode CLI runs record
no per-task cost in eval rows — their value is flat-rate. `$/pass` for
them is honestly **n/a (plan)**, never invented.

## Monthly projection at our cadence

Nightly battery = 22 tasks x 30 days = **660 tasks/month**.

| rail | $/month at 660 tasks | note |
|---|---|---|
| DS-flash metered | **~$0.09** | measured unit cost |
| glm metered | ~$1.17 | measured unit cost |
| ChatGPT Max (Codex included) | ~$200 flat | assumption (Frank's plan) |
| Claude plan | unknown | confirm plan tier/price |
| Grok plan | unknown | confirm plan tier/price |

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

- **Measurement + latency-sensitive routing**: metered DS-flash
  (measured cheapest and fastest; $0.09/mo at nightly volume).
- **Interactive deep work**: keep the flat plans — they buy sessions,
  not tasks; their marginal battery cost is zero but their quota
  windows are not.
- **glm**: only where its quality edge justifies ~13x the per-task
  cost; today the probe shows no such edge.
- Next step: receipts join so metered CLI runs (opencode/DS through
  the plugin) also carry measured $/task instead of "unrecorded".
