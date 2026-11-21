"""The app: the tray icon, the floating player, the play button, the toasts and the windows
(PySide6, lyssna/ui/), wired around one Controller. It runs on Windows, and on a Mac when
host.interface() picks it. The twin of LyssnaApp in lyssna/legacy/app.py, which is the
specification: every subscription, callback, timer and rule there has its counterpart here,
dictation and its history included.

What differs from the Mac's legacy wiring, and why:
  * One copy per user. host.instance (a named mutex on Windows, the control socket on a Mac)
    is asked before any window; a second launch asks the running copy to show its setup window
    and exits.
  * The Qt application is made first, before anything plays: the Windows player
    (lyssna/win/audio.py) reuses it.
  * The hooks report physical pixels. The Qt panels take physical points in
    contains_screen_point and in SelectionButton.present, so the points pass straight through.
  * The Mac wakes each app's accessibility as it comes to the front. On Windows that call does
    nothing, so there is no activation observer.
  * Screen changes re-place the player (the Mac only logs them; its panels repair themselves).
  * The dictation keys come from host.pushtotalk.attach, the same call on both systems (the Mac
    builds lyssna.mac.pushtotalk.PushToTalk by hand).
  * Shutdown also runs on Ctrl+C in a console, on Windows session end and when Qt quits, once,
    and cancels the deferred calls still waiting, so none fires after Qt is gone.

On a Mac host hands out the Mac taps, capture and player, which have the same public surface
(the taps start on the main thread, and their points are already Qt logical, which the
coordinate helpers pass through off Windows).

    python -m lyssna.app                  the real app (quit Lyssna.app first on a Mac)
    python -m lyssna.app --demo           fake reading and dictation, no hooks, no control channel
    python -m lyssna.app --exit-after 6   quit by itself after six seconds"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Callable

from . import __version__, config, host, whatsnew
from .controller import Controller, Notice
from .gestures import Point, SelectionGestureDetector
from .hotkeys import pretty
from .menu import key_name
from .player import Status
from .selection import SelectionWatcher

log = logging.getLogger("lyssna.app")

SHOW_REQUEST = {"cmd": "ui", "show": "setup"}  # what a second launch sends the running copy
HOTKEY_ACTIONS = ("speak", "pause", "stop", "next", "prev", "faster", "slower", "button")
DICTATION_PIECES = ("mic", "insert", "field_probe", "pushtotalk")  # the host modules dictation needs
SCREEN_SETTLE_MS = 300  # screen signals come in bursts; re-place once they stop
SIGNAL_POLL_MS = 250  # how often Python gets to run, so Ctrl+C is seen while Qt waits


# PySide6 and the Qt interface are imported where they are used, not at the top: host imports
# this module to find run(), and the import checks load it in interpreters where Qt is blocked
# (tests/test_windows_imports.py).


def on_main(fn: Callable, *args) -> None:
    from .ui import mainthread

    mainthread.on_main(fn, *args)


def later(delay: float, fn: Callable, *args):
    from .ui import mainthread

    return mainthread.later(delay, fn, *args)


def queued(fn: Callable, *args) -> None:
    """Run fn(*args) on the GUI thread later, never inline: the twin of the Mac's
    NSOperationQueue.mainQueue().addOperationWithBlock_."""
    from PySide6.QtCore import QTimer

    from .ui import mainthread

    if mainthread.is_main_thread():
        QTimer.singleShot(0, lambda: fn(*args))
    else:
        mainthread.on_main(fn, *args)


class LyssnaApp:
    """Build with the running QApplication in place. `events`, `capture` and `control_server`
    default to the platform's (host.events, host.capture, host.ipc.ControlServer); the tests pass
    fakes."""

    def __init__(self, instance=None, *, events=None, capture=None, control_server=None):
        from .ui.mini_player import MiniPlayer
        from .ui.selection_button import SelectionButton
        from .ui.toast import Tip, Toast
        from .ui.tray import Tray

        self.instance = instance
        self._events = events or host.events
        self._capture = capture or host.capture
        self._shut = False
        self.lost_race = False  # another copy owned the control channel already
        self.taps_ok = False
        self.server = None
        self.keyboard = None
        self.mouse = None
        log.info("Lyssna %s starting (Qt interface on %s)", __version__, host.DEVICE)

        self.ctl = Controller()
        self.tray = Tray(self.ctl)
        self.tray.on_quit = self.shutdown
        ui_cfg = self.ctl.cfg.get("ui", {})
        hk = self.ctl.cfg["hotkeys"]

        # Floating UI
        self.mini_player = MiniPlayer() if ui_cfg.get("mini_player", True) else None
        if self.mini_player:
            mp = self.mini_player
            mp.actions = {
                "toggle": self.ctl.toggle_pause, "stop": self.ctl.stop, "back": self.ctl.prev,
                "forward": self.ctl.next, "set_speed": self.ctl.set_speed, "seek_to": self.ctl.seek_to,
                "layout": self.ctl.save_player_layout, "set_voice": self.ctl.set_voice,
                "dictate": lambda: self.ctl.dictation_start(hands_free=True), "dictate_stop": self.ctl.dictation_stop,
                "dictate_cancel": self.ctl.dictation_cancel, "dictation_layout": self.ctl.save_dictation_layout,
                "read_selection": self._speak_from_hotkey, "history": self.show_history,
            }
            mp.voices_fn = lambda: (self.ctl.voices(), self.ctl.current_voice())
            mp.layout = self.ctl.player_layout()
            mp.dictation_layout = self.ctl.dictation_layout()
            mp.idle_pill = bool(ui_cfg.get("idle_pill", True))
            mp.auto_hide = bool(ui_cfg.get("auto_hide", True))
            self.tray.on_auto_hide = mp.set_auto_hide
            mp.rest_wave = bool(ui_cfg.get("wave", True))
            self.tray.on_wave = mp.set_wave
            mp.key_name = key_name(str(self.ctl.cfg.get("dictation", {}).get("hotkey", host.DICTATION_KEY_DEFAULT)))
            mp.read_key = pretty(hk["speak"])
            mp.tip = Tip()
            self.ctl.subscribe(mp.update)
            self.ctl.subscribe_dictation(mp.dictation_update)
            mp.status_fn = lambda: self.ctl.status  # so a queued update can check the live state
            mp.set_tooltips({
                "toggle": f"Pause / resume  {pretty(hk['pause'])}", "stop": f"Stop  {pretty(hk['stop'])}",
                "back": f"Previous sentence  {pretty(hk['prev'])}", "forward": f"Next sentence  {pretty(hk['next'])}",
                "speed": f"Speed  {pretty(hk['slower'])} / {pretty(hk['faster'])}",
            })
        self.toast = Toast()
        # Always queued, never inline, as on the Mac.
        self.ctl.subscribe_toast(lambda notice: queued(self._present_toast, notice))
        self.selection_button = SelectionButton()
        self.selection_button.timeout = float(ui_cfg.get("button_timeout", 4.0))
        self.selection_button.on_play = self._play_from_button
        self._button_source = "selection"  # what the play button on screen reads: "selection" or "clipboard"
        self._watch_screens()

        # The tray's windows
        self.setup_window = None
        self.tray.on_setup = self.show_setup
        self.tray.on_setup_dictation = lambda engine: self.show_setup(dictation=engine)
        self.history_window = None
        self.tray.on_history = self.show_history
        self.whats_new_window = None
        self.tray.on_whats_new = self.show_whats_new
        # Before _welcome_once writes its marker: the marker is how an update is told from a first install.
        self.tray.whats_new_pending = whatsnew.check(__version__, (config.STATE_DIR / "welcomed").exists())

        # The control channel, before the hooks: losing it means another copy runs, and then
        # this one must not grab the keys.
        self.ctl.extensions["ui"] = self._ui_state
        self.ctl.extensions["quit"] = self._quit_cmd
        self.server = (control_server or host.ipc.ControlServer)(self.ctl.dispatch)
        try:
            self.server.start()
        except OSError as e:
            log.warning("the control channel is taken (%s): another Lyssna is running", type(e).__name__)
            self.server = None
            self.lost_race = True
            return

        # Global input
        self.keyboard = self._events.KeyboardTap()
        for action, fn in (("speak", self._speak_from_hotkey), ("pause", self.ctl.toggle_pause), ("stop", self.ctl.stop),
                           ("next", self.ctl.next), ("prev", self.ctl.prev), ("faster", self.ctl.faster),
                           ("slower", self.ctl.slower), ("button", self._toggle_button_from_hotkey)):
            try:
                self.keyboard.bind(hk[action], fn)
            except ValueError as e:
                log.error("bad hotkey for %s: %s", action, e)
        # Any key press dismisses the play button, hotkeys included, as on the Mac.
        self.keyboard.on_any_key = self._dismiss_button
        self._attach_push_to_talk()
        cap = self._capture
        # The detector spots selection-like gestures; the watcher wants proof that text was
        # really selected (a new UI Automation selection, or a copy on select) before the button shows.
        self.selection_watcher = SelectionWatcher(
            probe_selection=cap.ax_selection,
            present=self._on_selection_gesture,
            enabled=lambda: self.ctl.selection_button_enabled,
            probe_clipboard=cap.clipboard_change_count,
            probe_copied=cap.copied_since,
            wake=lambda: cap.wake_accessibility(cap.front_app_pid()),
        )
        detector = SelectionGestureDetector(
            on_select=self.selection_watcher.gesture,
            on_dismiss=lambda p: self._dismiss_button(),
            ignore_region=self._is_over_our_ui,
            on_press=self.selection_watcher.press,
        )
        self.mouse = self._events.MouseTap(detector)
        self.taps_ok = self._start_taps()
        if not self.taps_ok:
            log.warning("the keyboard or mouse hook did not start")
            self.tray.show_permission_warning()
        self.ctl.warm_dictation()  # gets the microphone ready if access was already granted

        self.tray.show()
        later(1.0, self._welcome_once)
        if self.mini_player:
            later(0.5, self.mini_player.show_idle)  # the idle squiggle, when nothing is playing

    def _start_taps(self) -> bool:
        """Start both taps. On the Mac they attach to the main thread's run loop, which Qt runs,
        so this must run on the main thread there; the Windows hooks run on their own threads."""
        trusted = self._events.accessibility_trusted(prompt=True)
        return bool(trusted and self.keyboard.start() and self.mouse.start())

    # -- glue ----------------------------------------------------------------------------------

    def _welcome_once(self) -> None:
        """First launch only: open the setup window."""
        marker = config.STATE_DIR / "welcomed"
        if marker.exists():
            return
        marker.write_text("")
        log.info("first run: showing the setup window")
        self.show_setup()

    def _attach_push_to_talk(self) -> None:
        """Hold the dictation key ([dictation] hotkey) to dictate; the hands-free key while
        holding, or a double tap where it is on, keeps it recording. The controller keeps the
        keys in step with the session. A key this system does not have is said in a toast, and
        the app runs on without dictation keys."""
        try:
            ptt = host.pushtotalk.attach(self.keyboard, self.ctl.cfg, self.ctl.dictation_start, self.ctl.dictation_stop,
                                         self.ctl.dictation_lock, lambda quiet: self.ctl.dictation_cancel(quiet=quiet))
        except ValueError as e:  # a [dictation] hotkey this system does not have
            log.error("bad dictation key: %s", e)
            self.ctl.toast(Notice(str(e), seconds=8.0))
            return
        self.ctl.push_to_talk = ptt

    def _present_toast(self, notice: Notice) -> None:
        """On the screen the pill is on, else the one under the mouse; above the pill when it
        sits on the bottom edge."""
        screen, floor = None, None
        if self.mini_player and self.mini_player.visible():
            screen = self.mini_player.panel.screen()
            floor = self.mini_player.toast_floor()
        self.toast.present(notice, screen, floor)

    def show_history(self) -> None:
        """The dictation history and the dictionary, made on first use. It reads the shared
        history store and vocabulary (lyssna.dictation.history and .vocabulary), as the Mac's does."""
        from .ui.history_window import HistoryWindow

        if self.history_window is None:
            self.history_window = HistoryWindow()
            self.history_window.ctl = self.ctl
        self.history_window.show()

    def show_whats_new(self) -> None:
        from .ui.whats_new_window import WhatsNewWindow

        if self.whats_new_window is None:
            self.whats_new_window = WhatsNewWindow()
        self.whats_new_window.show()

    def show_setup(self, engine: str | None = None, dictation: str | None = None) -> None:
        from .ui.setup_window import SetupWindow

        if self.setup_window is None:
            self.setup_window = SetupWindow(self.ctl)
            self.setup_window.taps_ok = lambda: self.taps_ok
            self.setup_window.relaunch = self.relaunch
            self.setup_window.on_change = self.tray._refresh_checks
        self.setup_window.show(engine, dictation)

    def relaunch(self) -> None:
        """Shut down (letting go of the single-copy lock and the control channel), start a fresh
        copy, then quit. From source there is no program to start again, so it only quits."""
        self.shutdown()
        host.instance.relaunch()

    def _watch_screens(self) -> None:
        """A display plugged in, removed or rearranged, or the taskbar moved: re-place the
        player once the signals settle (the Mac's NSApplicationDidChangeScreenParametersNotification)."""
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        self._screen_timer = QTimer()
        self._screen_timer.setSingleShot(True)
        self._screen_timer.setInterval(SCREEN_SETTLE_MS)
        self._screen_timer.timeout.connect(self._screens_changed)
        if app is None:
            return
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(lambda screen: self._screen_timer.start())
        app.primaryScreenChanged.connect(lambda screen: self._screen_timer.start())
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(lambda rect: self._screen_timer.start())
        screen.availableGeometryChanged.connect(lambda rect: self._screen_timer.start())

    def _screen_added(self, screen) -> None:
        self._watch_screen(screen)
        self._screen_timer.start()

    def _screens_changed(self) -> None:
        from PySide6.QtGui import QGuiApplication

        names = []
        for screen in QGuiApplication.screens():
            g = screen.geometry()
            names.append(f"{screen.name()} ({g.x()}, {g.y()}, {g.width()}, {g.height()}) x{screen.devicePixelRatio():g}")
        log.info("screens changed: %s", names)
        mp = self.mini_player
        if mp is None or not mp.visible() or mp.dragging():
            return
        if mp.form in ("rest", "menu"):
            mp.show_idle(linger=False)
        else:
            mp.place()

    def _speak_from_hotkey(self) -> None:
        self._dismiss_button()
        self.ctl.speak_selection()

    def _toggle_button_from_hotkey(self) -> None:
        self._dismiss_button()
        self.ctl.toggle_selection_button()

    def _on_selection_gesture(self, p: Point, bounds: tuple[float, float, float, float] | None, source: str) -> None:
        """Called by the selection watcher from a worker thread once text is confirmed selected.
        `source` says where the evidence came from: "selection" (UI Automation, or
        Accessibility on a Mac) or "clipboard" (the app copied the selection itself)."""
        on_main(self._present_button, p, bounds, source)

    def _present_button(self, p: Point, bounds: tuple[float, float, float, float] | None, source: str) -> None:
        if not self.ctl.selection_button_enabled:
            return
        self._button_source = source
        avoid = self.mini_player.frame() if self.mini_player and self.mini_player.visible() else None
        self.selection_button.present(p.x, p.y, avoid, bounds)

    def _play_from_button(self) -> None:
        """The play button reads what it was offered for: a selection the app copied itself is
        on the clipboard already, so no Ctrl+C goes to that app."""
        if self._button_source == "clipboard":
            self.ctl.speak_clipboard()
        else:
            self.ctl.speak_selection()

    def _dismiss_button(self) -> None:
        if self.selection_button:
            self.selection_button.dismiss()

    def _is_over_our_ui(self, p: Point) -> bool:
        """`p` comes from the mouse hook, in physical pixels, which contains_screen_point takes."""
        for panel in (self.selection_button, self.mini_player, self.toast):
            if panel is not None and panel.contains_screen_point(p.x, p.y):
                return True
        return False

    def _quit_cmd(self, req: dict) -> dict:
        later(0.2, self.tray.quit)
        return {"ok": True, "status": self.ctl.status_dict()}

    def _ui_state(self, req: dict) -> dict:
        """Debug/introspection for the CLI and tests: where the floating panels are (Mac-style
        rects, as on the Mac), whether the dictation keys are attached, whether dictation's
        system pieces load and whether the local cleanup model can run (the built app's smoke
        test reads this). With "show": "setup" it
        also opens the setup window, which is how a second launch brings this copy forward;
        "shown" in the reply says it did (the legacy Mac app does not)."""
        reply: dict = {"ok": True}
        if req.get("show") == "setup":
            on_main(self.show_setup)
            reply["shown"] = "setup"

        def frame(panel):
            if panel is None or not panel.visible():
                return None
            x, y, w, h = panel.frame_mac()
            return {"x": x, "y": y, "w": w, "h": h}
        return {**reply, "selection_button": frame(self.selection_button), "mini_player": frame(self.mini_player),
                "dock": self.mini_player.dock if self.mini_player else "",
                "dictation": {"keys": self.ctl.push_to_talk is not None, "pieces": _dictation_pieces(),
                              "cleanup_model": _cleanup_model()}}

    # -- running and stopping ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop the hooks, close the control channel, stop the engine, take the tray icon away
        and end the Qt loop. Runs once, whichever way the app is asked to quit."""
        from .ui import mainthread

        if self._shut:
            return
        self._shut = True
        log.info("shutting down")
        mainthread.cancel_pending()  # a deferred call firing after Qt is gone would find nothing to run on
        for tap in (self.keyboard, self.mouse):
            stop = getattr(tap, "stop", None)  # the Mac taps have none; they end with the process
            if stop is not None:
                try:
                    stop()
                except Exception:
                    log.exception("could not stop %s", type(tap).__name__)
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                log.exception("could not close the control channel")
        try:
            self.ctl.shutdown()
        except Exception:
            log.exception("the controller did not shut down cleanly")
        self.tray.timer.stop()
        self.tray.tray.hide()  # no ghost icon by the clock
        if self.instance is not None:
            self.instance.release()
        _end_loop()

    def _install_quit_hooks(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        app.aboutToQuit.connect(self.shutdown)
        app.commitDataRequest.connect(lambda manager: self.shutdown())  # Windows is logging off or shutting down
        _quit_on_ctrl_c(self.shutdown)
        sys.excepthook = _log_uncaught

    def run(self, exit_after: float | None = None) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        if self.lost_race:
            _ask_running_copy_to_show()
            self.shutdown()
            return
        self._install_quit_hooks()
        if exit_after:
            QTimer.singleShot(round(exit_after * 1000), self.shutdown)
        QApplication.instance().exec()
        self.shutdown()


# -- process setup -----------------------------------------------------------------------------


def _end_loop() -> None:
    """End the Qt loop. exit(), not quit(): Qt 6 lets a window refuse quit(), and the setup
    window does (closing it only hides it)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.exit(0)


