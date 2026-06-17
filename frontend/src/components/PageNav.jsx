import React, { useState, useEffect } from 'react'

export default function PageNav({ page, total, onChange }) {
  const [input, setInput] = useState(String(page))

  useEffect(() => { setInput(String(page)) }, [page])

  const commit = () => {
    const n = parseInt(input, 10)
    if (!isNaN(n) && n >= 1 && n <= total) onChange(n)
    else setInput(String(page))
  }

  return (
    <div className="page-nav">
      <button onClick={() => onChange(Math.max(1, page - 1))} disabled={page <= 1}>‹</button>
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') commit() }}
      />
      <span>/ {total}</span>
      <button onClick={() => onChange(Math.min(total, page + 1))} disabled={page >= total}>›</button>
    </div>
  )
}
