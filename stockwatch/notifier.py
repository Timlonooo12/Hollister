"""Telegram delivery: one alert fanned out to every subscriber."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from .keyboards import alert as alert_keyboard
from .state import StateStore

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends to the chats listed in the config plus everyone who ran /start.

    A chat that blocked the bot is dropped from the subscriber list instead of
    failing the alert for everybody else.
    """

    def __init__(self, bot: Bot, state: StateStore, chat_ids: list[int]) -> None:
        self._bot = bot
        self._state = state
        self._configured = list(chat_ids)

    def recipients(self) -> list[int]:
        seen: list[int] = []
        for chat_id in [*self._configured, *sorted(self._state.subscribers)]:
            if chat_id not in seen:
                seen.append(chat_id)
        return seen

    async def broadcast(self, text: str, *, url: str | None = None, silent: bool = False,
                        markup: InlineKeyboardMarkup | None = None) -> int:
        targets = self.recipients()
        if not targets:
            logger.warning("Alert ready but nobody is subscribed — send /start to the bot.")
            return 0
        if markup is None:
            markup = alert_keyboard(url) if url else None
        results = await asyncio.gather(
            *(self._send(chat_id, text, markup, silent) for chat_id in targets),
            return_exceptions=True,
        )
        return sum(1 for result in results if result is True)

    async def _send(self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None, silent: bool) -> bool:
        for attempt in range(3):
            try:
                await self._bot.send_message(
                    chat_id,
                    text,
                    reply_markup=markup,
                    disable_notification=silent,
                    disable_web_page_preview=True,
                )
                return True
            except TelegramRetryAfter as exc:
                # Telegram's own throttle. Alerts are time-critical, so wait the
                # minimum it asks for and retry rather than dropping.
                await asyncio.sleep(exc.retry_after)
            except TelegramForbiddenError:
                logger.info("Chat %s blocked the bot — unsubscribing", chat_id)
                await self._state.remove_subscriber(chat_id)
                return False
            except Exception as exc:  # noqa: BLE001 - delivery must never crash the loop
                logger.warning("Telegram send to %s failed (attempt %s): %s", chat_id, attempt + 1, exc)
                await asyncio.sleep(0.5 * (attempt + 1))
        return False
