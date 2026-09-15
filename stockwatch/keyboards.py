"""Claviers inline du bot.

Tout se pilote au doigt depuis un seul message que l'on met à jour sur place,
plutôt qu'en tapant des commandes et en empilant les réponses dans le fil.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import WatchConfig

ALL_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL"]
INTERVAL_CHOICES = [(1, "1 s"), (3, "3 s"), (10, "10 s"), (30, "30 s"), (60, "1 min"), (300, "5 min")]
QUIET_CHOICES = [(-1, -1, "Désactivée"), (20, 7, "20h → 7h"), (22, 8, "22h → 8h"), (0, 8, "0h → 8h")]

BACK = InlineKeyboardButton(text="⬅️ Menu", callback_data="nav:home")


def _rows(buttons: list[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[index:index + per_row] for index in range(0, len(buttons), per_row)]


def home(config: WatchConfig, sleeping: bool) -> InlineKeyboardMarkup:
    pause = (
        InlineKeyboardButton(text="▶️ Reprendre", callback_data="nav:resume")
        if config.paused
        else InlineKeyboardButton(text="⏸ Mettre en pause", callback_data="nav:pause")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔎 Vérifier maintenant", callback_data="nav:check"),
            InlineKeyboardButton(text="🔄 Actualiser", callback_data="nav:home"),
        ],
        [
            InlineKeyboardButton(text="📏 Tailles", callback_data="nav:sizes"),
            InlineKeyboardButton(text="🎨 Coloris", callback_data="nav:colours"),
        ],
        [
            InlineKeyboardButton(text="⚡ Cadence", callback_data="nav:interval"),
            InlineKeyboardButton(text=("😴 Veille" if sleeping else "🌙 Veille"), callback_data="nav:quiet"),
        ],
        [pause, InlineKeyboardButton(text="📊 Détails", callback_data="nav:status")],
        [
            InlineKeyboardButton(text="🛒 Ouvrir la fiche", url=config.product_url),
            InlineKeyboardButton(text="❓ Aide", callback_data="nav:help"),
        ],
    ])


def sizes(config: WatchConfig) -> InlineKeyboardMarkup:
    watched = set(config.sizes)
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅' if size in watched else '⬜️'} {size}",
            callback_data=f"size:{size}",
        )
        for size in ALL_SIZES
    ]
    return InlineKeyboardMarkup(inline_keyboard=[*_rows(buttons, 4), [BACK]])


def intervals(config: WatchConfig) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'🔘' if abs(config.poll_interval - value) < 0.01 else '⚪️'} {label}",
            callback_data=f"int:{value}",
        )
        for value, label in INTERVAL_CHOICES
    ]
    return InlineKeyboardMarkup(inline_keyboard=[*_rows(buttons, 3), [BACK]])


def quiet(config: WatchConfig) -> InlineKeyboardMarkup:
    buttons = []
    for start, end, label in QUIET_CHOICES:
        active = (config.quiet_start, config.quiet_end) == (start, end)
        buttons.append(
            InlineKeyboardButton(
                text=f"{'🔘' if active else '⚪️'} {label}",
                callback_data=f"quiet:{start}:{end}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[*_rows(buttons, 2), [BACK]])


def colours(labels: dict[str, str], current: str) -> InlineKeyboardMarkup:
    """Un bouton par coloris vu dans la page, le coloris suivi étant coché."""
    buttons = []
    for name in sorted(set(labels.values()))[:12]:
        active = current and current.casefold() == name.casefold()
        buttons.append(
            InlineKeyboardButton(
                text=f"{'🔘' if active else '⚪️'} {name}"[:64],
                callback_data=f"col:{name}"[:64],
            )
        )
    rows = _rows(buttons, 2) if buttons else []
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [BACK]])


def back_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[BACK]])


def alert(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🛒 Commander maintenant", url=url),
    ]])
