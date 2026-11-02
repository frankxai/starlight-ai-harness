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

Usage: python nightly_eval.py          -> the full night (no argv = schtasks)
       python nightly_eval.py prep     -> refresh checkout, refuse stale
       python nightly_eval.py selftest -> stubbed rc-propagation checks
Any other argument REFUSES (exit 2) - never a fall-through into a real
unattended battery (the 2026-11-02 catch).

Exit codes (schtasks Last Run Result): measurement steps (gateway battery,
cross-harness report) are exit-fatal; a failing one exits 1. Content/cap
refusals exit with their own codes (prep 2, gate rc). The free opencode
rail and page builders never fail the night (logged to the transcript).
Transcript: fleet/reports/night/nightly-eval.log (tee'd, size-capped).

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


def prep() -> int:
    """Step 0: keep the nightly checkout current with reviewed main.

    The 2026-11-02 audit found the schtasks wire running a pinned checkout
    stuck on a consumed branch: the night would have measured an 18-check
    battery under the pre-DST-fix cap gate and written inconsistent rows.
    Fast-forward only - no rebase, no stash, never merging around foreign
    dirty state. A diverged or dirty-conflicting checkout REFUSES to run
    stale: the battery is skipped, the refusal is recorded, nothing spends.
    Already-current trees pass even when dirty (no-op merge)."""
    repo = str(HERE.parent)

    def git(*args: str) -> tuple[int, str]:
        p = subprocess.run(["git", "-C", repo, *args],
                           capture_output=True, text=True, timeout=300)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    rc, out = git("fetch", "origin")
    if rc != 0:
        print(f"[prep] fetch refused: {out.strip()}")
        return 1
    rc, _ = git("merge", "--ff-only", "origin/main")
    if rc != 0:
        print("[prep] cannot fast-forward to origin/main - refusing to run "
              "stale (merge manually or point the task at a fresh worktree)")
        return 2
    # a branch ahead of main carries LOCAL un-reviewed commits: reviewed
    # main is the tracked-content contract, so anything ahead refuses too
    rc, n_ahead = git("rev-list", "--count", "origin/main..HEAD")
    if n_ahead.strip() not in ("", "0"):
        print(f"[nightly] prep refuses: {n_ahead.strip()} local commit(s) "
              "ahead of origin/main - run main-tracking branches only")
        return 2
    print("[prep] at", git("rev-parse", "--short", "HEAD")[1].strip())
    return 0


def run_night() -> int:
    """The night's steps; returns the process exit code.

    Measurement steps (gateway battery, cross-harness report) are
    exit-fatal: their failure IS the night's failure, so schtasks
    Last Run Result turns non-zero. The free opencode rail and the
    page builders stay best-effort (doctrine: control-only bonus rail;
    pages render honest staleness per the reports selftest) - their
    exit codes are logged to the transcript, not fatal."""
    # 0. content freshness: never measure a stale battery
    rc = prep()
    if rc != 0:
        print(f"[nightly] prep rc={rc} - battery skipped entirely "
              "(stale content is the hazard the fix removes)")
        return 2

    # 1. night-cap gate (manual battery counts as planned spend of ~$0.01)
    rc = run(["fleet/night_cap_check.py", "--extra-planned-usd", "0.01"],
             "night-cap gate")
    if rc != 0:
        # fail-closed on BOTH "cap exceeded" and "gate tool broke": never
        # spend unattended when the cap itself cannot be verified. The
        # message stays truthful for either cause.
        print(f"night-cap gate did not pass (rc={rc}) - battery skipped "
              "entirely")
        return rc

    # 2. gateway rail (records usage+cost now)
    failed: list[str] = []
    rc = run(["fleet/harness_eval.py", "run-gateway", "--model",
              GATEWAY_MODEL], f"gateway battery {GATEWAY_MODEL}")
    if rc != 0:
        failed.append(f"gateway battery (rc={rc})")

    # 3. free opencode rail only with real headroom
    ram = free_ram()
    if ram >= RAM_MIN:
        rc = run(["fleet/run_opencode.py", "--model", OPENCODE_MODEL],
                 f"opencode battery {OPENCODE_MODEL}")
        if rc != 0:
            print(f"[nightly] opencode rail rc={rc} - free rail, logged "
                  "not fatal")
    else:
        print(f"[nightly] opencode rail SKIP: {ram / 2**30:.2f} GiB < 4 GiB "
              "free RAM (control-only)")

    # 4. comparison report (measurement: the pages' data input)
    rc = run(["fleet/cross_harness.py"], "cross-harness report")
    if rc != 0:
        failed.append(f"cross-harness report (rc={rc})")

    # 5. refresh the self-grading pages (best-effort: a page builder
    # failure never fails the run's measurement path)
    for builder, label in (
        (["fleet/report_dashboard.py"], "dashboard page"),
        (["fleet/build_showcase.py"], "showcase page"),
        (["fleet/build_statuspage.py"], "status page"),
        (["fleet/build_planpage.py"], "estate plan page"),
    ):
        run(builder, label)  # exit codes logged; nightly outcome is report + rows

    if failed:
        print(f"[nightly] measurement steps failed: {'; '.join(failed)}")
        return 1
    print("[nightly] all measurement steps passed")
    return 0


def selftest() -> int:
    """Prove rc propagation with every step stubbed (no subprocess, no
    spend, no writes): a failing measurement step fails the night; a
    clean night passes; prep/cap refusals return their own codes."""
    real_prep, real_ram, real_run = prep, free_ram, run

    def stub_night(prep_rc=0, cap_rc=0, gateway_rc=0, report_rc=0) -> int:
        def fake_run(args, label):
            if label == "night-cap gate":
                return cap_rc
            if "gateway battery" in label:
                return gateway_rc
            if "opencode battery" in label:
                return 0  # free rail: never fatal, stubbed pass
            if "report" in label:
                return report_rc
            return 0  # page builders: best-effort
        globals()["run"] = fake_run
        globals()["free_ram"] = lambda: RAM_MIN
        globals()["prep"] = lambda: prep_rc
        rc = run_night()
        globals()["prep"], globals()["free_ram"], globals()["run"] = \
            real_prep, real_ram, real_run
        return rc

    fails = []
    if stub_night(prep_rc=2) != 2:
        fails.append("prep refusal must return 2")
    if stub_night(cap_rc=3) != 3:
        fails.append("cap refusal must return the gate's rc")
    if stub_night(gateway_rc=1) != 1:
        fails.append("failing gateway battery must fail the night")
    if stub_night(report_rc=1) != 1:
        fails.append("failing report must fail the night")
    if stub_night() != 0:
        fails.append("clean night must pass (rc=0)")
    # argv contract: a typo-shaped invocation must REFUSE (exit 2), never
    # fall through into an unattended real battery. Real subprocess; the
    # child exits at the refusal before any step runs.
    p = subprocess.run([sys.executable, __file__, "bogus-arg"],
                       capture_output=True, text=True)
    if p.returncode != 2:
        fails.append(f"unknown argv must refuse with rc=2 "
                     f"(got rc={p.returncode})")

    if fails:
        print("NIGHTLY SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print("nightly selftest OK: rc propagation holds on all 5 cases; "
          "unknown argv refuses")
    return 0


def main() -> None:
    # argv contract: NO argument (or "") = the full night (schtasks wire);
    # "prep"/"selftest" = the standalone tools. Anything else REFUSES -
    # exit 2, never a fall-through into an unattended real battery. The
    # 2026-11-02 catch: `selftest` on pre-refusal code ran a REAL full
    # night (gateway spend) from a typo-shaped invocation.
    argv = sys.argv[1:]
    if argv == [] or argv == [""]:
        raise SystemExit(run_night())
    if argv[0] == "prep":
        # standalone boot (also the selftest of the refresh step)
        raise SystemExit(prep())
    if argv[0] == "selftest":
        raise SystemExit(selftest())
    print(f"[nightly] refusing unknown argument(s) {argv!r} - "
          "usage: nightly_eval.py [prep|selftest]", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    # schtasks gives this process no usable console: tee stdout+stderr to
    # the night transcript so the morning check has forensics even when
    # only the Last Run Result would otherwise survive.
    LOG = HERE / "reports" / "night" / "nightly-eval.log"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > 1_000_000:
        LOG.unlink()  # cap: one bounded file, never unbounded growth

    class _Tee:
        def __init__(self, out):
            self.out = out
        def write(self, s):
            self.out.write(s)
            _log.write(s)
            self.out.flush()
            _log.flush()
        def flush(self):
            self.out.flush()
            _log.flush()

    with LOG.open("a", encoding="utf-8") as _log:
        _old = sys.stdout, sys.stderr
        sys.stdout = _Tee(_old[0])  # type: ignore[assignment]
        sys.stderr = _Tee(_old[1])  # type: ignore[assignment]
        try:
            main()
        finally:
            sys.stdout, sys.stderr = _old
