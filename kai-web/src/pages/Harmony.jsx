import { useState, useEffect, useRef } from 'react'
import { api } from '../lib/api'
import { HARMONY_DOMAINS, domainOverallStatus } from '../lib/harmonyData'
import { StatusDot, StatusToggle } from '../components/StatusBadge'
import {
  Activity, Dumbbell, Stethoscope, HeartPulse, Heart, Smile,
  Brain, BookOpen, Lightbulb, Compass, Infinity, Feather,
  Users, HeartHandshake, Baby, Globe, Home, Waves,
  Briefcase, TrendingUp, DollarSign, BarChart, Target, Trophy,
  Sparkles, Star, Sun, Moon, Flame, Zap,
  Music, Palette, Mic, Camera, Pen, Mountain,
  Shield, Crown, Award, TreePine, Leaf, Coffee,
  Gem, Rocket, Bike, Wind, Flower, Eye, Anchor, Map, Flag, Clock,
  CheckCircle, RefreshCw,
} from 'lucide-react'

const ICON_SET = [
  { name: 'Activity', C: Activity }, { name: 'Dumbbell', C: Dumbbell },
  { name: 'Stethoscope', C: Stethoscope }, { name: 'HeartPulse', C: HeartPulse },
  { name: 'Heart', C: Heart }, { name: 'Smile', C: Smile },
  { name: 'Brain', C: Brain }, { name: 'BookOpen', C: BookOpen },
  { name: 'Lightbulb', C: Lightbulb }, { name: 'Compass', C: Compass },
  { name: 'Infinity', C: Infinity }, { name: 'Feather', C: Feather },
  { name: 'Users', C: Users }, { name: 'HeartHandshake', C: HeartHandshake },
  { name: 'Baby', C: Baby }, { name: 'Globe', C: Globe },
  { name: 'Home', C: Home }, { name: 'Waves', C: Waves },
  { name: 'Briefcase', C: Briefcase }, { name: 'TrendingUp', C: TrendingUp },
  { name: 'DollarSign', C: DollarSign }, { name: 'BarChart', C: BarChart },
  { name: 'Target', C: Target }, { name: 'Trophy', C: Trophy },
  { name: 'Sparkles', C: Sparkles }, { name: 'Star', C: Star },
  { name: 'Sun', C: Sun }, { name: 'Moon', C: Moon },
  { name: 'Flame', C: Flame }, { name: 'Zap', C: Zap },
  { name: 'Music', C: Music }, { name: 'Palette', C: Palette },
  { name: 'Mic', C: Mic }, { name: 'Camera', C: Camera },
  { name: 'Pen', C: Pen }, { name: 'Mountain', C: Mountain },
  { name: 'Shield', C: Shield }, { name: 'Crown', C: Crown },
  { name: 'Award', C: Award }, { name: 'TreePine', C: TreePine },
  { name: 'Leaf', C: Leaf }, { name: 'Coffee', C: Coffee },
  { name: 'Gem', C: Gem }, { name: 'Rocket', C: Rocket },
  { name: 'Bike', C: Bike }, { name: 'Wind', C: Wind },
  { name: 'Flower', C: Flower }, { name: 'Eye', C: Eye },
  { name: 'Anchor', C: Anchor }, { name: 'Map', C: Map },
  { name: 'Flag', C: Flag }, { name: 'Clock', C: Clock },
]
const ICON_MAP = Object.fromEntries(ICON_SET.map(i => [i.name, i.C]))
function LucideIcon({ name, size = 16, color = 'currentColor' }) {
  const C = ICON_MAP[name]
  return C ? <C size={size} color={color} strokeWidth={1.75} /> : null
}

const DOMAIN_ICONS = {
  'health-fitness': 'Activity', 'intellectual-life': 'BookOpen',
  'emotional-life': 'Heart', 'character': 'Shield',
  'spiritual-life': 'Sparkles', 'love-relationship': 'HeartHandshake',
  'parenting': 'Baby', 'social-life': 'Globe',
  'financial-life': 'TrendingUp', 'career': 'Briefcase',
  'quality-of-life': 'Coffee', 'life-vision': 'Compass', 'passion-sex': 'Flame',
}

const DEFAULT_GROUPS = [
  { name: 'Life',          ids: ['life-vision', 'passion-sex'],                                           color: '#10b981', icon: 'Compass' },
  { name: 'Body',          ids: ['health-fitness', 'quality-of-life'],                                    color: '#f97316', icon: 'Activity' },
  { name: 'Mind',          ids: ['intellectual-life', 'emotional-life', 'character', 'spiritual-life'],   color: '#a855f7', icon: 'Brain' },
  { name: 'Work & Money',  ids: ['career', 'financial-life'],                                             color: '#3b82f6', icon: 'Briefcase' },
  { name: 'Relationships', ids: ['love-relationship', 'parenting', 'social-life'],                        color: '#ec4899', icon: 'HeartHandshake' },
]

