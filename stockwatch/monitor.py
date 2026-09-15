"""The watching loop: poll, diff, alert.

One probe per `poll_interval` (1 s by default). A size that flips from
"unavailable" to "available" fires a Telegram alert immediately — the send is
dispatched as a task so the next probe is not delayed by Telegram's latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .client import FetchResult, ProductClient
from .config import StockWatchSettings, WatchConfig
from .notifier import TelegramNotifier
from .parsing import ParseResult, parse_availability
from .schedule import format_window, seconds_until_wake
from .state import StateStore, utcnow

logger = logging.getLogger(__name__)


@dataclass
class Tick:
    """The outcome of a single probe."""

    at: datetime
    ok: bool
    elapsed: float
    status_code: int | None = None
    strategy: str = "none"
    sizes: dict[str, bool] = field(default_factory=dict)
    newly_available: list[str] = field(default_factory=list)
    error: str | None = None
    bytes_downloaded: int = 0
    not_modified: bool = False

    def watched_summary(self, watched: list[str]) -> str:
        parts = []
        for size in watched:
            value = self.sizes.get(size)
            icon = "✅" if value else ("❌" if value is False else "❔")
            parts.append(f"{icon} {size}")
        return "  ".join(parts) if parts else "—"


class Monitor:
    def __init__(
        self,
        settings: StockWatchSettings,
        config: WatchConfig,
        client: ProductClient,
        notifier: TelegramNotifier,
        state: StateStore,
    ) -> None:
        self.settings = settings
        self.config = config
        self.client = client
        self.notifier = notifier
        self.state = state

        self.started_at = utcnow()
        self.last_tick: Tick | None = None
        self.last_ok_at: datetime | None = None
        self.last_alert_at: datetime | None = None
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self._degraded_notified = False
        self._pending: set[asyncio.Task[object]] = set()
        self._last_sizes: dict[str, bool] = {}
        self._last_labels: dict[str, str] = {}
        self._budget_notified = False
        self._sleeping = False

    # -- one probe ---------------------------------------------------------
    async def check_once(self) -> Tick:
        url = self.settings.api_url or self.config.product_url
        result = await self.client.fetch(url)
        self.state.bump("checks")
        if result.bytes_downloaded:
            self.state.add_bytes(result.bytes_downloaded)

        if not result.ok:
            if result.blocked:
                self.state.bump("blocked")
            return self._record_failure(result, result.error or "unknown error")

        if result.not_modified:
            # Le serveur confirme que la page n'a pas bougé d'un octet : rien
            # n'a pu changer côté stock, inutile de re-télécharger ni d'analyser.
            self._recover()
            self.state.bump("not_modified")
            tick = Tick(
                at=utcnow(),
                ok=True,
                elapsed=result.elapsed,
                status_code=304,
                strategy="304-non-modifie",
                sizes=dict(self._last_sizes),
                bytes_downloaded=result.bytes_downloaded,
                not_modified=True,
            )
            self.last_tick = tick
            self.last_ok_at = tick.at
            return tick

        parsed = parse_availability(
            result.body,
            product_id=self.config.effective_product_id(),
            product_color=self.config.product_color or None,
            html_fallback=self.settings.html_fallback,
        )
        if not parsed.found:
            if parsed.strategy == "json-ambiguous-products":
                message = (
                    "la page contient plusieurs produits (coloris, recommandations) et aucun ne "
                    "correspond à l'identifiant de l'URL : impossible de savoir lequel est affiché. "
                    "Lance `python -m stockwatch diagnose` puis choisis-le avec /variante <id>"
                )
            elif parsed.strategy == "html-no-stock-state":
                message = (
                    "les tailles sont listées dans la page mais sans état de stock : "
                    "ce site charge la disponibilité en JavaScript. Lance `python -m stockwatch diagnose` "
                    "et renseigne STOCKWATCH_API_URL avec l'endpoint trouvé"
                )
            else:
                message = (
                    "page récupérée mais aucune taille lisible (structure changée ?) — "
                    "lance `python -m stockwatch diagnose`"
                )
            return self._record_failure(result, message, strategy=parsed.strategy)

        watched = list(self.config.sizes)
        if all(parsed.sizes.get(size) is None for size in watched):
            return self._record_failure(
                result,
                f"none of the watched sizes ({', '.join(watched)}) appear on the page; found: "
                f"{', '.join(sorted(parsed.sizes)) or '—'}",
                strategy=parsed.strategy,
            )

        self._recover()
        newly_available = self._diff(parsed)
        self._last_sizes = dict(parsed.sizes)
        if parsed.labels:
            self._last_labels = dict(parsed.labels)
        tick = Tick(
            at=utcnow(),
            ok=True,
            elapsed=result.elapsed,
            status_code=result.status_code,
            strategy=parsed.strategy,
            sizes=dict(parsed.sizes),
            newly_available=newly_available,
            bytes_downloaded=result.bytes_downloaded,
        )
        self.last_tick = tick
        self.last_ok_at = tick.at

        if newly_available:
            self._spawn(self._alert(newly_available, parsed, tick))
        return tick

    def _diff(self, parsed: ParseResult) -> list[str]:
        """Update the persisted state and return the sizes to alert on."""
        newly_available: list[str] = []
        changed = False
        repeat_after = (
            timedelta(minutes=self.settings.repeat_alert_minutes)
            if self.settings.repeat_alert_minutes > 0
            else None
        )

        for size in self.config.sizes:
            available = parsed.sizes.get(size)
            if available is None:
                continue  # size not present in this response — keep the last known state
            previous = self.state.get_size(size)
            should_alert = False
            if previous is None:
                should_alert = available and self.settings.alert_on_first_seen
            elif available and not previous.available:
                should_alert = True
            elif available and repeat_after is not None:
                last = previous.last_alert_at
                should_alert = last is None or utcnow() - last >= repeat_after

            if previous is None or previous.available != available or should_alert:
                changed = True
            self.state.record_size(size, available, alerted=should_alert)
            if should_alert:
                newly_available.append(size)

        if changed:
            self._spawn(self.state.save())
        return newly_available

    # -- failures ----------------------------------------------------------
    def _record_failure(self, result: FetchResult, message: str, *, strategy: str = "none") -> Tick:
        self.consecutive_errors += 1
        self.last_error = message
        self.state.bump("errors")
        tick = Tick(
            at=utcnow(),
            ok=False,
            elapsed=result.elapsed,
            status_code=result.status_code,
            strategy=strategy,
            error=message,
        )
        self.last_tick = tick
        logger.warning("Check failed (%s in a row): %s", self.consecutive_errors, message)

        threshold = max(1, self.settings.error_alert_after)
        if self.consecutive_errors >= threshold and not self._degraded_notified:
            self._degraded_notified = True
            self._spawn(
                self.notifier.broadcast(
                    "⚠️ <b>Surveillance dégradée — aucune alerte ne partira</b>\n"
                    f"{self.consecutive_errors} échecs consécutifs.\n"
                    f"Cause : <code>{html.escape(message[:300])}</code>\n\n"
                    "Je continue d'essayer avec un délai progressif, et je préviens dès que c'est rétabli. "
                    "Je préfère te dire que je ne sais pas lire le stock plutôt que t'annoncer une "
                    "disponibilité qui n'existe pas.",
                    silent=True,
                )
            )
        return tick

    def _recover(self) -> None:
        if self.consecutive_errors and self._degraded_notified:
            self._spawn(
                self.notifier.broadcast(
                    "✅ <b>Surveillance rétablie</b> — la page est de nouveau lisible.",
                    silent=True,
                )
            )
        self.consecutive_errors = 0
        self.last_error = None
        self._degraded_notified = False

    # -- alerting ----------------------------------------------------------
    async def _alert(self, sizes: list[str], parsed: ParseResult, tick: Tick) -> None:
        label = html.escape(self.config.product_label or "Produit surveillé")
        detected = tick.at.astimezone()
        size_list = ", ".join(sizes)
        others = [size for size in parsed.available_sizes() if size not in sizes]
        lines = [
            f"🚨🚨 <b>DISPO — TAILLE {html.escape(size_list)}</b> 🚨🚨",
            "",
            f"👕 {label}",
            f"✅ En stock : <b>{html.escape(size_list)}</b>",
        ]
        if others:
            lines.append(f"➕ Autres tailles dispo : {html.escape(', '.join(others))}")
        lines += [
            f"🕒 Détecté à {detected.strftime('%H:%M:%S')} (le {detected.strftime('%d/%m/%Y')})",
            f"⚡ Vérification toutes les {self.config.poll_interval:g} s",
        ]
        if parsed.strategy == "html-heuristic":
            lines.append("ℹ️ Lecture via le HTML (à vérifier sur le site).")
        lines += ["", f'<a href="{html.escape(self.config.product_url, quote=True)}">Commander maintenant</a>']

        self.last_alert_at = utcnow()
        self.state.bump("alerts")
        sent = await self.notifier.broadcast("\n".join(lines), url=self.config.product_url)
        logger.info("Alert for %s delivered to %s chat(s)", size_list, sent)
        await self.state.save()

    # -- loop --------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        logger.info(
            "Watching %s for %s every %.2fs",
            self.config.product_url,
            ", ".join(self.config.sizes),
            self.config.poll_interval,
        )
        while not stop.is_set():
            started = time.perf_counter()
            if self.config.is_quiet_now():
                self._enter_sleep()
                # On se réveille au plus tard dans une minute pour tenir compte
                # d'un changement d'horaires fait depuis Telegram entre-temps.
                delay = min(60.0, seconds_until_wake(self.config.now(), self.config.quiet_start,
                                                     self.config.quiet_end))
            else:
                self._leave_sleep()
                if not self.config.paused:
                    try:
                        await self.check_once()
                    except Exception:  # noqa: BLE001 - the loop must outlive any bug
                        logger.exception("Unexpected error during check")
                        self.consecutive_errors += 1
                delay = self._next_delay(time.perf_counter() - started)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
        await self._drain()

    # -- veille nocturne ---------------------------------------------------
    def _enter_sleep(self) -> None:
        if self._sleeping:
            return
        self._sleeping = True
        wake = self.config.now().replace(hour=self.config.quiet_end % 24, minute=0)
        logger.info("Mise en veille jusqu'à %sh", self.config.quiet_end)
        self._spawn(
            self.notifier.broadcast(
                "😴 <b>Veille nocturne</b>\n"
                f"Je ne vérifie plus jusqu'à {wake:%Hh%M}.\n"
                "<i>/veille off pour surveiller en continu.</i>",
                silent=True,
            )
        )

    def _leave_sleep(self) -> None:
        if not self._sleeping:
            return
        self._sleeping = False
        logger.info("Fin de veille, reprise de la surveillance")
        self._spawn(
            self.notifier.broadcast(
                "☀️ <b>Surveillance reprise</b>\n"
                f"Une vérification toutes les {self.config.poll_interval:g} s.",
                silent=True,
            )
        )

    @property
    def sleeping(self) -> bool:
        return self._sleeping or self.config.is_quiet_now()

    @property
    def last_labels(self) -> dict[str, str]:
        """Coloris vus lors de la dernière lecture — sert aux boutons."""
        return dict(self._last_labels)

    def _next_delay(self, elapsed: float) -> float:
        """Keep a steady cadence, but back off while failing or over budget."""
        if self.config.paused:
            return max(1.0, self.config.poll_interval)
        if self.consecutive_errors:
            over = self.consecutive_errors - max(0, self.settings.failure_grace)
            if over > 0:
                penalty = self.config.poll_interval * (2 ** min(over, 8))
                return min(self.settings.max_backoff, max(self.config.poll_interval, penalty))
            # Dans la fenêtre de tolérance : on garde la cadence, la requête
            # suivante a de bonnes chances de passer.
        if self.over_budget():
            self._warn_budget_once()
            return max(self.config.poll_interval, self.settings.throttled_interval)
        return max(0.0, self.config.poll_interval - elapsed)

    # -- bande passante ----------------------------------------------------
    def bytes_today(self) -> int:
        return int(self.state.stats.get("bytes_today", 0))

    def over_budget(self) -> bool:
        budget = self.settings.daily_budget_mb
        return budget > 0 and self.bytes_today() >= budget * 1_000_000

    def _warn_budget_once(self) -> None:
        if self._budget_notified:
            return
        self._budget_notified = True
        self._spawn(
            self.notifier.broadcast(
                "🐢 <b>Budget de données atteint</b>\n"
                f"{_human_bytes(self.bytes_today())} téléchargés aujourd'hui "
                f"(plafond : {self.settings.daily_budget_mb:g} Mo).\n"
                f"Je ralentis à une vérification toutes les "
                f"{self.settings.throttled_interval:g} s jusqu'à minuit UTC "
                "plutôt que de faire grimper la facture.",
                silent=True,
            )
        )

    # -- housekeeping ------------------------------------------------------
    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _drain(self) -> None:
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    # -- introspection (used by /status) -----------------------------------
    def _sleep_line(self) -> str:
        if not self.config.quiet_enabled:
            return "🌙 Veille : désactivée"
        window = format_window(self.config.quiet_start, self.config.quiet_end)
        if self.sleeping:
            reprise = seconds_until_wake(self.config.now(), self.config.quiet_start, self.config.quiet_end)
            return f"😴 En veille ({window}) — reprise dans {_humanize(timedelta(seconds=reprise))}"
        return f"🌙 Veille : {window} ({self.config.timezone})"

    def status_lines(self) -> list[str]:
        now = utcnow()
        uptime = now - self.started_at
        lines = [
            "📊 <b>État de la surveillance</b>",
            "",
            f"👕 Produit : {html.escape(self.config.product_label or 'Produit surveillé')}",
            f"🎯 Tailles : <b>{html.escape(', '.join(self.config.sizes))}</b>"
            + (f"  •  🎨 {html.escape(self.config.product_color)}" if self.config.product_color else ""),
            f"⚡ Intervalle : {self.config.poll_interval:g} s"
            + ("  (⏸ en pause)" if self.config.paused else ""),
            f"⏱ Actif depuis : {_humanize(uptime)}",
            self._sleep_line(),
            f"🔁 Vérifications : {self.state.stats.get('checks', 0)}  •  "
            f"Alertes : {self.state.stats.get('alerts', 0)}  •  Erreurs : {self.state.stats.get('errors', 0)}",
        ]
        if self.last_tick is not None:
            lines.append(f"🕒 Dernier check : {self.last_tick.at.astimezone().strftime('%H:%M:%S')} "
                         f"({self.last_tick.elapsed * 1000:.0f} ms)")
            lines.append(f"📦 Stock : {self.last_tick.watched_summary(self.config.sizes)}")
            if self.last_tick.sizes:
                dispo = ", ".join(sorted(s for s, ok in self.last_tick.sizes.items() if ok)) or "aucune"
                lines.append(f"🛒 Toutes tailles dispo : {html.escape(dispo)}")
        else:
            lines.append("🕒 Aucune vérification effectuée pour l'instant.")
        if self.last_error:
            lines.append(f"⚠️ Dernière erreur : <code>{html.escape(self.last_error[:250])}</code> "
                         f"({self.consecutive_errors} d'affilée)")
        if self.last_alert_at:
            lines.append(f"🚨 Dernière alerte : {self.last_alert_at.astimezone().strftime('%d/%m %H:%M:%S')}")
        checks = max(1, int(self.state.stats.get("checks", 0)))
        unchanged = int(self.state.stats.get("not_modified", 0))
        blocked = int(self.state.stats.get("blocked", 0))
        if blocked:
            lines.append(
                f"🚧 Refusées par le site : {blocked} ({blocked * 100 // checks} %) — "
                "je réessaie à la cadence normale"
            )
        today = self.bytes_today()
        lines.append(
            f"📡 Données : {_human_bytes(today)} aujourd'hui "
            f"(≈ {_human_bytes(today * 30)}/mois) • {unchanged * 100 // checks} % de réponses « inchangé »"
        )
        if self.settings.daily_budget_mb > 0:
            state = "⚠️ atteint, cadence réduite" if self.over_budget() else "ok"
            lines.append(f"🎚 Budget : {self.settings.daily_budget_mb:g} Mo/jour ({state})")
        lines.append(f"👥 Abonnés : {len(self.notifier.recipients())}")
        return lines


def _human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def _humanize(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days} j {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


__all__ = ["Monitor", "Tick", "UTC", "datetime"]
