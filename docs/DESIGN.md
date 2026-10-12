# Design decisions

## 1. Idempotency
Running the pipeline twice on the same inputs must yield byte-identical outputs, so a re-run never creates "new" exceptions or noisy diffs.
- Reports contain no timestamps or run IDs; rows are sorted by `(order_id, error_type)`.
- Money is integer cents everywhere (no float drift); the generator is seeded.
- API pages are sorted by `order_id` after fetch, so page order and retries can't change results.
- Output files are overwritten, never appended. Covered by `test_cli_run_is_idempotent`.

Trade-off: the report doesn't record when it ran; that's left to logs and the caller (file mtime, CI run).

## 2. Tolerance rules
- **Fees:** expected fee is `round(2.9% × amount) + 30¢`; a charge is flagged only if the fee differs by more than **1¢**, which absorbs rounding-mode differences between the processor and our model without hiding real overcharges.
- **Amounts:** a charge/order total gap of **≤ 2¢** is `rounding_issue` (low severity, usually tax/currency rounding); larger gaps are `amount_mismatch`. Splitting them keeps rounding noise from burying real money problems.
- Currency differences are never tolerated: they're reported as `currency_mismatch` and the amount comparison is skipped, since comparing across currencies is meaningless without FX.
Thresholds are module constants in `recon/reconcile.py` (`FEE_TOLERANCE`, `ROUNDING_MAX`).

## 3. Retry policy (orders API client)
- Retry only transient failures: HTTP **429, 500, 502, 503**. Other 4xx fail immediately (retrying can't fix them).
- Honor `Retry-After` when present, otherwise exponential backoff (`0.1 × 2^attempt` seconds).
- Max **5 retries** per page, then raise: a partial order list would produce false `missing`/drift exceptions, so failing loudly beats reconciling incomplete data.
- Pagination follows `Link: rel="next"`; each page is retried independently. `sleep` is injectable so tests run instantly.
