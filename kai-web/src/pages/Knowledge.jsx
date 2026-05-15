import React, { useState, useEffect } from 'react'

const API = '/api'

const ADVISOR_COLORS = {
  kai: '#6366f1',
  ember: '#ec4899', beats: '#f59e0b',
  doc: '#10b981', coach: '#f97316',
  roads: '#f59e0b', creative: '#8b5cf6',
}

function AdvisorDot({ channel }) {
  const color = ADVISOR_COLORS[channel] || '#6366f1'
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: color, marginRight: 8, flexShrink: 0,
    }} />
  )
}

function SessionReader({ session, onClose }) {
  const [content, setContent] = useState(null)
  useEffect(() => {
    fetch(`${API}/knowledge/session?path=${encodeURIComponent(session.path)}`)
      .then(r => r.json()).then(d => setContent(d.content)).catch(() => setContent('Error loading session.'))
  }, [session.path])

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 100,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }} onClick={onClose}>
      <div style={{
        background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)',
        width: '100%', maxWidth: 680, maxHeight: '80vh', overflow: 'hidden',
        display: 'flex', flexDirection: 'column',
      }} onClick={e => e.stopPropagation()}>
        <div style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <AdvisorDot channel={session.channel} />
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
              {session.channel} / {session.filename}
            </span>
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-tertiary)', fontSize: 20, lineHeight: 1, padding: 4,
          }}>×</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
          {content === null ? (
            <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>Loading...</div>
          ) : (
            <pre style={{
              fontFamily: 'inherit', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              fontSize: 13, lineHeight: 1.7, color: 'var(--text-primary)', margin: 0,
            }}>{content}</pre>
          )}
        </div>
      </div>
    </div>
  )
}

function SessionsTab() {
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetch(`${API}/knowledge/sessions`).then(r => r.json()).then(setData).catch(() => setData({ sessions: {} }))
  }, [])

  if (!data) return <div style={{ padding: 32, color: 'var(--text-tertiary)', fontSize: 13 }}>Loading...</div>

  const channels = Object.keys(data.sessions)
  if (channels.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>📚</div>
        <div style={{ color: 'var(--text-tertiary)', fontSize: 14 }}>No sessions saved yet.</div>
        <div style={{ color: 'var(--text-subtle)', fontSize: 12, marginTop: 6 }}>
          Ask KAI to "save this session" or sessions auto-save after 10+ exchanges.
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      {channels.map(ch => (
        <div key={ch}>
          <div style={{
            display: 'flex', alignItems: 'center', marginBottom: 10,
            paddingBottom: 8, borderBottom: '1px solid var(--border)',
          }}>
            <AdvisorDot channel={ch} />
            <span style={{
              fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
              letterSpacing: '0.08em', color: ADVISOR_COLORS[ch] || 'var(--text-secondary)',
            }}>{ch}</span>
            <span style={{
              marginLeft: 8, fontSize: 11, color: 'var(--text-subtle)',
              background: 'var(--bg-screen)', padding: '1px 7px', borderRadius: 10,
            }}>{data.sessions[ch].length}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {data.sessions[ch].map(s => (
              <button key={s.filename} onClick={() => setSelected(s)} style={{
                background: 'var(--bg-screen)', border: '1px solid var(--border)',
                borderRadius: 10, padding: '10px 14px',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s',
              }}
                onMouseEnter={e => e.currentTarget.style.borderColor = ADVISOR_COLORS[ch] || 'var(--accent)'}
                onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border)'}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>
                    {s.title.replace(/^# Session — \S+ — /, '').trim() || s.filename}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-subtle)', marginTop: 2 }}>
                    {s.filename.replace('.md', '').replace('T', ' ').replace(/(\d{4})(\d{2})$/, '$1:$2')}
                  </div>
                </div>
                <svg width="14" height="14" fill="none" stroke="var(--text-subtle)" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                </svg>
              </button>
            ))}
          </div>
        </div>
      ))}
      {selected && <SessionReader session={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

function DecisionsTab() {
  const [files, setFiles] = useState(null)
  const [active, setActive] = useState(null)
  const [content, setContent] = useState(null)

  useEffect(() => {
    fetch(`${API}/knowledge/decisions`).then(r => r.json()).then(d => {
      setFiles(d.files || [])
      if (d.files && d.files.length > 0) {
        setActive(d.files[0].month)
      }
    }).catch(() => setFiles([]))
  }, [])

  useEffect(() => {
    if (!active) return
    fetch(`${API}/knowledge/decisions/${active}`).then(r => r.json())
      .then(d => setContent(d.content)).catch(() => setContent(null))
  }, [active])

  if (!files) return <div style={{ padding: 32, color: 'var(--text-tertiary)', fontSize: 13 }}>Loading...</div>

  if (files.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>⚖️</div>
        <div style={{ color: 'var(--text-tertiary)', fontSize: 14 }}>No decisions logged yet.</div>
        <div style={{ color: 'var(--text-subtle)', fontSize: 12, marginTop: 6 }}>
          Ask KAI to "log that decision" during important conversations.
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Month sidebar */}
      <div style={{
        width: 140, flexShrink: 0, borderRight: '1px solid var(--border)',
        overflowY: 'auto', padding: '16px 12px',
      }}>
        {files.map(f => (
          <button key={f.month} onClick={() => setActive(f.month)} style={{
            width: '100%', padding: '8px 10px', borderRadius: 8,
            background: active === f.month ? 'var(--accent)' : 'transparent',
            border: 'none', cursor: 'pointer', textAlign: 'left',
            color: active === f.month ? '#fff' : 'var(--text-secondary)',
            fontSize: 13, fontWeight: active === f.month ? 600 : 400,
            marginBottom: 2,
          }}>{f.month}</button>
        ))}
      </div>
      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
        {content ? (
          <pre style={{
            fontFamily: 'inherit', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            fontSize: 13, lineHeight: 1.7, color: 'var(--text-primary)', margin: 0,
          }}>{content}</pre>
        ) : (
          <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>Loading...</div>
        )}
      </div>
    </div>
  )
}

export default function Knowledge() {
  const [tab, setTab] = useState('sessions')

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--bg-screen)' }}>
      {/* Header */}
      <div style={{ padding: '20px 24px 0', flexShrink: 0 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          Knowledge
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-tertiary)' }}>
          Session summaries and key decisions
        </p>
        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginTop: 16, marginBottom: 0 }}>
          {[['sessions', 'Sessions'], ['decisions', 'Decisions']].map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding: '6px 16px', borderRadius: 8, border: 'none', cursor: 'pointer',
              fontSize: 13, fontWeight: 500, transition: 'all 0.15s',
              background: tab === id ? 'var(--accent)' : 'transparent',
              color: tab === id ? '#fff' : 'var(--text-secondary)',
            }}>{label}</button>
          ))}
        </div>
      </div>
      {/* Tab border */}
      <div style={{ height: 1, background: 'var(--border)', flexShrink: 0, marginTop: 12 }} />
      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {tab === 'sessions' ? <SessionsTab /> : <DecisionsTab />}
      </div>
    </div>
  )
}
