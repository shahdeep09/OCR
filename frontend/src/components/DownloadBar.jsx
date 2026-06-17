import React, { useState } from 'react'
import { api } from '../api.js'

export default function DownloadBar({ jobId, currentText }) {
  const [copyState, setCopyState] = useState('')

  const dl = (kind) => {
    const a = document.createElement('a')
    a.href = api.downloadUrl(jobId, kind)
    a.click()
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(currentText || '')
      setCopyState('Copied!')
    } catch {
      setCopyState('Copy failed')
    }
    setTimeout(() => setCopyState(''), 1500)
  }

  return (
    <div className="download-bar">
      <button onClick={copy} title="Copy current page text">
        {copyState || 'Copy page'}
      </button>
      <button onClick={() => dl('json')}>JSON</button>
      <button onClick={() => dl('txt')}>TXT</button>
      <button className="primary" onClick={() => dl('pdf')}>Searchable PDF</button>
    </div>
  )
}
