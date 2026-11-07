"""Device synchronisation: conflict-free merge of inventory, saved projects and manuals."""
import io
import json
import sqlite3
import zipfile

import httpx

from .config import MANUALS_DIR
from .db import device_id, device_name, now_ms

MANUAL_COLS = ["id", "set_num", "title", "filename", "source_url", "sha256", "size", "deleted", "updated_at", "device_id"]


def export_state(conn: sqlite3.Connection) -> dict:
    return {
        "format": "brick-scanner-sync/1",
        "device_id": device_id(conn),
        "device_name": device_name(conn),
        "exported_at": now_ms(),
        "inventory": [dict(r) for r in conn.execute(
            "SELECT part_num,color_id,device_id,quantity,updated_at FROM inventory")],
        "saved_projects": [dict(r) for r in conn.execute(
            "SELECT set_num,note,deleted,updated_at,device_id FROM saved_projects")],
        "manuals": [dict(r) for r in conn.execute(f"SELECT {','.join(MANUAL_COLS)} FROM manuals")],
    }


def import_state(conn: sqlite3.Connection, data: dict) -> dict:
    if data.get("format") != "brick-scanner-sync/1":
        raise ValueError("Formato di sincronizzazione non riconosciuto")
    stats = {"inventory": 0, "saved_projects": 0, "manuals": 0}
    for r in data.get("inventory", []):
        cur = conn.execute(
            "INSERT INTO inventory (part_num,color_id,device_id,quantity,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(part_num,color_id,device_id) DO UPDATE SET quantity=excluded.quantity, "
            "updated_at=excluded.updated_at WHERE excluded.updated_at > inventory.updated_at",
            (r["part_num"], int(r["color_id"]), r["device_id"], int(r["quantity"]), int(r["updated_at"])),
        )
        stats["inventory"] += cur.rowcount
    for r in data.get("saved_projects", []):
        cur = conn.execute(
            "INSERT INTO saved_projects (set_num,note,deleted,updated_at,device_id) VALUES (?,?,?,?,?) "
            "ON CONFLICT(set_num) DO UPDATE SET note=excluded.note, deleted=excluded.deleted, "
            "updated_at=excluded.updated_at, device_id=excluded.device_id "
            "WHERE excluded.updated_at > saved_projects.updated_at",
            (r["set_num"], r.get("note"), int(r.get("deleted", 0)), int(r["updated_at"]), r["device_id"]),
        )
        stats["saved_projects"] += cur.rowcount
    for r in data.get("manuals", []):
        cur = conn.execute(
            f"INSERT INTO manuals ({','.join(MANUAL_COLS)}) VALUES ({','.join('?' * len(MANUAL_COLS))}) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, deleted=excluded.deleted, "
            "updated_at=excluded.updated_at, device_id=excluded.device_id "
            "WHERE excluded.updated_at > manuals.updated_at",
            tuple(r.get(c) for c in MANUAL_COLS),
        )
        stats["manuals"] += cur.rowcount
    return {"merged": stats, "missing_files": missing_files(conn)}


def missing_files(conn: sqlite3.Connection) -> list[str]:
    shas = {r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM manuals WHERE deleted=0 AND sha256 IS NOT NULL")}
    return sorted(s for s in shas if not (MANUALS_DIR / f"{s}.pdf").exists())


def save_file(sha: str, data: bytes) -> None:
    import hashlib
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("Checksum del file non valido")
    (MANUALS_DIR / f"{sha}.pdf").write_bytes(data)


def build_backup(conn: sqlite3.Connection) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        state = export_state(conn)
        z.writestr("data.json", json.dumps(state))
        for sha in {m["sha256"] for m in state["manuals"] if m["sha256"] and not m["deleted"]}:
            path = MANUALS_DIR / f"{sha}.pdf"
            if path.exists():
                z.write(path, f"manuals/{sha}.pdf")
    return buf.getvalue()


def restore_backup(conn: sqlite3.Connection, raw: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if name.startswith("manuals/") and name.endswith(".pdf"):
                sha = name.split("/")[-1][:-4]
                if not (MANUALS_DIR / f"{sha}.pdf").exists():
                    save_file(sha, z.read(name))
        return import_state(conn, json.loads(z.read("data.json")))


async def sync_with_peer(conn: sqlite3.Connection, peer_url: str) -> dict:
    base = peer_url.rstrip("/")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.get(f"{base}/api/sync/export")
        r.raise_for_status()
        pulled = import_state(conn, r.json())
        for sha in pulled["missing_files"]:
            f = await client.get(f"{base}/api/sync/files/{sha}")
            if f.status_code == 200:
                save_file(sha, f.content)
        conn.commit()

        r = await client.post(f"{base}/api/sync/import", json=export_state(conn))
        r.raise_for_status()
        pushed = r.json()
        for sha in pushed.get("missing_files", []):
            path = MANUALS_DIR / f"{sha}.pdf"
            if path.exists():
                await client.put(f"{base}/api/sync/files/{sha}", content=path.read_bytes(),
                                 headers={"Content-Type": "application/pdf"})
    conn.execute("INSERT INTO peers (url,last_sync) VALUES (?,?) ON CONFLICT(url) DO UPDATE SET last_sync=excluded.last_sync",
                 (base, now_ms()))
    return {"pulled": pulled["merged"], "pushed": pushed.get("merged", {})}
