"""Site administration: users, login sessions and per-user sync tokens (data/admin.db)."""
import hashlib
import logging
import re
import secrets
import sqlite3
import time

from .config import ADMIN_DB, ADMIN_PASSWORD, ADMIN_USER

log = logging.getLogger(__name__)

ADMIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_tokens (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
SESSION_MS = 30 * 86400 * 1000
_PBKDF2_ROUNDS = 120_000


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(ADMIN_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, expected = stored.split("$")
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return secrets.compare_digest(h.hex(), expected)
    except (ValueError, TypeError):
        return False


def init_admin_db() -> None:
    with _connect() as conn:
        conn.executescript(ADMIN_SCHEMA)
        if not conn.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
            password = ADMIN_PASSWORD or secrets.token_urlsafe(9)
            conn.execute(
                "INSERT OR IGNORE INTO users (username,password_hash,is_admin,created_at) VALUES (?,?,1,?)",
                (ADMIN_USER, hash_password(password), int(time.time() * 1000)),
            )
            if ADMIN_PASSWORD:
                log.info("admin: utente amministratore '%s' creato (password da variabile d'ambiente)", ADMIN_USER)
            else:
                log.warning(
                    "admin: nessuna ADMIN_PASSWORD impostata - utente '%s' creato con password casuale: %s",
                    ADMIN_USER, password,
                )


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match(username or ""))


def create_user(username: str, password: str, is_admin: bool = False) -> None:
    username = (username or "").strip().lower()
    if not valid_username(username):
        raise ValueError("Nome utente non valido (2-32 caratteri: lettere minuscole, numeri, . _ -)")
    if len(password or "") < 4:
        raise ValueError("Password troppo corta (minimo 4 caratteri)")
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("Utente già esistente")
        conn.execute(
            "INSERT INTO users (username,password_hash,is_admin,created_at) VALUES (?,?,?,?)",
            (username, hash_password(password), int(is_admin), int(time.time() * 1000)),
        )


def list_users() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT username,is_admin,created_at FROM users ORDER BY username")]


def delete_user(username: str, acting: str) -> None:
    with _connect() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            raise ValueError("Utente non trovato")
        if username == acting:
            raise ValueError("Non puoi eliminare te stesso")
        if row["is_admin"] and conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0] <= 1:
            raise ValueError("Non puoi eliminare l'ultimo amministratore")
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.execute("DELETE FROM sync_tokens WHERE username=?", (username,))


def set_password(username: str, password: str) -> None:
    if len(password or "") < 4:
        raise ValueError("Password troppo corta (minimo 4 caratteri)")
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_password(password), username))
        if cur.rowcount == 0:
            raise ValueError("Utente non trovato")
        conn.execute("DELETE FROM sessions WHERE username=?", (username,))


def authenticate(username: str, password: str) -> str | None:
    """Checks credentials; returns a new session token or None."""
    username = (username or "").strip().lower()
    with _connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        now = int(time.time() * 1000)
        conn.execute("INSERT INTO sessions (token,username,created_at,expires_at) VALUES (?,?,?,?)",
                     (token, username, now, now + SESSION_MS))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return token


def user_from_session(token: str | None) -> dict | None:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT s.username, s.expires_at, u.is_admin FROM sessions s "
            "JOIN users u ON u.username=s.username WHERE s.token=?", (token,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < int(time.time() * 1000):
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
        return {"username": row["username"], "is_admin": bool(row["is_admin"])}


def destroy_session(token: str | None) -> None:
    if token:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_from_sync_token(token: str | None) -> dict | None:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT t.username, u.is_admin FROM sync_tokens t "
            "JOIN users u ON u.username=t.username WHERE t.token=?", (token,)).fetchone()
        return {"username": row["username"], "is_admin": bool(row["is_admin"])} if row else None


def get_sync_token(username: str) -> str:
    with _connect() as conn:
        row = conn.execute("SELECT token FROM sync_tokens WHERE username=?", (username,)).fetchone()
        if row:
            return row["token"]
        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO sync_tokens (token,username,created_at) VALUES (?,?,?)",
                     (token, username, int(time.time() * 1000)))
        return token


def regenerate_sync_token(username: str) -> str:
    with _connect() as conn:
        conn.execute("DELETE FROM sync_tokens WHERE username=?", (username,))
    return get_sync_token(username)


# ---------- site settings ----------
def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

