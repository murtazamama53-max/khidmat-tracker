"""
Encrypts OAuth refresh tokens at rest (blueprint sections 10/12: "Google
OAuth tokens stored encrypted at rest", "Protect OAuth tokens
appropriately"). Uses Fernet (AES-128-CBC + HMAC) with a key derived from
the app's SECRET_KEY via PBKDF2, so no separate secret needs to be
managed -- rotating SECRET_KEY does mean previously-stored tokens can no
longer be decrypted, which is documented in the README.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_SALT = b"khidmat-earnings-tracker-token-salt-v1"  # fixed salt is fine here: SECRET_KEY is already the real secret


class TokenDecryptionError(ValueError):
    pass


def _derive_key(secret_key: str) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", secret_key.encode("utf-8"), _SALT, 200_000, dklen=32)
    return base64.urlsafe_b64encode(raw)


def encrypt_token(plaintext_token: str, secret_key: str) -> str:
    fernet = Fernet(_derive_key(secret_key))
    return fernet.encrypt(plaintext_token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str, secret_key: str) -> str:
    fernet = Fernet(_derive_key(secret_key))
    try:
        return fernet.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise TokenDecryptionError("Could not decrypt the stored token -- SECRET_KEY may have changed.") from e


def encrypt_bytes(plaintext: bytes, secret_key: str) -> bytes:
    """Same scheme as encrypt_token, but for arbitrary binary data (e.g. a database backup file)."""
    fernet = Fernet(_derive_key(secret_key))
    return fernet.encrypt(plaintext)


def decrypt_bytes(ciphertext: bytes, secret_key: str) -> bytes:
    fernet = Fernet(_derive_key(secret_key))
    try:
        return fernet.decrypt(ciphertext)
    except InvalidToken as e:
        raise TokenDecryptionError("Could not decrypt this backup -- it may be corrupt or SECRET_KEY has changed.") from e
