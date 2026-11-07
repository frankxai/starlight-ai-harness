#!/usr/bin/env python3
"""Agentic (multi-step) battery: tasks that require tool loops and
workspace state, not a single answer.

Each task places fixtures in a throwaway directory, sends ONE prompt
that forces multiple internal steps (create -> read -> compute ->
write / run -> fix -> verify), then grades the RESULTING WORKSPACE
state (and stdout). Deterministic graders only; no LLM judge.

Harnesses run with their auto-approve/permission flag so the tool loop
can actually execute; each CLI gets its own shape (claude -p +
--permission-mode acceptEdits, codex exec -s workspace-write, grok
--single + acceptEdits, opencode run --auto).

Usage:
  python fleet/agentic_eval.py --probe
  python fleet/agentic_eval.py --harness opencode --model vercel/deepseek/deepseek-v4.1-flash
  python fleet/agentic_eval.py --all
  python fleet/agentic_eval.py --report
  python fleet/agentic_eval.py selftest
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness_eval as he  # noqa: E402
from harness_matrix import BIN_CANDIDATES, extract, resolve  # noqa: E402

MULTI_FLAGS = {
    "claude": ["--permission-mode", "acceptEdits"],
    "codex": ["-s", os.environ.get("CODEX_SANDBOX", "workspace-write"),
              "--skip-git-repo-check"],
    "grok": ["--permission-mode", "acceptEdits"],
    "opencode": ["--auto"],
}


def _fixture_plain(_wd: Path) -> None:
    return None


def _fixture_buggy(wd: Path) -> None:
    (wd / "buggy.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8")
    (wd / "test_add.py").write_text(
        "from buggy import add\nassert add(2, 3) == 5\nprint('PASS')\n",
        encoding="utf-8")


def _fixture_files(wd: Path) -> None:
    data = wd / "data"
    (data / "inner").mkdir(parents=True)
    (data / "a.txt").write_text("x", encoding="utf-8")
    (data / "b.md").write_text("x", encoding="utf-8")
    (data / "inner" / "c.txt").write_text("x", encoding="utf-8")
    (data / "inner" / "d.txt").write_text("x", encoding="utf-8")


def _fixture_csv(wd: Path) -> None:
    (wd / "sales.csv").write_text(
        "id,region,amount\n1,EU,10.00\n2,US,5.00\n3,EU,16.50\n",
        encoding="utf-8")


def _fixture_json(wd: Path) -> None:
    (wd / "config.json").write_text(
        '{"name": "app", "debug": false, "ports": [80, 443]}',
        encoding="utf-8")


def _fixture_grep_bug(wd: Path) -> None:
    (wd / "calc.py").write_text(
        "def area(r):\n    return 2 * 3.14\n", encoding="utf-8")
    (wd / "test_area.py").write_text(
        "from calc import area\nassert abs(area(2) - 12.56) < 0.01\n"
        "print('PASS')\n", encoding="utf-8")


def _fixture_logs(wd: Path) -> None:
    (wd / "a.log").write_text(
        "INFO ok\nERROR disk\nINFO x\nERROR net\n", encoding="utf-8")
    (wd / "b.log").write_text("ERROR time\nINFO ok\n", encoding="utf-8")
    (wd / "c.log").write_text("INFO only\n", encoding="utf-8")


def _grade_file_chain(wd: Path, _out: str) -> bool:
    f = wd / "notes.txt"
    if not f.exists():
        return False
    lines = [ln.strip() for ln in
             f.read_text(encoding="utf-8-sig").splitlines()]
    return lines == ["alpha", "1"]


def _grade_fix_test(wd: Path, _out: str) -> bool:
    if not (wd / "buggy.py").exists() or not (wd / "test_add.py").exists():
        return False
    proc = subprocess.run([sys.executable, "test_add.py"], cwd=str(wd),
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0 and "PASS" in proc.stdout


def _grade_count_files(wd: Path, _out: str) -> bool:
    f = wd / "answer.txt"
    if not f.exists():
        return False
    return f.read_text(encoding="utf-8-sig").strip() == "3"


def _grade_csv(wd: Path, _out: str) -> bool:
    f = wd / "total.txt"
    if not f.exists():
        return False
    return f.read_text(encoding="utf-8-sig").strip() == "26.50"


def _grade_json(wd: Path, _out: str) -> bool:
    import json as _json
    f = wd / "config.json"
    if not f.exists():
        return False
    try:
        doc = _json.loads(f.read_text(encoding="utf-8-sig"))
    except ValueError:
        return False
    return doc == {"name": "app", "debug": True, "ports": [80]}


def _grade_grep_fix(wd: Path, _out: str) -> bool:
    if not (wd / "calc.py").exists() or not (wd / "test_area.py").exists():
        return False
    proc = subprocess.run([sys.executable, "test_area.py"], cwd=str(wd),
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0 and "PASS" in proc.stdout


def _grade_logs(wd: Path, _out: str) -> bool:
    f = wd / "report.txt"
    if not f.exists():
        return False
    return f.read_text(encoding="utf-8-sig").strip() == "errors: 3"


MULTI_TASKS = {
    "ms-file-chain": {
        "prompt": ("Work step by step in the current directory. Create a file "
                   "notes.txt whose first line is exactly 'alpha'. Then append "
                   "a second line: the 1-based alphabet position of the first "
                   "letter of the first line. Verify the file by reading it "
                   "back, then print its final contents."),
        "fixture": _fixture_plain,
        "grade": _grade_file_chain,
    },
    "ms-fix-test": {
        "prompt": ("Work step by step in the current directory. Run "
                   "`python test_add.py` and observe the failure. Fix "
                   "buggy.py so the test passes. Re-run the test to prove it, "
                   "then print PASS."),
        "fixture": _fixture_buggy,
        "grade": _grade_fix_test,
    },
    "ms-count-files": {
        "prompt": ("Work step by step in the current directory. Count the "
                   ".txt files under data/ recursively. Write ONLY the number "
                   "to answer.txt, then print the number."),
        "fixture": _fixture_files,
        "grade": _grade_count_files,
    },
    "ms-csv-sum": {
        "prompt": ("Work step by step in the current directory. In sales.csv, "
                   "sum the amount column for rows where region is EU. Write "
                   "the total with two decimals (e.g. 12.34) to total.txt, "
                   "then print it."),
        "fixture": _fixture_csv,
        "grade": _grade_csv,
    },
    "ms-json-edit": {
        "prompt": ("Work step by step in the current directory. Edit "
                   "config.json: set debug to true and remove 443 from the "
                   "ports list (keep the other values intact). Write the file "
                   "back as valid JSON, then print it."),
        "fixture": _fixture_json,
        "grade": _grade_json,
    },
    "ms-grep-fix": {
        "prompt": ("Work step by step in the current directory. Run "
                   "`python test_area.py` and observe the failure. Find the "
                   "bug in calc.py (the circle-area formula is wrong), fix "
                   "it, re-run the test to prove it, then print PASS."),
        "fixture": _fixture_grep_bug,
        "grade": _grade_grep_fix,
    },
    "ms-log-report": {
        "prompt": ("Work step by step in the current directory. Count all "
                   "lines containing ERROR across a.log, b.log and c.log. "
                   "Write exactly 'errors: <N>' to report.txt, then print "
                   "it."),
        "fixture": _fixture_logs,
        "grade": _grade_logs,
    },
}


def build_cmd(name: str, binary: str, model: str, prompt: str) -> list[str]:
    flags = MULTI_FLAGS[name]
    if name == "claude":
        return [binary, "-p", prompt, "--output-format", "json", *flags]
    if name == "codex":
        return ([binary, "exec", "--json", *flags]
                + (["-m", model] if model else []) + [prompt])
    if name == "grok":
        return [binary, *flags, "--single", prompt] \
            + (["-m", model] if model else [])
    if name == "opencode":
        return [binary, "run", *flags] + (["-m", model] if model else []) \
            + [prompt]
    raise KeyError(name)


def _receipts_path():
    """Where receipt rows live on this machine. Env override first (a
    harness install may write elsewhere); default is the fleet receipts
    dir. Absent = no cost join is attempted (honest 'unrecorded')."""
    env = os.environ.get("STARLIGHT_RECEIPTS_READ")
    cands = [Path(env)] if env else []
    cands.append(HERE.parent / "receipts" / "opencode.jsonl")
    for p in cands:
        if p.exists():
            return p
    return None


def _join_cost(model: str, t0: float, t1: float):
    """Sum receipt costs in the run's time window for the same model.

    Window join is an approximation (plugins timestamp per step, the
    eval row per task) - documented as such; a model that matches no
    receipt leaves cost unrecorded rather than inventing a number."""
    path = _receipts_path()
    if not path or not model:
        return None
    tail = model.rsplit("/", 1)[-1]
    total, found = 0.0, False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            ts = r.get("ts")
            if not ts:
                continue
            try:
                t = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            if not (t0 - 2 <= t <= t1 + 5):
                continue
            mid = r.get("modelID") or ""
            if mid and (mid == tail or mid.endswith("/" + tail)
                        or tail.endswith(mid)):
                found = True
                total += float(r.get("cost_usd") or 0)
    except (OSError, ValueError):
        return None
    return round(total, 6) if found else None


def run_task(name: str, task: str, model: str, timeout: int,
             runner=None) -> dict:
    binary = resolve(name)
    spec = MULTI_TASKS[task]
    if not binary:
        return {"missing": True, "harness": name, "task": task}
    workdir = Path(tempfile.mkdtemp(prefix="ms-run-"))
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model or f"{name}/default",
        "task": task,
        "harness": name,
        "mode": "agentic",
        "ok": False,
    }
    if name == "codex":
        rec["policy"] = os.environ.get("CODEX_SANDBOX", "workspace-write")
    try:
        spec["fixture"](workdir)
        cmd = build_cmd(name, binary, model, spec["prompt"])
        t0 = time.time()
        out, err = "", ""
        if runner is None:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout, cwd=str(workdir))
                out = proc.stdout or ""
                if proc.returncode != 0:
                    err = (proc.stderr or "").strip() \
                        or f"exit {proc.returncode}"
            except (subprocess.TimeoutExpired, OSError) as e:
                err = str(e)[:300]
        else:
            out, err = runner(cmd, timeout, str(workdir))
        rec["latency_ms"] = int((time.time() - t0) * 1000)
        # native usage (claude/codex JSON): a capped rail reports its
        # API error honestly instead of an empty-reply mystery
        text, nat = extract(name, out)
        if nat and nat.get("is_error"):
            native = str(nat.get("api_error") or "harness error")[:250]
            err = (err + "; " if err else "") + f"native api_error: {native}"
        if nat:
            usage = {k: v for k, v in nat.items()
                     if k not in ("is_error", "api_error") and v is not None}
            if usage:
                rec["usage"] = usage
            if nat.get("total_cost_usd") is not None:
                rec["cost_usd"] = nat["total_cost_usd"]
        try:
            ok = err == "" and bool(spec["grade"](workdir, text))
        except Exception as e:  # noqa: BLE001 - grader robustness
            ok = False
            if not err:
                err = f"grader raised: {type(e).__name__}: {e}"[:300]
        rec["ok"] = ok
        cost = _join_cost(model or f"{name}/default", t0, time.time())
        if cost is not None and "cost_usd" not in rec:
            rec["cost_usd"] = cost
        # never record absolute machine paths: replies may link their
        # temp files; the sample becomes public-grade text only
        import re as _re
        sample = text.replace(str(workdir), "<workdir>")
        sample = _re.sub(r"[A-Za-z]:[/\\]+Users[/\\]+[^\s\"')]+",
                         "<path>", sample)
        rec["reply_sample"] = sample[:300].strip()
        if err:
            rec["error"] = err
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return rec


def probe() -> int:
    found = 0
    for name in BIN_CANDIDATES:
        path = resolve(name)
        print(f"{name:9s} {'OK ' + path if path else 'NOT INSTALLED'}")
        found += bool(path)
    print(f"{found}/{len(BIN_CANDIDATES)} harnesses installed here")
    return 0


def report() -> int:
    rows = [r for r in he.load_results() if r.get("mode") == "agentic"]
    if not rows:
        print("No agentic rows in the ledger yet. Run the battery.")
        return 0
    agg = {}
    for r in rows:
        pol = r.get("policy") or ("workspace-write"
                                  if r.get("harness") == "codex" else "-")
        key = (r.get("harness"), r.get("model"), pol)
        a = agg.setdefault(key, {"n": 0, "ok": 0, "ms": [],
                                 "cost": 0.0, "cost_n": 0})
        a["n"] += 1
        a["ok"] += int(bool(r["ok"]))
        if r.get("latency_ms"):
            a["ms"].append(r["latency_ms"])
        if r.get("cost_usd") is not None:
            a["cost"] += float(r["cost_usd"])
            a["cost_n"] += 1
    print("| harness | model | policy | n | pass | avg latency | $/task |")
    print("|---|---|---|---|---|---|---|")
    for (h, m, pol), a in sorted(agg.items()):
        lat = f"{sum(a['ms']) // len(a['ms'])} ms" if a["ms"] else "n/a"
        cost = (f"${a['cost'] / a['cost_n']:.6f}"
                if a["cost_n"] else "unrecorded")
        print(f"| {h} | {m} | {pol} | {a['n']} | {a['ok'] / a['n']:.0%} | "
              f"{lat} | {cost} |")
    return 0


def selftest() -> int:
    """Hermetic: no real CLI is invoked; fixtures and graders are
    exercised directly, and the command shapes are asserted."""
    fails = []
    real_resolve = globals()["resolve"]
    globals()["resolve"] = lambda n: f"/fake/bin/{n}"
    try:
        # a runner that performs the RIGHT workspace changes passes each
        # grader; a runner that does nothing fails it
        def good_chain(_c, _t, wd):
            Path(wd, "notes.txt").write_text("alpha\n1\n", encoding="utf-8")
            return "alpha\n1", ""

        def good_fix(_c, _t, wd):
            Path(wd, "buggy.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8")
            return "PASS", ""

        def good_count(_c, _t, wd):
            Path(wd, "answer.txt").write_text("3", encoding="utf-8")
            return "3", ""

        def good_csv(_c, _t, wd):
            Path(wd, "total.txt").write_text("26.50", encoding="utf-8")
            return "26.50", ""

        def good_json(_c, _t, wd):
            Path(wd, "config.json").write_text(
                '{"name": "app", "debug": true, "ports": [80]}',
                encoding="utf-8")
            return "{}", ""

        def good_grep(_c, _t, wd):
            Path(wd, "calc.py").write_text(
                "def area(r):\n    return 3.14 * r * r\n", encoding="utf-8")
            return "PASS", ""

        def good_logs(_c, _t, wd):
            Path(wd, "report.txt").write_text("errors: 3", encoding="utf-8")
            return "errors: 3", ""

        def noop(_c, _t, _wd):
            return "", ""

        cases = (
            ("ms-file-chain", good_chain), ("ms-fix-test", good_fix),
            ("ms-count-files", good_count), ("ms-csv-sum", good_csv),
            ("ms-json-edit", good_json), ("ms-grep-fix", good_grep),
            ("ms-log-report", good_logs),
        )
        for task, runner in cases:
            rec = run_task("claude", task, "", 30, runner=runner)
            if not rec.get("ok"):
                fails.append(f"{task}: right workspace must grade pass")
            rec_bad = run_task("claude", task, "", 30, runner=noop)
            if rec_bad.get("ok"):
                fails.append(f"{task}: untouched workspace must grade fail")
            if "latency_ms" not in rec or "mode" not in rec:
                fails.append(f"{task}: row shape incomplete")

        # harness flags present in every shape
        for name in BIN_CANDIDATES:
            cmd = build_cmd(name, f"/bin/{name}", "", "PROMPT")
            if "PROMPT" not in cmd:
                fails.append(f"{name}: prompt missing from command")
            if not any(f in cmd for f in MULTI_FLAGS[name]):
                fails.append(f"{name}: permission flag missing")

        # missing binary -> skip marker
        globals()["resolve"] = lambda n: None
        skip = run_task("claude", "ms-file-chain", "", 5)
        if not skip.get("missing"):
            fails.append("missing binary must return a skip marker")
    finally:
        globals()["resolve"] = real_resolve

    # grader that raises -> failed row, never an abort
    def raising_grade(_wd, _out):
        raise RuntimeError("bad state")
    MULTI_TASKS["__raising__"] = {"prompt": "x",
                                  "fixture": _fixture_plain,
                                  "grade": raising_grade}
    globals()["resolve"] = lambda n: f"/fake/bin/{n}"
    try:
        rec = run_task("claude", "__raising__", "", 5,
                       runner=lambda c, t, w: ("", ""))
    finally:
        globals()["resolve"] = real_resolve
        MULTI_TASKS.pop("__raising__", None)
    if rec.get("ok") or not rec.get("error", "").startswith("grader raised"):
        fails.append("raising grader must record a failed row, not abort")

    # receipts cost join: window+model match sums the steps; a model with
    # no matching receipt stays 'unrecorded' (never an invented number)
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        rp = Path(td) / "receipts.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        rp.write_text(
            json.dumps({"ts": now, "modelID": "deepseek-v4.1-flash",
                        "cost_usd": 0.0002}) + "\n"
            + json.dumps({"ts": now, "modelID": "other-model",
                          "cost_usd": 9.99}) + "\n",
            encoding="utf-8")
        real_env = os.environ.get("STARLIGHT_RECEIPTS_READ")
        os.environ["STARLIGHT_RECEIPTS_READ"] = str(rp)
        try:
            import time as _time
            t = _time.time()
            got = _join_cost("vercel/deepseek/deepseek-v4.1-flash", t, t)
            none = _join_cost("claude/default", t, t)
        finally:
            if real_env is None:
                os.environ.pop("STARLIGHT_RECEIPTS_READ", None)
            else:
                os.environ["STARLIGHT_RECEIPTS_READ"] = real_env
    if got != 0.0002:
        fails.append(f"cost join must sum matching model receipts (got {got})")
    if none is not None:
        fails.append("unmatched model must stay unrecorded (got a number)")

    if fails:
        print("AGENTIC EVAL SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print("agentic eval selftest OK: 7 workspace graders (right/untouched/"
          "raising), permission flags, missing-binary skip, cost join "
          "(window+model, unrecorded stays unrecorded)")
    return 0


def main() -> int:
    import argparse
    argv = sys.argv[1:]
    if argv == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--harness", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--tasks", default=",".join(MULTI_TASKS))
    ap.add_argument("--timeout", type=int, default=300)
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
    bad = [t for t in tasks if t not in MULTI_TASKS]
    if bad:
        print(f"unknown task(s): {', '.join(bad)} - valid: "
              f"{', '.join(sorted(MULTI_TASKS))}", file=sys.stderr)
        return 2
    written = 0
    for name in names:
        if not resolve(name):
            print(f"{name}: NOT INSTALLED - skipped")
            continue
        for task in tasks:
            rec = run_task(name, task, args.model, args.timeout)
            if rec.get("missing"):
                print(f"{name}: NOT INSTALLED - skipped")
                continue
            he.append(rec)
            written += 1
            mark = "pass" if rec["ok"] else "fail"
            print(f"{name}/{task}: {mark} ({rec['latency_ms']} ms)")
            if rec.get("error"):
                print(f"  error: {rec['error'][:200]}")
    print(f"rows written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
