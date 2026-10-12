import threading
import urllib.error

import pandas as pd
import pytest

from recon.api import fetch_orders, make_server
from recon.cli import main
from recon.generate import generate
from recon.models import Payment
from recon.reconcile import load, run


@pytest.fixture
def api(tmp_path):
    """Mock orders API with small pages and a 429 every 2nd request."""
    generate(tmp_path, seed=7)
    srv = make_server(tmp_path / "orders_api.json", port=0, throttle_every=2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield tmp_path, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def full_run(d, url):
    return run(d / "orders.csv", d / "payments.csv", url, d / "tickets.csv")


def test_detects_every_injected_error(api):
    d, url = api
    exc, stats = full_run(d, url)
    truth = pd.read_csv(d / "injected_errors.csv")
    assert set(zip(truth.order_id, truth.error_type)) <= set(zip(exc.order_id, exc.error_type))
    assert len(exc) == len(truth)  # no false positives
    assert stats["sources"] == 4
    assert {"source_drift", "refund_ticket_unprocessed"} <= set(exc.error_type)


def test_api_pagination_and_retry(api):
    d, url = api
    waits = []
    rows = fetch_orders(url, limit=40, sleep=waits.append)
    assert len(rows) == 300 and rows == sorted(rows, key=lambda r: r["order_id"])
    assert waits  # 429s were hit and retried


def test_api_gives_up_after_retries(tmp_path):
    generate(tmp_path)
    srv = make_server(tmp_path / "orders_api.json", port=0, throttle_every=1)  # always 429
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pytest.raises(urllib.error.HTTPError):
        fetch_orders(f"http://127.0.0.1:{srv.server_port}", retries=2, sleep=lambda s: None)
    srv.shutdown()


def test_generator_is_reproducible(tmp_path):
    generate(tmp_path / "a", seed=1); generate(tmp_path / "b", seed=1)
    for f in ("orders.csv", "payments.csv", "injected_errors.csv", "orders_api.json", "tickets.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_cli_run_is_idempotent(api):
    d, url = api
    args = ["run", "--orders", str(d / "orders.csv"), "--payments", str(d / "payments.csv"),
            "--api-url", url, "--tickets", str(d / "tickets.csv"), "--out", str(d / "o")]
    main(args)
    first = {f.name: f.read_bytes() for f in (d / "o").iterdir()}
    main(args)
    assert first == {f.name: f.read_bytes() for f in (d / "o").iterdir()}
    assert set(first) == {"exceptions.csv", "summary.html"}


def test_invalid_rows_are_rejected(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("id,order_id,created,type,amount,fee,currency,status\n"
                 "a,#1,2025-01-01T00:00:00,charge,100,33,USD,succeeded\n"
                 "b,#2,not-a-date,charge,-5,0,USD,succeeded\n")
    good, bad = load(f, Payment)
    assert len(good) == 1 and len(bad) == 1
