import logging
import sqlite3

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import catalog, manuals, scan, sync
from .config import AUTO_IMPORT_CATALOG, MANUALS_DIR, REBRICKABLE_API_KEY, STATIC_DIR
from .db import catalog_ready, device_id, device_name, get_db, get_quantity, init_db, now_ms, set_quantity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Brick Scanner", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    init_db()
    with get_db() as conn:
        ready = catalog_ready(conn)
    if ready:
        catalog.status.update(state="ready", message="Catalogo pronto", progress=1.0)
    elif AUTO_IMPORT_CATALOG:
        catalog.start_import_thread()


def require_catalog(conn: sqlite3.Connection) -> None:
    if not catalog_ready(conn):
        raise HTTPException(503, "Catalogo non ancora importato. Vai in Impostazioni.")


# ---------- status ----------
@app.get("/api/status")
def status():
    with get_db() as conn:
        info = {
            "device_id": device_id(conn),
            "device_name": device_name(conn),
            "rebrickable_key": bool(REBRICKABLE_API_KEY),
            "catalog": dict(catalog.status),
            "inventory": {"distinct": 0, "total": 0},
        }
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM inventory_total").fetchone()
        info["inventory"] = {"distinct": row[0], "total": row[1]}
        if catalog_ready(conn):
            info["catalog"]["sets"] = conn.execute("SELECT COUNT(*) FROM cat.sets").fetchone()[0]
            info["catalog"]["imported_at"] = int(conn.execute(
                "SELECT value FROM cat.catalog_meta WHERE key='imported_at'").fetchone()[0])
    return info


@app.post("/api/catalog/import")
def catalog_import():
    if catalog.status["state"] == "running":
        return {"started": False}
    catalog.start_import_thread()
    return {"started": True}


@app.get("/api/colors")
def colors():
    with get_db() as conn:
        require_catalog(conn)
        rows = conn.execute("SELECT id,name,rgb,is_trans FROM cat.colors WHERE id>=0 ORDER BY name").fetchall()
        return [dict(r) for r in rows]


