// KAI-1319 Slice 2 — the action-first home, as a command/control center.
// Phone: single focused column. Desktop: a status strip up top + a multi-panel
// grid that uses the width (needs-you spans full width; day + proactive sit side
// by side). Every section is fail-soft (a dead fetch renders nothing, never an
// error wall) and self-hides when empty. Composes only proven live endpoints.
import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Lock, Calendar, ListTodo, ChevronRight, ShieldCheck, Sparkles,
} from 'lucide-react'
import { api } from '../lib/api'
import ProactiveDigest from '../components/ProactiveDigest'

const BLUE = '#3882F6'
const GREEN = '#10b981'
const AMBER = '#f59e0b'
const RED = '#ef4444'

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}
function longDate() {
  return new Date().toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })
}
function ageOf(iso) {
  if (!iso) return ''
  try {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.round(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.round(hrs / 24)}d ago`
  } catch { return '' }
}
function eventWhen(startISO) {
  if (!startISO) return ''
  const d = new Date(startISO)
  const allDay = startISO.length <= 10
  const sameDay = d.toDateString() === new Date().toDateString()
  const dayLabel = sameDay ? 'Today' : d.toLocaleDateString([], { weekday: 'short' })
  if (allDay) return `${dayLabel} · all day`
  return `${dayLabel} · ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

// A titled panel — the command-center building block.
function Panel({ title, count, countColor, accent, children }) {
  return (
    <section style={{
      background: 'var(--bg-card)', border: `1px solid ${accent || 'var(--border)'}`,
      borderRadius: 14, overflow: 'hidden', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.13em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>{title}</span>
        {count != null && count > 0 && (
          <span style={{ fontSize: 10, fontWeight: 700, color: '#fff', background: countColor || 'var(--accent)', borderRadius: 20, padding: '1px 8px', fontVariantNumeric: 'tabular-nums' }}>{count}</span>
        )}
      </div>
      <div style={{ padding: 14, flex: 1 }}>{children}</div>
    </section>
  )
}

// ── header status strip: greeting + live pulse chips ─────────────────────────
function PulseCluster() {
  const [h, setH] = useState(null)
  const nav = useNavigate()
  useEffect(() => { api.getSystemHealth().then(setH).catch(() => setH(null)) }, [])
  if (!h) return null
  const alerts = (h.alerts || []).length
  const cells = [
    { k: 'Baseline', v: h.ok ? 'GREEN' : 'WARN', c: h.ok ? GREEN : AMBER },
    { k: 'Warnings', v: alerts, c: alerts ? AMBER : 'var(--text-secondary)' },
    { k: 'Disk', v: h.disk_pct != null ? `${Math.round(h.disk_pct)}%` : '—', c: h.disk_pct >= 90 ? RED : 'var(--text-secondary)' },
    { k: 'Mem', v: h.mem_pct != null ? `${Math.round(h.mem_pct)}%` : '—', c: h.mem_pct >= 90 ? RED : 'var(--text-secondary)' },
  ]
  return (
    <button onClick={() => nav('/system')} title="Open System" style={{
      all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'stretch', gap: 1,
      background: 'var(--border)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden',
    }}>
      {cells.map((c) => (
        <div key={c.k} style={{ background: 'var(--bg-card)', padding: '8px 14px', minWidth: 66 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>{c.k}</div>
          <div style={{ fontSize: 15, fontWeight: 700, marginTop: 2, color: c.c, fontVariantNumeric: 'tabular-nums' }}>{c.v}</div>
        </div>
      ))}
      <div style={{ background: 'var(--bg-card)', display: 'flex', alignItems: 'center', padding: '0 12px', color: 'var(--text-tertiary)' }}>
        <ShieldCheck size={14} /><ChevronRight size={13} />
      </div>
    </button>
  )
}

// ── Needs you — mode-lock unlock requests awaiting Leo ───────────────────────
function NeedsYou() {
  const [pending, setPending] = useState([])
  const load = useCallback(() => {
    api.get('/mode_lock/pending').then((d) => setPending(d.pending || [])).catch(() => setPending([]))
  }, [])
  useEffect(() => {
    load()
    const iv = setInterval(load, 20000)
    return () => clearInterval(iv)
  }, [load])
  if (pending.length === 0) return null
  return (
    <Panel title="Needs you" count={pending.length} countColor="var(--accent)" accent="var(--hover-border)">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
        {pending.map((p) => (
          <div key={p.request_id} style={{ padding: '11px 13px', borderRadius: 11, background: 'var(--accent-bg)', border: '1px solid var(--hover-border)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <Lock size={15} color="var(--accent)" strokeWidth={2} style={{ flexShrink: 0 }} />
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                Unlock — {p.tool || 'action'}
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-tertiary)', flexShrink: 0 }}>{ageOf(p.created_at)}</span>
            </div>
            {p.reason && <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', margin: '6px 0 0', lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{p.reason}</div>}
            <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 7 }}>Approve on Buzz or type <b style={{ color: 'var(--text-secondary)' }}>YES</b> at the keyboard.</div>
          </div>
        ))}
      </div>
    </Panel>
  )
}

// ── Your day — calendar + today's focus tasks ────────────────────────────────
function YourDay() {
  const [events, setEvents] = useState(null)
  const [focus, setFocus] = useState(null)
  useEffect(() => {
    api.get('/calendar/events').then((d) => setEvents(d.events || [])).catch(() => setEvents([]))
    api.getFocusBrief().then((d) => setFocus((d.top3 || []).concat(d.next5 || []))).catch(() => setFocus([]))
  }, [])
  const evs = (events || []).slice(0, 5)
  const tasks = (focus || []).slice(0, 4)
  const today = new Date().toISOString().slice(0, 10)
  const empty = evs.length === 0 && tasks.length === 0
  return (
    <Panel title="Your day">
      {empty ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Nothing on the calendar or the focus list. Clear runway.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {evs.map((e, i) => (
            <div key={e.id || i} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '9px 4px' }}>
              <Calendar size={15} color={BLUE} strokeWidth={1.9} style={{ flexShrink: 0 }} />
              <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.title || '(no title)'}</span>
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)', flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{eventWhen(e.start)}</span>
            </div>
          ))}
          {tasks.map((t, i) => (
            <div key={t.id || `t${i}`} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '9px 4px' }}>
              <ListTodo size={15} color="var(--accent)" strokeWidth={1.9} style={{ flexShrink: 0 }} />
              <span style={{ fontSize: 13, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.content}</span>
              {t.due && <span style={{ fontSize: 11, color: t.due <= today ? AMBER : 'var(--text-tertiary)', flexShrink: 0 }}>{t.due <= today ? 'due' : t.due.slice(5)}</span>}
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}

// ProactiveDigest brings its own card chrome; wrap it in a matching panel only when
// it has content (it self-hides when empty, so an empty panel never shows).
function ProactivePanel() {
  return <ProactiveDigest />
}

export default function Now() {
  return (
    <div style={{ maxWidth: 1180, margin: '0 auto', padding: '24px 20px 44px' }}>
      {/* status strip */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between" style={{ gap: 14, marginBottom: 22 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 680, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>{greeting()}, Leo</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 3, fontFamily: 'var(--mono, ui-monospace)' }}>{longDate()}</div>
        </div>
        <PulseCluster />
      </div>

      {/* needs-you spans full width when present */}
      <div style={{ marginBottom: 16 }}>
        <NeedsYou />
      </div>

      {/* working area — two panels side by side on desktop, stacked on phone */}
      <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 16, alignItems: 'start' }}>
        <YourDay />
        <ProactivePanel />
      </div>
    </div>
  )
}
