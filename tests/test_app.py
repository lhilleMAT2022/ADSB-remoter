from __future__ import annotations

from adsb_console.app import ADSBConsoleApp


def test_app_constructs_without_textual_attribute_collisions() -> None:
    app = ADSBConsoleApp(source=("127.0.0.1", 28887))

    assert app.source == ("127.0.0.1", 28887)
    assert app.event_log is None
    assert app.tracker.message_count == 0
    assert app.selected_observer.is_local
    assert len(app.observers) == 2
    assert app.observers[0].name == "Goat Island Lighthouse"
    assert app.observers[1].name == "MathWorks Apple Hill"
