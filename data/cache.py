"""data/cache.py — SQLite-based cache with TTL for OHLCV and info data."""
import sqlite3
import pickle
import time
import os
from pathlib import Path

CACHE_DIR = Path.home() / ".analizator_rynkowy"
CACHE_FILE = CACHE_DIR / "cache.db"


def _get_conn() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_FILE), timeout=10)
    # WAL mode allows concurrent readers + one writer, essential for ThreadPoolExecutor
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data BLOB NOT NULL,
            expires_at REAL NOT NULL
        )"""
    )
    conn.commit()
    return conn


def get(key: str):
    """Return cached value or None if missing / expired."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT data, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        if row is None:
            return None
        data_blob, expires_at = row
        if time.time() > expires_at:
            return None
        return pickle.loads(data_blob)
    except Exception:
        return None


def set(key: str, value, ttl: int):
    """Store value in cache with TTL in seconds."""
    try:
        conn = _get_conn()
        data_blob = pickle.dumps(value)
        expires_at = time.time() + ttl
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, data, expires_at) VALUES (?, ?, ?)",
            (key, data_blob, expires_at),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def invalidate(key: str):
    """Remove a specific cache entry."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def clear_expired():
    """Remove all expired entries."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
        conn.commit()
        conn.close()
    except Exception:
        pass
