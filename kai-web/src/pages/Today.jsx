import { useState, useEffect, useRef } from 'react'
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

function weatherEmoji(theme) {
  const m = { clear: '☀️', clouds: '☁️', rain: '🌧️', drizzle: '🌦️', thunderstorm: '⛈️', snow: '❄️', mist: '🌫️', fog: '🌫️', haze: '🌫️' }
  return m[(theme || '').toLowerCase()] || '🌤️'
}

// ── Section header ─────────────────────────────────────────────────────────

function SectionHeader({ title, action }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
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

  const visible = [...projects]
    .sort((a, b) => (b.updated || '').localeCompare(a.updated || ''))
    .slice(0, 5)

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <SectionHeader
        title={
          <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            Projects
            {projects.length > 0 && (
              <span style={{ fontSize: 10, fontWeight: 600, color: '#9ca3af', background: '#f3f4f6', borderRadius: 10, padding: '2px 7px' }}>
                {projects.length}
              </span>
            )}
          </span>
        }
      />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, overflow: 'hidden' }}>
        {visible.map(p => {
          const sc  = SDOT[p.status]  || '#9ca3af'
          const stc = STEXT[p.status] || '#6b7280'
          const sbg = SBG[p.status]   || 'rgba(156,163,175,0.08)'
          const ago = daysAgo(p.updated)
          const pct = p.milestone_pct ?? null
          return (
            <div key={p.id} style={{ padding: '6px 11px', borderRadius: 10, border: '1px solid #e8ecf1', background: '#ffffff', transition: 'all 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.background = '#fff7ed'; e.currentTarget.style.borderColor = '#c2410c' }}
              onMouseLeave={e => { e.currentTarget.style.background = '#ffffff'; e.currentTarget.style.borderColor = '#e8ecf1' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: sc, flexShrink: 0 }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: '#1f2937' }}>{p.name}</span>
                {p.milestone && <>
                  <span style={{ fontSize: 11, color: '#d1d5db' }}>|</span>
                  <span style={{ fontSize: 11, color: '#6b7280', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.milestone}</span>
                </>}
                {p.version && <span style={{ fontSize: 10, color: '#9ca3af', flexShrink: 0 }}>v{p.version}</span>}
                <span style={{ fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4, flexShrink: 0, background: sbg, color: stc, textTransform: 'uppercase', letterSpacing: '0.03em' }}>{p.status}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 10, color: '#9ca3af', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.next}</span>
                {pct !== null && <>
                  <div style={{ width: 40, height: 2, borderRadius: 1, background: '#e8ecf1', overflow: 'hidden', flexShrink: 0 }}>
                    <div style={{ height: '100%', width: `${pct}%`, background: sc }} />
                  </div>
                  <span style={{ fontSize: 10, fontWeight: 600, color: sc, flexShrink: 0 }}>{pct}%</span>
                </>}
                {ago && <span style={{ fontSize: 10, color: '#c4c9d4', flexShrink: 0 }}>{ago}</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Harmony + Weather + Quote + Intention ──────────────────────────────────

const HARMONY_GROUPS = [
  { name: 'Body',          ids: ['health-fitness', 'quality-of-life'] },
  { name: 'Mind',          ids: ['intellectual-life', 'emotional-life', 'character', 'spiritual-life'] },
  { name: 'Relationships', ids: ['love-relationship', 'parenting', 'social-life'] },
  { name: 'Work & Money',  ids: ['career', 'financial-life'] },
  { name: 'Life',          ids: ['life-vision', 'passion-sex'] },
]

const HCOL = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444', gray: '#d1d5db' }

function domainStatus(aspects) {
  const vals = Object.values(aspects || {}).map(a => a.status || 'green')
  if (vals.includes('red'))    return 'red'
  if (vals.includes('yellow')) return 'yellow'
  return 'green'
}

function HarmonyWidget() {
  const [domains, setDomains] = useState([])
  const [weather, setWeather] = useState(null)
  const [quote,   setQuote]   = useState(null)

  useEffect(() => {
    fetch('/api/harmony').then(r => r.json()).then(d => setDomains(d.domains || [])).catch(() => {})
    fetch('/api/weather').then(r => r.json()).then(setWeather).catch(() => {})
    fetch('/api/stoic-quote').then(r => r.json()).then(setQuote).catch(() => {})
  }, [])

  return (
    <div style={{ flex: 1, background: '#fafbfc', borderRadius: 16, border: '1px solid #e8ecf1', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 7, overflow: 'hidden' }}>
      <span className="section-title" style={{ flexShrink: 0 }}>Harmony</span>

      {/* Category rows — dot per domain on right */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, flexShrink: 0 }}>
        {HARMONY_GROUPS.map(g => {
          const domainStatuses = g.ids.map(id => {
            const d = domains.find(x => x.id === id)
            return d ? domainStatus(d.aspects) : null
          })
          const gs = domainStatuses.filter(Boolean).includes('red') ? 'red'
            : domainStatuses.filter(Boolean).includes('yellow') ? 'yellow'
            : domainStatuses.filter(Boolean).length ? 'green' : 'gray'
          return (
            <div key={g.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 7, height: 7, borderRadius: '50%', background: HCOL[gs], flexShrink: 0 }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: '#374151', flex: 1 }}>{g.name}</span>
              <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                {g.ids.map((id, idx) => {
                  const s = domainStatuses[idx]
                  const label = id.replace(/-/g, ' ')
                  return (
                    <span key={id} title={label} style={{
                      width: 10, height: 10, borderRadius: 3, flexShrink: 0,
                      background: s ? HCOL[s] : '#e8ecf1',
                      opacity: s ? 1 : 0.4,
                    }} />
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* Dark Sky weather + date + stoic quote */}
      <WeatherCard weather={weather} quote={quote} />
    </div>
  )
}

function skyGradient(theme, hour) {
  if (hour < 5)  return ['#0f0c29', '#302b63']
  if (hour < 7)  return ['#614385', '#516395']
  if (hour < 9)  return ['#ee0979', '#ff6a00']
  if (hour < 17) {
    if (theme === 'rain' || theme === 'drizzle')  return ['#373b44', '#4286f4']
    if (theme === 'thunderstorm')                  return ['#1a1a2e', '#16213e']
    if (theme === 'snow')                          return ['#83a4d4', '#b6fbff']
    if (theme === 'clouds')                        return ['#4b6cb7', '#182848']
    return ['#2980b9', '#2c3e50']
  }
  if (hour < 20) return ['#f7971e', '#c94b4b']
  return ['#0f0c29', '#302b63']
}

function WeatherCard({ weather, quote }) {
  const hour = new Date().getHours()
  const [from, to] = skyGradient(weather?.theme, hour)
  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  return (
    <div style={{
      borderRadius: 12, overflow: 'hidden', flexShrink: 0,
      background: `linear-gradient(135deg, ${from} 0%, ${to} 100%)`,
      padding: '12px 14px',
    }}>
      <div style={{ fontSize: 9, color: 'rgba(255,255,255,0.65)', marginBottom: 6, letterSpacing: '0.04em', textTransform: 'uppercase' }}>{today}</div>
      {weather && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ fontSize: 26, lineHeight: 1 }}>{weatherEmoji(weather.theme)}</span>
          <div style={{ flex: 1 }}>
            <span style={{ fontSize: 22, fontWeight: 700, color: '#ffffff' }}>{weather.temp}°</span>
            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.7)', marginLeft: 6, textTransform: 'capitalize' }}>{weather.condition}</span>
          </div>
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.55)' }}>{weather.humidity}%</span>
        </div>
      )}
      {quote && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.15)', paddingTop: 8 }}>
          <p style={{ fontSize: 10, fontStyle: 'italic', color: 'rgba(255,255,255,0.85)', margin: '0 0 3px', lineHeight: 1.5 }}>"{quote.content}"</p>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.5)' }}>— {quote.author}</span>
        </div>
      )}
    </div>
  )
}

// ── Intention ──────────────────────────────────────────────────────────────

function IntentionSection() {
  const [intent,  setIntent]  = useState('')
  const [editing, setEditing] = useState(false)
  const [saved,   setSaved]   = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    fetch('/api/checkin').then(r => r.json()).then(d => {
      if (d.date === new Date().toISOString().slice(0, 10)) setIntent(d.intent || '')
    }).catch(() => {})
  }, [])

  useEffect(() => { if (editing) ref.current?.focus() }, [editing])

  function save() {
    setEditing(false)
    fetch('/api/checkin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ intent }) })
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1500) })
  }

  return (
    <div style={{ borderTop: '1px solid #e8ecf1', paddingTop: 7, flex: 1, minHeight: 0, cursor: editing ? 'default' : 'text' }}
      onClick={() => { if (!editing) setEditing(true) }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9ca3af' }}>Intention</span>
        {saved && <span style={{ fontSize: 10, color: '#10b981' }}>Saved ✓</span>}
        {!editing && !saved && <span style={{ fontSize: 10, color: '#c2410c', opacity: 0.4 }}>edit</span>}
      </div>
      {editing ? (
        <textarea ref={ref} value={intent} onChange={e => setIntent(e.target.value)}
          onBlur={save} onKeyDown={e => { if (e.key === 'Escape') save() }}
          placeholder="What is your intention for today?"
          style={{ width: '100%', fontSize: 11, color: '#1f2937', lineHeight: 1.5, background: 'transparent', border: 'none', outline: 'none', resize: 'none', fontFamily: 'inherit', minHeight: 36 }}
        />
      ) : (
        <p style={{ fontSize: 11, margin: 0, lineHeight: 1.5, fontStyle: 'italic', color: intent ? '#4b5563' : '#c4c9d4', borderLeft: '2px solid rgba(194,65,12,0.2)', paddingLeft: 8 }}>
          {intent || 'Set your intention for today…'}
        </p>
      )}
    </div>
  )
}