@app.get("/api/parts/search")
def parts_search(q: str = Query(..., min_length=1), limit: int = 30):
    with get_db() as conn:
        require_catalog(conn)
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT p.part_num, p.name, (SELECT img_url FROM cat.part_images i WHERE i.part_num=p.part_num LIMIT 1) img_url "
            "FROM cat.parts p WHERE p.part_num = ? OR p.name LIKE ? "
            "ORDER BY (p.part_num = ?) DESC, length(p.name) LIMIT ?",
            (q, like, q, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- scan ----------
@app.post("/api/scan")
async def scan_photo(image: UploadFile = File(...), segment: bool = Form(True)):
    data = await image.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Immagine troppo grande (max 25 MB)")
    with get_db() as conn:
        require_catalog(conn)
        try:
            return await scan.scan_image(conn, data, segment=segment)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


# ---------- inventory ----------
INV_SELECT = """
SELECT t.part_num, t.color_id, t.quantity, t.updated_at,
       p.name AS part_name, c.name AS color_name, c.rgb, c.is_trans,
       COALESCE(pi.img_url, (SELECT img_url FROM cat.part_images x WHERE x.part_num=t.part_num LIMIT 1)) AS img_url
FROM inventory_total t
LEFT JOIN cat.parts p ON p.part_num = t.part_num
LEFT JOIN cat.colors c ON c.id = t.color_id
LEFT JOIN cat.part_images pi ON pi.part_num = t.part_num AND pi.color_id = t.color_id
"""


@app.get("/api/inventory")
def inventory_list():
    with get_db() as conn:
        require_catalog(conn)
        return [dict(r) for r in conn.execute(INV_SELECT + " ORDER BY p.name, c.name")]


@app.post("/api/inventory/add")
def inventory_add(items: list[dict] = Body(...)):
    """items: [{part_num, color_id, quantity}] -> quantities are added to the current totals."""
    with get_db() as conn:
        require_catalog(conn)
        for it in items:
            part, color, qty = str(it["part_num"]), int(it["color_id"]), int(it.get("quantity", 1))
            if not conn.execute("SELECT 1 FROM cat.parts WHERE part_num=?", (part,)).fetchone():
                raise HTTPException(400, f"Pezzo sconosciuto: {part}")
            set_quantity(conn, part, color, get_quantity(conn, part, color) + qty)
    return {"ok": True, "added": len(items)}


@app.put("/api/inventory/{part_num}/{color_id}")
def inventory_set(part_num: str, color_id: int, quantity: int = Body(..., embed=True)):
    with get_db() as conn:
        set_quantity(conn, part_num, color_id, quantity)
    return {"ok": True}


@app.get("/api/inventory/export.csv", response_class=PlainTextResponse)
def inventory_csv():
    with get_db() as conn:
        rows = conn.execute("SELECT part_num, color_id, quantity FROM inventory_total ORDER BY part_num").fetchall()
    lines = ["Part,Color,Quantity"] + [f"{r[0]},{r[1]},{r[2]}" for r in rows]
    return PlainTextResponse("\n".join(lines) + "\n", headers={"Content-Disposition": "attachment; filename=inventario.csv"})


# ---------- projects (buildable sets) ----------
@app.get("/api/projects")
def projects(
    ignore_color: bool = False,
    min_parts: int = 10,
    max_parts: int = 5000,
    min_pct: float = 0,
    q: str = "",
    limit: int = 60,
):
    with get_db() as conn:
        require_catalog(conn)
        if ignore_color:
            have_sql = """
              WITH inv AS (SELECT part_num, SUM(quantity) q FROM inventory_total GROUP BY part_num),
              sp AS (SELECT set_num, part_num, SUM(quantity) q FROM cat.set_parts
                     WHERE part_num IN (SELECT part_num FROM inv) GROUP BY set_num, part_num)
              SELECT set_num, SUM(MIN(inv.q, sp.q)) have, COUNT(*) kinds FROM sp JOIN inv USING(part_num) GROUP BY set_num"""
        else:
            have_sql = """
              SELECT sp.set_num, SUM(MIN(t.quantity, sp.quantity)) have, COUNT(*) kinds
              FROM inventory_total t JOIN cat.set_parts sp ON sp.part_num=t.part_num AND sp.color_id=t.color_id
              GROUP BY sp.set_num"""
        sql = f"""
            WITH h AS ({have_sql})
            SELECT s.set_num, s.name, s.year, s.img_url, s.total_qty, th.name theme, h.have,
                   ROUND(100.0 * h.have / s.total_qty, 1) pct,
                   EXISTS(SELECT 1 FROM manuals m WHERE m.set_num=s.set_num AND m.deleted=0) has_manual,
                   EXISTS(SELECT 1 FROM saved_projects sp WHERE sp.set_num=s.set_num AND sp.deleted=0) saved
            FROM h JOIN cat.sets s ON s.set_num=h.set_num
            LEFT JOIN cat.themes th ON th.id=s.theme_id
            WHERE s.total_qty BETWEEN ? AND ? AND 100.0 * h.have / s.total_qty >= ?
              AND (? = '' OR s.name LIKE ? OR s.set_num LIKE ? OR th.name LIKE ?)
            ORDER BY pct DESC, h.have DESC LIMIT ?"""
        like = f"%{q}%"
        rows = conn.execute(sql, (min_parts, max_parts, min_pct, q, like, like, like, limit)).fetchall()
        return [dict(r) for r in rows]


def _set_row(conn, set_num):
    row = conn.execute(
        "SELECT s.*, th.name theme FROM cat.sets s LEFT JOIN cat.themes th ON th.id=s.theme_id WHERE s.set_num=?",
        (set_num,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Set non trovato")
    return row


@app.get("/api/sets/{set_num}")
def set_detail(set_num: str, ignore_color: bool = False):
    with get_db() as conn:
        require_catalog(conn)
        s = dict(_set_row(conn, set_num))
        parts = conn.execute(
            """SELECT sp.part_num, sp.color_id, sp.quantity need, p.name part_name, c.name color_name, c.rgb,
                      COALESCE(pi.img_url, (SELECT img_url FROM cat.part_images x WHERE x.part_num=sp.part_num LIMIT 1)) img_url,
                      COALESCE((SELECT quantity FROM inventory_total t WHERE t.part_num=sp.part_num AND t.color_id=sp.color_id),0) have_exact,
                      COALESCE((SELECT SUM(quantity) FROM inventory_total t WHERE t.part_num=sp.part_num),0) have_any
               FROM cat.set_parts sp
               LEFT JOIN cat.parts p ON p.part_num=sp.part_num
               LEFT JOIN cat.colors c ON c.id=sp.color_id
               LEFT JOIN cat.part_images pi ON pi.part_num=sp.part_num AND pi.color_id=sp.color_id
               WHERE sp.set_num=? ORDER BY p.name, c.name""",
            (set_num,),
        ).fetchall()
        out, have_total = [], 0
        for r in parts:
            d = dict(r)
            have = d["have_any"] if ignore_color else d["have_exact"]
            d["have"] = min(have, d["need"])
            d["missing"] = d["need"] - d["have"]
            have_total += d["have"]
            out.append(d)
        s["parts"] = out
        s["have"] = have_total
        s["pct"] = round(100 * have_total / s["total_qty"], 1) if s["total_qty"] else 0
        s["manuals"] = [dict(m) for m in conn.execute(
            "SELECT id,title,size,source_url FROM manuals WHERE set_num=? AND deleted=0", (set_num,))]
        s["saved"] = bool(conn.execute(
            "SELECT 1 FROM saved_projects WHERE set_num=? AND deleted=0", (set_num,)).fetchone())
        s["lego_instructions_url"] = manuals.lego_instructions_page(set_num)
        s["rebrickable_url"] = f"https://rebrickable.com/sets/{set_num}/"
        return s


@app.get("/api/sets/{set_num}/missing.csv", response_class=PlainTextResponse)
def set_missing_csv(set_num: str, ignore_color: bool = False):
    d = set_detail(set_num, ignore_color)
    lines = ["Part,Color,Quantity"] + [f"{p['part_num']},{p['color_id']},{p['missing']}" for p in d["parts"] if p["missing"] > 0]
    return PlainTextResponse("\n".join(lines) + "\n",
                             headers={"Content-Disposition": f"attachment; filename=mancanti-{set_num}.csv"})


@app.get("/api/sets/{set_num}/alternates")
async def set_alternates(set_num: str):
    """Alternative builds (MOCs) from Rebrickable. Requires REBRICKABLE_API_KEY."""
    if not REBRICKABLE_API_KEY:
        return {"available": False, "results": []}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"https://rebrickable.com/api/v3/lego/sets/{set_num}/alternates/",
                             headers={"Authorization": f"key {REBRICKABLE_API_KEY}"}, params={"page_size": 50})
        if r.status_code == 404:
            return {"available": True, "results": []}
        if r.status_code in (401, 403):
            raise HTTPException(502, "Chiave Rebrickable non valida")
        r.raise_for_status()
        return {"available": True, "results": [
            {k: m.get(k) for k in ("set_num", "name", "year", "num_parts", "moc_img_url", "moc_url", "designer_name")}
            for m in r.json().get("results", [])
        ]}


@app.post("/api/saved/{set_num}")
def save_project(set_num: str, saved: bool = Body(True, embed=True)):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO saved_projects (set_num,note,deleted,updated_at,device_id) VALUES (?,NULL,?,?,?) "
            "ON CONFLICT(set_num) DO UPDATE SET deleted=excluded.deleted, updated_at=excluded.updated_at, device_id=excluded.device_id",
            (set_num, 0 if saved else 1, now_ms(), device_id(conn)),
        )
    return {"ok": True}


@app.get("/api/saved")
def saved_projects():
    with get_db() as conn:
        require_catalog(conn)
        rows = conn.execute(
            """SELECT s.set_num, s.name, s.year, s.img_url, s.total_qty, th.name theme,
                      (SELECT COUNT(*) FROM manuals m WHERE m.set_num=s.set_num AND m.deleted=0) manuals
               FROM saved_projects sp JOIN cat.sets s ON s.set_num=sp.set_num
               LEFT JOIN cat.themes th ON th.id=s.theme_id
               WHERE sp.deleted=0 ORDER BY sp.updated_at DESC""").fetchall()
        return [dict(r) for r in rows]


# ---------- manuals ----------
@app.get("/api/manuals")
def manuals_list():
    with get_db() as conn:
        ready = catalog_ready(conn)
        name_sql = "(SELECT name FROM cat.sets s WHERE s.set_num=m.set_num)" if ready else "NULL"
        img_sql = "(SELECT img_url FROM cat.sets s WHERE s.set_num=m.set_num)" if ready else "NULL"
        rows = conn.execute(
            f"SELECT m.id, m.set_num, m.title, m.size, m.sha256, m.source_url, {name_sql} set_name, {img_sql} img_url "
            "FROM manuals m WHERE m.deleted=0 ORDER BY m.set_num, m.title").fetchall()
        return [dict(r) | {"available": (MANUALS_DIR / f"{r['sha256']}.pdf").exists()} for r in rows]


@app.post("/api/manuals/fetch/{set_num}")
async def manuals_fetch(set_num: str):
    with get_db() as conn:
        require_catalog(conn)
        s = _set_row(conn, set_num)
        try:
            stored = await manuals.fetch_from_lego(conn, set_num, s["name"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"{exc}. Scarica il PDF da {manuals.lego_instructions_page(set_num)} "
                                     "e caricalo manualmente.") from exc
        conn.execute("INSERT INTO saved_projects (set_num,deleted,updated_at,device_id) VALUES (?,0,?,?) "
                     "ON CONFLICT(set_num) DO UPDATE SET deleted=0, updated_at=excluded.updated_at",
                     (set_num, now_ms(), device_id(conn)))
        return {"stored": stored}


@app.post("/api/manuals/upload")
async def manuals_upload(set_num: str = Form(...), title: str = Form(""),
                         file: UploadFile | None = File(None), url: str = Form("")):
    if file is not None:
        data, source = await file.read(), None
    elif url:
        try:
            data, source = await manuals.download_pdf(url), url
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Download non riuscito: {exc}") from exc
    else:
        raise HTTPException(400, "Carica un file PDF o indica un URL")
    with get_db() as conn:
        name = title
        if not name and catalog_ready(conn):
            row = conn.execute("SELECT name FROM cat.sets WHERE set_num=?", (set_num,)).fetchone()
            name = row[0] if row else ""
        try:
            return manuals.store_pdf(conn, set_num, name or set_num, data, source)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/manuals/{manual_id}/file")
def manuals_file(manual_id: str):
    with get_db() as conn:
        row = conn.execute("SELECT sha256, set_num, title FROM manuals WHERE id=? AND deleted=0", (manual_id,)).fetchone()
    if not row or not (MANUALS_DIR / f"{row[0]}.pdf").exists():
        raise HTTPException(404, "Manuale non disponibile su questo dispositivo")
    return FileResponse(MANUALS_DIR / f"{row[0]}.pdf", media_type="application/pdf",
                        filename=f"{row[1]}.pdf", content_disposition_type="inline")


@app.delete("/api/manuals/{manual_id}")
def manuals_delete(manual_id: str):
    with get_db() as conn:
        conn.execute("UPDATE manuals SET deleted=1, updated_at=?, device_id=? WHERE id=?",
                     (now_ms(), device_id(conn), manual_id))
    return {"ok": True}


# ---------- sync ----------
@app.get("/api/sync/export")
def sync_export():
    with get_db() as conn:
        return sync.export_state(conn)


@app.post("/api/sync/import")
def sync_import(data: dict = Body(...)):
    with get_db() as conn:
        try:
            return sync.import_state(conn, data)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"Dati non validi: {exc}") from exc


@app.get("/api/sync/files/{sha}")
def sync_file_get(sha: str):
    path = MANUALS_DIR / f"{sha}.pdf"
    if not sha.isalnum() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf")


@app.put("/api/sync/files/{sha}")
async def sync_file_put(sha: str, request: Request):
    if not sha.isalnum():
        raise HTTPException(400)
    try:
        sync.save_file(sha, await request.body())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/sync/peer")
async def sync_peer(url: str = Body(..., embed=True)):
    with get_db() as conn:
        if url.rstrip("/").endswith(device_id(conn)):
            raise HTTPException(400, "URL non valido")
        try:
            return await sync.sync_with_peer(conn, url)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Impossibile contattare {url}: {exc}") from exc


@app.get("/api/sync/peers")
def sync_peers():
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT url, last_sync FROM peers ORDER BY last_sync DESC")]


@app.get("/api/sync/backup")
def sync_backup():
    with get_db() as conn:
        data = sync.build_backup(conn)
        name = device_name(conn)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=brick-scanner-{name}.zip"})


@app.post("/api/sync/restore")
async def sync_restore(file: UploadFile = File(...)):
    raw = await file.read()
    with get_db() as conn:
        try:
            return sync.restore_backup(conn, raw)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Backup non valido: {exc}") from exc


@app.post("/api/settings/device-name")
def set_device_name(name: str = Body(..., embed=True)):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('device_name', ?)", (name.strip()[:60],))
    return {"ok": True}


# ---------- frontend ----------
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets") if (STATIC_DIR / "assets").exists() else None

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = STATIC_DIR / path
        if path and f.is_file() and STATIC_DIR in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(STATIC_DIR / "index.html")
