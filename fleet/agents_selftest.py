"""Selftest: every .opencode agent and command markdown is a valid contract.

Validates the OmO-native corps the way the harness will actually read it:
frontmatter keys, model id format, mode enum, permission values, and the
public-repo leak rule (no private literals in agent/command text).

Deterministic, dependency-free (manual frontmatter parse), no network,
no spend. Negative controls: malformed fixtures must be caught.
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / ".opencode" / "agents"
COMMANDS = ROOT / ".opencode" / "commands"

MODEL_RE = re.compile(r"^[^/#]+/[^#]+$")
MODES = {"primary", "subagent", "all"}
PERM_ACTIONS = {"allow", "ask", "deny"}
PERM_KEYS = {
    "read", "edit", "glob", "grep", "list", "bash", "task", "todowrite",
    "external_directory", "webfetch", "websearch", "lsp", "skill",
    "question", "doom_loop",
}
LEAK_PATTERNS = (
    re.compile(r"[Cc]:[/\\]+[Uu]sers[/\\]+frank\b"),
    re.compile(r"vck_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


def parse_frontmatter(text: str) -> tuple[dict, list[str]]:
    """Manual frontmatter parse: top-level keys + one nested level.

    Returns (mapping, errors). Only the shapes our corps uses:
    key: value, key: (starts a nested block),   nested: value.
    """
    if not text.startswith("---"):
        return {}, ["no frontmatter block (must start with ---)"]
    lines = text.splitlines()
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return {}, ["frontmatter not closed (missing closing ---)"]
    fm, nested, errors = {}, None, []
    for raw in lines[1:end]:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw.startswith("  ") and nested is not None:
            key, _, val = raw.strip().partition(":")
            val = val.strip()
            if key not in PERM_KEYS:
                errors.append(f"unknown permission key '{key}'")
            if val and val not in PERM_ACTIONS:
                errors.append(f"permission '{key}': '{val}' not in "
                               "allow/ask/deny")
            nested.setdefault(key, val)
            continue
        if raw.startswith(("\t", " ")):
            errors.append(f"indented line outside a block: '{raw.strip()}'")
            continue
        key, sep, val = raw.partition(":")
        if not sep:
            errors.append(f"line without colon: '{raw.strip()}'")
            continue
        val = val.strip()
        if val == "":
            nested = fm.setdefault(key, {})
            if not isinstance(nested, dict):
                errors.append(f"'{key}' re-used as block and value")
                nested = {}
        else:
            fm[key] = val
            nested = None
    return fm, errors


def validate(path: Path, kind: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    fm, errors = parse_frontmatter(text)
    desc = fm.get("description", "")
    if not desc:
        errors.append("missing required 'description'")
    model = fm.get("model", "")
    if model and not MODEL_RE.match(model):
        errors.append(f"model '{model}' is not provider/model format")
    if kind == "agent":
        mode = fm.get("mode", "all")
        if mode not in MODES:
            errors.append(f"mode '{mode}' not in {sorted(MODES)}")
    else:
        if "template" in fm:
            errors.append("markdown command must not carry 'template'")
        for key in ("agent", "model"):
            if key in fm and not isinstance(fm[key], str):
                errors.append(f"'{key}' must be a string")
        if "subtask" in fm and fm["subtask"] not in ("true", "false"):
            errors.append("subtask must be true/false")
    body = text.split("---")[-1]
    for pat in LEAK_PATTERNS:
        m = pat.search(text)
        if m:
            errors.append(f"leak: {pat.pattern[:12]}... in {path.name}")
    if len(text) - len(text.rstrip()) > 1:
        errors.append("trailing blank lines")
    return errors


def main() -> int:
    fails = []
    checked = 0
    for folder, kind in ((AGENTS, "agent"), (COMMANDS, "command")):
        if not folder.exists():
            fails.append(f"missing {folder.relative_to(ROOT)}")
            continue
        files = sorted(folder.glob("*.md"))
        if not files:
            fails.append(f"empty {folder.relative_to(ROOT)}")
        for path in files:
            checked += 1
            for err in validate(path, kind):
                fails.append(f"{path.relative_to(ROOT)}: {err}")
    if checked == 0:
        fails.append("no agent or command files found at all")

    # negative controls: each malformed fixture must be caught
    with tempfile.TemporaryDirectory() as td:
        fixtures = {
            "bad-model.md": ("agent", "---\ndescription: x\n"
                              "model: no-slash\nmode: subagent\n---\nbody\n"),
            "no-desc.md": ("agent", "---\nmode: primary\n---\nbody\n"),
            "bad-mode.md": ("agent", "---\ndescription: x\nmode: spaceship\n"
                             "---\nbody\n"),
            "bad-perm.md": ("agent", "---\ndescription: x\nmode: subagent\n"
                             "permission:\n  edit: sometimes\n---\nbody\n"),
            "leak.md": ("agent", "---\ndescription: x\nmode: subagent\n---\n"
                        "run C:" + "/Users/" + "frank/thing\n"),
            "open-fm.md": ("agent", "---\ndescription: x\nmode: subagent\n"
                           "body never closed\n"),
            "cmd-template.md": ("command", "---\ndescription: x\n"
                                "template: stale\n---\nbody\n"),
        }
        negatives = 0
        tmp = Path(td)
        for name, (kind, content) in fixtures.items():
            p = tmp / name
            p.write_text(content, encoding="utf-8")
            if validate(p, kind):
                negatives += 1
            else:
                fails.append(f"negative control NOT caught: {name}")
        if negatives != len(fixtures):
            print(f"agents_selftest: only {negatives}/{len(fixtures)} "
                  "negatives caught")
    if fails:
        print(f"AGENTS SELFTEST FAIL ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"agents selftest OK: {checked} corps files valid, "
          f"{len(fixtures)} negatives caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())