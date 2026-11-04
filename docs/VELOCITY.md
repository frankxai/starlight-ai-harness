# Velocity — why the gateway rail is fast, and how to go faster

Measured from the tracked ledger (`fleet/evals/eval_results.jsonl`,
429 rows on 2026-11-04) and same-day live runs. No guesses below.

## Which rail we use

- **Endpoint**: `https://ai-gateway.vercel.sh/v1/chat/completions`
  (Vercel AI Gateway, OpenAI-compatible chat completions).
- **Key**: `vck_` Token Pass key read from `~/.config/opencode/opencode.json`.
- **Models**: `vercel/deepseek/deepseek-v4.1-flash` (primary code
  rail), `vercel/zai/glm-5.3` (first fallback), `vercel/moonshotai/kimi-k3`
  (OmO config).
- **Call shape**: one non-streaming HTTP POST per task
  (`urllib.request`), timeout 120 s.

## Why it is so much faster (decomposition, same prompts)

| path | same 3 tasks | n | avg |
|---|---|---|---|
| gateway direct, DS-flash | **1.81 s** | 51 | model + 1 HTTP call |
| opencode CLI, DS-flash | 2.37 s | 3 | **harness overhead ~+0.56 s** |
| claude / codex / grok CLIs | 13.8–17.8 s | 9 | their bootstraps + models |
| gateway glm-5.3 vs DS (22-task battery) | 3.9 s vs 2.23 s | | glm ~1.75x slower model |

So the gap is **not gateway magic**. It is:

1. **No agent harness in the path.** A direct single-shot call skips
   session bootstrap, system prompt assembly, tool schemas, permission
   checks — measured at only ~+0.56 s for the lightweight opencode CLI,
   but 13–18 s for the heavier subscription CLIs.
2. **A fast model.** DS-flash is ~1.75x faster than glm-5.3 on the
   same battery, and much faster than the subscription rails' default
   models.
3. **Provider prompt caching** shows up in receipts as `cache_read`
   tokens — repeated prefixes are cheap and fast, which helps every
   caller that keeps prompts stable (we do: deterministic battery
   prompts).

## Measured parallel throughput (the big lever)

`run-gateway --concurrency N` (new): independent tasks dispatched in
parallel; wall clock becomes the slowest batch.

| config (22 tasks, same model) | wall | speedup |
|---|---|---|
| c=1, warm repeat | **44.8 s** | 1x |
| **c=3** | **10.9 s** | **4.1x** |
| c=6 | 14.8 s | 3.0x (overload) |

Honest finding: **c~3 is the sweet spot on this key/provider**; c=6
inflates per-call latency (likely provider/per-key throughput limits),
so more parallelism stops helping. Adaptive concurrency (start 3,
back off on 429/latency inflation) is the next refinement.

## Levers still on the table

1. **Streaming (TTFT)**: our calls are non-streaming. Total time does
   not change, but time-to-first-token does — matters for interactive
   routing, not for the battery. Add a streaming probe to measure the
   gateway's edge latency.
2. **Connection reuse**: each task opens a new TLS connection
   (`urllib`). A keep-alive session saves ~100–300 ms/call; meaningful
   at 22+ calls/night, negligible at 3.
3. **Context diet for agentic runs**: agentic opencode+DS costs ~15 s
   and $0.081/task (vs 2.4 s / $0.000138 single-shot) because the loop
   re-bills growing context every step. Trimming system prompts and
   tool-output caps is the highest-value latency+cost lever.
4. **Class-based chains**: route by measured class (already: DS-flash
   for code, mimo for scout); never send a slow model where a fast one
   passes.
5. **Parallelism across rails**: the harness matrix is sequential per
   harness — running harnesses concurrently turns wall time into the
   slowest rail (~39 s claude) instead of the sum (~2.5 min for 4
   rails).
6. **Cloud executor**: when D1 flips, GHA matrix jobs split the battery
   across runners (horizontal scale, not just threads).

## Throughput across the estate

- Nightly battery: **45 s → 11 s** at c=3 (measured).
- Matrix runs: 4 rails in parallel → wall = slowest rail, not the sum.
- Agentic: parallelize per harness (safe: separate workdirs), bounded
  to the measured sweet spot (c~3) to avoid latency inflation.
- This velocity is what lets the loop run more often: an 11 s battery
  can run hourly, not just nightly.
