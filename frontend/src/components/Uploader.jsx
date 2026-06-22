import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

const MAX = 5

export default function Uploader({ onUploaded }) {
  const inputRef = useRef(null)
  const [picked, setPicked] = useState([])
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
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
      setError('Please drop PDF files.')
      return
    }
    if (arr.length > MAX) {
      setError(`Up to ${MAX} PDFs per upload.`)
      return
    }
    setError('')
    setPicked(arr)
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  const submit = async () => {
    if (picked.length === 0) return
    setUploading(true)
    setError('')
    try {
      const created = await api.upload(picked)
      setPicked([])
      onUploaded(created)
    } catch (e) {
      setError(e.message || 'Upload failed')
    } finally {
      setUploading(false)
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
  const bigUpload = totalMB > 80

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
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {picked.length > 0 && (
        <div className="picked">
          <strong>{picked.length} file{picked.length > 1 ? 's' : ''} ready:</strong>
          <ul>
            {picked.map((f, i) => (
              <li key={i}>{f.name} <span className="hint">({(f.size / 1048576).toFixed(1)} MB)</span></li>
            ))}
          </ul>
          {bigUpload && (
            <div className="warn-note">
              ⚠ Large upload ({totalMB.toFixed(0)} MB). Big files can fail through the
              proxy — if it does, use “Process a file already on the server” below.
            </div>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}

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
