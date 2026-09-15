"""Fenêtre de veille : les heures où le bot ne vérifie rien.

Surveiller un réassort à 3 h du matin n'a pas d'intérêt — et sur un proxy
facturé au volume, ça coûte la moitié de la facture. Les horaires sont exprimés
dans le fuseau de l'utilisateur, jamais en UTC : un VPS tourne en UTC et
« 20 h » ne veut pas dire la même chose là-bas.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def is_quiet(now: datetime, start_hour: int, end_hour: int) -> bool:
    """La fenêtre passe-t-elle par minuit ? 20 → 7 oui, 1 → 6 non."""
    if start_hour == end_hour:
        return False
    hour = now.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def next_wake(now: datetime, start_hour: int, end_hour: int) -> datetime:
    """Le prochain instant où la surveillance reprend."""
    wake = now.replace(hour=end_hour % 24, minute=0, second=0, microsecond=0)
    if wake <= now:
        wake += timedelta(days=1)
    return wake


def seconds_until_wake(now: datetime, start_hour: int, end_hour: int) -> float:
    return max(1.0, (next_wake(now, start_hour, end_hour) - now).total_seconds())


def format_window(start_hour: int, end_hour: int) -> str:
    return f"{time(hour=start_hour % 24):%Hh%M} → {time(hour=end_hour % 24):%Hh%M}"
