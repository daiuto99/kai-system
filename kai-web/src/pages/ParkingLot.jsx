import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import {
  Inbox, Send, RefreshCw, Sparkles,
  FileText, Lightbulb, ShoppingBag, Video, Link2, Music, BookOpen,
  Archive, Trash2, ExternalLink, ChevronRight, X, ClipboardList,
} from 'lucide-react'

const TYPE_META = {
  article:  { label: 'Article',  color: '#f97316', icon: FileText },
  idea:     { label: 'Idea',     color: '#a855f7', icon: Lightbulb },
  product:  { label: 'Product',  color: '#3b82f6', icon: ShoppingBag },
  note:     { label: 'Note',     color: '#94a3b8', icon: FileText },
  link:     { label: 'Link',     color: '#06b6d4', icon: Link2 },
  music:    { label: 'Music',    color: '#ec4899', icon: Music },
  video:    { label: 'Video',    color: '#ef4444', icon: Video },
  item:     { label: 'Item',     color: '#64748b', icon: BookOpen },
}

const STATUS_META = {
  new:      { label: 'New',      color: '#6366f1', bg: '#6366f118' },
  active:   { label: 'Active',   color: '#10b981', bg: '#10b98118' },
  waiting:  { label: 'Waiting',  color: '#f59e0b', bg: '#f59e0b18' },
  archived: { label: 'Archived', color: '#6b7280', bg: '#6b728018' },
}

const INTENTS = ['Read', 'Research', 'Implement', 'Buy', 'Compare', 'Reference', 'Follow Up', 'Decision Needed']

const INTENT_COLOR = {
  'Read':             '#06b6d4',
  'Research':         '#8b5cf6',
  'Implement':        '#10b981',
  'Buy':              '#f59e0b',
  'Compare':          '#3b82f6',
  'Reference':        '#64748b',
  'Follow Up':        '#f97316',
  'Decision Needed':  '#ef4444',
}

function typeMeta(type) { return TYPE_META[type] || TYPE_META.item }
function statusMeta(status) { return STATUS_META[status] || STATUS_META.new }

function TypeBadge({ type }) {
  const m = typeMeta(type)
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em',
      color: m.color, background: m.color + '18', border: `1px solid ${m.color}35`,
      borderRadius: 4, padding: '2px 6px', flexShrink: 0,
    }}>{m.label}</span>
  )
}

function StatusPill({ status }) {
  const m = statusMeta(status)
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
      color: m.color, background: m.bg, border: `1px solid ${m.color}35`,
      borderRadius: 10, padding: '2px 7px', flexShrink: 0,
    }}>{m.label}</span>
  )
}

function IntentBadge({ intent }) {
  if (!intent) return null
  const color = INTENT_COLOR[intent] || '#64748b'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600,
      color, background: color + '15', border: `1px solid ${color}30`,
      borderRadius: 6, padding: '2px 8px', flexShrink: 0,
    }}>{intent}</span>
  )
}

// ── Card ─────────────────────────────────────────────────────────────────────