// ── Habits (icon grid) ─────────────────────────────────────────────────────

function HabitsWidget() {
  const [habits,  setHabits]  = useState([])
  const [loading, setLoading] = useState(true)
  const today = new Date().toISOString().slice(0, 10)

  useEffect(() => {
    fetch('/api/habits').then(r => r.json())
      .then(d => setHabits((d.habits || d || []).slice(0, 12)))
      .catch(() => {}).finally(() => setLoading(false))
  }, [])

  function toggle(habit) {
    const done = habit.completions?.includes(today)
    fetch(`/api/habits/${habit.id}/complete`, { method: done ? 'DELETE' : 'POST' })
      .then(r => r.json())
      .then(() => {
        setHabits(prev => prev.map(h => h.id === habit.id
          ? { ...h, completions: done ? h.completions.filter(c => c !== today) : [...(h.completions || []), today] }
          : h))
      }).catch(() => {})
  }

  const done = habits.filter(h => h.completions?.includes(today)).length
  const cols = habits.length <= 6 ? 3 : habits.length <= 8 ? 4 : 4

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexShrink: 0 }}>
        <span className="section-title">Habits</span>
        {habits.length > 0 && <span style={{ fontSize: 10, color: '#9ca3af' }}>{done}/{habits.length}</span>}
      </div>
      {loading ? (
        <p style={{ fontSize: 12, color: '#9ca3af' }}>Loading…</p>
      ) : habits.length === 0 ? (
        <p style={{ fontSize: 12, color: '#9ca3af', fontStyle: 'italic' }}>Add habits at <span style={{ color: '#6b7280' }}>:6842</span></p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 6, overflow: 'hidden' }}>
          {habits.map(h => {
            const isDone = h.completions?.includes(today)
            return (
              <div key={h.id} onClick={() => toggle(h)} title={h.name} style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5,
                padding: '10px 4px', borderRadius: 10, cursor: 'pointer', userSelect: 'none',
                background: isDone ? '#ecfdf5' : '#f9fafb',
                border: `1.5px solid ${isDone ? '#a7f3d0' : '#e8ecf1'}`,
                transition: 'all 0.15s',
              }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = isDone ? '#10b981' : '#c2410c'; e.currentTarget.style.background = isDone ? '#d1fae5' : '#fff7ed' }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = isDone ? '#a7f3d0' : '#e8ecf1'; e.currentTarget.style.background = isDone ? '#ecfdf5' : '#f9fafb' }}
              >
                <div style={{
                  width: 26, height: 26, borderRadius: '50%',
                  background: isDone ? '#10b981' : '#e8ecf1',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'background 0.15s',
                }}>
                  {isDone && <span style={{ color: '#fff', fontSize: 13, fontWeight: 700 }}>✓</span>}
                </div>
                <span style={{ fontSize: 9, fontWeight: 500, color: isDone ? '#059669' : '#6b7280', textAlign: 'center', lineHeight: 1.2, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', width: '90%' }}>
                  {h.name}
                </span>
              </div>
            )
          })}
        </div>
      )}
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
    fetch(`/api/tasks/${task.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ priority: next }) })
      .then(() => onPriorityChange && onPriorityChange(task.id, next))
  }

  if (gone) return null
  return (
    <div onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderRadius: 8, background: '#ffffff', border: `1px solid ${hover ? '#d1d5db' : '#e8ecf1'}`, transition: 'border-color 0.15s' }}
    >
      <div onClick={cyclePriority} title={`${PRIORITY_LABEL[priority]} — click to change`} style={{
        width: 14, height: 14, borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
        border: `1.5px solid ${PRIORITY_COLOR[priority]}`,
        background: priority < 4 ? PRIORITY_COLOR[priority] + '25' : 'transparent',
      }} />
      <span style={{ flex: 1, fontSize: 12, color: '#1f2937', lineHeight: 1.4 }}>{task.content}</span>
      <div style={{ display: 'flex', gap: 4, opacity: hover ? 1 : 0, transition: 'opacity 0.15s', flexShrink: 0 }}>
        <button onClick={e => { e.stopPropagation(); setGone(true); fetch(`/api/tasks/${task.id}/complete`, { method: 'POST' }).then(() => onDone(task.id)) }}
          style={{ width: 24, height: 24, borderRadius: 6, border: '1px solid #e8ecf1', background: '#f9fafb', cursor: 'pointer', fontSize: 12, color: '#10b981', fontWeight: 700, padding: 0 }}>✓</button>
        <button onClick={e => { e.stopPropagation(); setGone(true); fetch(`/api/tasks/${task.id}`, { method: 'DELETE' }).then(() => onDone(task.id)) }}
          style={{ width: 24, height: 24, borderRadius: 6, border: '1px solid #e8ecf1', background: '#f9fafb', cursor: 'pointer', fontSize: 11, color: '#ef4444', fontWeight: 700, padding: 0 }}>✕</button>
      </div>
    </div>
  )
}

function TodayPlayWidget() {
  const [today,   setToday]   = useState([])
  const [inbox,   setInbox]   = useState([])
  const [loading, setLoading] = useState(true)
  const [tab,     setTab]     = useState('today')

  useEffect(() => {
    fetch('/api/tasks').then(r => r.json()).then(d => { setToday(d.today || []); setInbox(d.inbox || []) })
      .catch(() => {}).finally(() => setLoading(false))
  }, [])

  const handleDone = id => { setToday(p => p.filter(t => t.id !== id)); setInbox(p => p.filter(t => t.id !== id)) }
  const handlePriorityChange = (id, pri) => {
    setToday(p => p.map(t => t.id === id ? { ...t, priority: pri } : t))
    setInbox(p => p.map(t => t.id === id ? { ...t, priority: pri } : t))
  }

  const tasks = (tab === 'today' ? today : inbox).slice(0, 5)
  const sections = (() => {
    const groups = {}; tasks.forEach(t => { const p = t.priority || 4; (groups[p] = groups[p] || []).push(t) })
    return [1,2,3,4].filter(p => groups[p]?.length).map(p => ({ priority: p, tasks: groups[p] }))
  })()

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexShrink: 0 }}>
        <span className="section-title">Today</span>
        <div style={{ display: 'flex', background: '#f3f4f6', borderRadius: 7, padding: 2, gap: 2 }}>
          {[['today', `Today${today.length ? ` (${today.length})` : ''}`], ['inbox', `Inbox${inbox.length ? ` (${inbox.length})` : ''}`]].map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)} style={{
              fontSize: 10, fontWeight: 600, padding: '3px 9px', borderRadius: 5, border: 'none', fontFamily: 'inherit', cursor: 'pointer',
              background: tab === key ? '#ffffff' : 'transparent', color: tab === key ? '#1f2937' : '#9ca3af',
              boxShadow: tab === key ? '0 1px 3px rgba(0,0,0,0.08)' : 'none', transition: 'all 0.15s',
            }}>{label}</button>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {loading ? <p style={{ fontSize: 12, color: '#9ca3af' }}>Loading…</p>
          : tasks.length === 0 ? <p style={{ fontSize: 13, color: '#9ca3af', textAlign: 'center', padding: '20px 0' }}>{tab === 'today' ? 'Nothing scheduled.' : 'Inbox clear.'}</p>
          : sections.map(({ priority, tasks: ts }) => (
            <div key={priority} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: PRIORITY_COLOR[priority], flexShrink: 0 }} />
                <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: PRIORITY_COLOR[priority] }}>{PRIORITY_LABEL[priority]}</span>
                <div style={{ flex: 1, height: 1, background: PRIORITY_COLOR[priority] + '30' }} />
                <span style={{ fontSize: 9, color: '#c4c9d4' }}>{ts.length}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {ts.map(t => <TaskRow key={t.id} task={t} onDone={handleDone} onPriorityChange={handlePriorityChange} />)}
              </div>
            </div>
          ))
        }
      </div>
      {!loading && (today.length > 0 || inbox.length > 0) && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid #f3f4f6', flexShrink: 0 }}>
          <span style={{ fontSize: 10, color: '#c4c9d4' }}>{today.length} today · {inbox.length} inbox</span>
        </div>
      )}
    </div>
  )
}

// ── Chat ───────────────────────────────────────────────────────────────────

function AdvisorAvatar({ advisor, size, isActive }) {
  const GRADS = { kai: ['#1e3a5f','#2d5a8e'], ember: ['#7f1d1d','#be123c'], beats: ['#431407','#9a3412'], doc: ['#064e3b','#059669'], coach: ['#713f12','#d97706'], biz: ['#3b0764','#7c3aed'] }
  const [from, to] = GRADS[advisor.id] || ['#374151','#6b7280']
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0, overflow: 'hidden', fontSize: size * 0.4,
      background: advisor.avatar ? 'transparent' : `linear-gradient(135deg, ${from} 0%, ${to} 100%)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      boxShadow: isActive ? `0 0 0 2px #fff, 0 0 0 3.5px ${advisor.color}` : '0 1px 4px rgba(0,0,0,0.18)',
      transition: 'all 0.2s',
    }}>
      {advisor.avatar ? <img src={advisor.avatar} alt={advisor.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : advisor.emoji}
    </div>
  )
}

