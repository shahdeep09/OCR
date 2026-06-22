import React, { useEffect, useState } from 'react'

// Stored timestamps are naive UTC ISO strings; append 'Z' so the browser
// converts them to local time.
const asDate = (s) => (s ? new Date(s + 'Z') : null)
const fmtTime = (s) => {
  const d = asDate(s)
  return d ? d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—'
}

function timing(j) {
  const start = asDate(j.started_at || j.created_at)
  const end = asDate(j.completed_at)
  if (!start || !end || !j.total_pages) return null
  const totalSec = (end - start) / 1000
  if (totalSec <= 0) return null
  const perPage = totalSec / j.total_pages
  const mins = Math.floor(totalSec / 60)
  const secs = Math.round(totalSec % 60)
  const dur = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`
  return { perPage: perPage.toFixed(1), dur }
}

export default function JobHistory({ jobs, selectedId, onSelect, onDelete, onNew }) {
  const [confirmId, setConfirmId] = useState(null)

  // Cancel pending-confirm if the user clicks elsewhere or after 3s of inactivity.
  useEffect(() => {
    if (!confirmId) return
    const t = setTimeout(() => setConfirmId(null), 3000)
    return () => clearTimeout(t)
  }, [confirmId])

  const handleDeleteClick = (e, id) => {
    e.stopPropagation()
    if (confirmId === id) {
      setConfirmId(null)
      onDelete(id)
    } else {
      setConfirmId(id)
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h1>📖 BookScan</h1>
        <button className="primary new-btn" onClick={onNew}>+ New</button>
      </div>
      <div className="history-list">
        {jobs.length === 0 && (
          <div className="history-empty">No jobs yet. Upload a PDF to start.</div>
        )}
        {jobs.map((j) => {
          const isConfirm = confirmId === j.id
          return (
            <div
              key={j.id}
              className={`history-item ${selectedId === j.id ? 'selected' : ''}`}
              onClick={() => { setConfirmId(null); onSelect(j.id) }}
            >
              <div className="row1">
                <span className="filename" title={j.filename}>{j.filename}</span>
                <button
                  className={`delete-btn ${isConfirm ? 'confirm' : ''}`}
                  title={isConfirm ? 'Click again to confirm delete' : 'Delete'}
                  onClick={(e) => handleDeleteClick(e, j.id)}
                >
                  {isConfirm ? 'Sure?' : '×'}
                </button>
              </div>
              <div className="row2">
                <span className={`status-pill ${j.status}`}>{j.status}</span>
                <span>
                  {j.total_pages > 0 ? `${j.processed_pages} / ${j.total_pages}` : ''}
                </span>
              </div>
              {(() => {
                const t = timing(j)
                if (j.status === 'done' && t) {
                  return (
                    <div
                      className="row3"
                      title={`Started: ${fmtTime(j.started_at || j.created_at)}\nCompleted: ${fmtTime(j.completed_at)}\nTook ${t.dur} · ${t.perPage} s/page`}
                    >
                      <span>{t.perPage} s/page</span>
                      <span>{t.dur}</span>
                    </div>
                  )
                }
                if (j.started_at && j.status === 'running') {
                  return <div className="row3"><span>started {fmtTime(j.started_at)}</span></div>
                }
                return null
              })()}
            </div>
          )
        })}
      </div>
    </aside>
  )
}
