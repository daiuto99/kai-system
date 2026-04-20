import React, { useState, useEffect } from 'react'

const API = '/api'

function renderMarkdown(text) {
  if (!text) return null
  const lines = text.split('\n')
  const elements = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    // H1
    if (line.startsWith('# ')) {
      elements.push(<h1 key={i} style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', margin: '20px 0 8px', letterSpacing: '-0.02em' }}>{renderInline(line.slice(2))}</h1>)
    // H2
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={i} style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', margin: '16px 0 6px' }}>{renderInline(line.slice(3))}</h2>)
    // H3
    } else if (line.startsWith('### ')) {
      elements.push(<h3 key={i} style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 4px' }}>{renderInline(line.slice(4))}</h3>)
    // HR
    } else if (line === '---' || line === '***') {
      elements.push(<hr key={i} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '16px 0' }} />)
    // List items
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      elements.push(<li key={i} style={{ marginLeft: 20, marginBottom: 2, fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>{renderInline(line.slice(2))}</li>)
    // Blank line
    } else if (line.trim() === '') {
      elements.push(<div key={i} style={{ height: 8 }} />)
    } else {
      elements.push(<p key={i} style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-primary)', margin: '2px 0' }}>{renderInline(line)}</p>)
    }
    i++
  }
  return elements
}

function renderInline(text) {
  // Handle bold **text**, inline `code`
  const parts = []
  let remaining = text
  let key = 0
  while (remaining.length > 0) {
    const boldIdx = remaining.indexOf('**')
    const codeIdx = remaining.indexOf('`')
    if (boldIdx === -1 && codeIdx === -1) {
      parts.push(<span key={key++}>{remaining}</span>)
      break
    }
    const nextIdx = (boldIdx === -1) ? codeIdx : (codeIdx === -1) ? boldIdx : Math.min(boldIdx, codeIdx)
    if (nextIdx > 0) {
      parts.push(<span key={key++}>{remaining.slice(0, nextIdx)}</span>)
      remaining = remaining.slice(nextIdx)
    }
    if (remaining.startsWith('**')) {
      const end = remaining.indexOf('**', 2)
      if (end === -1) { parts.push(<span key={key++}>{remaining}</span>); break }
      parts.push(<strong key={key++} style={{ fontWeight: 600 }}>{remaining.slice(2, end)}</strong>)
      remaining = remaining.slice(end + 2)
    } else if (remaining.startsWith('`')) {
      const end = remaining.indexOf('`', 1)
      if (end === -1) { parts.push(<span key={key++}>{remaining}</span>); break }
      parts.push(<code key={key++} style={{ fontFamily: 'monospace', fontSize: 12, background: 'var(--bg-screen)', padding: '1px 4px', borderRadius: 3 }}>{remaining.slice(1, end)}</code>)
      remaining = remaining.slice(end + 1)
    }
  }
  return parts
}

export default function Wiki() {
  const [tree, setTree] = useState(null)
  const [selected, setSelected] = useState(null)
  const [content, setContent] = useState(null)
  const [loadingFile, setLoadingFile] = useState(false)
  const [collapsed, setCollapsed] = useState({})

  useEffect(() => {
    fetch(`${API}/wiki/tree`)
      .then(r => r.json())
      .then(d => setTree(d.tree || []))
      .catch(() => setTree([]))
  }, [])

  function openFile(path, name) {
    setSelected({ path, name })
    setContent(null)
    setLoadingFile(true)
    fetch(`${API}/wiki/file?path=${encodeURIComponent(path)}`)
      .then(r => r.json())
      .then(d => { setContent(d.content); setLoadingFile(false) })
      .catch(() => { setContent('Error loading file.'); setLoadingFile(false) })
  }

  function toggleDir(path) {
    setCollapsed(c => ({ ...c, [path]: !c[path] }))
  }

  function renderTree(nodes, depth = 0) {
    return nodes.map(node => {
      if (node.type === 'dir') {
        const open = !collapsed[node.path]
        return (
          <div key={node.path}>
            <button onClick={() => toggleDir(node.path)} style={{
              width: '100%', background: 'none', border: 'none', cursor: 'pointer',
              padding: `5px 8px 5px ${8 + depth * 12}px`,
              display: 'flex', alignItems: 'center', gap: 6, textAlign: 'left',
              color: 'var(--text-secondary)', fontSize: 11, fontWeight: 700,
              textTransform: 'uppercase', letterSpacing: '0.06em',
            }}>
              <span style={{ fontSize: 9, opacity: 0.6 }}>{open ? '\u25be' : '\u25b8'}</span>
              {node.name}
            </button>
            {open && node.children && node.children.length > 0 && renderTree(node.children, depth + 1)}
          </div>
        )
      }
      const isActive = selected?.path === node.path
      return (
        <button key={node.path} onClick={() => openFile(node.path, node.name.replace('.md', ''))} style={{
          width: '100%', background: isActive ? 'var(--accent)' : 'none',
          border: 'none', cursor: 'pointer', borderRadius: 6,
          padding: `5px 8px 5px ${8 + depth * 12}px`,
          textAlign: 'left', transition: 'background 0.1s',
          color: isActive ? '#fff' : 'var(--text-secondary)',
          fontSize: 13, fontWeight: isActive ? 500 : 400, marginBottom: 1,
        }}
          onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-screen)' }}
          onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'none' }}
        >
          {node.name.replace('.md', '')}
        </button>
      )
    })
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '20px 24px 0', flexShrink: 0 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>Wiki</h1>
        <p style={{ margin: '4px 0 16px', fontSize: 13, color: 'var(--text-tertiary)' }}>Knowledge vault — guides, brand docs, build notes</p>
      </div>
      <div style={{ height: 1, background: 'var(--border)', flexShrink: 0 }} />

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Sidebar */}
        <div style={{
          width: 220, flexShrink: 0, borderRight: '1px solid var(--border)',
          overflowY: 'auto', padding: '12px 8px',
        }}>
          {tree === null ? (
            <div style={{ padding: 16, color: 'var(--text-tertiary)', fontSize: 12 }}>Loading…</div>
          ) : tree.length === 0 ? (
            <div style={{ padding: 16, color: 'var(--text-tertiary)', fontSize: 12 }}>No files yet. Ask KAI to save notes here.</div>
          ) : renderTree(tree)}
        </div>

        {/* Viewer */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '24px 28px' }}>
          {!selected ? (
            <div style={{ textAlign: 'center', paddingTop: 60 }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>📖</div>
              <div style={{ color: 'var(--text-tertiary)', fontSize: 14 }}>Select a file to read</div>
              <div style={{ color: 'var(--text-subtle)', fontSize: 12, marginTop: 6 }}>
                KAI writes notes here automatically. You can also ask KAI to "save this to the wiki."
              </div>
            </div>
          ) : loadingFile ? (
            <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>Loading…</div>
          ) : (
            <>
              <div style={{ marginBottom: 20, paddingBottom: 16, borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                  {selected.name}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-subtle)', marginTop: 3, fontFamily: 'monospace' }}>
                  70_Knowledge/{selected.path}
                </div>
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                {renderMarkdown(content)}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
