"""The look-and-layout logic both interfaces share: sizes, colours as plain numbers, and the
pure functions that place and shape things. Nothing here imports AppKit, Quartz, Foundation,
objc or Qt, so the Mac interface (AppKit) and the Windows one (Qt) draw from the same rules."""
