"""The menu bar menu: its rules (lyssna.menu) and the real menu built from a real controller.
Nothing is shown on screen: the NSMenu is built but the app never runs."""

import pytest
from AppKit import NSMenu, NSMenuItem

from lyssna import config, menu
from lyssna.menu import MenuState, separators_shown, visibility
from lyssna.providers import kokoro

# -- rules ----------------------------------------------------------------------------------


def test_idle_hides_now_playing():
    assert visibility(MenuState(player="idle"))["now_playing"] is False
    for player in ("loading", "playing", "paused"):
        assert visibility(MenuState(player=player))["now_playing"] is True
    assert visibility(MenuState(message="Nothing selected"))["now_playing"] is True
    assert visibility(MenuState(downloading=True))["now_playing"] is True


def test_zero_cost_hides_usage():
    assert visibility(MenuState(usage_line=""))["usage"] is False
    assert visibility(MenuState(usage_line="This month: 4k characters, about $0.10"))["usage"] is True


def test_local_only_hides_api_keys():
    assert visibility(MenuState(local_only=False))["api_keys"] is True
    assert visibility(MenuState(local_only=True))["api_keys"] is False


def test_no_voice_list_hides_voice():
    assert visibility(MenuState(has_voices=False))["voice"] is False


def test_transport_shows_only_while_reading():
    assert visibility(MenuState(player="idle"))["transport"] is False
    assert visibility(MenuState(player="idle", message="Nothing selected", downloading=True))["transport"] is False
    for player in ("loading", "playing", "paused"):
        assert visibility(MenuState(player=player))["transport"] is True


@pytest.mark.parametrize(("rows", "expected"), [
    ([True, None, True], [True]),
    ([True, None, False, None, True], [True, False]),   # an empty group: one separator, not two
    ([None, True, None], [False, False]),              # never first or last
    ([True, None, False], [False]),                    # nothing visible after it
    ([False, None, True], [False]),                    # nothing visible before it
    ([True, None, None, True], [True, False]),
])
def test_separators_never_double_up_or_dangle(rows, expected):
    assert separators_shown(rows) == expected


def test_wording():
    assert menu.local_only_title(False) == "Keep Everything on This Mac"
    assert menu.local_only_title(True) == "Keep Everything on This Mac (Set by Your Organization)"
    assert menu.provider_title("Cartesia Sonic", True, "no API key") == "Cartesia Sonic"
    assert menu.provider_title("Cartesia Sonic", False, "no API key") == "Cartesia Sonic · no API key"
    assert menu.now_playing("paused", 2, 5, "Hello") == "Paused 2 of 5: Hello"
    assert menu.now_playing("idle", 0, 0, "") == ""


def test_engine_row_label_drops_the_on_this_mac_wrapper():
    assert menu.engine_row_label("On this Mac (Kokoro)") == "Kokoro"
    assert menu.engine_row_label("On this Mac (S1-mini by Superwhisper)") == "S1-mini by Superwhisper"
    assert menu.engine_row_label("Inworld TTS-2") == "Inworld TTS-2"
    assert menu.engine_row_label("OpenAI (gpt-transcribe)") == "OpenAI (gpt-transcribe)"


def test_privacy_symbol():
    assert menu.privacy_symbol(True) == "lock"
    assert menu.privacy_symbol(False) == "arrow.up.right"


def test_tidy_separators_on_a_real_nsmenu():
    from lyssna.app import tidy_separators

    m = NSMenu.alloc().init()
    a = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("A", None, "")
    hidden = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Hidden", None, "")
    hidden.setHidden_(True)
    b = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("B", None, "")
    s1, s2 = NSMenuItem.separatorItem(), NSMenuItem.separatorItem()
    for item in (a, s1, hidden, s2, b):
        m.addItem_(item)
    tidy_separators(m)
    assert (s1.isHidden(), s2.isHidden()) == (False, True)
    hidden.setHidden_(False)
    tidy_separators(m)
    assert (s1.isHidden(), s2.isHidden()) == (False, False)


