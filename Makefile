PY ?= python
PORT ?= 8765

.PHONY: demo test
demo:  ## generate synthetic data, start mock API, reconcile, write out/
	$(PY) -m recon generate
	@$(PY) -m recon serve-mock --port $(PORT) >/dev/null 2>&1 & echo $$! > .mock.pid; sleep 2; \
	$(PY) -m recon run --api-url http://127.0.0.1:$(PORT) --tickets data/tickets.csv; status=$$?; \
	kill `cat .mock.pid` 2>/dev/null; rm -f .mock.pid; exit $$status

test:
	$(PY) -m pytest -q
