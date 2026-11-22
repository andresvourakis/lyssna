"""The side-by-side comparison of today's Mac interface (AppKit, lyssna/legacy/ui) and the Qt
interface (lyssna/ui), screen state by screen state, in light and dark. Entry point:

    uv run --no-sync python -m scripts.compare [--only PATTERN] [--out DIR]

run.py starts the legacy side and the Qt side in two processes of their own (AppKit and an
offscreen Qt do not share one process cleanly), each rendering the catalogue (catalogue.py) from
the same fake data (fixtures.py) into raw pictures and element lists, then compose.py measures
and lays out the comparison sheets, the index and the reports, and motion.py the animation table
and filmstrips. Nothing here changes the app."""
