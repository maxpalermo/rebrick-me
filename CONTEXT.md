# Contesto del progetto (per riprendere il lavoro con Devin)

> Leggi questo file prima di continuare. Riassume le decisioni prese e lo stato attuale.

## Obiettivo (richiesta originale dell'utente)
Web app con cui fotografare i propri pezzi LEGO. Un'IA riconosce i pezzi, l'app cerca online quali progetti (set) si possono costruire con i pezzi disponibili, mostra un elenco con i **pezzi mancanti** e permette di scaricare il **manuale di istruzioni**.

Requisiti aggiuntivi dell'utente:
- tutto dentro un **container Docker** con tutti i servizi attivi;
- database **SQLite**, perché l'app deve stare sul cellulare o sul PC. Registra i pezzi disponibili e conserva le istruzioni sempre disponibili;
- **sincronizzazione** tra i propri dispositivi, per unire i dati;
- cartella del progetto sul PC dell'utente: `/home/massimiliano/docker/rebrick-me`;
- l'utente lavora con **Devin Desktop** sul proprio PC, non con Devin cloud.

## Stato attuale: v0.2.0 — multi-utente con login
Testato end-to-end in Docker:
- scansione di una foto con 3 pezzi: segmentazione OpenCV, poi Brickognize. Codice e colore corretti per tutti e 3;
- calcolo dei set costruibili su ~28.500 set in meno di 0,2 s;
- download delle istruzioni da LEGO.com (set 10696: libretto principale + 4 extra) salvate in `data/manuals`;
- sincronizzazione tra due istanze (inventari sommati senza duplicati, PDF copiati) e backup/ripristino `.zip`;
- **multi-utente (v0.2.0)**: login con cookie di sessione, un `lego.sqlite` per utente in `data/users/<nome>/` (isolamento totale), `data/admin.db` per utenti/sessioni/token; area admin su `#/admin` (creazione/eliminazione utenti, reset password, statistiche); admin ha anche il proprio inventario; sync tra istanze via `X-Sync-Token` (Impostazioni → Token di sincronizzazione); migrazione automatica del vecchio `data/lego.sqlite` in `users/<ADMIN_USER>/` al primo avvio;
- foto reali dal telefono testate in produzione: riconoscimento pezzi OK.

Non ancora testato: installazione PWA via HTTPS, chiave Rebrickable reale (MOC alternativi), peer sync tra istanze con token.

## Architettura
Un solo container (`Dockerfile` multi-stage: build Node del frontend + runtime Python 3.12-slim). `docker-compose.yml` espone la porta `${PORT:-8080}` → 8000 e monta il volume `./data:/data`.

```
backend/app/
  config.py    variabili d'ambiente (DATA_DIR, REBRICKABLE_API_KEY, DEVICE_NAME, ADMIN_USER/PASSWORD, USERS_DIR...)
  admin_db.py  admin.db: utenti (pbkdf2), sessioni cookie 30gg, sync_tokens per utente
  db.py        lego.sqlite per-utente in users/<nome>/ + ATTACH "cat"; set_quantity() CRDT; migrate_legacy()
  catalog.py   download dei dump CSV pubblici Rebrickable -> catalog.sqlite (tabelle set_parts, part_images...)
  scan.py      OpenCV (sfondo stimato dai bordi, distanza Lab, Otsu, contorni) -> crop -> Brickognize /predict/parts/
  manuals.py   scraping dei link PDF dalla pagina lego.com/it-it/service/building-instructions/<num>; upload/URL
  sync.py      export/import JSON, merge, peer sync bidirezionale, backup zip
  main.py      API FastAPI + serve la SPA (static/)
frontend/src/  React 18 + Vite 5, PWA (manifest + sw.js), UI in italiano, navigazione via hash
  App.jsx      viste: Scansiona, Pezzi (inventario), Progetti (+ dettaglio set), Istruzioni, Impostazioni
```

### Dati (`./data`)
- `admin.db`: `users` (username, password_hash pbkdf2, is_admin), `sessions`, `sync_tokens`
- `users/<nome>/lego.sqlite`: `meta` (device_id, device_name), `inventory`, `inventory_total`, `manuals`, `saved_projects`, `peers`
- `users/<nome>/manuals/<sha256>.pdf`: file indirizzati per contenuto (deduplicati, sincronizzati per hash)
- `catalog.sqlite`: condiviso tra utenti, si rigenera dai CSV Rebrickable
- Produzione: `docker-compose.prod.yml` (porta host 8020, NPM → HTTPS + dominio; la porta 8020 non va esposta pubblicamente)

### Decisioni chiave
- **Riconoscimento**: API Brickognize (gratuita, senza chiave). Restituisce ID **BrickLink** di pezzi e colori, mappati a Rebrickable così:
  - pezzo: stesso part_num nel catalogo, altrimenti `part_alias` o Rebrickable API `?bricklink_id=` (solo con chiave);
  - colore: `colors.bl_id` (popolato solo con la chiave), altrimenti corrispondenza per nome.
- **Catalogo locale** dai dump CSV pubblici (`cdn.rebrickable.com/media/downloads/*.csv.gz`), senza chiave: i set costruibili si calcolano offline in SQL. Si usa l'ultima versione dell'inventario di ogni set e si escludono i pezzi di ricambio (spare).
- **Matching**: per ogni set somma `MIN(posseduti, richiesti)`; percentuale = have / total_qty; opzione "ignora colori"; filtro min/max numero di pezzi (default min 10, per escludere i set minuscoli).
- **Inventario CRDT**: riga per `(part_num, color_id, device_id)`. Il totale è la somma dei contributi; ogni dispositivo modifica solo le proprie righe. Il merge è "vince l'`updated_at` più recente" per riga, quindi è senza conflitti e risincronizzare non duplica nulla.
- **manuals / saved_projects**: last-write-wins con soft delete (`deleted=1`).
- **Peer sync** (`POST /api/sync/peer {url}`): pull export → merge → scarica i PDF mancanti → push export → carica i PDF che mancano al peer (`PUT /api/sync/files/<sha>`).
- **Istruzioni LEGO**: non esiste un'API ufficiale; si leggono i link `product.bi.core.pdf/...` e `product.bi.additional.extra.pdf/...` dalla pagina. Possono rompersi se LEGO cambia il sito. Ripiego: upload del PDF o URL.
- **Cellulare**: Docker gira su PC/NAS. Il telefono usa il browser (`<input capture="environment">` funziona anche su HTTP). L'installazione come PWA richiede HTTPS (Tailscale serve / reverse proxy).
- Chiave **Rebrickable** opzionale (`.env` → `REBRICKABLE_API_KEY`): MOC alternativi (`/api/sets/{set}/alternates`) e mappatura BrickLink più precisa.

## Comandi utili
```bash
cp .env.example .env && docker compose up -d --build   # http://localhost:8080 , API docs su /docs
docker compose logs -f
```

## Idee / prossimi passi possibili
- Testare con foto reali e regolare la segmentazione (parametri in `scan.py`: `min_area`, padding, `MAX_PIECES`).
- Ricerca anche sui MOC (inventari MOC via Rebrickable API con chiave).
- HTTPS integrato (servizio Caddy opzionale in compose) per installare la PWA sul telefono.
- Autenticazione semplice se l'app viene esposta fuori dalla rete di casa (oggi non ce n'è).
- Sincronizzazione automatica periodica con i peer salvati.
