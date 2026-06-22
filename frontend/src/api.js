// Backend URL. Set `VITE_BACKEND_URL` in `.env.local` to point at RunPod or
// any remote. Defaults to a local uvicorn instance.
const _RAW = (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '')
export const API_BASE = _RAW
// Derive a matching ws:// or wss:// base from the http(s):// URL.
export const WS_BASE = _RAW.replace(/^http/, 'ws')

async function jfetch(path, opts) {
  const r = await fetch(`${API_BASE}${path}`, opts)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const body = await r.json()
      if (body && body.detail) msg = body.detail
    } catch {}
    throw new Error(msg)
  }
  if (r.status === 204) return null
  const ct = r.headers.get('content-type') || ''
  return ct.includes('application/json') ? r.json() : r.text()
}

export const api = {
  health: () => jfetch('/api/health'),
  listJobs: () => jfetch('/api/jobs'),
  getJob: (id) => jfetch(`/api/jobs/${id}`),
  getJobError: (id) => jfetch(`/api/jobs/${id}/error`),
  resumeJob: (id) => jfetch(`/api/jobs/${id}/resume`, { method: 'POST' }),
  deleteJob: (id) => jfetch(`/api/jobs/${id}`, { method: 'DELETE' }),
  getPages: (id) => jfetch(`/api/jobs/${id}/pages`),
  updatePage: (id, page, text) =>
    jfetch(`/api/jobs/${id}/pages/${page}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
  upload: async (files) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    const r = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: fd })
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`
      try { const b = await r.json(); if (b.detail) msg = b.detail } catch {}
      throw new Error(msg)
    }
    return r.json()
  },
  // Single continuous upload of one file (the original mechanism), via XHR so we
  // can show a progress bar. One streaming request is far faster through the
  // RunPod proxy than many small chunked requests. Returns the created job list.
  uploadOne: (file, onProgress) =>
    new Promise((resolve, reject) => {
      const fd = new FormData()
      fd.append('files', file)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${API_BASE}/api/upload`)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)) } catch { resolve([]) }
        } else {
          let msg = `${xhr.status} ${xhr.statusText}`
          try { const b = JSON.parse(xhr.responseText); if (b.detail) msg = b.detail } catch {}
          reject(new Error(msg))
        }
      }
      xhr.onerror = () => reject(new Error('Upload failed (network error)'))
      xhr.send(fd)
    }),
  getInbox: () => jfetch('/api/inbox'),
  ingestFile: (filename) =>
    jfetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    }),
  pageImageUrl: (id, page) => `${API_BASE}/api/jobs/${id}/pages/${page}/image`,
  downloadUrl: (id, kind) => `${API_BASE}/api/jobs/${id}/download/${kind}`,
  wsProgress: (id) => new WebSocket(`${WS_BASE}/api/ws/progress/${id}`),
  wsJobs: () => new WebSocket(`${WS_BASE}/api/ws/jobs`),
}
