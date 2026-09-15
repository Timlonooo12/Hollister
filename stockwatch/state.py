"""Durable state: subscribers, per-size stock memory and runtime overrides.

Stored as a single small JSON file written atomically, so a restart never
replays an alert for a size that was already in stock before the restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class SizeState:
    available: bool
    changed_at: datetime
    last_alert_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "changed_at": self.changed_at.isoformat(),
            "last_alert_at": self.last_alert_at.isoformat() if self.last_alert_at else None,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SizeState:
        return cls(
            available=bool(raw.get("available")),
            changed_at=_parse_dt(raw.get("changed_at")) or utcnow(),
            last_alert_at=_parse_dt(raw.get("last_alert_at")),
        )


class StateStore:
    """Async-safe wrapper around the JSON state file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self.subscribers: set[int] = set()
        self.sizes: dict[str, SizeState] = {}
        self.overrides: dict[str, Any] = {}
        # Cookie renouvelé automatiquement : gardé ici plutôt que dans .env,
        # pour ne jamais réécrire un fichier que l'utilisateur édite aussi.
        self.session: dict[str, Any] = {}
        self.stats: dict[str, Any] = {
            "checks": 0,
            "alerts": 0,
            "errors": 0,
            "not_modified": 0,
            "blocked": 0,
            "partial_pages": 0,
            "cookies_renewed": 0,
            "bytes_total": 0,
            "bytes_today": 0,
            "bytes_day": "",
        }

    # -- load / save -------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("State file %s unreadable (%s) — starting fresh", self.path, exc)
            return
        if not isinstance(raw, dict):
            return
        self.subscribers = {int(cid) for cid in raw.get("subscribers", []) if str(cid).lstrip("-").isdigit()}
        self.sizes = {
            str(size): SizeState.from_json(payload)
            for size, payload in (raw.get("sizes") or {}).items()
            if isinstance(payload, dict)
        }
        self.overrides = dict(raw.get("overrides") or {})
        self.session = dict(raw.get("session") or {})
        stats = raw.get("stats")
        if isinstance(stats, dict):
            self.stats.update({k: stats.get(k, v) for k, v in self.stats.items()})

    def _snapshot(self) -> dict[str, Any]:
        return {
            "subscribers": sorted(self.subscribers),
            "sizes": {size: state.to_json() for size, state in self.sizes.items()},
            "overrides": self.overrides,
            "session": self.session,
            "stats": self.stats,
            "saved_at": utcnow().isoformat(),
        }

    async def save(self) -> None:
        async with self._lock:
            payload = self._snapshot()
            await asyncio.to_thread(self._write, payload)

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent or "."), prefix=".stockwatch-", suffix=".tmp", delete=False
        )
        try:
            with handle as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(handle.name, self.path)
        except OSError as exc:  # never let persistence kill the watcher
            logger.warning("Could not persist state to %s: %s", self.path, exc)
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    # -- subscribers -------------------------------------------------------
    async def add_subscriber(self, chat_id: int) -> bool:
        if chat_id in self.subscribers:
            return False
        self.subscribers.add(chat_id)
        await self.save()
        return True

    async def remove_subscriber(self, chat_id: int) -> bool:
        if chat_id not in self.subscribers:
            return False
        self.subscribers.discard(chat_id)
        await self.save()
        return True

    # -- sizes -------------------------------------------------------------
    def get_size(self, size: str) -> SizeState | None:
        return self.sizes.get(size)

    def record_size(self, size: str, available: bool, *, alerted: bool = False) -> None:
        previous = self.sizes.get(size)
        if previous is None:
            self.sizes[size] = SizeState(available=available, changed_at=utcnow())
        elif previous.available != available:
            previous.available = available
            previous.changed_at = utcnow()
        if alerted:
            self.sizes[size].last_alert_at = utcnow()

    # -- overrides ---------------------------------------------------------
    async def remember_session(self, cookie: str, user_agent: str) -> None:
        self.session = {
            "cookie": cookie,
            "user_agent": user_agent,
            "obtained_at": utcnow().isoformat(),
        }
        await self.save()

    def session_age_minutes(self) -> float | None:
        obtained = _parse_dt(self.session.get("obtained_at"))
        return None if obtained is None else (utcnow() - obtained).total_seconds() / 60

    async def set_override(self, key: str, value: Any) -> None:
        self.overrides[key] = value
        await self.save()

    def bump(self, counter: str, amount: int = 1) -> None:
        self.stats[counter] = int(self.stats.get(counter, 0)) + amount

    def add_bytes(self, count: int) -> int:
        """Compte les octets reçus et renvoie le total du jour.

        Le compteur journalier repart à zéro au changement de date UTC, ce qui
        permet d'asseoir un budget quotidien sur un proxy facturé au volume.
        """
        today = utcnow().strftime("%Y-%m-%d")
        if self.stats.get("bytes_day") != today:
            self.stats["bytes_day"] = today
            self.stats["bytes_today"] = 0
        self.stats["bytes_today"] = int(self.stats.get("bytes_today", 0)) + max(0, count)
        self.stats["bytes_total"] = int(self.stats.get("bytes_total", 0)) + max(0, count)
        return self.stats["bytes_today"]


def apply_overrides(config: Any, overrides: dict[str, Any]) -> None:
    """Re-apply `/produit`, `/tailles` and `/intervalle` after a restart."""
    url = overrides.get("product_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        config.product_url = url
    label = overrides.get("product_label")
    if isinstance(label, str) and label:
        config.product_label = label
    sizes = overrides.get("sizes")
    if isinstance(sizes, list) and sizes:
        config.sizes = [str(size) for size in sizes]
    interval = overrides.get("poll_interval")
    if isinstance(interval, int | float) and interval > 0:
        config.poll_interval = max(0.2, float(interval))
    variant = overrides.get("product_id")
    if isinstance(variant, str):
        config.product_id = variant.strip()
    colour = overrides.get("product_color")
    if isinstance(colour, str):
        config.product_color = colour.strip()
    for key in ("quiet_start", "quiet_end"):
        hour = overrides.get(key)
        if isinstance(hour, int) and -1 <= hour <= 23:
            setattr(config, key, hour)
    paused = overrides.get("paused")
    if isinstance(paused, bool):
        config.paused = paused


__all__ = ["SizeState", "StateStore", "apply_overrides", "utcnow"]
