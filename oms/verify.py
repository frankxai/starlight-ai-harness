#!/usr/bin/env python3
"""oh-my-starlight release gate.

Verifies the OmS package is self-consistent before anything ships:
 1. every manifest file exists
 1b. every shipped fleet/corps file is manifest-listed (packages only) -
     the export wipe would silently delete an unlisted file
 2. the schema parses, and its eval_battery task list matches the battery
    actually implemented in harness_eval.py
 3. harness_eval selftest passes (checkers vs ground truth, no model calls)
 4. route_task classifies a smoke prompt into a class the schema serves
 5. the generated OmO config on disk reflects the schema's primary model

Exit 0 = releasable, exit 1 = not. Deterministic only - no model calls.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fleet"))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> None:
    manifest = json.loads((ROOT / "oms" / "manifest.json").read_text(encoding="utf-8"))

    # 1. manifest file list
    missing = [f["path"] for f in manifest["files"] if not (ROOT / f["path"]).exists()]
    check("manifest files resolve", not missing, f"missing: {missing}" if missing else f"{len(manifest['files'])} files")

    # 1b. shipped-surface coverage (packages only: SANITIZATION.md marks a
    # generated package; the private dev tree is the source, not a package).
    # Every fleet script and corps file present must be manifest-listed -
    # the export wipes-and-recopies from this manifest, so an unlisted file
    # is silently deleted on the next regen (the 2026-11-03 corps wipe).
    if (ROOT / "SANITIZATION.md").exists():
        listed = {f["path"] for f in manifest["files"]}
        present = {p.relative_to(ROOT).as_posix()
                   for p in (ROOT / "fleet").glob("*.py")}
        if (ROOT / ".opencode").exists():
            present |= {p.relative_to(ROOT).as_posix()
                        for p in (ROOT / ".opencode").rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts}
        orphaned = sorted(present - listed)
        check("shipped surface fully manifest-listed", not orphaned,
              f"unlisted (would be wiped on regen): {orphaned}"
              if orphaned else f"{len(present)} fleet/corps files covered")

    # 2. schema <-> battery task list
    schema = json.loads((ROOT / manifest["schema"]).read_text(encoding="utf-8"))
    print(f"[INFO] package version = schema v{schema['version']} ({manifest['schema']} as SoT)")
    import harness_eval
    schema_tasks = list(schema.get("eval_battery", {}).get("tasks", []))
    impl_tasks = list(harness_eval.TASKS)
    check("schema battery matches harness_eval.TASKS", schema_tasks == impl_tasks,
          f"schema={schema_tasks} impl={impl_tasks}" if schema_tasks != impl_tasks else f"{len(impl_tasks)} tasks")

    # 2b. battery count sanity (Phase 1 contract: >= 10)
    check("battery >= 10 tasks", len(impl_tasks) >= 10, f"{len(impl_tasks)}")

    # 3. selftest
    print("[RUN ] harness_eval selftest")
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            harness_eval.cmd_selftest(None)
        check("harness_eval selftest", True, buf.getvalue().strip())
    except SystemExit:
        check("harness_eval selftest", False, buf.getvalue().strip())

    # 4. classify smoke: routed class must exist in schema chains
    from route_task import classify
    cls = classify("review this PR")["class"]
    check("route_task smoke classify", cls in schema["task_classes"], f"class={cls}")

    # 5. generated OmO config in sync with schema primary (skip when the
    # machine has no OmO wiring; the config is install state, not package)
    primary = schema["primary_model"]
    omo = Path.home() / ".omo" / "omo.jsonc"
    if not omo.exists():
        print("[SKIP] gen_omo_config output in sync - no ~/.omo/omo.jsonc on this machine; "
              "run gen_omo_config.py to install")
    else:
        ok = primary in omo.read_text(encoding="utf-8", errors="replace")
        check("gen_omo_config output in sync", ok,
              f"primary={primary} missing from {omo}" if not ok else "")

    print()
    if FAILS:
        print(f"NOT RELEASABLE: {len(FAILS)} check(s) failed: {FAILS}")
        raise SystemExit(1)
    print("oh-my-starlight package verified: releasable")


if __name__ == "__main__":
    main()
