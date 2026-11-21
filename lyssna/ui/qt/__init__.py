"""The Windows interface, drawn with Qt (PySide6). Each module twins a Mac file in lyssna/ui/
and builds on the same pure sizes, colours and geometry in lyssna/ui/shared/, so the two
interfaces look and behave alike.

The foundation the screens are built on:
  theme      colours, fonts, light and dark mode, reduced motion (twin of ui/theme.py)
  coords     every coordinate conversion: physical pixels, Qt logical, Mac-style rects
  mainthread on_main and later (twin of ui/mainthread.py)
  panel      FloatingPanel, the frameless no-focus window (twin of ui/panel.py)
  widgets    HoverView, IconButton, PillButton, ProgressBar, ScrubBar (twin of ui/widgets.py)
  icons      vector twins of the SF Symbols the Mac interface uses
  mark, wave the logo and the dictation meter (twins of ui/mark.py and ui/wave.py)
  preview    a gallery and screenshot tool: `python -m lyssna.ui.qt.preview`

Everything here runs on the Qt GUI thread; call in from workers with mainthread.on_main.
PySide6 is imported at module top inside this package only. Nothing outside it may import
PySide6 at module top, because the Mac app never installs it."""
