"""Stockwatch — a Telegram bot that watches a product page and alerts the
second a given size (XS, S, ...) comes back in stock.

The package is self-contained: it shares the repository's dependencies
(aiogram 3, httpx, pydantic-settings) but nothing from `app/`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
