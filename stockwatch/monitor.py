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

from . import keyboards
from .browser import BrowserUnavailable, fetch_session
from .client import FetchResult, ProductClient
from .config import StockWatchSettings, WatchConfig
from .cookie import extract_cookie
from .notifier import TelegramNotifier
from .parsing import ParseResult, owners_matching_colour, parse_availability
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
        self._typical_body = 0
        self._cookie_lock = asyncio.Lock()
        self._last_cookie_attempt: datetime | None = None
        self._browser_missing_notified = False
        self.last_cookie_error: str | None = None
        self._cookie_file_seen: float = 0.0
        self._cookie_expiry_notified = False
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

        if result.not_modified and not self._last_sizes:
            # « Inchangée » ne sert à rien tant qu'on n'a jamais su la lire : le
            # serveur confirme seulement qu'une réponse inexploitable est la
            # même. On oublie l'ETag et on retélécharge tout de suite.
            self.client.forget_validators(url)
            result = await self.client.fetch(url)
            self.state.bump("checks")
            if result.bytes_downloaded:
                self.state.add_bytes(result.bytes_downloaded)
            if not result.ok:
                return self._record_failure(result, result.error or "unknown error")
            if result.not_modified:
                return self._record_failure(
                    result,
                    "le site répond « inchangé » alors qu'aucune lecture n'a jamais abouti",
                    strategy="304-sans-lecture",
                )

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

        parsed = self._parse(result)

        # Le site sert parfois une variante allégée de la page, sans les données
        # de stock. Ce n'est ni un blocage ni un changement de structure : la
        # requête suivante ramène en général la version complète. On la tente
        # tout de suite plutôt que de perdre le tour — une seule fois, et
        # seulement après une lecture réussie, pour ne pas doubler le trafic
        # quand le site est réellement en panne.
        if not parsed.found and self._typical_body and self.consecutive_errors == 0:
            retry = await self.client.fetch(url)
            self.state.bump("checks")
            if retry.bytes_downloaded:
                self.state.add_bytes(retry.bytes_downloaded)
            if retry.ok and not retry.not_modified:
                retried = self._parse(retry)
                if retried.found:
                    logger.info(
                        "Page incomplète (%s Ko) ignorée, la seconde tentative a réussi (%s Ko)",
                        len(result.body) // 1024, len(retry.body) // 1024,
                    )
                    result, parsed = retry, retried
                    self.state.bump("partial_pages")

        if not parsed.found:
            # « Anormalement courte » se juge par rapport aux pages déjà lues,
            # pas dans l'absolu : une réponse cinq fois plus petite que d'habitude
            # est un contrôle anti-bot servi en 200, pas une fiche produit.
            if self._typical_body > 50_000 and len(result.body) < self._typical_body // 5:
                return self._record_failure(
                    result,
                    f"réponse anormalement courte ({len(result.body)} caractères) pour une fiche "
                    "produit : le site a probablement servi une page de contrôle. Réessaie, "
                    "ajoute un cookie de navigateur (STOCKWATCH_COOKIE) ou espace les vérifications",
                    strategy=parsed.strategy,
                )
            if self.config.product_color and parsed.labels and not _colour_present(
                parsed.labels, self.config.product_color
            ):
                known = ", ".join(sorted(set(parsed.labels.values()))[:8]) or "aucun"
                message = (
                    f"le coloris « {self.config.product_color} » n'apparaît plus dans la page. "
                    f"Coloris détectés : {known}. Choisis-en un avec /menu → 🎨 Coloris"
                )
            elif parsed.strategy == "json-ambiguous-products":
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
                size = len(result.body) // 1024
                message = (
                    f"page récupérée (HTTP {result.status_code}, {size} Ko) mais aucune taille "
                    "lisible — lance `python -m stockwatch diagnose`"
                )
                if self._typical_body and len(result.body) < self._typical_body * 0.8:
                    message += (
                        f" (page habituellement de {self._typical_body // 1024} Ko : le site a "
                        "servi une version allégée, sans les données de stock)"
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
        self._typical_body = max(self._typical_body, len(result.body))
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

    def _parse(self, result: FetchResult) -> ParseResult:
        return parse_availability(
            result.body,
            product_id=self.config.effective_product_id(),
            product_color=self.config.product_color or None,
            html_fallback=self.settings.html_fallback,
        )

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

        # Un refus isolé n'est pas une expiration : on attend la fenêtre de
        # tolérance, celle-là même qui absorbe les refus intermittents.
        if self.consecutive_errors >= max(2, self.settings.failure_grace) and \
                self._looks_like_an_expired_cookie(result):
            self._warn_cookie_expired()

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

    # -- cookie expiré -----------------------------------------------------
    def _has_cookie(self) -> bool:
        return bool(self.settings.cookie or self.state.session.get("cookie"))

    def _looks_like_an_expired_cookie(self, result: FetchResult) -> bool:
        """La signature d'une session qui n'est plus reconnue.

        Le site ne dit jamais « ton cookie a expiré » : il refuse la requête, ou
        sert une version amputée de la page. Les deux signifient la même chose
        quand une session était en place.
        """
        if not self._has_cookie():
            return False
        if result.blocked or result.status_code in (401, 403, 418, 429):
            return True
        return bool(self._typical_body and 0 < len(result.body) < self._typical_body * 0.8)

    def _warn_cookie_expired(self) -> None:
        if self._cookie_expiry_notified:
            return
        self._cookie_expiry_notified = True
        age = self.state.session_age_minutes()
        since = f" (obtenu il y a {_humanize(timedelta(minutes=age))})" if age is not None else ""
        self._spawn(
            self.notifier.broadcast(
                "🍪 <b>Cookie expiré</b>\n"
                f"Le site ne reconnaît plus la session{since} : il refuse la requête ou "
                "sert une page amputée, et je ne peux plus lire le stock.\n\n"
                "<b>Aucune alerte ne partira tant qu'il n'est pas renouvelé.</b>\n\n"
                "Appuie sur le bouton ci-dessous ; si le serveur ne peut pas en obtenir "
                "lui-même, dépose-en un depuis ton ordinateur "
                "(<code>bash deploy/mac-cookie-courier.sh</code>).",
                markup=keyboards.cookie_alert(),
            )
        )

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
        self._cookie_expiry_notified = False

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
                await self.adopt_cookie_file()
                if not self.config.paused:
                    try:
                        tick = await self.check_once()
                    except Exception:  # noqa: BLE001 - the loop must outlive any bug
                        logger.exception("Unexpected error during check")
                        self.consecutive_errors += 1
                    else:
                        # Un cookie périmé se voit à l'échec de lecture : c'est
                        # le meilleur moment pour en chercher un neuf.
                        if not tick.ok:
                            await self.refresh_cookie("lecture impossible")
                        elif self._cookie_is_stale():
                            await self.refresh_cookie("renouvellement préventif")
                delay = self._next_delay(time.perf_counter() - started)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
        await self._drain()

    # -- cookie automatique ------------------------------------------------
    async def adopt_cookie_file(self) -> bool:
        """Prendre en compte un cookie déposé par une autre machine.

        Quand l'adresse du serveur est refusée mais qu'une machine de confiance
        (un ordinateur personnel, par exemple) peut en obtenir un, il suffit de
        déposer le fichier : le bot le relit dès qu'il change, sans redémarrage
        ni intervention.
        """
        path = self.settings.cookie_file
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return False
        if stamp <= self._cookie_file_seen:
            return False
        self._cookie_file_seen = stamp

        cookie = extract_cookie(path.read_text("utf-8", errors="replace"))
        if not cookie:
            logger.warning("%s ne contient pas de cookie exploitable", path)
            return False

        self.client.set_identity(cookie, None)
        await self.state.remember_session(cookie, self.settings.user_agent)
        self._typical_body = 0
        self.state.bump("cookies_renewed")
        self.last_cookie_error = None
        logger.info("Cookie repris depuis %s", path)
        self._spawn(
            self.notifier.broadcast(
                "🍪 <b>Cookie reçu</b>\nDéposé par une autre machine — la surveillance "
                "repart avec une session neuve.",
                silent=True,
            )
        )
        return True

    def _cookie_is_stale(self) -> bool:
        if not self.settings.auto_cookie or self.settings.cookie_refresh_minutes <= 0:
            return False
        age = self.state.session_age_minutes()
        return age is None or age >= self.settings.cookie_refresh_minutes

    async def refresh_cookie(self, reason: str = "", *, force: bool = False) -> bool:
        """Aller chercher un cookie neuf avec un navigateur headless.

        Un vrai navigateur obtient un cookie valide à chaque visite — c'est
        l'objet même du contrôle anti-bot. Les milliers de vérifications qui
        suivent restent de simples requêtes HTTP.

        `force` ignore le délai entre deux tentatives : ce délai existe pour ne
        pas lancer un navigateur à chaque vérification ratée, pas pour refuser
        un appui volontaire sur un bouton.
        """
        if not self.settings.auto_cookie or self._cookie_lock.locked():
            return False
        async with self._cookie_lock:
            now = utcnow()
            if not force and self._last_cookie_attempt is not None:
                since = (now - self._last_cookie_attempt).total_seconds() / 60
                if since < self.settings.cookie_retry_minutes:
                    return False
            self._last_cookie_attempt = now

            logger.info("Renouvellement du cookie (%s)…", reason or "demandé")
            try:
                proxy = self.settings.browser_proxy_url or self.settings.proxy_url
                session = await fetch_session(
                    self.config.product_url,
                    locale=self.settings.accept_language,
                    proxy=proxy.get_secret_value() if proxy else None,
                )
            except BrowserUnavailable as exc:
                logger.warning("Cookie non renouvelé : %s", exc)
                self.last_cookie_error = str(exc)
                self._warn_browser_missing(str(exc))
                return False
            except Exception as exc:  # noqa: BLE001 - un échec ici ne doit pas tuer la boucle
                logger.warning("Cookie non renouvelé : %s", exc)
                self.last_cookie_error = f"{type(exc).__name__}: {exc}"
                return False

            self.client.set_identity(session.cookie, session.user_agent)
            await self.state.remember_session(session.cookie, session.user_agent)
            # La taille « habituelle » d'une page était celle vue sous l'ancienne
            # identité : on repart sur une base neuve.
            self._typical_body = 0
            self.state.bump("cookies_renewed")
            self.last_cookie_error = None
            logger.info("Cookie renouvelé : %s", session.summary())
            self._spawn(
                self.notifier.broadcast(
                    f"🍪 <b>Cookie renouvelé</b> ({html.escape(reason or 'demandé')})\n"
                    f"{session.summary()} — la surveillance reprend normalement.",
                    silent=True,
                )
            )
            return True

    def _warn_browser_missing(self, detail: str) -> None:
        if self._browser_missing_notified:
            return
        self._browser_missing_notified = True
        self._spawn(
            self.notifier.broadcast(
                "🍪 <b>Renouvellement automatique indisponible</b>\n"
                f"<code>{html.escape(detail[:300])}</code>\n\n"
                "Tant qu'il n'est pas installé, le cookie doit être renouvelé à la main "
                "(<code>python -m stockwatch cookie</code>).",
                silent=True,
            )
        )

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
        partial = int(self.state.stats.get("partial_pages", 0))
        if partial:
            lines.append(f"🧩 Pages incomplètes rattrapées : {partial}")
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
        age = self.state.session_age_minutes()
        if age is not None:
            renewed = int(self.state.stats.get("cookies_renewed", 0))
            lines.append(f"🍪 Cookie : obtenu il y a {_humanize(timedelta(minutes=age))} "
                         f"({renewed} renouvellement{'s' if renewed > 1 else ''})")
        lines.append(f"👥 Abonnés : {len(self.notifier.recipients())}")
        return lines


def _colour_present(labels: dict[str, str], wanted: str) -> bool:
    return bool(owners_matching_colour(labels, wanted))


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
