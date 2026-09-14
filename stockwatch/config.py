"""Configuration for the stock watcher, loaded from environment / `.env`.

Every setting is prefixed with `STOCKWATCH_` so the file can live next to the
Eldorado bot's own `.env` without any clash. Only `STOCKWATCH_BOT_TOKEN` is
mandatory; everything else defaults to the Hollister product this bot was
built for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .parsing import normalize_size, product_id_from_url

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
    # Optional JSON endpoint. Left empty the watcher reads the product page and
    # digs the state out of the embedded JSON, which needs no guessing.
    api_url: str = Field(default="", alias="STOCKWATCH_API_URL")

    # --- Cadence ---
    # "À la seconde": one probe per second by default. Going below ~0.5s buys
    # nothing (the CDN caches for longer than that) and gets you blocked.
    poll_interval: float = Field(default=1.0, alias="STOCKWATCH_POLL_INTERVAL")
    request_timeout: float = Field(default=8.0, alias="STOCKWATCH_REQUEST_TIMEOUT")
    max_backoff: float = Field(default=60.0, alias="STOCKWATCH_MAX_BACKOFF")

    # --- Alerting ---
    alert_on_first_seen: bool = Field(default=True, alias="STOCKWATCH_ALERT_ON_FIRST_SEEN")
    # 0 disables reminders: one alert per out-of-stock -> in-stock transition.
    repeat_alert_minutes: int = Field(default=0, alias="STOCKWATCH_REPEAT_ALERT_MINUTES")
    # Consecutive failures before the bot warns that it can no longer read the page.
    error_alert_after: int = Field(default=12, alias="STOCKWATCH_ERROR_ALERT_AFTER")

    # --- HTTP fingerprint ---
    user_agent: str = Field(default=DEFAULT_USER_AGENT, alias="STOCKWATCH_USER_AGENT")
    accept_language: str = Field(default="fr-FR,fr;q=0.9,en;q=0.8", alias="STOCKWATCH_ACCEPT_LANGUAGE")
    # Paste a browser cookie header here if the site starts serving a challenge.
    cookie: SecretStr | None = Field(default=None, alias="STOCKWATCH_COOKIE")
    extra_headers_raw: str = Field(default="", alias="STOCKWATCH_EXTRA_HEADERS")
    proxy_url: SecretStr | None = Field(default=None, alias="STOCKWATCH_PROXY_URL")
    http2: bool = Field(default=False, alias="STOCKWATCH_HTTP2")
    cache_buster: bool = Field(default=False, alias="STOCKWATCH_CACHE_BUSTER")
    html_fallback: bool = Field(default=True, alias="STOCKWATCH_HTML_FALLBACK")

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

    @field_validator("product_url")
    @classmethod
    def _require_http(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("STOCKWATCH_PRODUCT_URL must be an http(s) URL")
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
