#!/usr/bin/env python3
"""Harness matrix: run the SAME deterministic battery through every
installed agent harness and record one eval row per (harness, task).

The measurement is the model+harness pair as configured: each CLI uses
its own auth and default model unless --model is passed. Checkers are
harness_eval.TASKS (no LLM judge); rows land in eval_results.jsonl with
harness=<name>. Cost is the harness's subscription/plan, so $/pass is
n/a for subscription rails - honest, unlike the metered gateway rail.

Usage:
  python fleet/harness_matrix.py --probe
  python fleet/harness_matrix.py --harness claude --tasks math,code
  python fleet/harness_matrix.py --all --tasks math,json-format
  python fleet/harness_matrix.py --report
  python fleet/harness_matrix.py selftest
"""
import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness_eval as he  # noqa: E402

BIN_CANDIDATES = {
    "opencode": ("opencode.cmd", "opencode"),
    "claude": ("claude.cmd", "claude"),
    "codex": ("codex.cmd", "codex"),
    "grok": ("grok.cmd", "grok"),
}


def resolve(name: str):
    """Path to the harness binary, or None when not installed here."""
    for cand in BIN_CANDIDATES.get(name, ()):
        path = shutil.which(cand)
        if path:
            return path
    return None


def build_cmd(name: str, binary: str, model: str, prompt: str) -> list[str]:
    """Headless invocation per harness. Model is optional: a CLI without
    --model uses its own default (its configured plan)."""
    if name == "opencode":
        return [binary, "run"] + (["-m", model] if model else []) + [prompt]
    if name == "claude":
        return [binary, "-p", prompt] + (["--model", model] if model else [])
    if name == "codex":
        return [binary, "exec"] + (["-m", model] if model else []) + [prompt]
    if name == "grok":
        return [binary, "--single", prompt] + (["-m", model] if model else [])
    raise KeyError(f"unknown harness: {name}")


def invoke(cmd: list[str], timeout: int,
           cwd: str | None = None) -> tuple[str, int, str]:
    """Run a harness command; returns (stdout, elapsed_ms, error).

    Runs in `cwd` (the caller passes a throwaway dir: a tool-enabled
    agent must not be able to read the battery source or the expected
    answers from its working directory). A nonzero exit ALWAYS yields a
    non-empty error, even when stderr is empty (a failed CLI whose
    stdout happens to satisfy the checker must never grade pass)."""
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except (subprocess.TimeoutExpired, OSError) as e:
        return "", int((time.time() - t0) * 1000), str(e)[:300]
    ms = int((time.time() - t0) * 1000)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        return proc.stdout or "", ms, err[:300]
    return proc.stdout or "", ms, ""


_VERSION_CACHE: dict[str, str] = {}


def _version(binary: str) -> str:
    """CLI version once per process: default-model rows must not mask a
    mid-experiment version change behind a synthetic 'name/default'."""
    if binary in _VERSION_CACHE:
        return _VERSION_CACHE[binary]
    try:
        p = subprocess.run([binary, "--version"], capture_output=True,
                           text=True, timeout=15)
        v = (p.stdout or p.stderr or "").strip().splitlines()[0][:80]
    except (subprocess.TimeoutExpired, OSError, IndexError):
        v = ""
    _VERSION_CACHE[binary] = v
    return v


def run_one(name: str, task: str, model: str, timeout: int,
            runner=invoke) -> dict:
    """One (harness, task) measurement; row shape matches the battery."""
    binary = resolve(name)
    spec = he.TASKS[task]
    if not binary:
        return {"missing": True, "harness": name, "task": task}
    cmd = build_cmd(name, binary, model, spec["prompt"])
    import shutil as _shutil
    import tempfile as _tempfile
    workdir = _tempfile.mkdtemp(prefix="hm-run-")
    try:
        out, ms, err = runner(cmd, timeout, workdir)
    finally:
        _shutil.rmtree(workdir, ignore_errors=True)
    # a checker that raises on malformed output records a FAILED row,
    # never aborts the matrix (harness_eval's gateway battery does the
    # same; e.g. a regex reply can raise OverflowError)
    try:
        ok = bool(err == "" and spec["check"](out))
    except Exception as e:  # noqa: BLE001 - checker robustness
        ok = False
        if not err:
            err = f"checker raised: {type(e).__name__}: {e}"[:300]
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model or f"{name}/default",
        "task": task,
        "ok": ok,
        "latency_ms": ms,
        "harness": name,
        "cli_version": _version(binary),
        "reply_sample": out[:300].strip(),
    }
    if err:
        rec["error"] = err
    return rec


