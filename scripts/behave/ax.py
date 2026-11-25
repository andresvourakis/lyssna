"""Reading the test app's menu bar menu and windows through the Accessibility API, which this
shell is allowed to use. Only the test app's own process is ever asked (by its pid), never the
owner's apps."""

from __future__ import annotations

import time

import ApplicationServices as AX

MAX_DEPTH = 12


def app(pid: int):
    return AX.AXUIElementCreateApplication(pid)


def attr(el, name: str):
    err, value = AX.AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def children(el) -> list:
    return list(attr(el, "AXChildren") or [])


def press(el) -> bool:
    return AX.AXUIElementPerformAction(el, "AXPress") == 0


def status_items(pid: int) -> list:
    bar = attr(app(pid), "AXExtrasMenuBar")
    return children(bar) if bar is not None else []


def _modifiers(mask) -> str:
    # kAXMenuItemModifier*: 1 shift, 2 option, 4 control, 8 no command
    if mask is None:
        return ""
    m = int(mask)
    parts = []
    if m & 4:
        parts.append("ctrl")
    if m & 2:
        parts.append("opt")
    if m & 1:
        parts.append("shift")
    if not m & 8:
        parts.append("cmd")
    return "+".join(parts)


def menu_rows(menu, depth: int = 0) -> list[dict]:
    """Every row of an AXMenu: title, enabled, its mark (a check), its key, and its submenu rows."""
    rows = []
    for item in children(menu):
        if attr(item, "AXRole") != "AXMenuItem":
            continue
        title = str(attr(item, "AXTitle") or "")
        key = attr(item, "AXMenuItemCmdChar")
        row = {"title": title or "---", "enabled": bool(attr(item, "AXEnabled")),
               "mark": str(attr(item, "AXMenuItemMarkChar") or ""),
               "key": f"{_modifiers(attr(item, 'AXMenuItemCmdModifiers'))}+{key}" if key else ""}
        subs = [c for c in children(item) if attr(c, "AXRole") == "AXMenu"]
        if subs and depth < MAX_DEPTH:
            row["submenu"] = menu_rows(subs[0], depth + 1)
        rows.append(row)
    return rows


def find_row(menu, title_start: str):
    for item in children(menu):
        if str(attr(item, "AXTitle") or "").startswith(title_start):
            return item
    return None


def status_menu(pid: int, open_wait: float = 0.6):
    """The status item's menu element, opened by pressing the item if AX lists no menu while it
    is closed. Returns (item, menu, opened_by_press)."""
    items = status_items(pid)
    if not items:
        return None, None, False
    item = items[0]
    menus = [c for c in children(item) if attr(c, "AXRole") == "AXMenu"]
    if menus and children(menus[0]):
        return item, menus[0], False
    press(item)
    time.sleep(open_wait)
    menus = [c for c in children(item) if attr(c, "AXRole") == "AXMenu"]
    return item, (menus[0] if menus else None), True


def cancel(el) -> bool:
    return AX.AXUIElementPerformAction(el, "AXCancel") == 0


def windows(pid: int) -> list:
    return list(attr(app(pid), "AXWindows") or [])


def frame(el) -> tuple[float, float, float, float] | None:
    """The element's (x, y, w, h) on screen, top-left points."""
    pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AX.AXValueGetValue(pos, AX.kAXValueCGPointType, None)
    ok2, s = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
    return (float(p.x), float(p.y), float(s.width), float(s.height)) if ok1 and ok2 else None


def find(el, test, depth: int = 0) -> list:
    """Every descendant of `el` (and itself) for which test(element) is true."""
    out = [el] if test(el) else []
    if depth < MAX_DEPTH:
        for c in children(el):
            out += find(c, test, depth + 1)
    return out


def tree(el, depth: int = 0, limit: int = 400, frames: bool = False) -> list[dict]:
    """A flat list of the element and its descendants: role, title or value, enabled, depth (and
    the frame with `frames`)."""
    out: list[dict] = []

    def visit(e, d):
        if len(out) >= limit or d > MAX_DEPTH:
            return
        role = str(attr(e, "AXRole") or "")
        text = attr(e, "AXTitle") or attr(e, "AXValue") or attr(e, "AXDescription") or ""
        if not isinstance(text, str):
            text = str(text)
        out.append({"role": role, "text": text[:120], "enabled": attr(e, "AXEnabled"), "depth": d})
        if frames:
            f = frame(e)
            out[-1]["frame"] = [round(v) for v in f] if f else None
        for c in children(e):
            visit(c, d + 1)

    visit(el, depth)
    return out
