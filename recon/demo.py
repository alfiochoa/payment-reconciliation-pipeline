"""Logic behind the Streamlit demo, kept out of the UI so it is tested like the rest of the pipeline.

Default and generated data is synthetic. Uploaded files are written to a temporary folder, processed and deleted: nothing is kept.
"""
from __future__ import annotations

import functools
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .generate import generate
from .models import Order, Payment, Ticket
from .reconcile import load, reconcile, summarize
from .report import write_reports

MAX_BYTES = 5_000_000   # per uploaded file
MAX_ROWS = 20_000       # per uploaded file
DEFAULT_ORDERS, DEFAULT_SEED = 300, 42   # the project's default demo; generated on the fly because data/ is not committed

# Deliberately broken rows for the "show validation" switch. Every one fails a pydantic rule and must be rejected, never silently dropped.
BAD_ORDERS = [
    dict(order_id="#BAD1", created_at="not-a-date", currency="USD", total="abc", refunded_amount="0", financial_status="paid"),
    dict(order_id="#BAD2", created_at="2025-02-01T10:00:00", currency="US", total="1000", refunded_amount="0", financial_status="unknown"),
]
BAD_PAYMENTS = [
    dict(id="ch_bad1", order_id="#1001", created="2025-02-01T10:00:00", type="charge", amount="-500", fee="0", currency="USD", status="succeeded"),
    dict(id="ch_bad2", order_id="#1002", created="2025-02-01T10:00:00", type="charge", amount="1000", fee="59", currency="USD", status="weird"),
    dict(id="ch_bad3", order_id="#1003", created="yesterday", type="charge", amount="1000", fee="59", currency="USD", status="succeeded"),
]


class DemoError(ValueError):
    """A problem with the user's input that should be shown as a message, not a stack trace."""


@dataclass
class Result:
    exc: pd.DataFrame
    stats: dict
    rejects: dict[str, pd.DataFrame]
    truth: pd.DataFrame | None
    exceptions_csv: bytes
    summary_html: bytes


def _load(path: Path | None, model, name: str, required: bool):
    if path is None or not path.exists():
        if required:
            raise DemoError(f"{name} is required.")
        return None, pd.DataFrame()
    try:
        good, bad = load(path, model)
    except Exception as e:  # unreadable file, wrong delimiter, not CSV at all
        raise DemoError(f"{name} could not be read: {type(e).__name__}") from e
    if good.empty and required:
        first = bad["_error"].iloc[0][:200] if len(bad) else "no rows found"
        raise DemoError(f"{name}: no valid rows. First problem: {first}")
    return (good if not good.empty else None), bad


def analyze_files(orders: Path, payments: Path, tickets: Path | None = None, api: Path | None = None,
                  truth: Path | None = None) -> Result:
    o, bad_o = _load(orders, Order, "orders", True)
    p, bad_p = _load(payments, Payment, "payments", True)
    t, bad_t = _load(tickets, Ticket, "tickets", False)
    a, bad_a = _load(api, Order, "orders API snapshot", False)
    exc = reconcile(o, p, a, t)
    stats = summarize(exc, o, p, rejected_rows=len(bad_o) + len(bad_p) + len(bad_t) + len(bad_a),
                      sources=2 + (a is not None) + (t is not None))
    rejects = {k: v for k, v in (("orders", bad_o), ("payments", bad_p), ("tickets", bad_t), ("orders API", bad_a)) if len(v)}
    with tempfile.TemporaryDirectory() as td:
        write_reports(exc, stats, Path(td))
        csv_bytes, html_bytes = (Path(td) / "exceptions.csv").read_bytes(), (Path(td) / "summary.html").read_bytes()
    truth_df = pd.read_csv(truth, dtype=str) if truth is not None and truth.exists() else None
    return Result(exc, stats, rejects, truth_df, csv_bytes, html_bytes)


def add_invalid_rows(directory: Path) -> None:
    """Append rows that break the schema to orders.csv and payments.csv in `directory` (a temporary copy)."""
    for name, bad in (("orders.csv", BAD_ORDERS), ("payments.csv", BAD_PAYMENTS)):
        f = directory / name
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        pd.concat([df, pd.DataFrame(bad)], ignore_index=True).to_csv(f, index=False)


def analyze_directory(directory: Path, corrupt: bool = False) -> Result:
    """Run the full four-source reconciliation on a folder produced by `recon generate`."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "d"
        shutil.copytree(directory, work)
        if corrupt:
            add_invalid_rows(work)
        return analyze_files(work / "orders.csv", work / "payments.csv", work / "tickets.csv", work / "orders_api.json",
                             work / "injected_errors.csv")


def analyze_bundled(corrupt: bool = False) -> Result:
    """The default demo: 300 orders, seed 42 (46 injected errors), exactly what `python -m recon generate` writes."""
    return analyze_synthetic(DEFAULT_ORDERS, DEFAULT_SEED, corrupt)


@functools.lru_cache(maxsize=1)
def template_files() -> dict[str, bytes]:
    """The default demo's CSVs, offered as templates for the upload mode."""
    with tempfile.TemporaryDirectory() as td:
        generate(Path(td), DEFAULT_ORDERS, DEFAULT_SEED)
        return {n: (Path(td) / n).read_bytes() for n in ("orders.csv", "payments.csv", "tickets.csv")}


def analyze_synthetic(n_orders: int, seed: int, corrupt: bool = False) -> Result:
    with tempfile.TemporaryDirectory() as td:
        generate(Path(td), n_orders, seed)
        return analyze_directory(Path(td), corrupt)


def analyze_uploads(orders: bytes, payments: bytes, tickets: bytes | None = None, api_json: bytes | None = None) -> Result:
    blobs = {"orders.csv": orders, "payments.csv": payments, "tickets.csv": tickets, "orders_api.json": api_json}
    for name, data in blobs.items():
        if data is None:
            continue
        if len(data) > MAX_BYTES:
            raise DemoError(f"{name} is larger than {MAX_BYTES // 1_000_000} MB. This demo is for small samples.")
        if name.endswith(".csv") and data.count(b"\n") > MAX_ROWS + 1:
            raise DemoError(f"{name} has more than {MAX_ROWS:,} rows. This demo is for small samples.")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for name, data in blobs.items():
            if data is not None:
                (d / name).write_bytes(data)
        return analyze_files(d / "orders.csv", d / "payments.csv", d / "tickets.csv" if tickets else None,
                             d / "orders_api.json" if api_json else None)


def proof(exc: pd.DataFrame, truth: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Compare what the pipeline found with the errors the generator injected (ground truth)."""
    found = set(zip(exc["order_id"], exc["error_type"]))
    known = set(zip(truth["order_id"], truth["error_type"]))
    rows = []
    for kind in sorted({k for _, k in known | found}):
        inj = {x for x in known if x[1] == kind}
        got = {x for x in found if x[1] == kind}
        rows.append(dict(error_type=kind, injected=len(inj), found=len(got & inj), missed=len(inj - got), extra=len(got - inj)))
    table = pd.DataFrame(rows)
    totals = dict(injected=len(known), found=len(found & known), missed=len(known - found), extra=len(found - known))
    return table, totals