function ChatWidget() {
  const [advisor,  setAdvisor]  = useState(getAdvisor('kai'))
  const [messages, setMessages] = useState([])
  const [input,    setInput]    = useState('')
  const [thinking, setThinking] = useState(false)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  useEffect(() => {
    api.getChannelHistory(advisor.channel).then(d => setMessages(d.messages || [])).catch(() => {})
  }, [advisor.channel])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, thinking])

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
    } finally { setThinking(false); inputRef.current?.focus() }
  }

  return (
    <div style={{ background: '#ffffff', borderRadius: 20, boxShadow: '0 4px 20px rgba(0,0,0,0.06)', border: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>
      <div style={{ flexShrink: 0, borderBottom: '1px solid #e8ecf1', padding: '12px 20px', background: `linear-gradient(to right, ${advisor.color}06 0%, transparent 50%)`, display: 'flex', alignItems: 'center', gap: 10, overflowX: 'auto' }} className="no-scrollbar">
        {ADVISORS.map(a => {
          const active = advisor.id === a.id
          return (
            <button key={a.id} onClick={() => setAdvisor(a)} title={a.name} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, background: 'none', border: 'none', cursor: 'pointer', padding: '2px 8px', borderRadius: 10, opacity: active ? 1 : 0.45, flexShrink: 0, transition: 'opacity 0.15s' }}
              onMouseEnter={e => e.currentTarget.style.opacity = '1'}
              onMouseLeave={e => { if (!active) e.currentTarget.style.opacity = '0.45' }}
            >
              <AdvisorAvatar advisor={a} size={active ? 42 : 34} isActive={active} />
              <span style={{ fontSize: 10, fontWeight: active ? 600 : 400, color: active ? advisor.color : '#9ca3af', whiteSpace: 'nowrap' }}>{a.name}</span>
            </button>
          )
        })}
        <button onClick={() => setMessages([])} title="Clear chat" style={{ marginLeft: 'auto', flexShrink: 0, alignSelf: 'center', background: 'none', border: 'none', cursor: 'pointer', color: '#d1d5db', fontSize: 14, padding: '4px 8px', lineHeight: 1, transition: 'color 0.15s' }}
          onMouseEnter={e => e.currentTarget.style.color = '#ef4444'}
          onMouseLeave={e => e.currentTarget.style.color = '#d1d5db'}
        >✕</button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 10, background: '#fafbfc' }}>
        {messages.length === 0 && !thinking && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
            <AdvisorAvatar advisor={advisor} size={52} isActive={false} />
            <p style={{ fontSize: 13, textAlign: 'center', maxWidth: 220, lineHeight: 1.6, color: '#9ca3af' }}>{advisor.intro}</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', alignItems: 'flex-end', gap: 8 }}>
            {msg.role !== 'user' && <AdvisorAvatar advisor={advisor} size={26} isActive={false} />}
            <div style={{ maxWidth: '78%', padding: '9px 13px', borderRadius: msg.role === 'user' ? '12px 12px 4px 12px' : '12px 12px 12px 4px', fontSize: 13, lineHeight: 1.5, background: msg.role === 'user' ? '#fff7ed' : '#ffffff', color: '#1f2937', border: msg.role === 'user' ? '1px solid rgba(194,65,12,0.12)' : '1px solid #e8ecf1' }}>
              <p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                {msg.content}
                {msg.ts && <span style={{ fontSize: 10, opacity: 0.3, marginLeft: 8, whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{fmtTime(msg.ts)}</span>}
              </p>
            </div>
          </div>
        ))}
        {thinking && (
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
            <AdvisorAvatar advisor={advisor} size={26} isActive={false} />
            <div style={{ background: '#ffffff', border: '1px solid #e8ecf1', borderRadius: '12px 12px 12px 4px', padding: '10px 14px' }}>
              <div style={{ display: 'flex', gap: 4 }}>
                {[0,150,300].map(d => <span key={d} style={{ width: 6, height: 6, borderRadius: '50%', background: '#c4c9d4', display: 'inline-block', animation: `bounce 1s ${d}ms infinite` }} />)}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ flexShrink: 0, padding: '12px 16px', borderTop: '1px solid #e8ecf1', display: 'flex', gap: 10, background: '#ffffff' }}>
        <input ref={inputRef} value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={`Message ${advisor.name}…`}
          style={{ flex: 1, padding: '7px 11px', borderRadius: 10, border: '1px solid #e8ecf1', background: '#fafbfc', color: '#1f2937', fontSize: 13, fontFamily: 'inherit', outline: 'none', transition: 'border-color 0.15s' }}
          onFocus={e => e.target.style.borderColor = advisor.color}
          onBlur={e => e.target.style.borderColor = '#e8ecf1'}
        />
        <button onClick={send} disabled={!input.trim() || thinking} style={{ padding: '10px 16px', borderRadius: 10, border: 'none', fontSize: 13, fontWeight: 500, fontFamily: 'inherit', transition: 'all 0.2s', cursor: input.trim() && !thinking ? 'pointer' : 'default', background: input.trim() && !thinking ? `linear-gradient(135deg, ${advisor.color} 0%, ${advisor.color}cc 100%)` : '#e8ecf1', color: input.trim() && !thinking ? '#ffffff' : '#9ca3af' }}>
          Send
        </button>
      </div>
    </div>
  )
}