def _qapplication():
    """The one QApplication, made before any window and before anything plays, with no Dock
    icon where the system has a Dock (host.window: prepare_app before it exists, hide_dock_icon
    after). Qt 6 is per-monitor DPI aware by default, which the coordinate helpers rely on, so
    the DPI mode is left alone.

    The display name is set empty on purpose. Qt on Windows writes every window title as
    "<title> - <display name>" unless the title already ends with the display name
    (QPlatformWindow::formatWindowTitle, which QWidget and QWindow titles both go through), so a
    display name of "Lyssna" would make "Lyssna Setup - Lyssna". Empty, the titles are exactly
    the Mac's. The application name stays "Lyssna"."""
    from PySide6.QtWidgets import QApplication

    from .ui.window import app_icon

    host.window.prepare_app()
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # closing setup must not end a tray app
    app.setApplicationName("Lyssna")
    app.setApplicationDisplayName("")  # no " - Lyssna" after the window titles on Windows (see above)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    host.window.hide_dock_icon()
    return app


def _dictation_pieces() -> dict[str, str]:
    """Each host module dictation needs, by name: the module that loaded, or why it did not.
    Loading them is what proves a built app bundled them."""
    pieces = {}
    for name in DICTATION_PIECES:
        try:
            module = host.require(name)
            pieces[name] = module.__name__
        except Exception as e:
            pieces[name] = f"missing: {type(e).__name__}: {e}"
    return pieces


