"""`python -m stockwatch probe` — trouver un endpoint plus léger que la page.

Lire 350 Ko de HTML pour en extraire sept lignes de stock coûte cher : à une
vérification par seconde, c'est ~10 Go par jour. Le navigateur, lui, obtient la
même information par un appel JSON de quelques kilo-octets.

Cette commande cherche cet appel toute seule : elle récupère la page, en extrait
toutes les URL qui ressemblent à une API, les essaie une par une, et garde
celles qui renvoient réellement des tailles avec leur disponibilité. Elle
affiche ensuite la ligne à coller dans `.env`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from .client import ProductClient
from .config import StockWatchSettings
from .parsing import parse_availability, product_id_from_url

# Une URL candidate doit évoquer une API, pas une image ou une feuille de style.
_INTERESTING = ("api", "graphql", "gql", "inventory", "availability", "sku", "product", "service")
_BORING_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".woff", ".woff2", ".ico")

_URL_RE = re.compile(r"""["'(]((?:https?:)?//[^"'()\s\\]{8,300}|/[A-Za-z0-9_\-./]{6,300})["')]""")

# Chemins utilisés par la plateforme Abercrombie & Fitch (dont Hollister fait
# partie). `{id}` est remplacé par l'identifiant du produit lu dans l'URL.
_GUESSES = (
    "/api/ecomm/{brand}/product/{id}",
    "/api/ecomm/{brand}/products/{id}",
    "/api/ecomm/{brand}/product/detail/{id}",
    "/api/ccp/v1/product/{id}",
    "/api/search/{brand}/p/{id}",
    "/api/ecomm/{brand}/inventory/{id}",
    "/api/graphql",
    "/graphql",
    "/api/ecomm/graphql",
)

_MAX_CANDIDATES = 30


@dataclass
class ProbeResult:
    url: str
    status: int | None
    bytes_downloaded: int
    sizes: dict[str, bool]
    error: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.sizes)


def extract_candidates(body: str, page_url: str) -> list[str]:
    """Les URL de la page qui ressemblent à un appel de données."""
    origin = "{0.scheme}://{0.netloc}".format(urlparse(page_url))
    seen: dict[str, None] = {}
    for match in _URL_RE.finditer(body):
        raw = match.group(1)
        if raw.startswith("//"):
            raw = "https:" + raw
        lowered = raw.lower()
        if lowered.endswith(_BORING_SUFFIXES):
            continue
        if not any(word in lowered for word in _INTERESTING):
            continue
        absolute = urljoin(origin + "/", raw)
        if urlparse(absolute).netloc != urlparse(origin).netloc:
            continue
        seen.setdefault(absolute, None)
    return list(seen)


def guessed_candidates(page_url: str) -> list[str]:
    origin = "{0.scheme}://{0.netloc}".format(urlparse(page_url))
    product = product_id_from_url(page_url) or ""
    brand = "hol" if "hollister" in origin else "anf"
    return [origin + path.format(id=product, brand=brand) for path in _GUESSES]


def rank(candidates: list[str], page_url: str) -> list[str]:
    """Les plus prometteuses d'abord : celles qui nomment le produit, puis les
    API de stock, puis le reste."""
    product = product_id_from_url(page_url) or ""

    def score(url: str) -> tuple[int, int]:
        lowered = url.lower()
        points = 0
        if product and product in lowered:
            points += 4
        if any(word in lowered for word in ("inventory", "availability", "sku")):
            points += 3
        if "graphql" in lowered or "/gql" in lowered:
            points += 2
        if "product" in lowered:
            points += 1
        return (-points, len(url))

    return sorted(dict.fromkeys(candidates), key=score)


async def probe(settings: StockWatchSettings, *, url: str | None = None) -> int:
    page_url = url or settings.product_url
    client = ProductClient(settings)
    try:
        page = await client.fetch(page_url)
        if not page.ok:
            print(f"❌ La page elle-même n'est pas accessible : {page.error}")
            return 1

        product_id = settings.product_id.strip() or product_id_from_url(page_url)
        reference = parse_availability(
            page.body, product_id=product_id, product_color=settings.product_color or None
        )
        print(f"Page       : {_human(page.bytes_downloaded)} sur le réseau, "
              f"{len(reference.sizes)} tailles lues")
        print()

        candidates = rank(extract_candidates(page.body, page_url) + guessed_candidates(page_url), page_url)
        candidates = [c for c in candidates if c.rstrip("/") != page_url.rstrip("/")][:_MAX_CANDIDATES]
        print(f"{len(candidates)} pistes à essayer…")
        print()

        results: list[ProbeResult] = []
        for candidate in candidates:
            result = await client.fetch(candidate)
            parsed = parse_availability(
                result.body,
                product_id=product_id,
                product_color=settings.product_color or None,
                html_fallback=False,
            )
            results.append(
                ProbeResult(
                    url=candidate,
                    status=result.status_code,
                    bytes_downloaded=result.bytes_downloaded,
                    sizes=parsed.sizes,
                    error=result.error,
                )
            )
            mark = "✅" if parsed.sizes else ("·" if result.ok else "✗")
            print(f"  {mark} {result.status_code or '—':>3}  {_human(result.bytes_downloaded):>9}  "
                  f"{len(parsed.sizes)} tailles  {candidate[:96]}")
            await asyncio.sleep(0.3)   # on ne martèle pas le site pendant la recherche
    finally:
        await client.aclose()

    usable = sorted((r for r in results if r.usable), key=lambda r: r.bytes_downloaded)
    print()
    if not usable:
        print("Aucun endpoint plus léger trouvé automatiquement.")
        print("La page reste la seule source : joue sur STOCKWATCH_POLL_INTERVAL,")
        print("ou relève l'appel exact dans l'onglet Réseau du navigateur (F12).")
        return 1

    best = usable[0]
    gain = page.bytes_downloaded / max(1, best.bytes_downloaded)
    print(f"🏆 Le plus léger qui lit le stock : {_human(best.bytes_downloaded)} "
          f"contre {_human(page.bytes_downloaded)} pour la page — {gain:.0f}× moins.")
    print(f"   {best.url}")
    print()
    print("À coller dans .env, puis redémarrer le bot :")
    print(f"   STOCKWATCH_API_URL={best.url}")
    print()
    print("Vérifie ensuite avec `python -m stockwatch diagnose` que les tailles")
    print("correspondent à la fiche avant de compter dessus.")
    return 0


def _human(count: int) -> str:
    value = float(count)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def run_probe(settings: StockWatchSettings, *, url: str | None = None) -> int:
    return asyncio.run(probe(settings, url=url))