// ── The Lot ────────────────────────────────────────────────────────────────

const LOT_CATS = [
  { key: 'all',       label: 'All',     types: null },
  { key: 'links',     label: 'Links',   types: ['link','product','url'] },
  { key: 'notes',     label: 'Notes',   types: ['note','item','text'] },
  { key: 'images',    label: 'Images',  types: ['image'] },
  { key: 'ideas',     label: 'Ideas',   types: ['idea'] },
  { key: 'videos',    label: 'Videos',  types: ['video'] },
  { key: 'documents', label: 'Docs',    types: ['document','doc'] },
]

const TYPE_STYLE = {
  link:     { icon: '🔗', color: '#3b82f6', bg: '#eff6ff' },
  product:  { icon: '🛍️', color: '#3b82f6', bg: '#eff6ff' },
  url:      { icon: '🔗', color: '#3b82f6', bg: '#eff6ff' },
  note:     { icon: '📝', color: '#8b5cf6', bg: '#f5f3ff' },
  item:     { icon: '📌', color: '#8b5cf6', bg: '#f5f3ff' },
  text:     { icon: '📝', color: '#8b5cf6', bg: '#f5f3ff' },
  image:    { icon: '🖼️', color: '#ec4899', bg: '#fdf2f8' },
  idea:     { icon: '💡', color: '#f59e0b', bg: '#fffbeb' },
  video:    { icon: '🎬', color: '#10b981', bg: '#ecfdf5' },
  document: { icon: '📄', color: '#6b7280', bg: '#f9fafb' },
  doc:      { icon: '📄', color: '#6b7280', bg: '#f9fafb' },
}

