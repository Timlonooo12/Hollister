"""Alerting rules: fire on the transition, exactly once, and never go quiet
when the page stops being readable."""

from __future__ import annotations

import asyncio
import json

import pytest

from stockwatch.client import FetchResult
from stockwatch.monitor import Monitor
from tests.conftest import FakeClient, FakeNotifier


def page(**sizes: bool) -> str:
    variants = [{"size": size, "inStock": available} for size, available in sizes.items()]
    payload = {"product": {"productId": "63586319", "variants": variants}}
    return "<script>window.__INITIAL_STATE__ = " + json.dumps(payload) + ";</script>"


def build(settings, config, state, notifier, bodies) -> Monitor:
    return Monitor(settings, config, FakeClient(bodies), notifier, state)


async def drain(monitor: Monitor) -> None:
    await monitor._drain()


class TestTransitions:
    async def test_alerts_when_a_size_comes_back(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier,
                        [page(XS=False, S=False), page(XS=True, S=False)])
        first = await monitor.check_once()
        assert first.ok and first.newly_available == []

        second = await monitor.check_once()
        await drain(monitor)
        assert second.newly_available == ["XS"]
        assert len(fake_notifier.messages) == 1
        assert "XS" in fake_notifier.messages[0]
        assert "DISPO" in fake_notifier.messages[0]

    async def test_alerts_once_while_it_stays_in_stock(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier,
                        [page(XS=False, S=False), page(XS=True, S=False), page(XS=True, S=False)])
        for _ in range(3):
            await monitor.check_once()
        await drain(monitor)
        assert len(fake_notifier.messages) == 1

    async def test_realerts_after_a_new_restock(self, settings, config, state, fake_notifier):
        bodies = [page(XS=True), page(XS=False), page(XS=True)]
        monitor = build(settings, config, state, fake_notifier, bodies)
        for _ in range(3):
            await monitor.check_once()
        await drain(monitor)
        assert len(fake_notifier.messages) == 2

    async def test_first_observation_in_stock_alerts(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [page(XS=True, S=True)])
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.newly_available == ["XS", "S"]
        assert len(fake_notifier.messages) == 1

    async def test_first_observation_can_stay_silent(self, settings, config, state, fake_notifier):
        settings.alert_on_first_seen = False
        monitor = build(settings, config, state, fake_notifier, [page(XS=True)])
        await monitor.check_once()
        await drain(monitor)
        assert fake_notifier.messages == []

    async def test_unwatched_sizes_never_alert(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [page(XS=False, S=False, M=True)])
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.newly_available == []
        assert fake_notifier.messages == []
        assert tick.sizes["M"] is True

    async def test_state_survives_a_restart(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [page(XS=True)])
        await monitor.check_once()
        await drain(monitor)
        await state.save()

        from stockwatch.state import StateStore

        reloaded = StateStore(settings.state_file)
        reloaded.load()
        notifier = FakeNotifier()
        again = build(settings, config, reloaded, notifier, [page(XS=True)])
        await again.check_once()
        await drain(again)
        assert notifier.messages == []  # already alerted before the restart


