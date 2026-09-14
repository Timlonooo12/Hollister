"""Shared fixtures. Tests never touch Telegram nor the real product page."""

from __future__ import annotations

from pathlib import Path

import pytest

from stockwatch.client import FetchResult
from stockwatch.config import StockWatchSettings, WatchConfig
from stockwatch.state import StateStore


@pytest.fixture
def settings(tmp_path: Path) -> StockWatchSettings:
    return StockWatchSettings(
        _env_file=None,
        STOCKWATCH_BOT_TOKEN="123456:TEST",
        STOCKWATCH_PRODUCT_URL="https://example.test/shop/eu-fr/p/icon-henley-63586319-2",
        STOCKWATCH_SIZES="XS,S",
        STOCKWATCH_POLL_INTERVAL="1",
        STOCKWATCH_STATE_FILE=str(tmp_path / "state.json"),
        STOCKWATCH_ERROR_ALERT_AFTER="2",
    )


@pytest.fixture
def config(settings: StockWatchSettings) -> WatchConfig:
    return WatchConfig.from_settings(settings)


@pytest.fixture
def state(settings: StockWatchSettings) -> StateStore:
    store = StateStore(settings.state_file)
    store.load()
    return store


class FakeClient:
    """Serves canned bodies in order; the last one repeats."""

    def __init__(self, bodies: list[str | FetchResult]) -> None:
        self.bodies = list(bodies)
        self.calls: list[str] = []

    async def fetch(self, url: str, **_: object) -> FetchResult:
        self.calls.append(url)
        item = self.bodies[min(len(self.calls) - 1, len(self.bodies) - 1)]
        if isinstance(item, FetchResult):
            return item
        return FetchResult(url=url, status_code=200, body=item, elapsed=0.01)

    async def aclose(self) -> None:
        return None


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def recipients(self) -> list[int]:
        return [1]

    async def broadcast(self, text: str, *, url: str | None = None, silent: bool = False) -> int:
        self.messages.append(text)
        return 1


@pytest.fixture
def fake_notifier() -> FakeNotifier:
    return FakeNotifier()
