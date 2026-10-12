"""Load + validate inputs, then detect mismatches. Pure functions, deterministic output."""
import logging
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from .generate import expected_fee
from .models import Order, Payment

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


def reconcile(orders: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
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
    out = pd.DataFrame(rows, columns=COLS)
    return out.sort_values(["order_id", "error_type"], kind="stable").reset_index(drop=True)


def run(orders_path: Path, payments_path: Path) -> tuple[pd.DataFrame, dict]:
    orders, bad_o = load(orders_path, Order)
    pays, bad_p = load(payments_path, Payment)
    exc = reconcile(orders, pays)
    ok = pays[pays["status"] == "succeeded"]
    stats = dict(orders=len(orders), payments=len(pays), rejected_rows=len(bad_o) + len(bad_p),
                 exceptions=len(exc), orders_affected=exc["order_id"].nunique(),
                 amount_at_risk=int(exc["difference"].abs().sum()),
                 by_type=exc["error_type"].value_counts().sort_index().to_dict(),
                 net_processed=int(ok.loc[ok.type == "charge", "amount"].sum() - ok.loc[ok.type == "refund", "amount"].sum()))
    return exc, stats
