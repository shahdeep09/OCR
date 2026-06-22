import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import PageNav from './PageNav.jsx'
import DownloadBar from './DownloadBar.jsx'

export default function Viewer({ job, onClose }) {
  const [pageNum, setPageNum] = useState(1)
  const [text, setText] = useState('')
  const [saveState, setSaveState] = useState('') // '', 'saving', 'saved', 'error'
  const saveTimer = useRef(null)
  const lastSavedRef = useRef('')
  const textareaRef = useRef(null)

  const total = job.total_pages || 0

  // Reset to page 1 when the job changes
  useEffect(() => { setPageNum(1) }, [job.id])

  // Always fetch the current page's text fresh from the server — never a stale
  // cached list. This is why JSON/TXT had text but the old viewer showed empty.
  useEffect(() => {
    let cancelled = false
    setSaveState('')
    api.getPage(job.id, pageNum)
      .then((p) => {
        if (cancelled) return
        const t = (p && p.text) || ''
        setText(t)
        lastSavedRef.current = t
      })
      .catch(() => {
        if (cancelled) return
        setText('')
        lastSavedRef.current = ''
      })
    return () => { cancelled = true }
  }, [job.id, pageNum])

  const saveNow = async (force = false) => {
    if (!force && text === lastSavedRef.current) return
    setSaveState('saving')
    try {
      await api.updatePage(job.id, pageNum, text)
      lastSavedRef.current = text
      setSaveState('saved')
      setTimeout(() => setSaveState((s) => s === 'saved' ? '' : s), 1200)
    } catch (e) {
      console.error('save failed', e)
      setSaveState('error')
    }
  }

  // Debounced auto-save
  useEffect(() => {
    if (text === lastSavedRef.current) return
    setSaveState('saving')
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => saveNow(false), 500)
    return () => clearTimeout(saveTimer.current)
  }, [text])

  // Keyboard shortcuts
  useEffect(() => {
    const isTyping = (target) =>
      target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')

    const handler = (e) => {
      // Ctrl/Cmd+S: explicit save flush — works even while typing
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        clearTimeout(saveTimer.current)
        saveNow(true)
        return
      }
      // Esc: close back to history (blur first so it doesn't fight a focused input)
      if (e.key === 'Escape') {
        e.preventDefault()
        if (document.activeElement && document.activeElement.blur) {
          document.activeElement.blur()
        }
        onClose && onClose()
        return
      }
      // Arrow nav only when NOT typing in the textarea/input — caret control matters
      if (isTyping(e.target)) return
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        setPageNum((n) => Math.max(1, n - 1))
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        setPageNum((n) => Math.min(total, n + 1))
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [total, onClose, text, pageNum, job.id])

  const imgUrl = total > 0 ? api.pageImageUrl(job.id, pageNum) : null

  return (
    <div className="viewer">
      <div className="viewer-toolbar">
        <span className="title" title={job.filename}>{job.filename}</span>
        <PageNav page={pageNum} total={total} onChange={setPageNum} />
        <DownloadBar jobId={job.id} currentText={text} />
      </div>
      <div className="viewer-body">
        <div className="viewer-pane">
          <div className="pane-header"><span>Scanned page</span></div>
          <div className="image-pane">
            {imgUrl && <img src={imgUrl} alt={`Page ${pageNum}`} />}
          </div>
        </div>
        <div className="viewer-pane">
          <div className="pane-header">
            <span>OCR text (editable) · ←/→ pages · Ctrl+S save · Esc close</span>
            <span className={`save-indicator ${saveState}`}>
              {saveState === 'saving' && 'Saving…'}
              {saveState === 'saved' && 'Saved'}
              {saveState === 'error' && 'Save failed'}
            </span>
          </div>
          <div className="text-pane">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              placeholder="(empty page)"
            />
          </div>
        </div>
      </div>
    </div>
  )
}
