"""Lyssna's colours as plain numbers: the brand cream and ink as 0-255 RGB tuples, the alphas
the interface draws them at, and the few fixed colours that do not follow light and dark mode.
Shared so the Qt interface and the Mac's legacy one (legacy/ui/theme.py, legacy/ui/widgets.py,
legacy/ui/panel.py) build their colours from the same values."""

from __future__ import annotations

CREAM = (0xF4, 0xEF, 0xE6)    # #F4EFE6
INK = (0x0F, 0x0F, 0x11)      # #0F0F11, the dark surface (the logo tile)
INK_TEXT = (0x14, 0x14, 0x14)  # #141414, text and marks on cream

CONTENT_DIM_ALPHA = 0.60  # secondary text
HAIRLINE_ALPHA = 0.12     # borders and separators
FILL_SOFT_ALPHA = 0.08    # soft pills, keycaps, tracks

# The hover glows: content at this alpha on brand surfaces, white at this alpha on dark ones.
CLEAR_GLOW_ALPHA = 0.10
WHITE_GLOW_ALPHA = 0.16

# The idle squiggle's colours, the same in light and dark mode: a quiet dark shape with a soft white outline
# reads on any background and asks for less attention than the brand colours did.
SQUIGGLE_FILL = (0x2C / 255, 0x2C / 255, 0x2E / 255, 0.95)   # dark gray, sRGB + alpha
SQUIGGLE_OUTLINE = (1.0, 1.0, 1.0, 0.55)                     # soft white, sRGB + alpha

# The fixed dark background of a floating panel that does not use the brand surface, RGB + alpha in 0..1.
PANEL_BG = (0.10, 0.10, 0.11, 0.96)