class TestFailures:
    async def test_block_page_is_an_error_not_an_out_of_stock(self, settings, config, state, fake_notifier):
        blocked = FetchResult(url="u", status_code=403, body="Access Denied", elapsed=0.01,
                              error="HTTP 403", blocked=True)
        monitor = build(settings, config, state, fake_notifier, [page(XS=True), blocked])
        await monitor.check_once()
        fake_notifier.messages.clear()
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.ok is False
        # The remembered state must not flip to "out of stock" on a block page.
        assert state.get_size("XS").available is True

    async def test_unreadable_page_is_reported(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, ["<html>nothing useful</html>"])
        tick = await monitor.check_once()
        assert tick.ok is False
        assert "aucune taille lisible" in (tick.error or "")

    async def test_sizes_without_stock_state_never_alert(self, settings, config, state, fake_notifier):
        """Regression: a page listing sizes with no stock state (rendered in
        JavaScript) once produced "XS, S disponibles" for a sold-out product."""
        body = """
        <div class="size-selector">
          <button data-size="XS">XS</button>
          <button data-size="S">S</button>
          <button data-size="M">M</button>
        </div>
        """
        monitor = build(settings, config, state, fake_notifier, [body])
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.ok is False
        assert tick.sizes == {}
        assert fake_notifier.messages == []
        assert "JavaScript" in (tick.error or "")

    async def test_several_colourways_never_alert_for_the_wrong_one(self, settings, config, state, fake_notifier):
        payload = {"products": [
            {"productId": "111", "skus": [{"sizePrimary": "XS_p", "inventory": 0,
                                           "inventoryStatus": "Unavailable"}]},
            {"productId": "222", "skus": [{"sizePrimary": "XS_p", "inventory": 9,
                                           "inventoryStatus": "InStock"}]},
        ]}
        body = "<script>window.__INITIAL_STATE__ = " + json.dumps(payload) + ";</script>"
        monitor = build(settings, config, state, fake_notifier, [body])
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.ok is False
        assert fake_notifier.messages == []
        assert "/variante" in (tick.error or "")

    async def test_naming_the_colourway_makes_it_readable(self, settings, config, state, fake_notifier):
        payload = {"products": [
            {"productId": "111", "skus": [{"sizePrimary": "XS_p", "inventory": 0,
                                           "inventoryStatus": "Unavailable"}]},
            {"productId": "222", "skus": [{"sizePrimary": "XS_p", "inventory": 9,
                                           "inventoryStatus": "InStock"}]},
        ]}
        body = "<script>window.__INITIAL_STATE__ = " + json.dumps(payload) + ";</script>"
        config.product_id = "222"
        monitor = build(settings, config, state, fake_notifier, [body])
        tick = await monitor.check_once()
        await drain(monitor)
        assert tick.ok is True
        assert tick.newly_available == ["XS"]

    async def test_repeated_unreadable_pages_warn_the_owner(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, ['<button data-size="XS">XS</button>'])
        for _ in range(3):
            await monitor.check_once()
        await drain(monitor)
        assert any("dégradée" in message for message in fake_notifier.messages)
        assert not any("DISPO" in message for message in fake_notifier.messages)

    async def test_owner_is_warned_after_repeated_failures(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, ["<html></html>"])
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)
        warnings = [m for m in fake_notifier.messages if "dégradée" in m]
        assert len(warnings) == 1  # warned once, not on every failure

    async def test_recovery_is_announced(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier,
                        ["<html></html>", "<html></html>", page(XS=False)])
        for _ in range(3):
            await monitor.check_once()
        await drain(monitor)
        assert any("rétablie" in m for m in fake_notifier.messages)

    async def test_backoff_grows_then_resets(self, settings, config, state, fake_notifier):
        settings.failure_grace = 0   # ralentissement dès le premier échec
        monitor = build(settings, config, state, fake_notifier, ["<html></html>"])
        assert monitor._next_delay(0.0) == pytest.approx(config.poll_interval)
        await monitor.check_once()
        first = monitor._next_delay(0.0)
        await monitor.check_once()
        second = monitor._next_delay(0.0)
        assert second > first
        assert second <= settings.max_backoff
        monitor._recover()
        assert monitor._next_delay(0.0) == pytest.approx(config.poll_interval)


class TestStatus:
    async def test_status_lines_render(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [page(XS=False, S=True)])
        await monitor.check_once()
        await drain(monitor)
        text = "\n".join(monitor.status_lines())
        assert "Tailles" in text and "XS" in text and "Abonnés" in text


class TestIntermittentBlocking:
    """Un CDN qui refuse une requête sur trois n'est pas une panne : ralentir
    ferait rater le réassort que le bot est censé attraper."""

    def _blocked(self):
        return FetchResult(url="u", status_code=403, body="Access Denied", elapsed=0.01,
                           error="HTTP 403", blocked=True)

    async def test_cadence_is_kept_during_the_grace_window(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [self._blocked()])
        for _ in range(3):
            await monitor.check_once()
        assert monitor.consecutive_errors == 3
        assert monitor._next_delay(0.0) == pytest.approx(config.poll_interval)

    async def test_backoff_starts_past_the_grace_window(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, [self._blocked()])
        for _ in range(5):
            await monitor.check_once()
        assert monitor._next_delay(0.0) > config.poll_interval

    async def test_a_success_between_blocks_resets_everything(self, settings, config, state, fake_notifier):
        bodies = [self._blocked(), self._blocked(), page(XS=False, S=False), self._blocked()]
        monitor = build(settings, config, state, fake_notifier, bodies)
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)
        assert monitor.consecutive_errors == 1
        assert monitor._next_delay(0.0) == pytest.approx(config.poll_interval)
        assert state.stats["blocked"] == 3