# -- the real menu --------------------------------------------------------------------------


@pytest.fixture
def menubar(monkeypatch):
    monkeypatch.setattr(kokoro.KokoroProvider, "can_run", classmethod(lambda cls: True))
    monkeypatch.setattr(kokoro.KokoroProvider, "prepare", lambda self, on_error=None: None)  # no download
    config.ensure_dirs()
    config.CONFIG_PATH.write_text(config.TEMPLATE)
    from lyssna.app import MenuBar
    from lyssna.controller import Controller

    return MenuBar(Controller())


def visible_rows(nsmenu) -> list[str]:
    return ["---" if i.isSeparatorItem() else i.title() for i in nsmenu.itemArray() if not i.isHidden()]


def test_top_level_menu_when_idle(menubar):
    assert visible_rows(menubar._menu._menu) == [
        "Listen", "Read Selection", "Read Clipboard",
        "Speed: 1.2×", "Voice: Heart", "Voice Provider: On this Mac (Kokoro)", "Text stays on this Mac", "---",
        "Dictate", "Start Dictation · hold fn", "History…", "Transcription: On this Mac (Parakeet)",
        "Cleanup: On this Mac (S1-mini by Superwhisper)", "Speech stays on this Mac", "---",
        "Settings", "Setup…", "About Lyssna", "---",
        "Quit Lyssna",
    ]


def test_sections_have_native_headers(menubar):
    items = {i.title(): i for i in menubar._menu._menu.itemArray()}
    for title in ("Listen", "Dictate"):
        assert items[title].isSectionHeader() and items[title].action() is None


def test_menu_while_reading_shows_the_transport(menubar, monkeypatch):
    from lyssna.controller import Controller
    from lyssna.player import Status

    monkeypatch.setattr(Controller, "status", property(lambda self: Status(state="playing", index=1, total=3, text="Hello")))
    menubar._tick(None)
    rows = visible_rows(menubar._menu._menu)
    assert rows[:9] == ["Listen", "Read Selection", "Read Clipboard", "Reading 1 of 3: Hello",
                        "Pause / Resume", "Stop", "Previous Sentence", "Next Sentence", "Speed: 1.2×"]


def test_permission_warning_sits_above_listen(menubar):
    menubar.show_permission_warning()
    assert visible_rows(menubar._menu._menu)[:2] == ["Allow Accessibility Access…", "Listen"]


def test_privacy_lines_show_where_things_go(menubar, fake_keychain):
    from lyssna import keychain

    assert menubar._privacy_symbols == {"reading": "lock", "dictation": "lock"}
    assert menubar.item_privacy._menuitem.image() is not None
    fake_keychain.items["openai"] = "k"
    keychain.clear_cache()
    menubar._set_transcriber("openai")
    assert menubar._privacy_symbols["dictation"] == "arrow.up.right"


def test_provider_menu_groups_local_and_online(menubar):
    assert visible_rows(menubar.provider_menu._menu) == [
        "On this Mac", "Kokoro · 140 MB download", "System Voice",
        "Online, with your API key", "Inworld TTS-2 · no API key", "Inworld TTS-2 Flash · no API key",
        "Cartesia Sonic · no API key", "ElevenLabs · no API key", "OpenAI TTS · no API key",
    ]
    headers = [i.title() for i in menubar.provider_menu._menu.itemArray() if i.isSectionHeader()]
    assert headers == ["On this Mac", "Online, with your API key"]


def test_settings_menu(menubar):
    assert visible_rows(menubar.settings_menu._menu) == [
        "Play Button on Selection", "Auto-hide Squiggle", "Squiggle Wave", "Keep Everything on This Mac", "API Keys", "---",
        "Edit Config File…", "Open Log", "Clear Audio Cache",
    ]
    keys = visible_rows(menubar.key_menu._menu)
    assert "---" not in keys and not any(k.startswith("Remove") for k in keys)  # no key stored


