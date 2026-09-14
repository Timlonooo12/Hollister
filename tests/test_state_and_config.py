"""Persistence and configuration parsing."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from stockwatch.config import StockWatchSettings, WatchConfig
from stockwatch.state import StateStore, apply_overrides


class TestSettings:
    def test_defaults_target_the_hollister_product(self, settings):
        assert settings.product_url.endswith("icon-henley-63586319-2")
        assert settings.sizes == ["XS", "S"]
        assert settings.poll_interval == 1.0

    def test_sizes_are_normalised(self):
        s = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_SIZES=" xs ; small,S")
        assert s.sizes == ["XS", "S"]

    def test_chat_ids_accept_a_csv_list(self):
        s = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_CHAT_IDS="42, -100123, oops")
        assert s.chat_ids == [42, -100123]

    def test_interval_has_a_floor(self):
        s = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_POLL_INTERVAL="0.01")
        assert s.poll_interval == 0.2

    def test_url_must_be_http(self):
        with pytest.raises(ValidationError):
            StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_PRODUCT_URL="ftp://x")

    def test_token_is_required(self, monkeypatch):
        monkeypatch.delenv("STOCKWATCH_BOT_TOKEN", raising=False)
        with pytest.raises(ValidationError):
            StockWatchSettings(_env_file=None)

    def test_extra_headers_json(self):
        s = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
                               STOCKWATCH_EXTRA_HEADERS='{"X-Test": "1"}')
        assert s.extra_headers == {"X-Test": "1"}
        bad = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_EXTRA_HEADERS="nope")
        assert bad.extra_headers == {}

    def test_token_never_appears_in_repr(self, settings):
        assert "123456:TEST" not in repr(settings)


class TestStateStore:
    async def test_round_trip(self, settings):
        store = StateStore(settings.state_file)
        store.load()
        await store.add_subscriber(77)
        store.record_size("XS", True, alerted=True)
        store.bump("alerts")
        await store.save()

        reloaded = StateStore(settings.state_file)
        reloaded.load()
        assert reloaded.subscribers == {77}
        assert reloaded.get_size("XS").available is True
        assert reloaded.get_size("XS").last_alert_at is not None
        assert reloaded.stats["alerts"] == 1

    async def test_unsubscribe(self, settings):
        store = StateStore(settings.state_file)
        assert await store.add_subscriber(1) is True
        assert await store.add_subscriber(1) is False
        assert await store.remove_subscriber(1) is True
        assert await store.remove_subscriber(1) is False

    def test_corrupt_file_does_not_crash(self, settings):
        settings.state_file.write_text("{ not json", "utf-8")
        store = StateStore(settings.state_file)
        store.load()
        assert store.subscribers == set()

    async def test_changed_at_only_moves_on_a_real_change(self, settings):
        store = StateStore(settings.state_file)
        store.record_size("S", False)
        first = store.get_size("S").changed_at
        store.record_size("S", False)
        assert store.get_size("S").changed_at == first
        store.record_size("S", True)
        assert store.get_size("S").changed_at > first

    async def test_overrides_are_reapplied_after_a_restart(self, settings):
        store = StateStore(settings.state_file)
        await store.set_override("sizes", ["M"])
        await store.set_override("poll_interval", 5)
        await store.set_override("product_url", "https://example.test/p/other-12345678")
        await store.set_override("paused", True)

        reloaded = StateStore(settings.state_file)
        reloaded.load()
        config = WatchConfig.from_settings(settings)
        apply_overrides(config, reloaded.overrides)
        assert config.sizes == ["M"]
        assert config.poll_interval == 5
        assert config.product_url.endswith("other-12345678")
        assert config.paused is True

    async def test_garbage_overrides_are_ignored(self, settings):
        config = WatchConfig.from_settings(settings)
        apply_overrides(config, {"sizes": "XS", "poll_interval": -3, "product_url": "javascript:alert(1)"})
        assert config.sizes == ["XS", "S"]
        assert config.poll_interval == 1.0
        assert config.product_url.endswith("icon-henley-63586319-2")

    async def test_written_file_is_valid_json(self, settings):
        store = StateStore(settings.state_file)
        await store.add_subscriber(5)
        payload = json.loads(settings.state_file.read_text("utf-8"))
        assert payload["subscribers"] == [5]


class TestLabel:
    def test_label_is_derived_from_the_url(self):
        from stockwatch.config import label_from_url

        assert label_from_url("https://x.test/shop/eu-fr/p/icon-henley-63586319-2") == "Icon Henley"
        assert label_from_url("https://x.test/p/63586319") == "Produit surveillé"

    def test_explicit_label_wins(self, settings):
        settings.product_label = "Mon tee"
        assert WatchConfig.from_settings(settings).product_label == "Mon tee"
