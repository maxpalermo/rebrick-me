"""Imports the public Rebrickable CSV catalog into a local SQLite database."""
import csv
import gzip
import io
import logging
import os
import sqlite3
import threading
import time

import httpx

from .config import CATALOG_DB, REBRICKABLE_DOWNLOADS

log = logging.getLogger("catalog")

FILES = ["colors", "themes", "part_categories", "parts", "sets", "inventories", "inventory_parts", "elements"]

SCHEMA = """
CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE colors (id INTEGER PRIMARY KEY, name TEXT, rgb TEXT, is_trans INTEGER, bl_id INTEGER);
CREATE TABLE themes (id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER);
CREATE TABLE part_categories (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE parts (part_num TEXT PRIMARY KEY, name TEXT, part_cat_id INTEGER);
CREATE TABLE sets (set_num TEXT PRIMARY KEY, name TEXT, year INTEGER, theme_id INTEGER,
                   num_parts INTEGER, img_url TEXT, total_qty INTEGER DEFAULT 0);
CREATE TABLE inventories (id INTEGER PRIMARY KEY, version INTEGER, set_num TEXT);
CREATE TABLE set_parts (set_num TEXT, part_num TEXT, color_id INTEGER, quantity INTEGER,
                        PRIMARY KEY (set_num, part_num, color_id)) WITHOUT ROWID;
CREATE TABLE part_images (part_num TEXT, color_id INTEGER, img_url TEXT,
                          PRIMARY KEY (part_num, color_id)) WITHOUT ROWID;
CREATE TABLE part_alias (alias TEXT PRIMARY KEY, part_num TEXT);
CREATE TABLE elements (part_num TEXT, color_id INTEGER, element_id TEXT,
                       PRIMARY KEY (part_num, color_id)) WITHOUT ROWID;
"""

status = {"state": "idle", "message": "", "progress": 0.0, "error": None}
_lock = threading.Lock()


def _set(state=None, message=None, progress=None, error=None):
    if state is not None:
        status["state"] = state
    if message is not None:
        status["message"] = message
    if progress is not None:
        status["progress"] = progress
    status["error"] = error
    log.info("catalog: %s %s", status["state"], status["message"])


def _rows(raw: bytes):
    text = io.TextIOWrapper(io.BytesIO(gzip.decompress(raw)), encoding="utf-8", newline="")
    reader = csv.reader(text)
    next(reader)
    yield from reader


def _bool(v: str) -> int:
    return 1 if v in ("True", "t", "true", "1") else 0


def _int(v: str):
    return int(v) if v not in ("", None) else None


def _fetch_bricklink_color_ids(client: httpx.Client, key: str) -> dict[int, int]:
    if not key:
        return {}
    try:
        r = client.get(
            "https://rebrickable.com/api/v3/lego/colors/",
            params={"page_size": 1000},
            headers={"Authorization": f"key {key}"},
        )
        r.raise_for_status()
        mapping = {}
        for c in r.json()["results"]:
            bl = (c.get("external_ids") or {}).get("BrickLink") or {}
            ids = bl.get("ext_ids") or []
            if ids:
                mapping[c["id"]] = ids[0]
        return mapping
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch BrickLink color ids: %s", exc)
        return {}


