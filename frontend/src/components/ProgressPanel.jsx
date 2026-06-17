import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

const MAX_BACKOFF_MS = 4000

export default function ProgressPanel({ job, onJobUpdate }) {
  const [snapshot, setSnapshot] = useState({
    status: job.status,
    processed_pages: job.processed_pages,
    total_pages: job.total_pages,
    error: job.error,
  })
  const wsRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    let backoff = 250

    const connect = () => {
      if (cancelled) return
      let ws
      try {
        ws = api.wsProgress(job.id)
      } catch (e) {
        scheduleReconnect()
        return
      }
      wsRef.current = ws
      ws.onopen = () => { backoff = 250 }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          setSnapshot((cur) => ({ ...cur, ...msg }))
          if (msg.status === 'done' || msg.status === 'failed') {
            onJobUpdate && onJobUpdate()
          }
        } catch {}
      }
      ws.onerror = () => {}
      ws.onclose = () => {
        if (cancelled) return
        if (snapshot.status === 'done' || snapshot.status === 'failed') return
        scheduleReconnect()
      }
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
    }

    connect()
    return () => {
      cancelled = true
      try { wsRef.current && wsRef.current.close() } catch {}
    }
  }, [job.id])

  const { status, processed_pages, total_pages, error } = snapshot
  const pct = total_pages > 0 ? Math.round((processed_pages / total_pages) * 100) : 0

  return (
    <div className="card progress-card">
      <h2>{status === 'queued' ? 'Queued' : status === 'running' ? 'Processing…' : status}</h2>
      <div className="filename" title={job.filename}>{job.filename}</div>
      <div className="progress-bar">
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-stats">
        {total_pages > 0
          ? `${processed_pages} / ${total_pages} pages (${pct}%)`
          : 'Counting pages…'}
      </div>
      {error && <div className="progress-error">Error: {error}</div>}
    </div>
  )
}
