"""The legacy AppKit interface's tests: what every one of them must stop when it ends."""

import pytest
import rumps


@pytest.fixture(autouse=True)
def stop_menu_timers():
    """Every legacy MenuBar starts a repeating rumps timer on the main run loop that nothing
    stops, and the run loop keeps it, its menu and its Controller alive. A later test that spins
    the run loop (test_panel, test_keystore_launch, the pill's) would then tick every menu built
    before it, against that later test's config, key store and paths. So a test's timers stop
    when it ends."""
    before = set(rumps.timers())
    yield
    for timer in set(rumps.timers()) - before:
        timer.stop()
