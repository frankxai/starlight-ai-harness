"""Build a self-contained styled HTML dashboard from the cross-harness report.

Usage:
    python fleet/report_dashboard.py                 # build fleet/reports/dashboard.html
    python fleet/report_dashboard.py --check         # build, then self-grade, exit 1 on fail

Design contract (per estate DESIGN-EXCELLENCE doctrine):
- FrankX tokens: Obsidian #0A0A0B surface, Emerald #10B981 action, Cyan #06B6D4
  detail, Amber #F59E0B attention - fail cells only.
- Self-contained: no external fetch, no script, no remote font, no image bodysnatch.
- Semantic-first landmarks; tables with <caption> + <th scope="col">.
- Deterministic: rendered numbers come from the report JSON; timestamps come
  from the JSON's generated_at, not wall clock, so re-runs are byte-stable
  for identical input.

Inputs:  fleet/reports/cross-harness-latest.json
Output:  fleet/reports/dashboard.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_JSON = ROOT / "fleet" / "reports" / "cross-harness-latest.json"
ROUTING_SCHEMA = ROOT / "fleet" / "routing.schema.json"
OUT_HTML = ROOT / "fleet" / "reports" / "dashboard.html"

_DOC = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-Harness Report · Starlight AI Harness</title>
<style>
:root{{
  --bg:#0A0A0B; --surface:#131315; --border:#26262A; --text:#F4F4F5;
  --muted:#9CA3AF; --accent:#10B981; --cyan:#06B6D4; --amber:#F59E0B;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);
  font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:62rem;margin:0 auto;padding:2.25rem 1.25rem 3rem}}
header h1{{margin:0 0 .25rem;font-size:1.45rem;letter-spacing:-.01em}}
header p{{margin:0 0 1.25rem;color:var(--muted);font-size:.9rem}}
dl.kpi{{display:flex;flex-wrap:wrap;gap:.75rem;margin:0 0 2rem}}
dl.kpi>div{{background:var(--surface);border:1px solid var(--border);
  border-radius:.5rem;padding:.75rem 1rem;min-width:9.5rem}}
dl.kpi dt{{color:var(--muted);font-size:.75rem;text-transform:uppercase;
  letter-spacing:.06em;margin:0 0 .2rem}}
dl.kpi dd{{margin:0;font-family:var(--mono);font-size:1.15rem}}
dl.kpi dd em{{font-style:normal;color:var(--muted);font-size:.8rem}}
section{{margin:0 0 2.25rem}}
section h2{{font-size:1.05rem;margin:0 0 .75rem;color:var(--cyan)}}
.scroll{{overflow-x:auto;border:1px solid var(--border);border-radius:.5rem;
  background:var(--surface)}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;min-width:46rem}}
caption{{text-align:left;color:var(--muted);font-size:.8rem;padding:.6rem .9rem 0}}
th,td{{text-align:left;padding:.55rem .9rem;border-top:1px solid var(--border)}}
thead tr{{border-top:none}}
th{{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em}}
tbody tr:first-child{{border-top:none}}
td.num,th.num{{font-family:var(--mono);text-align:right;font-variant-numeric:tabular-nums}}
.pill{{display:inline-block;border-radius:99px;padding:.1rem .65rem;font-size:.8rem;
  font-family:var(--mono)}}
.pill.good{{background:rgba(16,185,129,.14);color:var(--accent)}}
.pill.warn{{background:rgba(245,158,11,.15);color:var(--amber)}}
.pill.dim{{color:var(--muted)}}
td.fails span{{color:var(--amber)}}
footer{{border-top:1px solid var(--border);margin-top:1rem;padding-top:1rem;
  color:var(--muted);font-size:.82rem}}
footer code{{font-family:var(--mono);color:var(--cyan)}}
a{{color:var(--cyan)}}
.why{{color:var(--muted);font-size:.85rem;margin:.6rem 0 1.25rem;max-width:48rem}}
@media (prefers-reduced-motion:reduce){{*{{animation:none;transition:none}}}}
</style>
</head>
<body>
<div class="wrap">
<header role="banner">
  <h1>Cross-Harness Report</h1>
  <p>Starlight AI Harness · generated <time datetime="{gen_attr}">{gen}</time></p>
  <dl class="kpi">
    <div><dt>Graded passes</dt><dd>{kpi_rows}</dd></div>
    <div><dt>Rails measured</dt><dd>{kpi_rails}</dd></div>
    <div><dt>Gateway spend</dt><dd>${kpi_spend}<em> total</em></dd></div>
    <div><dt>Free-tier passes</dt><dd>{kpi_free}<em> at $0</em></dd></div>
  </dl>
</header>
<main role="main">
<section aria-labelledby="sec-battery">
  <h2 id="sec-battery">Battery · 12 tasks, deterministic checkers</h2>
  <div class="scroll">
  <table>
  <caption>Pass rate, latency, cost per recorded battery row. Fails name the task(s).</caption>
  <thead><tr>
    <th scope="col">Harness</th><th scope="col">Model</th>
    <th scope="col" class="num">n</th><th scope="col" class="num">Pass</th>
    <th scope="col" class="num">Avg latency</th><th scope="col" class="num">Cost</th>
    <th scope="col" class="num">$/pass</th><th scope="col">Fails</th>
  </tr></thead>
  <tbody>
{battery_rows}
  </tbody>
  </table>
  </div>
</section>
<section aria-labelledby="sec-receipts">
  <h2 id="sec-receipts">OpenCode plugin receipts · harness overhead</h2>
  <div class="scroll">
  <table>
  <caption>Cost per message and token mix from fleet/receipts/opencode.jsonl.</caption>
  <thead><tr>
    <th scope="col">Model</th><th scope="col" class="num">Msgs</th>
    <th scope="col" class="num">Cost</th>
    <th scope="col" class="num">Mean latency</th>
    <th scope="col" class="num">Input tok</th><th scope="col" class="num">Cache read</th>
    <th scope="col">Finish reason</th>
  </tr></thead>
  <tbody>
{receipt_rows}
  </tbody>
  </table>
  </div>
</section>
</main>
<section aria-labelledby="sec-routing">
  <h2 id="sec-routing">Routing SoT · chains, caps, pricing</h2>
  <div class="scroll">
  <table>
  <caption>Task classes from routing.schema.json: primary model, fallback chain, per-class ceiling.</caption>
  <thead><tr>
    <th scope="col">Class</th><th scope="col">Primary</th>
    <th scope="col">Fallbacks</th><th scope="col" class="num">Ceiling</th>
  </tr></thead>
  <tbody>
{chain_rows}
  </tbody>
  </table>
  </div>
  <p class="why">{primary_why}</p>
  <div class="scroll">
  <table>
  <caption>Token Pass keys and gates; gateway model pricing ($ per M tokens).</caption>
  <thead><tr>
    <th scope="col">Gate</th><th scope="col" class="num">Value</th>
    <th scope="col">Model</th>
    <th scope="col" class="num">In</th><th scope="col" class="num">Cached</th>
    <th scope="col" class="num">Out</th>
  </tr></thead>
  <tbody>
{cap_rows}
{priced_rows}
  </tbody>
  </table>
  </div>
</section>
<footer role="contentinfo">
<p>Deterministic render of <code>fleet/reports/cross-harness-latest.json</code> ·
regenerate with <code>python fleet/report_dashboard.py</code> ·
gate: <code>python oms/verify.py</code></p>
</footer>
</div>
</body>
</html>
"""


