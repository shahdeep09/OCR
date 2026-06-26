import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

// Confidence cut-offs offered in the dropdown. Lower = stricter = flags more.
const LEVELS = [60, 70, 80, 85, 90]

// Stored timestamps are naive UTC; append 'Z' so the browser shows local time.
const fmt = (s) =>
  s ? new Date(s + 'Z').toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—'

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function ProofreadPanel() {
  const [reports, setReports] = useState([])
  const [generatedAt, setGeneratedAt] = useState(null)
  const [native, setNative] = useState(false)
  const [minConf, setMinConf] = useState(80)
  const [selected, setSelected] = useState(() => new Set())
  const [running, setRunning] = useState(false)
  const [zipping, setZipping] = useState(false)
  const [error, setError] = useState('')

  // Show the previous run (if any) on mount, without re-analyzing.
  useEffect(() => {
    api.getProofreadReports()
      .then((data) => {
        setReports(data.reports || [])
        setGeneratedAt(data.generated_at || null)
        setNative(!!data.native_confidence)
        if (data.min_confidence) setMinConf(data.min_confidence)
      })
      .catch(() => {})
  }, [])

  const run = async () => {
    setRunning(true)
    setError('')
    setSelected(new Set())
    try {
      const data = await api.runProofread(minConf)
      setReports(data.reports || [])
      setGeneratedAt(data.generated_at || null)
      setNative(!!data.native_confidence)
    } catch (e) {
      setError(e.message || 'Failed to run analysis')
    } finally {
      setRunning(false)
    }
  }

  const toggle = (name) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })

  const allSelected = reports.length > 0 && selected.size === reports.length
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(reports.map((r) => r.csv_name)))

  const downloadZip = async (names) => {
    setZipping(true)
    setError('')
    try {
      const blob = await api.proofreadZip(names)
      triggerDownload(blob, 'bookscan-proofread.zip')
    } catch (e) {
      setError(e.message || 'Download failed')
    } finally {
      setZipping(false)
    }
  }

  const totalFlagged = reports.reduce((s, r) => s + (r.flagged || 0), 0)

  return (
    <div className="card proofread">
      <h2>🔍 Proofreading confidence</h2>
      <p>
        Score every page of every book and get a CSV per book — worst pages first —
        so you only check the low-confidence ones.
      </p>

      <div className="proofread-controls">
        <label className="hint">
          Flag below{' '}
          <select
            value={minConf}
            onChange={(e) => setMinConf(Number(e.target.value))}
            disabled={running}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={run} disabled={running}>
          {running ? 'Analyzing…' : 'Run analysis'}
        </button>
        {generatedAt && <span className="hint">Last run: {fmt(generatedAt)}</span>}
      </div>

      {error && <div className="error">{error}</div>}

      {!native && reports.length > 0 && (
        <div className="warn-note">
          Surya gives no real confidence in this build — these scores are computed from
          the OCR text (loops, garbage characters, script mix, blank/sparse pages).
        </div>
      )}

      {reports.length > 0 && (
        <>
          <div className="proofread-summary hint">
            {reports.length} book{reports.length > 1 ? 's' : ''} · {totalFlagged} page
            {totalFlagged === 1 ? '' : 's'} flagged below {minConf}
          </div>

          <table className="proofread-table">
            <thead>
              <tr>
                <th>
                  <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                </th>
                <th>Book</th>
                <th>Pages</th>
                <th>Flagged</th>
                <th>CSV</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.csv_name}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(r.csv_name)}
                      onChange={() => toggle(r.csv_name)}
                    />
                  </td>
                  <td className="pf-name" title={r.filename}>{r.filename}</td>
                  <td>{r.total_pages}</td>
                  <td className={r.flagged > 0 ? 'pf-flagged' : ''}>{r.flagged}</td>
                  <td>
                    <a href={api.proofreadCsvUrl(r.csv_name)}>Download</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="proofread-actions">
            <button
              onClick={() => downloadZip(Array.from(selected))}
              disabled={zipping || selected.size === 0}
            >
              {zipping ? 'Zipping…' : `Download selected (${selected.size})`}
            </button>
            <button className="primary" onClick={() => downloadZip([])} disabled={zipping}>
              Download all (zip)
            </button>
          </div>
        </>
      )}

      {reports.length === 0 && !running && (
        <div className="hint" style={{ marginTop: 10 }}>
          No reports yet — click “Run analysis”.
        </div>
      )}
    </div>
  )
}
