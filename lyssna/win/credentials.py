"""API keys in Windows Credential Manager, one generic credential per engine config section.

The real version will call CredRead, CredWrite and CredDelete through ctypes. Same contract
as keychain.SecurityBackend: each method returns a status (0 for success, keychain.NOT_FOUND
when there is no key, anything else is a failure) and get also returns the stored text.
Not built yet: every call raises, which lyssna.keychain logs and reads as "no key here"."""

from __future__ import annotations

_NOT_BUILT = "Windows Credential Manager support is not built yet"


class CredentialBackend:
    def get(self, account: str) -> tuple[int, str | None]:
        raise NotImplementedError(_NOT_BUILT)

    def set(self, account: str, value: str) -> int:
        raise NotImplementedError(_NOT_BUILT)

    def delete(self, account: str) -> int:
        raise NotImplementedError(_NOT_BUILT)
