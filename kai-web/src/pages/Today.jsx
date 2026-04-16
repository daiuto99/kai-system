import { useState, useEffect, useRef } from 'react'
import { Plus, Check, Send, Clock, ExternalLink, FileText, Link, Image, Lightbulb, Film, Archive } from 'lucide-react'
import { api } from '../lib/api'
import { ADVISORS, getAdvisor } from '../lib/advisors'

// ── Helpers ────────────────────────────────────────────────────────────────

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function fmtTime(ts) {
  if (!ts) return ''
  return new Date(parseFloat(ts) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ── Section header ─────────────────────────────────────────────────────────

function SectionHeader({ title, action }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
      <span className="section-title">{title}</span>
      {action}
    </div>
  )
}

// ── Projects ───────────────────────────────────────────────────────────────

const SDOT  = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }
const STEXT = { green: '#059669', yellow: '#d97706', red: '#dc2626' }
const SBG   = { green: 'rgba(16,185,129,0.08)', yellow: 'rgba(245,158,11,0.08)', red: 'rgba(239,68,68,0.08)' }

function daysAgo(dateStr) {
  if (!dateStr) return null
  const diff = Math.floor((Date.now() - new Date(dateStr)) / 86400000)
  if (diff === 0) return 'today'
  if (diff === 1) return '1d ago'
  return `${diff}d ago`
}

