import pandas as pd

from recon.cli import main
from recon.generate import generate
from recon.models import Payment
from recon.reconcile import load, run


def test_detects_every_injected_error(tmp_path):
    generate(tmp_path, seed=7)
    exc, _ = run(tmp_path / "orders.csv", tmp_path / "payments.csv")
    truth = pd.read_csv(tmp_path / "injected_errors.csv")
    found = set(zip(exc.order_id, exc.error_type))
    assert set(zip(truth.order_id, truth.error_type)) <= found
    assert len(exc) == len(truth)  # no false positives


def test_generator_is_reproducible(tmp_path):
    generate(tmp_path / "a", seed=1); generate(tmp_path / "b", seed=1)
    for f in ("orders.csv", "payments.csv", "injected_errors.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_cli_run_is_idempotent(tmp_path):
    main(["generate", "--out", str(tmp_path / "d")])
    args = ["run", "--orders", str(tmp_path / "d/orders.csv"), "--payments", str(tmp_path / "d/payments.csv"),
            "--out", str(tmp_path / "o")]
    main(args)
    first = {f.name: f.read_bytes() for f in (tmp_path / "o").iterdir()}
    main(args)
    assert first == {f.name: f.read_bytes() for f in (tmp_path / "o").iterdir()}
    assert set(first) == {"exceptions.csv", "summary.html"}


def test_invalid_rows_are_rejected(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("id,order_id,created,type,amount,fee,currency,status\n"
                 "a,#1,2025-01-01T00:00:00,charge,100,33,USD,succeeded\n"
                 "b,#2,not-a-date,charge,-5,0,USD,succeeded\n")
    good, bad = load(f, Payment)
    assert len(good) == 1 and len(bad) == 1
