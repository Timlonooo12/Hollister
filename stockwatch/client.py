"""HTTP client used to poll the product page.

Kept deliberately small: one long-lived connection pool (so a probe costs a
single round-trip, not a TLS handshake), a browser-shaped header set, and
explicit detection of the block pages Akamai serves when you poll too hard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from .config import StockWatchSettings

logger = logging.getLogger(__name__)

# Text that appears on the CDN's challenge / deny pages. When one of these comes
# back the response body is useless and must not be parsed as "everything is out
# of stock" — that would silently turn the watcher into a no-op.
_BLOCK_MARKERS = (
    "access denied",
    "reference #",
    "you don't have permission to access",
    "request unsuccessful",
    "incapsula",
    "px-captcha",
    "are you a human",
    "bot detection",
)


@dataclass
class FetchResult:
    url: str
    status_code: int | None
    body: str
    elapsed: float
    error: str | None = None
    blocked: bool = False
    # Octets facturables de l'échange : corps compressé + en-têtes des deux
    # sens. httpx ne compte que le corps, or un « 304 » n'en a pas — sans les
    # en-têtes, un budget se croirait à zéro. Estimation volontairement haute
    # (HTTP/2 compresse les en-têtes répétés).
    bytes_downloaded: int = 0
    not_modified: bool = False

    @property
    def ok(self) -> bool:
        if self.error is not None or self.blocked:
            return False
        if self.not_modified:
            return True
        return self.status_code is not None and 200 <= self.status_code < 300


class ProductClient:
    """Thin async wrapper over `httpx.AsyncClient` with the right fingerprint."""

    def __init__(self, settings: StockWatchSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        # ETag / Last-Modified du dernier corps reçu, par URL. Les renvoyer
        # permet au serveur de répondre « 304 Not Modified » — quelques
        # centaines d'octets au lieu de la page entière.
        self._validators: dict[str, tuple[str | None, str | None]] = {}

    def _headers(self) -> dict[str, str]:
        # A full desktop-Chrome header set, client hints included. Bot filters
        # score the whole set, not just the User-Agent: a "Chrome" UA arriving
        # without sec-ch-ua is a giveaway.
        headers = {
            "User-Agent": self._settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": self._settings.accept_language,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Priority": "u=0, i",
        }
        if self._settings.cookie is not None:
            headers["Cookie"] = self._settings.cookie.get_secret_value()
        headers.update(self._settings.extra_headers)
        return headers

    async def __aenter__(self) -> ProductClient:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        if self._client is not None:
            return
        timeout = httpx.Timeout(
            self._settings.request_timeout,
            connect=min(5.0, self._settings.request_timeout),
        )
        limits = httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=120.0)
        kwargs: dict[str, object] = {
            "headers": self._headers(),
            "timeout": timeout,
            "limits": limits,
            "follow_redirects": True,
        }
        if self._settings.proxy_url is not None:
            kwargs["proxy"] = self._settings.proxy_url.get_secret_value()
        # Browsers speak HTTP/2; a client that negotiates HTTP/1.1 while
        # claiming to be Chrome stands out to a bot filter. Enabled whenever the
        # `h2` package is installed, unless the operator turns it off.
        if self._settings.http2:
            try:
                import h2  # noqa: F401
            except ImportError:
                logger.info("HTTP/2 unavailable (`h2` not installed) — using HTTP/1.1.")
            else:
                kwargs["http2"] = True
        self._client = httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def forget_validators(self, url: str | None = None) -> None:
        """Oublier l'ETag mémorisé pour forcer un téléchargement complet.

        Un « 304 » ne vaut que si l'on a déjà lu la page avec succès : sinon il
        ne fait que confirmer qu'une réponse inexploitable n'a pas changé.
        """
        if url is None:
            self._validators.clear()
        else:
            self._validators.pop(url, None)

    async def fetch(self, url: str, *, cache_buster: bool | None = None) -> FetchResult:
        """GET `url` once. Never raises: failures come back inside the result."""
        if self._client is None:
            await self.start()
        assert self._client is not None

        target = url
        bust = self._settings.cache_buster if cache_buster is None else cache_buster
        if bust:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}_={int(time.time() * 1000)}"

        conditional: dict[str, str] = {}
        if self._settings.conditional_requests:
            etag, last_modified = self._validators.get(url, (None, None))
            if etag:
                conditional["If-None-Match"] = etag
            if last_modified:
                conditional["If-Modified-Since"] = last_modified

        started = time.perf_counter()
        try:
            response = await self._client.get(target, headers=conditional or None)
        except httpx.HTTPError as exc:
            return FetchResult(
                url=target,
                status_code=None,
                body="",
                elapsed=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )
        elapsed = time.perf_counter() - started
        received = _exchange_bytes(response)

        if response.status_code == 304:
            return FetchResult(
                url=target,
                status_code=304,
                body="",
                elapsed=elapsed,
                bytes_downloaded=received,
                not_modified=True,
            )

        if self._settings.conditional_requests:
            etag = response.headers.get("etag")
            last_modified = response.headers.get("last-modified")
            if etag or last_modified:
                self._validators[url] = (etag, last_modified)

        body = response.text
        blocked = response.status_code in (401, 403, 406, 429) or _looks_blocked(body)
        error = None
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}"
        elif blocked:
            error = "blocked by the site's bot protection"
        return FetchResult(
            url=target,
            status_code=response.status_code,
            body=body,
            elapsed=elapsed,
            error=error,
            blocked=blocked,
            bytes_downloaded=received,
        )


def _exchange_bytes(response: httpx.Response) -> int:
    """Taille approximative de l'échange, en-têtes des deux sens compris."""
    def header_size(headers: object) -> int:
        return sum(len(str(key)) + len(str(value)) + 4 for key, value in headers.items())  # type: ignore[attr-defined]

    request = response.request
    sent = header_size(request.headers) + len(str(request.url)) + 16
    received = response.num_bytes_downloaded + header_size(response.headers) + 16
    return sent + received


def _looks_blocked(body: str) -> bool:
    if not body or len(body) > 200_000:
        # Real product pages are large; block pages are tiny. Skip the scan on
        # anything big to keep the 1 s budget.
        return False
    lowered = body[:4000].lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)
