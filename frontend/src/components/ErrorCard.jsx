import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ErrorCard({ job }) {
  const [errText, setErrText] = useState(job.error || '')
  const [copyState, setCopyState] = useState('')

  useEffect(() => {
    let cancelled = false
    api.getJobError(job.id)
      .then((r) => { if (!cancelled && r && r.error) setErrText(r.error) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [job.id])

  const headline = errText.split('\n', 1)[0] || 'Job failed'
  const hasTraceback = errText.includes('\n')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(errText)
      setCopyState('Copied!')
    } catch {
      setCopyState('Copy failed')
    }
    setTimeout(() => setCopyState(''), 1500)
  }

  return (
    <div className="card error-card">
      <h2>Job failed</h2>
      <div className="filename" title={job.filename}>{job.filename}</div>
      <div className="error-headline">{headline}</div>
      {hasTraceback && (
        <details className="error-trace">
          <summary>Show traceback</summary>
          <pre>{errText}</pre>
        </details>
      )}
      <div className="error-actions">
        <button onClick={copy}>{copyState || 'Copy error'}</button>
      </div>
    </div>
  )
}
