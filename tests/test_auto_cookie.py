"""Renouvellement automatique du cookie.

Un cookie de pare-feu applicatif expire en quelques heures : sans
renouvellement, la surveillance s'arrête pendant la nuit et personne ne le voit.
"""

from __future__ import annotations

import json

import pytest

from stockwatch import monitor as monitor_module
from stockwatch.browser import BrowserSession, BrowserUnavailable
from stockwatch.config import StockWatchSettings, WatchConfig
from stockwatch.monitor import Monitor
from tests.conftest import FakeClient

PAGE = ("<script>window['APOLLO_STATE__x'] = " + json.dumps({"product": {
    "productId": "63586319",
    "skus": [{"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"}],
}}) + ";</script>")


@pytest.fixture
def settings(tmp_path):
    return StockWatchSettings(
        _env_file=None,
        STOCKWATCH_BOT_TOKEN="1:x",
        STOCKWATCH_STATE_FILE=str(tmp_path / "state.json"),
        STOCKWATCH_COOKIE_RETRY_MINUTES="10",
        STOCKWATCH_COOKIE_REFRESH_MINUTES="180",
    )


@pytest.fixture
def config(settings):
    return WatchConfig.from_settings(settings)


def build(settings, config, state, notifier, bodies=(PAGE,)):
    return Monitor(settings, config, FakeClient(list(bodies)), notifier, state)


def fake_browser(monkeypatch, session: BrowserSession | Exception):
    async def _fetch(url, **kwargs):
        if isinstance(session, Exception):
            raise session
        return session

    monkeypatch.setattr(monitor_module, "fetch_session", _fetch)


class TestRenewal:
    async def test_a_fresh_cookie_is_applied_and_remembered(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserSession(cookie="a=1; b=2", user_agent="Safari/26"))
        monitor = build(settings, config, state, fake_notifier)

        assert await monitor.refresh_cookie("test") is True
        await monitor._drain()

        assert monitor.client.identity == ("a=1; b=2", "Safari/26")
        assert state.session["cookie"] == "a=1; b=2"
        assert state.session["user_agent"] == "Safari/26"
        assert state.stats["cookies_renewed"] == 1
        assert any("Cookie renouvelé" in message for message in fake_notifier.messages)

    async def test_attempts_are_spaced_out(self, settings, config, state, fake_notifier, monkeypatch):
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Safari"))
        monitor = build(settings, config, state, fake_notifier)

        assert await monitor.refresh_cookie() is True
        # Lancer un navigateur à chaque vérification ratée serait ruineux.
        assert await monitor.refresh_cookie() is False

    async def test_a_missing_browser_is_reported_once(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserUnavailable("Playwright n'est pas installé"))
        monitor = build(settings, config, state, fake_notifier)

        assert await monitor.refresh_cookie() is False
        monitor._last_cookie_attempt = None
        assert await monitor.refresh_cookie() is False
        await monitor._drain()

        warnings = [m for m in fake_notifier.messages if "indisponible" in m]
        assert len(warnings) == 1

    async def test_a_browser_crash_does_not_kill_the_loop(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, RuntimeError("chromium a planté"))
        monitor = build(settings, config, state, fake_notifier)
        assert await monitor.refresh_cookie() is False

    async def test_it_can_be_turned_off(self, config, state, fake_notifier, monkeypatch, tmp_path):
        settings = StockWatchSettings(
            _env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
            STOCKWATCH_STATE_FILE=str(tmp_path / "s.json"),
            STOCKWATCH_AUTO_COOKIE="false",
        )
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Safari"))
        monitor = build(settings, config, state, fake_notifier)
        assert await monitor.refresh_cookie() is False