const COLOR_PALETTE = [
  '#f97316','#ef4444','#ec4899','#a855f7',
  '#6366f1','#3b82f6','#06b6d4','#10b981',
  '#84cc16','#eab308','#f59e0b','#64748b',
]

function loadGroupConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem('kai-harmony-groups') || '{}')
    return DEFAULT_GROUPS.map(g => ({ ...g, ...saved[g.name] }))
  } catch { return DEFAULT_GROUPS }
}
function saveGroupConfig(groups) {
  const patch = {}
  groups.forEach(g => { patch[g.name] = { color: g.color, icon: g.icon } })
  localStorage.setItem('kai-harmony-groups', JSON.stringify(patch))
}

function GroupEditor({ group, onSave, onClose }) {
  const [color, setColor] = useState(group.color)
  const [icon,  setIcon]  = useState(group.icon)
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{group.name}</span>
        <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 16 }}>✕</button>
      </div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Color</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {COLOR_PALETTE.map(c => (
            <button key={c} onClick={() => setColor(c)} style={{ all: 'unset', cursor: 'pointer', width: 22, height: 22, borderRadius: '50%', background: c, outline: color === c ? `2px solid ${c}` : 'none', outlineOffset: 2, transform: color === c ? 'scale(1.2)' : 'scale(1)', transition: 'transform 0.1s' }} />
          ))}
        </div>
      </div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Icon</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: 4 }}>
          {ICON_SET.map(({ name, C }) => (
            <button key={name} onClick={() => setIcon(name)} title={name} style={{ all: 'unset', cursor: 'pointer', width: 32, height: 32, borderRadius: 7, display: 'flex', alignItems: 'center', justifyContent: 'center', background: icon === name ? color + '30' : 'var(--bg-surface)', border: `1px solid ${icon === name ? color : 'transparent'}`, color: icon === name ? color : 'var(--text-muted)', transition: 'all 0.15s' }}>
              <C size={15} strokeWidth={1.75} />
            </button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button onClick={onClose} style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)', padding: '6px 14px', borderRadius: 7, border: '1px solid var(--border)' }}>Cancel</button>
        <button onClick={() => onSave({ color, icon })} style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, color: '#fff', padding: '6px 14px', borderRadius: 7, background: color }}>Save</button>
      </div>
    </div>
  )
}

const ASPECT_LABELS = { premise: 'Premise', vision: 'Vision', purpose: 'Purpose', strategy: 'Strategy' }

function domainStatus(aspects) {
  const vals = Object.values(aspects || {}).map(a => a.status || 'green')
  if (vals.includes('red'))    return 'red'
  if (vals.includes('yellow')) return 'yellow'
  return 'green'
}

function EditableStatement({ value, onSave }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(value)
  const ref = useRef()
  useEffect(() => { if (editing) ref.current?.focus() }, [editing])
  function commit() {
    setEditing(false)
    if (val.trim() !== value) onSave(val.trim())
  }
  return editing ? (
    <textarea ref={ref} value={val} onChange={e => setVal(e.target.value)}
      onBlur={commit} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit() } if (e.key === 'Escape') { setVal(value); setEditing(false) } }}
      style={{ width: '100%', fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, background: 'var(--bg-elevated)', border: '1px solid var(--accent)', borderRadius: 6, padding: '4px 8px', fontFamily: 'inherit', resize: 'none', outline: 'none', boxSizing: 'border-box' }}
      rows={2}
    />
  ) : (
    <p onClick={() => setEditing(true)} title="Click to edit" style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0, cursor: 'text', borderRadius: 4, padding: '2px 4px', transition: 'background 0.15s' }}
      onMouseEnter={e => e.currentTarget.style.background = 'var(--hover-bg)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
    >{val}</p>
  )
}

