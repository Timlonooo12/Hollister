"""Économie de bande passante : requêtes conditionnelles, comptage, budget.

Derrière un proxy résidentiel facturé au gigaoctet, ces trois mécanismes font
la différence entre quelques euros et quelques centaines par mois.
"""

from __future__ import annotations

import gzip
import json
import random
import string
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stockwatch.client import ProductClient
from stockwatch.config import StockWatchSettings
from stockwatch.monitor import Monitor
from stockwatch.state import StateStore
from tests.conftest import FakeClient, FakeNotifier

PAGE = {"product": {"productId": "63586319", "skus": [
    {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"},
    {"sizePrimary": "S_p", "inventory": 0, "inventoryStatus": "Unavailable"},
]}}
# Du texte peu compressible : une page de 50 000 espaces se réduirait à
# quelques octets et le test ne mesurerait plus rien.
random.seed(1)
_FILLER = "".join(random.choices(string.ascii_letters + string.digits, k=60_000))
BODY = ("<script>window['APOLLO_STATE__x'] = " + json.dumps(PAGE) + ";</script><!--" + _FILLER + "-->").encode()
ETAG = '"v1"'


class _Handler(BaseHTTPRequestHandler):
    served = []

    def do_GET(self):
        self.served.append(self.headers.get("If-None-Match"))
        if self.headers.get("If-None-Match") == ETAG:
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.end_headers()
            return
        payload = gzip.compress(BODY)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("ETag", ETAG)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


@pytest.fixture
def server():
    _Handler.served = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/p/icon-henley-63586319-2"
    httpd.shutdown()


def build_settings(url: str, **extra) -> StockWatchSettings:
    return StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
                              STOCKWATCH_PRODUCT_URL=url, **extra)


class TestConditionalRequests:
    async def test_second_request_is_a_304_and_costs_almost_nothing(self, server):
        client = ProductClient(build_settings(server))
        try:
            first = await client.fetch(server)
            second = await client.fetch(server)
        finally:
            await client.aclose()

        assert first.ok and first.not_modified is False
        assert second.ok and second.not_modified is True
        # Un échange « inchangé » ne coûte que ses en-têtes.
        assert second.bytes_downloaded < 2_000
        assert second.bytes_downloaded < first.bytes_downloaded / 10
        assert _Handler.served == [None, ETAG]

    async def test_can_be_turned_off(self, server):
        client = ProductClient(build_settings(server, STOCKWATCH_CONDITIONAL_REQUESTS="false"))
        try:
            await client.fetch(server)
            second = await client.fetch(server)
        finally:
            await client.aclose()
        assert second.not_modified is False
        assert _Handler.served == [None, None]

    async def test_bytes_counted_are_the_compressed_ones(self, server):
        client = ProductClient(build_settings(server))
        try:
            result = await client.fetch(server)
        finally:
            await client.aclose()
        # Le corps décompressé pèse ~60 Ko ; seul le transfert compressé
        # (en-têtes compris) est compté.
        assert len(result.body) > 50_000
        assert 0 < result.bytes_downloaded < len(result.body)


class TestMonitorOnNotModified:
    async def test_unchanged_page_keeps_the_last_reading_and_stays_silent(
        self, settings, config, state, fake_notifier
    ):
        from stockwatch.client import FetchResult

        body = "<script>window['APOLLO_STATE__x'] = " + json.dumps(PAGE) + ";</script>"
        unchanged = FetchResult(url="u", status_code=304, body="", elapsed=0.01,
                                bytes_downloaded=180, not_modified=True)
        monitor = Monitor(settings, config, FakeClient([body, unchanged]), fake_notifier, state)

        first = await monitor.check_once()
        second = await monitor.check_once()
        await monitor._drain()

        assert first.sizes == {"XS": False, "S": False}
        assert second.ok is True and second.not_modified is True
        assert second.sizes == first.sizes          # dernière lecture conservée
        assert second.strategy == "304-non-modifie"
        assert fake_notifier.messages == []
        assert state.stats["not_modified"] == 1

    async def test_bytes_are_accumulated(self, settings, config, state, fake_notifier):
        from stockwatch.client import FetchResult

        result = FetchResult(url="u", status_code=304, body="", elapsed=0.01,
                             bytes_downloaded=200, not_modified=True)
        monitor = Monitor(settings, config, FakeClient([result]), fake_notifier, state)
        for _ in range(3):
            await monitor.check_once()
        assert state.stats["bytes_today"] == 600
        assert state.stats["bytes_total"] == 600


class TestDailyBudget:
    def _monitor(self, settings, config, state, notifier, budget_mb: str):
        settings = StockWatchSettings(
            _env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
            STOCKWATCH_PRODUCT_URL=settings.product_url,
            STOCKWATCH_DAILY_BUDGET_MB=budget_mb,
            STOCKWATCH_THROTTLED_INTERVAL="120",
        )
        return Monitor(settings, config, FakeClient(["<html></html>"]), notifier, state)

    async def test_no_budget_means_no_throttling(self, settings, config, state, fake_notifier):
        monitor = self._monitor(settings, config, state, fake_notifier, "0")
        state.add_bytes(50_000_000)
        assert monitor.over_budget() is False
        assert monitor._next_delay(0.0) == pytest.approx(config.poll_interval)

    async def test_over_budget_slows_down_and_warns_once(self, settings, config, state, fake_notifier):
        monitor = self._monitor(settings, config, state, fake_notifier, "10")
        state.add_bytes(11_000_000)
        assert monitor.over_budget() is True
        assert monitor._next_delay(0.0) == 120
        assert monitor._next_delay(0.0) == 120
        await monitor._drain()
        warnings = [m for m in fake_notifier.messages if "Budget" in m]
        assert len(warnings) == 1

    async def test_counter_resets_on_a_new_day(self, state):
        state.add_bytes(1_000)
        state.stats["bytes_day"] = "1999-01-01"
        assert state.add_bytes(500) == 500
        assert state.stats["bytes_total"] == 1_500
