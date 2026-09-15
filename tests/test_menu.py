"""Le tableau de bord : rendu, claviers et boutons."""

from __future__ import annotations

from dataclasses import dataclass, field

from stockwatch import keyboards
from stockwatch.bot import (
    on_colour_set,
    on_interval_set,
    on_pause_toggle,
    on_quiet_set,
    on_size_toggle,
    render_home,
)
from stockwatch.config import StockWatchSettings


@dataclass
class FakeUser:
    id: int = 10


@dataclass
class FakeCallback:
    data: str
    from_user: FakeUser = field(default_factory=FakeUser)
    message: object = None
    answers: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        (self.alerts if show_alert else self.answers).append(text)


def texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


class TestRendering:
    def test_home_card_shows_the_essentials(self, config):
        config.product_color = "Blanc"
        card = render_home(config)
        assert "Icon Henley" in card
        assert "Blanc" in card
        assert "XS, S" in card
        assert "1 s" in card

    def test_home_card_mentions_the_quiet_window(self, config):
        config.quiet_start, config.quiet_end = 20, 7
        assert "20h00 → 07h00" in render_home(config)

    def test_home_card_mentions_a_pause(self, config):
        config.paused = True
        assert "pause" in render_home(config)


class TestKeyboards:
    def test_watched_sizes_are_ticked(self, config):
        labels = texts(keyboards.sizes(config))
        assert "✅ XS" in labels and "✅ S" in labels
        assert "⬜️ M" in labels

    def test_current_interval_is_marked(self, config):
        config.poll_interval = 30
        assert "🔘 30 s" in texts(keyboards.intervals(config))

    def test_quiet_presets_mark_the_active_one(self, config):
        config.quiet_start, config.quiet_end = 20, 7
        assert "🔘 20h → 7h" in texts(keyboards.quiet(config))

    def test_colour_buttons_come_from_the_page(self):
        markup = keyboards.colours({"1": "Blanc", "2": "Vert sauge"}, "Blanc")
        assert "🔘 Blanc" in texts(markup)
        assert "⚪️ Vert sauge" in texts(markup)

    def test_home_offers_a_link_to_the_product(self, config):
        urls = [b.url for row in keyboards.home(config, False).inline_keyboard for b in row if b.url]
        assert config.product_url in urls


class TestButtons:
    async def test_toggling_a_size_updates_the_watch_list(self, config, state, settings):
        await on_size_toggle(FakeCallback("size:M"), config, state, settings)
        assert "M" in config.sizes
        await on_size_toggle(FakeCallback("size:M"), config, state, settings)
        assert "M" not in config.sizes
        assert state.overrides["sizes"] == config.sizes

    async def test_the_last_size_cannot_be_removed(self, config, state, settings):
        config.sizes = ["XS"]
        callback = FakeCallback("size:XS")
        await on_size_toggle(callback, config, state, settings)
        assert config.sizes == ["XS"]
        assert callback.alerts   # prévenu par une alerte, pas silencieusement

    async def test_sizes_stay_in_a_natural_order(self, config, state, settings):
        config.sizes = ["S"]
        await on_size_toggle(FakeCallback("size:XXL"), config, state, settings)
        await on_size_toggle(FakeCallback("size:XS"), config, state, settings)
        assert config.sizes == ["XS", "S", "XXL"]

    async def test_interval_button(self, config, state, settings):
        await on_interval_set(FakeCallback("int:30"), config, state, settings)
        assert config.poll_interval == 30
        assert state.overrides["poll_interval"] == 30

    async def test_quiet_buttons_set_and_clear_the_window(self, config, state, settings):
        await on_quiet_set(FakeCallback("quiet:20:7"), config, state, settings)
        assert (config.quiet_start, config.quiet_end) == (20, 7)
        assert config.quiet_enabled is True
        await on_quiet_set(FakeCallback("quiet:-1:-1"), config, state, settings)
        assert config.quiet_enabled is False

    async def test_colour_button_forgets_the_previous_stock(self, config, state, settings):
        state.record_size("XS", True)
        await on_colour_set(FakeCallback("col:Blanc"), config, state, settings)
        assert config.product_color == "Blanc"
        assert config.product_id == ""
        assert state.sizes == {}

    async def test_pause_and_resume(self, config, state, settings):
        await on_pause_toggle(FakeCallback("nav:pause"), config, state, settings)
        assert config.paused is True
        await on_pause_toggle(FakeCallback("nav:resume"), config, state, settings)
        assert config.paused is False

    async def test_only_the_owner_may_press(self, config, state):
        owned = StockWatchSettings(_env_file=None, STOCKWATCH_BOT_TOKEN="1:x", STOCKWATCH_OWNER_ID="999")
        callback = FakeCallback("size:M")
        await on_size_toggle(callback, config, state, owned)
        assert "M" not in config.sizes
        assert callback.alerts
