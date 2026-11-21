"""API keys in the macOS login Keychain, one generic password per engine config section.

Why: the Keychain encrypts the key and ties it to the app that stored it. We call the Security
framework directly instead of the `security` command line tool, which would need the key as an
argument where any other process could see it. Same contract as lyssna/win/credentials.py: each
method returns a status (0 for success, keystore.NOT_FOUND when there is no key) and get also
returns the stored text. lyssna.keystore does the caching and the logging. Security loads inside
the methods, so this module imports without it."""

from __future__ import annotations

from .. import keystore


class CredentialBackend:
    """The real login Keychain. Each method returns an OSStatus (0 = success); get also
    returns the stored text. Tests swap this out for an in-memory fake."""

    def __init__(self, service: str = keystore.SERVICE):
        self.service = service

    def _query(self, account: str) -> dict:
        import Security

        return {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: self.service,
            Security.kSecAttrAccount: account,
        }

    def get(self, account: str) -> tuple[int, str | None]:
        import Security

        query = self._query(account)
        query[Security.kSecReturnData] = True
        query[Security.kSecMatchLimit] = Security.kSecMatchLimitOne
        status, data = Security.SecItemCopyMatching(query, None)
        if status != 0 or data is None:
            return status, None
        return 0, bytes(data).decode("utf-8")

    def set(self, account: str, value: str) -> int:
        import Security
        from Foundation import NSData

        raw = value.encode("utf-8")
        data = NSData.dataWithBytes_length_(raw, len(raw))
        query = self._query(account)
        attrs = dict(query)
        attrs[Security.kSecValueData] = data
        attrs[Security.kSecAttrLabel] = f"Lyssna API key ({account})"
        status, _ = Security.SecItemAdd(attrs, None)
        if status == keystore.DUPLICATE:
            status = Security.SecItemUpdate(query, {Security.kSecValueData: data})
        return status

    def delete(self, account: str) -> int:
        import Security

        return Security.SecItemDelete(self._query(account))
