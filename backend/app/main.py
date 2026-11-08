import json
import logging
import sqlite3
import uuid

import httpx
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import admin_db, catalog, manuals, scan, sync
from .config import (
    ADMIN_USER, AUTO_IMPORT_CATALOG, CATALOG_DB, REBRICKABLE_API_KEY, SESSION_COOKIE, STATIC_DIR,
)
from .db import (
    catalog_ready, device_id, device_name, empty_bin, get_db, get_quantity, manuals_dir, migrate_legacy,
    move_quantity, now_ms, rebrickable_key, set_quantity, set_rebrickable_key, user_dir, valid_bin,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Brick Scanner", version="0.2.0")


@app.on_event("startup")
def startup() -> None:
    admin_db.init_admin_db()
    migrate_legacy()
    site_key = admin_db.get_setting("rebrickable_api_key")
    if site_key:  # pre-per-user-key installs: hand the site key to the admin who configured it
        with get_db(ADMIN_USER) as conn:
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('rebrickable_api_key', ?)", (site_key,))
        admin_db.set_setting("rebrickable_api_key", "")
        log = logging.getLogger(__name__)
        log.info("chiave Rebrickable migrata nel profilo di '%s'", ADMIN_USER)
    ready = False
    if CATALOG_DB.exists():
        conn = sqlite3.connect(CATALOG_DB)
        try:
            ready = conn.execute("SELECT 1 FROM catalog_meta WHERE key='imported_at'").fetchone() is not None
        except sqlite3.Error:
            ready = False
        conn.close()
    if ready:
        catalog.status.update(state="ready", message="Catalogo pronto", progress=1.0)
    elif AUTO_IMPORT_CATALOG:
        catalog.start_import_thread(REBRICKABLE_API_KEY)


# ---------- auth ----------
def current_user(request: Request) -> dict:
    user = admin_db.user_from_session(request.cookies.get(SESSION_COOKIE))
    if not user:
        user = admin_db.user_from_sync_token(request.headers.get("x-sync-token"))
    if not user:
        raise HTTPException(401, "Login richiesto")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Solo l'amministratore")
    return user


@app.post("/api/login")
def login(request: Request, response: Response, data: dict = Body(...)):
    token = admin_db.authenticate(data.get("username", ""), data.get("password", ""))
    if not token:
        raise HTTPException(401, "Utente o password errati")
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=secure, max_age=30 * 86400)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    admin_db.destroy_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


# ---------- admin ----------
@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(require_admin)):
    out = []
    for u in admin_db.list_users():
        stats = {"pieces": 0, "manuals": 0}
        if (user_dir(u["username"]) / "lego.sqlite").exists():
            with get_db(u["username"]) as conn:
                stats["pieces"] = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory_total").fetchone()[0]
                stats["manuals"] = conn.execute("SELECT COUNT(*) FROM manuals WHERE deleted=0").fetchone()[0]
        out.append(u | stats)
    return out


