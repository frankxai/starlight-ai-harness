# Harness Matrix (measured)

One battery, every harness. Same deterministic checkers
(`fleet/harness_eval.py` tasks, no LLM judge), same prompts, each CLI
on its own configured model/plan unless `--model` is passed. Rows land
in `fleet/evals/eval_results.jsonl` with `harness=<name>`; regenerate
the table with `python fleet/harness_matrix.py --report`.

## Measured (2026-11-03, 3-task probe: math, instruction, json-format)

| harness | model | n | pass | avg latency | cost |
|---|---|---|---|---|---|
| claude | default (subscription) | 3 | 100% | 17.4 s | n/a (plan) |
| codex | default (subscription) | 3 | 100% | 17.8 s | n/a (plan) |
| grok | default (subscription) | 3 | 100% | 13.8 s | n/a (plan) |
| opencode | opencode/beam-free (free tier) | 3 | 0% | 2.7 s | free tier (daily limit hit) |
| opencode | vercel/deepseek-v4.1-flash | 3 | 100% | **2.4 s** | metered (cost not captured in eval rows) |

Probe caveat: 3 of 22 tasks — a directional signal, not the full
battery. The eval rows carry no per-task cost for metered rails; the
receipts join (plugin/adapter ledgers) is the next step before any
$/pass number is published for them. Full-matrix runs are the next
wave (22 tasks x 4 harnesses).

## Measured agentic (multi-step) matrix (2026-11-03, 7 tasks)

`fleet/agentic_eval.py` — fixtures placed in a throwaway workdir, one
prompt forcing a tool loop, graded on the **resulting workspace state**.
Tasks: file-chain, fix-test, count-files, csv-sum, json-edit, grep-fix,
log-report. $/task is the receipts window-join where measurable
(approximation, documented); unrecorded stays unrecorded.

| harness | model | policy | n | pass | avg latency | $/task |
|---|---|---|---|---|---|---|
| opencode | vercel/deepseek-v4.1-flash | auto | 10 | **100%** | **15.1 s** | **$0.081** |
| grok | default (plan) | acceptEdits | 11 | 91% | 25.4 s | plan |
| codex | default (plan) | danger-full-access | 7 | **100%** | 36.5 s | plan |
| codex | default (plan) | workspace-write | 10 | 70% | 58.7 s | plan |
| claude | default (plan) | acceptEdits | 10 | **100%** | 38.7 s | plan |

Findings:

- **All harnesses execute multi-step tool loops.** At this task level
  the differentiator is latency, reliability, and sandbox policy — not
  capability.
- **codex's workspace-write sandbox is the failure (not the model)**: it
  denied reads of the throwaway workspace inconsistently ("Permission
  denied", "couldn't find config.json"). The same 7 tasks with
  `danger-full-access`: **7/7**. Policy is recorded per row so mixed
  policy data can never be silently averaged.
- **grok is flaky on one task**: file-chain failed once ("I'll create…"
  with no tool action) and passed on retry — recorded as n=2, 91%
  overall, not rounded away.
- **Agentic metering is real**: opencode+DS-flash agent loops cost
  **$0.081/task** (context grows per step) vs $0.000138 for a
  single-shot battery task — ~600x. See COST-MODEL.md for what that
  means at volume.

## What the numbers already say

- **Quality**: all four harnesses solve the probe tasks at 100% when
  their model actually answers — the harness itself is not the quality
  bottleneck at this task level; the model is.
- **Speed**: opencode + DS-flash is **~7x faster** than the
  subscription CLIs on identical prompts (2.4 s vs 14-18 s average) —
  the harness's lean single-shot path plus a fast model. For
  latency-sensitive routing (scout, extraction, classification) that
  is the measured reason to prefer it.
- **Free tier reality**: opencode's free model hit its **daily usage
  limit** during measurement (0/3 with the limit error recorded).
  Honest state: the free rail is bursty, not guaranteed; the paid
  metered rail is the reliable one.
- **Cost honesty**: subscription rails report `n/a (plan)` — we do not
  invent a $/pass for flat-rate plans. Metered rails are labeled
  `metered (receipts)`: the eval row records no per-task cost, so no
  number is claimed until the receipts join lands.

## Security notes

- Harnesses run in a **throwaway working directory** — a tool-enabled
  agent cannot read the battery source, the ledger, or the expected
  answers from its cwd.
- The shared checkers **execute model output in-process** (the battery
  design: real code, real asserts, no LLM judge) — identical to the
  gateway battery. For untrusted rails, run the matrix in a throwaway
  checkout or sandbox.

## Per-harness notes (annotations, evidence-backed)

| harness | measured strength | caveat | config needed |
|---|---|---|---|
| claude | reliable 3/3, mature headless `-p` | slowest of the three at flat 100% | `claude` CLI + plan auth |
| codex | reliable 3/3, `exec` subcommand | similar latency to claude | `codex` CLI + plan auth |
| grok | 3/3, fastest subscription rail here (13.8 s) | `--single` flag shape (prompt is its value) | `grok` CLI + plan auth |
| opencode | **2.4 s measured** with DS-flash; native plugin receipts | free tier caps daily; needs paid model for reliability | `opencode` CLI + model config |

## Reproduce

```bash
python fleet/harness_matrix.py --probe                 # what is installed
python fleet/harness_matrix.py --all --tasks math,instruction,json-format
python fleet/harness_matrix.py --harness opencode \
  --model vercel/deepseek/deepseek-v4.1-flash --tasks math
python fleet/harness_matrix.py --report                # markdown table
```

Selftest (hermetic; never invokes a real CLI, runs with or without
harnesses installed): `python fleet/harness_matrix.py selftest` —
command shapes, checker mapping (pass/fail/error/raise),
missing-binary skip, empty-stderr exit honesty, cost labels, strict
argv refusal, pre-run task validation, cli_version capture.
