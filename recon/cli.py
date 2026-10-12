import argparse
import logging
from pathlib import Path

from .generate import generate
from .reconcile import run
from .report import write_reports


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="recon", description="Reconcile payment payouts against store orders.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="create synthetic orders/payments with injected errors")
    g.add_argument("--out", type=Path, default=Path("data"))
    g.add_argument("--orders", type=int, default=300)
    g.add_argument("--seed", type=int, default=42)
    r = sub.add_parser("run", help="reconcile and write reports")
    r.add_argument("--orders", type=Path, default=Path("data/orders.csv"))
    r.add_argument("--payments", type=Path, default=Path("data/payments.csv"))
    r.add_argument("--out", type=Path, default=Path("out"))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "generate":
        generate(a.out, a.orders, a.seed)
        logging.info("wrote synthetic data to %s", a.out)
    else:
        exc, stats = run(a.orders, a.payments)
        write_reports(exc, stats, a.out)
        logging.info("%d exceptions -> %s", len(exc), a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
