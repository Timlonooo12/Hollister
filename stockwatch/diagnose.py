"""`python -m stockwatch diagnose` — show exactly what the parser sees.

The product page is the ground truth and it changes: this command fetches it
(or reads a saved copy), prints every size/stock pair it managed to extract and,
when it extracts nothing, dumps enough of the response to tell a layout change
from a bot-protection page.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .client import ProductClient
from .config import StockWatchSettings
from .parsing import iter_json_blobs, parse_availability, product_id_from_url, size_from_mapping


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
        if parsed.strategy == "html-no-stock-state":
            print("❌ Les tailles sont dans la page, mais SANS état de stock.")
            print(f"   Tailles listées : {', '.join(sorted(parsed.guessed_sizes)) or '—'}")
            print("   Ce site charge la disponibilité en JavaScript : le HTML seul ne peut pas y répondre,")
            print("   et deviner « bouton affiché = disponible » produirait de fausses alertes.")
            print("   → Trouve l'appel qui porte le stock et mets-le dans STOCKWATCH_API_URL :")
            print("     navigateur → F12 → onglet Réseau → filtre « Fetch/XHR » → recharge la fiche")
            print("     → cherche une réponse JSON contenant XS / inStock / inventory → clic droit → Copier l'URL.")
        else:
            print("❌ Aucune taille détectée.")
            print("   • Si la réponse est une page de blocage : ajoute STOCKWATCH_COOKIE "
                  "(copie l'en-tête Cookie de ton navigateur) et/ou augmente STOCKWATCH_POLL_INTERVAL.")
            print("   • Sinon, la page a changé de structure. Aperçu du début de la réponse :")
            print("   " + body[:600].replace("\n", " ")[:600])
        _print_leads(blobs)
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


def _print_leads(blobs: list[Any]) -> None:
    """Show where size labels sit inside the page's JSON.

    Short enough to paste into a bug report, and enough to write the reader for
    a site whose schema nobody documented.
    """
    paths: list[tuple[str, list[str]]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 20 or len(paths) >= 20:
            return
        if isinstance(node, dict):
            if size_from_mapping(node):
                paths.append((path or "(racine)", sorted(node.keys())[:14]))
            for key, value in node.items():
                walk(value, f"{path}.{key}", depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node[:40]):
                walk(value, f"{path}[{index}]", depth + 1)

    for number, blob in enumerate(blobs[:12]):
        walk(blob, f"json[{number}]", 0)

    print()
    if not paths:
        print("Aucune taille trouvée dans le JSON de la page non plus.")
        if blobs:
            top = sorted({key for blob in blobs if isinstance(blob, dict) for key in blob})[:20]
            print(f"Clés de premier niveau des blocs JSON : {', '.join(top) or '—'}")
        return
    print("Pistes — où les tailles apparaissent dans le JSON de la page :")
    for path, keys in paths:
        print(f"  {path}\n      clés voisines : {', '.join(keys)}")
    print()
    print("Colle ces lignes dans la conversation : elles suffisent à écrire le lecteur exact.")


def run_diagnose(settings: StockWatchSettings, **kwargs: str | None) -> int:
    return asyncio.run(diagnose(settings, **kwargs))  # type: ignore[arg-type]
