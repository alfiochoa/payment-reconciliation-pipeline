import argparse
import logging
from pathlib import Path

from .api import make_server
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
    m = sub.add_parser("serve-mock", help="serve data/orders_api.json as a mock Shopify orders API")
    m.add_argument("--data", type=Path, default=Path("data/orders_api.json"))
    m.add_argument("--port", type=int, default=8765)
    r = sub.add_parser("run", help="reconcile and write reports")
    r.add_argument("--orders", type=Path, default=Path("data/orders.csv"))
    r.add_argument("--payments", type=Path, default=Path("data/payments.csv"))
    r.add_argument("--api-url", help="orders API base URL, e.g. http://127.0.0.1:8765 (3rd source)")
    r.add_argument("--tickets", type=Path, help="refund/support tickets CSV (4th source)")
    r.add_argument("--out", type=Path, default=Path("out"))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "generate":
        generate(a.out, a.orders, a.seed)
        logging.info("wrote synthetic data to %s", a.out)
    elif a.cmd == "serve-mock":
        srv = make_server(a.data, a.port)
        logging.info("mock orders API on http://127.0.0.1:%d (Ctrl+C to stop)", a.port)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        exc, stats = run(a.orders, a.payments, a.api_url, a.tickets)
        write_reports(exc, stats, a.out)
        logging.info("%d exceptions -> %s", len(exc), a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
