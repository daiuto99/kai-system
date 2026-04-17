import { useState, useEffect, useRef } from 'react'
import {
  Activity, Brain, HeartHandshake, Briefcase, Compass,
  Dumbbell, Stethoscope, HeartPulse, Heart, Smile,
  BookOpen, Lightbulb, Infinity, Feather,
  Users, Baby, Globe, Home, Waves,
  TrendingUp, DollarSign, BarChart, Target, Trophy,
  Sparkles, Star, Sun, Moon, Flame, Zap,
  Music, Palette, Mic, Camera, Pen, Mountain,
  Shield, Crown, Award, TreePine, Leaf, Coffee,
  Gem, Rocket, Bike, Wind, Flower, Eye, Anchor, Map, Flag, Clock,
} from 'lucide-react'
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


const _ICON_MAP = {
  Activity, Brain, HeartHandshake, Briefcase, Compass,
  Dumbbell, Stethoscope, HeartPulse, Heart, Smile,
  BookOpen, Lightbulb, Infinity, Feather,
  Users, Baby, Globe, Home, Waves,
  TrendingUp, DollarSign, BarChart, Target, Trophy,
  Sparkles, Star, Sun, Moon, Flame, Zap,
  Music, Palette, Mic, Camera, Pen, Mountain,
  Shield, Crown, Award, TreePine, Leaf, Coffee,
  Gem, Rocket, Bike, Wind, Flower, Eye, Anchor, Map, Flag, Clock,
}
function LucideIcon({ name, size = 14, color = 'currentColor' }) {
  const C = _ICON_MAP[name]
  return C ? <C size={size} color={color} strokeWidth={1.75} /> : null
}
function loadGroupConfig(defaults) {
  try {
    const saved = JSON.parse(localStorage.getItem('kai-harmony-groups') || '{}')
    return defaults.map(g => ({ ...g, ...saved[g.name] }))
  } catch { return defaults }
}

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
              <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', background: 'var(--bg-muted)', borderRadius: 10, padding: '2px 7px' }}>
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
            <div key={p.id} style={{ padding: '6px 11px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--bg-card)', transition: 'all 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.borderColor = 'var(--accent)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-card)'; e.currentTarget.style.borderColor = 'var(--border)' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: sc, flexShrink: 0 }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{p.name}</span>
                {p.milestone && <>
                  <span style={{ fontSize: 11, color: '#d1d5db' }}>|</span>
                  <span style={{ fontSize: 11, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.milestone}</span>
                </>}
                {p.version && <span style={{ fontSize: 10, color: 'var(--text-tertiary)', flexShrink: 0 }}>v{p.version}</span>}
                <span style={{ fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4, flexShrink: 0, background: sbg, color: stc, textTransform: 'uppercase', letterSpacing: '0.03em' }}>{p.status}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.next}</span>
                {pct !== null && <>
                  <div style={{ width: 40, height: 2, borderRadius: 1, background: '#e8ecf1', overflow: 'hidden', flexShrink: 0 }}>
                    <div style={{ height: '100%', width: `${pct}%`, background: sc }} />
                  </div>
                  <span style={{ fontSize: 10, fontWeight: 600, color: sc, flexShrink: 0 }}>{pct}%</span>
                </>}
                {ago && <span style={{ fontSize: 10, color: 'var(--text-subtle)', flexShrink: 0 }}>{ago}</span>}
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

function HarmonyRings({ domains }) {
  const r    = 26
  const sw   = 5
  const size = r * 2 + sw + 2
  const circ = 2 * Math.PI * r

  return (
    <div style={{ display: 'flex', justifyContent: 'space-around', alignItems: 'center', flex: 1, padding: '6px 0 2px' }}>
      {HARMONY_GROUPS.map(g => {
        const statuses = g.ids.map(id => {
          const d = domains.find(x => x.id === id)
          return d ? domainStatus(d.aspects) : null
        }).filter(Boolean)

        const greenCount = statuses.filter(s => s === 'green').length
        const pct        = statuses.length ? greenCount / statuses.length : 0
        const overall    = statuses.includes('red') ? 'red'
          : statuses.includes('yellow') ? 'yellow'
          : statuses.length ? 'green' : 'gray'
        const color = HCOL[overall]
        const cx = size / 2, cy = size / 2
        const offset = circ * (1 - pct)

        return (
          <div key={g.name} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
            <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
              {/* track */}
              <circle cx={cx} cy={cy} r={r} fill="none"
                stroke="rgba(255,255,255,0.07)" strokeWidth={sw} />
              {/* progress */}
              <circle cx={cx} cy={cy} r={r} fill="none"
                stroke={color} strokeWidth={sw}
                strokeDasharray={circ}
                strokeDashoffset={offset}
                strokeLinecap="round"
                style={{ transition: 'stroke-dashoffset 0.7s ease, stroke 0.3s' }}
              />
            </svg>
            <span style={{
              fontSize: 9, fontWeight: 500,
              color: 'var(--text-muted)',
              letterSpacing: '0.03em', textAlign: 'center',
              lineHeight: 1.2, maxWidth: 52,
            }}>{g.name}</span>
          </div>
        )
      })}
    </div>
  )
}

const _DEFAULT_BAR_GROUPS = [
  { name: 'Life',          ids: ['life-vision', 'passion-sex'],                                           color: '#10b981', icon: 'Compass' },
  { name: 'Body',          ids: ['health-fitness', 'quality-of-life'],                                    color: '#f97316', icon: 'Activity' },
  { name: 'Mind',          ids: ['intellectual-life', 'emotional-life', 'character', 'spiritual-life'],   color: '#a855f7', icon: 'Brain' },
  { name: 'Work & Money',  ids: ['career', 'financial-life'],                                             color: '#3b82f6', icon: 'Briefcase' },
  { name: 'Relationships', ids: ['love-relationship', 'parenting', 'social-life'],                        color: '#ec4899', icon: 'HeartHandshake' },
]

function HarmonyWidget() {
  const [domains, setDomains] = useState([])
  const [tooltip, setTooltip] = useState(null)
  const BAR_GROUPS = loadGroupConfig(_DEFAULT_BAR_GROUPS)

  useEffect(() => {
    fetch('/api/harmony').then(r => r.json()).then(d => setDomains(d.domains || [])).catch(() => {})
  }, [])

  const SC = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }

  function handleEnter(e, g) {
    const rect = e.currentTarget.getBoundingClientRect()
    setTooltip({ group: g, x: rect.right + 10, y: rect.top + rect.height / 2 })
  }

  return (
    <div style={{ flex: 1, background: 'var(--bg-surface)', borderRadius: 16, border: '1px solid var(--border)', padding: '14px 14px 10px', display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden' }}>
      <span className="section-title" style={{ flexShrink: 0 }}>Harmony</span>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 7, minHeight: 0, justifyContent: 'space-between' }}>
        {BAR_GROUPS.map(g => {
          const stats = g.ids.map(id => {
            const d = domains.find(x => x.id === id)
            return d ? domainStatus(d.aspects) : null
          }).filter(Boolean)
          const green = stats.filter(s => s === 'green').length
          const pct   = stats.length ? Math.round((green / stats.length) * 100) : 0

          return (
            <div key={g.name}
              onMouseEnter={e => handleEnter(e, g.name)}
              onMouseLeave={() => setTooltip(null)}
              style={{ flex: 1, position: 'relative', borderRadius: 8, overflow: 'hidden', background: g.color + '18', cursor: 'default' }}
            >
              <div style={{
                position: 'absolute', top: 0, left: 0, bottom: 0,
                width: `${pct}%`,
                background: `linear-gradient(to right, ${g.color}, ${g.color}cc)`,
                borderRadius: 8, transition: 'width 0.9s cubic-bezier(.4,0,.2,1)',
              }} />
              <div style={{
                position: 'absolute', inset: 0, zIndex: 1,
                display: 'flex', alignItems: 'center',
                padding: '0 12px', gap: 7, pointerEvents: 'none',
              }}>
                <LucideIcon name={g.icon} size={13} color={pct > 30 ? 'rgba(255,255,255,0.85)' : g.color} />
                <span style={{
                  fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', flex: 1,
                  color: pct > 30 ? 'rgba(255,255,255,0.92)' : g.color + 'cc', transition: 'color 0.3s',
                }}>{g.name}</span>
                <span style={{
                  fontSize: 10, fontWeight: 600,
                  color: pct > 85 ? 'rgba(255,255,255,0.7)' : g.color + '99', transition: 'color 0.3s',
                }}>{pct}%</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* fixed tooltip — escapes all overflow:hidden parents */}
      {tooltip && (() => {
        const g = BAR_GROUPS.find(x => x.name === tooltip.group)
        if (!g) return null
        const groupDomains = g.ids.map(id => domains.find(x => x.id === id)).filter(Boolean)
        return (
          <div style={{
            position: 'fixed', left: tooltip.x, top: tooltip.y,
            transform: 'translateY(-50%)',
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '10px 12px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            zIndex: 9999, minWidth: 160, pointerEvents: 'none',
          }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: g.color, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 7 }}>{g.name}</div>
            {groupDomains.map(d => (
              <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2px 0' }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: SC[domainStatus(d.aspects)], flexShrink: 0 }} />
                <span style={{ fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{d.icon} {d.name}</span>
              </div>
            ))}
          </div>
        )
      })()}
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
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 7, flex: 1, minHeight: 0, cursor: editing ? 'default' : 'text' }}
      onClick={() => { if (!editing) setEditing(true) }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>Intention</span>
        {saved && <span style={{ fontSize: 10, color: '#10b981' }}>Saved ✓</span>}
        {!editing && !saved && <span style={{ fontSize: 10, color: 'var(--accent)', opacity: 0.4 }}>edit</span>}
      </div>
      {editing ? (
        <textarea ref={ref} value={intent} onChange={e => setIntent(e.target.value)}
          onBlur={save} onKeyDown={e => { if (e.key === 'Escape') save() }}
          placeholder="What is your intention for today?"
          style={{ width: '100%', fontSize: 11, color: 'var(--text-primary)', lineHeight: 1.5, background: 'transparent', border: 'none', outline: 'none', resize: 'none', fontFamily: 'inherit', minHeight: 36 }}
        />
      ) : (
        <p style={{ fontSize: 11, margin: 0, lineHeight: 1.5, fontStyle: 'italic', color: intent ? '#4b5563' : '#c4c9d4', borderLeft: '2px solid var(--accent-bg)', paddingLeft: 8 }}>
          {intent || 'Set your intention for today…'}
        </p>
      )}
    </div>
  )
}

// ── Habits (icon grid) ─────────────────────────────────────────────────────

function HabitsWidget() {
  const [habits,    setHabits]    = useState([])
  const [loading,   setLoading]   = useState(true)
  const [editMode,  setEditMode]  = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editVal,   setEditVal]   = useState('')
  const [icons, setIcons] = useState(() => {
    try { return JSON.parse(localStorage.getItem('kai-habit-icons') || '{}') } catch { return {} }
  })
  const today = new Date().toISOString().slice(0, 10)

  const HCOLOR = [
    '#e53935','#e64a19','#f57c00','#f9a825','#fdd835',
    '#c0ca33','#7cb342','#2e7d32','#00695c','#00838f',
    '#0277bd','#1565c0','#283593','#4527a0','#6a1b9a',
    '#ad1457','#880e4f','#4e342e','#546e7a','#37474f',
  ]
  const habitColor = idx => HCOLOR[idx % HCOLOR.length] || 'var(--accent)'

  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (6 - i))
    return d.toISOString().slice(0, 10)
  })

  useEffect(() => {
    fetch('/api/habits').then(r => r.json())
      .then(d => setHabits(d.habits || d || []))
      .catch(() => {}).finally(() => setLoading(false))
  }, [])

  function toggle(h) {
    const done = h.completions?.includes(today)
    fetch(`/api/habits/${h.id}/complete`, { method: done ? 'DELETE' : 'POST' })
      .then(r => r.json())
      .then(() => setHabits(prev => prev.map(x => x.id === h.id
        ? { ...x, completions: done
            ? x.completions.filter(c => c !== today)
            : [...(x.completions || []), today] }
        : x)))
      .catch(() => {})
  }

  function getIcon(h) { return icons[h.id] || h.emoji || (h.displayName || h.name || '?')[0] }

  function openEdit(h) {
    setEditingId(h.id)
    setEditVal(icons[h.id] || h.emoji || '')
  }

  function saveIcon(id) {
    const v = editVal.trim()
    const updated = v
      ? { ...icons, [id]: v }
      : (() => { const c = { ...icons }; delete c[id]; return c })()
    setIcons(updated)
    localStorage.setItem('kai-habit-icons', JSON.stringify(updated))
    setEditingId(null)
  }

  const doneCount = habits.filter(h => h.completions?.includes(today)).length
  const total     = habits.length
  const pct       = total ? Math.round((doneCount / total) * 100) : 0

  return (
    <div className="kai-card" style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8, flex: 1, overflow: 'hidden' }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <span className="section-title">Habits</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 54, height: 3, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, borderRadius: 2, background: pct === 100 ? '#22c55e' : 'var(--accent)', transition: 'width 0.4s ease' }} />
          </div>
          <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 28 }}>{doneCount}/{total}</span>
          <button onClick={() => { setEditMode(m => !m); setEditingId(null) }}
            title={editMode ? 'Done' : 'Assign icons'}
            style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: editMode ? 'var(--accent)' : 'var(--text-muted)', padding: '2px 4px' }}>✏</button>
        </div>
      </div>

      {/* column headers */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, paddingRight: 2 }}>
        <div style={{ width: 32, flexShrink: 0 }} />
        <span style={{ flex: 1, fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>Habit</span>
        <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', width: 30, textAlign: 'center' }}>Today</span>
        <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', width: 68, textAlign: 'right' }}>Week</span>
      </div>

      {loading ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 11 }}>Loading…</div>
      ) : (
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {habits.map((h, idx) => {
            const isDone   = h.completions?.includes(today)
            const weekCount = weekDays.filter(d => h.completions?.includes(d)).length
            const weekPct  = Math.round((weekCount / 7) * 100)
            const accent   = habitColor(h.color ?? 0)
            const isEditing = editingId === h.id

            return (
              <div key={h.id} style={{ position: 'relative' }}>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '6px 0',
                  borderBottom: idx < habits.length - 1 ? '1px solid var(--border)' : 'none',
                }}>
                  {/* icon */}
                  <button
                    onClick={() => editMode ? openEdit(h) : toggle(h)}
                    title={h.displayName || h.name}
                    style={{
                      all: 'unset', cursor: 'pointer', flexShrink: 0,
                      width: 32, height: 32, borderRadius: 8,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: isDone ? '#22c55e' : accent + '22',
                      border: `1.5px solid ${isDone ? '#22c55e' : accent + '55'}`,
                      fontSize: isDone ? 13 : 16, color: isDone ? '#fff' : accent,
                      transition: 'all 0.2s',
                      outline: editMode && !isDone ? `1px dashed ${accent}88` : 'none',
                      outlineOffset: 2,
                    }}
                  >
                    {isDone ? '✓' : getIcon(h)}
                  </button>

                  {/* name */}
                  <span style={{ flex: 1, fontSize: 12, fontWeight: 500, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {h.displayName || h.name}
                  </span>

                  {/* today */}
                  <span style={{ width: 30, textAlign: 'center', fontSize: 13, color: isDone ? '#22c55e' : 'var(--text-muted)', flexShrink: 0 }}>
                    {isDone ? '✓' : '○'}
                  </span>

                  {/* week */}
                  <div style={{ width: 68, display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0, justifyContent: 'flex-end' }}>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', width: 24, textAlign: 'right' }}>{weekPct}%</span>
                    <div style={{ width: 36, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                      <div style={{ width: `${weekPct}%`, height: '100%', background: weekPct === 100 ? '#22c55e' : 'var(--accent)', borderRadius: 2, transition: 'width 0.4s ease' }} />
                    </div>
                  </div>
                </div>

                {/* inline emoji editor */}
                {isEditing && (
                  <div style={{
                    position: 'absolute', top: '100%', left: 0, zIndex: 50,
                    background: 'var(--bg-card)', border: '1px solid var(--border)',
                    borderRadius: 8, padding: '6px 10px', boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
                    display: 'flex', alignItems: 'center', gap: 6,
                  }}>
                    <input autoFocus value={editVal} onChange={e => setEditVal(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') saveIcon(h.id); if (e.key === 'Escape') setEditingId(null) }}
                      placeholder="emoji" style={{ all: 'unset', fontSize: 20, width: 36, textAlign: 'center', color: 'var(--text-primary)' }} />
                    <button onClick={() => saveIcon(h.id)} style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: '#22c55e', fontWeight: 700 }}>✓</button>
                    <button onClick={() => setEditingId(null)} style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--text-muted)' }}>✕</button>
                  </div>
                )}
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
      style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderRadius: 8, background: 'var(--bg-card)', border: `1px solid ${hover ? '#d1d5db' : '#e8ecf1'}`, transition: 'border-color 0.15s' }}
    >
      <div onClick={cyclePriority} title={`${PRIORITY_LABEL[priority]} — click to change`} style={{
        width: 14, height: 14, borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
        border: `1.5px solid ${PRIORITY_COLOR[priority]}`,
        background: priority < 4 ? PRIORITY_COLOR[priority] + '25' : 'transparent',
      }} />
      <span style={{ flex: 1, fontSize: 12, color: 'var(--text-primary)', lineHeight: 1.4 }}>{task.content}</span>
      <div style={{ display: 'flex', gap: 4, opacity: hover ? 1 : 0, transition: 'opacity 0.15s', flexShrink: 0 }}>
        <button onClick={e => { e.stopPropagation(); setGone(true); fetch(`/api/tasks/${task.id}/complete`, { method: 'POST' }).then(() => onDone(task.id)) }}
          style={{ width: 24, height: 24, borderRadius: 6, border: '1px solid var(--border)', background: '#f9fafb', cursor: 'pointer', fontSize: 12, color: '#10b981', fontWeight: 700, padding: 0 }}>✓</button>
        <button onClick={e => { e.stopPropagation(); setGone(true); fetch(`/api/tasks/${task.id}`, { method: 'DELETE' }).then(() => onDone(task.id)) }}
          style={{ width: 24, height: 24, borderRadius: 6, border: '1px solid var(--border)', background: '#f9fafb', cursor: 'pointer', fontSize: 11, color: '#ef4444', fontWeight: 700, padding: 0 }}>✕</button>
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
        <div style={{ display: 'flex', background: 'var(--bg-muted)', borderRadius: 7, padding: 2, gap: 2 }}>
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
        {loading ? <p style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading…</p>
          : tasks.length === 0 ? <p style={{ fontSize: 13, color: 'var(--text-tertiary)', textAlign: 'center', padding: '20px 0' }}>{tab === 'today' ? 'Nothing scheduled.' : 'Inbox clear.'}</p>
          : sections.map(({ priority, tasks: ts }) => (
            <div key={priority} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: PRIORITY_COLOR[priority], flexShrink: 0 }} />
                <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: PRIORITY_COLOR[priority] }}>{PRIORITY_LABEL[priority]}</span>
                <div style={{ flex: 1, height: 1, background: PRIORITY_COLOR[priority] + '30' }} />
                <span style={{ fontSize: 9, color: 'var(--text-subtle)' }}>{ts.length}</span>
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
          <span style={{ fontSize: 10, color: 'var(--text-subtle)' }}>{today.length} today · {inbox.length} inbox</span>
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
    <div style={{ background: 'var(--bg-card)', borderRadius: 20, boxShadow: '0 4px 20px rgba(0,0,0,0.06)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>
      <div style={{ flexShrink: 0, borderBottom: '1px solid var(--border)', padding: '12px 20px', background: `linear-gradient(to right, ${advisor.color}06 0%, transparent 50%)`, display: 'flex', alignItems: 'center', gap: 10, overflowX: 'auto' }} className="no-scrollbar">
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
        <button onClick={() => { setMessages([]); api.clearHistory(advisor.channel).catch(() => {}) }} title="Clear chat" style={{ marginLeft: 'auto', flexShrink: 0, alignSelf: 'center', background: 'none', border: 'none', cursor: 'pointer', color: '#d1d5db', fontSize: 14, padding: '4px 8px', lineHeight: 1, transition: 'color 0.15s' }}
          onMouseEnter={e => e.currentTarget.style.color = '#ef4444'}
          onMouseLeave={e => e.currentTarget.style.color = '#d1d5db'}
        >✕</button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 10, background: 'var(--bg-surface)' }}>
        {messages.length === 0 && !thinking && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
            <AdvisorAvatar advisor={advisor} size={52} isActive={false} />
            <p style={{ fontSize: 13, textAlign: 'center', maxWidth: 220, lineHeight: 1.6, color: 'var(--text-tertiary)' }}>{advisor.intro}</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', alignItems: 'flex-end', gap: 8 }}>
            {msg.role !== 'user' && <AdvisorAvatar advisor={advisor} size={26} isActive={false} />}
            <div style={{ maxWidth: '78%', padding: '9px 13px', borderRadius: msg.role === 'user' ? '12px 12px 4px 12px' : '12px 12px 12px 4px', fontSize: 13, lineHeight: 1.5, background: msg.role === 'user' ? 'var(--accent-bg)' : 'var(--bg-card)', color: 'var(--text-primary)', border: msg.role === 'user' ? '1px solid var(--accent-bg)' : '1px solid var(--border)' }}>
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
            <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '12px 12px 12px 4px', padding: '10px 14px' }}>
              <div style={{ display: 'flex', gap: 4 }}>
                {[0,150,300].map(d => <span key={d} style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--text-subtle)', display: 'inline-block', animation: `bounce 1s ${d}ms infinite` }} />)}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{ flexShrink: 0, padding: '12px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 10, background: 'var(--bg-card)' }}>
        <input ref={inputRef} value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={`Message ${advisor.name}…`}
          style={{ flex: 1, padding: '7px 11px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', outline: 'none', transition: 'border-color 0.15s' }}
          onFocus={e => e.target.style.borderColor = advisor.color}
          onBlur={e => e.target.style.borderColor = 'var(--border)'}
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

const LOT_TYPE = {
  link:     { label: 'Link',    from: '#1e3a8a', to: '#3b82f6' },
  url:      { label: 'Link',    from: '#1e3a8a', to: '#3b82f6' },
  product:  { label: 'Product', from: '#7c2d12', to: '#f97316' },
  note:     { label: 'Note',    from: '#3b0764', to: '#7c3aed' },
  item:     { label: 'Item',    from: '#3b0764', to: '#7c3aed' },
  text:     { label: 'Text',    from: '#3b0764', to: '#7c3aed' },
  image:    { label: 'Image',   from: '#831843', to: '#ec4899' },
  idea:     { label: 'Idea',    from: '#78350f', to: '#d97706' },
  video:    { label: 'Video',   from: '#064e3b', to: '#10b981' },
  document: { label: 'Doc',     from: '#1e293b', to: '#64748b' },
  doc:      { label: 'Doc',     from: '#1e293b', to: '#64748b' },
}
const LOT_DEFAULT = { label: 'Item', from: '#1f2937', to: '#4b5563' }

function LotIcon({ type, size = 28 }) {
  const P = {
    link:     <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />,
    url:      <path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />,
    product:  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 10.5V6a3.75 3.75 0 10-7.5 0v4.5m11.356-1.993l1.263 12c.07.665-.45 1.243-1.119 1.243H4.25a1.125 1.125 0 01-1.12-1.243l1.264-12A1.125 1.125 0 015.513 7.5h12.974c.576 0 1.059.435 1.119 1.007z" />,
    note:     <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />,
    text:     <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />,
    item:     <path strokeLinecap="round" strokeLinejoin="round" d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z" />,
    idea:     <path strokeLinecap="round" strokeLinejoin="round" d="M12 18v-5.25m0 0a6.01 6.01 0 001.5-.189m-1.5.189a6.01 6.01 0 01-1.5-.189m3.75 7.478a12.06 12.06 0 01-4.5 0m3.75 2.383a14.406 14.406 0 01-3 0M14.25 18v-.192c0-.983.658-1.823 1.508-2.316a7.5 7.5 0 10-7.517 0c.85.493 1.509 1.333 1.509 2.316V18" />,
    image:    <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5zm10.5-11.25h.008v.008h-.008V8.25zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />,
    video:    <><path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15.91 11.672a.375.375 0 010 .656l-5.603 3.113a.375.375 0 01-.557-.328V8.887c0-.286.307-.466.557-.328l5.603 3.113z" /></>,
    document: <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />,
    doc:      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />,
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.92)" strokeWidth="1.5" style={{ display: 'block', filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.4))' }}>
      {P[type] || P.item}
    </svg>
  )
}

