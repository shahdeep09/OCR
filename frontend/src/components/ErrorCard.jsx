import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ErrorCard({ job, onResume }) {
  const [errText, setErrText] = useState(job.error || '')
  const [copyState, setCopyState] = useState('')
  const [resuming, setResuming] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.getJobError(job.id)
      .then((r) => { if (!cancelled && r && r.error) setErrText(r.error) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [job.id])

  const headline = errText.split('\n', 1)[0] || 'Job failed'
  const hasTraceback = errText.includes('\n')
  const done = job.processed_pages || 0
  const total = job.total_pages || 0
  const hasPartial = done > 0

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(errText)
      setCopyState('Copied!')
    } catch {
      setCopyState('Copy failed')
    }
    setTimeout(() => setCopyState(''), 1500)
  }

  const resume = async () => {
    setResuming(true)
    try {
      await api.resumeJob(job.id)
      onResume && onResume()
    } catch (e) {
      console.error('resume failed', e)
      setResuming(false)
    }
  }

  const dl = (kind) => {
    const a = document.createElement('a')
    a.href = api.downloadUrl(job.id, kind)
    a.click()
  }

  return (
    <div className="card error-card">
      <h2>Job interrupted</h2>
      <div className="filename" title={job.filename}>{job.filename}</div>

      {hasPartial && (
        <div className="error-partial">
          <strong>{done} / {total}</strong> page{done === 1 ? '' : 's'} completed and saved.
          Resume to finish the rest, or download what's done.
        </div>
      )}

      <div className="error-headline">{headline}</div>

      <div className="error-actions">
        <button className="primary" onClick={resume} disabled={resuming}>
          {resuming ? 'Resuming…' : (hasPartial ? `Resume (${total - done} left)` : 'Retry')}
        </button>
        {hasPartial && (
          <>
            <button onClick={() => dl('json')}>JSON</button>
            <button onClick={() => dl('txt')}>TXT</button>
            <button onClick={() => dl('pdf')}>Searchable PDF</button>
          </>
        )}
      </div>

      {hasTraceback && (
        <details className="error-trace">
          <summary>Show error details</summary>
          <pre>{errText}</pre>
          <button onClick={copy} style={{ marginTop: 8 }}>{copyState || 'Copy error'}</button>
        </details>
      )}
    </div>
  )
}
