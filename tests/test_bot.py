"""Command handlers, driven with light stand-ins for aiogram's types."""

from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.filters import CommandObject

from stockwatch.bot import cmd_interval, cmd_product, cmd_sizes, cmd_start, cmd_stop
from stockwatch.config import StockWatchSettings


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeMessage:
    chat: FakeChat = field(default_factory=lambda: FakeChat(10))
    from_user: FakeUser = field(default_factory=lambda: FakeUser(10))
    sent: list[str] = field(default_factory=list)

    async def answer(self, text: str, **_: object) -> FakeMessage:
        self.sent.append(text)
        return self


def command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="x", args=args)


class TestSubscription:
    async def test_start_subscribes_then_stop_unsubscribes(self, state, config):
        message = FakeMessage()
        await cmd_start(message, state, config)
        assert state.subscribers == {10}
        await cmd_stop(message, state)
        assert state.subscribers == set()

    async def test_start_is_idempotent(self, state, config):
        message = FakeMessage()
        await cmd_start(message, state, config)
        await cmd_start(message, state, config)
        assert state.subscribers == {10}
        assert "déjà abonné" in message.sent[-1]


class TestSizes:
    async def test_sets_normalised_sizes(self, settings, config, state):
        message = FakeMessage()
        await cmd_sizes(message, command("xs, small"), config, state, settings)
        assert config.sizes == ["XS", "S"]
        assert state.overrides["sizes"] == ["XS", "S"]

    async def test_reports_unknown_sizes(self, settings, config, state):
        message = FakeMessage()
        await cmd_sizes(message, command("XS, bleu"), config, state, settings)
        assert config.sizes == ["XS"]
        assert "Ignoré" in message.sent[-1]

    async def test_refuses_an_empty_selection(self, settings, config, state):
        message = FakeMessage()
        await cmd_sizes(message, command("bleu, rouge"), config, state, settings)
        assert config.sizes == ["XS", "S"]
        assert "Aucune taille valide" in message.sent[-1]

    async def test_only_the_owner_may_change_them(self, config, state, settings):
        owned = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_OWNER_ID="999")
        message = FakeMessage()
        await cmd_sizes(message, command("M"), config, state, owned)
        assert config.sizes == ["XS", "S"]
        assert "propriétaire" in message.sent[-1]

        message = FakeMessage(chat=FakeChat(999), from_user=FakeUser(999))
        await cmd_sizes(message, command("M"), config, state, owned)
        assert config.sizes == ["M"]


class TestProductAndInterval:
    async def test_switching_product_clears_the_stock_memory(self, settings, config, state):
        state.record_size("XS", True)
        message = FakeMessage()
        await cmd_product(message, command("https://example.test/p/other-99999999"), config, state, settings)
        assert config.product_url.endswith("other-99999999")
        assert state.sizes == {}

    async def test_rejects_a_non_http_url(self, settings, config, state):
        message = FakeMessage()
        await cmd_product(message, command("javascript:alert(1)"), config, state, settings)
        assert config.product_url.endswith("icon-henley-63586319-2")
        assert "invalide" in message.sent[-1]

    async def test_interval_is_clamped_and_warns_below_one_second(self, settings, config, state):
        message = FakeMessage()
        await cmd_interval(message, command("0,05"), config, state, settings)
        assert config.poll_interval == 0.2
        assert "⚠️" in message.sent[-1]

    async def test_interval_rejects_garbage(self, settings, config, state):
        message = FakeMessage()
        await cmd_interval(message, command("vite"), config, state, settings)
        assert config.poll_interval == 1.0
        assert "invalide" in message.sent[-1]
