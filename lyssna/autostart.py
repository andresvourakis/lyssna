"""Start at Login for the bundled app, via SMAppService (macOS 13+). Only meaningful when
running from Lyssna.app; from source it would register the wrong bundle."""

from __future__ import annotations

import logging

log = logging.getLogger("lyssna.autostart")

STATUS_ENABLED = 1


def _service():
    from ServiceManagement import SMAppService

    return SMAppService.mainAppService()


def is_enabled() -> bool:
    try:
        return _service().status() == STATUS_ENABLED
    except Exception:
        log.exception("could not read login item status")
        return False


def set_enabled(enabled: bool) -> bool:
    """Register or unregister the app as a login item. Returns True on success."""
    try:
        service = _service()
        ok, error = service.registerAndReturnError_(None) if enabled else service.unregisterAndReturnError_(None)
    except Exception:
        log.exception("could not change login item")
        return False
    if not ok:
        log.error("start at login %s failed: %s", "on" if enabled else "off", error)
        return False
    log.info("start at login %s", "on" if enabled else "off")
    return True
