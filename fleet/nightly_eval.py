#!/usr/bin/env python3
r"""Nightly battery orchestrator (successor of the direct schtasks Arguments).

Order (each step logs one line):
  1. night-cap gate: refuse below cap? (routing.schema.json night_cap_usd)
  2. gateway rail: DS-flash full battery (schema-priced rows)
  3. opencode rail: free plugin-captured battery - ONLY if free RAM >= 4 GiB
     (control-only doctrine: never let an unattended battery push the
     machine down; skip line is recorded, nothing fails)
  4. cross_harness report regenerate
  5. page refresh (dashboard / showcase / status / estate plan), best-effort

Wire (GATE: mutation of a registered scheduled task, one command):
  schtasks /change /tn "starlight-nightly-eval" /st 03:30
  /tr "\"<PythonCallable>\" \"<RepoRoot>\fleet\nightly_eval.py\""
Where the current task already uses the same python.exe; only the Arguments
swap from harness_eval run-gateway to this orchestrator (paths stay machine-local
in the operator config, never in source).
Credentials: vck_ key read from ~/.config/opencode/opencode.json as before.
"""
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

GATEWAY_MODEL = "vercel/deepseek/deepseek-v4.1-flash"
OPENCODE_MODEL = "opencode/glm-5.3-flash-free"
RAM_MIN = 4 * 1024 * 1024 * 1024  # 4 GiB floor, control-only doctrine


def free_ram() -> int:
    class RAM(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = RAM(); st.dwLength = ctypes.sizeof(RAM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPhys


def run(args: list[str], label: str) -> int:
    p = subprocess.run([sys.executable] + args, cwd=str(HERE.parent))
    print(f"[nightly] {label}: exit {p.returncode}")
    return p.returncode


def main() -> None:
    # 1. night-cap gate (manual battery counts as planned spend of ~$0.01)
    rc = run(["fleet/night_cap_check.py", "--extra-planned-usd", "0.01"],
             "night-cap gate")
    if rc != 0:
        sys.exit("night cap exceeded - battery skipped entirely")

    # 2. gateway rail (records usage+cost now)
    run(["fleet/harness_eval.py", "run-gateway", "--model", GATEWAY_MODEL],
        f"gateway battery {GATEWAY_MODEL}")

    # 3. free opencode rail only with real headroom
    ram = free_ram()
    if ram >= RAM_MIN:
        run(["fleet/run_opencode.py", "--model", OPENCODE_MODEL],
            f"opencode battery {OPENCODE_MODEL}")
    else:
        print(f"[nightly] opencode rail SKIP: {ram / 2**30:.2f} GiB < 4 GiB "
              "free RAM (control-only)")

    # 4. comparison report, then refresh the self-grading pages (best-effort:
    # a page builder failure never fails the run's measurement path)
    run(["fleet/cross_harness.py"], "cross-harness report")
    for builder, label in (
        (["fleet/report_dashboard.py"], "dashboard page"),
        (["fleet/build_showcase.py"], "showcase page"),
        (["fleet/build_statuspage.py"], "status page"),
        (["fleet/build_planpage.py"], "estate plan page"),
    ):
        run(builder, label)  # exit codes logged; nightly outcome is report + rows


if __name__ == "__main__":
    main()