class TestQuietHours:
    def _cover_now(self, config) -> None:
        hour = config.now().hour
        config.quiet_start, config.quiet_end = hour, (hour + 1) % 24

    async def test_nothing_is_fetched_during_the_window(self, settings, config, state, fake_notifier):
        self._cover_now(config)
        client = FakeClient([page(XS=True, S=True)])
        monitor = Monitor(settings, config, client, fake_notifier, state)

        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

        assert client.calls == []                     # aucune requête
        assert not any("DISPO" in m for m in fake_notifier.messages)
        assert any("Veille nocturne" in m for m in fake_notifier.messages)

    async def test_an_on_demand_check_still_works_while_asleep(self, settings, config, state, fake_notifier):
        self._cover_now(config)
        monitor = build(settings, config, state, fake_notifier, [page(XS=False, S=False)])
        tick = await monitor.check_once()
        assert tick.ok is True                        # le bouton « Vérifier » reste utile

    async def test_the_window_is_reported(self, settings, config, state, fake_notifier):
        self._cover_now(config)
        monitor = build(settings, config, state, fake_notifier, [page(XS=False)])
        assert monitor.sleeping is True
        assert "En veille" in "\n".join(monitor.status_lines())

    async def test_watching_resumes_outside_the_window(self, settings, config, state, fake_notifier):
        hour = config.now().hour
        config.quiet_start, config.quiet_end = (hour + 2) % 24, (hour + 3) % 24
        client = FakeClient([page(XS=False, S=False)])
        monitor = Monitor(settings, config, client, fake_notifier, state)

        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

        assert client.calls                            # il a bien vérifié
        assert monitor.sleeping is False


