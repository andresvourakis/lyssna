"""Runs the guided steps one after another, without ever blocking the Qt main thread.

Why generators: each step reads top to bottom like the script a person follows ("show this,
wait for the key, ask that"), but the waits must not block the window. A step is a generator
that yields a request (ask a question, wait for an event, run something slow on a worker
thread, wait a few seconds) and the Flow resumes it with the answer when it arrives, on the
main thread. An exception inside a step is recorded in the report and the next step starts.

Events from other threads (the keyboard hook, the mouse hook, workers) reach the Flow through
Bridge.post, a queued Qt signal, so step code only ever runs on the main thread.

Auto mode (--screenshots) answers every request at once, saving a picture of each screen."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from . import report as rpt

log = logging.getLogger("probe.flow")

YES_NO_SKIP = ("Yes", "No", "Skip")


class Skipped(Exception):
    """Thrown into a step when the person skips it."""


@dataclass
class Ask:
    question: str
    choices: tuple[str, ...] = YES_NO_SKIP
    key: str | None = None  # the name the answer is filed under; the question by default
    extra: tuple[tuple[str, Callable[[], None]], ...] = ()  # buttons that act without moving on
    record: bool = True


@dataclass
class Press:
    label: str
    skip: bool = True
    skip_label: str = "Skip this step"


@dataclass
class WaitFor:
    event: str
    timeout: float
    waiting: str = ""
    skip_label: str = "Skip"


@dataclass
class Work:
    fn: Callable
    busy: str = ""
    with_status: bool = False  # fn(say) gets a function that shows a line of progress


@dataclass
class Later:
    start: Callable[[Callable[[object], None]], None]  # start(done) on the main thread; done(result) there too


@dataclass
class Sleep:
    seconds: float


@dataclass
class Countdown:
    seconds: int
    template: str  # with {n}


@dataclass
class Step:
    key: str
    title: str
    fn: Callable
    number: int | None = None  # "Step 3 of 9"; None for the closing screen


@dataclass
class Context:
    flow: Flow
    ui: object
    backend: object
    report: rpt.Report
    rec: rpt.StepRecord | None = None
    state: dict = field(default_factory=dict)


class Bridge(QObject):
    """Runs a function on the main thread, from any thread."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            log.exception("main thread call failed")

    def post(self, fn: Callable[[], None]) -> None:
        self.call.emit(fn)


