---
description: Three-state page-builder selftest, then regenerate all report pages from current data
subtask: true
model: opencode/glm-5.3-flash-free
---
Reports pipeline - output injected below; report on it.

Three-state selftest: !`python -B fleet/reports_selftest.py`
Cross-harness report: !`python -B fleet/cross_harness.py`
Pages: !`python -B fleet/report_dashboard.py && python -B fleet/build_showcase.py && python -B fleet/build_statuspage.py && python -B fleet/build_planpage.py`
Changed artifacts: !`git status --porcelain fleet/reports`

Rules:
- Report the selftest verdict (PASS n/n or FAIL with the failing state), then the changed pages.
- Do not commit. Do not edit page content by hand - pages render from data or not at all.