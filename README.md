# Brick Scanner

Web app (PWA) per fotografare i propri pezzi LEGO, riconoscerli con l'IA, tenerne un inventario organizzato in **librerie** (luoghi o progetti), scoprire quali set si possono costruire, identificare da quale set provengono pezzi o costruzioni, ordinare i pezzi mancanti e salvare le istruzioni di montaggio in locale.

## Avvio rapido (Docker)

```bash
cp .env.example .env      # imposta ADMIN_PASSWORD (altrimenti ne genera una casuale, vedi docker logs)
docker compose up -d --build
```

Apri `http://localhost:8080` (o `http://<ip-del-pc>:8080` dal cellulare sulla stessa rete) e accedi con `admin` + la password impostata.

## Utenti e amministrazione

Ogni utente ha inventario, istruzioni e progetti salvati **completamente separati** (`data/users/<nome>/`). Solo l'amministratore può creare utenti, dall'area **Admin** (icona 🛠️ nella barra in basso, visibile solo agli admin). L'admin è anche un utente normale con il proprio inventario. In *Impostazioni → Token di sincronizzazione* trovi il token per sincronizzare il tuo account con un'altra installazione.

Al primo avvio il container scarica il catalogo pubblico di Rebrickable (~17 MB, circa 30 secondi) e lo importa in SQLite. Tutti i dati restano nella cartella `./data`:

| File | Contenuto |
|---|---|
| `data/admin.db` | utenti, sessioni di login, token di sincronizzazione |
| `data/users/<nome>/lego.sqlite` | inventario, librerie, progetti salvati, indice istruzioni, peer, chiave Rebrickable — per singolo utente |
| `data/users/<nome>/manuals/*.pdf` | istruzioni di montaggio scaricate, disponibili anche offline |
| `data/catalog.sqlite` | catalogo LEGO condiviso (set, pezzi, colori, inventari dei set); si può rigenerare |

## Chiavi API

| Servizio | Serve per | Chiave |
|---|---|---|
| [Brickognize](https://brickognize.com) | riconoscimento dei pezzi dalla foto | nessuna |
| [Rebrickable](https://rebrickable.com/api/) – dump CSV | catalogo locale e calcolo dei set costruibili | nessuna |
| Rebrickable – API v3 | costruzioni alternative (MOC), mappatura precisa dei codici e dei colori BrickLink → Rebrickable | **opzionale, per utente** |

La chiave Rebrickable è **personale**: ogni utente incolla la propria in *Impostazioni → Chiave API Rebrickable*. Per ottenerla (gratis):
1. registrati su https://rebrickable.com/signup/
2. vai su https://rebrickable.com/users/me/settings/#api e clicca **Generate new key**
3. incollala nelle Impostazioni dell'app

In alternativa si può impostare `REBRICKABLE_API_KEY=...` nel file `.env` (vale come riserva per tutti gli utenti che non configurano la propria).

## Come funziona

1. **Scansione**: la foto viene divisa in singoli pezzi con OpenCV (rilevando gli oggetti rispetto allo sfondo), poi ogni ritaglio viene inviato a Brickognize, che restituisce il codice del pezzo e il colore. L'utente conferma o corregge pezzo, colore e quantità.
2. **Librerie**: i pezzi si organizzano in librerie personalizzate (es. "Casa mia", "Casa genitori" o un progetto) dalla vista *Pezzi*: filtro per libreria, spostamento di quantità tra librerie, creazione/rinomina/eliminazione (i pezzi di una libreria eliminata passano in "Generale"). Le librerie restano interscambiabili: se in un set manca un pezzo, il dettaglio mostra in quali librerie si trova.
3. **Progetti**: l'inventario viene confrontato in SQL con gli inventari di tutti i ~28.000 set del catalogo. I set sono ordinati per percentuale di pezzi posseduti, con un'opzione per ignorare i colori.
4. **Identifica**: due strumenti per capire da dove viene un pezzo o una costruzione — (a) foto di una costruzione completa o minifig → candidati da Brickognize con punteggio; (b) ricerca inversa: si indicano alcuni pezzi distintivi e l'app trova i set che li contengono tutti (con opzioni di corrispondenza parziale e "ignora colori").
5. **Ordini**: il dettaglio set elenca i pezzi mancanti con element ID LEGO (link diretto a Pick a Brick, lista copiabile), link alla pagina BrickLink del pezzo e CSV esportabile per Rebrickable → Buy Parts.
6. **Istruzioni**: il pulsante "Scarica da LEGO.com" scarica i PDF ufficiali (libretti principali ed extra) e li salva in `data/manuals`. In alternativa si può caricare un PDF o indicarne un URL (utile per i MOC).
7. **Sincronizzazione**: ogni utente ha il proprio SQLite. In *Impostazioni → Sincronizza* si inserisce l'indirizzo di un'altra installazione **e il token del proprio utente** su quell'installazione: inventario, librerie, progetti salvati e istruzioni (PDF compresi) vengono uniti in entrambe le direzioni. In alternativa si esporta e importa un backup `.zip`.
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
