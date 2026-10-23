"""Load + validate inputs, then detect mismatches. Pure functions, deterministic output."""
import logging
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from .generate import expected_fee
from .api import fetch_orders
from .models import Order, Payment, Ticket

log = logging.getLogger("recon")
COLS = ["order_id", "error_type", "payment_ids", "expected", "actual", "difference", "detail"]
FEE_TOLERANCE = 1  # cents
ROUNDING_MAX = 2   # cents: amount diffs up to this are 'rounding_issue'


def load(path: Path, model) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read CSV/JSON, validate each row with pydantic. Returns (valid, rejected)."""
    raw = pd.read_json(path, dtype=False) if path.suffix == ".json" else pd.read_csv(path, dtype=str, keep_default_na=False)
    good, bad = [], []
    for rec in raw.to_dict("records"):
        rec = {k: (None if v == "" else v) for k, v in rec.items()}
        try:
            good.append(model(**rec).model_dump())
        except ValidationError as e:
            bad.append({**rec, "_error": str(e).replace("\n", " | ")})
    if bad:
        log.warning("%s: rejected %d invalid rows", path.name, len(bad))
    log.info("%s: %d valid rows", path.name, len(good))
    return pd.DataFrame(good), pd.DataFrame(bad)


def reconcile(orders, payments, api_orders=None, tickets=None) -> pd.DataFrame:
    rows = []

    def add(oid, kind, pids, exp, act, detail):
        rows.append(dict(order_id=oid, error_type=kind, payment_ids=";".join(sorted(pids)),
                         expected=exp, actual=act, difference=act - exp, detail=detail))

    ok = payments[payments["status"] == "succeeded"]
    charges = {k: g for k, g in ok[ok["type"] == "charge"].groupby("order_id")}
    refunds = ok[ok["type"] == "refund"].groupby("order_id")["amount"].sum().to_dict()
    refund_ids = ok[ok["type"] == "refund"].groupby("order_id")["id"].apply(list).to_dict()

    for o in orders.itertuples():
        g = charges.get(o.order_id)
        if g is None:
            if o.financial_status != "cancelled":
                add(o.order_id, "missing_payment", [], o.total, 0, "order has no succeeded charge")
        else:
            first = g.sort_values("id").iloc[0]
            if len(g) > 1:
                add(o.order_id, "duplicate_charge", g["id"], o.total, int(g["amount"].sum()), f"{len(g)} charges for one order")
            if (g["currency"] != o.currency).any():
                add(o.order_id, "currency_mismatch", g["id"], 0, 0, f"order {o.currency} vs charge {','.join(sorted(set(g['currency'])))}")
            elif len(g) == 1:
                d = int(first["amount"]) - o.total
                if d != 0:
                    kind = "rounding_issue" if abs(d) <= ROUNDING_MAX else "amount_mismatch"
                    add(o.order_id, kind, g["id"], o.total, int(first["amount"]), "charge amount differs from order total")
            for c in g.itertuples():
                if abs(c.fee - expected_fee(c.amount)) > FEE_TOLERANCE:
                    add(o.order_id, "fee_discrepancy", [c.id], expected_fee(c.amount), int(c.fee), "fee differs from 2.9% + 30c")
        r = refunds.get(o.order_id, 0)
        if r > o.refunded_amount:
            add(o.order_id, "refund_not_reflected", refund_ids[o.order_id], int(r), o.refunded_amount,
                "processor refunded more than order records (expected=processor, actual=order)")
    known = set(orders["order_id"])
    for oid, g in charges.items():
        if oid not in known:
            add(oid, "orphan_payment", g["id"], 0, int(g["amount"].sum()), "charge references unknown order")
    for c in ok[ok["order_id"].isna()].itertuples():
        add("", "orphan_payment", [c.id], 0, int(c.amount), "charge has no order_id")
    if api_orders is not None:
        a = api_orders.set_index("order_id")
        for o in orders.itertuples():
            if o.order_id not in a.index:
                add(o.order_id, "source_drift", [], o.total, 0, "order missing from orders API")
                continue
            r = a.loc[o.order_id]
            diffs = [f for f in ("total", "refunded_amount", "financial_status", "currency") if getattr(o, f) != r[f]]
            if diffs:
                add(o.order_id, "source_drift", [], o.total, int(r["total"]), "orders CSV vs API differ: " + ",".join(diffs))
        for oid in sorted(set(a.index) - set(orders["order_id"])):
            add(oid, "source_drift", [], 0, int(a.loc[oid, "total"]), "order only in orders API")
    if tickets is not None:
        for t in tickets[tickets["status"] == "approved"].itertuples():
            got = int(refunds.get(t.order_id, 0))
            if got < t.requested_amount:
                add(t.order_id, "refund_ticket_unprocessed", [], t.requested_amount, got,
                    f"approved ticket {t.ticket_id} has no matching processor refund")
    out = pd.DataFrame(rows, columns=COLS)
    return out.sort_values(["order_id", "error_type"], kind="stable").reset_index(drop=True)


def run(orders_path: Path, payments_path: Path, api_url: str | None = None,
        tickets_path: Path | None = None) -> tuple[pd.DataFrame, dict]:
    orders, bad_o = load(orders_path, Order)
    pays, bad_p = load(payments_path, Payment)
    api = pd.DataFrame(fetch_orders(api_url)) if api_url else None
    tickets, bad_t = load(tickets_path, Ticket) if tickets_path else (None, pd.DataFrame())
    exc = reconcile(orders, pays, api, tickets)
    stats = summarize(exc, orders, pays, rejected_rows=len(bad_o) + len(bad_p) + len(bad_t),
                      sources=2 + (api is not None) + (tickets is not None))
    return exc, stats


def summarize(exc: pd.DataFrame, orders: pd.DataFrame, pays: pd.DataFrame, rejected_rows: int, sources: int) -> dict:
    """Headline numbers for the report. Shared by the CLI (run) and the Streamlit demo."""
    ok = pays[pays["status"] == "succeeded"]
    return dict(orders=len(orders), payments=len(pays), rejected_rows=rejected_rows, sources=sources,
                exceptions=len(exc), orders_affected=exc["order_id"].nunique(),
                amount_at_risk=int(exc["difference"].abs().sum()),
                by_type=exc["error_type"].value_counts().sort_index().to_dict(),
                net_processed=int(ok.loc[ok.type == "charge", "amount"].sum() - ok.loc[ok.type == "refund", "amount"].sum()))
