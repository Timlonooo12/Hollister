"""Veille nocturne : la plage traverse minuit, et l'heure est celle de
l'utilisateur, pas celle du serveur (un VPS tourne en UTC)."""

from __future__ import annotations

from datetime import datetime

from stockwatch.config import StockWatchSettings, WatchConfig
from stockwatch.schedule import (
    format_window,
    is_quiet,
    next_wake,
    resolve_timezone,
    seconds_until_wake,
)


class TestWindow:
    def test_window_crossing_midnight(self):
        for hour in (20, 23, 0, 3, 6):
            assert is_quiet(datetime(2026, 9, 15, hour), 20, 7) is True
        for hour in (7, 12, 19):
            assert is_quiet(datetime(2026, 9, 15, hour), 20, 7) is False

    def test_window_inside_a_single_day(self):
        assert is_quiet(datetime(2026, 9, 15, 2), 1, 6) is True
        assert is_quiet(datetime(2026, 9, 15, 6), 1, 6) is False
        assert is_quiet(datetime(2026, 9, 15, 23), 1, 6) is False

    def test_identical_hours_disable_the_window(self):
        assert is_quiet(datetime(2026, 9, 15, 5), 7, 7) is False

    def test_next_wake_is_the_end_hour(self):
        wake = next_wake(datetime(2026, 9, 15, 23, 30), 20, 7)
        assert (wake.day, wake.hour, wake.minute) == (16, 7, 0)
        wake = next_wake(datetime(2026, 9, 15, 3, 0), 20, 7)
        assert (wake.day, wake.hour) == (15, 7)

    def test_seconds_until_wake(self):
        assert seconds_until_wake(datetime(2026, 9, 15, 6, 0), 20, 7) == 3600

    def test_formatting(self):
        assert format_window(20, 7) == "20h00 → 07h00"

    def test_unknown_timezone_is_not_fatal(self):
        assert resolve_timezone("Mars/Olympus") is None
        assert resolve_timezone("Europe/Paris") is not None


class TestConfigIntegration:
    def _config(self, **extra) -> WatchConfig:
        settings = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", **extra)
        return WatchConfig.from_settings(settings)

    def test_disabled_by_default(self):
        config = self._config()
        assert config.quiet_enabled is False
        assert config.is_quiet_now() is False

    def test_enabled_when_both_hours_are_set(self):
        config = self._config(STOCKWATCH_QUIET_START="20", STOCKWATCH_QUIET_END="7")
        assert config.quiet_enabled is True

    def test_out_of_range_hours_are_ignored(self):
        config = self._config(STOCKWATCH_QUIET_START="99", STOCKWATCH_QUIET_END="7")
        assert config.quiet_enabled is False

    def test_timezone_is_the_users(self):
        config = self._config(STOCKWATCH_TIMEZONE="Europe/Paris")
        assert config.now().tzinfo is not None
