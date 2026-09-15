"""Telegram command surface (French — the bot talks to its owner in French)."""

from __future__ import annotations

import contextlib
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import keyboards
from .config import StockWatchSettings, WatchConfig, label_from_url
from .monitor import Monitor
from .parsing import normalize_size
from .schedule import format_window
from .state import StateStore

logger = logging.getLogger(__name__)

router = Router(name="stockwatch")

HELP = (
    "🤖 <b>Bot de surveillance de stock</b>\n\n"
    "Je vérifie la page du produit en continu et je préviens <b>à la seconde</b> "
    "où une taille surveillée revient en stock.\n\n"
    "Tout se pilote depuis <b>/menu</b> — les commandes ci-dessous font la même "
    "chose au clavier.\n\n"
    "<b>Commandes</b>\n"
    "/menu — le tableau de bord\n"
    "/start — recevoir les alertes\n"
    "/stop — ne plus recevoir les alertes\n"
    "/status — état, stock actuel, statistiques\n"
    "/check — vérifier tout de suite\n"
    "/tailles XS,S — choisir les tailles surveillées\n"
    "/produit &lt;url&gt; — changer le produit surveillé\n"
    "/couleur Blanc — choisir le coloris par son nom (recommandé)\n"
    "/variante &lt;id&gt; — choisir le coloris par son identifiant\n"
    "/intervalle 1 — délai entre deux vérifications (secondes)\n"
    "/veille 20 7 — ne rien vérifier entre 20h et 7h (/veille off pour arrêter)\n"
    "/pause et /reprendre — suspendre ou relancer la surveillance\n"
    "/id — afficher l'identifiant de ce chat\n"
    "/aide — ce message"
)


def _is_owner(message: Message, settings: StockWatchSettings) -> bool:
    if settings.owner_id is None:
        return True
    return message.from_user is not None and message.from_user.id == settings.owner_id


# --------------------------------------------------------------------------
# Rendu du menu
# --------------------------------------------------------------------------

def render_home(config: WatchConfig, monitor: Monitor | None = None) -> str:
    """La carte principale : tout ce qui compte en un écran."""
    lines = ["🤖 <b>Surveillance de stock</b>", ""]

    title = html.escape(config.product_label or "Produit surveillé")
    if config.product_color:
        title += f" — <i>{html.escape(config.product_color)}</i>"
    lines.append(f"👕 {title}")
    lines.append(f"🎯 Tailles suivies : <b>{html.escape(', '.join(config.sizes))}</b>")

    if monitor is not None and monitor.last_tick is not None:
        tick = monitor.last_tick
        lines.append(f"📦 {tick.watched_summary(config.sizes)}")
        if tick.ok:
            lines.append(f"🕒 Dernier contrôle : {tick.at.astimezone():%H:%M:%S} "
                         f"({tick.elapsed * 1000:.0f} ms)")
        else:
            lines.append(f"⚠️ {html.escape((tick.error or '')[:120])}")
    else:
        lines.append("📦 Aucun contrôle effectué pour l'instant.")

    cadence = f"⚡ Une vérification toutes les {config.poll_interval:g} s"
    if config.paused:
        cadence += "  •  ⏸ <b>en pause</b>"
    lines.append(cadence)

    if config.quiet_enabled:
        window = format_window(config.quiet_start, config.quiet_end)
        asleep = monitor.sleeping if monitor is not None else config.is_quiet_now()
        lines.append(f"{'😴 En veille' if asleep else '🌙 Veille'} : {window}")

    lines += ["", "<i>Choisis une action ci-dessous.</i>"]
    return "\n".join(lines)


async def _show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup,
                notice: str | None = None) -> None:
    """Met à jour le message en place plutôt que d'en empiler un nouveau."""
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    await callback.answer(notice or "")


def _owner_ok(callback: CallbackQuery, settings: StockWatchSettings) -> bool:
    if settings.owner_id is None:
        return True
    return callback.from_user is not None and callback.from_user.id == settings.owner_id


async def _deny(message: Message) -> None:
    await message.answer("⛔️ Seul le propriétaire du bot peut modifier la surveillance.")