def _fmt_pass_rate(pr: float | None) -> str:
    if pr is None:
        return '<span class="pill dim">n/a</span>'
    cls = "good" if pr >= 0.999 else ("warn" if pr >= 0.9 else "warn")
    return f'<span class="pill {cls}">{pr * 100:.1f}%</span>'


def _fmt_num(value, fmt: str = "{}") -> str:
    if value is None:
        return '<span class="pill dim">n/a</span>'
    return f'<span>{fmt.format(value)}</span>'


def build() -> str:
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))

    battery_rows = []
    total_passes = 0
    total_spend = 0.0
    for item in data["battery"]:
        total_passes += int(item["n"] * (item["pass_rate"] or 0))
        if item.get("total_cost_usd"):
            total_spend += item["total_cost_usd"]
        lat = item.get("avg_latency_ms")
        lat_s = f"{lat / 1000:.1f} s" if lat is not None else "n/a"
        fails = (", ".join(item["fails"]) or "—")
        battery_rows.append(
            f'    <tr><td>{html.escape(item["harness"])}</td>'
            f'<td><code>{html.escape(item["model"])}</code></td>'
            f'<td class="num">{item["n"]}</td>'
            f"<td>{_fmt_pass_rate(item['pass_rate'])}</td>"
            f'<td class="num">{lat_s}</td>'
            f"<td class=\"num\">{_fmt_num(item.get('total_cost_usd'), '${:.4f}')}</td>"
            f"<td class=\"num\">{_fmt_num(item.get('cost_per_pass_usd'), '${:.2e}' if item.get('cost_per_pass_usd') and item['cost_per_pass_usd'] < 0.001 else '${:.5f}')}</td>"
            f'<td class="fails">{html.escape(fails)}</td></tr>'
        )

    receipt_rows = []
    for r in data.get("plugin_receipts", []):
        finish = ", ".join(f"{k} {v}" for k, v in (r.get("finish") or {}).items()) or "—"
        receipt_rows.append(
            f'    <tr><td><code>{html.escape(r["modelID"])}</code></td>'
            f'<td class="num">{r["msgs"]}</td>'
            f"<td class=\"num\">{_fmt_num(r.get('total_cost_usd'), '${:.4f}')}</td>"
            f"<td class=\"num\">{_fmt_num(r.get('mean_latency_ms'), '{} ms')}</td>"
            f"<td class=\"num\">{_fmt_num(r.get('mean_input_tokens'), '{:,.0f}')}</td>"
            f"<td class=\"num\">{_fmt_num(r.get('mean_cache_read_tokens'), '{:,.0f}')}</td>"
            f"<td>{html.escape(finish)}</td></tr>"
        )

    free_passes = sum(
        int(item["n"] * (item["pass_rate"] or 0))
        for item in data["battery"]
        if item["harness"].startswith("opencode")
    )

    gen = data["generated_at"][:19].replace("T", " ") + " UTC"

    schema = json.loads(ROUTING_SCHEMA.read_text(encoding="utf-8"))
    tp = schema["token_pass"]

    chain_rows = []
    for cls, spec in schema["task_classes"].items():
        chain_rows.append(
            f'    <tr><td><strong>{html.escape(cls)}</strong></td>'
            f'<td><code>{html.escape(spec["primary"])}</code></td>'
            f'<td>{" · ".join(f"<code>{html.escape(f)}</code>" for f in spec["fallbacks"] or ["—"])}</td>'
            f"<td class=\"num\">{_fmt_num(spec.get('cost_ceiling_usd'), '${:,.0f}')}</td></tr>"
        )

    cap_rows = [
        f'    <tr><td>Night cap (unattended)</td><td class="num">${schema["night_cap_usd"]}</td>'
        f'<td>·</td><td class="num">·</td><td class="num">·</td><td class="num">·</td></tr>',
    ]
    for label, val in (
        ("Credit quota / key", tp["credit_quota_per_key_usd"]),
        ("Soft alert / key", tp["soft_alert_usd_per_key"]),
        ("Hard cap / key", tp["hard_cap_usd_per_key"]),
        ("Keys configured", len(tp["keys"])),
    ):
        cap_rows.append(
            f'    <tr><td>{html.escape(str(label))}</td>'
            f"<td class=\"num\">{'${:,.0f}'.format(val) if isinstance(val, (int, float)) else val}</td>"
            f"<td>·</td><td class=\"num\">·</td><td class=\"num\">·</td><td class=\"num\">·</td></tr>"
        )

    priced_rows = []
    for model, mp in tp["models"].items():
        priced_rows.append(
            f'    <tr><td>Gateway</td><td class="num">·</td>'
            f'<td><code>{html.escape(model)}</code></td>'
            f"<td class=\"num\">${mp['input']:.2f}</td>"
            f"<td class=\"num\">${mp['cached']:.2f}</td>"
            f"<td class=\"num\">${mp['output']:.2f}</td></tr>"
        )
    for model in schema["free_tier"]["models"]:
        priced_rows.append(
            f'    <tr><td>Free tier</td><td class="num">·</td>'
            f'<td><code>{html.escape(model)}</code></td>'
            f'<td class="num">${0:.2f}</td><td class="num">$0.00</td>'
            f'<td class="num">$0.00</td></tr>'
        )

    primary_why = html.escape(schema["primary_why"])

    html_out = _DOC.format(
        gen_attr=html.escape(data["generated_at"][:19]),
        gen=html.escape(gen),
        kpi_rows=total_passes,
        kpi_rails=len(data["battery"]),
        kpi_spend=f"{total_spend:.4f}".rstrip("0").rstrip(".") or "0",
        kpi_free=free_passes,
        battery_rows="\n".join(battery_rows),
        receipt_rows="\n".join(receipt_rows),
        chain_rows="\n".join(chain_rows),
        cap_rows="\n".join(cap_rows),
        priced_rows="\n".join(priced_rows),
        primary_why=primary_why,
    )
    return html_out


