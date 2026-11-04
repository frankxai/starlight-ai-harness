#!/usr/bin/env python3
r"""Nightly battery orchestrator (successor of the direct schtasks Arguments).

Order (each step logs one line):
  1. night-cap gate: refuse below cap? (routing.schema.json night_cap_usd)
  2. gateway rail: DS-flash full battery (schema-priced rows, parallel
     at concurrency 3 - the measured sweet spot)
  3. opencode rail: free plugin-captured battery - ONLY if free RAM >= 4 GiB
     (control-only doctrine: never let an unattended battery push the
     machine down; skip line is recorded, nothing fails)
  4. cross_harness report regenerate
  5. page refresh (dashboard / showcase / status / estate plan), best-effort

Usage: python nightly_eval.py          -> the full night (no argv = schtasks)
       python nightly_eval.py prep     -> refresh checkout, refuse stale
       python nightly_eval.py selftest -> stubbed rc-propagation checks
       python nightly_eval.py --gateway-only -> cloud executor (paid rail)
Any other argument REFUSES (exit 2) - never a fall-through into a real
unattended battery (the 2026-11-02 catch).

Exit codes (schtasks Last Run Result): measurement steps (gateway battery,
cross-harness report) are exit-fatal; a failing one exits 1. Preflight (a
gate selftest failing) exits 3 before any spend. Content/cap refusals exit
with their own codes (prep 2, gate rc). The free opencode rail and page
builders never fail the night (logged to the transcript).
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
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
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


def archive_night():
    """Copy the night's measured artifacts OUTSIDE the runtime worktree.

    The nightly runs in a disposable checkout: if that worktree is wiped,
    the night's rows and pages vanish with it. This local archive makes
    them survive (rows, cross-harness report, pages, transcript). The
    shared cloud sink is decision D3; local archive needs no gate.
    Best-effort by design: an archive failure never fails the night."""
    env = os.environ.get("STARLIGHT_NIGHT_ARCHIVE")
    root = Path(env) if env else Path.home() / ".starlight" / "harness" / "nightly"
    dst = root / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src = HERE.parent
    try:
        dst.mkdir(parents=True, exist_ok=True)
        files = [
            src / "fleet" / "evals" / "eval_results.jsonl",
            src / "fleet" / "reports" / "cross-harness-latest.json",
            src / "fleet" / "reports" / "cross-harness-latest.md",
            src / "fleet" / "reports" / "night" / "nightly-eval.log",
        ]
        files += sorted((src / "fleet" / "reports").glob("*.html"))
        copied = 0
        for f in files:
            if f.exists():
                shutil.copy2(f, dst / f.name)
                copied += 1
        print(f"[nightly] archived {copied} artifacts -> {dst}")
        return dst
    except OSError as e:
        print(f"[nightly] archive skipped: {e}")
        return None


def run_night(gateway_only: bool = False) -> int:
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

    # 0b. fail-closed preflight: every gate's selftest must pass BEFORE the
    # night spends anything. A gate that cannot verify itself must not let
    # the battery proceed (the 2026-11-03 finding: last night's run measured
    # an 18-task battery on stale code and still exited 0 - nothing failed
    # because nothing could). Deterministic checks only, ~4s, no spend.
    for selftest, label in (
        (["fleet/harness_eval.py", "selftest"], "battery checker selftest"),
        (["fleet/night_cap_check.py", "selftest"], "cap gate selftest"),
        (["fleet/weekly_routing_review.py", "selftest"], "review selftest"),
        (["fleet/route_classifier.py", "selftest"], "classifier selftest"),
        (["fleet/nightly_eval.py", "selftest"], "orchestrator selftest"),
        (["fleet/agents_selftest.py"], "corps contract selftest"),
        (["fleet/reports_selftest.py"], "page builder selftest"),
    ):
        if not (HERE.parent / selftest[0]).exists():
            print(f"[nightly] preflight SKIP: {selftest[0]} not present "
                  "(install shape) - logged, never silent")
            continue
        rc = run(selftest, label)
        if rc != 0:
            print(f"[nightly] preflight FAIL: {label} rc={rc} - battery "
                  "skipped (a gate that cannot verify itself must not spend)")
            return 3

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

    # 2. gateway rail (records usage+cost now). Concurrency 3 is the
    # MEASURED sweet spot for this key/provider (22-task battery:
    # 44.8s -> 10.9s; c=6 overloads). See docs/VELOCITY.md.
    failed: list[str] = []
    rc = run(["fleet/harness_eval.py", "run-gateway", "--model",
              GATEWAY_MODEL, "--concurrency", "3"],
             f"gateway battery {GATEWAY_MODEL}")
    if rc != 0:
        failed.append(f"gateway battery (rc={rc})")

    # 3. free opencode rail only with real headroom
    ram = free_ram()
    if gateway_only:
        print("[nightly] opencode rail SKIP: --gateway-only (cloud executor "
              "runs the paid rail; the free rail is a local bonus)")
    elif ram >= RAM_MIN:
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

    # 5b. archive outside the runtime worktree (best-effort): rows,
    # report, pages and transcript survive a worktree wipe; the shared
    # cloud sink is decision D3
    archive_night()

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

    def stub_night(prep_rc=0, cap_rc=0, gateway_rc=0, report_rc=0,
                   preflight_rc=0) -> int:
        def fake_run(args, label):
            if label == "night-cap gate":
                return cap_rc
            if label.endswith("selftest"):
                return preflight_rc
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
    if stub_night(preflight_rc=1) != 3:
        fails.append("a failing gate selftest must return 3 (no spend)")
    if stub_night(cap_rc=3) != 3:
        fails.append("cap refusal must return the gate's rc")
    if stub_night(gateway_rc=1) != 1:
        fails.append("failing gateway battery must fail the night")
    if stub_night(report_rc=1) != 1:
        fails.append("failing report must fail the night")
    if stub_night() != 0:
        fails.append("clean night must pass (rc=0)")

    # --gateway-only: the cloud executor shape must never touch the free
    # rail (no opencode binary there; a missing binary would write
    # dishonest red rows through run_opencode's OSError path)
    seen = []
    real_run = globals()["run"]
    real_prep = globals()["prep"]
    real_ram = globals()["free_ram"]

    def recording_run(args, label):
        seen.append(label)
        return 0

    globals()["run"] = recording_run
    globals()["prep"] = lambda: 0
    globals()["free_ram"] = lambda: RAM_MIN
    try:
        rc_gw = run_night(gateway_only=True)
    finally:
        globals()["run"], globals()["prep"], globals()["free_ram"] = \
            real_run, real_prep, real_ram
    if rc_gw != 0:
        fails.append("gateway-only clean night must pass")
    if any("opencode battery" in label for label in seen):
        fails.append("--gateway-only must skip the free opencode rail")
    # argv contract: a typo-shaped invocation must REFUSE (exit 2), never
    # fall through into an unattended real battery. Real subprocess; the
    # child exits at the refusal before any step runs.
    p = subprocess.run([sys.executable, __file__, "bogus-arg"],
                       capture_output=True, text=True)
    if p.returncode != 2:
        fails.append(f"unknown argv must refuse with rc=2 "
                     f"(got rc={p.returncode})")

    # night archive: artifacts land OUTSIDE the tree (survive worktree
    # wipe). Asserts the destination, not a specific artifact: public
    # installs ship no ledger (runtime data), so zero-artifact archives
    # are legitimate there.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        real_env = os.environ.get("STARLIGHT_NIGHT_ARCHIVE")
        os.environ["STARLIGHT_NIGHT_ARCHIVE"] = td
        try:
            dst = archive_night()
            ok = dst is not None and Path(dst).exists()
        finally:
            if real_env is None:
                os.environ.pop("STARLIGHT_NIGHT_ARCHIVE", None)
            else:
                os.environ["STARLIGHT_NIGHT_ARCHIVE"] = real_env
    if not ok:
        fails.append("night archive must create the dated destination "
                     "outside the tree")

    if fails:
        print("NIGHTLY SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print("nightly selftest OK: rc propagation holds on all 5 cases; "
          "preflight fail-closed; gateway-only skips the free rail; "
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
    if argv[0] == "--gateway-only":
        # cloud executor shape (GitHub Actions): paid rail only, no
        # local opencode binary, no RAM dependency
        raise SystemExit(run_night(gateway_only=True))
    print(f"[nightly] refusing unknown argument(s) {argv!r} - "
          "usage: nightly_eval.py [prep|selftest|--gateway-only]",
          file=sys.stderr)
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