def _cleanup_model() -> dict:
    """Whether the local cleanup model can run in this app: how this computer runs it
    (host.LOCAL_CLEANUP), can_run(), and whether llama_cpp imports. Importing it loads its
    native libraries, so in a built app a missing DLL shows here as an import error. No model is
    loaded."""
    from .dictation.cleaners.s1mini import S1MiniCleaner

    out: dict = {"route": host.LOCAL_CLEANUP, "can_run": S1MiniCleaner.can_run()}
    if host.LOCAL_CLEANUP is None:
        out["llama_cpp"] = "not used on this computer"
        return out
    try:
        import llama_cpp

        out["llama_cpp"] = f"imported {getattr(llama_cpp, '__version__', '?')}"
    except Exception as e:
        out["llama_cpp"] = f"missing: {type(e).__name__}: {e}"
    return out


def _ask_running_copy_to_show() -> None:
    reply = host.ipc.send(dict(SHOW_REQUEST))
    log.info("Lyssna is already running; asked it to show itself (%s)", "answered" if reply else "no answer")
    if not (reply or {}).get("shown"):  # the legacy Mac app answers without coming forward
        print("Another Lyssna is running. Quit it first to run this one.", file=sys.stderr)


def _quit_on_ctrl_c(quit_fn: Callable[[], None]) -> None:
    """Ctrl+C in a console run quits cleanly. Python only sees the signal when it runs, so a
    short timer wakes it while Qt waits for events."""
    if threading.current_thread() is not threading.main_thread():
        return
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    signal.signal(signal.SIGINT, lambda signum, frame: quit_fn())
    timer = QTimer(QApplication.instance())
    timer.timeout.connect(lambda: None)
    timer.start(SIGNAL_POLL_MS)


