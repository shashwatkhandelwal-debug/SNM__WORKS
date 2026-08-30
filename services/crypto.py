import base64
import hashlib
import logging
from typing import Optional
from cryptography.fernet import Fernet
from config import settings

logger = logging.getLogger("snm_works.crypto")

# One-time setup: run this in PowerShell to generate your encryption key:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Copy the output into .env as ENCRYPTION_KEY=


def get_fernet_cipher() -> Fernet:
    """
    Returns a Fernet cipher instance initialized with settings.encryption_key.
    Falls back to a SHA256-derived 32-byte urlsafe base64 key from settings.secret_key if not set.
    """
    raw_key = settings.encryption_key
    if raw_key and len(raw_key.strip()) > 0:
        key_bytes = raw_key.strip().encode("utf-8")
        try:
            return Fernet(key_bytes)
        except Exception as exc:
            logger.warning(f"Invalid ENCRYPTION_KEY format ({exc}), generating fallback derived key.")

    # Deterministic fallback derivation from secret_key
    derived = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    b64_key = base64.urlsafe_b64encode(derived)
    return Fernet(b64_key)


def encrypt_token(plain_text: str) -> str:
    """
    Encrypts sensitive OAuth access tokens or API keys.
    Returns URL-safe encrypted ciphertext string.
    """
    if not plain_text:
        return ""
    cipher = get_fernet_cipher()
    encrypted_bytes = cipher.encrypt(plain_text.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_token(cipher_text: str) -> str:
    """
    Decrypts encrypted token ciphertext.
    Returns plain text token string.
    """
    if not cipher_text:
        return ""
    cipher = get_fernet_cipher()
    decrypted_bytes = cipher.decrypt(cipher_text.encode("utf-8"))
    return decrypted_bytes.decode("utf-8")
