# Playbooks

Operational recipes for this harness. Each ends in a verified artifact -
if it didn't produce receipts, eval rows, or a graded build, it didn't happen.

## 1. Build and grade a visible surface (page, dashboard, site)

*Before writing anything*, answer in one sentence each: who is this for, what
single action should it make obvious, what brand register does the repo
already carry, which existing components/tokens does it reuse, and how will
it be verified. Repo-local design rules always win over generic instincts.

1. **Reuse first.** Read the repo's tokens (`:root` CSS vars, Tailwind
   config), existing components, and layout primitives. A new primitive
   family is a last resort, not a starting move.
2. **Semantic-first build.** Structure (`header`/`main`/`nav`/`footer`,
   `<caption>` + `<th scope>` tables), real copy, crafted states
   (hover/focus/empty/error/loading), mobile order before desktop polish.
3. **Restraint contract.** One accent color, few type sizes, generous
   spacing, no decorative motion, no remote fetches - the surface must work
   offline, without JS/GPU, on a 4G phone.
4. **Grade deterministically.**
   - `python fleet/harness_eval.py selftest` (checker discipline)
   - `python fleet/harness_eval.py run-gateway --model <model> --tasks html-design`
     (modeled probe: doctype + lang + viewport + alt + label-for + button)
   - your artifact's own self-check (this harness's `report_dashboard.py
     --check` is the reference pattern: doctype, lang, viewport, landmarks,
     tokens, no external URL, no `<script>`)
5. **Browser QA** (desktop + 390px phone viewport, console clean) only when
   plenty of machine RAM is free - browser sessions are the heaviest step.
6. **Gate.** `python oms/verify.py` green before trusting the package; never
   publish from a run - publish stays a human action.

## 2. Measure a model before trusting it on real work

1. `python fleet/harness_eval.py run-gateway --model <model>` (full 12-task
   battery, priced rows via schema token pricing)
2. `python fleet/cross_harness.py` - compare $/pass and the fail matrix
   against every recorded rail
3. Read the fails: `reasoning_only=True` rows mean max_tokens truncation,
   not reasoning failure. One probe retry, then record the honest fail - do
   not hide red cells, they are the routing signal.
4. Scale n >= 10 per cell before trusting pass rates on a single run.
5. `python fleet/route_task.py "<representative task>"` - confirm the
   classifier sends this class of work to your measured chain.

## 3. Run the nightly loop safely

1. Wire the orchestrator, not raw battery args:
   `python fleet/nightly_eval.py` handles the night-cap gate, the gateway
   battery, receipts, and the cross-harness report in one pass.
2. Keep watchdogs on: `python fleet/credit_watchdog.py` hourly on spend.
3. The nightly orchestrator skips free OpenCode rails under a RAM floor -
   unattended runs must never baseline against a thrashing machine.
