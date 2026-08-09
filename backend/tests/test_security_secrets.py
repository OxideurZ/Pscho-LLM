import sys

import pytest

from backend.app.security import SecretCorruptError, SecretMissingError, WindowsDpapiSecretStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-specific")


def test_dpapi_secret_store_round_trip_and_distinct_names(tmp_path) -> None:
    store = WindowsDpapiSecretStore(tmp_path / "security")
    database_key = store.create("database-encryption")
    api_key = store.create("local-access")

    assert len(database_key) == 32
    assert database_key != api_key
    assert store.get("database-encryption") == database_key
    assert store.get("local-access") == api_key
    assert (
        database_key
        not in (tmp_path / "security" / "psych-local-database-encryption.protected").read_bytes()
    )


def test_dpapi_secret_store_fails_closed_for_missing_or_corrupt_secret(tmp_path) -> None:
    store = WindowsDpapiSecretStore(tmp_path / "security")
    with pytest.raises(SecretMissingError):
        store.get("database-encryption")
    store.create("database-encryption", b"artificial-secret")
    path = tmp_path / "security" / "psych-local-database-encryption.protected"
    path.write_bytes(b"not-a-dpapi-blob")
    with pytest.raises(SecretCorruptError):
        store.get("database-encryption")
