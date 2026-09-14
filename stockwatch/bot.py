"""Telegram command surface (French — the bot talks to its owner in French)."""

from __future__ import annotations

import html
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from .config import StockWatchSettings, WatchConfig, label_from_url
from .monitor import Monitor
from .parsing import normalize_size
from .state import StateStore

logger = logging.getLogger(__name__)

router = Router(name="stockwatch")

HELP = (
    "🤖 <b>Bot de surveillance de stock</b>\n\n"
    "Je vérifie la page du produit en continu et je préviens <b>à la seconde</b> "
    "où une taille surveillée revient en stock.\n\n"
    "<b>Commandes</b>\n"
    "/start — recevoir les alertes\n"
    "/stop — ne plus recevoir les alertes\n"
    "/status — état, stock actuel, statistiques\n"
    "/check — vérifier tout de suite\n"
    "/tailles XS,S — choisir les tailles surveillées\n"
    "/produit &lt;url&gt; — changer le produit surveillé\n"
    "/intervalle 1 — délai entre deux vérifications (secondes)\n"
    "/pause et /reprendre — suspendre ou relancer la surveillance\n"
    "/id — afficher l'identifiant de ce chat\n"
    "/aide — ce message"
)


def _is_owner(message: Message, settings: StockWatchSettings) -> bool:
    if settings.owner_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.owner_id


async def _deny(message: Message) -> None:
    await message.answer("⛔️ Seul le propriétaire du bot peut modifier la surveillance.")


@router.message(CommandStart())
async def cmd_start(message: Message, store: StateStore, config: WatchConfig) -> None:
    added = await store.add_subscriber(message.chat.id)
    intro = "✅ Tu recevras les alertes." if added else "✅ Tu es déjà abonné."
    await message.answer(
        f"{intro}\n\n"
        f"👕 Produit : {html.escape(config.product_label or 'Produit surveillé')}\n"
        f"🎯 Tailles : <b>{html.escape(', '.join(config.sizes))}</b>\n"
        f"⚡ Vérification toutes les {config.poll_interval:g} s\n\n"
        f"{HELP}"
    )


@router.message(Command("stop"))
async def cmd_stop(message: Message, store: StateStore) -> None:
    removed = await store.remove_subscriber(message.chat.id)
    await message.answer("🔕 Alertes désactivées pour ce chat." if removed else "Tu n'étais pas abonné ici.")


@router.message(Command("aide", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"🆔 Chat : <code>{message.chat.id}</code>")


@router.message(Command("status", "statut"))
async def cmd_status(message: Message, monitor: Monitor) -> None:
    await message.answer("\n".join(monitor.status_lines()))


@router.message(Command("check", "verif"))
async def cmd_check(message: Message, monitor: Monitor, config: WatchConfig) -> None:
    notice = await message.answer("🔎 Vérification en cours…")
    tick = await monitor.check_once()
    if not tick.ok:
        await notice.edit_text(f"⚠️ Échec : <code>{html.escape((tick.error or 'inconnu')[:300])}</code>")
        return
    available = sorted(size for size, ok in tick.sizes.items() if ok)
    await notice.edit_text(
        "🔎 <b>Vérification immédiate</b>\n"
        f"📦 {tick.watched_summary(config.sizes)}\n"
        f"🛒 Tailles dispo : {html.escape(', '.join(available)) if available else 'aucune'}\n"
        f"⏱ {tick.elapsed * 1000:.0f} ms • lecture : <code>{tick.strategy}</code>"
    )


@router.message(Command("tailles", "sizes"))
async def cmd_sizes(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                    settings: StockWatchSettings) -> None:
    if not command.args:
        await message.answer(
            f"🎯 Tailles surveillées : <b>{html.escape(', '.join(config.sizes))}</b>\n"
            "Pour changer : <code>/tailles XS,S</code>"
        )
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return
    wanted: list[str] = []
    unknown: list[str] = []
    for chunk in command.args.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        size = normalize_size(chunk)
        if size is None:
            unknown.append(chunk)
        elif size not in wanted:
            wanted.append(size)
    if not wanted:
        await message.answer("❌ Aucune taille valide. Exemple : <code>/tailles XS,S</code>")
        return
    config.sizes = wanted
    await store.set_override("sizes", wanted)
    suffix = f"\n⚠️ Ignoré : {html.escape(', '.join(unknown))}" if unknown else ""
    await message.answer(f"✅ Tailles surveillées : <b>{html.escape(', '.join(wanted))}</b>{suffix}")


@router.message(Command("produit", "product", "url"))
async def cmd_product(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                      settings: StockWatchSettings) -> None:
    if not command.args:
        await message.answer(f"👕 Produit surveillé :\n{config.product_url}")
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return
    url = command.args.strip().split()[0]
    if not url.startswith(("http://", "https://")):
        await message.answer("❌ URL invalide (elle doit commencer par https://).")
        return
    config.product_url = url
    config.product_label = label_from_url(url)
    await store.set_override("product_url", url)
    await store.set_override("product_label", config.product_label)
    # The stock memory belongs to the previous product: drop it so the first
    # check on the new page does not look like a restock.
    store.sizes.clear()
    await store.save()
    await message.answer(
        f"✅ Nouveau produit surveillé : <b>{html.escape(config.product_label)}</b>\n{html.escape(url)}"
    )


@router.message(Command("intervalle", "interval"))
async def cmd_interval(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                       settings: StockWatchSettings) -> None:
    if not command.args:
        await message.answer(f"⚡ Intervalle actuel : {config.poll_interval:g} s")
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return
    try:
        value = float(command.args.strip().replace(",", "."))
    except ValueError:
        await message.answer("❌ Valeur invalide. Exemple : <code>/intervalle 1</code>")
        return
    value = max(0.2, min(3600.0, value))
    config.poll_interval = value
    await store.set_override("poll_interval", value)
    warning = "\n⚠️ Sous 1 s, le site peut bloquer les requêtes." if value < 1 else ""
    await message.answer(f"✅ Vérification toutes les {value:g} s.{warning}")


@router.message(Command("pause"))
async def cmd_pause(message: Message, config: WatchConfig, store: StateStore, settings: StockWatchSettings) -> None:
    if not _is_owner(message, settings):
        await _deny(message)
        return
    config.paused = True
    await store.set_override("paused", True)
    await message.answer("⏸ Surveillance en pause. <code>/reprendre</code> pour relancer.")


@router.message(Command("reprendre", "resume"))
async def cmd_resume(message: Message, config: WatchConfig, store: StateStore, settings: StockWatchSettings) -> None:
    if not _is_owner(message, settings):
        await _deny(message)
        return
    config.paused = False
    await store.set_override("paused", False)
    await message.answer("▶️ Surveillance relancée.")
