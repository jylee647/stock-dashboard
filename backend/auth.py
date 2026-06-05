"""
인증 유틸 — 비밀번호 해시(pbkdf2, 표준 라이브러리)와 서명 세션 토큰(HMAC).
외부 의존성 없음.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
SESSION_TTL = 14 * 24 * 3600  # 14일


# ---------------------------------------------------------------------------
# 비밀번호 해시
# ---------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000)
    return f"pbkdf2_sha256$120000${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, iters, salt, hexhash = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexhash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 세션 토큰  (username|expiry|sig)
# ---------------------------------------------------------------------------
def _sign(msg: str) -> str:
    return hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(username: str) -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    msg = f"{username}|{exp}"
    return f"{msg}|{_sign(msg)}"


def read_session(token: str):
    try:
        username, exp, sig = token.split("|")
        msg = f"{username}|{exp}"
        if not hmac.compare_digest(sig, _sign(msg)):
            return None
        if int(exp) < time.time():
            return None
        return username
    except Exception:
        return None
