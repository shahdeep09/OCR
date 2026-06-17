import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import JobHistory from './components/JobHistory.jsx'
import Uploader from './components/Uploader.jsx'
import ProgressPanel from './components/ProgressPanel.jsx'
import Viewer from './components/Viewer.jsx'
import ErrorCard from './components/ErrorCard.jsx'

const POLL_MS = 3000
const RECONNECT_MAX_MS = 5000

export default function App() {
  const [jobs, setJobs] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [wsConnected, setWsConnected] = useState(false)
  const pollRef = useRef(null)
  const wsRef = useRef(null)

  const refreshJobs = useCallback(async () => {
    try {
      const list = await api.listJobs()
      setJobs(list)
    } catch (e) {
      console.error('listJobs failed', e)
    }
  }, [])

  // Initial load + poll fallback (only runs when WS not connected)
  useEffect(() => {
    refreshJobs()
  }, [refreshJobs])

  useEffect(() => {
    if (wsConnected) {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    pollRef.current = setInterval(refreshJobs, POLL_MS)
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [wsConnected, refreshJobs])

  // /api/ws/jobs subscription with exponential reconnect
  useEffect(() => {
    let cancelled = false
    let backoff = 250

    const connect = () => {
      if (cancelled) return
      let ws
      try {
        ws = api.wsJobs()
      } catch (e) {
        scheduleReconnect()
        return
      }
      wsRef.current = ws
      ws.onopen = () => {
        backoff = 250
        setWsConnected(true)
      }
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.type === 'jobs' && Array.isArray(msg.jobs)) {
            setJobs(msg.jobs)
          }
        } catch {}
      }
      ws.onerror = () => {}
      ws.onclose = () => {
        setWsConnected(false)
        scheduleReconnect()
      }
    }

    const scheduleReconnect = () => {
      if (cancelled) return
      setTimeout(connect, backoff)
      backoff = Math.min(backoff * 2, RECONNECT_MAX_MS)
    }

    connect()
    return () => {
      cancelled = true
      try { wsRef.current && wsRef.current.close() } catch {}
    }
  }, [])

  const onUploaded = async (created) => {
    await refreshJobs()
    if (created && created.length > 0) {
      setSelectedId(created[0].job_id)
    }
  }

  const onDelete = async (id) => {
    await api.deleteJob(id)
    if (selectedId === id) setSelectedId(null)
    refreshJobs()
  }

  const selectedJob = jobs.find((j) => j.id === selectedId) || null

  return (
    <div className="app">
      <JobHistory
        jobs={jobs}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onDelete={onDelete}
        onNew={() => setSelectedId(null)}
      />
      <main className="main">
        {!selectedJob && <div className="main-empty"><Uploader onUploaded={onUploaded} /></div>}
        {selectedJob && selectedJob.status === 'failed' && (
          <div className="main-progress"><ErrorCard job={selectedJob} /></div>
        )}
        {selectedJob && selectedJob.status !== 'done' && selectedJob.status !== 'failed' && (
          <div className="main-progress">
            <ProgressPanel job={selectedJob} onJobUpdate={refreshJobs} />
          </div>
        )}
        {selectedJob && selectedJob.status === 'done' && (
          <Viewer job={selectedJob} onClose={() => setSelectedId(null)} />
        )}
      </main>
    </div>
  )
}
