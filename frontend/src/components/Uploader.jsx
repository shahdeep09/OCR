import React, { useRef, useState } from 'react'
import { api } from '../api.js'

const MAX = 5

export default function Uploader({ onUploaded }) {
  const inputRef = useRef(null)
  const [picked, setPicked] = useState([])
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

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
            {picked.map((f, i) => <li key={i}>{f.name}</li>)}
          </ul>
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
    </div>
  )
}