def probe() -> int:
    found = 0
    for name in BIN_CANDIDATES:
        path = resolve(name)
        print(f"{name:9s} {'OK ' + path if path else 'NOT INSTALLED'}")
        found += bool(path)
    print(f"{found}/{len(BIN_CANDIDATES)} harnesses installed here")
    return 0


METERED_PREFIXES = ("vercel", "openai", "anthropic", "google")


def _cost_label(model: str) -> str:
    """Honest cost class per row: metered rails are not free, and eval
    rows do not carry per-task cost (receipt join is a next step)."""
    head = (model or "").split("/")[0]
    if head in METERED_PREFIXES:
        return "metered (receipts)"
    return "n/a (plan)"


def report() -> int:
    """Markdown matrix from the graded ledger (harness rows only).
    Task sets are shown per row: mixed task sets must be visible, not
    averaged into one misleading pass rate."""
    rows = [r for r in he.load_results()
            if r.get("harness") in BIN_CANDIDATES]
    if not rows:
        print("No harness-matrix rows in the ledger yet. Run the matrix.")
        return 0
    agg = {}
    for r in rows:
        key = (r.get("harness"), r.get("model"))
        a = agg.setdefault(key, {"n": 0, "ok": 0, "ms": [], "tasks": {}})
        a["n"] += 1
        a["ok"] += int(bool(r["ok"]))
        if r.get("latency_ms"):
            a["ms"].append(r["latency_ms"])
        a["tasks"][r.get("task")] = a["tasks"].get(r.get("task"), 0) + 1
    print("| harness | model | n | pass | avg latency | tasks | cost |")
    print("|---|---|---|---|---|---|---|")
    for (h, m), a in sorted(agg.items()):
        lat = f"{sum(a['ms']) // len(a['ms'])} ms" if a["ms"] else "n/a"
        tasks = ", ".join(f"{t} x{n}" for t, n in sorted(a["tasks"].items()))
        print(f"| {h} | {m} | {a['n']} | {a['ok'] / a['n']:.0%} | {lat} | "
              f"{tasks} | {_cost_label(m)} |")
    return 0


