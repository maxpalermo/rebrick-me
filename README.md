# Brick Scanner

Web app (PWA) per fotografare i propri pezzi LEGO, riconoscerli con l'IA, tenere un inventario e scoprire quali set si possono costruire, con l'elenco dei pezzi mancanti e le istruzioni di montaggio salvate in locale.

## Avvio rapido (Docker)

```bash
cp .env.example .env      # opzionale: inserisci REBRICKABLE_API_KEY e DEVICE_NAME
docker compose up -d --build
```

Apri `http://localhost:8080` (o `http://<ip-del-pc>:8080` dal cellulare sulla stessa rete).

Al primo avvio il container scarica il catalogo pubblico di Rebrickable (~17 MB, circa 30 secondi) e lo importa in SQLite. Tutti i dati restano nella cartella `./data`:

| File | Contenuto |
|---|---|
| `data/lego.sqlite` | inventario, progetti salvati, indice delle istruzioni, peer di sincronizzazione |
| `data/catalog.sqlite` | catalogo LEGO (set, pezzi, colori, inventari dei set); si può rigenerare |
| `data/manuals/*.pdf` | istruzioni di montaggio scaricate, disponibili anche offline |

## Chiavi API

| Servizio | Serve per | Chiave |
|---|---|---|
| [Brickognize](https://brickognize.com) | riconoscimento dei pezzi dalla foto | nessuna |
| [Rebrickable](https://rebrickable.com/api/) – dump CSV | catalogo locale e calcolo dei set costruibili | nessuna |
| Rebrickable – API v3 | costruzioni alternative (MOC), mappatura precisa dei codici e dei colori BrickLink → Rebrickable | **opzionale** |

Come ottenere la chiave Rebrickable (gratuita):
1. registrati su https://rebrickable.com/signup/
2. vai su https://rebrickable.com/users/me/settings/#api e clicca **Generate new key**
3. scrivila nel file `.env` come `REBRICKABLE_API_KEY=...` ed esegui `docker compose up -d`

## Come funziona

1. **Scansione**: la foto viene divisa in singoli pezzi con OpenCV (rilevando gli oggetti rispetto allo sfondo), poi ogni ritaglio viene inviato a Brickognize, che restituisce il codice del pezzo e il colore. L'utente conferma o corregge pezzo, colore e quantità.
2. **Progetti**: l'inventario viene confrontato in SQL con gli inventari di tutti i ~28.000 set del catalogo. I set sono ordinati per percentuale di pezzi posseduti, con un'opzione per ignorare i colori. Il dettaglio di un set mostra i pezzi mancanti, esportabili in CSV per Rebrickable o BrickLink.
3. **Istruzioni**: il pulsante "Scarica da LEGO.com" scarica i PDF ufficiali (libretti principali ed extra) e li salva in `data/manuals`. In alternativa si può caricare un PDF o indicarne un URL (utile per i MOC).
4. **Sincronizzazione**: ogni installazione ha il proprio SQLite. In *Impostazioni → Sincronizza* si inserisce l'indirizzo di un'altra installazione: inventario, progetti salvati e istruzioni (PDF compresi) vengono uniti in entrambe le direzioni. In alternativa si esporta e importa un backup `.zip`.
   L'inventario è un CRDT: ogni dispositivo registra il proprio contributo per ogni pezzo e il totale è la somma dei contributi. In questo modo i pezzi scansionati su dispositivi diversi si sommano senza conflitti e senza duplicazioni.

## Cellulare

Docker gira su PC, NAS o server. Dal telefono si apre l'app nel browser. Il pulsante "Scatta foto" usa la fotocamera anche su HTTP. Per installare l'app come PWA (icona nella home) il browser richiede **HTTPS**, per esempio tramite un reverse proxy (Caddy o Traefik) oppure Tailscale (`tailscale serve 8080`).

## Sviluppo

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
DATA_DIR=../data .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev     # http://localhost:5173 (proxy /api → :8000)
```

Documentazione API: `http://localhost:8080/docs`.
