import React, { useState, useEffect } from 'react'

const API = '/api'

const ADVISOR_COLORS = {
  chief: '#6366f1', beats: '#f59e0b', biz: '#3b82f6',
  coach: '#f97316', doc: '#10b981', ember: '#ec4899',
  roads: '#f59e0b', sky: '#06b6d4',
}

export default function Advisors() {
  const [advisors, setAdvisors] = useState(null)
  const [selected, setSelected] = useState(null)
  const [content, setContent] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetch(`${API}/advisors`)
      .then(r => r.json())
      .then(d => setAdvisors(d.advisors || []))
      .catch(() => setAdvisors([]))
  }, [])

  function openAdvisor(advisor) {
    setSelected(advisor)
    setEditing(false)
    setSaved(false)
    fetch(`${API}/advisors/${advisor.id}`)
      .then(r => r.json())
      .then(d => setContent(d.content || ''))
      .catch(() => setContent(''))
  }

  async function savePersona() {
    if (!selected) return
    setSaving(true)
    try {
      const r = await fetch(`${API}/advisors/${selected.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (r.ok) {
        setSaved(true)
        setEditing(false)
        setTimeout(() => setSaved(false), 3000)
      }
    } catch (e) {}
    setSaving(false)
  }

  const color = selected ? (ADVISOR_COLORS[selected.id] || '#6366f1') : '#6366f1'

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '20px 24px 0', flexShrink: 0 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>Advisors</h1>
        <p style={{ margin: '4px 0 16px', fontSize: 13, color: 'var(--text-tertiary)' }}>View and edit advisor personas</p>
      </div>
      <div style={{ height: 1, background: 'var(--border)', flexShrink: 0 }} />

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Sidebar */}
        <div style={{
          width: 200, flexShrink: 0, borderRight: '1px solid var(--border)',
          overflowY: 'auto', padding: '12px 8px',
        }}>
          {advisors === null ? (
            <div style={{ padding: 16, color: 'var(--text-tertiary)', fontSize: 12 }}>Loading…</div>
          ) : advisors.map(a => {
            const c = ADVISOR_COLORS[a.id] || '#6366f1'
            const isActive = selected?.id === a.id
            return (
              <button key={a.id} onClick={() => openAdvisor(a)} style={{
                width: '100%', background: isActive ? 'var(--accent)' : 'none',
                border: 'none', cursor: 'pointer', borderRadius: 8,
                padding: '9px 12px', textAlign: 'left', marginBottom: 2,
                transition: 'background 0.1s',
              }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-screen)' }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'none' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: isActive ? '#fff' : c, display: 'inline-block', flexShrink: 0 }} />
                  <span style={{ fontSize: 13, fontWeight: 500, color: isActive ? '#fff' : 'var(--text-primary)' }}>{a.name}</span>
                </div>
                {a.description && (
                  <div style={{ fontSize: 11, color: isActive ? 'rgba(255,255,255,0.65)' : 'var(--text-subtle)', marginTop: 2, paddingLeft: 16 }}>
                    {a.description}
                  </div>
                )}
              </button>
            )
          })}
        </div>

        {/* Editor panel */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {!selected ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 36, marginBottom: 12 }}>🤖</div>
                <div style={{ color: 'var(--text-tertiary)', fontSize: 14 }}>Select an advisor to view their persona</div>
              </div>
            </div>
          ) : (
            <>
              <div style={{
                padding: '12px 20px', borderBottom: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, display: 'inline-block' }} />
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{selected.name}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-subtle)', fontFamily: 'monospace' }}>{selected.id.toUpperCase()}.md</span>
                  {saved && <span style={{ fontSize: 12, color: '#10b981' }}>✓ saved</span>}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {editing ? (
                    <>
                      <button onClick={() => setEditing(false)} style={{
                        padding: '6px 14px', borderRadius: 8, border: '1px solid var(--border)',
                        background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'inherit',
                      }}>Cancel</button>
                      <button onClick={savePersona} disabled={saving} style={{
                        padding: '6px 14px', borderRadius: 8, border: 'none',
                        background: 'var(--accent)', cursor: 'pointer', fontSize: 12, color: '#fff', fontWeight: 500, fontFamily: 'inherit',
                      }}>{saving ? 'Saving…' : 'Save'}</button>
                    </>
                  ) : (
                    <button onClick={() => setEditing(true)} style={{
                      padding: '6px 14px', borderRadius: 8, border: '1px solid var(--border)',
                      background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'inherit',
                    }}>Edit</button>
                  )}
                </div>
              </div>

              <div style={{ flex: 1, overflow: 'hidden' }}>
                {editing ? (
                  <textarea
                    value={content}
                    onChange={e => setContent(e.target.value)}
                    style={{
                      width: '100%', height: '100%', padding: '20px 24px',
                      background: 'var(--bg-screen)', color: 'var(--text-primary)',
                      border: 'none', outline: 'none', resize: 'none',
                      fontFamily: 'monospace', fontSize: 12, lineHeight: 1.7,
                      boxSizing: 'border-box',
                    }}
                  />
                ) : (
                  <div style={{ height: '100%', overflowY: 'auto', padding: '20px 24px' }}>
                    {content ? (
                      <pre style={{
                        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                        fontSize: 13, lineHeight: 1.75, color: 'var(--text-primary)',
                        margin: 0, fontFamily: 'inherit',
                      }}>{content}</pre>
                    ) : (
                      <div style={{ color: 'var(--text-subtle)', fontSize: 13, fontStyle: 'italic' }}>
                        No persona file found. Click Edit to create one.
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
