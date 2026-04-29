import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
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
  Plus, Trash2, X as XIcon, Check, Send as SendIcon, Pin, PinOff, ExternalLink,
  ListTodo, Inbox, Archive,
  FileText, ShoppingBag, Video, Link2, UtensilsCrossed, ChevronDown, RefreshCw,
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

function ProjectProfile({ project, onClose, onPin }) {
  const sc  = SDOT[project.status]  || '#9ca3af'
  const stc = STEXT[project.status] || '#6b7280'
  const sbg = SBG[project.status]   || 'rgba(156,163,175,0.08)'
  return (
    <div style={{ position: 'fixed', right: 0, top: 0, bottom: 0, width: 360, background: 'var(--bg-card)', borderLeft: '1px solid var(--border)', zIndex: 200, display: 'flex', flexDirection: 'column', boxShadow: '-8px 0 32px rgba(0,0,0,0.3)' }}>
      <div style={{ padding: '20px 20px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: sc, flexShrink: 0 }} />
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>{project.name}</span>
            {project.version && <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>v{project.version}</span>}
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => onPin(project)} title={project.pinned ? 'Unpin' : 'Pin to top'}
              style={{ all: 'unset', cursor: 'pointer', padding: 6, borderRadius: 7, color: project.pinned ? 'var(--accent)' : 'var(--text-muted)', background: project.pinned ? 'var(--accent-bg)' : 'transparent', transition: 'all 0.15s' }}>
              {project.pinned ? <PinOff size={14} /> : <Pin size={14} />}
            </button>
            <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', padding: 6, borderRadius: 7, color: 'var(--text-muted)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
            ><XIcon size={16} /></button>
          </div>
        </div>
        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 5, background: sbg, color: stc, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{project.status}</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {project.description && <div><div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>About</div><p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0 }}>{project.description}</p></div>}
        {project.milestone && <div>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>Milestone</div>
          <p style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', margin: '0 0 8px' }}>{project.milestone}</p>
          {project.milestone_pct != null && <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><div style={{ flex: 1, height: 4, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}><div style={{ height: '100%', width: `${project.milestone_pct}%`, background: sc, borderRadius: 2 }} /></div><span style={{ fontSize: 11, fontWeight: 600, color: sc }}>{project.milestone_pct}%</span></div>}
        </div>}
        {project.next && <div><div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>Next</div><p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0, borderLeft: '2px solid var(--accent)', paddingLeft: 10 }}>{project.next}</p></div>}
        {project.advisor && <div><div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>Advisor</div><span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'capitalize' }}>{project.advisor}</span></div>}
        {project.url && <a href={project.url} target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}><ExternalLink size={12} /> {project.url}</a>}
      </div>
    </div>
  )
}

const TYPE_TABS  = ['all', 'active', 'live', 'idea', 'parked']
const TYPE_COLOR = { active: '#3882F6', live: '#10b981', idea: '#a855f7', parked: '#9ca3af' }
const TYPE_LABEL = { active: 'Active', live: 'Live', idea: 'Idea', parked: 'Parked', all: 'All' }

function ProjectsWidget() {
  const [projects,  setProjects]  = useState([])
  const [selected,  setSelected]  = useState(null)
  const [activeTab, setActiveTab] = useState('all')

  useEffect(() => {
    fetch('/api/projects').then(r => r.json()).then(d => setProjects(d.projects || [])).catch(() => {})
  }, [])

  function togglePin(p) {
    const next = !p.pinned
    setProjects(prev => prev.map(x => x.id === p.id ? { ...x, pinned: next } : x))
    if (selected?.id === p.id) setSelected(s => ({ ...s, pinned: next }))
    fetch(`/api/projects/${p.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pinned: next }) }).catch(() => {})
  }

  const [projPage, setProjPage] = useState(0)
  const PAGE_SIZE = 5

  const sorted = [...projects].sort((a, b) => {
    if (a.pinned && !b.pinned) return -1
    if (!a.pinned && b.pinned) return 1
    return (b.updated || '').localeCompare(a.updated || '')
  })
  const filtered = activeTab === 'all' ? sorted : sorted.filter(p => (p.type || 'active') === activeTab)
  const pages    = Math.ceil(filtered.length / PAGE_SIZE)
  const visible  = filtered.slice(projPage * PAGE_SIZE, (projPage + 1) * PAGE_SIZE)
  const stale30  = p => p.updated && Math.floor((Date.now() - new Date(p.updated)) / 86400000) >= 30

  useEffect(() => { setProjPage(0) }, [activeTab])

  return (
    <>
      {selected && <ProjectProfile project={selected} onClose={() => setSelected(null)} onPin={p => togglePin(p)} />}
      <div className="kai-inner" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexShrink: 0 }}>
          <span className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            Projects
            {projects.length > 0 && <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', background: 'var(--bg-muted)', borderRadius: 10, padding: '2px 7px' }}>{projects.length}</span>}
          </span>
        </div>
        {/* type tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 10, flexShrink: 0, overflowX: 'auto' }} className="no-scrollbar">
          {TYPE_TABS.map(t => {
            const count  = t === 'all' ? projects.length : projects.filter(p => (p.type || 'active') === t).length
            const color  = TYPE_COLOR[t] || 'var(--text-muted)'
            const active = activeTab === t
            return (
              <button key={t} onClick={() => setActiveTab(t)} style={{ all: 'unset', cursor: 'pointer', fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', padding: '3px 9px', borderRadius: 20, flexShrink: 0, border: `1px solid ${active ? color + '60' : 'var(--border)'}`, background: active ? color + '15' : 'transparent', color: active ? color : 'var(--text-muted)', transition: 'all 0.15s' }}>
                {TYPE_LABEL[t]} {count > 0 && <span style={{ opacity: 0.7 }}>· {count}</span>}
              </button>
            )
          })}
        </div>
        {/* list — 5 per page, dot pagination */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
          {visible.map(p => {
            const sc  = SDOT[p.status]  || '#9ca3af'
            const pct = p.milestone_pct ?? null
            const ago = daysAgo(p.updated)
            const isSelected = selected?.id === p.id
            const typeColor = TYPE_COLOR[p.type] || TYPE_COLOR.active
            return (
              <div key={p.id} onClick={() => setSelected(isSelected ? null : p)}
                style={{ padding: '7px 11px', borderRadius: 10, border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border)'}`, background: isSelected ? 'var(--accent-bg)' : 'var(--bg-card)', cursor: 'pointer', transition: 'all 0.15s', flexShrink: 0 }}
                onMouseEnter={e => { if (!isSelected) { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.borderColor = 'var(--accent)' } }}
                onMouseLeave={e => { if (!isSelected) { e.currentTarget.style.background = 'var(--bg-card)'; e.currentTarget.style.borderColor = 'var(--border)' } }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  {p.pinned && <Pin size={9} color="var(--accent)" strokeWidth={2.5} style={{ flexShrink: 0 }} />}
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: sc, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                  {p.type && <span style={{ fontSize: 8, fontWeight: 700, padding: '1px 5px', borderRadius: 4, flexShrink: 0, background: typeColor + '18', color: typeColor, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{p.type}</span>}
                  {stale30(p) && <span style={{ fontSize: 8, color: '#f59e0b', fontWeight: 700, flexShrink: 0 }}>STALE</span>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 10, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.milestone || p.next}</span>
                  {pct !== null && <><div style={{ width: 36, height: 2, borderRadius: 1, background: '#e8ecf1', overflow: 'hidden', flexShrink: 0 }}><div style={{ height: '100%', width: `${pct}%`, background: sc }} /></div><span style={{ fontSize: 10, fontWeight: 600, color: sc, flexShrink: 0 }}>{pct}%</span></>}
                  {ago && <span style={{ fontSize: 10, color: 'var(--text-subtle)', flexShrink: 0 }}>{ago}</span>}
                </div>
              </div>
            )
          })}
          {filtered.length === 0 && <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', padding: '20px 0', margin: 0 }}>No {activeTab === 'all' ? '' : activeTab} projects.</p>}
        </div>
        {pages > 1 && (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 5, paddingTop: 10, flexShrink: 0 }}>
            {Array.from({ length: pages }).map((_, i) => (
              <button key={i} onClick={() => setProjPage(i)} style={{ all: 'unset', cursor: 'pointer', width: i === projPage ? 14 : 5, height: 5, borderRadius: 3, background: i === projPage ? 'var(--accent)' : 'var(--border)', transition: 'all 0.2s' }} />
            ))}
          </div>
        )}
      </div>
    </>
  )
}

