"""The Mac's original AppKit interface (app.py and ui/), kept as a fallback to the Qt interface in
lyssna/ui/ (interface = "legacy" in the config file). host.interface() decides which one runs.
This package is deleted when the fallback is no longer needed; nothing outside it imports from it."""
