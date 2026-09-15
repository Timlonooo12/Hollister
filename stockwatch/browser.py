"""Renouvellement automatique du cookie, via un navigateur headless.

Un cookie de pare-feu applicatif expire au bout de quelques heures : le recopier
à la main à chaque fois n'est pas tenable pour une surveillance qui tourne en
continu. Un vrai navigateur, lui, en obtient un nouveau à chaque visite — c'est
tout l'objet du contrôle.

On ne s'en sert donc pas pour surveiller (ce serait lourd et lent), mais
uniquement pour aller chercher un cookie de temps en temps : les milliers de
vérifications qui suivent restent de simples requêtes HTTP.

Playwright est facultatif : sans lui, le bot fonctionne exactement comme avant,
avec le cookie fourni à la main.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Le temps que le contrôle anti-bot s'exécute et pose ses cookies.
_SETTLE_SECONDS = 4.0


async def _settle_and_collect(page: object, context: object) -> str:
    """Laisser le contrôle s'exécuter, puis relever les cookies posés."""
    import asyncio

    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=15_000)  # type: ignore[attr-defined]
    await asyncio.sleep(_SETTLE_SECONDS)
    cookies = await context.cookies()  # type: ignore[attr-defined]
    names = [cookie["name"] for cookie in cookies if cookie.get("name")]
    logger.info("Cookies vus par le navigateur : %s", ", ".join(names[:12]) or "aucun")
    return "; ".join(
        f"{cookie['name']}={cookie['value']}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value") is not None
    )


@dataclass
class BrowserSession:
    cookie: str
    user_agent: str

    def summary(self) -> str:
        names = [chunk.split("=", 1)[0].strip() for chunk in self.cookie.split(";") if "=" in chunk]
        return f"{len(names)} cookies ({len(self.cookie)} caractères)"


class BrowserUnavailable(RuntimeError):
    """Playwright n'est pas installé, ou son navigateur n'est pas téléchargé."""


async def fetch_session(url: str, *, locale: str = "fr-FR", timeout: float = 60.0) -> BrowserSession:
    """Ouvre `url` dans Chromium et renvoie les cookies obtenus.

    Le User-Agent est celui du navigateur lui-même : un cookie de contrôle est
    lié à l'empreinte qui l'a obtenu, les réutiliser séparément ne sert à rien.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        raise BrowserUnavailable(
            "Playwright n'est pas installé. Sur le serveur :\n"
            "  sudo /opt/stockwatch/.venv/bin/pip install playwright\n"
            "  sudo /opt/stockwatch/.venv/bin/playwright install-deps chromium\n"
            "  sudo PLAYWRIGHT_BROWSERS_PATH=/opt/stockwatch/browsers "
            "/opt/stockwatch/.venv/bin/playwright install chromium"
        ) from exc

    # Le réglage du bot est un en-tête Accept-Language complet
    # (« fr-FR,fr;q=0.9 ») ; le navigateur, lui, attend une locale (« fr-FR »).
    locale = locale.split(",")[0].split(";")[0].strip() or "fr-FR"

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                # Sur un serveur, le bac à sable de Chromium demande des
                # privilèges que le service n'a volontairement pas, et /dev/shm
                # y est trop petit. Le navigateur ne visite qu'une URL connue,
                # celle que l'on surveille déjà.
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # pragma: no cover - dépend de l'installation
            detail = str(exc).splitlines()[0]
            raise BrowserUnavailable(
                f"Chromium n'a pas pu démarrer ({detail}).\n"
                "Le plus souvent, il a été téléchargé dans le dossier de root alors que le "
                "service tourne sous un autre utilisateur. Installe-le à un endroit partagé :\n"
                "  sudo PLAYWRIGHT_BROWSERS_PATH=/opt/stockwatch/browsers "
                "/opt/stockwatch/.venv/bin/playwright install chromium\n"
                "  sudo chown -R stockwatch:stockwatch /opt/stockwatch/browsers"
            ) from exc

        try:
            context = await browser.new_context(
                locale=locale,
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            status = response.status if response is not None else None

            header = await _settle_and_collect(page, context)
            if not header:
                # Un contrôle anti-bot pose souvent ses cookies au second appel :
                # la première visite ne sert qu'à exécuter son script.
                logger.info("Aucun cookie à la première visite, rechargement…")
                await page.reload(wait_until="load", timeout=timeout * 1000)
                header = await _settle_and_collect(page, context)

            user_agent = await page.evaluate("() => navigator.userAgent")
            final_url = page.url
            title = await page.title()
            length = await page.evaluate("() => document.documentElement.outerHTML.length")
        finally:
            await browser.close()

    if not header:
        raise BrowserUnavailable(
            "Le navigateur n'a obtenu aucun cookie.\n"
            f"HTTP {status} · {length} caractères · « {title[:60]} »\n"
            f"URL finale : {final_url[:120]}"
        )
    logger.info("Cookie obtenu : HTTP %s, %s caractères, titre « %s »", status, length, title[:60])
    return BrowserSession(cookie=header, user_agent=str(user_agent))