def import_catalog(api_key: str = "") -> None:
    if not _lock.acquire(blocking=False):
        return
    tmp = CATALOG_DB.with_name(f"catalog.{os.getpid()}.tmp")
    try:
        _set("running", "Download catalogo Rebrickable...", 0.0)
        raw = {}
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            for i, name in enumerate(FILES):
                _set(message=f"Download {name}.csv.gz", progress=i / len(FILES) * 0.5)
                r = client.get(f"{REBRICKABLE_DOWNLOADS}/{name}.csv.gz")
                r.raise_for_status()
                raw[name] = r.content
            bl_colors = _fetch_bricklink_color_ids(client, api_key)

        if tmp.exists():
            tmp.unlink()
        conn = sqlite3.connect(tmp)
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

        _set(message="Importazione colori, temi, pezzi, set", progress=0.55)
        conn.executemany(
            "INSERT INTO colors VALUES (?,?,?,?,?)",
            ((int(r[0]), r[1], r[2], _bool(r[3]), bl_colors.get(int(r[0]))) for r in _rows(raw["colors"])),
        )
        conn.executemany("INSERT INTO themes VALUES (?,?,?)",
                         ((int(r[0]), r[1], _int(r[2])) for r in _rows(raw["themes"])))
        conn.executemany("INSERT INTO part_categories VALUES (?,?)",
                         ((int(r[0]), r[1]) for r in _rows(raw["part_categories"])))
        conn.executemany("INSERT OR IGNORE INTO parts VALUES (?,?,?)",
                         ((r[0], r[1], _int(r[2])) for r in _rows(raw["parts"])))
        conn.executemany("INSERT OR IGNORE INTO sets (set_num,name,year,theme_id,num_parts,img_url) VALUES (?,?,?,?,?,?)",
                         ((r[0], r[1], _int(r[2]), _int(r[3]), _int(r[4]), r[5]) for r in _rows(raw["sets"])))
        conn.executemany("INSERT INTO inventories VALUES (?,?,?)",
                         ((int(r[0]), int(r[1]), r[2]) for r in _rows(raw["inventories"])))

        _set(message="Importazione inventari dei set", progress=0.7)
        conn.execute("CREATE TEMP TABLE ip (inventory_id INTEGER, part_num TEXT, color_id INTEGER, quantity INTEGER, is_spare INTEGER, img_url TEXT)")
        conn.executemany(
            "INSERT INTO ip VALUES (?,?,?,?,?,?)",
            ((int(r[0]), r[1], int(r[2]), int(r[3]), _bool(r[4]), r[5] if len(r) > 5 else "") for r in _rows(raw["inventory_parts"])),
        )
        _set(message="Calcolo elenco pezzi per set", progress=0.85)
        conn.executescript(
            """
            CREATE TEMP TABLE latest AS
              SELECT i.id, i.set_num FROM inventories i
              JOIN (SELECT set_num, MAX(version) v FROM inventories GROUP BY set_num) m
                ON m.set_num = i.set_num AND m.v = i.version;
            INSERT INTO set_parts
              SELECT l.set_num, ip.part_num, ip.color_id, SUM(ip.quantity)
              FROM ip JOIN latest l ON l.id = ip.inventory_id
              WHERE ip.is_spare = 0
              GROUP BY l.set_num, ip.part_num, ip.color_id;
            INSERT OR IGNORE INTO part_images
              SELECT part_num, color_id, img_url FROM ip WHERE img_url <> '';
            CREATE INDEX set_parts_part ON set_parts(part_num, color_id);
            UPDATE sets SET total_qty = COALESCE((SELECT SUM(quantity) FROM set_parts sp WHERE sp.set_num = sets.set_num), 0);
            CREATE INDEX colors_name ON colors(name COLLATE NOCASE);
            CREATE INDEX parts_name ON parts(name COLLATE NOCASE);
            DROP TABLE ip;
            """
        )
        _set(message="Importazione element ID LEGO", progress=0.92)
        conn.execute("CREATE TEMP TABLE el (element_id TEXT, part_num TEXT, color_id INTEGER)")
        conn.executemany(
            "INSERT INTO el VALUES (?,?,?)",
            ((r[0], r[1], _int(r[2])) for r in _rows(raw["elements"])),
        )
        conn.execute("INSERT INTO elements SELECT part_num, color_id, MAX(element_id) FROM el GROUP BY part_num, color_id")
        conn.execute("DROP TABLE el")
        conn.execute("INSERT INTO catalog_meta VALUES ('imported_at', ?)", (str(int(time.time())),))
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        os.replace(tmp, CATALOG_DB)
        _set("ready", "Catalogo pronto", 1.0)
    except Exception as exc:  # noqa: BLE001
        log.exception("Catalog import failed")
        _set("error", "Importazione fallita", error=str(exc))
        if tmp.exists():
            tmp.unlink()
    finally:
        _lock.release()


def start_import_thread(api_key: str = "") -> None:
    threading.Thread(target=import_catalog, args=(api_key,), daemon=True).start()
