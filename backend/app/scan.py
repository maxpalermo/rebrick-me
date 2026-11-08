"""Piece detection (OpenCV) + recognition (Brickognize) + mapping to the Rebrickable catalog."""
import asyncio
import logging
import sqlite3

import cv2
import httpx
import numpy as np

from .config import BRICKOGNIZE_URL

log = logging.getLogger("scan")
MAX_SIDE = 1600
MAX_PIECES = 40


def _load(image_bytes: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Immagine non valida")
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def segment_pieces(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Finds separated pieces lying on a fairly uniform background. Returns boxes (x, y, w, h)."""
    h, w = img.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate([lab[:8].reshape(-1, 3), lab[-8:].reshape(-1, 3),
                             lab[:, :8].reshape(-1, 3), lab[:, -8:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(lab - bg, axis=2)
    dist = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = max(3, int(min(h, w) * 0.006)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = h * w * 0.0015
    boxes = []
    for c in contours:
        if cv2.contourArea(c) < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw * bh > h * w * 0.9:
            continue
        pad = int(max(bw, bh) * 0.12)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        boxes.append((x0, y0, x1 - x0, y1 - y0))
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    return boxes[:MAX_PIECES]


async def _brickognize(client: httpx.AsyncClient, jpeg: bytes) -> dict:
    r = await client.post(
        f"{BRICKOGNIZE_URL}/predict/parts/",
        params={"predict_color": "true", "top_k_items": 5, "top_k_colors": 3},
        files={"query_image": ("piece.jpg", jpeg, "image/jpeg")},
    )
    r.raise_for_status()
    return r.json()


async def _rebrickable_part_by_bricklink(client: httpx.AsyncClient, bl_id: str, key: str) -> str | None:
    if not key:
        return None
    try:
        r = await client.get(
            "https://rebrickable.com/api/v3/lego/parts/",
            params={"bricklink_id": bl_id},
            headers={"Authorization": f"key {key}"},
        )
        r.raise_for_status()
        res = r.json().get("results") or []
        return res[0]["part_num"] if res else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Rebrickable lookup failed for %s: %s", bl_id, exc)
        return None


async def resolve_part(conn: sqlite3.Connection, client: httpx.AsyncClient, bl_id: str,
                       api_key: str = "") -> str | None:
    if conn.execute("SELECT 1 FROM cat.parts WHERE part_num=?", (bl_id,)).fetchone():
        return bl_id
    row = conn.execute("SELECT part_num FROM cat.part_alias WHERE alias=?", (bl_id,)).fetchone()
    if row:
        return row[0]
    part = await _rebrickable_part_by_bricklink(client, bl_id, api_key)
    if part:
        conn.execute("INSERT OR REPLACE INTO cat.part_alias VALUES (?,?)", (bl_id, part))
        conn.commit()
    return part


def resolve_color(conn: sqlite3.Connection, bl_id: str, name: str) -> int | None:
    row = conn.execute("SELECT id FROM cat.colors WHERE bl_id=?", (_safe_int(bl_id),)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT id FROM cat.colors WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    return row[0] if row else None


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _encode(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes()


async def _brickognize_ep(client: httpx.AsyncClient, endpoint: str, jpeg: bytes, top_k: int = 8) -> dict:
    r = await client.post(
        f"{BRICKOGNIZE_URL}/predict/{endpoint}/",
        params={"top_k_items": top_k},
        files={"query_image": ("image.jpg", jpeg, "image/jpeg")},
    )
    r.raise_for_status()
    return r.json()


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        log.warning("Brickognize call failed: %s", exc)
        return {"error": str(exc)}


def _lookup_set(conn: sqlite3.Connection, sid: str) -> dict | None:
    for cand in (sid, f"{sid}-1"):
        row = conn.execute(
            "SELECT s.set_num, s.name, s.year, s.img_url, s.total_qty, th.name theme "
            "FROM cat.sets s LEFT JOIN cat.themes th ON th.id=s.theme_id WHERE s.set_num=?",
            (cand,),
        ).fetchone()
        if row:
            return dict(row)
    return None


async def scan_build_image(conn: sqlite3.Connection, image_bytes: bytes) -> dict:
    """Recognizes an assembled build/set via Brickognize (/predict/sets + /predict/figs)."""
    img = _load(image_bytes)
    jpeg = _encode(img)
    async with httpx.AsyncClient(timeout=60) as client:
        sets_res, figs_res = await asyncio.gather(
            _safe(_brickognize_ep(client, "sets", jpeg, 8)),
            _safe(_brickognize_ep(client, "figs", jpeg, 4)),
        )
    candidates = []
    for item in (sets_res.get("items") or [])[:8]:
        hit = _lookup_set(conn, item["id"]) or {}
        candidates.append({
            "set_num": hit.get("set_num") or item["id"],
            "name": hit.get("name") or item.get("name"),
            "img_url": hit.get("img_url") or item.get("img_url"),
            "year": hit.get("year"),
            "theme": hit.get("theme"),
            "total_qty": hit.get("total_qty"),
            "in_catalog": bool(hit),
            "score": round(item.get("score", 0), 3),
        })
    figs = [
        {"id": it["id"], "name": it.get("name"), "img_url": it.get("img_url"),
         "score": round(it.get("score", 0), 3)}
        for it in (figs_res.get("items") or [])[:4]
    ]
    return {"candidates": candidates, "figs": figs, "error": sets_res.get("error")}


async def scan_image(conn: sqlite3.Connection, image_bytes: bytes, segment: bool = True,
                     api_key: str = "") -> dict:
    img = _load(image_bytes)
    h, w = img.shape[:2]
    boxes = segment_pieces(img) if segment else []
    if len(boxes) <= 1:
        boxes = [(0, 0, w, h)]
    crops = [_encode(img[y:y + bh, x:x + bw]) for x, y, bw, bh in boxes]

    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=60) as client:
        async def one(jpeg):
            async with sem:
                try:
                    return await _brickognize(client, jpeg)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Brickognize failed: %s", exc)
                    return {"error": str(exc)}

        results = await asyncio.gather(*(one(c) for c in crops))

        pieces = []
        for idx, (box, res) in enumerate(zip(boxes, results)):
            candidates = []
            for item in (res.get("items") or [])[:5]:
                part_num = await resolve_part(conn, client, item["id"], api_key)
                cat_name = None
                if part_num:
                    row = conn.execute("SELECT name FROM cat.parts WHERE part_num=?", (part_num,)).fetchone()
                    cat_name = row[0] if row else None
                candidates.append({
                    "bricklink_id": item["id"],
                    "part_num": part_num,
                    "name": cat_name or item.get("name"),
                    "img_url": item.get("img_url"),
                    "score": round(item.get("score", 0), 3),
                })
            colors = []
            for c in res.get("colors") or []:
                cid = resolve_color(conn, c.get("id"), c.get("name", ""))
                if cid is not None:
                    colors.append({"color_id": cid, "name": c.get("name"), "score": round(c.get("score", 0), 3)})
            pieces.append({
                "index": idx,
                "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
                "candidates": candidates,
                "colors": colors,
                "error": res.get("error"),
            })
    return {"width": w, "height": h, "pieces": pieces}
