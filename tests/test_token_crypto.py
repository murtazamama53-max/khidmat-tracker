import pytest

from app.services.token_crypto import TokenDecryptionError, decrypt_token, encrypt_token


def test_encrypt_then_decrypt_roundtrip():
    secret = "test-secret-key-12345"
    plaintext = "1//09fake-refresh-token-value"
    encrypted = encrypt_token(plaintext, secret)
    assert encrypted != plaintext
    assert decrypt_token(encrypted, secret) == plaintext


def test_encrypted_value_is_not_plaintext_substring():
    secret = "another-secret"
    plaintext = "super-secret-refresh-token"
    encrypted = encrypt_token(plaintext, secret)
    assert plaintext not in encrypted


def test_wrong_secret_key_fails_to_decrypt():
    plaintext = "some-refresh-token"
    encrypted = encrypt_token(plaintext, "correct-key")
    with pytest.raises(TokenDecryptionError):
        decrypt_token(encrypted, "wrong-key")


def test_different_secrets_produce_different_ciphertexts():
    plaintext = "token-value"
    e1 = encrypt_token(plaintext, "key-one")
    e2 = encrypt_token(plaintext, "key-two")
    assert e1 != e2