function LotCard({ item, onArchive, onDelete }) {
  const [thumb,   setThumb]   = useState(item.thumbnail || null)
  const [hover,   setHover]   = useState(false)
  const [editing, setEditing] = useState(false)
  const [title,   setTitle]   = useState(item.title || item.slug)
  const inputRef = useRef(null)
  const style = TYPE_STYLE[item.type] || { icon: '📌', color: '#6b7280', bg: '#f9fafb' }
  const isImgUrl = item.url && /\.(jpg|jpeg|png|gif|webp|svg)(\?.*)?$/i.test(item.url)

  useEffect(() => {
    if (isImgUrl) { setThumb(item.url); return }
    if (!item.thumbnail && item.url) {
      fetch('/api/parking-lot/og?url=' + encodeURIComponent(item.url))
        .then(r => r.json()).then(d => { if (d.image) setThumb(d.image) }).catch(() => {})
    }
  }, [item.url])

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function saveTitle() {
    setEditing(false)
    fetch(`/api/parking-lot/${item.slug}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) }).catch(() => {})
  }

  return (
    <div style={{ background: '#ffffff', borderRadius: 12, border: `1px solid ${hover ? '#c4c9d4' : '#e8ecf1'}`, overflow: 'hidden', transition: 'all 0.15s', position: 'relative', boxShadow: hover ? '0 4px 12px rgba(0,0,0,0.1)' : '0 1px 3px rgba(0,0,0,0.04)' }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
    >
      {/* Thumbnail */}
      <div style={{ height: 80, background: style.bg, display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative', overflow: 'hidden' }}>
        {thumb
          ? <img src={thumb} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} onError={() => setThumb(null)} />
          : <span style={{ fontSize: 26 }}>{style.icon}</span>
        }
        {/* Hover actions */}
        {hover && (
          <div style={{ position: 'absolute', top: 5, right: 5, display: 'flex', gap: 3 }}>
            {[
              { label: '✏️', title: 'Edit',    fn: () => setEditing(true) },
              { label: '📦', title: 'Archive', fn: () => onArchive(item.slug) },
              { label: '✕',  title: 'Delete',  fn: () => onDelete(item.slug), color: '#ef4444' },
            ].map(({ label, title, fn, color }) => (
              <button key={title} onClick={e => { e.stopPropagation(); fn() }} title={title} style={{ width: 24, height: 24, borderRadius: 6, border: 'none', background: 'rgba(255,255,255,0.92)', cursor: 'pointer', fontSize: 11, color: color || '#374151', boxShadow: '0 1px 4px rgba(0,0,0,0.12)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{label}</button>
            ))}
          </div>
        )}
        <div style={{ position: 'absolute', bottom: 4, left: 4, background: 'rgba(255,255,255,0.88)', borderRadius: 4, padding: '1px 5px', fontSize: 9, fontWeight: 600, color: style.color, textTransform: 'uppercase', letterSpacing: '0.03em' }}>{item.type || 'item'}</div>
      </div>

      {/* Title */}
      <div style={{ padding: '7px 9px' }}>
        {editing ? (
          <input ref={inputRef} value={title} onChange={e => setTitle(e.target.value)}
            onBlur={saveTitle} onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setTitle(item.title); setEditing(false) } }}
            style={{ width: '100%', fontSize: 11, fontWeight: 500, border: 'none', borderBottom: '1px solid #c2410c', outline: 'none', background: 'transparent', fontFamily: 'inherit', padding: '1px 0' }}
          />
        ) : (
          <p style={{ fontSize: 11, fontWeight: 500, color: '#1f2937', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3 }}>{title}</p>
        )}
        {item.date && <p style={{ fontSize: 9, color: '#9ca3af', margin: '2px 0 0' }}>{new Date(item.date + 'T12:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</p>}
      </div>
    </div>
  )
}

function LotWidget() {
  const [items,    setItems]    = useState([])
  const [category, setCategory] = useState('all')

  useEffect(() => {
    fetch('/api/parking-lot/list').then(r => r.json()).then(d => setItems(d.items || [])).catch(() => {})
  }, [])

  function archive(slug) { fetch(`/api/parking-lot/${slug}/archive`, { method: 'POST' }).then(() => setItems(p => p.filter(i => i.slug !== slug))).catch(() => {}) }
  function del(slug)     { fetch(`/api/parking-lot/${slug}`, { method: 'DELETE' }).then(() => setItems(p => p.filter(i => i.slug !== slug))).catch(() => {}) }

  const activeDef = LOT_CATS.find(c => c.key === category)
  const filtered  = activeDef?.types ? items.filter(i => activeDef.types.includes(i.type || 'item')) : items

  return (
    <div style={{ display: 'flex', background: '#ffffff', borderRadius: 20, overflow: 'hidden', border: '1px solid #e8ecf1', boxShadow: '0 2px 8px rgba(0,0,0,0.04)', minHeight: 220 }}>
      {/* Category sidebar — far left */}
      <div style={{ width: 100, background: '#f8f9fa', borderRight: '1px solid #e8ecf1', padding: '14px 8px', display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0 }}>
        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9ca3af', paddingLeft: 8, marginBottom: 6 }}>The Lot</span>
        {LOT_CATS.map(cat => {
          const count = cat.types ? items.filter(i => cat.types.includes(i.type || 'item')).length : items.length
          if (cat.key !== 'all' && count === 0) return null
          const active = category === cat.key
          return (
            <button key={cat.key} onClick={() => setCategory(cat.key)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 10px', borderRadius: 8, border: 'none', cursor: 'pointer', fontFamily: 'inherit', background: active ? '#ffffff' : 'transparent', boxShadow: active ? '0 1px 3px rgba(0,0,0,0.08)' : 'none', transition: 'all 0.15s' }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = '#efefef' }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
            >
              <span style={{ fontSize: 11, fontWeight: active ? 600 : 400, color: active ? '#1f2937' : '#6b7280' }}>{cat.label}</span>
              <span style={{ fontSize: 10, color: '#9ca3af' }}>{count}</span>
            </button>
          )
        })}
      </div>

      {/* Items grid — center */}
      <div style={{ flex: 1, padding: 14, overflowY: 'auto' }}>
        {filtered.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#9ca3af' }}>
            <p style={{ fontSize: 13 }}>Nothing here</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 10 }}>
            {filtered.map(item => <LotCard key={item.slug} item={item} onArchive={archive} onDelete={del} />)}
          </div>
        )}
      </div>

      {/* Drop zone — far right */}
      <div style={{
        width: 110, borderLeft: '1px solid #e8ecf1', padding: 12,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: 8, cursor: 'default', flexShrink: 0, background: '#fafbfc', transition: 'all 0.2s',
      }}
        onMouseEnter={e => { e.currentTarget.style.background = '#fff7ed'; e.currentTarget.style.borderColor = '#c2410c' }}
        onMouseLeave={e => { e.currentTarget.style.background = '#fafbfc'; e.currentTarget.style.borderColor = '#e8ecf1' }}
      >
        <div style={{ width: 40, height: 40, borderRadius: 10, border: '2px dashed #d1d5db', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, transition: 'all 0.2s' }}>📥</div>
        <p style={{ margin: 0, fontSize: 10, fontWeight: 600, color: '#9ca3af', textAlign: 'center', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Drop Zone</p>
        <p style={{ margin: 0, fontSize: 9, color: '#c4c9d4', textAlign: 'center', lineHeight: 1.4 }}>{items.length} items</p>
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div style={{ height: '100%', background: '#f8f9fa', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="kai-card" style={{ padding: 20 }}>
          <p style={{ fontSize: 21, fontWeight: 300, color: '#1f2937', letterSpacing: '-0.02em', marginBottom: 14 }}>
            {greeting()}, <strong style={{ fontWeight: 600 }}>Leo</strong>
          </p>
          <div className="hidden md:grid" style={{ gridTemplateColumns: '1.15fr 0.85fr 1.2fr', gridTemplateRows: '380px 360px', gap: 12 }}>
            <div style={{ gridColumn: 1, gridRow: 1, display: 'flex', overflow: 'hidden' }}><ProjectsWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 1, display: 'flex', overflow: 'hidden' }}><HarmonyWidget /></div>
            <div style={{ gridColumn: 3, gridRow: '1 / 3' }}><ChatWidget /></div>
            <div style={{ gridColumn: 1, gridRow: 2, display: 'flex', overflow: 'hidden' }}><TodayPlayWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 2, display: 'flex', overflow: 'hidden' }}><HabitsWidget /></div>
          </div>
          <div className="md:hidden flex flex-col" style={{ gap: 12 }}>
            <HabitsWidget /><TodayPlayWidget /><HarmonyWidget /><ProjectsWidget />
            <div style={{ minHeight: 400 }}><ChatWidget /></div>
          </div>
        </div>
        <LotWidget />
      </div>
      <style>{`@keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-4px)} }`}</style>
    </div>
  )
}
