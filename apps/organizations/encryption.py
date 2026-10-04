"""Encrypt/decrypt secrets stored at rest — currently a connected org's
Google Drive OAuth tokens (CloudBackupConnection). Never stored or
logged in plain text.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretDecryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    return Fernet(settings.SECRETS_ENCRYPTION_KEY)


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Stored secret could not be decrypted — SECRETS_ENCRYPTION_KEY may have changed."
        ) from exc
