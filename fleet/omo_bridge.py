"""omo_bridge: machine-grade an OmO SUCCESS.md bar into harness rows.

OmO writes SUCCESS.md with checkbox criteria; the author loops with a
verifier until every box is checked. This bridge grades the bar's SHAPE
without running model calls:

  - every criterion must be author-checked
  - every criterion must name a verifier distinct from the owner
  - optional check:<name> must resolve to a real battery checker
    (so a claimed gate can never point at an imaginary one)

Grades:
  PASS     checked + independent verifier + valid check (if any)
  MAPPED   checked + independent verifier, but check name is unresolvable
  CLAIMED  checked, but verifier missing or equals owner
  FAIL     box not checked

Exit code is 0 only when nothing is CLAIMED/FAIL — the same "done =
verified" bar as omo itself, enforced mechanically.

Usage:
  python fleet/omo_bridge.py grade docs/SUCCESS.md
  python fleet/omo_bridge.py selftest
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FLEET = Path(__file__).resolve().parent
sys.path.insert(0, str(FLEET))

from harness_eval import TASKS  # noqa: E402
CHECKERS = set(TASKS)


def parse_bar(text):
    """Return list of criterion dicts from SUCCESS.md markdown.

    A criterion is its checkbox line plus the wrapped continuation lines
    that follow it (until the next checkbox): owner/verifier/check tags
    may sit on either."""
    blocks = []
    crit = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        m = re.match(r"\s*-\s*\[( |x|X)\]\s*(.*)$", raw)
        if m:
            crit = {"line": lineno, "checked": m.group(1).lower() == "x",
                    "raw": raw, "body": m.group(2)}
            blocks.append(crit)
            continue
        if crit and raw.strip() and not re.match(r"\s*#", raw):
            crit["body"] += " " + raw.strip()
    out = []
    tag_words = r"(owner|verifier|verified|verify|check)"
    for crit in blocks:
        tags = re.findall(tag_words + r"\s*:\s*([A-Za-z0-9_./-]+)", crit["body"])
        d = {
            "line": crit["line"],
            "checked": crit["checked"],
            "text": re.sub(r"\s*[-—]\s*" + tag_words + r":\S+", "", crit["raw"]).strip(" -—"),
            "owner": None,
            "verifier": None,
            "check": None,
        }
        # first checkbox line without trailing wrap keeps its own readable text
        first_text = re.sub(r"\s*[-—]\s*" + tag_words + r":\S+", "", crit["body"]).strip(" -—")
        d["text"] = first_text if first_text else d["text"]
        for key, val in tags:
            if key in ("verified", "verify"):
                key = "verifier"
            d[key] = val
        out.append(d)
    return out

def grade(crit):
    if not crit["checked"]:
        return "FAIL"
    if not crit["verifier"] or crit["verifier"] == crit["owner"]:
        return "CLAIMED"
    if crit["check"] and crit["check"] not in CHECKERS:
        return "MAPPED"
    return "PASS"


def grade_bar(path_or_text):
    text = Path(path_or_text).read_text(encoding="utf-8") if Path(path_or_text).exists() else path_or_text
    rows = []
    for crit in parse_bar(text):
        rows.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "omo-bridge",
            "criterion": crit["text"][:200],
            "line": crit["line"],
            "owner": crit["owner"],
            "verifier": crit["verifier"],
            "check": crit["check"],
            "grade": grade(crit),
        })
    return rows


def write_report(path, repo, rows):
    # repo dir + stem in the name so two repos' SUCCESS.md bars can't
    # overwrite each other's graded reports
    outp = FLEET / "reports" / f"omo_{repo}_{Path(path).stem}.json"
    outp.parent.mkdir(exist_ok=True)
    outp.write_text(json.dumps({"repo": repo, "graded_at": rows[0]["ts"] if rows else None, "rows": rows},
                               indent=2), encoding="utf-8")
    return outp


def summarize(rows):
    by = {}
    for r in rows:
        by[r["grade"]] = by.get(r["grade"], 0) + 1
    ok = by.get("PASS", 0)
    bad = by.get("CLAIMED", 0) + by.get("FAIL", 0)
    mapped_unresolvable = by.get("MAPPED", 0)
    return ok, bad, mapped_unresolvable, by


def cmd_grade(args):
    path = Path(args.path)
    if not path.exists():
        print(f"no bar at {path}"); return 2
    repo = path.resolve().parent.name
    rows = grade_bar(path)
    for r in rows:
        who = f"owner={r['owner'] or '-'} verifier={r['verifier'] or '-'}"
        chk = f" check={r['check']}" if r["check"] else ""
        print(f"[{r['grade']:>7}] line {r['line']:>3}  {r['criterion'][:70]}  ({who}{chk})")
    ok, bad, unres, by = summarize(rows)
    print(f"\n{len(rows)} criteria: {ok} PASS, {bad} unresolved, {unres} MAPPED")
    if not rows:
        print("bar has no checkboxes"); return 2
    outp = write_report(path, repo, rows)
    print(f"report: {outp}")
    return 0 if bad == 0 else 1


def cmd_selftest(_):
    ok = ("## bar\n"
            "- [x] pages render — owner: build, verified: claude, check: html-design\n"
            "- [x] battery green — owner: build, verify: codex\n"
            "- [x] claimed only — owner: build\n"
            "- [ ] not done — owner: build, verified: claude\n"
            "- [x] bad checker — owner: build, verified: claude, check: nonexistent-gate\n")
    wrapped = ("- [x] multi-line criterion with tags on the wrapped line coords\n"
               "  because bars wrap — owner: build, verified: codex, check: regex\n")
    combo = grade_bar(wrapped)
    assert combo[0]["grade"] == "PASS" and combo[0]["check"] == "regex", combo
    ok_rows = grade_bar(ok)
    grades = [r["grade"] for r in ok_rows]
    expect = ["PASS", "PASS", "CLAIMED", "FAIL", "MAPPED"]
    assert grades == expect, (grades, expect)
    # owner == verifier must CLAIM
    twin = grade_bar("- [x] self-approved — owner: a, verifier: a")
    assert twin[0]["grade"] == "CLAIMED"
    print("omo_bridge selftest OK: PASS/CLAIMED/FAIL/MAPPED all graded correctly, "
          "owner==verifier rejected, checker names validated")
    return 0


def main(argv):
    p = sys.argv[1] if len(argv) > 1 else ""
    a = sys.argv[2] if len(argv) > 2 else ""
    if p == "grade" and a:
        return cmd_grade(type("A", (), {"path": a}))
    if p == "selftest":
        return cmd_selftest(a)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