class TestStaleness:
    async def test_an_absent_cookie_counts_as_stale(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier)
        assert monitor._cookie_is_stale() is True

    async def test_a_fresh_cookie_is_not_stale(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Safari"))
        monitor = build(settings, config, state, fake_notifier)
        await monitor.refresh_cookie()
        assert monitor._cookie_is_stale() is False

    async def test_an_old_cookie_is_stale_again(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Safari"))
        monitor = build(settings, config, state, fake_notifier)
        await monitor.refresh_cookie()
        state.session["obtained_at"] = "2020-01-01T00:00:00+00:00"
        assert monitor._cookie_is_stale() is True


class TestClientIdentity:
    def test_the_runtime_cookie_wins_over_the_configured_one(self, tmp_path):
        from stockwatch.client import ProductClient

        settings = StockWatchSettings(
            _env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
            STOCKWATCH_COOKIE="ancien=1",
            STOCKWATCH_STATE_FILE=str(tmp_path / "s.json"),
        )
        client = ProductClient(settings)
        assert client._headers()["Cookie"] == "ancien=1"

        client.set_identity("neuf=2", "Safari/26")
        headers = client._headers()
        assert headers["Cookie"] == "neuf=2"
        assert headers["User-Agent"] == "Safari/26"


class TestBrowserArguments:
    """Le navigateur reçoit une locale, pas un en-tête Accept-Language."""

    async def test_the_locale_is_normalised(self, monkeypatch):
        import stockwatch.browser as browser_module

        seen: dict[str, object] = {}

        class FakePlaywright:
            def __init__(self):
                self.chromium = self

            async def launch(self, **kwargs):
                seen["args"] = kwargs.get("args")
                raise RuntimeError("arrêt volontaire du test")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setitem(
            __import__("sys").modules, "playwright.async_api",
            type("M", (), {"async_playwright": lambda: FakePlaywright()}),
        )
        with pytest.raises(browser_module.BrowserUnavailable):
            await browser_module.fetch_session("https://example.test", locale="fr-FR,fr;q=0.9")
        assert "--no-sandbox" in (seen.get("args") or [])


class TestHeadlessMarkers:
    """Un filtre anti-bot refuse d'abord sur les marqueurs évidents : le
    User-Agent « HeadlessChrome » et navigator.webdriver."""

    def test_the_user_agent_drops_the_headless_mention(self):
        from stockwatch.browser import _visible_user_agent

        class Browser:
            version = "128.0.6613.18"

        agent = _visible_user_agent(Browser())
        assert "Headless" not in agent
        assert "Chrome/128" in agent

    def test_the_platform_stays_the_real_one(self):
        from stockwatch.browser import _visible_user_agent

        class Browser:
            version = "128.0.6613.18"

        # Chromium annonce « Linux » dans ses indices client : prétendre venir
        # d'un Mac serait une incohérence repérable.
        assert "Linux" in _visible_user_agent(Browser())

    def test_no_version_means_no_override(self):
        from stockwatch.browser import _visible_user_agent

        assert _visible_user_agent(object()) is None


class TestManualRequest:
    """Le délai anti-rafale protège la boucle automatique, pas l'utilisateur
    qui appuie lui-même sur le bouton."""

    async def test_a_forced_request_ignores_the_delay(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Chrome"))
        monitor = build(settings, config, state, fake_notifier)

        assert await monitor.refresh_cookie() is True
        assert await monitor.refresh_cookie() is False              # trop tôt
        assert await monitor.refresh_cookie(force=True) is True     # demandé à la main

    async def test_the_last_error_is_kept_for_the_user(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserUnavailable("HTTP 403 · page vide"))
        monitor = build(settings, config, state, fake_notifier)
        await monitor.refresh_cookie(force=True)
        assert "403" in (monitor.last_cookie_error or "")

    async def test_a_success_clears_the_last_error(
        self, settings, config, state, fake_notifier, monkeypatch
    ):
        fake_browser(monkeypatch, BrowserUnavailable("oups"))
        monitor = build(settings, config, state, fake_notifier)
        await monitor.refresh_cookie(force=True)
        fake_browser(monkeypatch, BrowserSession(cookie="a=1", user_agent="Chrome"))
        await monitor.refresh_cookie(force=True)
        assert monitor.last_cookie_error is None


class TestCookieInbox:
    """Quand l'adresse du serveur est refusée, une machine de confiance peut
    déposer un cookie : le bot doit le prendre sans redémarrage."""

    def _monitor(self, tmp_path, config, state, notifier, path):
        settings = StockWatchSettings(
            _env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
            STOCKWATCH_STATE_FILE=str(tmp_path / "state.json"),
            STOCKWATCH_COOKIE_FILE=str(path),
        )
        return Monitor(settings, config, FakeClient([PAGE]), notifier, state)

    async def test_a_deposited_cookie_is_adopted(self, tmp_path, config, state, fake_notifier):
        path = tmp_path / "cookie.txt"
        monitor = self._monitor(tmp_path, config, state, fake_notifier, path)

        assert await monitor.adopt_cookie_file() is False       # rien déposé
        path.write_text("ANFSession=abc; _abck=zz\n", "utf-8")
        assert await monitor.adopt_cookie_file() is True
        await monitor._drain()

        assert monitor.client.identity[0] == "ANFSession=abc; _abck=zz"
        assert state.session["cookie"] == "ANFSession=abc; _abck=zz"
        assert any("Cookie reçu" in message for message in fake_notifier.messages)

    async def test_the_same_file_is_not_adopted_twice(self, tmp_path, config, state, fake_notifier):
        path = tmp_path / "cookie.txt"
        path.write_text("a=1", "utf-8")
        monitor = self._monitor(tmp_path, config, state, fake_notifier, path)

        assert await monitor.adopt_cookie_file() is True
        assert await monitor.adopt_cookie_file() is False

    async def test_a_curl_paste_is_accepted_too(self, tmp_path, config, state, fake_notifier):
        path = tmp_path / "cookie.txt"
        path.write_text("curl 'https://x' -H 'Cookie: a=1; b=2'", "utf-8")
        monitor = self._monitor(tmp_path, config, state, fake_notifier, path)

        assert await monitor.adopt_cookie_file() is True
        assert monitor.client.identity[0] == "a=1; b=2"

    async def test_an_unusable_file_is_ignored(self, tmp_path, config, state, fake_notifier):
        path = tmp_path / "cookie.txt"
        path.write_text("bonjour", "utf-8")
        monitor = self._monitor(tmp_path, config, state, fake_notifier, path)

        assert await monitor.adopt_cookie_file() is False