def selftest() -> int:
    """Deterministic: command shapes, checker mapping, missing-binary skip.
    No real harness is invoked."""
    fails = []
    for name in BIN_CANDIDATES:
        cmd = build_cmd(name, f"/bin/{name}", "some/model", "PROMPT")
        if "PROMPT" not in cmd:
            fails.append(f"{name}: prompt missing from command")
        if "some/model" in cmd and name == "opencode" and cmd[1] != "run":
            fails.append("opencode: expected 'run' subcommand")
    if resolve("does-not-exist") is not None:
        fails.append("resolve must return None for unknown harness")

    # checker mapping with a fake runner: pretend the harness exists so
    # this is hermetic on ANY machine (CI runners have no harness
    # binaries; the first CI run of this selftest caught exactly that)
    real_resolve = globals()["resolve"]
    globals()["resolve"] = lambda n: f"/fake/bin/{n}"
    try:
        good = run_one("claude", "math", "", 10,
                       runner=lambda c, t, w=None: ("24\n", 5, ""))
        bad = run_one("claude", "math", "", 10,
                      runner=lambda c, t, w=None: ("33\n", 5, ""))
        err = run_one("claude", "math", "", 10,
                      runner=lambda c, t, w=None: ("", 5, "boom"))
    finally:
        globals()["resolve"] = real_resolve
    if not good.get("ok"):
        fails.append("correct reply (24) must grade pass")
    if bad.get("ok"):
        fails.append("wrong reply (33) must grade fail")
    if err.get("ok"):
        fails.append("error reply must grade fail")
    for rec in (good, bad, err):
        for key in ("ts", "model", "task", "ok", "latency_ms", "harness"):
            if key not in rec:
                fails.append(f"row missing '{key}'")

    # missing binary -> explicit skip marker, never an invocation and
    # never a fake row. resolve is patched so a REAL harness is never
    # called from the selftest (hermetic).
    real_resolve = globals()["resolve"]
    globals()["resolve"] = lambda n: None
    try:
        skip = run_one("claude", "math", "", 5)
    finally:
        globals()["resolve"] = real_resolve
    if not skip.get("missing"):
        fails.append("missing binary must return a skip marker")

    # a checker that RAISES on malformed output must record a failed
    # row, not abort the matrix (review finding: regex reply ->
    # OverflowError used to kill the whole run)
    def raising_check(_out):
        raise OverflowError("malformed reply")
    he.TASKS["__raising__"] = {"prompt": "x", "check": raising_check}
    globals()["resolve"] = lambda n: f"/fake/bin/{n}"
    try:
        raised = run_one("claude", "__raising__", "", 5,
                         runner=lambda c, t, w=None: ("[A-Z][a-z]+-[0-9]{999}", 5, ""))
    finally:
        globals()["resolve"] = real_resolve
        he.TASKS.pop("__raising__", None)
    if raised.get("ok") or not raised.get("error", "").startswith("checker raised"):
        fails.append("raising checker must record a failed row, not abort")

    # nonzero exit with EMPTY stderr must still yield a non-empty error
    # (a failed CLI whose stdout satisfies the checker must not pass)
    out, ms, err = invoke([sys.executable, "-c", "import sys; sys.exit(7)"],
                          10, None)
    if err != "exit 7":
        fails.append(f"empty-stderr failure must yield 'exit 7' (got {err!r})")

    # cost class honesty: metered model prefixes are not '(plan)'
    if _cost_label("vercel/deepseek/deepseek-v4.1-flash") != "metered (receipts)":
        fails.append("metered model must not be labeled n/a (plan)")
    if _cost_label("claude/default") != "n/a (plan)":
        fails.append("subscription model must be labeled n/a (plan)")

    # cli_version is recorded (default-model rows must not mask version
    # changes behind a synthetic name)
    if "cli_version" not in good:
        fails.append("row must carry cli_version")

    # strict argv: a typo-shaped flag must REFUSE (argparse exit 2),
    # never fall through to real model calls
    real_argv = sys.argv[:]
    sys.argv = ["harness_matrix.py", "--all", "--reportt"]
    try:
        main()
        strict_rc = 0
    except SystemExit as e:
        strict_rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = real_argv
    if strict_rc != 2:
        fails.append(f"unknown flag must exit 2 (got {strict_rc})")

    # unknown task names must refuse BEFORE any invocation
    sys.argv = ["harness_matrix.py", "--harness", "claude",
                "--tasks", "math,typo"]
    try:
        bad_task_rc = main()
    finally:
        sys.argv = real_argv
    if bad_task_rc != 2:
        fails.append(f"unknown task must exit 2 before any run "
                     f"(got {bad_task_rc})")

    if fails:
        print("HARNESS MATRIX SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print("harness matrix selftest OK: command shapes, checker mapping "
          "(pass/fail/error/raise), missing-binary skip, empty-stderr exit, "
          "cost labels, strict argv, pre-run task validation, cli_version")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--harness", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--tasks", default="math")
    ap.add_argument("--timeout", type=int, default=180)
    # strict: an unknown or misspelled flag exits 2 (argparse) instead of
    # being silently ignored while a typo'd invocation spends on real
    # model calls (review finding: --all --reportt must never run)
    args = ap.parse_args()
    if args.probe:
        return probe()
    if args.report:
        return report()
    names = [h for h in BIN_CANDIDATES if args.all] if args.all else \
        ([args.harness] if args.harness else [])
    if not names:
        print("usage: --probe | --report | --harness <name> | --all "
              "| selftest", file=sys.stderr)
        return 2
    tasks = [t for t in args.tasks.split(",") if t]
    bad = [t for t in tasks if t not in he.TASKS]
    if bad:
        # validate BEFORE the first model call: a partial paid run from a
        # typo'd task list is exactly what this refuses
        print(f"unknown task(s): {', '.join(bad)} - valid: "
              f"{', '.join(sorted(he.TASKS))}", file=sys.stderr)
        return 2
    written = 0
    for name in names:
        if not resolve(name):
            print(f"{name}: NOT INSTALLED - skipped")
            continue
        for task in tasks:
            rec = run_one(name, task, args.model, args.timeout)
            if rec.get("missing"):
                print(f"{name}: NOT INSTALLED - skipped")
                continue
            he.append(rec)
            written += 1
            mark = "pass" if rec["ok"] else "fail"
            print(f"{name}/{task}: {mark} ({rec['latency_ms']} ms)")
            if rec.get("error"):
                print(f"  error: {rec['error'][:160]}")
    print(f"rows written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