@router.message(CommandStart())
async def cmd_start(message: Message, store: StateStore, config: WatchConfig,
                    monitor: Monitor | None = None) -> None:
    added = await store.add_subscriber(message.chat.id)
    intro = "✅ <b>Alertes activées pour ce chat.</b>" if added else "✅ Tu es déjà abonné."
    await message.answer(
        f"{intro}\n\n{render_home(config, monitor)}",
        reply_markup=keyboards.home(config, monitor.sleeping if monitor else config.is_quiet_now()),
        disable_web_page_preview=True,
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, config: WatchConfig, monitor: Monitor | None = None) -> None:
    await message.answer(
        render_home(config, monitor),
        reply_markup=keyboards.home(config, monitor.sleeping if monitor else config.is_quiet_now()),
        disable_web_page_preview=True,
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


@router.message(Command("couleur", "color"))
async def cmd_colour(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                     settings: StockWatchSettings) -> None:
    """Choisir le coloris par son nom, tel qu'il s'affiche sur la fiche.

    Plus sûr que l'identifiant numérique : les identifiants ne disent rien à
    personne et changent d'un article à l'autre, « Blanc » non.
    """
    if not command.args:
        current = config.product_color or "(aucun — sélection par identifiant)"
        await message.answer(
            f"🎨 Coloris suivi : <b>{html.escape(current)}</b>\n"
            "Pour changer : <code>/couleur Blanc</code>\n"
            "Les noms disponibles sont donnés par <code>python -m stockwatch diagnose</code>."
        )
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return
    colour = command.args.strip()
    config.product_color = colour
    config.product_id = ""          # le nom prime : on efface l'identifiant
    await store.set_override("product_color", colour)
    await store.set_override("product_id", "")
    store.sizes.clear()             # la mémoire du stock était celle d'un autre coloris
    await store.save()
    await message.answer(
        f"✅ Coloris suivi : <b>{html.escape(colour)}</b>\n"
        "Vérifie avec <code>/check</code> que les tailles correspondent bien à la fiche."
    )


@router.message(Command("variante", "variant"))
async def cmd_variant(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                      settings: StockWatchSettings) -> None:
    """Pick which product/colourway of the page to read.

    A product page also carries its other colours; when the page's own id for
    the one you want differs from the id in the URL, name it here.
    `python -m stockwatch diagnose` prints the candidates.
    """
    if not command.args:
        current = config.product_id or "(celui de l'URL)"
        await message.answer(
            f"🎨 Variante suivie : <b>{html.escape(current)}</b>\n"
            "Pour changer : <code>/variante 63503980</code>\n"
            "Les identifiants possibles sont donnés par <code>python -m stockwatch diagnose</code>."
        )
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return
    variant = command.args.strip().split()[0]
    if not variant.isalnum():
        await message.answer("❌ Identifiant invalide. Exemple : <code>/variante 63503980</code>")
        return
    config.product_id = variant
    await store.set_override("product_id", variant)
    store.sizes.clear()   # the stock memory belonged to the previous variant
    await store.save()
    await message.answer(f"✅ Variante suivie : <b>{html.escape(variant)}</b>")


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


@router.message(Command("veille", "quiet"))
async def cmd_quiet(message: Message, command: CommandObject, config: WatchConfig, store: StateStore,
                    settings: StockWatchSettings) -> None:
    """Définir la plage horaire pendant laquelle le bot ne vérifie rien."""
    if not command.args:
        current = format_window(config.quiet_start, config.quiet_end) if config.quiet_enabled else "désactivée"
        await message.answer(
            f"🌙 Veille : <b>{current}</b> <i>({html.escape(config.timezone)})</i>\n"
            "Pour changer : <code>/veille 20 7</code> — ou <code>/veille off</code>.",
            reply_markup=keyboards.quiet(config),
        )
        return
    if not _is_owner(message, settings):
        await _deny(message)
        return

    argument = command.args.strip().lower()
    if argument in {"off", "non", "stop", "0"}:
        config.quiet_start = config.quiet_end = -1
    else:
        hours = [chunk for chunk in argument.replace("h", " ").replace("-", " ").split() if chunk.isdigit()]
        if len(hours) != 2 or not all(0 <= int(hour) <= 23 for hour in hours):
            await message.answer("❌ Format attendu : <code>/veille 20 7</code> (heures pleines, 0 à 23).")
            return
        config.quiet_start, config.quiet_end = int(hours[0]), int(hours[1])

    await store.set_override("quiet_start", config.quiet_start)
    await store.set_override("quiet_end", config.quiet_end)
    if config.quiet_enabled:
        await message.answer(
            f"✅ Veille de <b>{format_window(config.quiet_start, config.quiet_end)}</b> "
            f"<i>({html.escape(config.timezone)})</i>.\n"
            "Aucune vérification, aucune alerte pendant cette plage.",
            reply_markup=keyboards.quiet(config),
        )
    else:
        await message.answer("✅ Veille désactivée — surveillance en continu.",
                             reply_markup=keyboards.quiet(config))


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


# --------------------------------------------------------------------------
# Navigation au doigt
# --------------------------------------------------------------------------

def _home_markup(config: WatchConfig, monitor: Monitor | None) -> InlineKeyboardMarkup:
    return keyboards.home(config, monitor.sleeping if monitor else config.is_quiet_now())


@router.callback_query(F.data == "nav:home")
async def on_home(callback: CallbackQuery, config: WatchConfig, monitor: Monitor | None = None) -> None:
    await _show(callback, render_home(config, monitor), _home_markup(config, monitor))


@router.callback_query(F.data == "nav:check")
async def on_check(callback: CallbackQuery, config: WatchConfig, monitor: Monitor) -> None:
    await callback.answer("Vérification en cours…")
    tick = await monitor.check_once()
    if not tick.ok:
        body = ("⚠️ <b>Lecture impossible</b>\n\n"
                f"<code>{html.escape((tick.error or 'inconnu')[:350])}</code>")
    else:
        available = sorted(size for size, ok in tick.sizes.items() if ok)
        body = (
            "🔎 <b>Vérification immédiate</b>\n\n"
            f"📦 {tick.watched_summary(config.sizes)}\n"
            f"🛒 Toutes tailles dispo : {html.escape(', '.join(available)) if available else 'aucune'}\n"
            f"⏱ {tick.elapsed * 1000:.0f} ms  •  lecture : <code>{tick.strategy}</code>"
        )
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(body, reply_markup=keyboards.back_only(),
                                             disable_web_page_preview=True)


@router.callback_query(F.data == "nav:status")
async def on_status(callback: CallbackQuery, monitor: Monitor) -> None:
    await _show(callback, "\n".join(monitor.status_lines()), keyboards.back_only())


@router.callback_query(F.data == "nav:help")
async def on_help(callback: CallbackQuery) -> None:
    await _show(callback, HELP, keyboards.back_only())


@router.callback_query(F.data == "nav:sizes")
async def on_sizes(callback: CallbackQuery, config: WatchConfig) -> None:
    text = (
        "📏 <b>Tailles surveillées</b>\n\n"
        f"Actuellement : <b>{html.escape(', '.join(config.sizes))}</b>\n\n"
        "<i>Touche une taille pour l'ajouter ou la retirer.</i>"
    )
    await _show(callback, text, keyboards.sizes(config))


@router.callback_query(F.data.startswith("size:"))
async def on_size_toggle(callback: CallbackQuery, config: WatchConfig, store: StateStore,
                         settings: StockWatchSettings) -> None:
    if not _owner_ok(callback, settings):
        await callback.answer("Réservé au propriétaire du bot.", show_alert=True)
        return
    size = (callback.data or "").split(":", 1)[1]
    if size in config.sizes:
        if len(config.sizes) == 1:
            await callback.answer("Il faut surveiller au moins une taille.", show_alert=True)
            return
        config.sizes.remove(size)
        notice = f"{size} retirée"
    else:
        config.sizes.append(size)
        config.sizes.sort(key=lambda value: keyboards.ALL_SIZES.index(value)
                          if value in keyboards.ALL_SIZES else 99)
        notice = f"{size} ajoutée"
    await store.set_override("sizes", list(config.sizes))
    text = (
        "📏 <b>Tailles surveillées</b>\n\n"
        f"Actuellement : <b>{html.escape(', '.join(config.sizes))}</b>\n\n"
        "<i>Touche une taille pour l'ajouter ou la retirer.</i>"
    )
    await _show(callback, text, keyboards.sizes(config), notice)


@router.callback_query(F.data == "nav:interval")
async def on_interval_menu(callback: CallbackQuery, config: WatchConfig) -> None:
    text = (
        "⚡ <b>Cadence de vérification</b>\n\n"
        f"Actuellement : une vérification toutes les <b>{config.poll_interval:g} s</b>.\n\n"
        "<i>Plus c'est rapide, plus l'alerte est immédiate — et plus le site est sollicité.</i>"
    )
    await _show(callback, text, keyboards.intervals(config))


@router.callback_query(F.data.startswith("int:"))
async def on_interval_set(callback: CallbackQuery, config: WatchConfig, store: StateStore,
                          settings: StockWatchSettings) -> None:
    if not _owner_ok(callback, settings):
        await callback.answer("Réservé au propriétaire du bot.", show_alert=True)
        return
    value = float((callback.data or "int:1").split(":", 1)[1])
    config.poll_interval = max(0.2, min(3600.0, value))
    await store.set_override("poll_interval", config.poll_interval)
    text = (
        "⚡ <b>Cadence de vérification</b>\n\n"
        f"Actuellement : une vérification toutes les <b>{config.poll_interval:g} s</b>.\n\n"
        "<i>Plus c'est rapide, plus l'alerte est immédiate — et plus le site est sollicité.</i>"
    )
    await _show(callback, text, keyboards.intervals(config), f"Cadence : {config.poll_interval:g} s")


@router.callback_query(F.data == "nav:quiet")
async def on_quiet_menu(callback: CallbackQuery, config: WatchConfig) -> None:
    current = format_window(config.quiet_start, config.quiet_end) if config.quiet_enabled else "désactivée"
    text = (
        "🌙 <b>Veille nocturne</b>\n\n"
        f"Actuellement : <b>{current}</b>  <i>({html.escape(config.timezone)})</i>\n\n"
        "Pendant la veille, le bot ne vérifie plus rien : pas d'alerte, pas de trafic.\n"
        "<i>Il reprend tout seul à l'heure de fin.</i>"
    )
    await _show(callback, text, keyboards.quiet(config))


@router.callback_query(F.data.startswith("quiet:"))
async def on_quiet_set(callback: CallbackQuery, config: WatchConfig, store: StateStore,
                       settings: StockWatchSettings) -> None:
    if not _owner_ok(callback, settings):
        await callback.answer("Réservé au propriétaire du bot.", show_alert=True)
        return
    _, start, end = (callback.data or "quiet:-1:-1").split(":")
    config.quiet_start, config.quiet_end = int(start), int(end)
    await store.set_override("quiet_start", config.quiet_start)
    await store.set_override("quiet_end", config.quiet_end)
    current = format_window(config.quiet_start, config.quiet_end) if config.quiet_enabled else "désactivée"
    text = (
        "🌙 <b>Veille nocturne</b>\n\n"
        f"Actuellement : <b>{current}</b>  <i>({html.escape(config.timezone)})</i>\n\n"
        "Pendant la veille, le bot ne vérifie plus rien : pas d'alerte, pas de trafic.\n"
        "<i>Il reprend tout seul à l'heure de fin.</i>"
    )
    await _show(callback, text, keyboards.quiet(config), f"Veille : {current}")


@router.callback_query(F.data == "nav:colours")
async def on_colours(callback: CallbackQuery, config: WatchConfig, monitor: Monitor | None = None) -> None:
    labels = monitor.last_labels if monitor else {}
    if not labels:
        text = (
            "🎨 <b>Coloris</b>\n\n"
            "Aucun coloris identifié pour l'instant — lance une vérification, "
            "puis reviens ici.\n\n"
            f"Coloris suivi : <b>{html.escape(config.product_color or 'non précisé')}</b>"
        )
    else:
        text = (
            "🎨 <b>Coloris</b>\n\n"
            f"Coloris suivi : <b>{html.escape(config.product_color or 'non précisé')}</b>\n\n"
            "<i>Choisis celui qui correspond à la fiche que tu regardes.</i>"
        )
    await _show(callback, text, keyboards.colours(labels, config.product_color))


@router.callback_query(F.data.startswith("col:"))
async def on_colour_set(callback: CallbackQuery, config: WatchConfig, store: StateStore,
                        settings: StockWatchSettings, monitor: Monitor | None = None) -> None:
    if not _owner_ok(callback, settings):
        await callback.answer("Réservé au propriétaire du bot.", show_alert=True)
        return
    colour = (callback.data or "col:").split(":", 1)[1]
    config.product_color = colour
    config.product_id = ""
    await store.set_override("product_color", colour)
    await store.set_override("product_id", "")
    store.sizes.clear()          # la mémoire du stock était celle d'un autre coloris
    await store.save()
    labels = monitor.last_labels if monitor else {}
    text = (
        "🎨 <b>Coloris</b>\n\n"
        f"Coloris suivi : <b>{html.escape(colour)}</b>\n\n"
        "<i>Vérifie avec « 🔎 Vérifier maintenant » que les tailles correspondent à la fiche.</i>"
    )
    await _show(callback, text, keyboards.colours(labels, colour), f"Coloris : {colour}")


@router.callback_query(F.data.in_({"nav:pause", "nav:resume"}))
async def on_pause_toggle(callback: CallbackQuery, config: WatchConfig, store: StateStore,
                          settings: StockWatchSettings, monitor: Monitor | None = None) -> None:
    if not _owner_ok(callback, settings):
        await callback.answer("Réservé au propriétaire du bot.", show_alert=True)
        return
    config.paused = (callback.data == "nav:pause")
    await store.set_override("paused", config.paused)
    notice = "Surveillance en pause" if config.paused else "Surveillance relancée"
    await _show(callback, render_home(config, monitor), _home_markup(config, monitor), notice)
