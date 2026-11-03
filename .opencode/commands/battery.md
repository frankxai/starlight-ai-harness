---
description: Run the deterministic battery rail on the free OpenCode model and report pass/fail per task
subtask: true
model: opencode/glm-5.3-flash-free
---
Run the battery rail and report:

!`python -B fleet/run_opencode.py --model opencode/glm-5.3-flash-free`

Rules:
- Report each task pass/fail with latency, exactly as printed. Do not round, do not summarize away failures.
- This is measurement, not repair. Do not fix anything, do not edit files.
- If the run exits non-zero or prints an error, report the error verbatim and stop.