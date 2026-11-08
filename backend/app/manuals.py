"""Building instructions: download from LEGO, manual upload, local storage."""
import hashlib
import re
import sqlite3
import uuid

from pathlib import Path

import httpx

from .db import device_id, now_ms

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PDF_RE = re.compile(r'https?://www\.lego\.com/cdn/product-assets/product\.bi\.[a-z.]+\.pdf/[^"\'\s<>\\]+\.pdf', re.I)


def lego_instructions_page(set_num: str) -> str:
    return f"https://www.lego.com/it-it/service/building-instructions/{set_num.split('-')[0]}"


def store_pdf(conn: sqlite3.Connection, manuals_dir: Path, set_num: str, title: str, data: bytes,
              source_url: str | None) -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("Il file non è un PDF valido")
    sha = hashlib.sha256(data).hexdigest()
    path = manuals_dir / f"{sha}.pdf"
    if not path.exists():
        path.write_bytes(data)
    existing = conn.execute("SELECT id FROM manuals WHERE set_num=? AND sha256=?", (set_num, sha)).fetchone()
    mid = existing[0] if existing else uuid.uuid4().hex
    conn.execute(
        "INSERT INTO manuals (id,set_num,title,filename,source_url,sha256,size,deleted,updated_at,device_id) "
        "VALUES (?,?,?,?,?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET deleted=0, title=excluded.title, "
        "updated_at=excluded.updated_at, device_id=excluded.device_id",
        (mid, set_num, title, path.name, source_url, sha, len(data), now_ms(), device_id(conn)),
    )
    return {"id": mid, "set_num": set_num, "title": title, "sha256": sha, "size": len(data)}


async def download_pdf(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": UA}) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def fetch_from_lego(conn: sqlite3.Connection, manuals_dir: Path, set_num: str, set_name: str) -> list[dict]:
    page = lego_instructions_page(set_num)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}) as client:
        r = await client.get(page)
        if r.status_code != 200:
            raise RuntimeError(f"lego.com ha risposto {r.status_code}")
        urls = []
        for u in PDF_RE.findall(r.text.replace("\\u002F", "/").replace("\\/", "/")):
            if u not in urls:
                urls.append(u)
        urls.sort(key=lambda u: ".core." not in u)
        if not urls:
            raise RuntimeError("Nessun PDF di istruzioni trovato su lego.com per questo set")
        stored = []
        core = [u for u in urls if ".core." in u]
        for u in urls[:15]:
            pdf = await client.get(u)
            if pdf.status_code == 200 and pdf.content.startswith(b"%PDF"):
                if u in core:
                    title = f"{set_name} - libretto {core.index(u) + 1}" if len(core) > 1 else set_name
                else:
                    title = f"{set_name} - extra: {u.rsplit('/', 1)[-1][:-4].replace('_', ' ')}"
                stored.append(store_pdf(conn, manuals_dir, set_num, title, pdf.content, u))
        if not stored:
            raise RuntimeError("Download dei PDF da lego.com non riuscito")
        return stored
