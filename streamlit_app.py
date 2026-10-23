"""Payment Reconciliation Pipeline: live demo. Runs the same code as the CLI on synthetic data, on data you generate, or on files you upload."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from recon import demo

REPO = "https://github.com/alfiochoa/payment-reconciliation-pipeline"
CHECKS = [
    ("missing_payment", "A non-cancelled order has no succeeded charge."),
    ("duplicate_charge", "More than one succeeded charge for one order."),
    ("fee_discrepancy", "The processor fee is not 2.9% + 30 cents (within 1 cent)."),
    ("refund_not_reflected", "The processor refunded more than the order records."),
    ("currency_mismatch", "The charge currency differs from the order currency."),
    ("rounding_issue", "The charge differs from the order total by 2 cents or less (larger gaps are amount_mismatch)."),
    ("orphan_payment", "A charge references an order that does not exist."),
    ("source_drift", "The orders CSV and the orders API disagree on total, status, currency or refunded amount."),
    ("refund_ticket_unprocessed", "An approved refund ticket has no matching processor refund."),
]
LIMITS = [
    "Matching is by `order_id` in the charge metadata: no fuzzy matching and no payout or bank-deposit grouping.",
    "The fee rule is one flat model (2.9% + 30 cents). Real fees vary by card type, region and currency conversion.",
    "The orders API is a local mock in the project; this demo reads its snapshot file instead.",
    "Tickets are matched to refunds by order and total amount only. One currency per order, no FX conversion.",
    "Synthetic data is cleaner than real exports; real files would need a column-mapping adapter.",
]
DOT = """digraph {
 rankdir=LR; bgcolor="transparent"; nodesep=0.3;
 node [shape=box, style="rounded,filled", fillcolor="#1a2442", fontcolor="#f2f5ff", color="#4f8cff", fontname="Helvetica"];
 edge [color="#8aa0c8", fontcolor="#8aa0c8", fontname="Helvetica"];
 "payments (Stripe-style)" -> Validate; "orders (Shopify-style)" -> Validate; "orders API" -> Validate; "refund tickets" -> Validate;
 Validate -> Reconcile [label="valid rows"]; Validate -> "rejected rows" [style=dashed, label="logged + counted"];
 Reconcile -> "exceptions.csv"; Reconcile -> "summary.html";
}"""

st.set_page_config(page_title="Payment Reconciliation Pipeline", layout="wide")
st.markdown("""<style>
.block-container{padding-top:2rem;max-width:1180px}
h1{font-size:1.9rem!important;letter-spacing:-.01em} h2,h3{font-size:1.15rem!important;font-weight:650!important}
.rc-note{margin:.2rem 0 1rem;border-left:4px solid #eda100;padding:.5rem .9rem;border-radius:6px;background:rgba(237,161,0,.10);font-size:.88rem}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner="Reconciling...")
def bundled(corrupt: bool):
    return demo.analyze_bundled(corrupt)


@st.cache_data(show_spinner="Generating data and reconciling...")
def synthetic(n_orders: int, seed: int, corrupt: bool):
    return demo.analyze_synthetic(n_orders, seed, corrupt)


@st.cache_data(show_spinner="Reconciling your files...")
def uploaded(orders: bytes, payments: bytes, tickets: bytes | None, api_json: bytes | None):
    return demo.analyze_uploads(orders, payments, tickets, api_json)


def money(cents: float) -> str:
    return f"${cents / 100:,.2f}"


with st.sidebar:
    st.header("Data")
    mode = st.radio("Source", ["Default demo data", "Generate synthetic data", "Upload my own files"],
                    help="The project's default demo: 300 orders, seed 42, generated on the fly. Same data as `python -m recon generate`.")
    corrupt, n_orders, seed = False, 300, 42
    if mode != "Upload my own files":
        corrupt = st.checkbox("Add 5 deliberately broken rows", help="Appends rows that break the schema, to show that validation rejects them instead of dropping them silently.")
    if mode == "Generate synthetic data":
        n_orders = st.slider("Orders", 100, 2000, 300, 50)
        seed = int(st.number_input("Seed", 0, 10_000, 42, help="Same seed, same data, byte-identical report."))
    up = {}
    if mode == "Upload my own files":
        st.warning("Use sample or fake data only. This runs on a free public host; files are processed in memory and deleted, but do not upload real customer or payment data.")
        up["orders"] = st.file_uploader("orders.csv (required)", type="csv")
        up["payments"] = st.file_uploader("payments.csv (required)", type="csv")
        up["tickets"] = st.file_uploader("tickets.csv (optional)", type="csv")
        up["api"] = st.file_uploader("orders API snapshot (optional, JSON)", type="json")
        st.caption("Columns must match the templates below.")
        for name, data in demo.template_files().items():
            st.download_button(f"Template: {name}", data, file_name=name, mime="text/csv", key=f"tpl-{name}")
    st.markdown(f"[Code and tests on GitHub]({REPO})")

st.title("Payment Reconciliation Pipeline")
st.caption("Live demo of the ETL project: four sources in, one exceptions report out. All demo data is synthetic: no real customers, orders or payment accounts.")

try:
    if mode == "Default demo data":
        res = bundled(corrupt)
    elif mode == "Generate synthetic data":
        res = synthetic(n_orders, seed, corrupt)
    elif up["orders"] and up["payments"]:
        res = uploaded(up["orders"].getvalue(), up["payments"].getvalue(), up["tickets"].getvalue() if up["tickets"] else None,
                       up["api"].getvalue() if up["api"] else None)
    else:
        st.info("Upload orders.csv and payments.csv in the sidebar to run the checks on your own sample files. tickets.csv and the orders API snapshot add two more checks.")
        st.stop()
except demo.DemoError as e:
    st.error(str(e))
    st.stop()

s = res.stats
m = st.columns(5)
m[0].metric("Orders", f"{s['orders']:,}")
m[1].metric("Payment rows", f"{s['payments']:,}")
m[2].metric("Exceptions", f"{s['exceptions']:,}")
m[3].metric("Orders affected", f"{s['orders_affected']:,}")
m[4].metric("Amount at risk", money(s["amount_at_risk"]), help="Sum of the absolute differences between what the order says and what the processor says.")
st.caption(f"{s['sources']} sources · {s['rejected_rows']} rejected rows · net processed {money(s['net_processed'])}")

t_exc, t_proof, t_val, t_how = st.tabs(["Exceptions", "Proof", "Validation", "How it works"])

with t_exc:
    if res.exc.empty:
        st.success("No exceptions: every order matches its payments.")
    else:
        st.bar_chart(pd.Series(s["by_type"], name="exceptions"), horizontal=True, height=max(160, 34 * len(s["by_type"])))
        f1, f2 = st.columns([2, 1])
        kinds = f1.multiselect("Error type", sorted(s["by_type"]), placeholder="All types")
        q = f2.text_input("Order id contains", placeholder="#1034")
        view = res.exc
        if kinds:
            view = view[view["error_type"].isin(kinds)]
        if q:
            view = view[view["order_id"].str.contains(q, case=False, regex=False)]
        shown = view.copy()
        for c in ("expected", "actual", "difference"):
            shown[c] = shown[c] / 100
        st.dataframe(shown, hide_index=True, use_container_width=True, column_config={
            c: st.column_config.NumberColumn(c, format="%.2f") for c in ("expected", "actual", "difference")})
        st.caption(f"{len(view)} of {len(res.exc)} exceptions shown. Amounts in currency units.")
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button("Download exceptions.csv", res.exceptions_csv, "exceptions.csv", "text/csv")
    d2.download_button("Download summary.html", res.summary_html, "summary.html", "text/html")

with t_proof:
    if res.truth is None:
        st.info("The proof compares the exceptions with the errors the generator injected, so it only exists for the default and generated data. Your own files have no answer key.")
    else:
        table, tot = demo.proof(res.exc, res.truth)
        p = st.columns(4)
        p[0].metric("Injected errors", tot["injected"])
        p[1].metric("Found", tot["found"])
        p[2].metric("Missed", tot["missed"])
        p[3].metric("False positives", tot["extra"])
        if tot["missed"] == 0 and tot["extra"] == 0:
            st.success(f"All {tot['injected']} injected errors were found and nothing else was flagged.")
        else:
            st.warning("The pipeline and the answer key disagree. See the table.")
        st.dataframe(table, hide_index=True, use_container_width=True)
        st.caption("The generator writes the answer key (`injected_errors.csv`) when it creates the data, and the tests assert the same thing on every run.")

with t_val:
    st.write("Every row is validated against a schema before any check runs. Invalid rows are **rejected, logged and counted**, never silently dropped.")
    if not res.rejects:
        st.success("No rows were rejected.")
        if mode != "Upload my own files":
            st.caption("Tick \"Add 5 deliberately broken rows\" in the sidebar to see them rejected.")
    for name, bad in res.rejects.items():
        st.subheader(f"{name}: {len(bad)} rejected")
        st.dataframe(bad, hide_index=True, use_container_width=True)

with t_how:
    st.graphviz_chart(DOT, use_container_width=True)
    st.subheader("The nine checks")
    st.dataframe(pd.DataFrame(CHECKS, columns=["Check", "Rule"]), hide_index=True, use_container_width=True)
    st.subheader("Idempotent")
    st.write("Reports contain no timestamps and rows are sorted, so the same input gives byte-identical output (tested).")
    st.subheader("Limitations")
    for line in LIMITS:
        st.markdown(f"- {line}")
