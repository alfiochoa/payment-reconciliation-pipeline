"""Write exceptions.csv + summary.html. No timestamps -> byte-identical on re-runs (idempotent)."""
import html
from pathlib import Path

import pandas as pd


def write_reports(exc: pd.DataFrame, stats: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    exc.to_csv(out_dir / "exceptions.csv", index=False)
    money = lambda c: f"{c / 100:,.2f}"
    summary = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in stats["by_type"].items())
    body = exc.copy()
    for c in ("expected", "actual", "difference"):
        body[c] = body[c].map(money)
    table = body.to_html(index=False, escape=True, border=0, classes="t")
    (out_dir / "summary.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Reconciliation summary</title><style>body{{font-family:sans-serif;margin:2rem}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}</style></head><body>
<h1>Reconciliation summary</h1><p><em>Synthetic demo data.</em></p>
<ul><li>Orders: {stats['orders']}</li><li>Payments: {stats['payments']}</li><li>Rejected rows: {stats['rejected_rows']}</li>
<li>Net processed: {money(stats['net_processed'])}</li><li>Exceptions: {stats['exceptions']} across {stats['orders_affected']} orders</li>
<li>Absolute amount at risk: {money(stats['amount_at_risk'])}</li></ul>
<h2>By type</h2><table>{summary}</table><h2>Exceptions</h2>{table}</body></html>""", encoding="utf-8")
