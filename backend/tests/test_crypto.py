"""Credential encryption — reversible by design, unlike a password hash.

These pin the property the whole feature rests on: a Jira token goes in, the
same token comes back out, and neither the ciphertext nor a wrong key can be
mistaken for success.
"""

import pytest

from app.core import crypto
from app.core.crypto import (
    CredentialEncryptionError,
    decrypt,
    encrypt,
    generate_key,
)


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", generate_key()
    )


def test_round_trip_returns_the_original_secret() -> None:
    """The plaintext MUST come back — these tokens are replayed to their APIs.

    This is the whole reason hashing is not an option here: a hash would store
    fine and then be unable to authenticate against Jira ever again.
    """
    token = "ATATT3xFfGF0-not-a-real-token"
    assert decrypt(encrypt(token)) == token


def test_ciphertext_does_not_contain_the_secret() -> None:
    stored = encrypt("super-secret-token")
    assert "super-secret-token" not in stored


def test_encryption_is_salted_per_call() -> None:
    """Two encryptions of one value differ, so equal rows are not detectable."""
    assert encrypt("same-token") != encrypt("same-token")


def test_empty_stays_empty_in_both_directions() -> None:
    """"Not configured" must stay distinguishable from "configured as blank".

    Encrypting "" would produce ciphertext, making an unset credential look set
    to every `has_*_token` flag in the API.
    """
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_a_different_key_cannot_read_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotated key fails loudly rather than yielding garbage.

    Silently returning "" here would surface later as a confusing 401 from
    Atlassian instead of a configuration error naming the real cause.
    """
    stored = encrypt("token")
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", generate_key()
    )

    with pytest.raises(CredentialEncryptionError, match="different key"):
        decrypt(stored)


def test_tampered_ciphertext_is_rejected() -> None:
    """Fernet authenticates, so an edited row cannot decrypt to something else."""
    stored = encrypt("token")
    tampered = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")

    with pytest.raises(CredentialEncryptionError):
        decrypt(tampered)


def test_missing_key_refuses_rather_than_inventing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-process fallback key would lose every credential on restart."""
    monkeypatch.setattr("app.core.crypto.settings.credential_encryption_key", "")

    with pytest.raises(CredentialEncryptionError, match="CREDENTIAL_ENCRYPTION_KEY"):
        encrypt("token")


def test_malformed_key_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", "not-a-fernet-key"
    )

    with pytest.raises(CredentialEncryptionError, match="not a valid Fernet key"):
        encrypt("token")


def test_generated_key_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented way to mint a key must produce one that works."""
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", generate_key()
    )
    assert decrypt(encrypt("x")) == "x"
    assert crypto is not None