function LotCard({ item, onStatusChange, onDelete, onArchive, onAskKai, projects }) {
  const hasUrl = item.url?.startsWith('http')
  const m = typeMeta(item.type)
  const Icon = m.icon

  function actions() {
    const base = []
    if (hasUrl) base.push(
      <button key="open" onClick={() => window.open(item.url, '_blank', 'noreferrer')}
        style={btnStyle('#ffffff18', '#ffffff35', '#fff')}
      ><ExternalLink size={11} /> Open</button>
    )
    base.push(
      <button key="kai" onClick={() => onAskKai(item)}
        style={btnStyle('#6366f118', '#6366f140', '#a5b4fc')}
      ><Sparkles size={11} /> Ask KAI</button>
    )
    if (item.status !== 'active') base.push(
      <button key="active" onClick={() => onStatusChange(item.slug, 'active')}
        style={btnStyle('#10b98118', '#10b98140', '#6ee7b7')}
      >Set Active</button>
    )
    if (item.status !== 'waiting') base.push(
      <button key="later" onClick={() => onStatusChange(item.slug, 'waiting')}
        style={btnStyle('#f59e0b18', '#f59e0b40', '#fcd34d')}
      >Save for Later</button>
    )
    base.push(
      <button key="arch" onClick={() => onArchive(item.slug)}
        style={btnStyle('#ffffff10', '#ffffff25', '#9ca3af')}
      ><Archive size={11} /></button>
    )
    base.push(
      <button key="del" onClick={() => onDelete(item.slug)}
        style={btnStyle('#ef444418', '#ef444440', '#fca5a5')}
      ><Trash2 size={11} /></button>
    )
    return base
  }

  const source = item.source && item.source !== 'web' ? item.source : (
    item.url ? (() => { try { return new URL(item.url).hostname.replace('www.', '') } catch { return '' } })() : ''
  )

  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 12, padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 10,
      borderLeft: `3px solid ${m.color}`,
    }}>
      {/* Row 1: type + status */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <TypeBadge type={item.type} />
        <StatusPill status={item.status} />
      </div>

      {/* Row 2: title */}
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.4 }}>
        {item.title}
      </div>

      {/* Row 3: source + date */}
      {(source || item.date) && (
        <div style={{ fontSize: 11, color: 'var(--text-subtle)', display: 'flex', gap: 6 }}>
          {source && <span style={{ textTransform: 'capitalize' }}>{source}</span>}
          {source && item.date && <span>·</span>}
          {item.date && <span>{item.date}</span>}
        </div>
      )}

      {/* Row 4: why saved */}
      <div style={{ fontSize: 12, color: item.why_saved ? 'var(--text-secondary)' : 'var(--text-subtle)', lineHeight: 1.5, fontStyle: item.why_saved ? 'normal' : 'italic' }}>
        {item.why_saved || (item.summary || '—')}
      </div>

      {/* Row 5: intent + project */}
      {(item.intent || item.project) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {item.intent && <IntentBadge intent={item.intent} />}
          {item.project && (
            <span style={{ fontSize: 10, color: 'var(--text-subtle)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, padding: '2px 8px' }}>
              {item.project}
            </span>
          )}
        </div>
      )}

      {/* Row 6: actions */}
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 2 }}>
        {actions()}
      </div>
    </div>
  )
}

function btnStyle(bg, border, color) {
  return {
    all: 'unset', cursor: 'pointer', fontSize: 10, fontWeight: 600,
    padding: '4px 9px', borderRadius: 6,
    background: bg, border: `1px solid ${border}`, color,
    display: 'inline-flex', alignItems: 'center', gap: 4,
  }
}

// ── Column ────────────────────────────────────────────────────────────────────

function Column({ label, count, accent, items, onStatusChange, onDelete, onArchive, onAskKai, projects }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
        paddingBottom: 10, borderBottom: '1px solid var(--border)',
      }}>
        <div style={{ width: 3, height: 14, borderRadius: 2, background: accent, flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: accent }}>{label}</span>
        <span style={{ fontSize: 11, color: 'var(--text-subtle)', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 10, padding: '1px 8px' }}>{count}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {items.map(item => (
          <LotCard key={item.slug} item={item}
            onStatusChange={onStatusChange} onDelete={onDelete}
            onArchive={onArchive} onAskKai={onAskKai} projects={projects} />
        ))}
        {items.length === 0 && (
          <div style={{ padding: '24px 0', textAlign: 'center', fontSize: 12, color: 'var(--text-subtle)', fontStyle: 'italic' }}>Empty</div>
        )}
      </div>
    </div>
  )
}

// ── Review Mode ───────────────────────────────────────────────────────────────

