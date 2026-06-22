import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

const MAX_BACKOFF_MS = 4000
const MAX_WS_ATTEMPTS = 5

export default function ProgressPanel({ job, onJobUpdate }) {
  const [snapshot, setSnapshot] = useState({
    status: job.status,
    processed_pages: job.processed_pages,
    total_pages: job.total_pages,
    error: job.error,
  })
  const wsRef = useRef(null)

  // Reliable path: keep progress fresh from the polled job prop. RunPod's proxy
  // often refuses WebSocket upgrades, so the 3s polling in App is what actually
  // drives the bar; the WS below is just a bonus when it does connect. Math.max
  // avoids the bar jumping backward if a poll lands behind a WS update.
  useEffect(() => {
    setSnapshot((cur) => ({
      status: job.status,
      processed_pages: Math.max(cur.processed_pages || 0, job.processed_pages || 0),
      total_pages: job.total_pages || cur.total_pages,
      error: job.error,
    }))
  }, [job.status, job.processed_pages, job.total_pages, job.error])

  useEffect(() => {
    let cancelled = false
    let backoff = 250
    let attempts = 0

    const connect = () => {
      if (cancelled || attempts >= MAX_WS_ATTEMPTS) return
      attempts += 1
      let ws
      try {
        ws = api.wsProgress(job.id)
      } catch (e) {
        scheduleReconnect()
        return
      }
      wsRef.current = ws
      ws.onopen = () => { backoff = 250; attempts = 0 }
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
      if (cancelled || attempts >= MAX_WS_ATTEMPTS) return  // give up, polling covers it
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
