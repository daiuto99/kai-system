// Life — a visual status + check-in mirror. Answers one question at a glance:
// "Am I living my life?" Text-forward / Vercel-style (hairlines not boxes, mono
// labels, terra accent) — matches Now.jsx exactly. Low interaction by design.
// Layout: header · light check-in line · the Harmony life-domain map (responsive
// tiles, color-coded by health) · "Needs a nudge" (ONLY off-track/attention
// domains, each with an advisor-attributed nudge + avatar). Habits feed domain
// status SILENTLY — no habits list here. Every fetch is fail-soft; empty
// sections self-hide, never an error wall.
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { HARMONY_DOMAINS } from '../lib/harmonyData'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

// status → color (green on track · amber needs attention · red off track).
// Reuses Harmony's exact status vocabulary (green/yellow/red).
const STATUS_COLOR = { green: '#10b981', yellow: '#f59e0b', red: '#ef4444' }
const STATUS_LABEL = { green: 'On track', yellow: 'Needs attention', red: 'Off track' }

// worst-aspect wins — same derivation Harmony uses for a domain's overall health.
function domainStatus(aspects) {
  const vals = Object.values(aspects || {}).map((a) => a.status || 'green')
  if (vals.includes('red')) return 'red'
  if (vals.includes('yellow')) return 'yellow'
  return 'green'
}

// Insights carry a category but no advisor field — map category → the advisor who
// naturally owns that lens, so a nudge surfaces with a real face. Falls back to KAI.
function advisorForCategory(category) {
  switch ((category || '').toLowerCase()) {
    case 'truth':
    case 'realization':
    case 'question':
      return 'ember'
    case 'pattern':
      return 'coach'
    case 'insight':
      return 'doc'
    default:
      return 'kai'
  }
}
const ADVISOR_NAME = { ember: 'Ember', doc: 'Doc', coach: 'Coach', kai: 'KAI' }

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

// ── light check-in entry point — reads today's state, hands off to the flow ───
function CheckInLine() {
  const [done, setDone] = useState(null) // null=unknown, true/false once loaded
  const nav = useNavigate()
  useEffect(() => {
    const now = new Date()
    const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    const type = now.getHours() >= 17 ? 'evening' : 'morning'
    api.getCheckin()
      .then((d) => setDone(!!(d && d.date === todayStr && d[type])))
      .catch(() => setDone(false))
  }, [])
  const label = done
    ? "Checked in today. Revisit or add an evening note."
    : "Take 30 seconds to check in — it tunes the map below."
  return (
    <div
      onClick={() => nav('/today-classic')}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') nav('/today-classic') }}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, marginTop: 20, cursor: 'pointer',
        padding: '13px 16px', borderRadius: 12,
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        transition: 'border-color 120ms',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--hover-border)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      <span style={{ flex: 1, fontSize: 14.5, color: 'var(--text-primary)', lineHeight: 1.4 }}>{label}</span>
      <span style={{
        flexShrink: 0, width: 30, height: 30, borderRadius: 8,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--accent)', color: '#fff',
      }}>
        <ArrowRight size={16} strokeWidth={2.2} />
      </span>
    </div>
  )
}

// ── a single domain tile — hairline, color-coded status dot + accent bar ──────
function DomainTile({ domain }) {
  const [hover, setHover] = useState(false)
  const status = domainStatus(domain.aspects)
  const color = STATUS_COLOR[status]
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: 'relative', overflow: 'hidden',
        padding: '14px 16px', borderRadius: 10,
        background: 'var(--bg-card)',
        border: `1px solid ${hover ? 'var(--hover-border)' : 'var(--border)'}`,
        transition: 'border-color 120ms',
      }}
    >
      {/* left accent bar carries the health color quietly */}
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: color }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingLeft: 6 }}>
        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span style={{
          flex: 1, fontSize: 14, fontWeight: 500, color: 'var(--text-primary)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{domain.name}</span>
      </div>
      <div style={{
        marginTop: 6, paddingLeft: 24, fontSize: 10.5, fontWeight: 600, letterSpacing: '0.08em',
        textTransform: 'uppercase', fontFamily: MONO, color,
      }}>{STATUS_LABEL[status]}</div>
    </div>
  )
}

