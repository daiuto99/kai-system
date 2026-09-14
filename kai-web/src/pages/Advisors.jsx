// Advisors (was "Chat") — talk to the LOCAL advisors + manage all advisors.
// This page exists because Ember + Doc are LOCAL-ONLY (they never leave Leo's
// environment, so they can't live on Buzz). KAI/Sky/Roads/Coach chat happens on
// Buzz; only the local pair is chatted with here. Text-forward / Vercel-style to
// match Now.jsx: hairlines not boxes, mono labels, terra accent. Avatar-forward
// cards under mono section labels, hairline-separated, responsive.
// Layout row order: 1) KAI  2) Doc + Ember  3) Roads + Sky  4) Coach.
// Fail-soft: a missing avatar falls back to initials; nothing errors to a wall.
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessageSquare, SlidersHorizontal } from 'lucide-react'
import { getAdvisor } from '../lib/advisors'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

// Which advisors are local-only (chatted with here + never leave the environment)
const LOCAL_ONLY = new Set(['doc', 'ember'])

// ── a text-forward section: a mono label over a hairline, then content ────────
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

// ── avatar with graceful initials fallback ────────────────────────────────────
function Avatar({ advisor, size = 52 }) {
  const [err, setErr] = useState(false)
  const r = Math.round(size * 0.26)
  if (advisor.avatar && !err) {
    return (
      <img
        src={advisor.avatar}
        alt={advisor.name}
        onError={() => setErr(true)}
        style={{ width: size, height: size, borderRadius: r, objectFit: 'cover', objectPosition: 'center top', flexShrink: 0 }}
      />
    )
  }
  const initials = advisor.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
  return (
    <div style={{
      width: size, height: size, borderRadius: r, flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--accent-dim)', color: 'var(--accent)',
      fontFamily: MONO, fontWeight: 600, fontSize: size * 0.36,
    }}>{initials}</div>
  )
}

// ── LOCAL-ONLY pill — small mono terra badge ──────────────────────────────────
function LocalOnlyBadge() {
  return (
    <span style={{
      fontSize: 9.5, fontWeight: 600, letterSpacing: '0.12em', fontFamily: MONO,
      color: 'var(--accent)', background: 'var(--accent-bg)',
      border: '1px solid var(--accent-dim)', borderRadius: 999,
      padding: '2px 7px', whiteSpace: 'nowrap',
    }}>LOCAL-ONLY</span>
  )
}

// ── a text-forward button (hairline, subtle accent hover) ─────────────────────
function AdvisorButton({ icon: Icon, label, onClick, primary }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        all: 'unset', cursor: 'pointer', boxSizing: 'border-box',
        display: 'inline-flex', alignItems: 'center', gap: 7,
        padding: '6px 12px', borderRadius: 8, fontSize: 12.5, fontWeight: 500,
        fontFamily: 'inherit',
        color: primary ? '#fff' : 'var(--text-secondary)',
        background: primary ? 'var(--accent)' : (hover ? 'var(--accent-bg)' : 'transparent'),
        border: `1px solid ${primary ? 'var(--accent)' : (hover ? 'var(--hover-border)' : 'var(--border)')}`,
        transition: 'background 120ms, border-color 120ms',
      }}
    >
      <Icon size={13} strokeWidth={2} />
      {label}
    </button>
  )
}

// ── one advisor card — avatar + name + role, hairline separated (not a box) ───
function AdvisorCard({ id }) {
  const advisor = getAdvisor(id)
  const nav = useNavigate()
  const isLocal = LOCAL_ONLY.has(id)
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16, padding: '18px 8px',
      borderBottom: '1px solid var(--border)',
    }}>
      <Avatar advisor={advisor} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>{advisor.name}</span>
          {isLocal && <LocalOnlyBadge />}
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 3, fontFamily: MONO, letterSpacing: '0.02em' }}>{advisor.role}</div>
        {advisor.intro && (
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.45, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{advisor.intro}</div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {isLocal && (
          <AdvisorButton icon={MessageSquare} label="Chat" primary onClick={() => nav(`/chat/${id}`)} />
        )}
        {/* Manage = knowledge/behavior/corrections — deep editing deferred.
            Structural affordance; navigates to the chat surface as the current
            place an advisor is worked with. */}
        <AdvisorButton icon={SlidersHorizontal} label="Manage" onClick={() => nav(`/chat/${id}`)} />
      </div>
    </div>
  )
}

export default function Advisors() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>Advisors</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        Chat with the local pair · manage every advisor
      </div>

      {/* KAI — top row, on its own */}
      <Section label="Command" style={{ marginTop: 34 }}>
        <AdvisorCard id="kai" />
      </Section>

      {/* Doc + Ember — the local-only pair, chatted with here */}
      <Section label="Local · Chat here" style={{ marginTop: 40 }}>
        <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, alignItems: 'start' }}>
          <AdvisorCard id="doc" />
          <AdvisorCard id="ember" />
        </div>
      </Section>

      {/* Roads + Sky — on Buzz for chat, managed here */}
      <Section label="Studio · Chat on Buzz" style={{ marginTop: 40 }}>
        <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, alignItems: 'start' }}>
          <AdvisorCard id="roads" />
          <AdvisorCard id="sky" />
        </div>
      </Section>

      {/* Coach — on Buzz for chat, managed here */}
      <Section label="Performance · Chat on Buzz" style={{ marginTop: 40 }}>
        <AdvisorCard id="coach" />
      </Section>
    </div>
  )
}