@app.post("/api/admin/users")
def admin_create_user(data: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        admin_db.create_user(data.get("username", ""), data.get("password", ""), bool(data.get("is_admin")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.delete("/api/admin/users/{username}")
def admin_delete_user(username: str, admin: dict = Depends(require_admin)):
    try:
        admin_db.delete_user(username, admin["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/admin/users/{username}/password")
def admin_reset_password(username: str, data: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        admin_db.set_password(username, data.get("password", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


# ---------- helpers ----------
def require_catalog(conn: sqlite3.Connection) -> None:
    if not catalog_ready(conn):
        raise HTTPException(503, "Catalogo non ancora importato. Vai in Impostazioni.")


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- status ----------
@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        info = {
            "device_id": device_id(conn),
            "device_name": device_name(conn),
            "rebrickable_key": bool(rebrickable_key(conn)),
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
def catalog_import(user: dict = Depends(current_user)):
    if catalog.status["state"] == "running":
        return {"started": False}
    with get_db(user["username"]) as conn:
        key = rebrickable_key(conn)
    catalog.start_import_thread(key)
    return {"started": True}


@app.get("/api/colors")
def colors(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        rows = conn.execute("SELECT id,name,rgb,is_trans FROM cat.colors WHERE id>=0 ORDER BY name").fetchall()
        return [dict(r) for r in rows]


@app.get("/api/parts/search")
def parts_search(q: str = Query(..., min_length=1), limit: int = 30, user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
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
async def scan_photo(image: UploadFile = File(...), segment: bool = Form(True), user: dict = Depends(current_user)):
    data = await image.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Immagine troppo grande (max 25 MB)")
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        try:
            return await scan.scan_image(conn, data, segment=segment, api_key=rebrickable_key(conn))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/scan/build")
async def scan_build(image: UploadFile = File(...), user: dict = Depends(current_user)):
    """Recognizes a complete assembled LEGO build/set (and minifigures) from a photo."""
    data = await image.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "Immagine troppo grande (max 25 MB)")
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        try:
            return await scan.scan_build_image(conn, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


# ---------- bins (librerie) ----------
@app.get("/api/bins")
def bins_list(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        rows = conn.execute(
            "SELECT b.id, b.name, COALESCE(t.pieces,0) pieces, COALESCE(t.kinds,0) kinds FROM bins b "
            "LEFT JOIN (SELECT bin, SUM(quantity) pieces, COUNT(*) kinds FROM inventory_by_bin GROUP BY bin) t "
            "ON t.bin=b.id WHERE b.deleted=0 ORDER BY b.id='default' DESC, b.name"
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/bins")
def bin_create(data: dict = Body(...), user: dict = Depends(current_user)):
    name = (data.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "Nome mancante")
    bin_id = uuid.uuid4().hex[:10]
    with get_db(user["username"]) as conn:
        conn.execute("INSERT INTO bins VALUES (?,?,0,?,?)", (bin_id, name, now_ms(), device_id(conn)))
    return {"id": bin_id, "name": name}


@app.put("/api/bins/{bin_id}")
def bin_rename(bin_id: str, data: dict = Body(...), user: dict = Depends(current_user)):
    name = (data.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "Nome mancante")
    with get_db(user["username"]) as conn:
        cur = conn.execute(
            "UPDATE bins SET name=?, updated_at=?, device_id=? WHERE id=? AND deleted=0",
            (name, now_ms(), device_id(conn), bin_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Libreria non trovata")
    return {"ok": True}


@app.delete("/api/bins/{bin_id}")
def bin_delete(bin_id: str, to: str = "default", user: dict = Depends(current_user)):
    """Deletes a bin; its pieces are moved to `to` (default: the built-in bin)."""
    if bin_id == "default":
        raise HTTPException(400, "La libreria predefinita non si può eliminare")
    with get_db(user["username"]) as conn:
        if not valid_bin(conn, bin_id):
            raise HTTPException(404, "Libreria non trovata")
        if not valid_bin(conn, to):
            raise HTTPException(400, "Libreria di destinazione non valida")
        empty_bin(conn, bin_id, to)
        conn.execute(
            "UPDATE bins SET deleted=1, updated_at=?, device_id=? WHERE id=?",
            (now_ms(), device_id(conn), bin_id),
        )
    return {"ok": True}


# ---------- inventory ----------
INV_SELECT = """
SELECT t.part_num, t.color_id, t.quantity, t.updated_at,
       p.name AS part_name, c.name AS color_name, c.rgb, c.is_trans,
       COALESCE(pi.img_url, (SELECT img_url FROM cat.part_images x WHERE x.part_num=t.part_num LIMIT 1)) AS img_url,
       (SELECT json_group_array(json_object('bin', bb.bin, 'qty', bb.quantity)) FROM inventory_by_bin bb
        WHERE bb.part_num=t.part_num AND bb.color_id=t.color_id) AS bins
FROM inventory_total t
LEFT JOIN cat.parts p ON p.part_num = t.part_num
LEFT JOIN cat.colors c ON c.id = t.color_id
LEFT JOIN cat.part_images pi ON pi.part_num = t.part_num AND pi.color_id = t.color_id
"""


@app.get("/api/inventory")
def inventory_list(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        bin_names = {r[0]: r[1] for r in conn.execute("SELECT id,name FROM bins WHERE deleted=0")}
        out = []
        for r in conn.execute(INV_SELECT + " ORDER BY p.name, c.name"):
            d = dict(r)
            d["bins"] = [{"bin": b["bin"], "name": bin_names.get(b["bin"], b["bin"]), "qty": b["qty"]}
                         for b in json.loads(d["bins"] or "[]")]
            out.append(d)
        return out


@app.post("/api/inventory/add")
def inventory_add(items: list[dict] = Body(...), user: dict = Depends(current_user)):
    """items: [{part_num, color_id, quantity, bin?}] -> quantities are added to the bin totals."""
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        for it in items:
            part, color, qty = str(it["part_num"]), int(it["color_id"]), int(it.get("quantity", 1))
            bin_id = str(it.get("bin") or "default")
            if not conn.execute("SELECT 1 FROM cat.parts WHERE part_num=?", (part,)).fetchone():
                raise HTTPException(400, f"Pezzo sconosciuto: {part}")
            if not valid_bin(conn, bin_id):
                raise HTTPException(400, "Libreria non valida")
            set_quantity(conn, part, color, get_quantity(conn, part, color, bin_id) + qty, bin_id)
    return {"ok": True, "added": len(items)}


@app.put("/api/inventory/{part_num}/{color_id}")
def inventory_set(part_num: str, color_id: int, data: dict = Body(...), user: dict = Depends(current_user)):
    bin_id = str(data.get("bin") or "default")
    with get_db(user["username"]) as conn:
        if not valid_bin(conn, bin_id):
            raise HTTPException(400, "Libreria non valida")
        set_quantity(conn, part_num, color_id, int(data.get("quantity", 0)), bin_id)
    return {"ok": True}


@app.post("/api/inventory/move")
def inventory_move(data: dict = Body(...), user: dict = Depends(current_user)):
    """Moves pieces between bins: {part_num, color_id, from, to, quantity}."""
    part, color = str(data.get("part_num", "")), int(data.get("color_id", 0))
    from_bin, to_bin = str(data.get("from") or "default"), str(data.get("to") or "default")
    if from_bin == to_bin:
        raise HTTPException(400, "Libreria di origine e destinazione coincidono")
    with get_db(user["username"]) as conn:
        if not valid_bin(conn, from_bin) or not valid_bin(conn, to_bin):
            raise HTTPException(400, "Libreria non valida")
        qty = min(int(data.get("quantity") or 0), get_quantity(conn, part, color, from_bin))
        if qty <= 0:
            raise HTTPException(400, "Nessun pezzo da spostare in quella libreria")
        move_quantity(conn, part, color, from_bin, to_bin, qty)
    return {"ok": True, "moved": qty}


@app.get("/api/inventory/export.csv", response_class=PlainTextResponse)
def inventory_csv(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
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
    user: dict = Depends(current_user),
):
    with get_db(user["username"]) as conn:
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


def _set_detail(conn, set_num: str, ignore_color: bool):
    s = dict(_set_row(conn, set_num))
    has_elements = conn.execute(
        "SELECT 1 FROM cat.sqlite_master WHERE name='elements'").fetchone() is not None
    el_col = (", (SELECT e.element_id FROM cat.elements e WHERE e.part_num=sp.part_num "
              "AND e.color_id=sp.color_id) element_id") if has_elements else ", NULL element_id"
    parts = conn.execute(
        """SELECT sp.part_num, sp.color_id, sp.quantity need, p.name part_name, c.name color_name, c.rgb, c.bl_id""" + el_col + """,
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
    bin_names = {r[0]: r[1] for r in conn.execute("SELECT id,name FROM bins WHERE deleted=0")}
    avail = conn.execute(
        "SELECT part_num, color_id, bin, SUM(quantity) q FROM inventory "
        "GROUP BY part_num, color_id, bin HAVING q > 0"
    ).fetchall()
    exact_map, any_map = {}, {}
    for r in avail:
        name = bin_names.get(r["bin"], r["bin"])
        exact_map.setdefault((r["part_num"], r["color_id"]), {})[name] = r["q"]
        m = any_map.setdefault(r["part_num"], {})
        m[name] = m.get(name, 0) + r["q"]
    out, have_total = [], 0
    for r in parts:
        d = dict(r)
        have = d["have_any"] if ignore_color else d["have_exact"]
        d["have"] = min(have, d["need"])
        d["missing"] = d["need"] - d["have"]
        where = any_map.get(d["part_num"], {}) if ignore_color else exact_map.get((d["part_num"], d["color_id"]), {})
        d["elsewhere"] = [{"name": n, "qty": q} for n, q in sorted(where.items())]
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


@app.get("/api/sets/{set_num}")
def set_detail(set_num: str, ignore_color: bool = False, user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        return _set_detail(conn, set_num, ignore_color)


@app.get("/api/sets/{set_num}/missing.csv", response_class=PlainTextResponse)
def set_missing_csv(set_num: str, ignore_color: bool = False, user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        d = _set_detail(conn, set_num, ignore_color)
    lines = ["Part,Color,Quantity"] + [f"{p['part_num']},{p['color_id']},{p['missing']}" for p in d["parts"] if p["missing"] > 0]
    return PlainTextResponse("\n".join(lines) + "\n",
                             headers={"Content-Disposition": f"attachment; filename=mancanti-{set_num}.csv"})


@app.post("/api/sets/identify")
def sets_identify(data: dict = Body(...), user: dict = Depends(current_user)):
    """Reverse lookup: sets that contain the given pieces (part_num + color_id).
    Returns sets matching at least `min_match` distinct sought pieces (default: all)."""
    raw = data.get("parts") or []
    if not raw:
        raise HTTPException(400, "Indica almeno un pezzo")
    ignore_color = bool(data.get("ignore_color"))
    limit = min(int(data.get("limit") or 60), 200)
    needle = sorted({(str(p["part_num"]), int(p.get("color_id") or 0)) for p in raw})[:20]
    sought = len(needle)
    min_match = int(data.get("min_match") or 0) or sought
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        if ignore_color:
            values = ",".join("(?)" for _ in needle)
            params = [p for p, _ in needle]
            have = f"""WITH n(part_num) AS (VALUES {values})
                SELECT sp.set_num, COUNT(DISTINCT sp.part_num) matched,
                       GROUP_CONCAT(DISTINCT sp.part_num) matched_parts
                FROM cat.set_parts sp JOIN n ON n.part_num=sp.part_num
                GROUP BY sp.set_num HAVING matched >= ?"""
        else:
            values = ",".join("(?,?)" for _ in needle)
            params = [x for pair in needle for x in pair]
            have = f"""WITH n(part_num,color_id) AS (VALUES {values})
                SELECT sp.set_num, COUNT(DISTINCT sp.part_num||'|'||sp.color_id) matched,
                       GROUP_CONCAT(DISTINCT sp.part_num||':'||sp.color_id) matched_parts
                FROM cat.set_parts sp JOIN n ON n.part_num=sp.part_num AND n.color_id=sp.color_id
                GROUP BY sp.set_num HAVING matched >= ?"""
        rows = conn.execute(
            f"""SELECT s.set_num, s.name, s.year, s.img_url, s.total_qty, th.name theme,
                       h.matched, h.matched_parts
                FROM ({have}) h JOIN cat.sets s ON s.set_num=h.set_num
                LEFT JOIN cat.themes th ON th.id=s.theme_id
                ORDER BY h.matched DESC, s.year DESC, s.name LIMIT ?""",
            (*params, min_match, limit),
        ).fetchall()
        return {"sought": sought, "results": [dict(r) for r in rows]}


@app.get("/api/sets/{set_num}/alternates")
async def set_alternates(set_num: str, user: dict = Depends(current_user)):
    """Alternative builds (MOCs) from Rebrickable. Requires a Rebrickable API key."""
    with get_db(user["username"]) as conn:
        key = rebrickable_key(conn)
    if not key:
        return {"available": False, "results": []}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"https://rebrickable.com/api/v3/lego/sets/{set_num}/alternates/",
                             headers={"Authorization": f"key {key}"}, params={"page_size": 50})
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
def save_project(set_num: str, saved: bool = Body(True, embed=True), user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        conn.execute(
            "INSERT INTO saved_projects (set_num,note,deleted,updated_at,device_id) VALUES (?,NULL,?,?,?) "
            "ON CONFLICT(set_num) DO UPDATE SET deleted=excluded.deleted, updated_at=excluded.updated_at, device_id=excluded.device_id",
            (set_num, 0 if saved else 1, now_ms(), device_id(conn)),
        )
    return {"ok": True}


@app.get("/api/saved")
def saved_projects(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
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
def manuals_list(user: dict = Depends(current_user)):
    mdir = manuals_dir(user["username"])
    with get_db(user["username"]) as conn:
        ready = catalog_ready(conn)
        name_sql = "(SELECT name FROM cat.sets s WHERE s.set_num=m.set_num)" if ready else "NULL"
        img_sql = "(SELECT img_url FROM cat.sets s WHERE s.set_num=m.set_num)" if ready else "NULL"
        rows = conn.execute(
            f"SELECT m.id, m.set_num, m.title, m.size, m.sha256, m.source_url, {name_sql} set_name, {img_sql} img_url "
            "FROM manuals m WHERE m.deleted=0 ORDER BY m.set_num, m.title").fetchall()
        return [dict(r) | {"available": (mdir / f"{r['sha256']}.pdf").exists()} for r in rows]


@app.post("/api/manuals/fetch/{set_num}")
async def manuals_fetch(set_num: str, user: dict = Depends(current_user)):
    mdir = manuals_dir(user["username"])
    with get_db(user["username"]) as conn:
        require_catalog(conn)
        s = _set_row(conn, set_num)
        try:
            stored = await manuals.fetch_from_lego(conn, mdir, set_num, s["name"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"{exc}. Scarica il PDF da {manuals.lego_instructions_page(set_num)} "
                                     "e caricalo manualmente.") from exc
        conn.execute("INSERT INTO saved_projects (set_num,deleted,updated_at,device_id) VALUES (?,0,?,?) "
                     "ON CONFLICT(set_num) DO UPDATE SET deleted=0, updated_at=excluded.updated_at",
                     (set_num, now_ms(), device_id(conn)))
        return {"stored": stored}


@app.post("/api/manuals/upload")
async def manuals_upload(set_num: str = Form(...), title: str = Form(""),
                         file: UploadFile | None = File(None), url: str = Form(""),
                         user: dict = Depends(current_user)):
    if file is not None:
        data, source = await file.read(), None
    elif url:
        try:
            data, source = await manuals.download_pdf(url), url
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Download non riuscito: {exc}") from exc
    else:
        raise HTTPException(400, "Carica un file PDF o indica un URL")
    with get_db(user["username"]) as conn:
        name = title
        if not name and catalog_ready(conn):
            row = conn.execute("SELECT name FROM cat.sets WHERE set_num=?", (set_num,)).fetchone()
            name = row[0] if row else ""
        try:
            return manuals.store_pdf(conn, manuals_dir(user["username"]), set_num, name or set_num, data, source)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/manuals/{manual_id}/file")
def manuals_file(manual_id: str, user: dict = Depends(current_user)):
    mdir = manuals_dir(user["username"])
    with get_db(user["username"]) as conn:
        row = conn.execute("SELECT sha256, set_num, title FROM manuals WHERE id=? AND deleted=0", (manual_id,)).fetchone()
    if not row or not (mdir / f"{row[0]}.pdf").exists():
        raise HTTPException(404, "Manuale non disponibile su questo dispositivo")
    return FileResponse(mdir / f"{row[0]}.pdf", media_type="application/pdf",
                        filename=f"{row[1]}.pdf", content_disposition_type="inline")


@app.delete("/api/manuals/{manual_id}")
def manuals_delete(manual_id: str, user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        conn.execute("UPDATE manuals SET deleted=1, updated_at=?, device_id=? WHERE id=?",
                     (now_ms(), device_id(conn), manual_id))
    return {"ok": True}


# ---------- sync ----------
# These endpoints accept either the session cookie or the X-Sync-Token header
# (the token each user finds in Impostazioni, needed for server-to-server sync).
@app.get("/api/sync/export")
def sync_export(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        return sync.export_state(conn)


@app.post("/api/sync/import")
def sync_import(data: dict = Body(...), user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        try:
            return sync.import_state(conn, manuals_dir(user["username"]), data)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"Dati non validi: {exc}") from exc


@app.get("/api/sync/files/{sha}")
def sync_file_get(sha: str, user: dict = Depends(current_user)):
    path = manuals_dir(user["username"]) / f"{sha}.pdf"
    if not sha.isalnum() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf")


@app.put("/api/sync/files/{sha}")
async def sync_file_put(sha: str, request: Request, user: dict = Depends(current_user)):
    if not sha.isalnum():
        raise HTTPException(400)
    try:
        sync.save_file(manuals_dir(user["username"]), sha, await request.body())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/sync/peer")
async def sync_peer(url: str = Body(...), token: str = Body(""), user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        if url.rstrip("/").endswith(device_id(conn)):
            raise HTTPException(400, "URL non valido")
        try:
            return await sync.sync_with_peer(conn, manuals_dir(user["username"]), url, token)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Impossibile contattare {url}: {exc}") from exc


@app.get("/api/sync/peers")
def sync_peers(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        return [dict(r) for r in conn.execute("SELECT url, token, last_sync FROM peers ORDER BY last_sync DESC")]


@app.get("/api/sync/backup")
def sync_backup(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        data = sync.build_backup(conn, manuals_dir(user["username"]))
        name = device_name(conn)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=brick-scanner-{name}.zip"})


@app.post("/api/sync/restore")
async def sync_restore(file: UploadFile = File(...), user: dict = Depends(current_user)):
    raw = await file.read()
    with get_db(user["username"]) as conn:
        try:
            return sync.restore_backup(conn, manuals_dir(user["username"]), raw)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Backup non valido: {exc}") from exc


# ---------- settings ----------
@app.post("/api/settings/device-name")
def set_device_name(name: str = Body(..., embed=True), user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('device_name', ?)", (name.strip()[:60],))
    return {"ok": True}


@app.get("/api/settings/rebrickable-key")
def rebrickable_key_get(user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        stored = conn.execute("SELECT value FROM meta WHERE key='rebrickable_api_key'").fetchone()
        stored = stored[0] if stored else ""
        key = stored or REBRICKABLE_API_KEY
    return {
        "configured": bool(key),
        "source": "ui" if stored else ("env" if key else None),
        "masked": f"{key[:4]}…{key[-4:]}" if key and len(key) > 8 else "",
    }


@app.post("/api/settings/rebrickable-key")
def rebrickable_key_set(data: dict = Body(...), user: dict = Depends(current_user)):
    with get_db(user["username"]) as conn:
        set_rebrickable_key(conn, (data.get("key") or "").strip())
        configured = bool(rebrickable_key(conn))
    return {"ok": True, "configured": configured}


@app.get("/api/settings/sync-token")
def sync_token_get(user: dict = Depends(current_user)):
    return {"token": admin_db.get_sync_token(user["username"])}


@app.post("/api/settings/sync-token/regenerate")
def sync_token_regenerate(user: dict = Depends(current_user)):
    return {"token": admin_db.regenerate_sync_token(user["username"])}


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
