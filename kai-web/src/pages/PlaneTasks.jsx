import { useEffect, useState } from 'react'

const WORKER = '/api'

const PRIORITY_COLOR = {
  urgent: '#ef4444',
  high:   '#f97316',
  medium: '#eab308',
  low:    '#6b7280',
  none:   '#6b7280',
}

const PRIORITY_LABEL = {
  urgent: 'Urgent',
  high:   'High',
  medium: 'Medium',
  low:    'Low',
  none:   '—',
}

const STATE_COLOR = {
  started:   '#6366f1',
  unstarted: '#6b7280',
  backlog:   '#374151',
}

export default function PlaneTasks() {
  const [projects, setProjects]     = useState([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState(null)
  const [form, setForm]             = useState({ name: '', description: '', priority: 'high', project_id: '' })
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted]   = useState(null)
  const [filter, setFilter]         = useState('all')

  useEffect(() => { load() }, [])

  function load() {
    setLoading(true)
    setError(null)
    fetch(`${WORKER}/plane/issues`)
      .then(r => r.json())
      .then(d => { setProjects(d.projects || []); setLoading(false) })
      .catch(e => { setError(e.message); setLoading(false) })
  }

  function submit(e) {
    e.preventDefault()
    if (!form.name.trim()) return
    setSubmitting(true)
    fetch(`${WORKER}/plane/issues`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
      .then(r => r.json())
      .then(d => {
        setSubmitted(d.name)
        setForm({ name: '', description: '', priority: 'high', project_id: form.project_id })
        setSubmitting(false)
        setTimeout(() => { setSubmitted(null); load() }, 2000)
      })
      .catch(() => setSubmitting(false))
  }

  const allIssues = projects.flatMap(p => p.issues.map(i => ({ ...i, project: p.name, project_id: p.id, identifier: p.identifier })))
  const filtered  = filter === 'all' ? allIssues : allIssues.filter(i => i.state_group === filter || i.priority === filter)

  const totalOpen = allIssues.length

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1100, margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>Plane</h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
            {loading ? 'Loading…' : `${totalOpen} open issues across ${projects.length} projects`}
          </p>
        </div>
        <button onClick={load} style={{
          padding: '6px 14px', borderRadius: 8, border: '1px solid var(--border)',
          background: 'transparent', color: 'var(--text-secondary)', fontSize: 12,
          cursor: 'pointer', fontFamily: 'inherit',
        }}>Refresh</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 24, alignItems: 'start' }}>

        {/* Issues list */}
        <div>
          {/* Filters */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
            {['all', 'started', 'unstarted', 'urgent', 'high'].map(f => (
              <button key={f} onClick={() => setFilter(f)} style={{
                padding: '4px 12px', borderRadius: 20, border: '1px solid var(--border)',
                background: filter === f ? 'var(--accent)' : 'transparent',
                color: filter === f ? '#fff' : 'var(--text-secondary)',
                fontSize: 12, cursor: 'pointer', fontFamily: 'inherit', textTransform: 'capitalize',
              }}>{f === 'all' ? `All (${totalOpen})` : f}</button>
            ))}
          </div>

          {error && <div style={{ padding: 16, borderRadius: 10, background: '#7f1d1d22', color: '#ef4444', fontSize: 13, marginBottom: 16 }}>{error}</div>}

          {loading
            ? <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: 24 }}>Loading issues…</div>
            : projects.map(p => {
                const pIssues = filtered.filter(i => i.project_id === p.id)
                if (!pIssues.length) return null
                return (
                  <div key={p.id} style={{ marginBottom: 24 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
                        background: 'var(--bg-elevated)', color: 'var(--text-secondary)',
                        padding: '2px 7px', borderRadius: 4, border: '1px solid var(--border)',
                      }}>{p.identifier}</span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{p.name}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{pIssues.length} open</span>
                    </div>
                    <div style={{ borderRadius: 10, border: '1px solid var(--border)', overflow: 'hidden' }}>
                      {pIssues.map((issue, idx) => (
                        <div key={issue.id} style={{
                          display: 'flex', alignItems: 'center', gap: 12,
                          padding: '10px 14px',
                          borderBottom: idx < pIssues.length - 1 ? '1px solid var(--border)' : 'none',
                          background: 'var(--bg-card)',
                        }}>
                          <span style={{
                            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                            background: STATE_COLOR[issue.state_group] || '#6b7280',
                          }} />
                          <span style={{ flex: 1, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.4 }}>
                            {issue.name}
                          </span>
                          <span style={{
                            fontSize: 11, fontWeight: 600, flexShrink: 0,
                            color: PRIORITY_COLOR[issue.priority] || '#6b7280',
                          }}>{PRIORITY_LABEL[issue.priority]}</span>
                          <span style={{
                            fontSize: 11, color: 'var(--text-subtle)', flexShrink: 0,
                            background: 'var(--bg-elevated)', padding: '2px 7px', borderRadius: 4,
                            border: '1px solid var(--border)',
                          }}>{issue.state}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })
          }
        </div>

        {/* Bug creation form */}
        <div style={{
          borderRadius: 12, border: '1px solid var(--border)',
          background: 'var(--bg-card)', padding: 20, position: 'sticky', top: 24,
        }}>
          <h2 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            Add Bug / Issue
          </h2>
          {submitted
            ? <div style={{ padding: 12, borderRadius: 8, background: '#14532d22', color: '#4ade80', fontSize: 13 }}>
                Created: {submitted}
              </div>
            : <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600, display: 'block', marginBottom: 4 }}>TITLE</label>
                  <input
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    placeholder="Describe the issue…"
                    required
                    style={{
                      width: '100%', boxSizing: 'border-box',
                      padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)',
                      background: 'var(--bg-base)', color: 'var(--text-primary)',
                      fontSize: 13, fontFamily: 'inherit', outline: 'none',
                    }}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600, display: 'block', marginBottom: 4 }}>DESCRIPTION</label>
                  <textarea
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                    placeholder="Steps to reproduce, expected vs actual…"
                    rows={4}
                    style={{
                      width: '100%', boxSizing: 'border-box',
                      padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)',
                      background: 'var(--bg-base)', color: 'var(--text-primary)',
                      fontSize: 13, fontFamily: 'inherit', outline: 'none', resize: 'vertical',
                    }}
                  />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600, display: 'block', marginBottom: 4 }}>PRIORITY</label>
                    <select
                      value={form.priority}
                      onChange={e => setForm(f => ({ ...f, priority: e.target.value }))}
                      style={{
                        width: '100%', padding: '8px 10px', borderRadius: 8,
                        border: '1px solid var(--border)', background: 'var(--bg-base)',
                        color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', cursor: 'pointer',
                      }}
                    >
                      <option value="urgent">Urgent</option>
                      <option value="high">High</option>
                      <option value="medium">Medium</option>
                      <option value="low">Low</option>
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600, display: 'block', marginBottom: 4 }}>PROJECT</label>
                    <select
                      value={form.project_id}
                      onChange={e => setForm(f => ({ ...f, project_id: e.target.value }))}
                      style={{
                        width: '100%', padding: '8px 10px', borderRadius: 8,
                        border: '1px solid var(--border)', background: 'var(--bg-base)',
                        color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', cursor: 'pointer',
                      }}
                    >
                      <option value="">KAI System</option>
                      {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={submitting || !form.name.trim()}
                  style={{
                    padding: '9px 16px', borderRadius: 8, border: 'none',
                    background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)',
                    color: '#fff', fontSize: 13, fontWeight: 600, cursor: submitting ? 'not-allowed' : 'pointer',
                    opacity: submitting || !form.name.trim() ? 0.5 : 1, fontFamily: 'inherit',
                  }}
                >{submitting ? 'Creating…' : 'Create Issue'}</button>
              </form>
          }
        </div>
      </div>
    </div>
  )
}
