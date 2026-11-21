"""The Mac's legacy interface (lyssna.legacy.ui) hands out the very same layout objects as
lyssna.ui.layout under its old names, so the two interfaces follow one set of rules."""


def test_legacy_ui_modules_hand_out_the_layout_objects():
    from lyssna.legacy.ui import (
        history_window,
        mark,
        mini_player,
        panel,
        selection_button,
        setup_window,
        theme,
        toast,
        wave,
        whats_new_window,
        widgets,
    )
    from lyssna.ui.layout import button, tokens, whats_new
    from lyssna.ui.layout import history as s_history
    from lyssna.ui.layout import mark as s_mark
    from lyssna.ui.layout import panel as s_panel
    from lyssna.ui.layout import pill as s_pill
    from lyssna.ui.layout import setup as s_setup
    from lyssna.ui.layout import toast as s_toast
    from lyssna.ui.layout import wave as s_wave

    pairs = [
        (mini_player.pick_form, s_pill.pick_form),
        (mini_player.edge_frame, s_pill.edge_frame),
        (mini_player.tuck_due, s_pill.tuck_due),
        (mini_player.DOCKS, s_pill.DOCKS),
        (mini_player.DICTATING, s_pill.DICTATING),
        (wave.wave_points, s_wave.wave_points),
        (wave.ease, s_wave.ease),
        (widgets.squiggle_points, s_wave.squiggle_points),
        (widgets.capsule_centre, s_wave.capsule_centre),
        (widgets.wave_phases, s_wave.wave_phases),
        (widgets.SQUIGGLE_FILL, tokens.SQUIGGLE_FILL),
        (mark.MARK_W, s_mark.MARK_W),
        (toast.toast_width, s_toast.toast_width),
        (toast.tip_origin, s_toast.tip_origin),
        (setup_window.steps_left, s_setup.steps_left),
        (setup_window.opening_tab, s_setup.opening_tab),
        (setup_window.SAMPLE, s_setup.SAMPLE),
        (setup_window.DICTATION_KEY_ENGINES, s_setup.DICTATION_KEY_ENGINES),
        (history_window.day_label, s_history.day_label),
        (history_window.column, s_history.column),
        (history_window.SOURCE_BADGE, s_history.SOURCE_BADGE),
        (history_window.TABS, s_history.TABS),
        (selection_button._intersects, button._intersects),
        (theme.CREAM, tokens.CREAM),
        (theme.INK_TEXT, tokens.INK_TEXT),
        (panel.BG, tokens.PANEL_BG),
        (whats_new_window.CHANGES_TITLE, whats_new.CHANGES_TITLE),
    ]
    for mac, shared in pairs:
        assert mac is shared
    assert panel.DRAG_SLOP == s_panel.DRAG_SLOP
