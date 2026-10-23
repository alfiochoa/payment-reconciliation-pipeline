"""Tests for the web demo's logic (recon/demo.py) and a smoke test of the Streamlit app itself."""
from pathlib import Path

import pandas as pd
import pytest

from recon import demo
from recon.generate import generate
from recon.reconcile import run


def test_default_demo_finds_every_injected_error():
    res = demo.analyze_bundled()
    table, tot = demo.proof(res.exc, res.truth)
    assert res.stats["exceptions"] == tot["injected"] == tot["found"] == 46
    assert tot["missed"] == tot["extra"] == 0
    assert res.stats["sources"] == 4 and res.stats["rejected_rows"] == 0 and not res.rejects
    assert table["extra"].sum() == 0 and table["missed"].sum() == 0


def test_invalid_rows_are_rejected_and_counted_not_dropped_silently():
    res = demo.analyze_bundled(corrupt=True)
    assert res.stats["rejected_rows"] == len(demo.BAD_ORDERS) + len(demo.BAD_PAYMENTS) == 5
    assert set(res.rejects) == {"orders", "payments"} and all("_error" in v.columns for v in res.rejects.values())
    assert res.stats["exceptions"] == 46          # the broken rows must not change the findings


def test_generated_data_is_fully_detected_and_reproducible():
    a, b = demo.analyze_synthetic(150, 3), demo.analyze_synthetic(150, 3)
    _, tot = demo.proof(a.exc, a.truth)
    assert tot["missed"] == tot["extra"] == 0
    assert a.exceptions_csv == b.exceptions_csv and a.summary_html == b.summary_html   # byte-identical re-runs


def test_demo_stats_match_the_cli_pipeline(tmp_path):
    generate(tmp_path, seed=11)
    exc, stats = run(tmp_path / "orders.csv", tmp_path / "payments.csv", None, tmp_path / "tickets.csv")
    res = demo.analyze_files(tmp_path / "orders.csv", tmp_path / "payments.csv", tmp_path / "tickets.csv")
    assert res.stats == stats and res.exc.equals(exc)


def test_uploads_work_with_two_sources():
    t = demo.template_files()
    res = demo.analyze_uploads(t["orders.csv"], t["payments.csv"])
    assert res.stats["sources"] == 2 and res.truth is None
    assert {"missing_payment", "duplicate_charge", "fee_discrepancy"} <= set(res.exc["error_type"])


@pytest.mark.parametrize("orders,payments,message", [
    (b"not,a,real\nfile,at,all\n", b"id\n1\n", "no valid rows"),
    (b"", b"", "could not be read"),
])
def test_bad_uploads_give_a_message_not_a_crash(orders, payments, message):
    with pytest.raises(demo.DemoError, match=message):
        demo.analyze_uploads(orders, payments)


def test_oversized_upload_is_refused():
    with pytest.raises(demo.DemoError, match="larger than"):
        demo.analyze_uploads(b"x" * (demo.MAX_BYTES + 1), b"id\n")


def test_streamlit_app_renders_with_the_default_data():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parent.parent
    at = AppTest.from_file(str(root / "streamlit_app.py"), default_timeout=90).run()
    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert values["Exceptions"] == "46" and values["Orders"] == "300" and values["False positives"] == "0"