function ReviewMode({ items, onSave, onSkip, onClose }) {
  const [index, setIndex]     = useState(0)
  const [whySaved, setWhySaved] = useState('')
  const [intent, setIntent]   = useState('')
  const [project, setProject] = useState('')

  const item = items[index]

  useEffect(() => {
    if (item) {
      setWhySaved(item.why_saved || '')
      setIntent(item.intent || '')
      setProject(item.project || '')
    }
  }, [index, item])

  if (!item) return (
    <Overlay onClose={onClose}>
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <div style={{ fontSize: 32, marginBottom: 12 }}>✓</div>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>All caught up</div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 24 }}>No new items left to review.</div>
        <button onClick={onClose} style={primaryBtn}>Close</button>
      </div>
    </Overlay>
  )

  function saveAndNext(action) {
    onSave(item.slug, { why_saved: whySaved, intent, project, status: action || 'active' })
    if (index < items.length - 1) setIndex(i => i + 1)
    else onClose()
  }

  return (
    <Overlay onClose={onClose}>
      {/* Progress */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <span style={{ fontSize: 12, color: 'var(--text-subtle)' }}>Reviewing new items</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 600 }}>{index + 1} / {items.length}</span>
      </div>
      <div style={{ height: 3, background: 'var(--border)', borderRadius: 2, marginBottom: 24 }}>
        <div style={{ height: '100%', background: '#6366f1', borderRadius: 2, width: `${((index + 1) / items.length) * 100}%`, transition: 'width 0.3s' }} />
      </div>

      {/* Item */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <TypeBadge type={item.type} />
          {item.date && <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{item.date}</span>}
        </div>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.4, marginBottom: 6 }}>{item.title}</div>
        {item.summary && <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{item.summary}</div>}
      </div>

      {/* Why saved */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 6 }}>Why did you save this?</label>
        <textarea
          value={whySaved}
          onChange={e => setWhySaved(e.target.value)}
          placeholder="Describe your reason for saving this item..."
          rows={2}
          style={{
            width: '100%', boxSizing: 'border-box', padding: '8px 10px',
            borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-base)', color: 'var(--text-primary)',
            fontSize: 12, fontFamily: 'inherit', resize: 'none', outline: 'none',
          }}
        />
      </div>

      {/* Intent */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 8 }}>What should happen next?</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {INTENTS.map(i => {
            const color = INTENT_COLOR[i] || '#64748b'
            const active = intent === i
            return (
              <button key={i} onClick={() => setIntent(active ? '' : i)} style={{
                all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                padding: '5px 12px', borderRadius: 20,
                background: active ? color + '25' : 'var(--bg-elevated)',
                border: `1px solid ${active ? color + '60' : 'var(--border)'}`,
                color: active ? color : 'var(--text-secondary)',
                transition: 'all 0.15s',
              }}>{i}</button>
            )
          })}
        </div>
      </div>

      {/* Project */}
      <div style={{ marginBottom: 24 }}>
        <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 6 }}>Route to project (optional)</label>
        <input
          value={project}
          onChange={e => setProject(e.target.value)}
          placeholder="e.g. KAI, Encore, Studio 71..."
          style={{
            width: '100%', boxSizing: 'border-box', padding: '8px 10px',
            borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-base)', color: 'var(--text-primary)',
            fontSize: 12, fontFamily: 'inherit', outline: 'none',
          }}
        />
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
        <button onClick={() => onSkip(item.slug) || (index < items.length - 1 ? setIndex(i => i + 1) : onClose())}
          style={{ all: 'unset', cursor: 'pointer', fontSize: 12, color: 'var(--text-subtle)', padding: '8px 12px' }}>
          Skip
        </button>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => saveAndNext('waiting')}
            style={{ all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: '8px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>
            Save for Later
          </button>
          <button onClick={() => saveAndNext('active')} style={primaryBtn}>
            Save &amp; Next <ChevronRight size={13} />
          </button>
        </div>
      </div>
    </Overlay>
  )
}

function Overlay({ children, onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100, padding: 24,
    }} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 16, padding: 28, width: '100%', maxWidth: 560,
        maxHeight: '90vh', overflowY: 'auto', position: 'relative',
      }}>
        <button onClick={onClose} style={{
          all: 'unset', cursor: 'pointer', position: 'absolute', top: 16, right: 16,
          color: 'var(--text-subtle)', padding: 4, borderRadius: 6,
        }}><X size={16} /></button>
        {children}
      </div>
    </div>
  )
}