class TestFailureMessages:
    """Un message d'échec doit dire ce que le serveur a renvoyé : sans ça,
    impossible de distinguer un contrôle anti-bot d'un site qui a changé."""

    async def test_the_message_carries_the_status_and_the_size(self, settings, config, state, fake_notifier):
        monitor = build(settings, config, state, fake_notifier, ["<html>" + "x" * 60_000 + "</html>"])
        tick = await monitor.check_once()
        assert "HTTP 200" in (tick.error or "")
        assert "Ko" in (tick.error or "")

    async def test_a_response_far_shorter_than_usual_is_called_out(
        self, settings, config, state, fake_notifier
    ):
        big = page(XS=False, S=False) + " " * 200_000
        monitor = build(settings, config, state, fake_notifier, [big, "<html>trop court</html>"])
        await monitor.check_once()                      # apprend la taille habituelle
        tick = await monitor.check_once()
        assert "contrôle" in (tick.error or "")

    async def test_a_missing_colour_names_the_ones_found(self, settings, config, state, fake_notifier):
        body = "<script>window['APOLLO_STATE__x'] = " + json.dumps({"CACHE": {
            "Product:1": {"productId": "1", "colorName": "Vert sauge", "skus": [
                {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"}]},
            "Product:2": {"productId": "2", "colorName": "Marine", "skus": [
                {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"}]},
        }}) + ";</script>"
        config.product_color = "Blanc"
        monitor = build(settings, config, state, fake_notifier, [body])
        tick = await monitor.check_once()
        assert "Blanc" in (tick.error or "")
        assert "Vert sauge" in (tick.error or "") and "Marine" in (tick.error or "")


class TestIncompletePages:
    """Le site sert parfois une variante allégée, sans les données de stock :
    perdre le tour pour ça, c'est rater un réassort pour rien."""

    async def test_a_second_attempt_is_made_and_succeeds(self, settings, config, state, fake_notifier):
        complete = page(XS=False, S=False) + " " * 200_000
        light = "<html>version allégée, sans stock</html>"
        client = FakeClient([complete, light, complete])
        monitor = Monitor(settings, config, client, fake_notifier, state)

        await monitor.check_once()                    # apprend la page complète
        tick = await monitor.check_once()             # allégée, puis nouvelle tentative
        await drain(monitor)

        assert tick.ok is True
        assert tick.sizes == {"XS": False, "S": False}
        assert len(client.calls) == 3
        assert state.stats["partial_pages"] == 1

    async def test_the_retry_happens_only_once(self, settings, config, state, fake_notifier):
        complete = page(XS=False, S=False) + " " * 200_000
        client = FakeClient([complete, "<html>vide</html>"])
        monitor = Monitor(settings, config, client, fake_notifier, state)

        await monitor.check_once()
        before = len(client.calls)
        await monitor.check_once()                    # échec + une seule reprise
        assert len(client.calls) == before + 2
        await monitor.check_once()                    # déjà en échec : pas de reprise
        assert len(client.calls) == before + 3

    async def test_the_message_names_the_lighter_page(self, settings, config, state, fake_notifier):
        complete = page(XS=False) + " " * 400_000
        light = "<html>" + "x" * 1000 + "</html>"
        monitor = Monitor(settings, config, FakeClient([complete, light]), fake_notifier, state)
        await monitor.check_once()
        tick = await monitor.check_once()
        assert "allégée" in (tick.error or "") or "contrôle" in (tick.error or "")


class TestExpiredCookieAlert:
    """Un cookie expiré arrête la surveillance sans bruit : le site refuse ou
    sert une page amputée, mais ne dit jamais pourquoi."""

    def _with_cookie(self, settings, tmp_path=None):
        from stockwatch.config import StockWatchSettings

        return StockWatchSettings(
            _env_file=None, STOCKWATCH_BOT_TOKEN="1:x",
            STOCKWATCH_PRODUCT_URL=settings.product_url,
            STOCKWATCH_STATE_FILE=str(settings.state_file),
            STOCKWATCH_COOKIE="ANFSession=abc",
            STOCKWATCH_FAILURE_GRACE="3",
        )

    def _blocked(self, status=403):
        return FetchResult(url="u", status_code=status, body="", elapsed=0.01,
                           error=f"HTTP {status}", blocked=True)

    async def test_repeated_refusals_name_the_cookie(self, settings, config, state, fake_notifier):
        settings = self._with_cookie(settings)
        monitor = Monitor(settings, config, FakeClient([self._blocked(418)]), fake_notifier, state)
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)

        alerts = [m for m in fake_notifier.messages if "Cookie expiré" in m]
        assert len(alerts) == 1
        assert "Aucune alerte ne partira" in alerts[0]

    async def test_a_single_refusal_is_not_an_expiry(self, settings, config, state, fake_notifier):
        settings = self._with_cookie(settings)
        monitor = Monitor(settings, config, FakeClient([self._blocked()]), fake_notifier, state)
        await monitor.check_once()
        await drain(monitor)
        assert not any("Cookie expiré" in m for m in fake_notifier.messages)

    async def test_an_amputated_page_counts_as_an_expiry(self, settings, config, state, fake_notifier):
        settings = self._with_cookie(settings)
        complete = page(XS=False, S=False) + " " * 300_000
        light = "<html>" + "x" * 1_000 + "</html>"
        monitor = Monitor(settings, config, FakeClient([complete, light]), fake_notifier, state)
        await monitor.check_once()
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)
        assert any("Cookie expiré" in m for m in fake_notifier.messages)

    async def test_without_a_cookie_nothing_is_blamed_on_it(self, settings, config, state, fake_notifier):
        monitor = Monitor(settings, config, FakeClient([self._blocked()]), fake_notifier, state)
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)
        assert not any("Cookie expiré" in m for m in fake_notifier.messages)

    async def test_a_successful_read_re_arms_the_alert(self, settings, config, state, fake_notifier):
        settings = self._with_cookie(settings)
        bodies = [self._blocked(), self._blocked(), self._blocked(), self._blocked(),
                  page(XS=False, S=False), self._blocked(), self._blocked(),
                  self._blocked(), self._blocked()]
        monitor = Monitor(settings, config, FakeClient(bodies), fake_notifier, state)
        for _ in range(len(bodies)):
            await monitor.check_once()
        await drain(monitor)
        assert len([m for m in fake_notifier.messages if "Cookie expiré" in m]) == 2

    async def test_the_alert_carries_a_renewal_button(self, settings, config, state, fake_notifier):
        settings = self._with_cookie(settings)
        monitor = Monitor(settings, config, FakeClient([self._blocked(418)]), fake_notifier, state)
        for _ in range(4):
            await monitor.check_once()
        await drain(monitor)

        index = next(i for i, m in enumerate(fake_notifier.messages) if "Cookie expiré" in m)
        markup = fake_notifier.markups[index]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        assert any("Renouveler" in label for label in labels)
