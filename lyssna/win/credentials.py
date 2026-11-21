"""API keys in Windows Credential Manager, one generic credential per engine config section.

Why: Credential Manager is the Windows counterpart of the macOS Keychain. It encrypts the key
for this user account, so a key never sits in a plain text file. Same contract as
keychain.SecurityBackend: each method returns a status (0 for success, keychain.NOT_FOUND when
there is no key, any other number is a Windows error code) and get also returns the stored
text. lyssna.keychain logs those codes as numbers only.

The key is stored as UTF-16-LE bytes, which is what Windows tools (cmdkey, PowerShell) expect
for text. The calls into advapi32 live in _Advapi, a thin layer that loads only when first
used, so this module imports on any system and tests swap the layer for a fake. Nothing here
logs or raises the key itself, not even in part."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from .. import keychain

log = logging.getLogger("lyssna.win.credentials")

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # kept on this PC for this user, across sign-ins
ERROR_NOT_FOUND = 1168
ERROR_INVALID_DATA = 13  # a stored value that is not valid UTF-16 text
ERROR_INVALID_PARAMETER = 87  # a key that cannot be encoded as UTF-16 text
USER_NAME = "Lyssna"


def target_name(account: str) -> str:
    """The credential's name as Credential Manager lists it."""
    return f"{keychain.SERVICE}/{account}"


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _Advapi:
    """The four advapi32 calls. Each returns 0 or the GetLastError code."""

    def __init__(self):
        dll = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]  # exists on Windows only
        pcred = ctypes.POINTER(CREDENTIALW)
        self._read = dll.CredReadW
        self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pcred)]
        self._read.restype = wintypes.BOOL
        self._write = dll.CredWriteW
        self._write.argtypes = [pcred, wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._delete = dll.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = dll.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def read(self, target: str) -> tuple[int, bytes | None]:
        cred = ctypes.POINTER(CREDENTIALW)()
        if not self._read(target, CRED_TYPE_GENERIC, 0, ctypes.byref(cred)):
            return ctypes.get_last_error(), None  # type: ignore[attr-defined]
        try:
            size = cred.contents.CredentialBlobSize
            blob = ctypes.string_at(cred.contents.CredentialBlob, size) if size else b""
        finally:
            self._free(cred)
        return 0, blob

    def write(self, target: str, user: str, comment: str, blob: bytes) -> int:
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else None
        cred = CREDENTIALW()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = comment
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)) if buf is not None else None
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = user
        try:
            ok = self._write(ctypes.byref(cred), 0)  # CredWrite replaces an existing credential of the same name
            return 0 if ok else ctypes.get_last_error()  # type: ignore[attr-defined]
        finally:
            if buf is not None:
                ctypes.memset(buf, 0, len(blob))  # do not leave a copy of the key in our memory

    def delete(self, target: str) -> int:
        if not self._delete(target, CRED_TYPE_GENERIC, 0):
            return ctypes.get_last_error()  # type: ignore[attr-defined]
        return 0


def _status(code: int) -> int:
    return keychain.NOT_FOUND if code == ERROR_NOT_FOUND else code


class CredentialBackend:
    """Credential Manager behind the keychain backend contract. `api` is the advapi32 layer;
    the real one loads on first use, so building a backend never touches Windows."""

    def __init__(self, api: object | None = None):
        self._api = api

    @property
    def api(self):
        if self._api is None:
            self._api = _Advapi()
        return self._api

    def get(self, account: str) -> tuple[int, str | None]:
        code, blob = self.api.read(target_name(account))
        if code != 0 or blob is None:
            return _status(code), None
        try:
            return 0, blob.decode("utf-16-le")
        except UnicodeDecodeError:  # not raised on: its message would quote part of the stored bytes
            log.warning("the stored value for %s is not UTF-16 text (%d bytes)", account, len(blob))
            return ERROR_INVALID_DATA, None

    def set(self, account: str, value: str) -> int:
        try:
            blob = value.encode("utf-16-le")
        except UnicodeEncodeError:  # not raised on: its message would quote part of the key
            log.warning("the key for %s cannot be stored as UTF-16 text", account)
            return ERROR_INVALID_PARAMETER
        return _status(self.api.write(target_name(account), USER_NAME, f"Lyssna API key ({account})", blob))

    def delete(self, account: str) -> int:
        return _status(self.api.delete(target_name(account)))
