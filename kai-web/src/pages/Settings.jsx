// Settings — LEAN, text-forward (matches Now.jsx design language: hairlines not
// boxes, mono labels over a 1px border, terra accent, fail-soft fetches).
// Four sections only: Connections · Security · Comms · Appearance.
// Buzz is PRIMARY comms; Telegram is EMERGENCY/BACKUP. Slack does not exist.
// No per-integration health endpoint exists on the backend, so Connections rows
// are structural with a neutral status + a real re-auth link where one exists
// (Gmail/Calendar auth-url); we never fabricate a "connected" status.
import { useState, useEffect } from 'react'
import { api } from '../lib/api'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

const DOT = { connected: '#10b981', degraded: '#f59e0b', disconnected: '#ef4444', unknown: 'var(--text-tertiary)' }

// ── a text-forward section: a mono label over a hairline, then rows ───────────
function Section({ label, hint, children, style }) {
  return (
    <section style={style}>
      <div style={{
        fontSize: 11, fontWeight: 600, letterSpacing: '0.15em', textTransform: 'uppercase',
        color: 'var(--text-tertiary)', fontFamily: MONO,
        paddingBottom: 10, borderBottom: '1px solid var(--border)',
      }}>{label}</div>
      {hint && <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: '10px 8px 0', lineHeight: 1.5 }}>{hint}</div>}
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
        display: 'flex', alignItems: 'center', gap: 14, padding: '13px 8px',
        borderBottom: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default',
        background: hover && onClick ? 'var(--accent-bg)' : 'transparent',
        transition: 'background 120ms',
      }}
    >{children}</div>
  )
}

function Dot({ status }) {
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: DOT[status] || DOT.unknown, flexShrink: 0 }} />
}

// a small hairline "action" affordance (structural — link or button)
function Action({ children, href, onClick }) {
  const [hover, setHover] = useState(false)
  const style = {
    all: 'unset', cursor: 'pointer', flexShrink: 0, fontSize: 12, fontFamily: MONO,
    letterSpacing: '0.04em', color: hover ? 'var(--accent)' : 'var(--text-tertiary)',
    borderBottom: `1px solid ${hover ? 'var(--accent)' : 'transparent'}`, paddingBottom: 1,
    transition: 'color 120ms, border-color 120ms',
  }
  if (href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" style={style}
        onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>{children}</a>
    )
  }
  return (
    <button onClick={onClick} style={style}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>{children}</button>
  )
}

// ── 1. Connections — integration health + re-auth ────────────────────────────
// No backend "connected" status endpoint exists yet, so status stays neutral
// (unknown) and re-auth links point at the real auth-url routes where present.
function Connections() {
  const [wp, setWp] = useState('unknown') // WordPress has a real /wordpress/health
  useEffect(() => {
    api.get('/wordpress/health')
      .then((d) => setWp(d && (d.ok || d.healthy) ? 'connected' : d ? 'degraded' : 'unknown'))
      .catch(() => setWp('unknown'))
  }, [])

  const integrations = [
    { name: 'Google', status: 'unknown', reauth: '/api/calendar/auth-url' },
    { name: 'Gmail', status: 'unknown', reauth: '/api/gmail/auth-url' },
    { name: 'Calendar', status: 'unknown', reauth: '/api/calendar/auth-url' },
    { name: 'Todoist', status: 'unknown', reauth: null },
    { name: 'WordPress', status: wp, reauth: null },
    { name: 'Oura', status: 'unknown', reauth: null },
  ]

  return (
    <Section label="Connections" hint="Integration health. Live status is surfaced where the backend exposes it; others show a neutral state until wired.">
      {integrations.map((it) => (
        <Row key={it.name}>
          <Dot status={it.status} />
          <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1 }}>{it.name}</span>
          <span style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}>{it.status}</span>
          {it.reauth
            ? <Action href={it.reauth}>Re-auth →</Action>
            : <Action onClick={() => {}}>Re-auth →</Action>}
        </Row>
      ))}
    </Section>
  )
}

