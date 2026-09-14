"""`python -m stockwatch diagnose` — show exactly what the parser sees.

The product page is the ground truth and it changes: this command fetches it
(or reads a saved copy), prints every size/stock pair it managed to extract and,
when it extracts nothing, dumps enough of the response to tell a layout change
from a bot-protection page.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .client import ProductClient
from .config import StockWatchSettings
from .parsing import iter_json_blobs, parse_availability, product_id_from_url


async def diagnose(
    settings: StockWatchSettings,
    *,
    url: str | None = None,
    file: str | None = None,
    save: str | None = None,
) -> int:
    target = url or settings.api_url or settings.product_url

    if file:
        body = Path(file).read_text("utf-8", errors="replace")
        print(f"Source      : fichier {file} ({len(body)} caractères)")
    else:
        client = ProductClient(settings)
        try:
            result = await client.fetch(target)
        finally:
            await client.aclose()
        body = result.body
        print(f"Source      : {result.url}")
        print(f"HTTP        : {result.status_code}  en {result.elapsed * 1000:.0f} ms  "
              f"({len(body)} caractères)")
        if result.blocked:
            print("⚠️  La réponse ressemble à une page de blocage (Akamai / captcha).")
        if result.error:
            print(f"⚠️  Erreur    : {result.error}")

    if save:
        Path(save).write_text(body, "utf-8")
        print(f"Réponse enregistrée dans {save}")

    product_id = product_id_from_url(url or settings.product_url)
    blobs = list(iter_json_blobs(body))
    print(f"Produit id  : {product_id or '—'}")
    print(f"Blocs JSON  : {len(blobs)}")

    parsed = parse_availability(body, product_id=product_id, html_fallback=settings.html_fallback)
    print(f"Stratégie   : {parsed.strategy}")
    print()

    if not parsed.found:
        print("❌ Aucune taille détectée.")
        print("   • Si la réponse est une page de blocage : ajoute STOCKWATCH_COOKIE "
              "(copie l'en-tête Cookie de ton navigateur) et/ou augmente STOCKWATCH_POLL_INTERVAL.")
        print("   • Sinon, la page a changé de structure. Aperçu du début de la réponse :")
        print("   " + body[:600].replace("\n", " ")[:600])
        return 1

    print("Tailles détectées :")
    for size, available in sorted(parsed.sizes.items()):
        print(f"  {'✅ DISPO    ' if available else '❌ épuisée  '} {size}")
    print()
    print(f"Observations ({len(parsed.observations)}) — 20 premières :")
    for observation in parsed.observations[:20]:
        flag = "DISPO" if observation.available else "épuisée"
        focus = " [produit ciblé]" if observation.focused else ""
        print(f"  · {observation.size:<4} {flag:<8} via {observation.source}{focus} :: {observation.detail}")

    watched = settings.sizes
    missing = [size for size in watched if size not in parsed.sizes]
    if missing:
        print()
        print(f"⚠️  Tailles surveillées absentes de la page : {', '.join(missing)}")
    return 0


def run_diagnose(settings: StockWatchSettings, **kwargs: str | None) -> int:
    return asyncio.run(diagnose(settings, **kwargs))  # type: ignore[arg-type]
