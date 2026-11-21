"""The probe's window, the floating round button, and the microphone recording, in Qt.

Why it looks like this: the person running the probe is not technical, so each screen is one
short instruction in large plain text, a single dark button for the main action, and Yes, No
and Skip for questions. Cream background and near-black text, like Lyssna.

The floating button is the same kind of window the Windows app will use for its play button:
frameless, always on top, a tool window that never takes focus. The probe checks that showing
it leaves the other app's selection highlighted."""

from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .analysis import levels

CREAM = "#F4EFE6"
INK = "#141414"
MUTED = "#5E5A52"

STYLE = f"""
QWidget#probe {{ background: {CREAM}; }}
QLabel {{ color: {INK}; background: transparent; }}
QLabel#progress {{ color: {MUTED}; font-size: 15px; }}
QLabel#title {{ font-size: 28px; font-weight: 600; }}
QLabel#body {{ font-size: 19px; }}
QLabel#question {{ font-size: 20px; font-weight: 600; }}
QLabel#big {{ font-size: 40px; font-weight: 600; }}
QLabel#status {{ color: {MUTED}; font-size: 17px; }}
QLabel#banner {{ background: #E6D9BF; color: {INK}; font-size: 14px; padding: 8px 12px; border-radius: 6px; }}
QCheckBox {{ color: {INK}; font-size: 18px; spacing: 10px; padding: 4px 0; }}
QLineEdit {{ font-size: 20px; padding: 8px; border: 1px solid #BDB5A6; border-radius: 6px; background: white; color: {INK}; }}
QPushButton {{ font-size: 17px; border-radius: 10px; padding: 11px 26px; }}
QPushButton#primary {{ background: {INK}; color: {CREAM}; border: none; }}
QPushButton#primary:hover {{ background: #2C2C2C; }}
QPushButton#secondary {{ background: transparent; color: {INK}; border: 1.5px solid {INK}; min-width: 70px; }}
QPushButton#secondary:hover {{ background: #EAE3D6; }}
QPushButton#link {{ background: transparent; color: {MUTED}; border: none; text-decoration: underline; padding: 11px 10px; }}
"""

PREVIEW_BANNER = "Preview on a Mac. Nothing here touches Windows, and every result is made up."


class ProbeWindow(QWidget):
    def __init__(self, preview: bool = False):
        super().__init__()
        self.setObjectName("probe")
        self.setWindowTitle("Lyssna check")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.resize(760, 600)
        self.on_close = None

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 36, 48, 32)
        root.setSpacing(14)
        if preview:
            banner = QLabel(PREVIEW_BANNER, objectName="banner")
            banner.setWordWrap(True)
            root.addWidget(banner)
        self.progress = QLabel("", objectName="progress")
        self.title = QLabel("", objectName="title")
        self.title.setWordWrap(True)
        self.body = QLabel("", objectName="body")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(6)
        self.big = QLabel("", objectName="big")
        self.question = QLabel("", objectName="question")
        self.question.setWordWrap(True)
        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        for widget in (self.progress, self.title):
            root.addWidget(widget)
        root.addSpacing(6)
        root.addWidget(self.body)
        root.addLayout(self.extra)
        root.addWidget(self.big)
        root.addStretch(1)
        root.addWidget(self.status)
        root.addWidget(self.question)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(12)
        root.addLayout(self.buttons)
        self._extra_widgets: list[QWidget] = []
        for label in (self.big, self.question, self.status):
            label.hide()

    # ----- text ---------------------------------------------------------------------------

    def begin_step(self, number: int | None, total: int, title: str) -> None:
        self.progress.setText(f"Step {number} of {total}" if number is not None else "Finished")
        self.title.setText(title)
        self.set_body("")
        self.set_status("")
        self.hide_extra()

    def set_body(self, text: str) -> None:
        self.body.setText(text)
        self.body.setVisible(bool(text))

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def set_big(self, text: str) -> None:
        self.big.setText(text)
        self.big.setVisible(bool(text))

    def show_extra(self, widgets: list[QWidget]) -> None:
        self.hide_extra()
        for widget in widgets:
            self.extra.addWidget(widget)
            self._extra_widgets.append(widget)

    def hide_extra(self) -> None:
        for widget in self._extra_widgets:
            self.extra.removeWidget(widget)
            widget.deleteLater()
        self._extra_widgets = []

    # ----- buttons ------------------------------------------------------------------------

    def clear_controls(self) -> None:
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.question.hide()
        self.set_big("")
        self.set_status("")

    def _button(self, label: str, callback, kind: str) -> QPushButton:
        b = QPushButton(label, objectName=kind)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _checked=False: callback())
        return b

    def show_question(self, question: str, choices: list, extra: list) -> None:
        self.question.setText(question)
        self.question.setVisible(bool(question))
        for label, fn in extra:
            self.buttons.addWidget(self._button(label, fn, "link"))
        self.buttons.addStretch(1)
        for label, fn in choices:
            kind = "link" if label == "Skip" else ("primary" if len(choices) == 1 else "secondary")
            self.buttons.addWidget(self._button(label, fn, kind))

    def show_press(self, label: str, callback, skip_label: str | None, skip) -> None:
        if skip_label and skip is not None:
            self.buttons.addWidget(self._button(skip_label, skip, "link"))
        self.buttons.addStretch(1)
        self.buttons.addWidget(self._button(label, callback, "primary"))

    def show_wait(self, waiting: str, skip_label: str, skip, pretend) -> None:
        self.set_status(waiting)
        if pretend is not None:
            self.buttons.addWidget(self._button("Preview: pretend it happened", pretend, "secondary"))
        self.buttons.addStretch(1)
        self.buttons.addWidget(self._button(skip_label, skip, "link"))

    def show_busy(self, text: str) -> None:
        self.set_status(text)

    def closeEvent(self, event) -> None:
        if self.on_close is not None:
            self.on_close()
        super().closeEvent(event)


