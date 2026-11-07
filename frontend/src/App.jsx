import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'

const TABS = [
  ['scan', 'Scansiona', '📷'],
  ['inventory', 'Pezzi', '🧱'],
  ['projects', 'Progetti', '🏗️'],
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
          <h3>Pezzi riconosciuti ({rows.length})</h3>
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
            <a className="button" href={`/api/sets/${setNum}/missing.csv?ignore_color=${ignoreColor}`}>
              ⬇️ CSV mancanti
            </a>
          </div>
        </div>
        <p className="muted small">Il CSV si può importare su Rebrickable o BrickLink per ordinare i pezzi mancanti.</p>
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
function SettingsView({ status, refresh, notify }) {
  const [peer, setPeer] = useState('')
  const [peers, setPeers] = useState([])
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState(status?.device_name || '')
  const restoreRef = useRef()

  useEffect(() => {
    api.get('/api/sync/peers').then(setPeers).catch(() => {})
  }, [])
  useEffect(() => setName(status?.device_name || ''), [status?.device_name])

  const syncPeer = async (url) => {
    setBusy(true)
    try {
      const r = await api.post('/api/sync/peer', { url })
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
        {status?.rebrickable_key ? (
          <p>✅ Configurata. Abilita le costruzioni alternative (MOC) e una mappatura più precisa di pezzi e colori.</p>
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
              <li>
                Inserisci la chiave nel file <code>.env</code> come <code>REBRICKABLE_API_KEY=...</code> e riavvia il container
              </li>
            </ol>
          </>
        )}
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
          Inserisci l'indirizzo di un'altra installazione di Brick Scanner (es. <code>http://192.168.1.20:8080</code>). Inventario,
          progetti salvati e istruzioni vengono uniti in entrambe le direzioni.
        </p>
        <div className="row gap">
          <input value={peer} onChange={(e) => setPeer(e.target.value)} placeholder="http://indirizzo:8080" />
          <button className="primary" disabled={busy || !peer} onClick={() => syncPeer(peer)}>
            {busy ? 'Sincronizzo…' : 'Sincronizza'}
          </button>
        </div>
        {peers.length > 0 && (
          <div className="list mt">
            {peers.map((p) => (
              <div key={p.url} className="row between">
                <span className="small">
                  {p.url} · {p.last_sync ? new Date(p.last_sync).toLocaleString('it-IT') : 'mai'}
                </span>
                <button className="small" disabled={busy} onClick={() => syncPeer(p.url)}>
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

/* ---------------- App ---------------- */
export default function App() {
  const route = useHashTab()
  const [status, setStatus] = useState(null)
  const [colors, setColors] = useState([])
  const [toast, setToast] = useState(null)
  const timer = useRef()

  const notify = useCallback((text, error = false) => {
    setToast({ text, error })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), error ? 6000 : 3000)
  }, [])

  const refresh = useCallback(() => api.get('/api/status').then(setStatus).catch(() => {}), [])
  useEffect(() => {
    refresh()
  }, [refresh])

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

  const [tab, sub] = route
  let view
  if (tab === 'inventory') view = <InventoryView colors={colors} notify={notify} />
  else if (tab === 'projects' && sub) view = <SetDetail setNum={decodeURIComponent(sub)} notify={notify} />
  else if (tab === 'projects') view = <ProjectsView notify={notify} />
  else if (tab === 'manuals') view = <ManualsView notify={notify} />
  else if (tab === 'settings') view = <SettingsView status={status} refresh={refresh} notify={notify} />
  else view = <ScanView colors={colors} notify={notify} catalogReady={catalogReady} />

  return (
    <div className="app">
      <header>
        <span className="logo">🧱 Brick Scanner</span>
        {status && (
          <span className="small header-stat">
            {status.inventory.total} pezzi
          </span>
        )}
      </header>
      <main>{view}</main>
      <nav>
        {TABS.map(([id, label, icon]) => (
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
