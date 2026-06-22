import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

const MAX = 5

export default function Uploader({ onUploaded }) {
  const inputRef = useRef(null)
  const [picked, setPicked] = useState([])
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(null) // {name, idx, of, pct}
  const [error, setError] = useState('')

  // Pod-side inbox (for big files that can't go through the browser/proxy)
  const [inbox, setInbox] = useState([])
  const [ingestingName, setIngestingName] = useState('')

  const refreshInbox = async () => {
    try {
      const items = await api.getInbox()
      setInbox(items || [])
    } catch {
      setInbox([])
    }
  }

  useEffect(() => { refreshInbox() }, [])

  const handleFiles = (files) => {
    const arr = Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.pdf'))
    if (arr.length === 0) {
      setError('Please choose PDF files.')
      return
    }
    setError('')
    // Accumulate across multiple drops/picks (dedupe by name+size), cap at MAX.
    setPicked((prev) => {
      const combined = [...prev]
      for (const f of arr) {
        if (!combined.some((g) => g.name === f.name && g.size === f.size)) {
          combined.push(f)
        }
      }
      if (combined.length > MAX) {
        setError(`Up to ${MAX} PDFs at a time — keeping the first ${MAX}.`)
        return combined.slice(0, MAX)
      }
      return combined
    })
  }

  const removeFile = (i) => setPicked((prev) => prev.filter((_, idx) => idx !== i))

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  const submit = async () => {
    if (picked.length === 0) return
    setUploading(true)
    setError('')
    const created = []
    const failed = []
    for (let idx = 0; idx < picked.length; idx++) {
      const f = picked[idx]
      const onPct = (pct) => setProgress((p) => (p ? { ...p, pct } : p))
      setProgress({ name: f.name, idx: idx + 1, of: picked.length, pct: 0, chunked: false })
      try {
        let items
        try {
          // Fast path: one streaming request. Over a Direct TCP backend this
          // works for any size; over the RunPod proxy it works up to ~70MB.
          items = await api.uploadOne(f, onPct)
        } catch (e1) {
          // Single request failed (proxy dropped a big POST) — fall back to
          // chunked so it still completes. No-op when on a Direct TCP backend.
          setProgress((p) => (p ? { ...p, pct: 0, chunked: true } : p))
          items = await api.uploadChunked(f, onPct)
        }
        created.push(...(items || []))
      } catch (e) {
        // One bad file shouldn't abort the rest — record it and keep going.
        failed.push(`${f.name}: ${e.message || 'failed'}`)
      }
    }
    setUploading(false)
    setProgress(null)
    if (failed.length) setError(`Failed — ${failed.join(' · ')}`)
    if (created.length) {
      setPicked([])
      onUploaded(created)
    }
  }

  const ingest = async (filename) => {
    setIngestingName(filename)
    setError('')
    try {
      const created = await api.ingestFile(filename)
      onUploaded([created])
    } catch (e) {
      setError(e.message || 'Ingest failed')
    } finally {
      setIngestingName('')
    }
  }

  const totalMB = picked.reduce((s, f) => s + f.size, 0) / 1048576

  return (
    <div className="card uploader">
      <h2>Upload PDFs</h2>
      <p>Up to {MAX} files. Hindi · Gujarati · English supported.</p>
      <div
        className={`drop-zone ${dragging ? 'dragging' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        style={{ cursor: 'pointer' }}
      >
        <div><strong>Click to choose</strong> or drop PDF files here</div>
        <div className="hint">Max {MAX} files · .pdf only</div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
        />
      </div>

      {picked.length > 0 && (
        <div className="picked">
          <strong>{picked.length} of {MAX} file{picked.length > 1 ? 's' : ''} ready:</strong>
          <ul>
            {picked.map((f, i) => (
              <li key={i}>
                {f.name} <span className="hint">({(f.size / 1048576).toFixed(1)} MB)</span>
                {!uploading && (
                  <button className="link-btn" title="Remove" onClick={() => removeFile(i)}>×</button>
                )}
              </li>
            ))}
          </ul>
          <div className="hint" style={{ marginTop: 6 }}>
            Total {totalMB.toFixed(1)} MB · they process one after another.
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {progress && (
        <div className="upload-progress">
          <div className="hint">
            Uploading {progress.idx} of {progress.of}: {progress.name}
            {progress.chunked ? ' · large file (chunked, slower but reliable)' : ''}
          </div>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
          </div>
          <div className="hint">{progress.pct}%</div>
        </div>
      )}

      <div style={{ marginTop: 18, display: 'flex', gap: 8, justifyContent: 'center' }}>
        <button
          className="primary"
          disabled={picked.length === 0 || uploading}
          onClick={submit}
        >
          {uploading ? 'Uploading…' : `Start OCR (${picked.length})`}
        </button>
        {picked.length > 0 && (
          <button onClick={() => setPicked([])} disabled={uploading}>Clear</button>
        )}
      </div>

      {/* Pod-side ingest — for files too big for a browser upload */}
      <div className="inbox-section">
        <div className="inbox-header">
          <strong>Or process a file already on the server</strong>
          <button className="link-btn" onClick={refreshInbox} title="Refresh list">↻</button>
        </div>
        <div className="hint" style={{ marginBottom: 8 }}>
          For 50–200 MB books: drop the PDF into the server’s <code>inbox/</code> folder
          (RunPod file manager / Jupyter / scp), then process it here — no upload needed.
        </div>
        {inbox.length === 0 && (
          <div className="hint">No files in the server inbox.</div>
        )}
        {inbox.length > 0 && (
          <ul className="inbox-list">
            {inbox.map((it) => (
              <li key={it.filename}>
                <span className="inbox-name" title={it.filename}>{it.filename}</span>
                <span className="hint">{it.size_mb} MB</span>
                <button
                  className="primary"
                  disabled={!!ingestingName}
                  onClick={() => ingest(it.filename)}
                >
                  {ingestingName === it.filename ? 'Starting…' : 'Process'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