def test_auto_hide_squiggle_is_checked_persisted_and_applied(menubar):
    applied = []
    menubar.on_auto_hide = applied.append
    item = menubar.item_auto_hide
    assert item.state == 1 and menubar.ctl.auto_hide_enabled is True
    item.callback(item)
    assert item.state == 0 and applied == [False]
    assert config.load()["ui"]["auto_hide"] is False
    item.callback(item)
    assert item.state == 1 and applied == [False, True] and config.load()["ui"]["auto_hide"] is True


def test_squiggle_wave_is_checked_persisted_and_applied(menubar):
    applied = []
    menubar.on_wave = applied.append
    item = menubar.item_wave
    assert item.state == 1 and menubar.ctl.wave_enabled is True
    item.callback(item)
    assert item.state == 0 and applied == [False]
    assert config.load()["ui"]["wave"] is False
    item.callback(item)
    assert item.state == 1 and applied == [False, True] and config.load()["ui"]["wave"] is True


def test_stored_key_shows_its_remove_entry(menubar):
    from lyssna import keychain

    keychain.set("cartesia", "k")
    menubar._refresh_checks()
    keys = visible_rows(menubar.key_menu._menu)
    assert keys[-2:] == ["---", "Remove Cartesia Sonic Key…"]


def test_local_only_hides_api_keys_and_online_providers(menubar):
    menubar.ctl.set_local_only(True)
    menubar._refresh_checks()
    assert "API Keys" not in visible_rows(menubar.settings_menu._menu)
    assert visible_rows(menubar.provider_menu._menu) == ["On this Mac", "Kokoro · 140 MB download", "System Voice"]
    menubar.ctl.set_local_only(False)
    menubar._refresh_checks()
    assert "Online, with your API key" in visible_rows(menubar.provider_menu._menu)


def test_managed_local_only_is_greyed_out(menubar, managed_prefs):
    managed_prefs["LocalOnly"] = True
    menubar._refresh_checks()
    item = menubar.item_local._menuitem
    assert item.title() == "Keep Everything on This Mac (Set by Your Organization)"
    assert item.action() is None and item.state() == 1


def test_usage_line_shows_only_with_a_cost(menubar):
    from lyssna import usage

    usage.record("kokoro", 5000, 0.0)
    menubar._apply_visibility()
    assert menubar.item_usage.hidden
    usage.record("cartesia", 2000, 50.0)
    menubar._apply_visibility()
    assert not menubar.item_usage.hidden and menubar.item_usage.title == "This month: 2k characters, about $0.10"
    rows = visible_rows(menubar._menu._menu)
    assert rows[rows.index("Speech stays on this Mac") + 1:][:3] == ["---", "This month: 2k characters, about $0.10", "Settings"]


def test_no_submenu_title_ends_with_an_ellipsis(menubar):
    def walk(nsmenu):
        for item in nsmenu.itemArray():
            if item.hasSubmenu():
                assert not item.title().endswith(("…", "...")), item.title()
                walk(item.submenu())

    walk(menubar._menu._menu)


def test_no_speak_or_engine_in_any_title(menubar):
    def walk(nsmenu):
        for item in nsmenu.itemArray():
            assert "Speak" not in item.title() and "Engine" not in item.title(), item.title()
            if item.hasSubmenu():
                walk(item.submenu())

    walk(menubar._menu._menu)


# -- the Dictation group ---------------------------------------------------------------------