class Flow(QObject):
    def __init__(self, window, steps: list[Step], report: rpt.Report, backend, log_lines: Callable[[], list[str]],
                 auto: bool = False, shots: Path | None = None):
        super().__init__()
        self.window = window
        self.steps = steps
        self.report = report
        self.backend = backend
        self.log_lines = log_lines
        self.auto = auto
        self.shots = shots
        self.bridge = Bridge()
        self.ctx = Context(self, window, backend, report)
        self.total = sum(1 for s in steps if s.number is not None)
        self.index = -1
        self.gen = None
        self.token = 0
        self.waiting: tuple[str, int] | None = None
        self.finished = False
        self.report_path: Path | None = None
        self.report_text = ""
        self._shot = 0
        self.hotkey_work: Callable[[], dict] | None = None
        self.hotkey_count = 0
        self.hotkey_log: list[dict] = []
        self.fakes: dict[str, Callable[[], object]] = {}  # preview: what "pretend it happened" delivers
        for s in steps:
            report.step(s.key, s.title)

    # ----- running steps ------------------------------------------------------------------

    def start(self) -> None:
        self._next_step()

    def _step(self) -> Step:
        return self.steps[self.index]

    def _next_step(self) -> None:
        if self.finished:
            return
        self.index += 1
        if self.index >= len(self.steps):
            self.finish()
            return
        step = self._step()
        self.ctx.rec = self.report.step(step.key, step.title)
        self.ctx.rec.status(rpt.STATUS_RUNNING)
        self.window.begin_step(step.number, self.total, step.title)
        try:
            self.gen = step.fn(self.ctx)
        except Exception as e:
            self._failed(e)
            return
        self._resume()

    def _resume(self, value=None, exc: BaseException | None = None) -> None:
        if self.finished or self.gen is None:
            return
        self.token += 1
        self.waiting = None
        self.window.clear_controls()
        try:
            req = self.gen.throw(exc) if exc is not None else self.gen.send(value)
        except StopIteration:
            self.ctx.rec.status(rpt.STATUS_DONE)
            self._after_step()
        except Skipped:
            self.ctx.rec.status(rpt.STATUS_SKIPPED)
            self._after_step()
        except Exception as e:
            self._failed(e)
        else:
            self._handle(req)

    def _failed(self, e: BaseException) -> None:
        log.error("step %s failed: %s: %s", self._step().key, type(e).__name__, e)
        self.ctx.rec.error(self._step().key, e)
        self.ctx.rec.status(rpt.STATUS_ERROR)
        self._after_step()

    def _after_step(self) -> None:
        self.gen = None
        self.window.hide_extra()
        self.write_report()
        QTimer.singleShot(0, self._next_step)

    def _guard(self, fn: Callable) -> Callable:
        """fn, but only while the request that made it is still the current one."""
        token = self.token

        def run(*args):
            if token == self.token and not self.finished:
                fn(*args)

        return run

    def _handle(self, req) -> None:
        w = self.window
        if isinstance(req, Ask):
            def choose(choice: str):
                if req.record:
                    self.ctx.rec.answer(req.key or req.question, choice)
                self._resume(choice)

            w.show_question(req.question, [(c, self._guard(lambda c=c: choose(c))) for c in req.choices], list(req.extra))
            self._autoshot(lambda: choose(req.choices[0]))
        elif isinstance(req, Press):
            skip = self._guard(lambda: self._resume(exc=Skipped())) if req.skip else None
            w.show_press(req.label, self._guard(lambda: self._resume(req.label)), req.skip_label if req.skip else None, skip)
            self._autoshot(lambda: self._resume(req.label))
        elif isinstance(req, WaitFor):
            self.waiting = (req.event, self.token)
            pretend = None
            if self.backend.preview:
                pretend = self._guard(lambda: self.trigger(req.event, self.fakes.get(req.event, dict)()))
            w.show_wait(req.waiting, req.skip_label, self._guard(lambda: self._resume(None)), pretend)
            QTimer.singleShot(int(req.timeout * 1000), self._guard(lambda: self._resume(None)))
            self._autoshot(lambda: self.trigger(req.event, self.fakes.get(req.event, dict)()))
        elif isinstance(req, Work):
            w.show_busy(req.busy)
            self._run_work(req)
            self._autoshot(None)
        elif isinstance(req, Later):
            done = self._guard(self._deliver)
            req.start(lambda result: self.bridge.post(lambda: done(result)))
            self._autoshot(None)
        elif isinstance(req, Sleep):
            QTimer.singleShot(0 if self.auto else int(req.seconds * 1000), self._guard(lambda: self._resume(None)))
        elif isinstance(req, Countdown):
            self._countdown(req, req.seconds)
        else:
            self._resume(exc=TypeError(f"unknown request {req!r}"))

    def _deliver(self, result) -> None:
        if isinstance(result, BaseException):
            self._resume(exc=result)
        else:
            self._resume(result)

    def _run_work(self, req: Work) -> None:
        done = self._guard(self._deliver)
        say = self._guard(self.window.set_status)

        def run():
            try:
                if req.with_status:
                    result = req.fn(lambda text: self.bridge.post(lambda: say(text)))
                else:
                    result = req.fn()
            except BaseException as e:
                result = e
            self.bridge.post(lambda: done(result))

        threading.Thread(target=run, daemon=True, name="probe-work").start()

    def _countdown(self, req: Countdown, n: int) -> None:
        if n <= 0:
            self.window.set_big("")
            self._resume(None)
            return
        self.window.set_big(req.template.format(n=n))
        if n == req.seconds:
            self._autoshot(None)
        QTimer.singleShot(150 if self.auto else 1000, self._guard(lambda: self._countdown(req, n - 1)))

    # ----- events from other threads ------------------------------------------------------

    def trigger(self, event: str, payload) -> None:
        """Main thread: an event arrived. Resumes the step if it is waiting for this event."""
        if self.waiting is not None and self.waiting[0] == event and self.waiting[1] == self.token:
            self._resume(payload)

    def post_trigger(self, event: str, payload) -> None:
        """Any thread."""
        self.bridge.post(lambda: self.trigger(event, payload))

    def on_hotkey(self) -> None:
        """The reading hotkey, on the hook's worker thread. Does the step's work right here
        (reading the selection must happen now, while the other app is in front)."""
        from .backend import VK_RMENU

        self.hotkey_count += 1
        payload: dict = {"at": time.strftime("%H:%M:%S")}
        try:
            payload["right_alt_down"] = self.backend.key_down(VK_RMENU)
        except Exception as e:
            payload["right_alt_error"] = f"{type(e).__name__}: {e}"
        self.hotkey_log.append(dict(payload))
        work = self.hotkey_work
        if work is not None:
            try:
                payload.update(work())
            except Exception as e:
                payload["error"] = rpt.error_entry("hotkey work", e)
        self.post_trigger("hotkey", payload)

    # ----- the report ---------------------------------------------------------------------

    def write_report(self) -> tuple[Path | None, str]:
        self.report.meta("finished", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.report.set_log(self.log_lines())
        self.report_text = rpt.render_text(self.report.snapshot())
        try:
            path = Path(self.backend.report_dir()) / rpt.REPORT_NAME
            path.write_text(self.report_text, encoding="utf-8")
            self.report_path = path
        except Exception:
            log.exception("could not write the report")
        return self.report_path, self.report_text

    def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.write_report()
        if self.auto:
            QTimer.singleShot(0, QApplication.instance().quit)

    def abort(self) -> None:
        """The window is closing: keep what was found, remove the hooks, write the report."""
        if self.finished:
            return
        if self.gen is not None and 0 <= self.index < len(self.steps):
            self.ctx.rec.status(rpt.STATUS_RUNNING)
        self.finished = True
        closing = self.report.step("closing", "Window closed early")
        try:
            closing.update(self.backend.remove_hooks())
            closing.status(rpt.STATUS_DONE)
        except Exception as e:
            closing.error("remove hooks", e)
            closing.status(rpt.STATUS_ERROR)
        self.write_report()

    # ----- screenshots --------------------------------------------------------------------

    def _autoshot(self, then: Callable[[], None] | None) -> None:
        if not self.auto:
            return
        self._shot += 1
        name = f"{self._shot:02d}-{self._step().key}.png"
        token = self.token

        def shoot():
            if token != self.token:
                return
            QApplication.processEvents()
            if self.shots is not None:
                self.window.grab().save(str(self.shots / name))
            if then is not None:
                then()

        QTimer.singleShot(60, shoot)