// ── Harmony + Habits (combined) ─────────────────────────────────────────────

const _DEFAULT_BAR_GROUPS = [
  { name: 'Life',          ids: ['life-vision', 'passion-sex'],                                           color: '#10b981', icon: 'Compass' },
  { name: 'Body',          ids: ['health-fitness', 'quality-of-life'],                                    color: '#f97316', icon: 'Activity' },
  { name: 'Mind',          ids: ['intellectual-life', 'emotional-life', 'character', 'spiritual-life'],   color: '#a855f7', icon: 'Brain' },
  { name: 'Work & Money',  ids: ['career', 'financial-life'],                                             color: '#3b82f6', icon: 'Briefcase' },
  { name: 'Relationships', ids: ['love-relationship', 'parenting', 'social-life'],                        color: '#ec4899', icon: 'HeartHandshake' },
]

function domainStatus(aspects) {
  const vals = Object.values(aspects || {}).map(a => a.status || 'green')
  if (vals.includes('red'))    return 'red'
  if (vals.includes('yellow')) return 'yellow'
  return 'green'
}

const HABIT_COLORS = ['#e53935','#e64a19','#f57c00','#f9a825','#fdd835','#c0ca33','#7cb342','#2e7d32','#00695c','#00838f','#0277bd','#1565c0','#283593','#4527a0','#6a1b9a','#ad1457','#880e4f','#4e342e','#546e7a','#37474f']