const primaryBtn = {
  all: 'unset', cursor: 'pointer', fontSize: 12, fontWeight: 600,
  padding: '8px 16px', borderRadius: 8,
  background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)',
  color: '#fff', display: 'inline-flex', alignItems: 'center', gap: 6,
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ParkingLot() {
  const [items,        setItems]        = useState([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState(null)
  const [captureText,  setCaptureText]  = useState('')
  const [capturing,    setCapturing]    = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [typeFilter,   setTypeFilter]   = useState('all')
  const [intentFilter, setIntentFilter] = useState('all')
  const [reviewMode,   setReviewMode]   = useState(false)
  const navigate = useNavigate()

  async function load() {
    setLoading(true); setError(null)
    try {
      const data = await api.getParkingLot()
      setItems(data.items || [])
    } catch { setError('Could not load Lot Inventory.') }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  async function handleCapture(e) {
    e.preventDefault()
    if (!captureText.trim()) return
    setCapturing(true)
    try { await api.quickCapture(captureText.trim()); setCaptureText(''); setTimeout(load, 1200) }
    catch {} finally { setCapturing(false) }
  }

  async function handleStatusChange(slug, status) {
    try {
      await api.patchCapture(slug, { status })
      setItems(prev => prev.map(i => i.slug === slug ? { ...i, status } : i))
    } catch {}
  }

  async function handleArchive(slug) {
    try { await api.archiveCapture(slug); setItems(prev => prev.filter(i => i.slug !== slug)) } catch {}
  }

  async function handleDelete(slug) {
    try { await api.deleteCapture(slug); setItems(prev => prev.filter(i => i.slug !== slug)) } catch {}
  }

  async function handleReviewSave(slug, fields) {
    try {
      await api.patchCapture(slug, fields)
      setItems(prev => prev.map(i => i.slug === slug ? { ...i, ...fields } : i))
    } catch {}
  }

  function handleAskKai(item) {
    const msg = `Tell me about this: ${item.title}${item.summary ? ' — ' + item.summary : ''}${item.url ? ' ' + item.url : ''}`
    sessionStorage.setItem('kai:prefill', msg)
    navigate('/chat')
  }

  // Derive counts
  const newItems      = items.filter(i => i.status === 'new')
  const activeItems   = items.filter(i => i.status === 'active')
  const waitingItems  = items.filter(i => i.status === 'waiting')

  // Filters
  const allTypes   = [...new Set(items.map(i => i.type))].sort()
  const allIntents = [...new Set(items.map(i => i.intent).filter(Boolean))].sort()

  let visible = items
  if (statusFilter !== 'all') visible = visible.filter(i => i.status === statusFilter)
  if (typeFilter !== 'all')   visible = visible.filter(i => i.type === typeFilter)
  if (intentFilter !== 'all') visible = visible.filter(i => i.intent === intentFilter)
  visible = [...visible].sort((a, b) => (b.date || '').localeCompare(a.date || ''))

  const isFiltered = statusFilter !== 'all' || typeFilter !== 'all' || intentFilter !== 'all'

  // 3-column split (only when unfiltered)
  const colNew     = visible.filter(i => i.status === 'new')
  const colActive  = visible.filter(i => i.status === 'active')
  const colWaiting = visible.filter(i => i.status === 'waiting')

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: '24px 24px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 10, letterSpacing: '-0.02em' }}>
            <ClipboardList size={18} color="var(--text-subtle)" strokeWidth={1.75} />
            Lot Inventory
          </h1>
          {!loading && (
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-subtle)' }}>
              {items.length} captured · {newItems.length} new · {activeItems.length} active · {waitingItems.length} waiting
            </p>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {newItems.length > 0 && (
            <button onClick={() => setReviewMode(true)} style={{
              all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
              padding: '6px 12px', borderRadius: 8,
              background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)',
              color: '#fff', display: 'flex', alignItems: 'center', gap: 6,
            }}>
              Review New Items <span style={{ background: 'rgba(255,255,255,0.25)', borderRadius: 10, padding: '1px 6px', fontSize: 10 }}>{newItems.length}</span>
            </button>
          )}
          <button onClick={load} style={{
            all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
            padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--bg-card)', color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', gap: 5,
          }}>
            <RefreshCw size={11} /> Refresh
          </button>
        </div>
      </div>

      {/* Capture bar */}
      <form onSubmit={handleCapture} style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            type="text" value={captureText} onChange={e => setCaptureText(e.target.value)}
            placeholder="Capture a link, idea, product, article, task, video, or note..."
            style={{
              flex: 1, padding: '10px 16px', borderRadius: 12,
              border: '1px solid var(--border)', background: 'var(--bg-card)',
              color: 'var(--text-primary)', fontSize: 13, fontFamily: 'inherit', outline: 'none',
            }}
            onFocus={e => e.target.style.borderColor = 'rgba(99,102,241,0.5)'}
            onBlur={e => e.target.style.borderColor = 'var(--border)'}
          />
          <button type="submit" disabled={capturing || !captureText.trim()} style={{
            padding: '10px 18px', borderRadius: 12,
            background: capturing || !captureText.trim() ? 'var(--border)' : '#6366f1',
            color: '#fff', fontSize: 13, fontWeight: 600, border: 'none',
            cursor: capturing || !captureText.trim() ? 'default' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0,
          }}>
            <Send size={13} />{capturing ? 'Saving…' : 'Capture'}
          </button>
        </div>
      </form>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 24, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Status */}
        <div style={{ display: 'flex', gap: 4 }}>
          {['all', 'new', 'active', 'waiting'].map(s => {
            const label = s === 'all' ? 'All' : statusMeta(s).label
            const active = statusFilter === s
            const color = s === 'all' ? '#6b7280' : statusMeta(s).color
            return (
              <button key={s} onClick={() => setStatusFilter(s)} style={{
                all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                padding: '4px 12px', borderRadius: 20,
                background: active ? color + '20' : 'transparent',
                border: `1px solid ${active ? color + '50' : 'var(--border)'}`,
                color: active ? color : 'var(--text-subtle)',
              }}>{label}</button>
            )
          })}
        </div>

        {/* Type */}
        {allTypes.length > 1 && (
          <div style={{ display: 'flex', gap: 4, paddingLeft: 8, borderLeft: '1px solid var(--border)' }}>
            <button onClick={() => setTypeFilter('all')} style={{
              all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
              padding: '4px 10px', borderRadius: 20,
              background: typeFilter === 'all' ? 'var(--bg-elevated)' : 'transparent',
              border: `1px solid ${typeFilter === 'all' ? 'var(--border)' : 'transparent'}`,
              color: 'var(--text-subtle)',
            }}>Type</button>
            {allTypes.map(t => {
              const m = typeMeta(t)
              const active = typeFilter === t
              return (
                <button key={t} onClick={() => setTypeFilter(active ? 'all' : t)} style={{
                  all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                  padding: '4px 10px', borderRadius: 20,
                  background: active ? m.color + '20' : 'transparent',
                  border: `1px solid ${active ? m.color + '40' : 'transparent'}`,
                  color: active ? m.color : 'var(--text-subtle)',
                }}>{m.label}</button>
              )
            })}
          </div>
        )}

        {/* Intent */}
        {allIntents.length > 0 && (
          <div style={{ display: 'flex', gap: 4, paddingLeft: 8, borderLeft: '1px solid var(--border)' }}>
            {allIntents.map(i => {
              const color = INTENT_COLOR[i] || '#64748b'
              const active = intentFilter === i
              return (
                <button key={i} onClick={() => setIntentFilter(active ? 'all' : i)} style={{
                  all: 'unset', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                  padding: '4px 10px', borderRadius: 20,
                  background: active ? color + '20' : 'transparent',
                  border: `1px solid ${active ? color + '40' : 'transparent'}`,
                  color: active ? color : 'var(--text-subtle)',
                }}>{i}</button>
              )
            })}
          </div>
        )}

        {isFiltered && (
          <button onClick={() => { setStatusFilter('all'); setTypeFilter('all'); setIntentFilter('all') }} style={{
            all: 'unset', cursor: 'pointer', fontSize: 11, color: 'var(--text-subtle)',
            display: 'flex', alignItems: 'center', gap: 4,
          }}><X size={11} /> Clear filters</button>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--text-subtle)', fontSize: 13 }}>Loading…</div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '60px 0', color: '#ef4444', fontSize: 13 }}>{error}</div>
      ) : items.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '80px 0' }}>
          <Inbox size={40} color="var(--text-subtle)" strokeWidth={1} style={{ marginBottom: 12, opacity: 0.3 }} />
          <p style={{ fontSize: 14, color: 'var(--text-subtle)', margin: '0 0 6px', fontWeight: 600 }}>Nothing in the Lot yet.</p>
          <p style={{ fontSize: 12, color: 'var(--text-subtle)', margin: 0, maxWidth: 360, marginLeft: 'auto', marginRight: 'auto' }}>
            Capture anything worth revisiting: a link, idea, product, video, article, or quick note.
          </p>
        </div>
      ) : isFiltered ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
          {visible.map(item => (
            <LotCard key={item.slug} item={item}
              onStatusChange={handleStatusChange} onDelete={handleDelete}
              onArchive={handleArchive} onAskKai={handleAskKai} />
          ))}
          {visible.length === 0 && (
            <div style={{ gridColumn: '1/-1', textAlign: 'center', padding: '40px 0', color: 'var(--text-subtle)', fontSize: 13 }}>
              No items match the current filters.
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 20, alignItems: 'start' }}>
          <Column label="New / Untriaged" count={colNew.length}     accent="#6366f1" items={colNew}
            onStatusChange={handleStatusChange} onDelete={handleDelete} onArchive={handleArchive} onAskKai={handleAskKai} />
          <Column label="Active"          count={colActive.length}  accent="#10b981" items={colActive}
            onStatusChange={handleStatusChange} onDelete={handleDelete} onArchive={handleArchive} onAskKai={handleAskKai} />
          <Column label="Later / Reference" count={colWaiting.length} accent="#f59e0b" items={colWaiting}
            onStatusChange={handleStatusChange} onDelete={handleDelete} onArchive={handleArchive} onAskKai={handleAskKai} />
        </div>
      )}

      {/* Review Mode */}
      {reviewMode && (
        <ReviewMode
          items={newItems}
          onSave={handleReviewSave}
          onSkip={() => {}}
          onClose={() => { setReviewMode(false); load() }}
        />
      )}
    </div>
  )
}
