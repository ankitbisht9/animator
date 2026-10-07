"""Passwords, tokens and the password policy.

- Passwords: Argon2id (OWASP's first choice), hashed off the event loop; a sign-in for an unknown
  email still verifies a dummy hash, so the response time does not tell which emails have accounts.
- Access tokens: short-lived signed JWTs (HS256) naming the user and the session.
- Refresh and one-time tokens: 256-bit random values; only their SHA-256 is stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
_DUMMY = _hasher.hash("not a real password, only for timing")

ALGORITHM = "HS256"
ISSUER = "animator"

# The most common passwords and keyboard walks: refused whatever their length.
COMMON = {
    "password",
    "password1",
    "password12",
    "password123",
    "passw0rd",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "qwertyuiop",
    "11111111",
    "00000000",
    "iloveyou",
    "abc12345",
    "letmein123",
    "welcome123",
    "admin123",
    "administrator",
    "football",
    "baseball",
    "sunshine",
    "princess",
    "dragon123",
    "monkey123",
    "trustno1",
    "1q2w3e4r",
    "1qaz2wsx",
    "zaq12wsx",
    "asdfghjkl",
    "superman",
    "starwars",
    "whatever",
    "qazwsxedc",
    "computer",
    "internet",
    "animator",
    "animator123",
    "changeme",
    "p@ssw0rd",
    "p@ssword",
    "india123",
    "student123",
}


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored: str | None, password: str) -> tuple[bool, str | None]:
    """(matches, a new hash when the stored one uses outdated parameters)."""

    def check() -> tuple[bool, str | None]:
        try:
            _hasher.verify(stored or _DUMMY, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, None
        if stored is None:
            return False, None
        return True, (_hasher.hash(password) if _hasher.check_needs_rehash(stored) else None)

    return await asyncio.to_thread(check)


def password_problem(password: str, email: str = "", name: str = "", min_length: int = 8) -> str | None:
    """Why a password is too weak, in words for the person choosing it; None when it is fine."""
    if len(password) < min_length:
        return f"Use at least {min_length} characters."
    if len(password) > 128:
        return "Use at most 128 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9\W_]", password):
        return "Mix letters with numbers or symbols."
    low = password.lower()
    if low in COMMON or len(set(low)) < 4:
        return "This password is too common. Choose something less guessable."
    local = email.split("@")[0].lower()
    if (len(local) >= 4 and local in low) or (len(name) >= 4 and name.lower().replace(" ", "") in low):
        return "Do not use your name or email in the password."
    return None


def random_token() -> str:
    return secrets.token_urlsafe(32)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    session_id: str
    role: str
    expires_at: float


def issue_access(secret: str, user_id: str, session_id: str, role: str, ttl_s: int) -> str:
    t = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": user_id,
        "sid": session_id,
        "role": role,
        "typ": "access",
        "iat": t,
        "nbf": t - 5,
        "exp": t + ttl_s,
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def read_access(secret: str, token: str) -> AccessClaims | None:
    """The claims of a valid, unexpired access token; None for anything else."""
    try:
        data = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "sid", "typ"]},
            leeway=5,
        )
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "access":
        return None
    return AccessClaims(str(data["sub"]), str(data["sid"]), str(data.get("role", "")), float(data["exp"]))