function HarmonyHabitsWidget() {
  const [domains, setDomains] = useState([])
  const [habits,  setHabits]  = useState([])
  const today = new Date().toISOString().slice(0, 10)
  const BAR_GROUPS = loadGroupConfig(_DEFAULT_BAR_GROUPS)

  useEffect(() => {
    fetch('/api/harmony').then(r => r.json()).then(d => setDomains(d.domains || [])).catch(() => {})
    fetch('/api/habits').then(r => r.json()).then(d => setHabits(d.habits || d || [])).catch(() => {})
  }, [])

  function toggle(h) {
    const done = h.completions?.includes(today)
    fetch(`/api/habits/${h.id}/complete`, { method: done ? 'DELETE' : 'POST' })
      .then(() => setHabits(prev => prev.map(x => x.id === h.id
        ? { ...x, completions: done ? x.completions.filter(c => c !== today) : [...(x.completions || []), today] }
        : x)))
      .catch(() => {})
  }

  const doneCount = habits.slice(0, 6).filter(h => h.completions?.includes(today)).length

  return (
    <div style={{ flex: 1, background: 'var(--bg-surface)', borderRadius: 16, border: '1px solid var(--border)', padding: '14px 14px 12px', display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden' }}>
      {/* Harmony */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <span className="section-title">Harmony</span>
        {domains.length > 0 && (() => {
          const allStats = BAR_GROUPS.flatMap(g => g.ids.map(id => domains.find(x => x.id === id)).filter(Boolean).map(d => domainStatus(d.aspects)))
          const aligned = allStats.filter(s => s === 'green').length
          return <span style={{ fontSize: 9, color: 'var(--text-muted)', fontWeight: 600 }}>{aligned}/{allStats.length}</span>
        })()}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, flexShrink: 0 }}>
        {BAR_GROUPS.map(g => {
          const stats = g.ids.map(id => { const d = domains.find(x => x.id === id); return d ? domainStatus(d.aspects) : null }).filter(Boolean)
          const pct = stats.length ? Math.round((stats.filter(s => s === 'green').length / stats.length) * 100) : 0
          return (
            <div key={g.name} style={{ position: 'relative', borderRadius: 6, overflow: 'hidden', background: g.color + '18', height: 22 }}>
              <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: `${pct}%`, background: `linear-gradient(to right, ${g.color}, ${g.color}cc)`, borderRadius: 6, transition: 'width 0.9s cubic-bezier(.4,0,.2,1)' }} />
              <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', alignItems: 'center', padding: '0 9px', gap: 6, pointerEvents: 'none' }}>
                <LucideIcon name={g.icon} size={10} color={pct > 30 ? 'rgba(255,255,255,0.85)' : g.color} />
                <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', flex: 1, color: pct > 30 ? 'rgba(255,255,255,0.9)' : g.color + 'cc' }}>{g.name}</span>
                <span style={{ fontSize: 9, fontWeight: 600, color: pct > 85 ? 'rgba(255,255,255,0.7)' : g.color + '99' }}>{pct}%</span>
              </div>
            </div>
          )
        })}
      </div>
      {/* Divider */}
      <div style={{ height: 1, background: 'var(--border)', flexShrink: 0 }} />
      {/* Habits */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <span className="section-title">Habits</span>
        {habits.length > 0 && <span style={{ fontSize: 9, color: 'var(--text-muted)', fontWeight: 600 }}>{doneCount}/{Math.min(habits.length, 6)}</span>}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gridTemplateRows: 'repeat(2, 1fr)', gap: 6, flex: 1, minHeight: 0 }}>
        {Array.from({ length: 6 }).map((_, i) => {
          const h = habits[i]
          if (!h) return (
            <div key={`empty-${i}`} style={{ borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-muted)', border: '1.5px dashed var(--border)' }}>
              <Plus size={13} color="var(--text-muted)" />
            </div>
          )
          const done  = h.completions?.includes(today)
          const color = done ? '#22c55e' : (HABIT_COLORS[h.color ?? 0] || '#6366f1')
          const emoji = h.emoji || (h.displayName || h.name || '?')[0]
          return (
            <button key={h.id} onClick={() => toggle(h)} style={{ all: 'unset', cursor: 'pointer', borderRadius: 10, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, background: done ? '#22c55e15' : color + '15', border: `1.5px solid ${done ? '#22c55e50' : color + '40'}`, transition: 'all 0.2s' }}>
              <span style={{ fontSize: 18, lineHeight: 1 }}>{done ? '✓' : emoji}</span>
              <span style={{ fontSize: 7, fontWeight: 700, color: done ? '#22c55e' : color, letterSpacing: '0.04em', textAlign: 'center', lineHeight: 1.2, maxWidth: '92%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{(h.displayName || h.name || '').toUpperCase()}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Tasks 3-Column ──────────────────────────────────────────────────────────

const PRIORITY_COLOR = { 1: '#ef4444', 2: '#f97316', 3: '#f59e0b', 4: '#9ca3af' }
const TODAY_DATE = new Date().toISOString().slice(0, 10)

function TaskCard({ task, colId, onDone, onMove }) {
  const [gone, setGone] = useState(false)
  const ref = React.useRef(null)
  if (gone) return null
  const pc = PRIORITY_COLOR[task.priority || 4]

  function onDragStart(e) {
    e.dataTransfer.setData('taskId', task.id)
    e.dataTransfer.setData('fromCol', colId)
    e.dataTransfer.effectAllowed = 'move'
    setTimeout(() => { if (ref.current) ref.current.style.opacity = '0.4' }, 0)
  }
  function onDragEnd() { if (ref.current) ref.current.style.opacity = '1' }

  return (
    <div ref={ref} draggable onDragStart={onDragStart} onDragEnd={onDragEnd}
      style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '5px 8px', borderRadius: 7, background: 'var(--bg-card)', border: '1px solid var(--border)', transition: 'border-color 0.15s', flexShrink: 0, cursor: 'grab', userSelect: 'none' }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = pc + '50' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      <div style={{ width: 5, height: 5, borderRadius: '50%', background: pc, flexShrink: 0, marginTop: 4 }} />
      <span style={{ flex: 1, fontSize: 11, color: 'var(--text-primary)', lineHeight: 1.4 }}>{task.content}</span>
      <button onClick={() => { setGone(true); fetch('/api/tasks/' + task.id + '/complete', { method: 'POST' }).then(() => onDone(task.id)).catch(() => {}) }}
        style={{ all: 'unset', cursor: 'pointer', fontSize: 11, color: '#10b981', flexShrink: 0, padding: '0 2px', lineHeight: 1 }}>✓</button>
    </div>
  )
}

const _TASK_TODAY = new Date().toISOString().slice(0, 10)
const _TASK_IN7   = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
function taskBucket(t) {
  if (!t.due) return 'backlog'
  if (t.due <= _TASK_TODAY) return 'today'
  if (t.due <= _TASK_IN7)  return 'week'
  return 'backlog'
}
async function moveTaskWidget(id, toCol) {
  let body = {}
  if (toCol === 'today')   body = { move_to_today: true }
  if (toCol === 'week')    body = { due_date: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10) }
  if (toCol === 'backlog') body = { due_date: '' }
  await fetch('/api/tasks/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
}

function TasksWidget() {
  const [tasks,   setTasks]   = useState([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/tasks').then(r => r.json()).then(d => {
      const all  = [...(d.today || []), ...(d.inbox || [])]
      const seen = new Set()
      setTasks(all.filter(t => { if (seen.has(t.id)) return false; seen.add(t.id); return true }))
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const handleDone = id => setTasks(p => p.filter(t => t.id !== id))
  async function handleMove(id, toCol) {
    await moveTaskWidget(id, toCol)
    setTasks(prev => prev.map(t => {
      if (t.id !== id) return t
      if (toCol === 'today')   return { ...t, due: _TASK_TODAY }
      if (toCol === 'week')    return { ...t, due: new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10) }
      if (toCol === 'backlog') return { ...t, due: null }
      return t
    }))
  }

  const todayTs   = tasks.filter(t => taskBucket(t) === 'today').sort((a,b) => (a.priority||4)-(b.priority||4))
  const weekTs    = tasks.filter(t => taskBucket(t) === 'week')
  const backlogTs = tasks.filter(t => taskBucket(t) === 'backlog').sort((a,b) => (a.priority||4)-(b.priority||4))

  function Col({ id, title, color, items }) {
    const [over, setOver] = useState(false)
    const borderVal = over ? ('1px dashed ' + color + '50') : '1px solid transparent'
    const bgVal = over ? (color + '08') : 'transparent'
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden', borderRadius: 8, border: borderVal, background: bgVal, transition: 'all 0.15s', padding: 3 }}
        onDragOver={e => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); const tid = e.dataTransfer.getData('taskId'); const from = e.dataTransfer.getData('fromCol'); if (tid && from !== id) handleMove(tid, id) }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 6, flexShrink: 0 }}>
          <div style={{ width: 5, height: 5, borderRadius: '50%', background: color, flexShrink: 0 }} />
          <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color }}>{title}</span>
          <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', background: 'var(--bg-muted)', borderRadius: 8, padding: '1px 5px', flexShrink: 0 }}>{items.length}</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 3 }}>
          {items.length === 0
            ? <p style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', padding: '10px 0', margin: 0 }}>{over ? 'Drop' : '—'}</p>
            : items.map(t => <TaskCard key={t.id} task={t} colId={id} onDone={handleDone} onMove={handleMove} />)}
        </div>
      </div>
    )
  }

  return (
    <div className="kai-inner" style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexShrink: 0 }}>
        <span className="section-title">Tasks</span>
        <button onClick={() => navigate('/tasks')} title="Go to Tasks"
          style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)', padding: 4, borderRadius: 6, display: 'flex', transition: 'color 0.15s' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)' }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)' }}
        ><ListTodo size={14} /></button>
      </div>
      {loading ? <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--text-muted)', fontSize: 12 }}>Loading…</div> : (
        <div style={{ flex: 1, display: 'flex', gap: 8, overflow: 'hidden' }}>
          <Col id="today"   title="Today"     color="#6366f1" items={todayTs} />
          <div style={{ width: 1, background: 'var(--border)', flexShrink: 0 }} />
          <Col id="week"    title="This Week"  color="#10b981" items={weekTs} />
          <div style={{ width: 1, background: 'var(--border)', flexShrink: 0 }} />
          <Col id="backlog" title="Backlog"    color="#f59e0b" items={backlogTs} />
        </div>
      )}
    </div>
  )
}

