"""Reversible encryption for credentials the application has to replay.

Hashing is the wrong tool here and the distinction matters. A password is only
ever *verified*, so it is hashed: one-way, unrecoverable by design. A Jira API
token is *sent to Atlassian* on every request, so the plaintext must come back
out — that is encryption, not hashing. Storing a hash of these would produce a
database that looks secure and an integration that can never authenticate.

Fernet (AES-128-CBC + HMAC-SHA256) is used rather than raw AES because it
authenticates the ciphertext: a tampered value fails loudly instead of
decrypting to garbage that then gets sent to a third party as a credential.

The key lives in CREDENTIAL_ENCRYPTION_KEY, outside the database. A key stored
next to the ciphertext it protects is decoration.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CredentialEncryptionError(Exception):
    """Raised when a credential cannot be encrypted or decrypted."""


def generate_key() -> str:
    """Mint a key suitable for CREDENTIAL_ENCRYPTION_KEY.

    Exposed so operators have one obvious way to produce a valid key:

        python -c "from app.core.crypto import generate_key; print(generate_key())"
    """
    return Fernet.generate_key().decode()


def _cipher() -> Fernet:
    """Build the cipher, refusing to run without a configured key.

    Deliberately fails instead of falling back to a default or generated key.
    A generated-per-process key would encrypt happily and then be unable to read
    anything back after a restart — every stored credential silently lost.
    """
    key = settings.credential_encryption_key
    if not key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set, so credentials cannot be "
            "stored or read. Generate one with: python -c \"from "
            "app.core.crypto import generate_key; print(generate_key())\""
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. It must be "
            "32 url-safe base64-encoded bytes."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Encrypt one credential for storage.

    Empty input returns empty rather than encrypting the empty string, so
    "no credential set" stays distinguishable from "a credential that happens
    to be blank" without a second column to say which.
    """
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Recover one credential for use.

    Raises:
        CredentialEncryptionError: if the value was not produced by this key —
            a rotated key, a restored backup from another environment, or a
            tampered row. Silently returning "" here would surface as a
            confusing 401 from Jira instead of a clear configuration error.
    """
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "A stored credential could not be decrypted with the current "
            "CREDENTIAL_ENCRYPTION_KEY. It was encrypted with a different key "
            "— re-enter the credential, or restore the original key."
        ) from exc