def _log_uncaught(kind, value, tb) -> None:
    """An exception that reaches the top of a Qt callback is logged, and the app keeps running."""
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, value, tb)
        return
    log.error("uncaught %s", kind.__name__, exc_info=(kind, value, tb))


def run(*, demo: bool = False, exit_after: float | None = None) -> None:
    """Start the app (what `lyssna` with no arguments runs when host.app is this module). Returns when it quits,
    or at once when another copy is already running."""
    if demo:
        _run_demo(exit_after)
        return
    host.instance.set_app_id()
    instance = host.instance.SingleInstance()
    if not instance.acquire():
        _ask_running_copy_to_show()
        return
    _qapplication()
    LyssnaApp(instance).run(exit_after)


# -- the demo: a visual smoke check with fake state ----------------------------------------------


class Demo:
    """The tray, the player, a toast, a dictation and the setup and history windows on fake
    state: no hooks, no control channel, no engine, no microphone, nothing read from or written
    to the user's Lyssna files. The player cycles through loading, playing, paused, docked and
    idle, then a dictation (recording with a moving level, hands-free, working), then the setup
    window's Dictate tab. The tray's and the player's dictation and history items work on the
    same fake state."""

    STEP_MS = 1000
    LEVEL_MS = 50  # how often the made-up microphone level moves while "recording"
    WORKING_MS = 1500  # a dictation finished by hand "works" this long, then ends

    def __init__(self) -> None:
        from PySide6.QtCore import QTimer

        from .ui.mini_player import MiniPlayer
        from .ui.setup_window import _FakeController
        from .ui.toast import Tip, Toast
        from .ui.tray import Tray, _PreviewController

        demo = self

        class DemoController(_PreviewController):
            """The tray's preview controller, with what the setup window reads borrowed from its
            own fake, and dictation that only moves the demo's player."""

            mic = "granted"
            models = True  # the speech models count as downloaded
            add_api_key = _FakeController.add_api_key
            speak_text = _FakeController.speak_text
            microphone_status = _FakeController.microphone_status
            request_microphone = _FakeController.request_microphone
            local_dictation_models = _FakeController.local_dictation_models
            use_local_dictation = _FakeController.use_local_dictation
            add_dictation_key = _FakeController.add_dictation_key

            def dictation_key_state(self, name: str) -> bool:
                return False

            def dictation_start(self, hands_free: bool = False) -> bool:
                demo._dictate("locked" if hands_free else "recording")
                return True

            def dictation_lock(self) -> None:
                demo._dictate("locked")

            def dictation_stop(self) -> None:
                demo._dictate("working")
                QTimer.singleShot(demo.WORKING_MS, lambda: demo._dictate("idle") if self.dictation.state == "working" else None)

            def dictation_cancel(self, quiet: bool = False) -> None:
                demo._dictate("idle")

        self.ctl = DemoController(provider="kokoro")
        self.tray = Tray(self.ctl)
        self.tray.on_quit = _end_loop
        self.tray.on_setup = lambda engine: self.show_setup(engine)
        self.tray.on_setup_dictation = lambda engine: self.show_setup(dictation=engine)
        self.tray.on_history = self.show_history
        self.tray.show()
        mp = self.mini_player = MiniPlayer()
        mp.actions = {name: (lambda *args: None) for name in
                      ("toggle", "stop", "back", "forward", "set_speed", "seek_to", "layout", "set_voice", "read_selection",
                       "dictation_layout")}
        mp.actions.update({"dictate": lambda: self.ctl.dictation_start(hands_free=True), "dictate_stop": self.ctl.dictation_stop,
                           "dictate_cancel": self.ctl.dictation_cancel, "history": self.show_history})
        mp.voices_fn = lambda: ([("af_heart", "Heart"), ("af_bella", "Bella")], "af_heart")
        mp.auto_hide = False
        mp.read_key = pretty("ctrl+alt+s")
        mp.key_name = key_name(host.DICTATION_KEY_DEFAULT)
        mp.tip = Tip()
        self.toast = Toast()
        self.setup_window = None
        self.history_window = None
        self.step = 0
        self.timer = QTimer()
        self.timer.setInterval(self.STEP_MS)
        self.timer.timeout.connect(self._next)
        self._level_t = 0
        self.level_timer = QTimer()
        self.level_timer.setInterval(self.LEVEL_MS)
        self.level_timer.timeout.connect(self._level_tick)

    def show_setup(self, engine: str | None = None, dictation: str | None = None) -> None:
        from .ui.setup_window import SetupWindow

        if self.setup_window is None:
            self.setup_window = SetupWindow(self.ctl)
        self.setup_window.show(engine, dictation)

    def show_history(self) -> None:
        """The history window on the preview's store and dictionary, made in a fresh temporary
        folder: never the user's dictations."""
        from .ui.history_window import _preview_window

        if self.history_window is None:
            self.history_window = _preview_window()
        self.history_window.show()

    def _status(self, state: str, session: int, index: int = 3, position: float = 84.0) -> Status:
        return Status(state=state, index=index if state != "loading" else 0, total=9 if state != "loading" else 0,
                      speed=1.2, provider="Kokoro · Heart", position=position, duration=334.0, session=session)

    def _show(self, st: Status) -> None:
        self.ctl.status = st
        self.mini_player.update(st)

    def _dictate(self, state: str) -> None:
        """Show a dictation `state` ("recording", "locked", "working" or "idle") as the session would report it."""
        from .dictation.types import DictationStatus

        self.ctl.dictation.state = state
        if state in ("recording", "locked"):
            self.level_timer.start()
        else:
            self.level_timer.stop()
        self.mini_player.dictation_update(DictationStatus(state=state, level=self._level(), session=1))

    def _level(self) -> float:
        """A made-up microphone level that rises and falls like speech."""
        import math

        t = self._level_t * self.LEVEL_MS / 1000
        return max(0.0, min(1.0, 0.45 + 0.3 * math.sin(t * 5.1) + 0.15 * math.sin(t * 13.7)))

    def _level_tick(self) -> None:
        from .dictation.types import DictationStatus

        self._level_t += 1
        self.mini_player.dictation_update(DictationStatus(state=self.ctl.dictation.state, level=self._level(), session=1))

    def _next(self) -> None:
        steps = (
            ("loading", lambda: self._show(self._status("loading", 1))),
            ("playing", lambda: self._show(self._status("playing", 1))),
            ("paused", lambda: self._show(self._status("paused", 1, position=120.0))),
            ("docked", self._docked),
            ("toast and setup", self._toast_and_setup),
            ("idle", self._idle),
            ("dictation: recording", lambda: self._dictate("recording")),
            ("dictation: hands-free", lambda: self._dictate("locked")),
            ("dictation: working", lambda: self._dictate("working")),
            ("setup: Dictate tab", self._setup_dictate),
        )
        name, fn = steps[self.step % len(steps)]
        self.step += 1
        print(f"demo: {name}", flush=True)
        fn()

    def _docked(self) -> None:
        self.mini_player.layout = {"dock": "right", "x": 0.0, "y": 0.5}
        self._show(self._status("playing", 2, index=5, position=200.0))

    def _toast_and_setup(self) -> None:
        floor = self.mini_player.toast_floor() if self.mini_player.visible() else None
        self.toast.present(Notice("This is the demo. Nothing is being read.", "Undo", lambda: None, seconds=3.0), None, floor)
        self.show_setup()

    def _idle(self) -> None:
        self.mini_player.layout = {"dock": "", "x": -1.0, "y": -1.0}
        self._show(Status())

    def _setup_dictate(self) -> None:
        from .ui.setup_window import DICTATE

        self._dictate("idle")
        self.show_setup()
        self.setup_window._select_tab(DICTATE)

    def start(self) -> None:
        self._next()
        self.timer.start()

    def close(self) -> None:
        from .ui import mainthread

        self.timer.stop()
        self.level_timer.stop()
        mainthread.cancel_pending()
        self.tray.timer.stop()
        self.tray.tray.hide()


def _run_demo(exit_after: float | None) -> None:
    from PySide6.QtCore import QTimer

    app = _qapplication()
    demo = Demo()
    demo.start()
    if exit_after:
        QTimer.singleShot(round(exit_after * 1000), _end_loop)
    _quit_on_ctrl_c(_end_loop)
    sys.excepthook = _log_uncaught
    app.exec()
    demo.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lyssna.app", description="Run the Qt interface of Lyssna.")
    parser.add_argument("--demo", action="store_true",
                        help="fake reading and dictation; no hooks, no control channel, no engine, no microphone")
    parser.add_argument("--exit-after", type=float, metavar="SECONDS", help="quit by itself after this many seconds")
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args(argv)
    if ns.demo:
        logging.basicConfig(level=logging.DEBUG if ns.verbose else logging.WARNING,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        print("Lyssna Qt interface: demo mode (fake state, no hooks, no control channel, no microphone)", flush=True)
    else:
        from .cli import _setup_logging

        _setup_logging(ns.verbose)
        print(f"Lyssna Qt interface: real mode on this {host.DEVICE} (hooks, control channel, engine)", flush=True)
    run(demo=ns.demo, exit_after=ns.exit_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