// ── one nudge row — advisor avatar + attributed, actionable observation ───────
function NudgeRow({ nudge }) {
  const color = STATUS_COLOR[nudge.status] || 'var(--accent)'
  return (
    <Row>
      <img
        src={`/avatar-${nudge.advisorId}.png`}
        alt={nudge.advisorName}
        onError={(e) => { e.currentTarget.style.visibility = 'hidden' }}
        style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, objectFit: 'cover', alignSelf: 'center' }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{nudge.domainName}</span>
          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
          <span style={{ fontSize: 11, fontFamily: MONO, color: 'var(--text-tertiary)' }}>{nudge.advisorName}</span>
        </div>
        <div style={{ marginTop: 4, fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{nudge.text}</div>
      </div>
    </Row>
  )
}

export default function Life() {
  // Domains seed from the local Harmony definitions (instant paint), then the
  // live board overwrites them fail-soft.
  const [domains, setDomains] = useState(HARMONY_DOMAINS)
  const [insights, setInsights] = useState(null) // null until fetched; [] = none

  useEffect(() => {
    api.getHarmony()
      .then((d) => { if (d && Array.isArray(d.domains) && d.domains.length) setDomains(d.domains) })
      .catch(() => {}) // keep the seed
    api.getInsights()
      .then((d) => setInsights(Array.isArray(d?.insights) ? d.insights : []))
      .catch(() => setInsights([]))
  }, [])

  // Off-track / needs-attention domains, worst first — these drive "Needs a nudge".
  const flagged = domains
    .map((d) => ({ domain: d, status: domainStatus(d.aspects) }))
    .filter((x) => x.status !== 'green')
    .sort((a, b) => (a.status === 'red' ? -1 : 1) - (b.status === 'red' ? -1 : 1))

  const onTrackCount = domains.length - flagged.length

  // Build nudges ONLY when we have real advisor observations to attribute. Each
  // flagged domain pairs with one insight (matched by name mention if possible,
  // else round-robin). No insights → no nudges → the section self-hides. We never
  // fabricate a nudge.
  const available = insights || []
  const nudges = []
  if (available.length && flagged.length) {
    const used = new Set()
    flagged.forEach((f, i) => {
      const nameLc = f.domain.name.toLowerCase()
      let idx = available.findIndex((ins, j) => !used.has(j) && (ins.content || '').toLowerCase().includes(nameLc))
      if (idx === -1) idx = available.findIndex((_, j) => !used.has(j))
      if (idx === -1) return
      used.add(idx)
      const ins = available[idx]
      const advisorId = advisorForCategory(ins.category)
      nudges.push({
        key: `${f.domain.id}-${i}`,
        domainName: f.domain.name,
        status: f.status,
        advisorId,
        advisorName: ADVISOR_NAME[advisorId] || 'KAI',
        text: ins.content,
      })
    })
  }

  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      {/* Header */}
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>Life</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        AM I LIVING MY LIFE? · {onTrackCount}/{domains.length} ON TRACK
      </div>

      {/* Light check-in entry point — feeds the map below */}
      <CheckInLine />

      {/* The map — every domain, color-coded by health. The glance = the answer. */}
      <Section label="The Map" style={{ marginTop: 34 }}>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3" style={{ gap: 12, marginTop: 16 }}>
          {domains.map((d) => (
            <DomainTile key={d.id} domain={d} />
          ))}
        </div>
      </Section>

      {/* Needs a nudge — ONLY flagged domains, each with an advisor observation.
          Self-hides when there's nothing to attribute a real nudge to. */}
      {nudges.length > 0 && (
        <Section label="Needs a Nudge" style={{ marginTop: 34 }}>
          {nudges.map((n) => (
            <NudgeRow key={n.key} nudge={n} />
          ))}
        </Section>
      )}
    </div>
  )
}