// ── 2. Security — mode-lock state (read-only default; unlock window) ──────────
function Security() {
  const [sessions, setSessions] = useState(null)
  useEffect(() => {
    api.get('/mode_lock/sessions')
      .then((d) => setSessions(d.sessions || []))
      .catch(() => setSessions([]))
  }, [])

  const active = (sessions || []).filter((s) => s && s.expires_at)
  const unlocked = active.length > 0
  const status = sessions === null ? 'unknown' : unlocked ? 'degraded' : 'connected'
  const stateLabel = sessions === null ? 'checking…' : unlocked ? 'unlock window open' : 'read-only (locked)'

  return (
    <Section label="Security" hint="Mode Lock. The system runs read-only by default; a YES unlocks a timed write window.">
      <Row>
        <Dot status={status} />
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1 }}>Mode Lock</span>
        <span style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}>{stateLabel}</span>
      </Row>
      {unlocked && active.map((s, i) => {
        let when = ''
        try { when = new Date(s.expires_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) } catch { /* noop */ }
        return (
          <Row key={s.id || i}>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, minWidth: 90 }}>{s.requester || 'session'}</span>
            <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1 }}>write window</span>
            {when && <span style={{ fontSize: 11.5, fontFamily: MONO, color: 'var(--accent)' }}>until {when}</span>}
          </Row>
        )
      })}
    </Section>
  )
}

// ── 3. Comms — Buzz PRIMARY, Telegram EMERGENCY/BACKUP (no Slack) ─────────────
function Comms() {
  const channels = [
    { name: 'Buzz', role: 'Primary', desc: 'Day-to-day comms, advisor DMs, approvals', status: 'connected' },
    { name: 'Telegram', role: 'Emergency / backup', desc: 'Escalation only — fires when Buzz is down', status: 'connected' },
  ]
  return (
    <Section label="Comms" hint="Routing preferences. Buzz carries everything; Telegram is the emergency lifeline.">
      {channels.map((c) => (
        <Row key={c.name}>
          <Dot status={c.status} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>{c.name}</div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 2 }}>{c.desc}</div>
          </div>
          <span style={{ fontSize: 11, fontFamily: MONO, color: c.role === 'Primary' ? 'var(--accent)' : 'var(--text-tertiary)', letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 }}>{c.role}</span>
        </Row>
      ))}
    </Section>
  )
}

// ── 4. Appearance — theme toggle (reuses the Nav/Layout mechanism) ────────────
// Same mechanism as Nav.jsx/TopNav.jsx: document.documentElement.dataset.theme
// + localStorage 'kai-theme'; main.jsx applies the saved value before render.
function Appearance() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme !== 'light')
  const setTheme = (nextDark) => {
    const next = nextDark ? 'dark' : 'light'
    document.documentElement.dataset.theme = next
    localStorage.setItem('kai-theme', next)
    setDark(nextDark)
  }
  const opts = [{ key: true, label: 'Dark' }, { key: false, label: 'Light' }]
  return (
    <Section label="Appearance" hint="Theme. Applies instantly and persists across sessions.">
      <Row>
        <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1 }}>Theme</span>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          {opts.map((o) => {
            const on = dark === o.key
            return (
              <button
                key={o.label}
                onClick={() => setTheme(o.key)}
                style={{
                  all: 'unset', cursor: 'pointer', fontSize: 12.5, fontFamily: MONO, letterSpacing: '0.04em',
                  padding: '5px 12px', borderRadius: 8,
                  color: on ? 'var(--accent)' : 'var(--text-tertiary)',
                  background: on ? 'var(--accent-bg)' : 'transparent',
                  border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                  transition: 'color 120ms, background 120ms, border-color 120ms',
                }}
              >{o.label}</button>
            )
          })}
        </div>
      </Row>
    </Section>
  )
}

export default function Settings() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>Settings</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        CONNECTIONS · SECURITY · COMMS · APPEARANCE
      </div>

      {/* Responsive: two columns on desktop, stacked on phone */}
      <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, marginTop: 34, alignItems: 'start' }}>
        <Connections />
        <Security />
        <Comms />
        <Appearance />
      </div>
    </div>
  )
}
