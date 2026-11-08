import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import (
    ADMIN_USER, CATALOG_DB, DATA_DIR, DEVICE_NAME, MANUALS_DIR, REBRICKABLE_API_KEY, USER_DB, USERS_DIR,
)

log = logging.getLogger(__name__)

USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Each device owns its own rows; the total quantity is the SUM over devices.
-- Rows are only written by their owner device, so sync is a conflict-free merge.
CREATE TABLE IF NOT EXISTS inventory (
    part_num TEXT NOT NULL,
    color_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (part_num, color_id, device_id)
);
CREATE VIEW IF NOT EXISTS inventory_total AS
    SELECT part_num, color_id, SUM(quantity) AS quantity, MAX(updated_at) AS updated_at
    FROM inventory GROUP BY part_num, color_id HAVING SUM(quantity) > 0;
CREATE TABLE IF NOT EXISTS manuals (
    id TEXT PRIMARY KEY,
    set_num TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT,
    source_url TEXT,
    sha256 TEXT,
    size INTEGER,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    device_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS manuals_set ON manuals(set_num);
CREATE TABLE IF NOT EXISTS saved_projects (
    set_num TEXT PRIMARY KEY,
    note TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    device_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS peers (
    url TEXT PRIMARY KEY,
    token TEXT,
    last_sync INTEGER
);
"""

_initialized: set[str] = set()


def now_ms() -> int:
    return int(time.time() * 1000)


def user_dir(username: str) -> Path:
    return USERS_DIR / username


def manuals_dir(username: str) -> Path:
    d = user_dir(username) / "manuals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _connect(username: str) -> sqlite3.Connection:
    user_dir(username).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(user_dir(username) / "lego.sqlite", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if CATALOG_DB.exists():
        conn.execute("ATTACH DATABASE ? AS cat", (str(CATALOG_DB),))
    return conn


def _init_user_db(conn: sqlite3.Connection, username: str) -> None:
    conn.executescript(USER_SCHEMA)
    try:  # column added after the first multi-user release
        conn.execute("ALTER TABLE peers ADD COLUMN token TEXT")
    except sqlite3.Error:
        pass
    if not conn.execute("SELECT 1 FROM meta WHERE key='device_id'").fetchone():
        conn.execute("INSERT INTO meta VALUES ('device_id', ?)", (uuid.uuid4().hex[:12],))
    if not conn.execute("SELECT 1 FROM meta WHERE key='device_name'").fetchone():
        conn.execute("INSERT INTO meta VALUES ('device_name', ?)", (DEVICE_NAME or username,))


@contextmanager
def get_db(username: str):
    conn = _connect(username)
    try:
        if username not in _initialized:
            _init_user_db(conn, username)
            _initialized.add(username)
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate_legacy() -> None:
    """Move a pre-multi-user data/lego.sqlite (+ manuals) into the admin user's directory."""
    if not USER_DB.exists():
        return
    target = user_dir(ADMIN_USER)
    target.mkdir(parents=True, exist_ok=True)
    if (target / "lego.sqlite").exists():
        USER_DB.rename(DATA_DIR / "lego.sqlite.preusers-backup")
        log.warning("db: trovato sia %s sia il DB di '%s'; il vecchio file e' stato rinominato lego.sqlite.preusers-backup",
                    USER_DB.name, ADMIN_USER)
    else:
        for suffix in ("", "-wal", "-shm"):
            f = DATA_DIR / f"lego.sqlite{suffix}"
            if f.exists():
                f.rename(target / f.name)
        log.info("db: inventario esistente migrato in users/%s/", ADMIN_USER)
    mdir = manuals_dir(ADMIN_USER)
    if MANUALS_DIR.is_dir():
        moved = 0
        for pdf in MANUALS_DIR.glob("*.pdf"):
            if not (mdir / pdf.name).exists():
                pdf.rename(mdir / pdf.name)
                moved += 1
        if moved:
            log.info("db: %d manuali migrati in users/%s/manuals/", moved, ADMIN_USER)


def catalog_ready(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT value FROM cat.catalog_meta WHERE key='imported_at'").fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def device_id(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]


def device_name(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='device_name'").fetchone()
    return row[0] if row else device_id(conn)


def rebrickable_key(conn: sqlite3.Connection) -> str:
    """Per-user Rebrickable API key (meta table), falling back to the env var."""
    row = conn.execute("SELECT value FROM meta WHERE key='rebrickable_api_key'").fetchone()
    return (row[0] if row and row[0] else "") or REBRICKABLE_API_KEY


def set_rebrickable_key(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO meta VALUES ('rebrickable_api_key', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key,),
    )


def set_quantity(conn: sqlite3.Connection, part_num: str, color_id: int, total: int) -> None:
    """Sets the total quantity of a piece by adjusting this device's contribution."""
    dev = device_id(conn)
    others = conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE part_num=? AND color_id=? AND device_id<>?",
        (part_num, color_id, dev),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO inventory (part_num,color_id,device_id,quantity,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(part_num,color_id,device_id) DO UPDATE SET quantity=excluded.quantity, updated_at=excluded.updated_at",
        (part_num, color_id, dev, max(total, 0) - others, now_ms()),
    )


def get_quantity(conn: sqlite3.Connection, part_num: str, color_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE part_num=? AND color_id=?", (part_num, color_id)
    ).fetchone()[0]