def test_dictation_wording():
    assert menu.dictation_title("fn", recording=False) == "Start Dictation · hold fn"
    assert menu.dictation_title("right_option", recording=False) == "Start Dictation · hold right ⌥"
    assert menu.dictation_title("fn", recording=True) == "Finish Dictation"
    assert menu.dictation_privacy("", "") == "Speech stays on this Mac"
    assert menu.dictation_privacy("OpenAI", "") == "Speech is sent to OpenAI"
    assert menu.dictation_privacy("", "Anthropic") == "Speech stays on this Mac, text is sent to Anthropic"
    assert menu.dictation_privacy("Inworld", "Anthropic") == "Speech is sent to Inworld, text is sent to Anthropic"
    assert menu.cleanup_title("") == "Cleanup: Off"
    assert menu.download_line("voice", 0.43) == "Downloading voice, 43%"
    assert menu.download_line("model", 0.43) == "Getting the speech model ready, 43%"


def test_no_menu_bar_text_while_dictating():
    for state in ("recording", "locked", "working"):
        assert menu.menubar_text_allowed(MenuState(dictation=state)) is False
    for state in ("idle", "notice"):
        assert menu.menubar_text_allowed(MenuState(dictation=state)) is True


def test_dictation_engines_show_their_hints(menubar, monkeypatch):
    from lyssna.dictation import models

    monkeypatch.setattr(models.ModelSet, "present", lambda self: False)
    menubar._refresh_checks()
    assert visible_rows(menubar.transcription_menu._menu) == [
        "On this Mac", "Parakeet · 630 MB download",
        "Online, with your API key", "OpenAI (gpt-transcribe) · no API key", "Inworld STT · no API key"]
    rows = visible_rows(menubar.cleanup_menu._menu)
    assert rows[:2] == ["Off", "On this Mac"] and rows[-2:] == ["Online, with your API key", "Claude Haiku 5.5 · no API key"]
    checked = [i.title() for i in menubar.transcription_menu._menu.itemArray() if i.state() == 1]
    assert checked == ["Parakeet · 630 MB download"]


def test_local_only_hides_online_dictation_engines(menubar):
    menubar.ctl.set_local_only(True)
    menubar._refresh_checks()
    assert [r.split(" · ")[0] for r in visible_rows(menubar.transcription_menu._menu)] == ["On this Mac", "Parakeet"]
    assert [r.split(" · ")[0] for r in visible_rows(menubar.cleanup_menu._menu)] == [
        "Off", "On this Mac", "S1-mini by Superwhisper"]


def test_cleanup_off_is_checked(menubar):
    menubar._set_cleaner("")
    assert menubar.cleanup_menu.title == "Cleanup: Off"
    assert [i.title() for i in menubar.cleanup_menu._menu.itemArray() if i.state() == 1] == ["Off"]


def test_choosing_an_engine_without_its_key(menubar, monkeypatch):
    opened, dictation, toasts = [], [], []
    menubar.on_setup = opened.append
    menubar.on_setup_dictation = dictation.append
    menubar.ctl.subscribe_toast(toasts.append)
    menubar._set_transcriber("openai")
    menubar._set_cleaner("haiku")
    assert dictation == ["openai", "haiku"]  # the setup window's dictation step, at that key
    menubar._set_transcriber("inworld")
    assert opened == ["inworld"]  # shares its key with the Inworld voices: the voice step takes it
    assert toasts == []
    assert config.load()["dictation"]["transcriber"] == "parakeet"  # nothing switched


def test_choosing_an_engine_with_its_key(menubar, fake_keychain):
    from lyssna import keychain

    fake_keychain.items["openai"] = "k"
    keychain.clear_cache()  # building the menu already looked for it
    menubar._set_transcriber("openai")
    assert menubar.transcription_menu.title == "Transcription: OpenAI (gpt-transcribe)"
    assert menubar.item_dictation_privacy.title == "Speech is sent to OpenAI"


def test_history_entry_calls_the_app(menubar):
    called = []
    menubar.on_history = lambda: called.append(1)
    item = next(i for i in menubar._menu._menu.itemArray() if i.title() == "History…")
    item.target().performSelector_withObject_(item.action(), item)
    assert called == [1]
