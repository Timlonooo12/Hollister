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

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Le temps que le contrôle anti-bot s'exécute et pose ses cookies.
_SETTLE_SECONDS = 4.0


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
            "  sudo .venv/bin/pip install playwright\n"
            "  sudo .venv/bin/playwright install --with-deps chromium"
        ) from exc

    import asyncio

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - dépend de l'installation
            raise BrowserUnavailable(
                f"Chromium n'a pas pu démarrer ({exc}). Installe-le avec :\n"
                "  sudo .venv/bin/playwright install --with-deps chromium"
            ) from exc

        try:
            context = await browser.new_context(
                locale=locale,
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # Le contrôle s'exécute après le rendu : on lui laisse le temps de
            # poser ses cookies avant de les lire.
            await asyncio.sleep(_SETTLE_SECONDS)

            user_agent = await page.evaluate("() => navigator.userAgent")
            cookies = await context.cookies()
            header = "; ".join(
                f"{cookie['name']}={cookie['value']}"
                for cookie in cookies
                if cookie.get("name") and cookie.get("value") is not None
            )
        finally:
            await browser.close()

    if not header:
        raise BrowserUnavailable("Le navigateur n'a obtenu aucun cookie — page inaccessible ?")
    return BrowserSession(cookie=header, user_agent=str(user_agent))
