"""
사용자 계정 DB (PostgreSQL / Neon)
DATABASE_URL 환경변수가 있으면 Postgres 사용. 없으면 인증 비활성(개발용).
"""
from __future__ import annotations

import os
import threading
from typing import Optional

_DSN = os.getenv("DATABASE_URL", "").strip()
_LOCK = threading.Lock()
_pool = None


def auth_enabled() -> bool:
    return bool(_DSN)


def _get_pool():
    global _pool
    if _pool is None:
        with _LOCK:
            if _pool is None:
                import psycopg2
                from psycopg2 import pool as pgpool
                # Neon은 sslmode=require 필요
                dsn = _DSN
                if "sslmode=" not in dsn:
                    dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
                _pool = pgpool.SimpleConnectionPool(1, 5, dsn)
    return _pool


def _conn():
    return _get_pool().getconn()


def _put(c):
    try:
        _get_pool().putconn(c)
    except Exception:
        pass


def init_db():
    """users 테이블 생성 (없으면)."""
    if not auth_enabled():
        return
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    pw_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        c.commit()
    finally:
        _put(c)


def create_user(username: str, pw_hash: str) -> bool:
    """성공 True, 중복이면 False."""
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, pw_hash) VALUES (%s, %s) "
                "ON CONFLICT (username) DO NOTHING RETURNING id",
                (username, pw_hash),
            )
            row = cur.fetchone()
        c.commit()
        return row is not None
    finally:
        _put(c)


def get_user(username: str) -> Optional[dict]:
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT id, username, pw_hash FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "username": row[1], "pw_hash": row[2]}
    finally:
        _put(c)


def user_count() -> int:
    c = _conn()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            return int(cur.fetchone()[0])
    finally:
        _put(c)
