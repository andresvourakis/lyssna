"""Lyssna's interface, drawn with Qt (PySide6), for the Mac and Windows. Each screen twins one
of the Mac's AppKit interface in lyssna/legacy/ui/, and both build on the same pure sizes,
colours and geometry in layout/, so the two look and behave alike.

The foundation the screens are built on:
  theme      colours, fonts, light and dark mode, reduced motion (twin of legacy/ui/theme.py)
  coords     every coordinate conversion: physical pixels, Qt logical, Mac-style rects
  mainthread on_main and later (twin of legacy/ui/mainthread.py)
  panel      FloatingPanel, the frameless no-focus window (twin of legacy/ui/panel.py)
  widgets    HoverView, IconButton, PillButton, ProgressBar, ScrubBar (twin of legacy/ui/widgets.py)
  icons      vector twins of the SF Symbols the Mac interface uses
  mark, wave the logo and the dictation meter (twins of legacy/ui/mark.py and legacy/ui/wave.py)
  preview    a gallery and screenshot tool: `python -m lyssna.ui.preview`

Everything here runs on the Qt GUI thread; call in from workers with mainthread.on_main.
PySide6 is imported at module top inside this package only, never in layout/. Nothing outside
it may import PySide6 at module top: the engine stays free of Qt."""