def checkbox(label: str, checked: bool) -> QCheckBox:
    box = QCheckBox(label)
    box.setChecked(checked)
    return box


def line_edit(placeholder: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setMaximumWidth(420)
    return edit


# ----- the floating button -----------------------------------------------------------------


class FloatingButton(QWidget):
    """A 34-point dark circle with a white play triangle that never takes focus."""

    SIZE = 34

    def __init__(self, backend=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.style_info: dict = {}
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        if sys.platform == "win32" and backend is not None:
            hwnd = int(self.winId())  # creates the native window, so its style can be changed before it shows
            try:
                self.style_info = backend.add_no_activate(hwnd)
            except Exception as e:
                self.style_info = {"error": f"{type(e).__name__}: {e}"}

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawEllipse(0, 0, self.SIZE, self.SIZE)
        p.setBrush(QColor("white"))
        s = self.SIZE
        p.drawPolygon(QPolygonF([QPointF(s * 0.40, s * 0.30), QPointF(s * 0.40, s * 0.70), QPointF(s * 0.72, s * 0.50)]))
        p.end()

    def show_at(self, x: float, y: float, seconds: float = 4.0) -> dict:
        self.move(round(x), round(y))
        self.show()
        self._hide.start(int(seconds * 1000))
        g = self.frameGeometry()
        screen = self.screen()
        return {"qt_geometry": [g.x(), g.y(), g.width(), g.height()], "qt_screen": screen.name() if screen else ""}


# ----- details only Qt knows ---------------------------------------------------------------


def qt_screens() -> list[dict]:
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append({"name": s.name(), "x": g.x(), "y": g.y(), "width": g.width(), "height": g.height(),
                    "dpr": round(s.devicePixelRatio(), 3), "logical_dpi": round(s.logicalDotsPerInch(), 1),
                    "physical_dpi": round(s.physicalDotsPerInch(), 1), "primary": s == QGuiApplication.primaryScreen()})
    return out


def qt_details() -> dict:
    import PySide6
    from PySide6 import QtCore

    out: dict = {"qt_version": QtCore.qVersion(), "pyside_version": PySide6.__version__,
                 "platform": QGuiApplication.platformName(), "screens": qt_screens()}
    try:
        from PySide6.QtMultimedia import QMediaDevices

        out["audio_outputs"] = [d.description() for d in QMediaDevices.audioOutputs()]
        out["default_output"] = QMediaDevices.defaultAudioOutput().description()
        out["audio_inputs"] = [d.description() for d in QMediaDevices.audioInputs()]
        out["default_input"] = QMediaDevices.defaultAudioInput().description()
    except Exception as e:
        out["audio_devices_error"] = f"{type(e).__name__}: {e}"
    return out


# ----- the microphone ----------------------------------------------------------------------


class MicRecording:
    """Records `seconds` from the default input on the main thread, then calls done(result)."""

    def __init__(self, seconds: float, done, tick=None):
        self.seconds = seconds
        self.done = done
        self.tick = tick
        self.data = bytearray()
        self.result: dict = {}
        self.source = None
        self.device = None
        self._timer = None
        self._left = int(seconds)

    def start(self) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.done({"opened": False, "reason": "Windows reports no microphone"})
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(16000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        wanted = device.isFormatSupported(fmt)
        if not wanted:
            fmt = device.preferredFormat()
        self.result = {"device": device.description(), "wanted_format_supported": wanted,
                       "format": {"rate": fmt.sampleRate(), "channels": fmt.channelCount(), "sample": fmt.sampleFormat().name}}
        self.fmt = fmt
        self.source = QAudioSource(device, fmt)
        self.io = self.source.start()
        self.result["error_after_start"] = self.source.error().name
        self.result["state_after_start"] = self.source.state().name
        self._timer = QTimer()
        self._timer.timeout.connect(self._read)
        self._timer.start(50)
        self._ticker = QTimer()
        self._ticker.timeout.connect(self._count)
        self._ticker.start(1000)
        if self.tick:
            self.tick(self._left)
        QTimer.singleShot(int(self.seconds * 1000), self._finish)

    def _count(self) -> None:
        self._left -= 1
        if self.tick and self._left > 0:
            self.tick(self._left)

    def _read(self) -> None:
        if self.io is not None:
            chunk = self.io.readAll()
            if chunk.size():
                self.data.extend(bytes(chunk.data()))

    def _finish(self) -> None:
        self._read()
        self._timer.stop()
        self._ticker.stop()
        err = self.source.error().name
        state = self.source.state().name
        self.source.stop()
        r = self.result
        r["error_at_end"] = err
        r["state_at_end"] = state
        r["bytes"] = len(self.data)
        lv = levels(bytes(self.data), self.fmt.sampleFormat().name)
        channels = max(1, self.fmt.channelCount())
        r.update(lv)
        r["frames"] = lv["samples"] // channels
        r["recorded_seconds"] = round(r["frames"] / self.fmt.sampleRate(), 2) if self.fmt.sampleRate() else 0
        r["opened"] = self.io is not None and err == "NoError" and len(self.data) > 0
        r["all_silent"] = r["opened"] and lv["peak"] == 0.0
        self.done(r)
