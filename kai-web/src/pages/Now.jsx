// KAI-1319 — the text-forward Now (v1.1). Leo's JARVIS life-assistant home:
// organize / plan / prioritize the day. Vercel-style: hairlines not boxes,
// mono labels, terra accent. NO system stats / health telemetry — the system
// is autonomous and escalates via Buzz (project_dashboard_is_life_assistant).
// Layout (responsive): greeting · Talk-to-KAI · Digest (full width, top) ·
// then Today (Schedule=today+tomorrow · Priorities) and Inbox side-by-side on
// desktop, stacked on phone. TRUST (Stage-2 exit — KAI-1314 sibling): a section
// distinguishes a DOWN feed (flagged, never shown as "clear") from a genuinely
// empty one. A dead fetch is surfaced, never fabricated as an empty day — a
// brief you cannot trust is worse than no brief. Never an error wall.
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import ProactiveDigest from '../components/ProactiveDigest'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}
function longDate() {
  return new Date().toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })
}
function localDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
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
function eventWhen(startISO, todayStr, tomStr) {
  if (!startISO) return ''
  const d = new Date(startISO)
  const allDay = startISO.length <= 10
  const ds = allDay ? startISO : localDateStr(d)
  const dayLabel = ds === todayStr ? 'Today' : ds === tomStr ? 'Tomorrow' : d.toLocaleDateString([], { weekday: 'short' })
  if (allDay) return `${dayLabel} · all day`
  return `${dayLabel} · ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

// ── a text-forward section: a mono label over a hairline, then rows ───────────
function Section({ label, children, style }) {
  return (
    <section style={style}>
      <div style={{
        fontSize: 11, fontWeight: 600, letterSpacing: '0.15em', textTransform: 'uppercase',
        color: 'var(--text-tertiary)', fontFamily: MONO,
        paddingBottom: 10, borderBottom: '1px solid var(--border)',
      }}>{label}</div>
      <div>{children}</div>
    </section>
  )
}

// a single hairline-separated row
function Row({ children, onClick }) {
  const [hover, setHover] = useState(false)
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: 'baseline', gap: 14, padding: '12px 8px',
        borderBottom: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default',
        background: hover && onClick ? 'var(--accent-bg)' : 'transparent',
        transition: 'background 120ms',
      }}
    >{children}</div>
  )
}

function SubLabel({ children }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-tertiary)', fontFamily: MONO, margin: '16px 8px 2px' }}>{children}</div>
  )
}

// ── FlaggedFeed — a DOWN feed is shown, never silently collapsed to "empty".
// This is the Stage-2 "trustworthy" property: Leo must be able to tell an
// actually-clear day from a feed that failed to answer.
function FlaggedFeed({ children }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, padding: '12px 8px',
      fontSize: 12.5, color: '#b45309', fontFamily: MONO,
      borderBottom: '1px solid var(--border)',
    }}>
      <AlertTriangle size={14} strokeWidth={2} style={{ flexShrink: 0 }} />
      <span>{children}</span>
    </div>
  )
}

// a genuinely-empty (but healthy) feed says so plainly — distinct from a flag
function Muted({ children }) {
  return <div style={{ padding: '12px 8px', fontSize: 13, color: 'var(--text-tertiary)' }}>{children}</div>
}

// ── Talk-to-KAI — 1:1 planning line; hands off to the live KAI conversation ───
function TalkToKai() {
  const [text, setText] = useState('')
  const [focus, setFocus] = useState(false)
  const nav = useNavigate()
  const send = () => {
    const t = text.trim()
    if (t) sessionStorage.setItem('kai:prefill', t)
    nav('/chat/kai')
  }
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, marginTop: 20,
      padding: '13px 16px', borderRadius: 12,
      background: 'var(--bg-card)',
      border: `1px solid ${focus ? 'var(--accent)' : 'var(--border)'}`,
      boxShadow: focus ? '0 0 0 3px var(--accent-bg)' : 'none',
      transition: 'border-color 120ms, box-shadow 120ms',
    }}>
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        onKeyDown={(e) => { if (e.key === 'Enter') send() }}
        placeholder="Talk to KAI — plan the day, think out loud, ask anything…"
        style={{ all: 'unset', flex: 1, fontSize: 14.5, color: 'var(--text-primary)', fontFamily: 'inherit', lineHeight: 1.4 }}
      />
      <button
        onClick={send}
        aria-label="Talk to KAI"
        style={{
          all: 'unset', cursor: 'pointer', flexShrink: 0, width: 30, height: 30,
          borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--accent)', color: '#fff',
        }}
      >
        <ArrowRight size={16} strokeWidth={2.2} />
      </button>
    </div>
  )
}

// ── Today — Schedule (today + tomorrow only) + Priorities (focus tasks) ───────
function Today() {
  const [events, setEvents] = useState(null)   // null = still loading
  const [focus, setFocus] = useState(null)
  const [evDown, setEvDown] = useState(false)   // fetch rejected → feed is DOWN, not empty
  const [focusDown, setFocusDown] = useState(false)
  useEffect(() => {
    api.get('/calendar/events').then((d) => setEvents(d.events || [])).catch(() => { setEvents([]); setEvDown(true) })
    api.getFocusBrief().then((d) => setFocus((d.top3 || []).concat(d.next5 || []))).catch(() => { setFocus([]); setFocusDown(true) })
  }, [])
  const now = new Date()
  const todayStr = localDateStr(now)
  const tomStr = localDateStr(new Date(now.getTime() + 86400000))
  const inWindow = (e) => {
    if (!e.start) return false
    const ds = e.start.length <= 10 ? e.start : localDateStr(new Date(e.start))
    return ds === todayStr || ds === tomStr
  }
  const evs = (events || []).filter(inWindow).slice(0, 6)
  const tasks = (focus || []).slice(0, 5)
  // still loading both feeds → render nothing yet (avoid a flash of "clear runway")
  if (events === null && focus === null) return null
  // "Clear runway" is only honest when BOTH feeds are UP and genuinely empty.
  const bothClearAndHealthy = !evDown && !focusDown && evs.length === 0 && tasks.length === 0
  if (bothClearAndHealthy) {
    return (
      <Section label="Today">
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-tertiary)' }}>
          Nothing today or tomorrow, and the priority list is clear. Clear runway.
        </div>
      </Section>
    )
  }
  return (
    <Section label="Today">
      <div>
        <SubLabel>Schedule</SubLabel>
        {evDown ? (
          <FlaggedFeed>Calendar feed didn’t respond — schedule unavailable, not empty.</FlaggedFeed>
        ) : evs.length > 0 ? (
          evs.map((e, i) => (
            <Row key={e.id || i}>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, minWidth: 132 }}>{eventWhen(e.start, todayStr, tomStr)}</span>
              <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.title || '(no title)'}</span>
            </Row>
          ))
        ) : (
          <Muted>Nothing on the calendar today or tomorrow.</Muted>
        )}
      </div>
      <div>
        <SubLabel>Priorities</SubLabel>
        {focusDown ? (
          <FlaggedFeed>Task feed didn’t respond — priorities unavailable, not empty.</FlaggedFeed>
        ) : tasks.length > 0 ? (
          tasks.map((t, i) => (
            <Row key={t.id || `t${i}`}>
              <span style={{ fontSize: 12, color: 'var(--accent)', fontFamily: MONO, flexShrink: 0, minWidth: 22, fontWeight: 600 }}>{String(i + 1).padStart(2, '0')}</span>
              <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.content}</span>
              {t.due && <span style={{ fontSize: 11.5, fontFamily: MONO, color: t.due <= todayStr ? 'var(--accent)' : 'var(--text-tertiary)', flexShrink: 0 }}>{t.due <= todayStr ? 'due' : t.due.slice(5)}</span>}
            </Row>
          ))
        ) : (
          <Muted>No priorities queued.</Muted>
        )}
      </div>
    </Section>
  )
}

// ── Inbox — deliverables KAI produced + surfaced items awaiting Leo ───────────
function Inbox() {
  const [items, setItems] = useState(null)
  const [down, setDown] = useState(false)
  useEffect(() => {
    api.get('/inbox/pending').then((d) => setItems(d.pending || [])).catch(() => { setItems([]); setDown(true) })
  }, [])
  // DOWN feed is flagged (never silently hidden); a genuinely-empty inbox self-hides.
  if (down) {
    return (
      <Section label="Inbox">
        <FlaggedFeed>Inbox feed didn’t respond — items may be waiting.</FlaggedFeed>
      </Section>
    )
  }
  const list = (items || []).slice(0, 6)
  if (list.length === 0) return null
  return (
    <Section label="Inbox">
      {list.map((it, i) => {
        const m = it.meta || {}
        const title = m.title || it.filename?.replace(/\.md$/, '') || 'Item'
        const when = m.created_at || m.captured_at || m.date
        return (
          <Row key={it.filename || i}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div>
              {m.summary && <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', lineHeight: 1.45 }}>{m.summary}</div>}
            </div>
            {when && <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)', flexShrink: 0 }}>{ageOf(when)}</span>}
          </Row>
        )
      })}
    </Section>
  )
}

export default function Now() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>{greeting()}, Leo</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>{longDate()}</div>

      <TalkToKai />

      {/* Digest — full width at the top; self-hides when the queue is empty */}
      <div style={{ marginTop: 26 }}>
        <ProactiveDigest />
      </div>

      {/* Responsive: Today + Inbox side-by-side on desktop, stacked on phone */}
      <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, marginTop: 34, alignItems: 'start' }}>
        <Today />
        <Inbox />
      </div>
    </div>
  )
}
