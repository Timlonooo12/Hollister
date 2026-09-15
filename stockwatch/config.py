"""Configuration for the stock watcher, loaded from environment / `.env`.

Every setting is prefixed with `STOCKWATCH_` so the file can live next to the
Eldorado bot's own `.env` without any clash. Only `STOCKWATCH_BOT_TOKEN` is
mandatory; everything else defaults to the Hollister product this bot was
built for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .parsing import normalize_size, product_id_from_url
from .schedule import is_quiet, resolve_timezone

# The product the bot was originally built for (Hollister "Icon Henley").
# Query parameters from the shared link are stripped: they only carry the
# gallery position and a marketing campaign id, never the stock state.
DEFAULT_PRODUCT_URL = "https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2"
DEFAULT_SIZES = "XS,S"

# A plain desktop Chrome fingerprint. Hollister sits behind Akamai: an obvious
# bot User-Agent gets a 403 within a handful of requests.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _split_csv(raw: str) -> list[str]:
    return [chunk.strip() for chunk in raw.replace(";", ",").split(",") if chunk.strip()]


def label_from_url(url: str) -> str:
    """`.../p/icon-henley-63586319-2` -> `Icon Henley`.

    Used when no STOCKWATCH_PRODUCT_LABEL is given, so alerts still name the
    product instead of a generic placeholder.
    """
    slug = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    words = [word for word in slug.split("-") if word and not word.isdigit()]
    return " ".join(word.capitalize() for word in words) or "Produit surveillé"


class StockWatchSettings(BaseSettings):
    """Immutable, process-wide configuration.

    Values a Telegram command may change at runtime (watched sizes, URL, poll
    interval) are copied into a :class:`WatchConfig` — this object stays the
    boot-time baseline.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: SecretStr = Field(..., alias="STOCKWATCH_BOT_TOKEN")
    # Chats alerted even if they never sent /start. Comma separated.
    chat_ids_raw: str = Field(default="", alias="STOCKWATCH_CHAT_IDS")
    # When set, only this user may change the watched product/sizes/interval.
    owner_id: int | None = Field(default=None, alias="STOCKWATCH_OWNER_ID")

    # --- What to watch ---
    product_url: str = Field(default=DEFAULT_PRODUCT_URL, alias="STOCKWATCH_PRODUCT_URL")
    product_label: str = Field(default="", alias="STOCKWATCH_PRODUCT_LABEL")
    sizes_raw: str = Field(default=DEFAULT_SIZES, alias="STOCKWATCH_SIZES")
    # A product page also carries its other colourways. When the page's own id
    # for the colour you want differs from the one in the URL, name it here
    # (`diagnose` prints the candidates).
    product_id: str = Field(default="", alias="STOCKWATCH_PRODUCT_ID")
    # Plus simple que l'identifiant : le nom du coloris tel qu'il s'affiche sur
    # la fiche (« Blanc »). Prioritaire sur product_id quand les deux sont là.
    product_color: str = Field(default="", alias="STOCKWATCH_PRODUCT_COLOR")
    # Optional JSON endpoint. Left empty the watcher reads the product page and
    # digs the state out of the embedded JSON, which needs no guessing.
    api_url: str = Field(default="", alias="STOCKWATCH_API_URL")

    # --- Cadence ---
    # "À la seconde": one probe per second by default. Going below ~0.5s buys
    # nothing (the CDN caches for longer than that) and gets you blocked.
    poll_interval: float = Field(default=1.0, alias="STOCKWATCH_POLL_INTERVAL")
    request_timeout: float = Field(default=8.0, alias="STOCKWATCH_REQUEST_TIMEOUT")
    max_backoff: float = Field(default=60.0, alias="STOCKWATCH_MAX_BACKOFF")

    # --- Veille nocturne ---
    # Heures pleines, dans le fuseau ci-dessous. start == end désactive.
    quiet_start: int = Field(default=-1, alias="STOCKWATCH_QUIET_START")
    quiet_end: int = Field(default=-1, alias="STOCKWATCH_QUIET_END")
    # Indispensable : un VPS tourne en UTC, « 20 h » n'y est pas 20 h chez toi.
    timezone: str = Field(default="Europe/Paris", alias="STOCKWATCH_TIMEZONE")

    # --- Alerting ---
    alert_on_first_seen: bool = Field(default=True, alias="STOCKWATCH_ALERT_ON_FIRST_SEEN")
    # 0 disables reminders: one alert per out-of-stock -> in-stock transition.
    repeat_alert_minutes: int = Field(default=0, alias="STOCKWATCH_REPEAT_ALERT_MINUTES")
    # Consecutive failures before the bot warns that it can no longer read the page.
    error_alert_after: int = Field(default=12, alias="STOCKWATCH_ERROR_ALERT_AFTER")
    # Échecs tolérés à cadence normale avant de commencer à ralentir. Un CDN
    # qui refuse une requête sur trois n'est pas une panne : réessayer tout de
    # suite passe souvent, alors que ralentir ferait rater le réassort.
    failure_grace: int = Field(default=3, alias="STOCKWATCH_FAILURE_GRACE")

    # --- HTTP fingerprint ---
    user_agent: str = Field(default=DEFAULT_USER_AGENT, alias="STOCKWATCH_USER_AGENT")
    accept_language: str = Field(default="fr-FR,fr;q=0.9,en;q=0.8", alias="STOCKWATCH_ACCEPT_LANGUAGE")
    # Paste a browser cookie header here if the site starts serving a challenge.
    cookie: SecretStr | None = Field(default=None, alias="STOCKWATCH_COOKIE")
    extra_headers_raw: str = Field(default="", alias="STOCKWATCH_EXTRA_HEADERS")
    proxy_url: SecretStr | None = Field(default=None, alias="STOCKWATCH_PROXY_URL")
    http2: bool = Field(default=True, alias="STOCKWATCH_HTTP2")
    cache_buster: bool = Field(default=False, alias="STOCKWATCH_CACHE_BUSTER")
    # Renvoyer l'ETag / Last-Modified du dernier corps reçu : le serveur répond
    # alors « 304 Not Modified » tant que la page n'a pas bougé, ce qui divise
    # la bande passante par plusieurs centaines. Incompatible avec le
    # cache-buster, qui force une URL neuve à chaque requête.
    conditional_requests: bool = Field(default=True, alias="STOCKWATCH_CONDITIONAL_REQUESTS")
    # Plafond de téléchargement par jour, en Mo (0 = illimité). Au-delà, le bot
    # ralentit tout seul plutôt que de faire exploser la facture d'un proxy
    # facturé au gigaoctet.
    daily_budget_mb: float = Field(default=0.0, alias="STOCKWATCH_DAILY_BUDGET_MB")
    # Intervalle appliqué une fois le budget atteint.
    throttled_interval: float = Field(default=300.0, alias="STOCKWATCH_THROTTLED_INTERVAL")
    html_fallback: bool = Field(default=True, alias="STOCKWATCH_HTML_FALLBACK")

    # --- Cookie automatique ---
    # Renouveler le cookie tout seul avec un navigateur headless (Playwright).
    # Sans Playwright installé, le bot se rabat sur STOCKWATCH_COOKIE.
    auto_cookie: bool = Field(default=True, alias="STOCKWATCH_AUTO_COOKIE")
    # Âge au-delà duquel on va en chercher un neuf, même si tout va bien.
    cookie_refresh_minutes: int = Field(default=180, alias="STOCKWATCH_COOKIE_REFRESH_MINUTES")
    # Délai minimal entre deux tentatives, pour ne pas lancer un navigateur à
    # chaque vérification ratée.
    cookie_retry_minutes: int = Field(default=10, alias="STOCKWATCH_COOKIE_RETRY_MINUTES")

    # --- Persistence ---
    state_file: Path = Field(default=Path("stockwatch-state.json"), alias="STOCKWATCH_STATE_FILE")
    log_level: str = Field(default="INFO", alias="STOCKWATCH_LOG_LEVEL")

    @field_validator("*", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object, info: ValidationInfo) -> object:
        """Treat `STOCKWATCH_FOO=` (empty) as "not set".

        `.env.example` ships several keys with no value on purpose (owner id,
        cookie, proxy): without this an empty string would be parsed as `0`,
        as an empty proxy URL, or would wipe a default.
        """
        if isinstance(value, str) and not value.strip():
            field = cls.model_fields.get(info.field_name or "")
            if field is not None and not field.is_required():
                return field.get_default(call_default_factory=True)
        return value

    @field_validator("bot_token")
    @classmethod
    def _check_token(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()
        head, _, tail = raw.partition(":")
        if not head or not tail:
            raise ValueError(
                "STOCKWATCH_BOT_TOKEN is not a Telegram token — expected `123456789:AA...` from @BotFather"
            )
        return SecretStr(raw)

    @field_validator("poll_interval")
    @classmethod
    def _floor_interval(cls, value: float) -> float:
        # Below 0.2s the loop hammers the CDN without ever seeing fresher data.
        return max(0.2, float(value))

    @field_validator("request_timeout", "max_backoff")
    @classmethod
    def _positive(cls, value: float) -> float:
        return max(0.5, float(value))

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _valid_hour(cls, value: int) -> int:
        value = int(value)
        return value if 0 <= value <= 23 else -1

    @field_validator("product_url")
    @classmethod
    def _require_http(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("STOCKWATCH_PRODUCT_URL must be an http(s) URL")
        return value

    @field_validator("api_url")
    @classmethod
    def _ignore_bogus_api_url(cls, value: str) -> str:
        """Un STOCKWATCH_API_URL qui n'est pas une URL est ignoré.

        Une valeur de remplacement collée telle quelle (« <ton url> ») ferait
        échouer chaque vérification alors que la page, elle, reste lisible :
        mieux vaut l'ignorer et continuer à surveiller.
        """
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            return ""
        return value

    @property
    def chat_ids(self) -> list[int]:
        ids: list[int] = []
        for chunk in _split_csv(self.chat_ids_raw):
            try:
                ids.append(int(chunk))
            except ValueError:
                continue
        return ids

    @property
    def sizes(self) -> list[str]:
        """Watched sizes, normalised (`xs` -> `XS`, `Small` -> `S`)."""
        wanted: list[str] = []
        for chunk in _split_csv(self.sizes_raw):
            size = normalize_size(chunk) or chunk.strip().upper()
            if size not in wanted:
                wanted.append(size)
        return wanted or ["XS", "S"]

    @property
    def extra_headers(self) -> dict[str, str]:
        raw = self.extra_headers_raw.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items()}


@dataclass
class WatchConfig:
    """The mutable half of the configuration — what `/tailles`, `/produit` and
    `/intervalle` edit at runtime. Shared by reference between the bot and the
    monitor, so a command takes effect on the very next tick."""

    product_url: str
    product_label: str
    sizes: list[str] = field(default_factory=lambda: ["XS", "S"])
    poll_interval: float = 1.0
    paused: bool = False
    product_id: str = ""
    product_color: str = ""
    quiet_start: int = -1
    quiet_end: int = -1
    timezone: str = "Europe/Paris"

    @property
    def quiet_enabled(self) -> bool:
        return 0 <= self.quiet_start <= 23 and 0 <= self.quiet_end <= 23 and self.quiet_start != self.quiet_end

    def now(self) -> datetime:
        """L'heure locale de l'utilisateur, pas celle du serveur."""
        zone = resolve_timezone(self.timezone)
        return datetime.now(zone) if zone else datetime.now().astimezone()

    def is_quiet_now(self) -> bool:
        return self.quiet_enabled and is_quiet(self.now(), self.quiet_start, self.quiet_end)

    def effective_product_id(self) -> str | None:
        """The id identifying the watched colourway inside the page."""
        return self.product_id or product_id_from_url(self.product_url)

    @classmethod
    def from_settings(cls, settings: StockWatchSettings) -> WatchConfig:
        return cls(
            product_url=settings.product_url,
            product_label=settings.product_label or label_from_url(settings.product_url),
            sizes=list(settings.sizes),
            poll_interval=settings.poll_interval,
            product_id=settings.product_id.strip(),
            product_color=settings.product_color.strip(),
            quiet_start=settings.quiet_start,
            quiet_end=settings.quiet_end,
            timezone=settings.timezone,
        )


def load_settings(*, allow_missing_token: bool = False) -> StockWatchSettings:
    """Build the settings, optionally tolerating a missing bot token.

    `diagnose` only needs the HTTP half of the configuration, so it can run
    before a token exists — every other entry point requires one.
    """
    try:
        return StockWatchSettings()
    except ValidationError:
        if not allow_missing_token:
            raise
        return StockWatchSettings(STOCKWATCH_BOT_TOKEN="0:diagnose-placeholder")
