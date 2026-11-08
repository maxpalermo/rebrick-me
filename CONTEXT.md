# Contesto del progetto (per riprendere il lavoro con Devin)

> Leggi questo file prima di continuare. Riassume le decisioni prese e lo stato attuale.

## Obiettivo
Web app PWA per fotografare pezzi LEGO: l'IA li riconosce, l'app tiene l'inventario, mostra
quali set si possono costruire (con pezzi mancanti ordinabili), salva le istruzioni PDF offline,
identifica da quale set provengono pezzi o costruzioni complete, sincronizza tra dispositivi.

## Stato attuale: v0.3 in produzione
App multi-utente deployata sul server di produzione (vedi sotto) e sul PC locale.
Testato end-to-end: login, creazione utenti, isolamento inventari, scansione pezzi,
identificazione set da foto (BrickHeadz Batman -> 41585-1), intersezione pezzi->set
(3 pezzi distintivi -> set unico corretto), ordine pezzi mancanti, catalogo con element_id.

## Architettura
Un solo container (`Dockerfile` multi-stage: build Node del frontend + runtime Python 3.12-slim).
DB SQLite, niente ORM, query raw. FastAPI + React 18/Vite 5 (hash routing, PWA).

```
backend/app/
  config.py    env: DATA_DIR, ADMIN_USER, ADMIN_PASSWORD, DEVICE_NAME, REBRICKABLE_API_KEY,
               AUTO_IMPORT_CATALOG, USERS_DIR=data/users, ADMIN_DB=data/admin.db
  admin_db.py  admin.db: users (pbkdf2), sessions (cookie bs_session 30gg), sync_tokens,
               settings (chiave Rebrickable salvata da UI via get_rebrickable_key())
  db.py        lego.sqlite PER-UTENTE in users/<nome>/ + ATTACH "cat" (catalogo condiviso);
               get_db(username) inizializza lazy; migrate_legacy() sposta il vecchio
               data/lego.sqlite in users/<ADMIN_USER>/ al primo avvio
  catalog.py   dump CSV Rebrickable -> catalog.sqlite (set_parts, elements element_id LEGO,
               part_images...). Import via thread; tmp file per-process (fix race)
  scan.py      OpenCV segmenta pezzi -> Brickognize /predict/parts; scan_build_image()
               per costruzioni complete -> /predict/sets + /predict/figs in parallelo
  manuals.py   scraping PDF istruzioni da lego.com + upload/URL
  sync.py      export/import JSON merge CRDT, peer sync con header X-Sync-Token, backup zip
  main.py      tutte le API; auth via Depends(current_user) (cookie o X-Sync-Token),
               require_admin per /api/admin/*
frontend/src/
  App.jsx      viste: scan, inventory, projects(+set detail), identify, manuals,
               settings, admin (solo is_admin). LoginView LEGO animata (mattoncini cadono)
```

### Dati (`./data`)
- `admin.db`: users, sessions, sync_tokens, settings — sito-wide
- `users/<nome>/lego.sqlite`: inventario CRDT (riga per part+color+device), manuals, saved_projects, peers
- `users/<nome>/manuals/<sha256>.pdf`
- `catalog.sqlite`: condiviso (set_parts, elements, colors con bl_id solo se chiave Rebrickable)

### Decisioni chiave
- **Multi-utente**: un DB per utente (isolamento fisico, query invariate). Admin crea utenti
  da #/admin; admin è anche utente col suo inventario. Nessuna registrazione pubblica.
- **Chiave Rebrickable**: salvata in admin.db.settings da Impostazioni (admin), letta runtime
  da get_rebrickable_key(); env REBRICKABLE_API_KEY = fallback. Abilita MOC, bl_id colore,
  mappatura BrickLink precisa.
- **Identifica**: (a) foto costruzione -> predict/sets+figs -> candidati con score, click->dettaglio;
  (b) ricerca inversa per N pezzi -> POST /api/sets/identify intersezione set_parts
  (min_match = corrispondenza parziale, ignore_color).
- **Ordini**: cat.elements (element_id max per part+color) -> link LEGO Pick a Brick
  ?query=element_id, link BrickLink ?P=part&C=bl_id, "copia element ID", CSV -> Rebrickable Buy Parts.
- **Sync**: sessione cookie per browser; X-Sync-Token (per-utente, in Impostazioni) per
  sync server-server. peers(url,token,last_sync).
- **Zero-quantity**: conferma prima di eliminare il pezzo (confirm() in InventoryView).
- **Auth edge**: /api/me 401 -> frontend mostra LoginView; fetch 401 dispatcha 'bs-unauthorized'.

## Deploy
- **Locale** (`docker-compose.yml`): `.env` con ADMIN_USER/PASSWORD, porta 8080, ./data.
- **Produzione** (`docker-compose.prod.yml`): build SUL server (arm64!), porta host 8020,
  network_mode bridge, path assoluti /home/massimiliano/docker/rebrick-me.
  Davanti c'e' Nginx Proxy Manager: dominio -> :8020, SSL Let's Encrypt + Force SSL.
  La porta 8020 NON va esposta pubblicamente (l'app ha il suo login; NPM serve solo HTTPS).

### Server di produzione
- SSH: `ssh -p 8022 massimiliano@192.168.1.18` (chiave, no password)
- Deploy: rsync codice + rebuild sul server:
  ```bash
  rsync -avz -e "ssh -p 8022" --exclude data --exclude .git --exclude .env \
    /home/massimiliano/docker/rebrick-me/ massimiliano@192.168.1.18:/home/massimiliano/docker/rebrick-me/
  ssh -p 8022 massimiliano@192.168.1.18 'cd /home/massimiliano/docker/rebrick-me && \
    docker compose -f docker-compose.prod.yml up -d --build'
  ```
- Utenti attuali sul server: admin + matteo + ernesto. Password admin impostata dall'utente
  (non nel repo).
- Catalogo: se serve re-importare -> POST /api/catalog/import con sessione admin,
  oppure docker exec (MAI in parallelo: bug tmp-file risolto con nome per-pid).

## Comandi utili
```bash
docker compose up -d --build        # locale :8080
docker logs -f brick-scanner
# test API locale: admin/mattoncino (da .env); server: chiedi password all'utente
```

## Gotchas note
- Server arm64: niente docker save/load dal PC x86, build sul server (cache veloce).
- Playwright container (screenshot): non risolve nomi container/host.docker.internal;
  usare IP LAN dell'host (192.168.1.240) da caddy-net.
- basic-auth NPM + app: non usata; l'app ha login proprio (cookie + sync token).
- La LAN IP del PC: 192.168.1.240 (e .226). Telefono: https://<dominio NPM> -> PWA installabile.

## Idee / prossimi passi possibili
- Peer sync testata solo con credenziali locali: verificare X-Sync-Token su istanze remote.
- BrickLink wanted-list XML (richiede bl_id -> chiave Rebrickable configurata).
- HTTPS/installazione PWA testata su dominio reale; Tailscale non usato (scelto NPM).
- Sync automatica periodica peer; dedup element_id per data rilascio (oggi MAX).