def selfcheck(html_out: str) -> list[str]:
    """Deterministic self-grade of the rendered surface. Returns failures."""
    errs = []

    def want(cond: bool, why: str) -> None:
        if not cond:
            errs.append(why)

    low = html_out.lower()
    want(low.startswith("<!doctype html>"), "file must start with doctype")
    want('<html lang="en">' in low, "html lang attribute missing")
    want("viewport" in low, "viewport meta missing")
    want("<script" not in low, "must not contain script")
    want("http://" not in low and "https://" not in low,
         "self-contained violation: external URL present")
    want('role="banner"' in low and 'role="contentinfo"' in low, "landmarks missing")
    want('scope="col"' in low, "table headers must carry scope=col")
    want("<caption>" in low, "captions missing")
    want('id="sec-routing"' in low, "routing SoT section missing")
    want("night cap" in low and "hard cap" in low, "cap gates missing")
    for token in ("#0a0a0b", "#10b981", "#06b6d4", "#f59e0b"):
        want(token in low, f"token {token} missing")
    want("<time datetime=" in low, "time element missing")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="self-grade the rendered surface; exit 1 on failure")
    args = ap.parse_args()
    out = build()
    OUT_HTML.write_text(out, encoding="utf-8", newline="\n")
    print(f"wrote {OUT_HTML} ({len(out)} bytes)")
    if args.check:
        errs = selfcheck(out)
        if errs:
            print("self-check FAIL:")
            for e in errs:
                print(f"  - {e}")
            return 1
        print("self-check OK: doctype, lang, viewport, landmarks, tokens, "
              "self-contained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
