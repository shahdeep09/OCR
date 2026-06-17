import React, { useEffect, useState } from 'react'

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
            </div>
          )
        })}
      </div>
    </aside>
  )
}
