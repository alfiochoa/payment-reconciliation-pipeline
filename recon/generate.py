"""Reproducible synthetic data generator. ALL DATA IS FAKE.

Fee model (Stripe-like): round(2.9% * amount) + 30 cents.
Injected errors are written to injected_errors.csv as ground truth.
"""
import random
from pathlib import Path

import pandas as pd

FEE_PCT, FEE_FIXED = 0.029, 30
ERROR_COUNTS = {
    "missing_payment": 8, "duplicate_charge": 6, "fee_discrepancy": 7,
    "refund_not_reflected": 6, "currency_mismatch": 5, "rounding_issue": 5,
}


def expected_fee(amount: int) -> int:
    return round(amount * FEE_PCT) + FEE_FIXED


def generate(out_dir: Path, n_orders: int = 300, seed: int = 42) -> None:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    orders, pays = [], []
    base = pd.Timestamp("2025-01-01")
    for i in range(n_orders):
        oid = f"#{1001 + i}"
        ts = base + pd.Timedelta(minutes=rng.randint(0, 60 * 24 * 60))
        total = rng.randint(900, 25000)
        status = "cancelled" if rng.random() < 0.04 else "paid"
        refunded = 0
        orders.append(dict(order_id=oid, created_at=ts.isoformat(), currency="USD", total=total,
                           refunded_amount=0, financial_status=status))
        if status == "cancelled":
            continue
        pays.append(dict(id=f"ch_{i:05d}", order_id=oid, created=(ts + pd.Timedelta(seconds=30)).isoformat(),
                         type="charge", amount=total, fee=expected_fee(total), currency="USD", status="succeeded"))
        if rng.random() < 0.06:  # legitimate refund, reflected on both sides
            refunded = total // 2
            orders[-1].update(refunded_amount=refunded, financial_status="partially_refunded")
            pays.append(dict(id=f"re_{i:05d}", order_id=oid, created=(ts + pd.Timedelta(days=2)).isoformat(),
                             type="refund", amount=refunded, fee=0, currency="USD", status="succeeded"))

    # inject errors on distinct, clean, paid orders
    clean = [o for o in orders if o["financial_status"] == "paid"]
    rng.shuffle(clean)
    truth, k = [], 0
    pay_by_order = {p["order_id"]: p for p in pays if p["type"] == "charge"}
    for kind, n in ERROR_COUNTS.items():
        for _ in range(n):
            o = clean[k]; k += 1
            p = pay_by_order[o["order_id"]]
            if kind == "missing_payment":
                pays.remove(p)
            elif kind == "duplicate_charge":
                pays.append({**p, "id": p["id"].replace("ch_", "chdup_")})
            elif kind == "fee_discrepancy":
                p["fee"] += rng.choice([50, 125, 300])
            elif kind == "refund_not_reflected":
                pays.append(dict(id=p["id"].replace("ch_", "re_"), order_id=o["order_id"],
                                 created=p["created"], type="refund", amount=o["total"], fee=0,
                                 currency="USD", status="succeeded"))
            elif kind == "currency_mismatch":
                p["currency"] = "EUR"
            elif kind == "rounding_issue":
                p["amount"] += rng.choice([-1, 1]); p["fee"] = expected_fee(p["amount"])
            truth.append(dict(order_id=o["order_id"], error_type=kind))
    # orphan payment (charge with no matching order)
    pays.append(dict(id="ch_orphan1", order_id="#9999", created="2025-03-01T10:00:00", type="charge",
                     amount=4200, fee=expected_fee(4200), currency="USD", status="succeeded"))
    truth.append(dict(order_id="#9999", error_type="orphan_payment"))

    rng.shuffle(pays)
    pd.DataFrame(orders).to_csv(out_dir / "orders.csv", index=False)
    pd.DataFrame(pays).to_csv(out_dir / "payments.csv", index=False)
    pd.DataFrame(truth).sort_values("order_id").to_csv(out_dir / "injected_errors.csv", index=False)
