"""The Windows Credential Manager backend, with a fake advapi32 layer in place of Windows."""

import logging

import pytest

from lyssna import keychain
from lyssna.win import credentials
from lyssna.win.credentials import CredentialBackend

SECRET = "sk-très-sécret-ключ"


class FakeAdvapi:
    def __init__(self):
        self.store: dict[str, tuple[str, str, bytes]] = {}
        self.fail: int | None = None

    def read(self, target):
        if self.fail is not None:
            return self.fail, None
        if target not in self.store:
            return credentials.ERROR_NOT_FOUND, None
        return 0, self.store[target][2]

    def write(self, target, user, comment, blob):
        if self.fail is not None:
            return self.fail
        self.store[target] = (user, comment, blob)
        return 0

    def delete(self, target):
        if self.fail is not None:
            return self.fail
        return 0 if self.store.pop(target, None) is not None else credentials.ERROR_NOT_FOUND


@pytest.fixture
def api():
    return FakeAdvapi()


def test_round_trip_set_get_delete(api):
    backend = CredentialBackend(api)
    assert backend.get("openai") == (keychain.NOT_FOUND, None)
    assert backend.set("openai", "first") == 0
    assert backend.set("openai", "second") == 0  # overwrites
    assert backend.get("openai") == (0, "second")
    assert backend.delete("openai") == 0
    assert backend.get("openai") == (keychain.NOT_FOUND, None)
    assert backend.delete("openai") == keychain.NOT_FOUND


def test_credential_names_and_fields(api):
    CredentialBackend(api).set("cartesia", "abc")
    user, comment, _blob = api.store["io.github.andresvourakis.lyssna/cartesia"]
    assert user == "Lyssna"
    assert comment == "Lyssna API key (cartesia)"


def test_non_ascii_key_is_stored_as_utf16le(api):
    backend = CredentialBackend(api)
    backend.set("openai", SECRET)
    assert api.store[credentials.target_name("openai")][2] == SECRET.encode("utf-16-le")
    assert backend.get("openai") == (0, SECRET)


def test_error_codes_pass_through(api):
    backend = CredentialBackend(api)
    api.fail = 5  # ERROR_ACCESS_DENIED
    assert backend.get("openai") == (5, None)
    assert backend.set("openai", "x") == 5
    assert backend.delete("openai") == 5


def test_undecodable_value_is_an_error_not_an_exception(api):
    api.store[credentials.target_name("openai")] = ("Lyssna", "", b"\x00")  # odd length
    assert CredentialBackend(api).get("openai") == (credentials.ERROR_INVALID_DATA, None)


def test_unencodable_key_is_an_error_not_an_exception(api):
    assert CredentialBackend(api).set("openai", "bad\ud800key") == credentials.ERROR_INVALID_PARAMETER
    assert api.store == {}


def test_no_secret_in_logs(api, caplog):
    caplog.set_level(logging.DEBUG)
    backend = CredentialBackend(api)
    backend.set("openai", SECRET)
    backend.get("openai")
    backend.set("openai", "lone\ud800" + SECRET)
    api.store[credentials.target_name("openai")] = ("Lyssna", "", SECRET.encode("utf-16-le") + b"\x00")
    backend.get("openai")
    for record in caplog.records:
        text = record.getMessage()
        assert SECRET not in text and "sk-" not in text


def test_building_a_backend_does_not_load_windows():
    backend = CredentialBackend()
    assert backend._api is None
