from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, get_jwt_secret

_PBKDF2_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2-SHA256 hash."""
    try:
        scheme, iter_str, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_access_token(user_id: int, login_id: str, role: str, expires_minutes: int = JWT_EXPIRE_MINUTES) -> str:
    """Create a signed HS256 JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "login_id": login_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate an HS256 JWT access token."""
    return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])


def normalize_name(text: str) -> str:
    """Normalize client/party names for duplicate suggestion matching."""
    cleaned = text.lower().strip()
    for token in ("private limited", "pvt. ltd.", "pvt ltd", "limited", "ltd.", "ltd", "llp", "(demo)"):
        cleaned = cleaned.replace(token, " ")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_case_number(text: str) -> str:
    """Normalize case numbers (e.g., 'COMS/24/2026' == 'coms 24 2026') for suggestion matching."""
    return re.sub(r"[^a-z0-9]", "", text.lower().strip())

