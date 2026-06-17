import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import PageNav from './PageNav.jsx'
import DownloadBar from './DownloadBar.jsx'

export default function Viewer({ job, onClose }) {
  const [pages, setPages] = useState([]) // [{page_num, text}]
  const [pageNum, setPageNum] = useState(1)
  const [text, setText] = useState('')
  const [saveState, setSaveState] = useState('') // '', 'saving', 'saved', 'error'
  const saveTimer = useRef(null)
  const lastSavedRef = useRef('')
  const textareaRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    setPages([])
    setPageNum(1)
    setText('')
    api.getPages(job.id).then((list) => {
      if (cancelled) return
      setPages(list)
      if (list.length > 0) {
        setText(list[0].text || '')
        lastSavedRef.current = list[0].text || ''
      }
    })
    return () => { cancelled = true }
  }, [job.id])

  useEffect(() => {
    const p = pages.find((p) => p.page_num === pageNum)
    if (p) {
      setText(p.text || '')
      lastSavedRef.current = p.text || ''
      setSaveState('')
    }
  }, [pageNum, pages])

  const saveNow = async (force = false) => {
    if (!force && text === lastSavedRef.current) return
    setSaveState('saving')
    try {
      await api.updatePage(job.id, pageNum, text)
      lastSavedRef.current = text
      setPages((prev) => prev.map((p) => p.page_num === pageNum ? { ...p, text } : p))
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
  }, [text, pageNum, job.id])

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
        setPageNum((n) => Math.min(pages.length || job.total_pages, n + 1))
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [pages.length, job.total_pages, onClose, text, pageNum, job.id])

  const total = pages.length || job.total_pages
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
