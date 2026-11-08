import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'

const TABS = [
  ['scan', 'Scansiona', '📷'],
  ['inventory', 'Pezzi', '🧱'],
  ['projects', 'Progetti', '🏗️'],
  ['identify', 'Identifica', '🔍'],
  ['manuals', 'Istruzioni', '📘'],
  ['settings', 'Impostazioni', '⚙️'],
]

function useHashTab() {
  const read = () => (window.location.hash.slice(1) || 'scan').split('/')
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const on = () => setRoute(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

const go = (path) => (window.location.hash = path)

function Swatch({ rgb, trans }) {
  return <span className={'swatch' + (trans ? ' trans' : '')} style={{ background: rgb ? `#${rgb}` : '#ccc' }} />
}

function Img({ src, alt, className }) {
  const [err, setErr] = useState(false)
  if (!src || err) return <div className={'img-ph ' + (className || '')}>🧱</div>
  return <img className={className} src={src} alt={alt || ''} loading="lazy" onError={() => setErr(true)} />
}

function Toast({ toast }) {
  if (!toast) return null
  return <div className={'toast ' + (toast.error ? 'error' : '')}>{toast.text}</div>
}

function ColorSelect({ colors, value, onChange, suggested = [] }) {
  const sugIds = new Set(suggested.map((s) => s.color_id))
  return (
    <select value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="" disabled>
        Colore…
      </option>
      {suggested.length > 0 && (
        <optgroup label="Suggeriti">
          {suggested.map((s) => (
            <option key={'s' + s.color_id} value={s.color_id}>
              {s.name} ({Math.round(s.score * 100)}%)
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="Tutti i colori">
        {colors
          .filter((c) => !sugIds.has(c.id))
          .map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
      </optgroup>
    </select>
  )
}

function PartSearch({ onPick, placeholder = 'Cerca pezzo per nome o codice (es. 3001, brick 2 x 4)' }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState([])
  useEffect(() => {
    if (q.trim().length < 2) return setRes([])
    const t = setTimeout(() => api.get(`/api/parts/search?q=${encodeURIComponent(q.trim())}`).then(setRes).catch(() => {}), 250)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="part-search">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} />
      {res.length > 0 && (
        <div className="dropdown">
          {res.map((p) => (
            <button
              key={p.part_num}
              className="dropdown-item"
              onClick={() => {
                onPick(p)
                setQ('')
                setRes([])
              }}
            >
              <Img src={p.img_url} className="thumb-s" />
              <span>
                <b>{p.part_num}</b> {p.name}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------------- Scan ---------------- */
function ScanView({ colors, notify, catalogReady }) {
  const [photo, setPhoto] = useState(null)
  const [result, setResult] = useState(null)
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState(false)
  const [segment, setSegment] = useState(true)
  const camRef = useRef()
  const fileRef = useRef()

  const onFile = async (file) => {
    if (!file) return
    setPhoto(URL.createObjectURL(file))
    setResult(null)
    setRows([])
    setBusy(true)
    const fd = new FormData()
    fd.append('image', file)
    fd.append('segment', segment ? 'true' : 'false')
    try {
      const r = await api.post('/api/scan', fd)
      setResult(r)
      setRows(
        r.pieces.map((p) => {
          const cand = p.candidates.find((c) => c.part_num)
          return {
            ...p,
            include: !!cand && !p.error,
            part: cand ? { part_num: cand.part_num, name: cand.name, img_url: cand.img_url } : null,
            color_id: p.colors[0]?.color_id ?? null,
            quantity: 1,
          }
        })
      )
      if (!r.pieces.length) notify('Nessun pezzo riconosciuto', true)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  const update = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const findSet = () => {
    const picked = rows
      .filter((r) => r.include && r.part)
      .map((r) => ({ part_num: r.part.part_num, color_id: r.color_id, name: r.part.name, img_url: r.part.img_url }))
    if (!picked.length) return notify('Seleziona almeno un pezzo riconosciuto', true)
    localStorage.setItem('identifyParts', JSON.stringify(picked))
    go('identify')
  }

  const save = async () => {
    const items = rows
      .filter((r) => r.include && r.part && r.color_id != null)
      .map((r) => ({ part_num: r.part.part_num, color_id: r.color_id, quantity: r.quantity }))
    if (!items.length) return notify('Seleziona almeno un pezzo con colore', true)
    try {
      await api.post('/api/inventory/add', items)
      notify(`${items.reduce((a, b) => a + b.quantity, 0)} pezzi aggiunti all'inventario`)
      setRows([])
      setResult(null)
      setPhoto(null)
    } catch (e) {
      notify(e.message, true)
    }
  }

  return (
    <div className="view">
      <h2>Fotografa i tuoi pezzi</h2>
      <p className="muted">
        Per risultati migliori: pezzi separati tra loro, su uno sfondo uniforme (foglio bianco o tavolo chiaro), buona luce.
      </p>
      {!catalogReady && <div className="banner">Il catalogo è in fase di importazione: attendi qualche istante.</div>}
      <div className="row gap">
        <button className="primary big" disabled={busy || !catalogReady} onClick={() => camRef.current.click()}>
          📷 Scatta foto
        </button>
        <button className="big" disabled={busy || !catalogReady} onClick={() => fileRef.current.click()}>
          🖼️ Carica immagine
        </button>
      </div>
      <label className="check">
        <input type="checkbox" checked={segment} onChange={(e) => setSegment(e.target.checked)} /> Rileva più pezzi nella stessa foto
      </label>
      <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => onFile(e.target.files[0])} />
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files[0])} />

      {photo && (
        <div className="photo-wrap">
          <img src={photo} alt="foto" />
          {result &&
            result.pieces.map((p, i) => (
              <div
                key={i}
                className={'bbox' + (rows[i]?.include ? '' : ' off')}
                style={{
                  left: `${(p.box.x / result.width) * 100}%`,
                  top: `${(p.box.y / result.height) * 100}%`,
                  width: `${(p.box.w / result.width) * 100}%`,
                  height: `${(p.box.h / result.height) * 100}%`,
                }}
              >
                <span>{i + 1}</span>
              </div>
            ))}
          {busy && <div className="overlay">Riconoscimento in corso…</div>}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="row between wrap">
            <h3>Pezzi riconosciuti ({rows.length})</h3>
            <button className="small" onClick={findSet}>
              🔍 Da quale set viene?
            </button>
          </div>
          <div className="list">
            {rows.map((r, i) => (
              <div key={i} className={'card piece' + (r.include ? '' : ' dim')}>
                <div className="piece-head">
                  <input type="checkbox" checked={r.include} onChange={(e) => update(i, { include: e.target.checked })} />
                  <span className="badge">{i + 1}</span>
                  <Img src={r.part?.img_url} className="thumb" />
                  <div className="grow">
                    <div className="title">{r.part ? r.part.name : 'Non riconosciuto'}</div>
                    <div className="muted small">{r.part?.part_num}</div>
                  </div>
                </div>
                {r.error && <div className="error small">Errore riconoscimento: {r.error}</div>}
                {r.candidates.length > 1 && (
                  <div className="cands">
                    {r.candidates
                      .filter((c) => c.part_num)
                      .map((c) => (
                        <button
                          key={c.part_num}
                          className={'cand' + (r.part?.part_num === c.part_num ? ' sel' : '')}
                          onClick={() => update(i, { part: c, include: true })}
                          title={c.name}
                        >
                          <Img src={c.img_url} className="thumb-s" />
                          <span className="small">
                            {c.part_num} · {Math.round(c.score * 100)}%
                          </span>
                        </button>
                      ))}
                  </div>
                )}
                <div className="row gap wrap">
                  <ColorSelect colors={colors} value={r.color_id} suggested={r.colors} onChange={(v) => update(i, { color_id: v })} />
                  <input
                    type="number"
                    min="1"
                    className="qty"
                    value={r.quantity}
                    onChange={(e) => update(i, { quantity: Math.max(1, Number(e.target.value) || 1) })}
                  />
                </div>
                <PartSearch placeholder="Non è giusto? Cerca il pezzo corretto…" onPick={(p) => update(i, { part: p, include: true })} />
              </div>
            ))}
          </div>
          <button className="primary big sticky" onClick={save}>
            ✅ Aggiungi {rows.filter((r) => r.include).length} pezzi all'inventario
          </button>
        </>
      )}
    </div>
  )
}

/* ---------------- Inventory ---------------- */
function InventoryView({ colors, notify }) {
  const [items, setItems] = useState(null)
  const [filter, setFilter] = useState('')
  const [adding, setAdding] = useState({ part: null, color_id: null, quantity: 1 })

  const load = useCallback(() => api.get('/api/inventory').then(setItems).catch((e) => notify(e.message, true)), [notify])
  useEffect(() => {
    load()
  }, [load])

  const setQty = async (it, q) => {
    if (q <= 0 && !confirm(`Eliminare "${it.part_name || it.part_num}" (${it.color_name}) dall'inventario?`)) return
    await api.put(`/api/inventory/${encodeURIComponent(it.part_num)}/${it.color_id}`, { quantity: Math.max(0, q) })
    load()
  }

  const add = async () => {
    if (!adding.part || adding.color_id == null) return notify('Scegli pezzo e colore', true)
    await api.post('/api/inventory/add', [{ part_num: adding.part.part_num, color_id: adding.color_id, quantity: adding.quantity }])
    setAdding({ part: null, color_id: null, quantity: 1 })
    notify('Pezzo aggiunto')
    load()
  }

  const shown = useMemo(() => {
    if (!items) return []
    const f = filter.toLowerCase()
    return items.filter((i) => !f || `${i.part_num} ${i.part_name} ${i.color_name}`.toLowerCase().includes(f))
  }, [items, filter])

  const total = items ? items.reduce((a, b) => a + b.quantity, 0) : 0

  return (
    <div className="view">
      <div className="row between wrap">
        <h2>I miei pezzi</h2>
        <a className="button" href="/api/inventory/export.csv">
          ⬇️ Esporta CSV
        </a>
      </div>
      <p className="muted">
        {items ? `${total} pezzi, ${items.length} tipi diversi` : 'Caricamento…'}
      </p>
      <details className="card">
        <summary>➕ Aggiungi pezzo manualmente</summary>
        <PartSearch onPick={(p) => setAdding((a) => ({ ...a, part: p }))} />
        {adding.part && (
          <div className="row gap wrap mt">
            <Img src={adding.part.img_url} className="thumb" />
            <div className="grow">
              <b>{adding.part.part_num}</b> {adding.part.name}
            </div>
            <ColorSelect colors={colors} value={adding.color_id} onChange={(v) => setAdding((a) => ({ ...a, color_id: v }))} />
            <input
              type="number"
              min="1"
              className="qty"
              value={adding.quantity}
              onChange={(e) => setAdding((a) => ({ ...a, quantity: Math.max(1, Number(e.target.value) || 1) }))}
            />
            <button className="primary" onClick={add}>
              Aggiungi
            </button>
          </div>
        )}
      </details>
      <input className="mt" placeholder="Filtra…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="list mt">
        {shown.map((it) => (
          <div key={`${it.part_num}-${it.color_id}`} className="card inv">
            <Img src={it.img_url} className="thumb" />
            <div className="grow">
              <div className="title">{it.part_name || it.part_num}</div>
              <div className="muted small">
                {it.part_num} · <Swatch rgb={it.rgb} trans={it.is_trans} /> {it.color_name}
              </div>
            </div>
            <div className="stepper">
              <button onClick={() => setQty(it, it.quantity - 1)}>−</button>
              <span>{it.quantity}</span>
              <button onClick={() => setQty(it, it.quantity + 1)}>+</button>
            </div>
          </div>
        ))}
        {items && items.length === 0 && <p className="muted">Nessun pezzo ancora. Inizia dalla scansione!</p>}
      </div>
    </div>
  )
}

/* ---------------- Projects ---------------- */
function Pct({ value }) {
  const cls = value >= 90 ? 'good' : value >= 50 ? 'mid' : 'low'
  return (
    <div className="pct">
      <div className={'pct-bar ' + cls} style={{ width: `${Math.min(100, value)}%` }} />
      <span>{value}%</span>
    </div>
  )
}

function ProjectsView({ notify }) {
  const [f, setF] = useState(() => JSON.parse(localStorage.getItem('projFilters') || 'null') || {
    ignore_color: false,
    min_parts: 10,
    max_parts: 2000,
    min_pct: 0,
    q: '',
  })
  const [list, setList] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    localStorage.setItem('projFilters', JSON.stringify(f))
    setLoading(true)
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ ...f, limit: 80 }).toString()
      api
        .get(`/api/projects?${qs}`)
        .then(setList)
        .catch((e) => notify(e.message, true))
        .finally(() => setLoading(false))
    }, 300)
    return () => clearTimeout(t)
  }, [f, notify])

  const upd = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

  return (
    <div className="view">
      <h2>Cosa posso costruire?</h2>
      <div className="card filters">
        <input placeholder="Cerca per nome, numero o tema" value={f.q} onChange={upd('q')} />
        <div className="row gap wrap">
          <label>
            Pezzi min <input type="number" className="qty" value={f.min_parts} onChange={upd('min_parts')} />
          </label>
          <label>
            max <input type="number" className="qty" value={f.max_parts} onChange={upd('max_parts')} />
          </label>
          <label>
            Completezza ≥ <input type="number" className="qty" value={f.min_pct} onChange={upd('min_pct')} />%
          </label>
          <label className="check">
            <input type="checkbox" checked={f.ignore_color} onChange={upd('ignore_color')} /> Ignora i colori
          </label>
        </div>
      </div>
      {loading && <p className="muted">Calcolo in corso…</p>}
      {list && list.length === 0 && !loading && (
        <p className="muted">Nessun set trovato. Aggiungi pezzi all'inventario o allarga i filtri.</p>
      )}
      <div className="grid">
        {list?.map((s) => (
          <button key={s.set_num} className="card set" onClick={() => go(`projects/${s.set_num}`)}>
            <Img src={s.img_url} className="set-img" />
            <div className="title">{s.name}</div>
            <div className="muted small">
              {s.set_num} · {s.year} · {s.theme}
            </div>
            <Pct value={s.pct} />
            <div className="small">
              Hai {s.have}/{s.total_qty} pezzi {s.has_manual ? '· 📘' : ''} {s.saved ? '· ⭐' : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function SetDetail({ setNum, notify }) {
  const [ignoreColor, setIgnoreColor] = useState(() => JSON.parse(localStorage.getItem('projFilters') || '{}').ignore_color || false)
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState(false)
  const [alts, setAlts] = useState(null)
  const [onlyMissing, setOnlyMissing] = useState(true)
  const [pdfUrl, setPdfUrl] = useState('')
  const fileRef = useRef()

  const load = useCallback(
    () => api.get(`/api/sets/${setNum}?ignore_color=${ignoreColor}`).then(setD).catch((e) => notify(e.message, true)),
    [setNum, ignoreColor, notify]
  )
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    api.get(`/api/sets/${setNum}/alternates`).then(setAlts).catch(() => setAlts(null))
  }, [setNum])

  const fetchManual = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/api/manuals/fetch/${setNum}`)
      notify(`${r.stored.length} libretti salvati`)
      load()
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  const upload = async (file, url) => {
    const fd = new FormData()
    fd.append('set_num', setNum)
    if (file) fd.append('file', file)
    if (url) fd.append('url', url)
    setBusy(true)
    try {
      await api.post('/api/manuals/upload', fd)
      notify('Istruzioni salvate')
      setPdfUrl('')
      if (!d.saved) await api.post(`/api/saved/${setNum}`, { saved: true })
      load()
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  const toggleSave = async () => {
    await api.post(`/api/saved/${setNum}`, { saved: !d.saved })
    load()
  }

  const copyElementIds = () => {
    const lines = missing.filter((p) => p.element_id).map((p) => `${p.element_id}\t${p.missing}`)
    const skipped = missing.length - lines.length
    navigator.clipboard
      ?.writeText(lines.join('\n'))
      .then(() => notify(`Copiati ${lines.length} element ID${skipped ? ` (${skipped} pezzi senza codice LEGO)` : ''}`))
      .catch(() => notify('Copia non riuscita', true))
  }

  if (!d) return <div className="view muted">Caricamento…</div>
  const missing = d.parts.filter((p) => p.missing > 0)
  const parts = onlyMissing ? missing : d.parts

  return (
    <div className="view">
      <button className="link" onClick={() => history.back()}>
        ← Indietro
      </button>
      <div className="set-head">
        <Img src={d.img_url} className="set-img-l" />
        <div className="grow">
          <h2>{d.name}</h2>
          <div className="muted">
            {d.set_num} · {d.year} · {d.theme} · {d.total_qty} pezzi
          </div>
          <Pct value={d.pct} />
          <div>
            Hai <b>{d.have}</b> pezzi su {d.total_qty}, ne mancano <b>{d.total_qty - d.have}</b>
          </div>
          <div className="row gap wrap mt">
            <button onClick={toggleSave}>{d.saved ? '⭐ Salvato' : '☆ Salva progetto'}</button>
            <a className="button" href={d.rebrickable_url} target="_blank" rel="noreferrer">
              Rebrickable ↗
            </a>
          </div>
          <label className="check mt">
            <input type="checkbox" checked={ignoreColor} onChange={(e) => setIgnoreColor(e.target.checked)} /> Ignora i colori
          </label>
        </div>
      </div>

      <div className="card">
        <h3>📘 Istruzioni di montaggio</h3>
        {d.manuals.length > 0 ? (
          <div className="list">
            {d.manuals.map((m) => (
              <a key={m.id} className="button" href={`/api/manuals/${m.id}/file`} target="_blank" rel="noreferrer">
                📄 {m.title} ({(m.size / 1048576).toFixed(1)} MB)
              </a>
            ))}
          </div>
        ) : (
          <p className="muted small">Nessuna istruzione salvata per questo set.</p>
        )}
        <div className="row gap wrap mt">
          <button className="primary" disabled={busy} onClick={fetchManual}>
            {busy ? 'Download…' : '⬇️ Scarica da LEGO.com'}
          </button>
          <button disabled={busy} onClick={() => fileRef.current.click()}>
            📤 Carica PDF
          </button>
          <a className="button" href={d.lego_instructions_url} target="_blank" rel="noreferrer">
            Pagina LEGO ↗
          </a>
        </div>
        <div className="row gap mt">
          <input placeholder="…oppure incolla l'URL di un PDF" value={pdfUrl} onChange={(e) => setPdfUrl(e.target.value)} />
          <button disabled={busy || !pdfUrl} onClick={() => upload(null, pdfUrl)}>
            Salva
          </button>
        </div>
        <input ref={fileRef} type="file" accept="application/pdf" hidden onChange={(e) => e.target.files[0] && upload(e.target.files[0])} />
      </div>

      {alts?.available && alts.results.length > 0 && (
        <div className="card">
          <h3>💡 Costruzioni alternative (MOC) con i pezzi di questo set</h3>
          <div className="grid">
            {alts.results.map((m) => (
              <a key={m.set_num} className="card set" href={m.moc_url} target="_blank" rel="noreferrer">
                <Img src={m.moc_img_url} className="set-img" />
                <div className="title">{m.name}</div>
                <div className="muted small">
                  {m.num_parts} pezzi · {m.designer_name}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <div className="row between wrap">
          <h3>Pezzi {onlyMissing ? `mancanti (${missing.length} tipi)` : `(${d.parts.length} tipi)`}</h3>
          <div className="row gap">
            <label className="check">
              <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} /> Solo mancanti
            </label>
            <button className="small" onClick={copyElementIds} disabled={!missing.length}>
              📋 Copia element ID
            </button>
            <a className="button" href={`/api/sets/${setNum}/missing.csv?ignore_color=${ignoreColor}`}>
              ⬇️ CSV mancanti
            </a>
          </div>
        </div>
        {missing.length > 0 && (
          <p className="muted small">
            🛒 <b>LEGO Pick a Brick</b>: clicca 🧱 sul pezzo o incolla gli element ID su lego.com · <b>BrickLink</b>: clicca
            🛒 (pagina del pezzo, scegli colore e venditore) o importa il CSV su Rebrickable → Buy Parts.
          </p>
        )}
        <div className="list">
          {parts.map((p) => (
            <div key={`${p.part_num}-${p.color_id}`} className={'inv' + (p.missing ? '' : ' ok')}>
              <Img src={p.img_url} className="thumb" />
              <div className="grow">
                <div className="title">{p.part_name}</div>
                <div className="muted small">
                  {p.part_num} · <Swatch rgb={p.rgb} /> {p.color_name}
                </div>
              </div>
              {p.missing > 0 && (
                <span className="row gap">
                  {p.element_id && (
                    <a
                      className="button small"
                      href={`https://www.lego.com/it-it/pick-and-build/pick-a-brick?query=${p.element_id}`}
                      target="_blank"
                      rel="noreferrer"
                      title={`LEGO Pick a Brick · element ${p.element_id}`}
                    >
                      🧱
                    </a>
                  )}
                  <a
                    className="button small"
                    href={`https://www.bricklink.com/v2/catalog/catalogitem.page?P=${p.part_num}${p.bl_id ? `&C=${p.bl_id}` : ''}`}
                    target="_blank"
                    rel="noreferrer"
                    title="Cerca su BrickLink"
                  >
                    🛒
                  </a>
                </span>
              )}
              <div className={'need ' + (p.missing ? 'miss' : 'have')}>
                {p.have}/{p.need}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ---------------- Identify ---------------- */
function IdentifyView({ colors, notify }) {
  const [parts, setParts] = useState([])
  const [ignoreColor, setIgnoreColor] = useState(false)
  const [partial, setPartial] = useState(false)
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [photo, setPhoto] = useState(null)
  const [build, setBuild] = useState(null)
  const [buildBusy, setBuildBusy] = useState(false)
  const camRef = useRef()
  const fileRef = useRef()

  const onPhoto = async (file) => {
    if (!file) return
    setPhoto(URL.createObjectURL(file))
    setBuild(null)
    setRes(null)
    setBuildBusy(true)
    const fd = new FormData()
    fd.append('image', file)
    try {
      setBuild(await api.post('/api/scan/build', fd))
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBuildBusy(false)
    }
  }

  useEffect(() => {
    try {
      const pre = JSON.parse(localStorage.getItem('identifyParts') || 'null')
      if (pre?.length) setParts(pre)
      localStorage.removeItem('identifyParts')
    } catch {}
  }, [])

  const addPart = (p) => {
    if (parts.some((x) => x.part_num === p.part_num)) return
    setParts([...parts, { part_num: p.part_num, name: p.name, img_url: p.img_url, color_id: null }])
  }

  const setPartColor = (partNum, color_id) => setParts((ps) => ps.map((x) => (x.part_num === partNum ? { ...x, color_id } : x)))
  const removePart = (partNum) => setParts((ps) => ps.filter((x) => x.part_num !== partNum))

  const sought = new Set(parts.map((p) => p.part_num)).size

  const search = async () => {
    if (!parts.length) return
    if (!ignoreColor && parts.some((p) => !p.color_id)) {
      return notify('Scegli il colore di ogni pezzo, oppure attiva "Ignora i colori"', true)
    }
    setBusy(true)
    try {
      const r = await api.post('/api/sets/identify', {
        parts: parts.map((p) => ({ part_num: p.part_num, color_id: p.color_id || 0 })),
        ignore_color: ignoreColor,
        min_match: partial && sought > 1 ? sought - 1 : sought,
      })
      setRes(r)
      if (!r.results.length) notify('Nessun set trovato: prova la corrispondenza parziale o ignora i colori', true)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="view">
      <h2>🔍 Da quale set viene?</h2>
      <div className="card">
        <h3>📷 Fotografa una costruzione completa</h3>
        <p className="muted small">
          Inquadra il set o la minifigure già montata: l'IA la riconosce e ti dice da quale set proviene.
        </p>
        <div className="row gap">
          <button className="primary big" disabled={buildBusy} onClick={() => camRef.current.click()}>
            📷 Scatta foto
          </button>
          <button className="big" disabled={buildBusy} onClick={() => fileRef.current.click()}>
            🖼️ Carica immagine
          </button>
        </div>
        <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => onPhoto(e.target.files[0])} />
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => onPhoto(e.target.files[0])} />
        {photo && (
          <div className="photo-wrap">
            <img src={photo} alt="foto costruzione" />
            {buildBusy && <div className="overlay">Riconoscimento in corso…</div>}
          </div>
        )}
        {build && (
          <>
            {build.candidates?.length > 0 ? (
              <div className="list mt">
                {build.candidates.map((s) => (
                  <button
                    key={s.set_num}
                    className="card inv"
                    disabled={!s.in_catalog}
                    title={s.in_catalog ? '' : 'Set non presente nel catalogo'}
                    onClick={() => s.in_catalog && go(`projects/${s.set_num}`)}
                  >
                    <Img src={s.img_url} className="thumb" />
                    <div className="grow">
                      <div className="title">{s.name}</div>
                      <div className="muted small">
                        {s.set_num}
                        {s.year ? ` · ${s.year}` : ''}
                        {s.theme ? ` · ${s.theme}` : ''}
                        {s.total_qty ? ` · ${s.total_qty} pezzi` : ''}
                        {!s.in_catalog ? ' · non in catalogo' : ''}
                      </div>
                    </div>
                    <span className={'badge match ' + (s.score >= 0.5 ? 'full' : '')}>{Math.round(s.score * 100)}%</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted mt">Nessun set riconosciuto.</p>
            )}
            {build.figs?.length > 0 && (
              <p className="muted small mt">
                Potrebbe essere una minifigure:{' '}
                {build.figs.map((f) => `${f.name} (${Math.round(f.score * 100)}%)`).join(', ')}
              </p>
            )}
            {build.error && <p className="error small">{build.error}</p>}
          </>
        )}
      </div>
      <div className="card">
        <h3>🧱 …oppure cerca dai singoli pezzi</h3>
        <p className="muted small">
          Aggiungi i pezzi che hai trovato: l'app cerca i set che li contengono tutti. Con 2-3 pezzi distintivi la lista si
          restringe molto.
        </p>
        <PartSearch placeholder="Aggiungi un pezzo per nome o codice…" onPick={addPart} />
        {parts.length > 0 && (
          <div className="list mt">
            {parts.map((p) => (
              <div key={p.part_num} className="inv">
                <Img src={p.img_url} className="thumb-s" />
                <div className="grow">
                  <div className="title">{p.name || p.part_num}</div>
                  <div className="muted small">{p.part_num}</div>
                </div>
                {!ignoreColor && (
                  <ColorSelect colors={colors} value={p.color_id} onChange={(v) => setPartColor(p.part_num, v)} />
                )}
                <button className="small" onClick={() => removePart(p.part_num)}>
                  🗑
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="row gap wrap mt">
          <label className="check">
            <input type="checkbox" checked={ignoreColor} onChange={(e) => setIgnoreColor(e.target.checked)} /> Ignora i colori
          </label>
          <label className="check">
            <input type="checkbox" checked={partial} onChange={(e) => setPartial(e.target.checked)} disabled={sought < 2} />{' '}
            Corrispondenza parziale
          </label>
          <button className="primary" disabled={busy || !parts.length} onClick={search}>
            {busy ? 'Cerco…' : '🔍 Trova il set'}
          </button>
        </div>
      </div>

      {res && (
        <>
          <p className="muted">
            {res.results.length === 0
              ? 'Nessun set contiene questa combinazione di pezzi.'
              : `${res.results.length}${res.results.length >= 60 ? '+' : ''} set candidati (${res.sought} pezzi cercati)`}
          </p>
          <div className="grid">
            {res.results.map((s) => (
              <button key={s.set_num} className="card set" onClick={() => go(`projects/${s.set_num}`)}>
                <Img src={s.img_url} className="set-img" />
                <div className="title">{s.name}</div>
                <div className="muted small">
                  {s.set_num} · {s.year} · {s.theme}
                </div>
                <span className={'badge match ' + (s.matched === res.sought ? 'full' : '')}>
                  {s.matched}/{res.sought} pezzi
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/* ---------------- Manuals ---------------- */
function ManualsView({ notify }) {
  const [saved, setSaved] = useState([])
  const [manuals, setManuals] = useState([])
  const load = useCallback(() => {
    api.get('/api/saved').then(setSaved).catch((e) => notify(e.message, true))
    api.get('/api/manuals').then(setManuals).catch((e) => notify(e.message, true))
  }, [notify])
  useEffect(() => {
    load()
  }, [load])

  const remove = async (m) => {
    if (!confirm(`Eliminare "${m.title}"?`)) return
    await api.del(`/api/manuals/${m.id}`)
    load()
  }

  const bySet = manuals.reduce((acc, m) => ((acc[m.set_num] = acc[m.set_num] || []).push(m), acc), {})

  return (
    <div className="view">
      <h2>Le mie istruzioni</h2>
      {manuals.length === 0 && <p className="muted">Nessuna istruzione salvata. Apri un progetto e scarica o carica il manuale.</p>}
      <div className="list">
        {Object.entries(bySet).map(([setNum, ms]) => (
          <div key={setNum} className="card inv">
            <Img src={ms[0].img_url} className="thumb" />
            <div className="grow">
              <button className="link title" onClick={() => go(`projects/${setNum}`)}>
                {ms[0].set_name || setNum}
              </button>
              <div className="muted small">{setNum}</div>
              <div className="row gap wrap mt">
                {ms.map((m) =>
                  m.available ? (
                    <span key={m.id} className="row">
                      <a className="button small" href={`/api/manuals/${m.id}/file`} target="_blank" rel="noreferrer">
                        📄 {m.title}
                      </a>
                      <button className="small" onClick={() => remove(m)} title="Elimina">
                        🗑
                      </button>
                    </span>
                  ) : (
                    <span key={m.id} className="muted small">
                      {m.title} (file non ancora sincronizzato)
                    </span>
                  )
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      {saved.filter((s) => !s.manuals).length > 0 && (
        <>
          <h3>Progetti salvati senza istruzioni</h3>
          <div className="list">
            {saved
              .filter((s) => !s.manuals)
              .map((s) => (
                <button key={s.set_num} className="card inv" onClick={() => go(`projects/${s.set_num}`)}>
                  <Img src={s.img_url} className="thumb" />
                  <div className="grow">
                    <div className="title">{s.name}</div>
                    <div className="muted small">
                      {s.set_num} · {s.total_qty} pezzi
                    </div>
                  </div>
                </button>
              ))}
          </div>
        </>
      )}
    </div>
  )
}

/* ---------------- Settings ---------------- */
function SettingsView({ status, refresh, notify, me, onLogout }) {
  const [peer, setPeer] = useState('')
  const [peerToken, setPeerToken] = useState('')
  const [peers, setPeers] = useState([])
  const [syncToken, setSyncToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState(status?.device_name || '')
  const restoreRef = useRef()

  const [keyInfo, setKeyInfo] = useState(null)
  const [rbKey, setRbKey] = useState('')

  useEffect(() => {
    api.get('/api/sync/peers').then(setPeers).catch(() => {})
    api.get('/api/settings/sync-token').then((r) => setSyncToken(r.token)).catch(() => {})
    api.get('/api/settings/rebrickable-key').then(setKeyInfo).catch(() => {})
  }, [])

  const saveKey = async () => {
    try {
      await api.post('/api/settings/rebrickable-key', { key: rbKey.trim() })
      setRbKey('')
      setKeyInfo(await api.get('/api/settings/rebrickable-key'))
      refresh()
      notify('Chiave salvata')
    } catch (e) {
      notify(e.message, true)
    }
  }

  const removeKey = async () => {
    await api.post('/api/settings/rebrickable-key', { key: '' })
    setKeyInfo(await api.get('/api/settings/rebrickable-key'))
    refresh()
    notify('Chiave rimossa')
  }
  useEffect(() => setName(status?.device_name || ''), [status?.device_name])

  const regenToken = async () => {
    if (!confirm('Rigenerare il token? Quello attuale smetterà di funzionare sulle altre installazioni.')) return
    const r = await api.post('/api/settings/sync-token/regenerate')
    setSyncToken(r.token)
    notify('Token rigenerato')
  }

  const syncPeer = async (url, token) => {
    setBusy(true)
    try {
      const r = await api.post('/api/sync/peer', { url, token: token || '' })
      notify(`Sincronizzato: ricevuti ${r.pulled.inventory} pezzi, inviati ${r.pushed.inventory ?? 0}`)
      api.get('/api/sync/peers').then(setPeers)
      refresh()
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  const restore = async (file) => {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await api.post('/api/sync/restore', fd)
      notify(`Backup unito: ${r.merged.inventory} righe inventario, ${r.merged.manuals} manuali`)
      refresh()
    } catch (e) {
      notify(e.message, true)
    }
  }

  const c = status?.catalog
  return (
    <div className="view">
      <h2>Impostazioni</h2>
      <div className="card">
        <h3>👤 Account</h3>
        <div className="row between wrap">
          <span>
            Accesso come <b>{me.username}</b>
            {me.is_admin ? ' (amministratore)' : ''}
          </span>
          <button onClick={onLogout}>Esci</button>
        </div>
        <h3 className="mt">Token di sincronizzazione</h3>
        <p className="muted small">
          Serve a sincronizzare questo account con un'altra installazione di Brick Scanner: incollalo nel campo "token"
          dell'altro dispositivo.
        </p>
        <div className="row gap">
          <input readOnly value={syncToken} onFocus={(e) => e.target.select()} />
          <button
            className="small"
            onClick={() => navigator.clipboard?.writeText(syncToken).then(() => notify('Token copiato'))}
            title="Copia"
          >
            📋
          </button>
          <button className="small" onClick={regenToken} title="Rigenera">
            🔄
          </button>
        </div>
      </div>
      <div className="card">
        <h3>Catalogo LEGO (Rebrickable)</h3>
        {c && (
          <>
            <p>
              Stato: <b>{c.state === 'ready' ? 'pronto' : c.state === 'running' ? 'importazione in corso' : c.state}</b>
              {c.sets ? ` · ${c.sets.toLocaleString('it-IT')} set` : ''}
              {c.imported_at ? ` · aggiornato il ${new Date(c.imported_at * 1000).toLocaleDateString('it-IT')}` : ''}
            </p>
            {c.state === 'running' && (
              <>
                <Pct value={Math.round(c.progress * 100)} />
                <p className="muted small">{c.message}</p>
              </>
            )}
            {c.error && <p className="error small">{c.error}</p>}
          </>
        )}
        <button
          disabled={c?.state === 'running'}
          onClick={() => api.post('/api/catalog/import').then(() => setTimeout(refresh, 500))}
        >
          🔄 Aggiorna catalogo
        </button>
      </div>

      <div className="card">
        <h3>Chiave API Rebrickable</h3>
        <p className="muted small">Personale: vale solo per il tuo account ({me.username}).</p>
        {status?.rebrickable_key ? (
          <p>
            ✅ Configurata{keyInfo?.source === 'ui' ? ` (${keyInfo.masked})` : keyInfo?.source === 'env' ? ' (da .env)' : ''}.
            Abilita le costruzioni alternative (MOC) e una mappatura più precisa di pezzi e colori.
          </p>
        ) : (
          <>
            <p>⚠️ Non configurata (opzionale). L'app funziona comunque; con la chiave ottieni anche le costruzioni alternative (MOC).</p>
            <ol className="small">
              <li>
                Registrati su{' '}
                <a href="https://rebrickable.com/signup/" target="_blank" rel="noreferrer">
                  rebrickable.com
                </a>
              </li>
              <li>
                Vai su{' '}
                <a href="https://rebrickable.com/users/me/settings/#api" target="_blank" rel="noreferrer">
                  Settings → API
                </a>{' '}
                e clicca “Generate new key”
              </li>
              <li>Incolla la chiave qui sotto</li>
            </ol>
          </>
        )}
        <div className="row gap mt">
          <input
            placeholder={keyInfo?.source === 'ui' ? `Sostituisci la chiave salvata (${keyInfo.masked})` : 'Incolla qui la chiave API'}
            value={rbKey}
            onChange={(e) => setRbKey(e.target.value)}
          />
          <button className="primary" disabled={!rbKey.trim()} onClick={saveKey}>
            Salva
          </button>
          {keyInfo?.source === 'ui' && <button onClick={removeKey}>Rimuovi</button>}
        </div>
      </div>

      <div className="card">
        <h3>Questo dispositivo</h3>
        <div className="row gap">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome dispositivo" />
          <button onClick={() => api.post('/api/settings/device-name', { name }).then(refresh)}>Salva</button>
        </div>
        <p className="muted small">ID: {status?.device_id}</p>
      </div>

      <div className="card">
        <h3>🔁 Sincronizza con un altro dispositivo</h3>
        <p className="muted small">
          Inserisci l'indirizzo di un'altra installazione di Brick Scanner e il token del tuo utente su quell'installazione
          (Impostazioni → Token di sincronizzazione). Inventario, progetti salvati e istruzioni vengono uniti in entrambe le
          direzioni.
        </p>
        <div className="row gap">
          <input value={peer} onChange={(e) => setPeer(e.target.value)} placeholder="https://indirizzo:8080" />
          <button className="primary" disabled={busy || !peer} onClick={() => syncPeer(peer, peerToken)}>
            {busy ? 'Sincronizzo…' : 'Sincronizza'}
          </button>
        </div>
        <input
          className="mt"
          value={peerToken}
          onChange={(e) => setPeerToken(e.target.value)}
          placeholder="Token del tuo utente sull'altra installazione"
        />
        {peers.length > 0 && (
          <div className="list mt">
            {peers.map((p) => (
              <div key={p.url} className="row between">
                <span className="small">
                  {p.url} · {p.last_sync ? new Date(p.last_sync).toLocaleString('it-IT') : 'mai'}
                </span>
                <button className="small" disabled={busy} onClick={() => syncPeer(p.url, p.token || '')}>
                  Sincronizza ora
                </button>
              </div>
            ))}
          </div>
        )}
        <h3 className="mt">Backup su file</h3>
        <p className="muted small">In alternativa: esporta un file .zip e importalo sull'altro dispositivo (i dati vengono uniti, non sovrascritti).</p>
        <div className="row gap wrap">
          <a className="button" href="/api/sync/backup">
            ⬇️ Esporta backup
          </a>
          <button onClick={() => restoreRef.current.click()}>📤 Importa backup</button>
          <input ref={restoreRef} type="file" accept=".zip" hidden onChange={(e) => e.target.files[0] && restore(e.target.files[0])} />
        </div>
      </div>
    </div>
  )
}

/* ---------------- Login ---------------- */
const BRICK_COLORS = ['#d01012', '#ffcf00', '#006db7', '#00a550', '#fe8a18', '#e3008c', '#7dc142']

function LoginView({ onLogin }) {
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [shake, setShake] = useState(false)

  const bricks = useMemo(
    () =>
      Array.from({ length: 16 }, (_, i) => ({
        left: `${(i * 67) % 96}%`,
        color: BRICK_COLORS[i % BRICK_COLORS.length],
        w: 34 + (i % 4) * 18,
        delay: `${-((i * 1.7) % 12).toFixed(1)}s`,
        dur: `${(7 + ((i * 2.3) % 7)).toFixed(1)}s`,
      })),
    []
  )

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await api.post('/api/login', { username: u.trim(), password: p })
      onLogin(await api.get('/api/me'))
    } catch (ex) {
      setErr(ex.message)
      setShake(true)
      setTimeout(() => setShake(false), 500)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      {bricks.map((b, i) => (
        <div
          key={i}
          className="login-brick"
          style={{ left: b.left, width: b.w, height: b.w * 0.55, background: b.color, animationDuration: b.dur, animationDelay: b.delay }}
        />
      ))}
      <form className={'login-card' + (shake ? ' shake' : '')} onSubmit={submit}>
        <div className="studs">
          <i />
          <i />
          <i />
          <i />
        </div>
        <div className="login-title">🧱 Brick Scanner</div>
        <p className="muted small login-sub">Fotografa, colleziona, costruisci!</p>
        <input autoFocus autoComplete="username" placeholder="Utente" value={u} onChange={(e) => setU(e.target.value)} />
        <div className="pw-wrap">
          <input
            type={showPw ? 'text' : 'password'}
            autoComplete="current-password"
            placeholder="Password"
            value={p}
            onChange={(e) => setP(e.target.value)}
          />
          <button
            type="button"
            className="pw-eye"
            onClick={() => setShowPw(!showPw)}
            title={showPw ? 'Nascondi password' : 'Mostra password'}
            tabIndex={-1}
          >
            {showPw ? '🙈' : '👁️'}
          </button>
        </div>
        {err && <p className="error small">{err}</p>}
        <button className="primary big mt" disabled={busy || !u || !p}>
          {busy ? 'Costruisco…' : '🧱 Entra'}
        </button>
      </form>
    </div>
  )
}

/* ---------------- Admin ---------------- */
function AdminView({ notify }) {
  const [users, setUsers] = useState(null)
  const [nu, setNu] = useState({ username: '', password: '', is_admin: false })

  const load = useCallback(() => api.get('/api/admin/users').then(setUsers).catch((e) => notify(e.message, true)), [notify])
  useEffect(() => {
    load()
  }, [load])

  const create = async (e) => {
    e.preventDefault()
    try {
      await api.post('/api/admin/users', nu)
      notify(`Utente "${nu.username}" creato`)
      setNu({ username: '', password: '', is_admin: false })
      load()
    } catch (ex) {
      notify(ex.message, true)
    }
  }

  const resetPw = async (u) => {
    const password = prompt(`Nuova password per "${u.username}":`)
    if (!password) return
    try {
      await api.post(`/api/admin/users/${u.username}/password`, { password })
      notify('Password aggiornata')
    } catch (ex) {
      notify(ex.message, true)
    }
  }

  const remove = async (u) => {
    if (!confirm(`Eliminare l'utente "${u.username}"? Il suo inventario sul server non viene cancellato.`)) return
    try {
      await api.del(`/api/admin/users/${u.username}`)
      notify('Utente eliminato')
      load()
    } catch (ex) {
      notify(ex.message, true)
    }
  }

  return (
    <div className="view">
      <h2>🛠️ Amministrazione</h2>
      <div className="card">
        <h3>Utenti</h3>
        <div className="list">
          {users?.map((u) => (
            <div key={u.username} className="inv">
              <div className="grow">
                <div className="title">
                  {u.username} {!!u.is_admin && <span className="badge">admin</span>}
                </div>
                <div className="muted small">
                  {u.pieces} pezzi · {u.manuals} manuali · creato il {new Date(u.created_at).toLocaleDateString('it-IT')}
                </div>
              </div>
              <button className="small" onClick={() => resetPw(u)}>
                🔑 Password
              </button>
              <button className="small" onClick={() => remove(u)}>
                🗑
              </button>
            </div>
          ))}
          {users && users.length === 0 && <p className="muted">Nessun utente.</p>}
        </div>
      </div>
      <form className="card" onSubmit={create}>
        <h3>Crea utente</h3>
        <input
          placeholder="Nome utente (minuscole, numeri, . _ -)"
          value={nu.username}
          onChange={(e) => setNu({ ...nu, username: e.target.value })}
        />
        <input className="mt" placeholder="Password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
        <label className="check">
          <input type="checkbox" checked={nu.is_admin} onChange={(e) => setNu({ ...nu, is_admin: e.target.checked })} /> Amministratore
        </label>
        <button className="primary" disabled={!nu.username || !nu.password}>
          Crea utente
        </button>
      </form>
      <p className="muted small">Ogni utente ha inventario, manuali e progetti salvati completamente separati.</p>
    </div>
  )
}

/* ---------------- App ---------------- */
export default function App() {
  const route = useHashTab()
  const [me, setMe] = useState(undefined)
  const [status, setStatus] = useState(null)
  const [colors, setColors] = useState([])
  const [toast, setToast] = useState(null)
  const timer = useRef()

  const notify = useCallback((text, error = false) => {
    setToast({ text, error })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), error ? 6000 : 3000)
  }, [])

  useEffect(() => {
    api.get('/api/me').then(setMe).catch(() => setMe(null))
  }, [])
  useEffect(() => {
    const on = () => setMe(null)
    window.addEventListener('bs-unauthorized', on)
    return () => window.removeEventListener('bs-unauthorized', on)
  }, [])

  const logout = async () => {
    try {
      await api.post('/api/logout')
    } catch {}
    setMe(null)
  }

  const refresh = useCallback(() => api.get('/api/status').then(setStatus).catch(() => {}), [])
  useEffect(() => {
    if (me) refresh()
  }, [me, refresh])

  const catalogReady = status?.catalog?.state === 'ready'
  useEffect(() => {
    if (status && !catalogReady) {
      const t = setTimeout(refresh, 2000)
      return () => clearTimeout(t)
    }
  }, [status, catalogReady, refresh])
  useEffect(() => {
    if (catalogReady) api.get('/api/colors').then(setColors).catch(() => {})
  }, [catalogReady])

  if (me === undefined) return <div className="splash">🧱</div>
  if (me === null) return <LoginView onLogin={setMe} />

  const [tab, sub] = route
  let view
  if (tab === 'inventory') view = <InventoryView colors={colors} notify={notify} />
  else if (tab === 'projects' && sub) view = <SetDetail setNum={decodeURIComponent(sub)} notify={notify} />
  else if (tab === 'projects') view = <ProjectsView notify={notify} />
  else if (tab === 'identify') view = <IdentifyView colors={colors} notify={notify} />
  else if (tab === 'manuals') view = <ManualsView notify={notify} />
  else if (tab === 'settings') view = <SettingsView status={status} refresh={refresh} notify={notify} me={me} onLogout={logout} />
  else if (tab === 'admin' && me.is_admin) view = <AdminView notify={notify} />
  else view = <ScanView colors={colors} notify={notify} catalogReady={catalogReady} />

  const tabs = me.is_admin ? [...TABS, ['admin', 'Admin', '🛠️']] : TABS

  return (
    <div className="app">
      <header>
        <span className="logo">🧱 Brick Scanner</span>
        <span className="row gap">
          {status && <span className="small header-stat">{status.inventory.total} pezzi</span>}
          <span className="small header-user">{me.username}</span>
        </span>
      </header>
      <main>{view}</main>
      <nav>
        {tabs.map(([id, label, icon]) => (
          <button key={id} className={tab === id || (!tab && id === 'scan') ? 'active' : ''} onClick={() => go(id)}>
            <span className="icon">{icon}</span>
            <span className="label">{label}</span>
          </button>
        ))}
      </nav>
      <Toast toast={toast} />
    </div>
  )
}