function LotCard({ item, onArchive, onDelete }) {
  const [hover,   setHover]   = useState(false)
  const [editing, setEditing] = useState(false)
  const [title,   setTitle]   = useState(item.title || item.slug)
  const inputRef = useRef(null)
  const cfg = LOT_TYPE[item.type] || LOT_DEFAULT
  const isImgUrl = item.url && /\.(jpg|jpeg|png|gif|webp|svg)(\?.*)?$/i.test(item.url)

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function saveTitle() {
    setEditing(false)
    fetch(`/api/parking-lot/${item.slug}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) }).catch(() => {})
  }

  return (
    <div style={{ background: 'var(--bg-card)', borderRadius: 12, border: `1px solid ${hover ? '#c4c9d4' : '#e8ecf1'}`, overflow: 'hidden', transition: 'all 0.15s', position: 'relative', boxShadow: hover ? '0 6px 20px rgba(0,0,0,0.12)' : '0 1px 4px rgba(0,0,0,0.05)' }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
    >
      {/* Thumbnail */}
      <div style={{
        height: 80, position: 'relative', overflow: 'hidden',
        background: `linear-gradient(135deg, ${cfg.from} 0%, ${cfg.to} 100%)`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {isImgUrl
          ? <img src={item.url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          : <>
              <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(circle at 50% 30%, rgba(255,255,255,0.18) 0%, transparent 65%)' }} />
              <LotIcon type={item.type} size={28} />
            </>
        }
        {hover && (
          <div style={{ position: 'absolute', top: 5, right: 5, display: 'flex', gap: 3 }}>
            {[
              { label: '✏️', title: 'Edit',    fn: () => setEditing(true) },
              { label: '📦', title: 'Archive', fn: () => onArchive(item.slug) },
              { label: '✕',  title: 'Delete',  fn: () => onDelete(item.slug) },
            ].map(({ label, title: t, fn }) => (
              <button key={t} onClick={e => { e.stopPropagation(); fn() }} title={t} style={{ width: 24, height: 24, borderRadius: 6, border: 'none', background: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(6px)', cursor: 'pointer', fontSize: 11, color: 'rgba(255,255,255,0.9)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{label}</button>
            ))}
          </div>
        )}
        <div style={{ position: 'absolute', bottom: 5, left: 6, background: 'rgba(0,0,0,0.32)', backdropFilter: 'blur(6px)', borderRadius: 4, padding: '1px 6px', fontSize: 9, fontWeight: 700, color: 'rgba(255,255,255,0.88)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{cfg.label}</div>
      </div>

      {/* Title */}
      <div style={{ padding: '8px 10px' }}>
        {editing ? (
          <input ref={inputRef} value={title} onChange={e => setTitle(e.target.value)}
            onBlur={saveTitle} onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setTitle(item.title); setEditing(false) } }}
            style={{ width: '100%', fontSize: 11, fontWeight: 500, border: 'none', borderBottom: '1px solid #c2410c', outline: 'none', background: 'transparent', fontFamily: 'inherit', padding: '1px 0' }}
          />
        ) : (
          <p style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-primary)', margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3 }}>{title}</p>
        )}
        {item.date && <p style={{ fontSize: 9, color: 'var(--text-tertiary)', margin: '3px 0 0' }}>{new Date(item.date + 'T12:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</p>}
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
    <div style={{ display: 'flex', background: 'var(--bg-card)', borderRadius: 20, overflow: 'hidden', border: '1px solid var(--border)', boxShadow: '0 2px 8px rgba(0,0,0,0.04)', minHeight: 220 }}>
      {/* Category sidebar — far left */}
      <div style={{ width: 100, background: 'var(--bg-screen)', borderRight: '1px solid #e8ecf1', padding: '14px 8px', display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0 }}>
        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-tertiary)', paddingLeft: 8, marginBottom: 6 }}>The Lot</span>
        {LOT_CATS.map(cat => {
          const count = cat.types ? items.filter(i => cat.types.includes(i.type || 'item')).length : items.length
          if (cat.key !== 'all' && count === 0) return null
          const active = category === cat.key
          return (
            <button key={cat.key} onClick={() => setCategory(cat.key)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 10px', borderRadius: 8, border: 'none', cursor: 'pointer', fontFamily: 'inherit', background: active ? '#ffffff' : 'transparent', boxShadow: active ? '0 1px 3px rgba(0,0,0,0.08)' : 'none', transition: 'all 0.15s' }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--bg-surface)' }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
            >
              <span style={{ fontSize: 11, fontWeight: active ? 600 : 400, color: active ? '#1f2937' : '#6b7280' }}>{cat.label}</span>
              <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>{count}</span>
            </button>
          )
        })}
      </div>

      {/* Items grid — center */}
      <div style={{ flex: 1, padding: 14, overflowY: 'auto' }}>
        {filtered.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-tertiary)' }}>
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
        gap: 8, cursor: 'default', flexShrink: 0, background: 'var(--bg-surface)', transition: 'all 0.2s',
      }}
        onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.borderColor = 'var(--accent)' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-surface)'; e.currentTarget.style.borderColor = 'var(--border)' }}
      >
        <div style={{ width: 40, height: 40, borderRadius: 10, border: '2px dashed #d1d5db', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, transition: 'all 0.2s' }}>📥</div>
        <p style={{ margin: 0, fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', textAlign: 'center', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Drop Zone</p>
        <p style={{ margin: 0, fontSize: 9, color: 'var(--text-subtle)', textAlign: 'center', lineHeight: 1.4 }}>{items.length} items</p>
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="kai-card" style={{ padding: 20 }}>
          <p style={{ fontSize: 21, fontWeight: 300, color: 'var(--text-primary)', letterSpacing: '-0.02em', marginBottom: 14 }}>
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