// ── Advisor Tab Card ─────────────────────────────────────────────────────────

const COUNCIL_ADVISORS = ADVISORS.filter(a => a.id !== 'biz')

function AdvisorTabCard({ advisor, isActive, onClick }) {
  const [imgErr, setImgErr] = useState(false)
  return (
    <button onClick={onClick} style={{
      all: 'unset', cursor: 'pointer',
      width: isActive ? 104 : 52, flexShrink: 0,
      display: 'flex', flexDirection: 'column',
      position: 'relative', overflow: 'hidden',
      borderRadius: 10,
      border: `1px solid ${isActive ? advisor.color + '70' : 'rgba(255,255,255,0.05)'}`,
      background: isActive ? advisor.color + '20' : 'transparent',
      transition: 'all 0.15s',
      opacity: isActive ? 1 : 0.5,
    }}
      onMouseEnter={e => { if (!isActive) { e.currentTarget.style.opacity = '0.8'; e.currentTarget.style.borderColor = advisor.color + '40' } }}
      onMouseLeave={e => { if (!isActive) { e.currentTarget.style.opacity = '0.5'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.05)' } }}
    >
      {/* Color accent top bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: advisor.color, opacity: isActive ? 1 : 0.5, zIndex: 2 }} />
      {/* Full-height photo fill */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden', background: advisor.color + '18', minHeight: 0 }}>
        {advisor.avatar && !imgErr
          ? <img src={advisor.avatar} alt={advisor.name} onError={() => setImgErr(true)}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top' }} />
          : <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26 }}>{advisor.emoji}</div>
        }
        {/* Name overlay */}
        <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: '14px 3px 4px', background: 'linear-gradient(to top, rgba(0,0,0,0.75) 0%, transparent 100%)', zIndex: 1 }}>
          <span style={{ fontSize: 8, fontWeight: 700, color: '#fff', textAlign: 'center', display: 'block', letterSpacing: '0.06em', textTransform: 'uppercase', opacity: isActive ? 1 : 0.8 }}>{advisor.name}</span>
        </div>
      </div>
    </button>
  )
}

// ── Session Context Line (below greeting) ────────────────────────────────────

function SessionContextLine() {
  const [ctx, setCtx] = useState(null)
  useEffect(() => {
    function load() { fetch('/api/session-context').then(r => r.json()).then(setCtx).catch(() => {}) }
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])
  if (!ctx || !ctx.resets_iso) return null
  const pct = ctx.pct || 0
  const color = pct >= 80 ? '#ef4444' : pct >= 60 ? '#f59e0b' : 'var(--text-muted)'
  const resetsIn = (() => {
    const diff = Math.max(0, new Date(ctx.resets_iso) - Date.now())
    const h = Math.floor(diff / 3600000)
    const m = Math.floor((diff % 3600000) / 60000)
    if (h > 0) return `${h}h ${m}m`
    return `${m}m`
  })()
  return (
    <span style={{ fontSize: 11, color, letterSpacing: '-0.01em' }}>
      {pct.toFixed(0)}% of session · resets in {resetsIn}
    </span>
  )
}

// ── Inline Token Badge (for greeting row) ────────────────────────────────────

