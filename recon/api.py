"""Shopify-style orders API: a local mock server (pagination via Link header, injected 429s)
and a client with retry/backoff. Stdlib only."""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from .models import Order

log = logging.getLogger("recon.api")


def make_server(data_file: Path, port: int = 8765, throttle_every: int = 3) -> ThreadingHTTPServer:
    """Serve orders from a JSON file. Every `throttle_every`-th request gets 429 + Retry-After."""
    orders = json.loads(Path(data_file).read_text())
    state = {"n": 0}
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/admin/api/orders.json":
                return self._send(404, {"error": "not found"})
            with lock:
                state["n"] += 1
                throttled = throttle_every and state["n"] % throttle_every == 0
            if throttled:
                return self._send(429, {"error": "rate limited"}, {"Retry-After": "0.05"})
            q = parse_qs(u.query)
            limit = int(q.get("limit", ["50"])[0])
            start = int(q.get("page_info", ["0"])[0])
            hdr = {}
            if start + limit < len(orders):
                host = f"http://{self.headers['Host']}"
                hdr["Link"] = f'<{host}{u.path}?limit={limit}&page_info={start + limit}>; rel="next"'
            self._send(200, {"orders": orders[start:start + limit]}, hdr)

        def _send(self, code, body, headers=None):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def _get(url: str, retries: int, sleep) -> tuple[dict, str | None]:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.load(r), r.headers.get("Link")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == retries:
                raise
            wait = float(e.headers.get("Retry-After") or 0.1 * 2 ** attempt)
            log.warning("HTTP %s on %s; retry %d/%d in %.2fs", e.code, url, attempt + 1, retries, wait)
            sleep(wait)


def fetch_orders(base_url: str, limit: int = 50, retries: int = 5, sleep=time.sleep) -> list[dict]:
    """Follow Link rel=next pagination, retrying on 429/5xx. Output is sorted -> deterministic."""
    url, out = f"{base_url.rstrip('/')}/admin/api/orders.json?limit={limit}", []
    while url:
        body, link = _get(url, retries, sleep)
        out += body["orders"]
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        url = m.group(1) if m else None
    good = []
    for rec in out:
        try:
            good.append(Order(**rec).model_dump())
        except ValidationError as e:
            log.warning("API order rejected: %s", str(e).replace("\n", " | "))
    log.info("API: fetched %d orders", len(good))
    return sorted(good, key=lambda r: r["order_id"])
