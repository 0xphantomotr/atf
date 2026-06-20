import base64
import hashlib
from hmac import compare_digest

from app.core.config import settings

try:
    from cryptography.fernet import Fernet, InvalidToken
except ModuleNotFoundError:  # pragma: no cover - dependency is installed in runtime image.
    Fernet = None
    InvalidToken = ValueError


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return compare_digest(left, right)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored secret could not be decrypted.") from exc


def secret_hint(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}...{value[-4:]}"


def _fernet() -> "Fernet":
    if Fernet is None:
        raise RuntimeError("cryptography is required to encrypt user API keys.")

    digest = hashlib.sha256(settings.user_api_key_encryption_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)
