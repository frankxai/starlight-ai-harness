---
description: Dry-run the nightly gates - prep, cap check, all fleet selftests; report readiness without spending
subtask: true
model: opencode/glm-5.3-flash-free
---
Nightly dry-run - no battery, no spend. Each gate's output is injected below; report on it.

Prep gate: !`python -B fleet/nightly_eval.py prep`
Cap gate: !`python -B fleet/night_cap_check.py --extra-planned-usd 0.01`
Fleet selftests: !`python -B fleet/harness_eval.py selftest && python -B fleet/night_cap_check.py selftest && python -B fleet/weekly_routing_review.py selftest && python -B fleet/route_classifier.py selftest && python -B fleet/nightly_eval.py selftest && python -B fleet/reports_selftest.py`

Rules:
- Report each gate pass/fail with its last output line.
- Flag anything that would fail tonight 03:30, by failure class (silent fade, fall-through, stale output).
- Do not fix anything. Report only.