function ProjectsWidget() {
  const [projects, setProjects] = useState([])

  useEffect(() => {
    fetch('/api/projects')
      .then(r => r.json())
      .then(d => setProjects(d.projects || []))
      .catch(() => {})
  }, [])

  const sorted = [...projects].sort((a, b) => (b.updated || '').localeCompare(a.updated || ''))
  const visible = sorted.slice(0, 5)

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <SectionHeader
        title={
          <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            Projects
            {projects.length > 0 && (
              <span style={{
                fontSize: 10, fontWeight: 600, color: '#9ca3af',
                background: '#f3f4f6', borderRadius: 10, padding: '2px 7px',
                letterSpacing: '0.02em',
              }}>{projects.length}</span>
            )}
          </span>
        }
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, overflowY: 'auto' }}>
        {visible.map(p => {
          const pct = p.milestone_pct ?? null
          const ago = daysAgo(p.updated)
          const sc  = SDOT[p.status]  || '#9ca3af'
          const stc = STEXT[p.status] || '#6b7280'
          const sbg = SBG[p.status]   || 'rgba(156,163,175,0.08)'
          return (
            <div key={p.id} style={{
              padding: '10px 14px', borderRadius: 10,
              border: '1px solid #e8ecf1', background: '#ffffff',
              transition: 'border-color 0.15s, background 0.15s',
            }}
              onMouseEnter={e => { e.currentTarget.style.background = '#fff7ed'; e.currentTarget.style.borderColor = '#c2410c' }}
              onMouseLeave={e => { e.currentTarget.style.background = '#ffffff'; e.currentTarget.style.borderColor = '#e8ecf1' }}
            >
              {/* Row 1: name + status badge + version */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: sc, flexShrink: 0 }} />
                <span style={{ flex: 1, fontSize: 13, fontWeight: 600, color: '#1f2937', lineHeight: 1 }}>{p.name}</span>
                {p.version && (
                  <span style={{ fontSize: 10, fontWeight: 500, color: '#9ca3af', letterSpacing: '0.02em' }}>v{p.version}</span>
                )}
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 7px', borderRadius: 5,
                  background: sbg, color: stc, letterSpacing: '0.03em', textTransform: 'uppercase',
                }}>{p.status}</span>
              </div>

              {/* Row 2: milestone name + pct */}
              {p.milestone && (
                <div style={{ marginBottom: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: '#6b7280', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '75%' }}>
                      {p.milestone}
                    </span>
                    {pct !== null && (
                      <span style={{ fontSize: 11, fontWeight: 600, color: sc, flexShrink: 0 }}>{pct}%</span>
                    )}
                  </div>
                  {pct !== null && (
                    <div style={{ height: 3, borderRadius: 2, background: '#e8ecf1', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: sc, borderRadius: 2, transition: 'width 0.6s ease' }} />
                    </div>
                  )}
                </div>
              )}

              {/* Row 3: next action + updated */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: '#9ca3af', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '78%' }}>
                  {p.next}
                </span>
                {ago && (
                  <span style={{ fontSize: 10, color: '#c4c9d4', flexShrink: 0 }}>{ago}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Harmony (ambient design element) ──────────────────────────────────────

const HARMONY_GROUPS = [
  { name: 'Body',          ids: ['health-fitness', 'quality-of-life'] },
  { name: 'Mind',          ids: ['intellectual-life', 'emotional-life', 'character', 'spiritual-life'] },
  { name: 'Relationships', ids: ['love-relationship', 'parenting', 'social-life'] },
  { name: 'Work & Money',  ids: ['career', 'financial-life'] },
  { name: 'Life',          ids: ['life-vision', 'passion-sex'] },
]

function domainStatus(aspects) {
  const vals = Object.values(aspects || {}).map(a => a.status || 'green')
  if (vals.includes('red'))    return 'red'
  if (vals.includes('yellow')) return 'yellow'
  return 'green'
}

function groupScore(domains, ids) {
  const statuses = ids.map(id => {
    const d = domains.find(x => x.id === id)
    return d ? domainStatus(d.aspects) : 'green'
  })
  if (statuses.includes('red'))    return 'red'
  if (statuses.includes('yellow')) return 'yellow'
  return 'green'
}

function HarmonyWidget() {
  const [domains, setDomains] = useState([])
  const [counts,  setCounts]  = useState(null)
  const [open,    setOpen]    = useState(null)

  useEffect(() => {
    fetch('/api/harmony')
      .then(r => r.json())
      .then(data => {
        const list = data.domains || []
        setDomains(list)
        const c = { G: 0, Y: 0, R: 0 }
        list.forEach(d => {
          const s = domainStatus(d.aspects)
          if (s === 'green')  c.G++
          if (s === 'yellow') c.Y++
          if (s === 'red')    c.R++
        })
        setCounts(c)
      })
      .catch(() => {})
  }, [])

  const total = counts ? (counts.G + counts.Y + counts.R) || 1 : 1
  const pct = counts
    ? { G: (counts.G / total) * 100, Y: (counts.Y / total) * 100, R: (counts.R / total) * 100 }
    : { G: 33, Y: 34, R: 33 }

  const dominant = !counts ? null
    : counts.R / total > 0.4 ? 'red'
    : counts.Y / total > 0.4 ? 'yellow'
    : 'green'
  const img = dominant === 'red' ? '/harmony-red.png'
    : dominant === 'yellow' ? '/harmony-yellow.png'
    : '/harmony-green.png'

  const DOT = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }
  const BG  = { green: 'rgba(16,185,129,0.08)', yellow: 'rgba(245,158,11,0.08)', red: 'rgba(239,68,68,0.08)' }

  return (
    <div style={{
      flex: 1, background: '#fafbfc', borderRadius: 16, border: '1px solid #e8ecf1',
      padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <span className="section-title">Harmony</span>
        <img src={img} alt="harmony" style={{ width: 32, height: 32, objectFit: 'contain', opacity: counts ? 1 : 0.15, transition: 'opacity 0.4s' }} />
      </div>

      {/* Segmented bar */}
      <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', height: 6, borderRadius: 4, overflow: 'hidden', gap: 2 }}>
          <div style={{ width: `${pct.G}%`, background: '#10b981', borderRadius: '4px 0 0 4px', transition: 'width 0.8s ease' }} />
          <div style={{ width: `${pct.Y}%`, background: '#f59e0b', transition: 'width 0.8s ease' }} />
          <div style={{ width: `${pct.R}%`, background: '#ef4444', borderRadius: '0 4px 4px 0', transition: 'width 0.8s ease' }} />
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          {[['G', '#10b981'], ['Y', '#f59e0b'], ['R', '#ef4444']].map(([k, c]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: c, flexShrink: 0 }} />
              <span style={{ fontSize: 10, color: '#9ca3af', fontWeight: 500 }}>{counts ? (counts[k] || 0) : '—'}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Category groups */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 3 }}>
        {HARMONY_GROUPS.map(g => {
          const score = groupScore(domains, g.ids)
          const isOpen = open === g.name
          const groupDomains = g.ids.map(id => domains.find(d => d.id === id)).filter(Boolean)
          return (
            <div key={g.name}>
              {/* Category row */}
              <div
                onClick={() => setOpen(isOpen ? null : g.name)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 8px', borderRadius: 8, cursor: 'pointer',
                  background: isOpen ? BG[score] : 'transparent',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={e => { if (!isOpen) e.currentTarget.style.background = '#f3f4f6' }}
                onMouseLeave={e => { if (!isOpen) e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: DOT[score], flexShrink: 0,
                  boxShadow: `0 0 0 2px ${BG[score]}`,
                }} />
                <span style={{ flex: 1, fontSize: 12, fontWeight: 600, color: '#374151' }}>{g.name}</span>
                <span style={{ fontSize: 10, color: '#9ca3af' }}>{g.ids.length}</span>
                <span style={{ fontSize: 10, color: '#c4c9d4', marginLeft: 2 }}>{isOpen ? '▲' : '▾'}</span>
              </div>

              {/* Expanded domains */}
              {isOpen && (
                <div style={{ paddingLeft: 16, display: 'flex', flexDirection: 'column', gap: 1, marginBottom: 4 }}>
                  {groupDomains.map(d => {
                    const s = domainStatus(d.aspects)
                    return (
                      <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 8px', borderRadius: 6 }}>
                        <span style={{ fontSize: 12, lineHeight: 1, flexShrink: 0 }}>{d.icon}</span>
                        <span style={{ flex: 1, fontSize: 11, color: '#6b7280' }}>{d.name}</span>
                        <span style={{ width: 5, height: 5, borderRadius: '50%', background: DOT[s], flexShrink: 0 }} />
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Today's Plan ───────────────────────────────────────────────────────────

const PRIORITY_COLOR = { 1: '#ef4444', 2: '#f97316', 3: '#f59e0b', 4: '#9ca3af' }
const PRIORITY_LABEL = { 1: 'Critical', 2: 'Important', 3: 'Normal', 4: 'Defer' }
const PRIORITY_NEXT  = { 1: 2, 2: 3, 3: 4, 4: 1 }

function TaskRow({ task, onDone, onPriorityChange }) {
  const [priority, setPriority] = useState(task.priority || 4)
  const [hover,    setHover]    = useState(false)
  const [gone,     setGone]     = useState(false)

  function cyclePriority(e) {
    e.stopPropagation()
    const next = PRIORITY_NEXT[priority]
    setPriority(next)
    fetch(`/api/tasks/${task.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ priority: next }),
    }).then(() => onPriorityChange && onPriorityChange(task.id, next))
  }

  function complete(e) {
    e.stopPropagation()
    setGone(true)
    fetch(`/api/tasks/${task.id}/complete`, { method: 'POST' })
      .then(() => onDone(task.id))
  }

  function del(e) {
    e.stopPropagation()
    setGone(true)
    fetch(`/api/tasks/${task.id}`, { method: 'DELETE' })
      .then(() => onDone(task.id))
  }

  if (gone) return null

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '7px 10px', borderRadius: 8, background: '#ffffff',
        border: '1px solid #e8ecf1', transition: 'border-color 0.15s',
        borderColor: hover ? '#d1d5db' : '#e8ecf1',
      }}
    >
      {/* Priority dot */}
      <div
        onClick={cyclePriority}
        title={`${PRIORITY_LABEL[priority]} — click to change`}
        style={{
          width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
          border: `1.5px solid ${PRIORITY_COLOR[priority]}`,
          background: priority < 4 ? PRIORITY_COLOR[priority] + '25' : 'transparent',
          cursor: 'pointer', transition: 'all 0.15s',
        }}
      />
      {/* Content */}
      <span style={{ flex: 1, fontSize: 12, color: '#1f2937', lineHeight: 1.4 }}>{task.content}</span>
      {/* Actions — visible on hover */}
      <div style={{ display: 'flex', gap: 4, opacity: hover ? 1 : 0, transition: 'opacity 0.15s', flexShrink: 0 }}>
        <button onClick={complete} title="Mark complete" style={{
          width: 24, height: 24, borderRadius: 6, border: '1px solid #e8ecf1',
          background: '#f9fafb', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 12, color: '#10b981', fontWeight: 700, padding: 0,
        }}>✓</button>
        <button onClick={del} title="Delete" style={{
          width: 24, height: 24, borderRadius: 6, border: '1px solid #e8ecf1',
          background: '#f9fafb', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, color: '#ef4444', fontWeight: 700, padding: 0,
        }}>✕</button>
      </div>
    </div>
  )
}

function groupByPriority(tasks) {
  const order = [1, 2, 3, 4]
  const groups = {}
  order.forEach(p => { groups[p] = [] })
  tasks.forEach(t => {
    const p = t.priority || 4
    groups[p].push(t)
  })
  return order.filter(p => groups[p].length > 0).map(p => ({ priority: p, tasks: groups[p] }))
}

function TodayPlayWidget() {
  const [today,   setToday]   = useState([])
  const [inbox,   setInbox]   = useState([])
  const [loading, setLoading] = useState(true)
  const [tab,     setTab]     = useState('today')

  useEffect(() => {
    fetch('/api/tasks')
      .then(r => r.json())
      .then(d => {
        setToday(d.today || [])
        setInbox(d.inbox || [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleDone = (id) => {
    setToday(prev => prev.filter(t => t.id !== id))
    setInbox(prev => prev.filter(t => t.id !== id))
  }

  const handlePriorityChange = (id, newPriority) => {
    setToday(prev => prev.map(t => t.id === id ? { ...t, priority: newPriority } : t))
    setInbox(prev => prev.map(t => t.id === id ? { ...t, priority: newPriority } : t))
  }

  const tasks = tab === 'today' ? today : inbox
  const sections = groupByPriority(tasks)

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexShrink: 0 }}>
        <span className="section-title">Today's Plan</span>
        <div style={{ display: 'flex', background: '#f3f4f6', borderRadius: 7, padding: 2, gap: 2 }}>
          {[['today', `Today ${today.length ? `(${today.length})` : ''}`], ['inbox', `Inbox ${inbox.length ? `(${inbox.length})` : ''}`]].map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)} style={{
              fontSize: 10, fontWeight: 600, padding: '3px 9px', borderRadius: 5, border: 'none',
              background: tab === key ? '#ffffff' : 'transparent',
              color: tab === key ? '#1f2937' : '#9ca3af',
              cursor: 'pointer', fontFamily: 'inherit',
              boxShadow: tab === key ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              transition: 'all 0.15s',
            }}>{label}</button>
          ))}
        </div>
      </div>

      {/* Task list grouped by priority */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
        {loading ? (
          <p style={{ fontSize: 12, color: '#9ca3af', padding: '8px 0' }}>Loading…</p>
        ) : tasks.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 16px', color: '#9ca3af' }}>
            <p style={{ fontSize: 13, margin: 0 }}>
              {tab === 'today' ? 'Nothing scheduled for today. Tell KAI what matters →' : 'Inbox is clear.'}
            </p>
          </div>
        ) : (
          sections.map(({ priority, tasks: sectionTasks }) => (
            <div key={priority} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{
                  width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                  background: PRIORITY_COLOR[priority],
                }} />
                <span style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.08em',
                  textTransform: 'uppercase', color: PRIORITY_COLOR[priority],
                }}>{PRIORITY_LABEL[priority]}</span>
                <div style={{ flex: 1, height: 1, background: PRIORITY_COLOR[priority] + '30' }} />
                <span style={{ fontSize: 9, color: '#c4c9d4' }}>{sectionTasks.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {sectionTasks.map(t => <TaskRow key={t.id} task={t} onDone={handleDone} onPriorityChange={handlePriorityChange} />)}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      {!loading && (today.length > 0 || inbox.length > 0) && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #f3f4f6', flexShrink: 0 }}>
          <span style={{ fontSize: 10, color: '#c4c9d4' }}>
            {today.length} today · {inbox.length} in inbox · KAI manages this
          </span>
        </div>
      )}
    </div>
  )
}

// ── Today's Intention (ambient display, click to edit) ─────────────────────

function CheckInWidget() {
  const [intent, setIntent] = useState('')
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(false)
  const textareaRef = useRef(null)

  useEffect(() => {
    fetch('/api/checkin')
      .then(r => r.json())
      .then(d => {
        if (d.date === new Date().toISOString().slice(0, 10)) setIntent(d.intent || '')
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (editing) textareaRef.current?.focus()
  }, [editing])

  function save() {
    setEditing(false)
    fetch('/api/checkin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent }),
    }).then(() => { setSaved(true); setTimeout(() => setSaved(false), 1500) })
  }

  return (
    <div style={{
      flex: 1, background: '#fafbfc', borderRadius: 16, border: '1px solid #e8ecf1',
      padding: '16px 20px', display: 'flex', flexDirection: 'column',
      cursor: editing ? 'default' : 'text',
    }}
      onClick={() => { if (!editing) setEditing(true) }}
    >
      {/* Label row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span className="section-title">Intention</span>
        {saved && <span style={{ fontSize: 11, color: '#10b981', fontWeight: 500 }}>Saved ✓</span>}
        {!editing && !saved && <span style={{ fontSize: 10, color: '#c2410c', opacity: 0.5 }}>edit</span>}
      </div>

      {editing ? (
        <textarea
          ref={textareaRef}
          value={intent}
          onChange={e => setIntent(e.target.value)}
          onBlur={save}
          onKeyDown={e => { if (e.key === 'Escape') save() }}
          placeholder="What is your intention for today?"
          style={{
            flex: 1, width: '100%', fontSize: 13, color: '#1f2937', lineHeight: 1.6,
            background: 'transparent', border: 'none', outline: 'none', resize: 'none',
            fontFamily: 'inherit', minHeight: 60,
          }}
        />
      ) : (
        <div style={{
          flex: 1, borderLeft: '2px solid rgba(194,65,12,0.25)', paddingLeft: 12,
          display: 'flex', alignItems: intent ? 'flex-start' : 'center',
        }}>
          {intent ? (
            <p style={{ fontSize: 13, color: '#4b5563', lineHeight: 1.6, fontStyle: 'italic', margin: 0 }}>
              {intent}
            </p>
          ) : (
            <p style={{ fontSize: 13, color: '#c4c9d4', lineHeight: 1.6, fontStyle: 'italic', margin: 0 }}>
              Set your intention for today…
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Chat Widget ────────────────────────────────────────────────────────────

function AdvisorAvatar({ advisor, size, isActive }) {
  const colors = {
    kai:   { from: '#1e3a5f', to: '#2d5a8e' },
    ember: { from: '#7f1d1d', to: '#be123c' },
    beats: { from: '#431407', to: '#9a3412' },
    doc:   { from: '#064e3b', to: '#059669' },
    coach: { from: '#713f12', to: '#d97706' },
    biz:   { from: '#3b0764', to: '#7c3aed' },
  }
  const grad = colors[advisor.id] || { from: '#374151', to: '#6b7280' }

  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      background: advisor.avatar ? 'transparent' : `linear-gradient(135deg, ${grad.from} 0%, ${grad.to} 100%)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexShrink: 0, overflow: 'hidden',
      boxShadow: isActive
        ? `0 0 0 2px #ffffff, 0 0 0 3.5px ${advisor.color}`
        : '0 1px 4px rgba(0,0,0,0.18)',
      transition: 'all 0.2s ease',
      fontSize: size * 0.4,
    }}>
      {advisor.avatar
        ? <img src={advisor.avatar} alt={advisor.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        : advisor.emoji
      }
    </div>
  )
}

function ChatWidget() {
  const [advisor, setAdvisor] = useState(getAdvisor('kai'))
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.getChannelHistory(advisor.channel)
      .then(d => setMessages(d.messages || []))
      .catch(() => {})
  }, [advisor.channel])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  async function send() {
    const text = input.trim()
    if (!text || thinking) return
    setInput('')
    setMessages(p => [...p, { role: 'user', content: text, ts: String(Date.now() / 1000) }])
    setThinking(true)
    try {
      const d = await api.sendMessage(text, advisor.channel)
      setMessages(p => [...p, { role: 'assistant', content: d.reply || d.message || '', ts: String(Date.now() / 1000) }])
    } catch {
      setMessages(p => [...p, { role: 'assistant', content: 'Something went wrong.', error: true, ts: String(Date.now() / 1000) }])
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  return (
    <div style={{
      background: '#ffffff', borderRadius: 20, boxShadow: '0 4px 20px rgba(0,0,0,0.06)',
      border: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column',
      overflow: 'hidden', height: '100%',
    }}>

      {/* ── Team header — single row ── */}
      <div style={{
        flexShrink: 0, borderBottom: '1px solid #e8ecf1',
        padding: '12px 20px',
        background: `linear-gradient(to right, ${advisor.color}06 0%, transparent 50%)`,
        display: 'flex', alignItems: 'center', gap: 10,
        overflowX: 'auto',
      }} className="no-scrollbar">
        {ADVISORS.map(a => {
          const active = advisor.id === a.id
          return (
            <button
              key={a.id}
              onClick={() => setAdvisor(a)}
              title={a.name}
              style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5,
                background: 'none', border: 'none', cursor: 'pointer',
                padding: '2px 8px', borderRadius: 10, transition: 'all 0.15s',
                opacity: active ? 1 : 0.45, flexShrink: 0,
              }}
              onMouseEnter={e => e.currentTarget.style.opacity = '1'}
              onMouseLeave={e => { if (!active) e.currentTarget.style.opacity = '0.45' }}
            >
              <AdvisorAvatar advisor={a} size={active ? 42 : 34} isActive={active} />
              <span style={{
                fontSize: 10, fontWeight: active ? 600 : 400, lineHeight: 1,
                color: active ? advisor.color : '#9ca3af',
                whiteSpace: 'nowrap',
              }}>
                {a.name}
              </span>
            </button>
          )
        })}
      </div>

      {/* Messages */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 10, background: '#fafbfc' }}>
        {messages.length === 0 && !thinking && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12, color: '#9ca3af' }}>
            <AdvisorAvatar advisor={advisor} size={52} isActive={false} />
            <p style={{ fontSize: 13, textAlign: 'center', maxWidth: 220, lineHeight: 1.6, color: '#9ca3af' }}>{advisor.intro}</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', alignItems: 'flex-end', gap: 8 }}>
            {msg.role !== 'user' && (
              <AdvisorAvatar advisor={advisor} size={26} isActive={false} />
            )}
            <div style={{
              maxWidth: '78%', padding: '9px 13px', borderRadius: msg.role === 'user' ? '12px 12px 4px 12px' : '12px 12px 12px 4px',
              fontSize: 13, lineHeight: 1.5,
              background: msg.role === 'user' ? '#fff7ed' : '#ffffff',
              color: '#1f2937',
              border: msg.role === 'user' ? '1px solid rgba(194,65,12,0.12)' : '1px solid #e8ecf1',
            }}>
              <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{msg.content}</p>
              {msg.ts && <p style={{ fontSize: 10, opacity: 0.35, marginTop: 4, textAlign: 'right', marginBottom: 0 }}>{fmtTime(msg.ts)}</p>}
            </div>
          </div>
        ))}
        {thinking && (
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
            <AdvisorAvatar advisor={advisor} size={26} isActive={false} />
            <div style={{ background: '#ffffff', border: '1px solid #e8ecf1', borderRadius: '12px 12px 12px 4px', padding: '10px 14px' }}>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                {[0, 150, 300].map(d => (
                  <span key={d} style={{ width: 6, height: 6, borderRadius: '50%', background: '#c4c9d4', display: 'inline-block', animation: `bounce 1s ${d}ms infinite` }} />
                ))}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{ flexShrink: 0, padding: '12px 16px', borderTop: '1px solid #e8ecf1', display: 'flex', gap: 10, background: '#ffffff' }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={`Message ${advisor.name}…`}
          style={{
            flex: 1, padding: '10px 14px', borderRadius: 10, border: '1px solid #e8ecf1',
            background: '#fafbfc', color: '#1f2937', fontSize: 13, fontFamily: 'inherit', outline: 'none',
            transition: 'border-color 0.15s',
          }}
          onFocus={e => e.target.style.borderColor = advisor.color}
          onBlur={e => e.target.style.borderColor = '#e8ecf1'}
        />
        <button
          onClick={send}
          disabled={!input.trim() || thinking}
          style={{
            padding: '10px 16px', borderRadius: 10, border: 'none',
            background: input.trim() && !thinking
              ? `linear-gradient(135deg, ${advisor.color} 0%, ${advisor.color}cc 100%)`
              : '#e8ecf1',
            color: input.trim() && !thinking ? '#ffffff' : '#9ca3af',
            fontSize: 13, fontWeight: 500, cursor: input.trim() && !thinking ? 'pointer' : 'default',
            transition: 'all 0.2s', fontFamily: 'inherit',
          }}
        >
          Send
        </button>
      </div>
    </div>
  )
}

// ── The Lot (thumbnail grid) ───────────────────────────────────────────────

const CAT_ICON = {
  'Links':     { icon: Link,      color: '#3b82f6', bg: '#eff6ff' },
  'Notes':     { icon: FileText,  color: '#8b5cf6', bg: '#f5f3ff' },
  'Images':    { icon: Image,     color: '#ec4899', bg: '#fdf2f8' },
  'Ideas':     { icon: Lightbulb, color: '#f59e0b', bg: '#fffbeb' },
  'Videos':    { icon: Film,      color: '#10b981', bg: '#ecfdf5' },
  'Documents': { icon: FileText,  color: '#6b7280', bg: '#f9fafb' },
}

function LotThumbnail({ item }) {
  const cat = item.category || 'Notes'
  const { icon: Icon, color, bg } = CAT_ICON[cat] || CAT_ICON['Notes']
  const isUrl = item.url || (item.content && item.content.startsWith('http'))
  const url = item.url || (isUrl ? item.content : null)

  return (
    <div style={{
      background: '#ffffff', borderRadius: 12, border: '1px solid #e8ecf1',
      overflow: 'hidden', cursor: 'pointer', transition: 'all 0.2s',
    }}
      onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)'; e.currentTarget.style.transform = 'translateY(-2px)' }}
      onMouseLeave={e => { e.currentTarget.style.boxShadow = 'none'; e.currentTarget.style.transform = 'none' }}
    >
      <div style={{ height: 72, background: bg, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
        {item.thumbnail ? (
          <img src={item.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <Icon size={24} color={color} strokeWidth={1.5} />
        )}
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer"
            onClick={e => e.stopPropagation()}
            style={{ position: 'absolute', top: 6, right: 6, background: 'rgba(255,255,255,0.9)', borderRadius: 6, padding: '3px 5px', display: 'flex' }}>
            <ExternalLink size={11} color="#6b7280" />
          </a>
        )}
      </div>
      <div style={{ padding: '8px 10px' }}>
        <p style={{ fontSize: 12, fontWeight: 500, color: '#1f2937', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3 }}>
          {item.title || item.content?.slice(0, 35) || 'Untitled'}
        </p>
        <p style={{ fontSize: 10, color: '#9ca3af', margin: '2px 0 0', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 500 }}>
          {cat}
        </p>
      </div>
    </div>
  )
}

function LotWidget() {
  const [items, setItems] = useState([])

  useEffect(() => {
    api.getParkingLot?.()
      .then(d => setItems(d.items || []))
      .catch(() => {})
  }, [])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 4fr', gap: 12 }}>
      <div style={{
        background: '#fafbfc', padding: 20, display: 'flex', alignItems: 'center',
        justifyContent: 'center', border: '2px dashed #d1d5db', borderRadius: 16,
        transition: 'all 0.3s', cursor: 'default', minHeight: 100,
      }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = '#c2410c'; e.currentTarget.style.background = '#fff7ed' }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = '#d1d5db'; e.currentTarget.style.background = '#fafbfc' }}
      >
        <div style={{ textAlign: 'center' }}>
          <Archive size={20} color="#9ca3af" style={{ margin: '0 auto 6px' }} />
          <p style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: 500, margin: 0 }}>Drop Zone</p>
        </div>
      </div>

      <div style={{ background: '#fafbfc', padding: 20, borderRadius: 16, border: '1px solid #e8ecf1' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <span className="section-title">The Lot</span>
          <span style={{ fontSize: 11, color: '#9ca3af' }}>{items.length} items</span>
        </div>
        {items.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '20px 0', color: '#9ca3af' }}>
            <p style={{ fontSize: 13 }}>Nothing in The Lot</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 10 }}>
            {items.slice(0, 12).map((item, i) => <LotThumbnail key={i} item={item} />)}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div style={{ height: '100%', background: '#f8f9fa', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>

        <div className="kai-card" style={{ padding: 24 }}>
          <p style={{ fontSize: 22, fontWeight: 300, color: '#1f2937', letterSpacing: '-0.02em', marginBottom: 20 }}>
            {greeting()}, <strong style={{ fontWeight: 600 }}>Leo</strong>
          </p>

          {/* Desktop grid */}
          <div className="hidden md:grid" style={{ gridTemplateColumns: '1.2fr 0.6fr 1.3fr', gridTemplateRows: '1fr 1fr', gap: 12, minHeight: 480 }}>
            <div style={{ gridColumn: 1, gridRow: 1, display: 'flex' }}><ProjectsWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 1, display: 'flex' }}><HarmonyWidget /></div>
            <div style={{ gridColumn: 3, gridRow: '1 / 3' }}><ChatWidget /></div>
            <div style={{ gridColumn: 1, gridRow: 2, display: 'flex' }}><TodayPlayWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 2, display: 'flex' }}><CheckInWidget /></div>
          </div>

          {/* Mobile stack */}
          <div className="md:hidden flex flex-col" style={{ gap: 12 }}>
            <CheckInWidget />
            <TodayPlayWidget />
            <HarmonyWidget />
            <ProjectsWidget />
            <div style={{ minHeight: 400 }}><ChatWidget /></div>
          </div>
        </div>

        <div className="kai-card" style={{ padding: 20 }}>
          <LotWidget />
        </div>

      </div>

      <style>{`
        @keyframes bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-4px); }
        }
      `}</style>
    </div>
  )
}
