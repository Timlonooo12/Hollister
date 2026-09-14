"""Process wiring: the Telegram bot and the watching loop, side by side."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand

from .bot import router
from .client import ProductClient
from .config import StockWatchSettings, WatchConfig
from .monitor import Monitor
from .notifier import TelegramNotifier
from .state import StateStore, apply_overrides

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Recevoir les alertes"),
    BotCommand(command="status", description="État de la surveillance"),
    BotCommand(command="check", description="Vérifier tout de suite"),
    BotCommand(command="tailles", description="Tailles surveillées (ex : XS,S)"),
    BotCommand(command="produit", description="Changer le produit surveillé"),
    BotCommand(command="intervalle", description="Délai entre deux vérifications"),
    BotCommand(command="pause", description="Suspendre la surveillance"),
    BotCommand(command="reprendre", description="Relancer la surveillance"),
    BotCommand(command="stop", description="Ne plus recevoir les alertes"),
    BotCommand(command="aide", description="Aide"),
]


class StartupError(RuntimeError):
    """A problem the user can fix — reported as one line, not a traceback."""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )
    # aiogram's polling chatter is noise next to one line per restock.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run(settings: StockWatchSettings) -> None:
    configure_logging(settings.log_level)

    state = StateStore(settings.state_file)
    state.load()
    config = WatchConfig.from_settings(settings)
    apply_overrides(config, state.overrides)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        await bot.session.close()
        raise StartupError(
            "Telegram refuse ce token. Vérifie STOCKWATCH_BOT_TOKEN dans .env "
            "(@BotFather → /mybots → ton bot → API Token)."
        ) from exc
    except TelegramNetworkError as exc:
        await bot.session.close()
        raise StartupError(f"Impossible de joindre api.telegram.org : {exc}") from exc
    logger.info("Connecté à Telegram en tant que @%s", me.username)

    client = ProductClient(settings)
    await client.start()
    notifier = TelegramNotifier(bot, state, settings.chat_ids)
    monitor = Monitor(settings, config, client, notifier, state)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher["settings"] = settings
    dispatcher["config"] = config
    # "state" is reserved by aiogram's FSM middleware — use "store".
    dispatcher["store"] = state
    dispatcher["monitor"] = monitor
    dispatcher["notifier"] = notifier

    stop = asyncio.Event()
    watcher = asyncio.create_task(monitor.run(stop), name="stockwatch-monitor")

    try:
        with contextlib.suppress(Exception):
            await bot.set_my_commands(COMMANDS)
        if notifier.recipients():
            await notifier.broadcast(
                "🟢 <b>Surveillance démarrée</b>\n"
                f"👕 {config.product_label or 'Produit surveillé'}\n"
                f"🎯 Tailles : <b>{', '.join(config.sizes)}</b>\n"
                f"⚡ Une vérification toutes les {config.poll_interval:g} s",
                silent=True,
            )
        # aiogram installs its own SIGINT/SIGTERM handlers and returns cleanly.
        await dispatcher.start_polling(bot, allowed_updates=["message"])
    finally:
        stop.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        await client.aclose()
        await state.save()
        await bot.session.close()
        logger.info("Stopped.")
