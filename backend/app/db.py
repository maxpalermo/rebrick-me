import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import CATALOG_DB, DEVICE_NAME, USER_DB

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
    last_sync INTEGER
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USER_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if CATALOG_DB.exists():
        conn.execute("ATTACH DATABASE ? AS cat", (str(CATALOG_DB),))
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def catalog_ready(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT value FROM cat.catalog_meta WHERE key='imported_at'").fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(USER_SCHEMA)
        if not conn.execute("SELECT 1 FROM meta WHERE key='device_id'").fetchone():
            conn.execute("INSERT INTO meta VALUES ('device_id', ?)", (uuid.uuid4().hex[:12],))
        if DEVICE_NAME:
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('device_name', ?)", (DEVICE_NAME,))


def device_id(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]


def device_name(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='device_name'").fetchone()
    return row[0] if row else device_id(conn)


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
