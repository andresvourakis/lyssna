"""Fetch one large file over HTTPS into place, safely.

The bytes land in `<name>.part` next to the target. Only after the size and SHA-256 match
the pinned values is it renamed onto the real name, so a file at the final path is always
complete and verified, and a quick existence check is enough to know it is usable.
An interrupted download keeps its `.part`, and the next attempt asks the server for the rest
(an HTTP Range request) instead of starting over. There are no automatic retries: the caller
decides when to try again, usually because the user asked for something that needs the file."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

import httpx

log = logging.getLogger("lyssna.download")

# connect: fail fast when offline; read: a stalled connection surfaces in half a minute.
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


class DownloadError(RuntimeError):
    """A download that could not finish. The message is meant for the user."""


class DownloadCancelled(DownloadError):
    pass


def make_client() -> httpx.Client:
    """The HTTP client used for downloads. Tests replace this so nothing reaches the network."""
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True)


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(
    url: str,
    dest: Path,
    sha256: str,
    size: int,
    on_progress: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
    what: str = "voice",
) -> None:
    """Download `url` to `dest`, resuming a partial `.part` file if there is one.
    on_progress(bytes_of_this_file_so_far) is called as data arrives. Raises DownloadError
    (DownloadCancelled when `cancel` is set) and never leaves anything at `dest` unless the
    file is complete and its hash matches. `what` names the download in the error messages
    ("voice", "speech model"), so each caller's messages read naturally."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    have = part.stat().st_size if part.exists() else 0
    if have > size:  # not ours, or from a different version of the file
        part.unlink()
        have = 0
    report = on_progress or (lambda n: None)
    if have < size:
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with make_client() as client, client.stream("GET", url, headers=headers) as r:
                if r.status_code == 206 and have and r.headers.get("content-range", "").startswith(f"bytes {have}-"):
                    mode = "ab"
                elif r.status_code == 200:
                    mode, have = "wb", 0  # the server ignored the range: start over
                else:
                    if r.status_code == 416:  # our partial file does not fit the server's; drop it
                        part.unlink(missing_ok=True)
                    raise DownloadError(f"{what.capitalize()} download failed (the server answered {r.status_code}). Try again later.")
                report(have)
                with open(part, mode) as f:
                    for chunk in r.iter_bytes():  # as it arrives, so an interruption keeps every byte
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled(f"{what.capitalize()} download stopped.")
                        f.write(chunk)
                        have += len(chunk)
                        report(have)
                        if have > size:
                            break
                    f.flush()
                    os.fsync(f.fileno())
        except httpx.HTTPError as e:
            log.warning("download of %s failed: %s", url, e)
            raise DownloadError(f"{what.capitalize()} download failed. Check your internet connection and try again.") from e
        except OSError as e:
            raise DownloadError(f"Could not save the {what}: {e}") from e
    if part.stat().st_size != size or sha256_of(part) != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"The downloaded {what} was damaged and has been deleted. Try again.")
    os.replace(part, dest)
    log.info("downloaded %s (%d bytes, sha256 ok)", dest.name, size)