function AspectRow({ aspect, data, domainId, onStatusChange, onStatementsChange }) {
  function saveStatement(idx, newVal) {
    const updated = [...data.statements]
    updated[idx] = newVal
    onStatementsChange(aspect, updated)
  }
  return (
    <div style={{ padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '0 0 8px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {ASPECT_LABELS[aspect]}
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {data.statements.map((s, i) => (
              <EditableStatement key={i} value={s} onSave={v => saveStatement(i, v)} />
            ))}
          </div>
        </div>
        <div style={{ flexShrink: 0, paddingTop: 2 }}>
          <StatusToggle status={data.status} onChange={(s) => onStatusChange(aspect, s)} />
        </div>
      </div>
    </div>
  )
}

function reviewAgo(dateStr) {
  if (!dateStr) return null
  const days = Math.floor((Date.now() - new Date(dateStr).getTime()) / 86400000)
  if (days === 0) return 'Reviewed today'
  if (days === 1) return 'Reviewed yesterday'
  if (days < 30)  return `Reviewed ${days}d ago`
  if (days < 365) return `Reviewed ${Math.floor(days / 30)}mo ago`
  return `Reviewed ${Math.floor(days / 365)}y ago`
}

function DomainCard({ domain, groupColor, onStatusChange, onStatementsChange, onMarkReviewed }) {
  const [expanded, setExpanded] = useState(false)
  const overall = domainStatus(domain.aspects)
  const STATUS_COLOR = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }
  const iconName = DOMAIN_ICONS[domain.id] || 'Star'
  const reviewLabel = reviewAgo(domain.lastReviewed)
  const reviewDays = domain.lastReviewed
    ? Math.floor((Date.now() - new Date(domain.lastReviewed).getTime()) / 86400000)
    : 999
  const needsReview = reviewDays >= 90

  return (
    <div style={{ background: 'var(--bg-card)', borderRadius: 12, border: `1px solid ${needsReview && expanded ? '#f59e0b44' : 'var(--border)'}`, overflow: 'hidden' }}>
      <button onClick={() => setExpanded(e => !e)} style={{ width: '100%', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', transition: 'background 0.15s', fontFamily: 'inherit' }}
        onMouseEnter={e => e.currentTarget.style.background = 'var(--hover-bg)'}
        onMouseLeave={e => e.currentTarget.style.background = 'none'}
      >
        <div style={{ width: 32, height: 32, borderRadius: 8, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: groupColor + '18', color: groupColor }}>
          <LucideIcon name={iconName} size={16} color={groupColor} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 4px' }}>{domain.name}</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div style={{ display: 'flex', gap: 3 }}>
              {Object.values(domain.aspects).map((a, i) => (
                <StatusDot key={i} status={a.status} size={6} />
              ))}
            </div>
            {reviewLabel && (
              <span style={{ fontSize: 9, color: needsReview ? '#f59e0b' : 'var(--text-subtle)', letterSpacing: '0.04em' }}>
                {needsReview ? '⚠ ' : ''}{reviewLabel}
              </span>
            )}
            {!reviewLabel && (
              <span style={{ fontSize: 9, color: '#f59e0b' }}>⚠ Never reviewed</span>
            )}
          </div>
        </div>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: STATUS_COLOR[overall], background: STATUS_COLOR[overall] + '18', padding: '3px 8px', borderRadius: 6, flexShrink: 0 }}>
          {overall}
        </div>
        <svg width="14" height="14" fill="none" stroke="var(--text-muted)" viewBox="0 0 24 24" style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.2s', flexShrink: 0 }}>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {expanded && (
        <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border)' }}>
          {Object.entries(domain.aspects).map(([aspect, data]) => (
            <AspectRow key={aspect} aspect={aspect} data={data} domainId={domain.id}
              onStatusChange={onStatusChange}
              onStatementsChange={onStatementsChange}
            />
          ))}
          <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 12 }}>
            <button onClick={() => onMarkReviewed(domain.id)} style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600, color: '#10b981', padding: '6px 12px', borderRadius: 8, border: '1px solid #10b98133', background: '#10b98108', transition: 'all 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.background = '#10b98118'; e.currentTarget.style.borderColor = '#10b98166' }}
              onMouseLeave={e => { e.currentTarget.style.background = '#10b98108'; e.currentTarget.style.borderColor = '#10b98133' }}
            >
              <CheckCircle size={13} /> Mark Reviewed
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Harmony() {
  const [domains,  setDomains]  = useState(HARMONY_DOMAINS)
  const [saving,   setSaving]   = useState(false)
  const [groups,   setGroups]   = useState(loadGroupConfig)
  const [editing,  setEditing]  = useState(null)

  useEffect(() => {
    api.getHarmony()
      .then(data => { if (data?.domains) setDomains(data.domains) })
      .catch(() => {})
  }, [])

  // Overall metric
  const allDomainStatuses = domains.map(d => domainStatus(d.aspects))
  const alignedCount  = allDomainStatuses.filter(s => s === 'green').length
  const attentionCount = allDomainStatuses.filter(s => s === 'red').length
  const totalDomains  = domains.length

  function handleStatusChange(domainId) {
    return async (aspect, status) => {
      setDomains(prev => prev.map(d =>
        d.id === domainId
          ? { ...d, aspects: { ...d.aspects, [aspect]: { ...d.aspects[aspect], status } } }
          : d
      ))
      setSaving(true)
      try { await api.updateAspectStatus(domainId, aspect, status) }
      catch {} finally { setSaving(false) }
    }
  }

  function handleStatementsChange(domainId) {
    return async (aspect, statements) => {
      setDomains(prev => prev.map(d =>
        d.id === domainId
          ? { ...d, aspects: { ...d.aspects, [aspect]: { ...d.aspects[aspect], statements } } }
          : d
      ))
      setSaving(true)
      try {
        await fetch(`/api/harmony/${domainId}/aspect/${aspect}/statements`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ statements }),
        })
      } catch {} finally { setSaving(false) }
    }
  }

  async function handleMarkReviewed(domainId) {
    const today = new Date().toISOString().slice(0, 10)
    setDomains(prev => prev.map(d => d.id === domainId ? { ...d, lastReviewed: today } : d))
    try {
      await fetch(`/api/harmony/${domainId}/review`, { method: 'POST' })
    } catch {}
  }

  function saveGroup(name, patch) {
    const updated = groups.map(g => g.name === name ? { ...g, ...patch } : g)
    setGroups(updated)
    saveGroupConfig(updated)
    setEditing(null)
  }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '28px 32px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 300, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 }}>
            Harmony <span style={{ fontWeight: 600 }}>— Your Life Map</span>
          </h1>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '4px 0 0' }}>13 domains · 4 aspects each · click any statement to edit</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 4 }}>
          {saving && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Saving…</span>}
          {/* Overall score */}
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ textAlign: 'center', padding: '6px 14px', borderRadius: 10, background: '#10b98112', border: '1px solid #10b98133' }}>
              <div style={{ fontSize: 20, fontWeight: 300, color: '#10b981', lineHeight: 1 }}>{alignedCount}</div>
              <div style={{ fontSize: 9, fontWeight: 600, color: '#10b98188', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>Aligned</div>
            </div>
            {attentionCount > 0 && (
              <div style={{ textAlign: 'center', padding: '6px 14px', borderRadius: 10, background: '#ef444412', border: '1px solid #ef444433' }}>
                <div style={{ fontSize: 20, fontWeight: 300, color: '#ef4444', lineHeight: 1 }}>{attentionCount}</div>
                <div style={{ fontSize: 9, fontWeight: 600, color: '#ef444488', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>Need Attn</div>
              </div>
            )}
            <div style={{ textAlign: 'center', padding: '6px 14px', borderRadius: 10, background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 20, fontWeight: 300, color: 'var(--text-primary)', lineHeight: 1 }}>{Math.round(alignedCount / totalDomains * 100)}%</div>
              <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>Overall</div>
            </div>
          </div>
        </div>
      </div>

      {/* Two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 48px', alignItems: 'start' }}>
        {[groups.slice(0, 3), groups.slice(3)].map((col, ci) => (
          <div key={ci} style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
            {col.map(g => {
              const groupDomains = g.ids.map(id => domains.find(d => d.id === id)).filter(Boolean)
              const stats  = groupDomains.map(d => domainStatus(d.aspects))
              const green  = stats.filter(s => s === 'green').length
              const pct    = stats.length ? Math.round((green / stats.length) * 100) : 0
              const isEdit = editing === g.name
              return (
                <div key={g.name}>
                  <div style={{ position: 'relative', height: 22, borderRadius: 6, overflow: 'hidden', background: g.color + '18', marginBottom: 10, cursor: 'default' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: `${pct}%`, background: `linear-gradient(to right, ${g.color}, ${g.color}cc)`, borderRadius: 8, transition: 'width 0.9s cubic-bezier(.4,0,.2,1)' }} />
                    <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', alignItems: 'center', padding: '0 12px', gap: 8, pointerEvents: 'none' }}>
                      <LucideIcon name={g.icon} size={12} color={pct > 30 ? 'rgba(255,255,255,0.9)' : g.color} />
                      <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', flex: 1, color: pct > 30 ? 'rgba(255,255,255,0.92)' : g.color }}>{g.name}</span>
                      <span style={{ fontSize: 9, fontWeight: 600, color: pct > 85 ? 'rgba(255,255,255,0.7)' : g.color + '99' }}>{green}/{stats.length} · {pct}%</span>
                    </div>
                    <button onClick={() => setEditing(isEdit ? null : g.name)} style={{ position: 'absolute', right: 44, top: '50%', transform: 'translateY(-50%)', all: 'unset', cursor: 'pointer', zIndex: 2, width: 24, height: 24, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pct > 30 ? 'rgba(255,255,255,0.7)' : g.color + '88', transition: 'all 0.15s' }}
                      onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.2)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                    </button>
                  </div>
                  {isEdit && <GroupEditor group={g} onSave={patch => saveGroup(g.name, patch)} onClose={() => setEditing(null)} />}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {groupDomains.map(domain => (
                      <DomainCard key={domain.id} domain={domain} groupColor={g.color}
                        onStatusChange={handleStatusChange(domain.id)}
                        onStatementsChange={handleStatementsChange(domain.id)}
                        onMarkReviewed={handleMarkReviewed}
                      />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
