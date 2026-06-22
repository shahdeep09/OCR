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
  // Reliable upload for any file size: slice into chunks, upload one at a time
  // with per-chunk retry, reassemble server-side. Beats the proxy's big-request
  // limit and survives brief connection blips during upload.
  uploadLarge: async (file, onProgress) => {
    // 2 MB keeps each chunk well under the proxy's request timeout even on a
    // slow upstream (~0.6 Mbps), so chunks complete instead of getting cut off.
    const CHUNK = 2 * 1024 * 1024 // 2 MB
    const init = await jfetch('/api/upload/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name }),
    })
    const uploadId = init.upload_id
    const total = Math.max(1, Math.ceil(file.size / CHUNK))
    for (let i = 0; i < total; i++) {
      const blob = file.slice(i * CHUNK, Math.min((i + 1) * CHUNK, file.size))
      let done = false
      let lastErr
      for (let attempt = 0; attempt < 4 && !done; attempt++) {
        try {
          const r = await fetch(`${API_BASE}/api/upload/chunk/${uploadId}/${i}`, {
            method: 'PUT',
            body: blob,
          })
          if (!r.ok) throw new Error(`chunk ${i} failed (${r.status})`)
          done = true
        } catch (e) {
          lastErr = e
          await new Promise((res) => setTimeout(res, 600 * (attempt + 1)))
        }
      }
      if (!done) throw lastErr || new Error(`chunk ${i} failed`)
      if (onProgress) onProgress(Math.round(((i + 1) / total) * 100))
    }
    return jfetch(`/api/upload/finish/${uploadId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, total_chunks: total }),
    })
  },
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