function TokenBadge() {
  const [data, setData] = useState(null)
  useEffect(() => {
    function load() { fetch('/api/token-usage').then(r => r.json()).then(setData).catch(() => {}) }
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [])
  if (!data) return null
  const todayStr   = new Date().toISOString().slice(0, 10)
  const todayEntry = (data.days || []).find(d => d.date === todayStr) || { cost_usd: 0, calls: 0 }
  const dailyCap   = data.daily_cap_usd || 5
  const pct        = Math.min(Math.round(((todayEntry.cost_usd || 0) / dailyCap) * 100), 100)
  const barColor   = pct > 80 ? '#ef4444' : pct > 50 ? '#f59e0b' : '#10b981'
  const fmtCost    = c => c < 0.01 ? '<$0.01' : `$${c.toFixed(2)}`
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{fmtCost(todayEntry.cost_usd || 0)} today</span>
      <div style={{ width: 40, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{pct}% daily</span>
      <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>· {todayEntry.calls} calls</span>
    </div>
  )
}

// ── Parking Lot tile grid ───────────────────────────────────────────────────

const LOT_TYPE_META = {
  article:  { label: 'Articles',  color: '#f97316', bg: '#f9731615', icon: FileText },
  idea:     { label: 'Ideas',     color: '#a855f7', bg: '#a855f715', icon: Lightbulb },
  product:  { label: 'Products',  color: '#3b82f6', bg: '#3b82f615', icon: ShoppingBag },
  recipe:   { label: 'Recipes',   color: '#10b981', bg: '#10b98115', icon: UtensilsCrossed },
  note:     { label: 'Notes',     color: '#94a3b8', bg: '#94a3b815', icon: FileText },
  link:     { label: 'Links',     color: '#06b6d4', bg: '#06b6d415', icon: Link2 },
  music:    { label: 'Music',     color: '#ec4899', bg: '#ec489915', icon: Music },
  video:    { label: 'Videos',    color: '#ef4444', bg: '#ef444415', icon: Video },
  item:     { label: 'Items',     color: '#64748b', bg: '#64748b15', icon: BookOpen },
}
const LOT_ADVISORS = ['kai', 'beats', 'creative', 'dev', 'sky', 'roads']
const LOT_PAGE_SIZE = 10

function lotMeta(type) { return LOT_TYPE_META[type] || LOT_TYPE_META.item }

function LotTile({ item, onRoute, onArchive, onDelete }) {
  const [showOverlay, setShowOverlay] = useState(false)
  const [showRoute,   setShowRoute]   = useState(false)
  const [imgFailed,   setImgFailed]   = useState(false)
  const routeRef = useRef(null)
  const m = lotMeta(item.type)
  const Icon = m.icon
  const hasUrl = item.url?.startsWith('http')

  useEffect(() => {
    if (!showRoute) return
    function h(e) { if (routeRef.current && !routeRef.current.contains(e.target)) setShowRoute(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [showRoute])

  return (
    <div
      style={{ position: 'relative', borderRadius: 12, overflow: 'hidden', background: 'var(--bg-card)', border: '1px solid var(--border)', cursor: hasUrl ? 'pointer' : 'default', transition: 'border-color 0.15s, transform 0.15s', aspectRatio: '4/3' }}
      onMouseEnter={e => { setShowOverlay(true); e.currentTarget.style.borderColor = m.color + '50'; e.currentTarget.style.transform = 'translateY(-1px)' }}
      onMouseLeave={e => { setShowOverlay(false); e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = 'translateY(0)' }}
      onClick={() => hasUrl && window.open(item.url, '_blank', 'noreferrer')}
    >
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: m.color, zIndex: 2 }} />
      {item.image && !imgFailed ? (
        <img src={item.image} alt="" onError={() => setImgFailed(true)}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: m.bg }}>
          <Icon size={28} color={m.color} strokeWidth={1.5} />
        </div>
      )}
      <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: '8px 10px 8px', background: 'linear-gradient(to top, rgba(0,0,0,0.75) 0%, transparent 100%)', display: 'flex', flexDirection: 'column', gap: 3, zIndex: 2 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ fontSize: 8, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: m.color, background: m.bg, border: `1px solid ${m.color}40`, borderRadius: 4, padding: '1px 5px', flexShrink: 0 }}>
            {lotMeta(item.type).label.slice(0, -1)}
          </span>
          {item.date && <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.4)' }}>{item.date}</span>}
        </div>
        <p style={{ fontSize: 10, fontWeight: 600, color: '#fff', margin: 0, lineHeight: 1.3, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{item.title}</p>
      </div>
      {showOverlay && (
        <div style={{ position: 'absolute', inset: 0, zIndex: 3, background: 'linear-gradient(to bottom, rgba(0,0,0,0.88) 0%, rgba(0,0,0,0.95) 100%)', padding: '12px 10px 10px', display: 'flex', flexDirection: 'column', gap: 6 }}
          onClick={e => e.stopPropagation()}
        >
          <p style={{ fontSize: 11, fontWeight: 600, color: '#fff', margin: 0, lineHeight: 1.4, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{item.title}</p>
          {item.summary && <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.6)', margin: 0, lineHeight: 1.5, flex: 1, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical' }}>{item.summary}</p>}
          {!item.summary && <div style={{ flex: 1 }} />}
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 'auto' }}>
            <div ref={routeRef} style={{ position: 'relative' }}>
              <button onClick={e => { e.stopPropagation(); setShowRoute(v => !v) }}
                style={{ all: 'unset', cursor: 'pointer', fontSize: 9, fontWeight: 600, padding: '3px 8px', borderRadius: 6, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)', color: 'rgba(255,255,255,0.7)', display: 'flex', alignItems: 'center', gap: 3 }}>
                Route <ChevronDown size={8} />
              </button>
              {showRoute && (
                <div style={{ position: 'absolute', bottom: '100%', left: 0, marginBottom: 4, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10, padding: 6, minWidth: 110, boxShadow: '0 8px 24px rgba(0,0,0,0.5)', zIndex: 50 }}>
                  {LOT_ADVISORS.map(a => (
                    <button key={a} onClick={() => { onRoute(item.slug, a); setShowRoute(false) }}
                      style={{ all: 'unset', cursor: 'pointer', display: 'block', width: '100%', textAlign: 'left', fontSize: 10, padding: '4px 8px', borderRadius: 5, color: 'var(--text-secondary)', transition: 'background 0.1s', textTransform: 'capitalize' }}
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >{a}</button>
                  ))}
                </div>
              )}
            </div>
            <button onClick={() => onArchive(item.slug)}
              style={{ all: 'unset', cursor: 'pointer', padding: '3px 6px', borderRadius: 5, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)', color: 'rgba(255,255,255,0.5)' }}>
              <Archive size={10} />
            </button>
            <button onClick={() => onDelete(item.slug)}
              style={{ all: 'unset', cursor: 'pointer', padding: '3px 6px', borderRadius: 5, background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5' }}>
              <Trash2 size={10} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function LotTypeSection({ type, items, onRoute, onArchive, onDelete }) {
  const [page, setPage] = useState(1)
  const m = lotMeta(type)
  const visible = items.slice(0, page * LOT_PAGE_SIZE)
  const hasMore = items.length > visible.length
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div style={{ width: 3, height: 14, borderRadius: 2, background: m.color, flexShrink: 0 }} />
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.09em', color: m.color }}>{m.label}</span>
        <span style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-muted)', background: 'var(--bg-muted)', borderRadius: 10, padding: '1px 7px' }}>{items.length}</span>
        <div style={{ flex: 1, height: 1, background: m.color + '25' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8 }}>
        {visible.map(item => <LotTile key={item.slug} item={item} onRoute={onRoute} onArchive={onArchive} onDelete={onDelete} />)}
      </div>
      {hasMore && (
        <button onClick={() => setPage(p => p + 1)} style={{ all: 'unset', cursor: 'pointer', display: 'block', width: '100%', textAlign: 'center', marginTop: 8, padding: '7px 0', fontSize: 10, fontWeight: 600, color: m.color, border: `1px solid ${m.color}30`, borderRadius: 7, background: m.bg }}>
          Load more · {items.length - visible.length} remaining
        </button>
      )}
    </div>
  )
}

function LotWidget() {
  const [items,       setItems]       = useState([])
  const [captureText, setCaptureText] = useState('')
  const [capturing,   setCapturing]   = useState(false)
  const [activeTab,   setActiveTab]   = useState('recent')
  const navigate = useNavigate()

  async function fetchLot() {
    try {
      const d = await api.getParkingLot(); setItems(d.items || [])
    } catch {}
  }
  useEffect(() => { fetchLot(); const t = setInterval(fetchLot, 30000); return () => clearInterval(t) }, [])

  async function handleCapture(e) {
    e.preventDefault()
    if (!captureText.trim()) return
    setCapturing(true)
    try { await api.quickCapture(captureText.trim()); setCaptureText(''); setTimeout(fetchLot, 1200) }
    catch {} finally { setCapturing(false) }
  }

  async function handleRoute(slug, advisor) {
    try { await api.routeCapture(slug, advisor); setItems(prev => prev.filter(i => i.slug !== slug)) } catch {}
  }
  async function handleArchive(slug) {
    try { await api.archiveCapture(slug); setItems(prev => prev.filter(i => i.slug !== slug)) } catch {}
  }
  async function handleDelete(slug) {
    try { await api.deleteCapture(slug); setItems(prev => prev.filter(i => i.slug !== slug)) } catch {}
  }

  const TYPE_ORDER = ['article', 'idea', 'link', 'product', 'music', 'video', 'recipe', 'note', 'item']
  const grouped = {}
  ;[...items].sort((a, b) => (b.date || '').localeCompare(a.date || '')).forEach(item => {
    const t = item.type || 'item'
    if (!grouped[t]) grouped[t] = []
    grouped[t].push(item)
  })
  const orderedTypes = [...TYPE_ORDER.filter(t => grouped[t]), ...Object.keys(grouped).filter(t => !TYPE_ORDER.includes(t))]

  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 16, padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          <Inbox size={14} color="var(--text-muted)" strokeWidth={1.75} />
          Parking Lot
          <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text-muted)' }}>{items.length} items</span>
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={fetchLot} style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
          ><RefreshCw size={12} /></button>
          <a href="/parking-lot" style={{ fontSize: 11, color: 'var(--text-muted)', textDecoration: 'none' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--accent)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>View all →</a>
        </div>
      </div>
      <form onSubmit={handleCapture} style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <input type="text" value={captureText} onChange={e => setCaptureText(e.target.value)} placeholder="Drop a link, idea, or anything worth saving…"
          style={{ flex: 1, fontSize: 12, padding: '8px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)', outline: 'none', fontFamily: 'inherit', transition: 'border-color 0.15s' }}
          onFocus={e => e.target.style.borderColor = 'rgba(99,102,241,0.5)'}
          onBlur={e => e.target.style.borderColor = 'var(--border)'}
        />
        <button type="submit" disabled={capturing || !captureText.trim()}
          style={{ padding: '8px 16px', borderRadius: 10, background: capturing || !captureText.trim() ? 'var(--border)' : '#6366f1', color: '#fff', fontSize: 12, fontWeight: 600, border: 'none', cursor: capturing || !captureText.trim() ? 'default' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, transition: 'background 0.15s', flexShrink: 0 }}>
          <SendIcon size={12} />{capturing ? 'Saving…' : 'Capture'}
        </button>
      </form>
      {items.length === 0
        ? <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-muted)', fontSize: 12 }}>The lot is empty.</div>
        : <>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
              {/* Recent tab */}
              {(() => {
                const isActive = activeTab === 'recent'
                return (
                  <button onClick={() => setActiveTab('recent')}
                    style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', padding: '5px 12px', borderRadius: 20, border: `1px solid ${isActive ? '#6366f160' : 'var(--border)'}`, background: isActive ? '#6366f115' : 'transparent', color: isActive ? '#a5b4fc' : 'var(--text-muted)', transition: 'all 0.15s' }}
                    onMouseEnter={e => { if (!isActive) { e.currentTarget.style.borderColor = '#6366f140'; e.currentTarget.style.color = '#a5b4fc' } }}
                    onMouseLeave={e => { if (!isActive) { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' } }}
                  >
                    <Clock size={10} strokeWidth={2} />Recent <span style={{ opacity: 0.6 }}>· {Math.min(items.length, 10)}</span>
                  </button>
                )
              })()}
              {/* Type tabs */}
              {orderedTypes.map(t => {
                const m = lotMeta(t)
                const Icon = m.icon
                const isActive = activeTab === t
                return (
                  <button key={t} onClick={() => setActiveTab(t)}
                    style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', padding: '5px 12px', borderRadius: 20, border: `1px solid ${isActive ? m.color + '60' : 'var(--border)'}`, background: isActive ? m.color + '15' : 'transparent', color: isActive ? m.color : 'var(--text-muted)', transition: 'all 0.15s' }}
                    onMouseEnter={e => { if (!isActive) { e.currentTarget.style.borderColor = m.color + '40'; e.currentTarget.style.color = m.color } }}
                    onMouseLeave={e => { if (!isActive) { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' } }}
                  >
                    <Icon size={10} strokeWidth={2} />{m.label} <span style={{ opacity: 0.6 }}>· {grouped[t].length}</span>
                  </button>
                )
              })}
            </div>
            {activeTab === 'recent'
              ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8 }}>
                  {[...items].sort((a, b) => (b.date || '').localeCompare(a.date || '')).slice(0, 10).map(item => (
                    <LotTile key={item.slug} item={item} onRoute={handleRoute} onArchive={handleArchive} onDelete={handleDelete} />
                  ))}
                </div>
              : grouped[activeTab]
                ? <LotTypeSection type={activeTab} items={grouped[activeTab]} onRoute={handleRoute} onArchive={handleArchive} onDelete={handleDelete} />
                : null
            }
          </>
      }
    </div>
  )
}

// ── Chat Widget ────────────────────────────────────────────────────────────

const DEFAULT_FUNCTIONS = [
  { id: 'gm',         label: 'Good Morning',  prompt: "Good morning KAI — let's do my morning check-in. What should I focus on today?", send: true },
  { id: 'gn',         label: 'Good Night',    prompt: "Good night KAI — quick recap. What did I accomplish today and what should I prioritize tomorrow?", send: true },
  { id: 'research',   label: 'Research',      prompt: 'Research: ',   send: false },
  { id: 'brainstorm', label: 'Brainstorm',    prompt: 'Brainstorm: ', send: false },
]

function ChatWidget() {
  const [advisor,  setAdvisor]  = useState(getAdvisor('kai'))
  const [messages, setMessages] = useState([])
  const [input,    setInput]    = useState('')
  const [thinking, setThinking] = useState(false)
  const [functions,   setFunctions]   = useState(DEFAULT_FUNCTIONS)
  const [showFuncEd,  setShowFuncEd]  = useState(false)
  const [editingFunc, setEditingFunc] = useState(null)
  const [funcLabel,   setFuncLabel]   = useState('')
  const [funcPrompt,  setFuncPrompt]  = useState('')
  const [funcSend,    setFuncSend]    = useState(false)
  const [modelCfg, setModelCfg] = useState({})
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  useEffect(() => {
    api.getChannelHistory(advisor.channel).then(d => setMessages(d.messages || [])).catch(() => {})
  }, [advisor.channel])

  function fetchWorkflows() {
    fetch('/api/workflows').then(r => r.json()).then(d => { if (d.workflows?.length) setFunctions(d.workflows) }).catch(() => {})
  }
  useEffect(() => { fetchWorkflows() }, [])
  useEffect(() => {
    fetch('/council/models/config').then(r => r.json()).then(d => setModelCfg(d.advisors || {})).catch(() => {})
  }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, thinking])

  async function send(overrideText) {
    const text = (overrideText ?? input).trim()
    if (!text || thinking) return
    setInput('')
    const history = messages.map(m => ({ role: m.role, content: m.content }))
    setMessages(p => [...p, { role: 'user', content: text, ts: String(Date.now() / 1000) }])
    setThinking(true)
    try {
      const d = await api.sendMessage(text, advisor.channel, history)
      setMessages(p => [...p, { role: 'assistant', content: d.reply || d.message || '', ts: String(Date.now() / 1000), provider: d.provider, model: d.model }])
    } catch {
      setMessages(p => [...p, { role: 'assistant', content: 'Something went wrong.', error: true, ts: String(Date.now() / 1000) }])
    } finally { setThinking(false); inputRef.current?.focus(); fetchWorkflows() }
  }

  function fireFunction(fn) { if (fn.send) { send(fn.prompt) } else { setInput(fn.prompt); inputRef.current?.focus() } }
  async function saveWorkflowToAPI(entry) { await fetch('/api/workflows', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry) }).catch(() => {}); fetchWorkflows() }
  async function deleteWorkflowFromAPI(id) { await fetch(`/api/workflows/${id}`, { method: 'DELETE' }).catch(() => {}); fetchWorkflows() }
  function openNewFunc()    { setEditingFunc(null); setFuncLabel(''); setFuncPrompt(''); setFuncSend(false); setShowFuncEd(true) }
  function openEditFunc(fn) { setEditingFunc(fn); setFuncLabel(fn.label); setFuncPrompt(fn.prompt); setFuncSend(fn.send); setShowFuncEd(true) }
  function saveFunc() {
    if (!funcLabel.trim() || !funcPrompt.trim()) return
    saveWorkflowToAPI({ id: editingFunc?.id || funcLabel.trim().toLowerCase().replace(/\s+/g, '-'), label: funcLabel.trim(), prompt: funcPrompt.trim(), send: funcSend })
    setShowFuncEd(false)
  }
  function deleteFunc(id) { deleteWorkflowFromAPI(id); setShowFuncEd(false) }
  function resetFunctions() { DEFAULT_FUNCTIONS.forEach(f => saveWorkflowToAPI(f)) }

  return (
    <div style={{ background: 'var(--bg-card)', borderRadius: 20, border: `1px solid ${advisor.color}50`, display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>
      {/* Function editor modal */}
      {showFuncEd && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }} onClick={() => setShowFuncEd(false)}>
          <div style={{ background: 'var(--bg-card)', borderRadius: 16, padding: '20px 22px', border: '1px solid var(--border)', width: 340, boxShadow: '0 24px 48px rgba(0,0,0,0.4)' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{editingFunc ? 'Edit Command' : 'New Command'}</span>
              <button onClick={() => setShowFuncEd(false)} style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)' }}><XIcon size={16} /></button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div><label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 5 }}>Label</label>
                <input value={funcLabel} onChange={e => setFuncLabel(e.target.value)} placeholder="Good Morning" style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', outline: 'none' }} autoFocus /></div>
              <div><label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 5 }}>Prompt</label>
                <textarea value={funcPrompt} onChange={e => setFuncPrompt(e.target.value)} rows={3} style={{ width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text-primary)', fontSize: 12, fontFamily: 'inherit', resize: 'vertical', outline: 'none' }} /></div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)' }}>
                <input type="checkbox" checked={funcSend} onChange={e => setFuncSend(e.target.checked)} style={{ accentColor: 'var(--accent)', width: 14, height: 14 }} /> Send immediately
              </label>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 18, gap: 8 }}>
              {editingFunc ? <button onClick={() => deleteFunc(editingFunc.id)} style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: '#ef4444', display: 'flex', alignItems: 'center', gap: 4, padding: '8px 10px', borderRadius: 8, border: '1px solid #ef444433' }}><Trash2 size={13} /> Delete</button>
                : <button onClick={resetFunctions} style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', padding: '8px 10px' }}>Reset defaults</button>}
              <button onClick={saveFunc} disabled={!funcLabel.trim() || !funcPrompt.trim()} style={{ all: 'unset', cursor: 'pointer', fontSize: 13, fontWeight: 600, padding: '8px 18px', borderRadius: 9, background: funcLabel.trim() && funcPrompt.trim() ? 'var(--accent)' : 'var(--border)', color: '#fff', transition: 'all 0.15s' }}>Save</button>
            </div>
          </div>
        </div>
      )}

      {/* Advisor branded tabs — full height photo fill */}
      <div style={{
        flexShrink: 0, borderBottom: '1px solid var(--border)',
        background: `linear-gradient(to right, ${advisor.color}12 0%, transparent 70%)`,
        display: 'flex', alignItems: 'stretch', gap: 4,
        overflowX: 'auto', padding: '6px 10px', height: 92,
      }} className="no-scrollbar">
        {COUNCIL_ADVISORS.map(a => (
          <AdvisorTabCard key={a.id} advisor={a} isActive={advisor.id === a.id} onClick={() => setAdvisor(a)} />
        ))}
      </div>

      {/* Model indicator */}
      {(() => {
        const acfg = modelCfg[advisor.channel] || {}
        const prov = acfg.provider || 'anthropic'
        const mdl  = acfg.model || '—'
        const color = prov === 'anthropic' ? '#6366f1' : prov === 'ollama' ? '#f59e0b' : '#10a37f'
        const provLabel = prov === 'anthropic' ? 'Anthropic' : prov === 'ollama' ? 'Local' : 'OpenAI'
        return (
          <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '5px 16px', borderBottom: '1px solid var(--border)', background: color + '08' }}>
            <div style={{ width: 5, height: 5, borderRadius: '50%', background: color }} />
            <span style={{ fontSize: 10, color, fontWeight: 600 }}>{provLabel}</span>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{mdl}</span>
          </div>
        )
      })()}

      {/* Messages — scrollable */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'scroll', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12, position: 'relative' }}>
        {messages.length > 0 && (
          <button onClick={() => { setMessages([]); api.clearHistory(advisor.channel).catch(() => {}) }} title="Clear chat"
            style={{ position: 'absolute', top: 8, right: 10, all: 'unset', cursor: 'pointer', color: 'var(--text-subtle)', padding: 5, borderRadius: 6, display: 'flex', transition: 'color 0.15s', zIndex: 10 }}
            onMouseEnter={e => e.currentTarget.style.color = '#ef4444'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-subtle)'}
          ><XIcon size={13} /></button>
        )}
        {messages.length === 0 && !thinking && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6, opacity: 0.4 }}>
            <div style={{ fontSize: 28 }}>{advisor.emoji}</div>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', textAlign: 'center', margin: 0, lineHeight: 1.5 }}>{advisor.intro}</p>
          </div>
        )}
        {messages.map((m, i) => {
          const isUser = m.role === 'user'
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start', gap: 2 }}>
              <div style={{ maxWidth: '84%', padding: '9px 13px', borderRadius: isUser ? '14px 14px 4px 14px' : '4px 14px 14px 14px', background: isUser ? advisor.color : 'var(--bg-elevated)', color: isUser ? '#fff' : 'var(--text-primary)', fontSize: 13, lineHeight: 1.55, border: isUser ? 'none' : '1px solid var(--border)', whiteSpace: 'pre-wrap' }}>{m.content}</div>
              <span style={{ fontSize: 9, color: 'var(--text-subtle)', padding: '0 4px' }}>{fmtTime(m.ts)}</span>
            </div>
          )
        })}
        {thinking && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
            <div style={{ fontSize: 18 }}>{advisor.emoji}</div>
            <div style={{ display: 'flex', gap: 4 }}>
              {[0,1,2].map(i => <div key={i} style={{ width: 5, height: 5, borderRadius: '50%', background: advisor.color, animation: `bounce 1.2s ease-in-out ${i * 0.2}s infinite` }} />)}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Quick functions */}
      <div style={{ flexShrink: 0, padding: '8px 12px 6px', borderTop: '1px solid var(--border)', display: 'flex', gap: 4, overflowX: 'auto', alignItems: 'center' }} className="no-scrollbar">
        {functions.map(fn => (
          <button key={fn.id} onClick={() => fireFunction(fn)} onContextMenu={e => { e.preventDefault(); openEditFunc(fn) }}
            style={{ all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 500, padding: '5px 11px', borderRadius: 20, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text-secondary)', whiteSpace: 'nowrap', flexShrink: 0, transition: 'all 0.15s' }}
            onMouseEnter={e => { e.currentTarget.style.background = advisor.color + '18'; e.currentTarget.style.borderColor = advisor.color + '50'; e.currentTarget.style.color = advisor.color }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
          >{fn.label}</button>
        ))}
        <button onClick={openNewFunc} title="New command" style={{ all: 'unset', cursor: 'pointer', flexShrink: 0, width: 24, height: 24, borderRadius: 12, border: '1px dashed var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', transition: 'all 0.15s' }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)' }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' }}
        ><Plus size={12} /></button>
      </div>

      {/* Input */}
      <div style={{ flexShrink: 0, padding: '8px 12px 12px', display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea ref={inputRef} value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder={`Message ${advisor.name}…`} rows={1}
          style={{ flex: 1, resize: 'none', padding: '9px 12px', borderRadius: 12, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', outline: 'none', maxHeight: 120, overflowY: 'auto', transition: 'border-color 0.15s', lineHeight: 1.5 }}
          onFocus={e => e.target.style.borderColor = advisor.color + '60'}
          onBlur={e => e.target.style.borderColor = 'var(--border)'}
        />
        <button onClick={() => send()} disabled={!input.trim() || thinking}
          style={{ all: 'unset', cursor: input.trim() && !thinking ? 'pointer' : 'default', width: 36, height: 36, borderRadius: 10, background: input.trim() && !thinking ? advisor.color : 'var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, transition: 'background 0.15s' }}>
          <SendIcon size={15} color="#fff" />
        </button>
      </div>
    </div>
  )
}


// ── Lot Nudge ──────────────────────────────────────────────────────────────

function LotNudge() {
  const [count, setCount] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/parking-lot/list')
      .then(r => r.json())
      .then(d => setCount((d.items || []).filter(i => i.status === 'new').length))
      .catch(() => setCount(0))
  }, [])

  if (!count) return null

  return (
    <button
      onClick={() => navigate('/parking-lot')}
      style={{
        all: 'unset', cursor: 'pointer', width: '100%', boxSizing: 'border-box',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 18px', borderRadius: 12,
        background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.2)',
        transition: 'all 0.2s',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.10)'; e.currentTarget.style.borderColor = 'rgba(245,158,11,0.35)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.06)'; e.currentTarget.style.borderColor = 'rgba(245,158,11,0.2)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Inbox size={14} color="#f59e0b" strokeWidth={1.75} />
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{count}</span>
          {' '}untriaged item{count !== 1 ? 's' : ''} in the Parking Lot
        </span>
      </div>
      <span style={{ fontSize: 12, color: '#f59e0b', fontWeight: 600, flexShrink: 0 }}>Review →</span>
    </button>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Today() {
  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="kai-card" style={{ padding: 20 }}>

          {/* Greeting row with inline token badge */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
            <div>
              <p style={{ fontSize: 21, fontWeight: 300, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 }}>
                {greeting()}, <strong style={{ fontWeight: 600 }}>Leo</strong>
              </p>
              <SessionContextLine />
            </div>
            <TokenBadge />
          </div>

          {/* Desktop grid:
              col1/row1 = Projects (5 items, scroll)
              col2/row1 = HarmonyHabits
              col3/rows1+2 = Chat
              col1+col2/row2 = Tasks (spans both) */}
          <div className="hidden md:grid" style={{
            gridTemplateColumns: '1.15fr 0.85fr 1.2fr',
            gridTemplateRows: '420px 280px',
            gap: 12,
          }}>
            <div style={{ gridColumn: 1, gridRow: 1, display: 'flex', overflow: 'hidden' }}><ProjectsWidget /></div>
            <div style={{ gridColumn: 2, gridRow: 1, display: 'flex', overflow: 'hidden' }}><HarmonyHabitsWidget /></div>
            <div style={{ gridColumn: 3, gridRow: '1 / 3', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}><ChatWidget /></div>
            <div style={{ gridColumn: '1 / 3', gridRow: 2, display: 'flex', overflow: 'hidden' }}><TasksWidget /></div>
          </div>

          {/* Mobile stack */}
          <div className="md:hidden flex flex-col" style={{ gap: 12 }}>
            <div style={{ minHeight: 480, display: 'flex', flexDirection: 'column' }}><ChatWidget /></div>
            <HarmonyHabitsWidget />
            <ProjectsWidget />
            <TasksWidget />
          </div>
        </div>

        {/* Lot nudge — shows when untriaged items exist */}
        <LotNudge />

        {/* Parking Lot inline widget */}
        <LotWidget />
      </div>
      <style>{`@keyframes bounce { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-4px)} }`}</style>
    </div>
  )
}