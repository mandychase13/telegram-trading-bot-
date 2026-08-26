import base64
import hashlib

from cryptography.fernet import Fernet


def _make_fernet(key: str) -> Fernet:
    """Derive a URL-safe 32-byte Fernet key from any string."""
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(raw)
    return Fernet(fernet_key)


def encrypt(data: str, key: str) -> str:
    """Encrypt a plaintext string; returns a URL-safe base64 token."""
    return _make_fernet(key).encrypt(data.encode("utf-8")).decode("utf-8")


def decrypt(token: str, key: str) -> str:
    """Decrypt a token produced by encrypt()."""
    return _make_fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
