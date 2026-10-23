# starlight-ai-harness

A batteries-included AI harness template: model routing, spend control, eval
battery with deterministic checkers, and automatic receipts of every agent-loop
model call. OpenCode-native; no fork of the harness itself - a package of
assets that drive it.

## What's inside

- **routing.schema.json** - single source of truth: task classes, model chains
  and fallbacks, spend caps, eval battery def, receipts config
- **route_task.py** - classify a task, produce the model chain, record receipts
- **route_classifier.py / route_executor.py** - imported by the above
- **gateway_call.py** - gateway caller with 403-catch fallback walking
- **harness_eval.py** - 12-task battery (v2.1), deterministic checkers only
  (no LLM judging), `selftest`, `run-gateway`, `report`
- **report_dashboard.py** - self-contained styled HTML dashboard from the
  cross-harness report (`--check` self-grades the rendered surface)
- **PLAYBOOKS.md** - build-and-grade / measure-a-model / nightly-loop recipes
- **gen_omo_config.py** - generate the OpenCode plugin config from the schema
- **credit_watchdog.py / night_cap_check.py** - spend guardrails
- **weekly_routing_review.py** - receipts -> routing diff proposals
- **omo_bridge.py** - machine-grade an OmO `SUCCESS.md` bar: every criterion
  needs an author check plus a verifier distinct from the owner; `check:`
  tags must resolve to real battery checkers (`selftest` included)
- **oms/opencode-plugin/** - auto-captures one receipt per agent-loop model
  step (model, tokens, cost, latency, finish) via OpenCode events
- **oms/verify.py** - package release gate: run before trusting the install

## Quick start

Stdlib only - no dependencies. Python 3.11+ recommended.

```bash
python oms/verify.py                     # gate: files, schema<->battery, selftest
python fleet/route_task.py "review this PR"
python fleet/harness_eval.py selftest
```

Wire the auto-capture plugin in your global OpenCode config:

```jsonc
{ "plugins": ["<absolute path to this repo>/oms/opencode-plugin"] }
```

Set a receipt destination (defaults to `receipts/opencode.jsonl`):

```bash
export STARLIGHT_RECEIPTS_PATH=/your/dir/receipts.jsonl
```

## Design rules

- Deterministic evaluation: same battery for everything. Same rules for
  everything. If it didn't produce receipts or eval rows, it didn't happen.
- One source of truth (`routing.schema.json`): routing, caps, battery def.
- Model chains keep a gateway-reachable fallback before free-tier hops.
- Spend: per-key soft/hard caps, night-cap gate for unattended runs.

## Provenance

Exported from the private oh-my-starlight (OmS) package by `oms/export_template.py`,
which sanitizes machine specifics. See `SANITIZATION.md`.
