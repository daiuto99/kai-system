// System — the deliberate back-office. Unlike the Now page (which forbids
// telemetry), this is the ONE place system stats belong: you go to it, it's
// never in your face. Text-forward / Vercel-style to match Now.jsx: hairlines
// not boxes, mono uppercase labels, terra accent, status dots. Four sections:
//   1. Architecture & Status — layers/components with status dots + versions,
//      plus host health + ops state (reuses getSystemHealth / getOpsState).
//   2. Activity Log — chronological DevOps feed (getSystemActivity + getGitActivity).
//   3. Cost / Spend — KAI's OWN operating cost (getTokenUsage / getAnthropicBilling
//      / getFinancial). Not Leo's business finances.
//   4. About — a short, honest "About KAI" describing the self-hosted stack.
// Every fetch is fail-soft: a dead call renders a quiet empty line, never an
// error wall. Comms = Buzz primary / Telegram backup only (Slack is retired).
import { useState, useEffect } from 'react'
import { api } from '../lib/api'

const MONO = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, monospace"

// status dot colours — locked palette
const DOT = { green: '#10b981', amber: '#f59e0b', red: '#ef4444', grey: 'var(--text-tertiary)' }

// ── a text-forward section: a mono label over a hairline, then rows ───────────
function Section({ label, meta, children, style }) {
  return (
    <section style={style}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12,
        paddingBottom: 10, borderBottom: '1px solid var(--border)',
      }}>
        <span style={{
          fontSize: 11, fontWeight: 600, letterSpacing: '0.15em', textTransform: 'uppercase',
          color: 'var(--text-tertiary)', fontFamily: MONO,
        }}>{label}</span>
        {meta != null && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, letterSpacing: '0.02em', flexShrink: 0 }}>{meta}</span>
        )}
      </div>
      <div>{children}</div>
    </section>
  )
}

// a single hairline-separated row (subtle accent-bg hover only when clickable)
function Row({ children, onClick, align = 'baseline' }) {
  const [hover, setHover] = useState(false)
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: align, gap: 14, padding: '11px 8px',
        borderBottom: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default',
        background: hover && onClick ? 'var(--accent-bg)' : 'transparent',
        transition: 'background 120ms',
      }}
    >{children}</div>
  )
}

function SubLabel({ children }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-tertiary)', fontFamily: MONO, margin: '18px 8px 2px' }}>{children}</div>
  )
}

function Dot({ tone = 'grey' }) {
  return (
    <span style={{
      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
      background: DOT[tone] || DOT.grey, display: 'inline-block',
      alignSelf: 'center',
    }} />
  )
}

function Empty({ children }) {
  return (
    <div style={{ padding: '14px 8px', fontSize: 13, color: 'var(--text-tertiary)' }}>{children}</div>
  )
}

// ── time helpers ──────────────────────────────────────────────────────────────
function fmtTime(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return String(ts) }
}
function fmtUsd(n) {
  if (n == null || isNaN(n)) return '—'
  if (n === 0) return '$0'
  if (Math.abs(n) < 0.01) return '$' + n.toFixed(4)
  if (Math.abs(n) < 1)    return '$' + n.toFixed(3)
  if (Math.abs(n) < 100)  return '$' + n.toFixed(2)
  return '$' + Math.round(n).toLocaleString()
}
function fmtKilo(n) {
  if (n == null || n === 0) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(1) + 'k'
  return (n / 1_000_000).toFixed(2) + 'M'
}

// ══════════════════════════════════════════════════════════════════════════════
// 1. ARCHITECTURE & STATUS — components with status dots + versions, host + ops.
// ══════════════════════════════════════════════════════════════════════════════
function Architecture() {
  const [health, setHealth] = useState(null)
  const [ops, setOps] = useState(null)
  const [services, setServices] = useState(null)

  useEffect(() => {
    api.getSystemHealth().then(setHealth).catch(() => setHealth(null))
    api.getOpsState().then(setOps).catch(() => setOps(null))
    // Optional live service/component list — self-hides if the route isn't wired.
    api.get('/admin/services').then(setServices).catch(() => setServices(null))
  }, [])

  const t = (health && health.thresholds) || {}
  const tone = (v, max) => (max != null && v != null && v >= max) ? 'amber' : 'green'

  // Host health metrics as rows (only when the backend returned them).
  const metrics = health ? [
    { k: 'Disk', v: health.disk_pct != null ? `${health.disk_pct}%` : '—', sub: health.disk_free_gb != null ? `${health.disk_free_gb}G free / ${health.disk_total_gb}G` : '', tone: tone(health.disk_pct, t.disk_pct) },
    { k: 'Memory', v: health.mem_pct != null ? `${health.mem_pct}%` : '—', sub: health.mem_free_gb != null ? `${health.mem_free_gb}G free / ${health.mem_total_gb}G` : '', tone: tone(health.mem_pct, t.mem_pct) },
    { k: 'Load (1m)', v: health.load_1m != null ? String(health.load_1m) : '—', sub: '', tone: 'green' },
    { k: 'Temp', v: health.temp_c != null ? `${health.temp_c}°C` : '—', sub: '', tone: tone(health.temp_c, t.temp_c) },
    { k: 'Uptime', v: health.uptime || '—', sub: '', tone: 'green' },
    { k: 'Pending updates', v: health.apt_updates != null ? String(health.apt_updates) : '—', sub: 'apt', tone: tone(health.apt_updates, t.apt_updates) },
  ] : []

  // Live services/components list — accept a couple of common shapes, fail-soft.
  const svcList = (() => {
    if (!services) return []
    const raw = Array.isArray(services) ? services
      : services.services || services.components || services.items || []
    if (!Array.isArray(raw)) return []
    return raw.map((s) => {
      const status = String(s.status || s.state || '').toLowerCase()
      const ok = ['ok', 'up', 'running', 'healthy', 'live', 'green'].includes(status)
      const bad = ['down', 'error', 'failed', 'dead', 'red', 'unhealthy'].includes(status)
      return {
        name: s.name || s.id || s.label || 'component',
        version: s.version || s.tag || s.image || '',
        detail: s.detail || s.note || (ok || bad ? '' : status),
        tone: ok ? 'green' : bad ? 'red' : status ? 'amber' : 'grey',
      }
    })
  })()

  const failing = (ops && ops.failing_invariants) || {}
  const failKeys = Object.keys(failing)
  const backup = (ops && ops.backup) || null

  const nothing = !health && !ops && svcList.length === 0
  const invSummary = ops ? (failKeys.length ? `${failKeys.length} failing invariant${failKeys.length > 1 ? 's' : ''}` : 'invariants clean') : null

  return (
    <Section label="Architecture & Status" meta={invSummary}>
      {nothing && <Empty>System state unavailable right now.</Empty>}

      {/* Live components / services, when the backend exposes them */}
      {svcList.length > 0 && (
        <div>
          <SubLabel>Components</SubLabel>
          {svcList.map((s, i) => (
            <Row key={s.name + i}>
              <Dot tone={s.tone} />
              <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
              {s.detail && <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0 }}>{s.detail}</span>}
              {s.version && <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>{s.version}</span>}
            </Row>
          ))}
        </div>
      )}

      {/* Host health — the running node's hygiene, coloured on the backend's own thresholds */}
      {metrics.length > 0 && (
        <div>
          <SubLabel>Host health {health && health.ok === false ? `· ${(health.alerts || []).length} alert(s)` : '· nominal'}</SubLabel>
          {metrics.map((m) => (
            <Row key={m.k}>
              <Dot tone={m.tone} />
              <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>{m.k}</span>
              {m.sub && <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>{m.sub}</span>}
              <span style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 52, textAlign: 'right' }}>{m.v}</span>
            </Row>
          ))}
          {(health && health.alerts && health.alerts.length > 0) && health.alerts.map((a, i) => (
            <Row key={`al${i}`}>
              <Dot tone="amber" />
              <span style={{ fontSize: 13, color: 'var(--text-secondary)', flex: 1 }}>{a}</span>
            </Row>
          ))}
        </div>
      )}

      {/* Ops state — the same invariants the scheduler watchdog alerts on */}
      {ops && (
        <div>
          <SubLabel>Ops state</SubLabel>
          {backup && (
            <Row>
              <Dot tone={backup.status === 'ok' ? 'green' : 'amber'} />
              <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>
                Backups{ops.backup_trigger_pending ? ' · trigger pending' : ''}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }}>{backup.detail || backup.status || '—'}</span>
            </Row>
          )}
          {failKeys.length === 0 ? (
            <Row><Dot tone="green" /><span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>Invariants clean</span></Row>
          ) : failKeys.map((k) => (
            <Row key={k} align="baseline">
              <span style={{ marginTop: 4 }}><Dot tone="amber" /></span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text-primary)', fontFamily: MONO }}>{k}</div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 2, lineHeight: 1.4 }}>{failing[k]}</div>
              </div>
            </Row>
          ))}
        </div>
      )}

      <p style={{ fontSize: 10.5, color: 'var(--text-tertiary)', margin: '12px 8px 0', lineHeight: 1.5 }}>
        Source: /system/health + /system/ops-state (host, read-only). Dots track the backend's own thresholds — never a faked green.
      </p>
    </Section>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// 2. ACTIVITY LOG — chronological DevOps feed: gateway events + git commits.
// ══════════════════════════════════════════════════════════════════════════════
const GATEWAY_TONE = {
  delivered: 'green',
  dashboard_only: 'grey',
  suppressed_synthetic: 'grey',
  suppressed_dedup: 'grey',
  send_failed: 'red',
}
const GATEWAY_LABEL = {
  delivered: 'reached you',
  dashboard_only: 'dashboard',
  suppressed_synthetic: 'suppressed',
  suppressed_dedup: 'deduped',
  send_failed: 'send failed',
}

function ActivityLog() {
  const [act, setAct] = useState(null)
  const [git, setGit] = useState(null)

  useEffect(() => {
    api.getSystemActivity().then(setAct).catch(() => setAct(null))
    api.getGitActivity().then(setGit).catch(() => setGit(null))
  }, [])

  // Merge notify-gateway events + git commits into one chronological feed.
  const events = []
  for (const r of (act && act.records) || []) {
    events.push({
      kind: 'gateway',
      tone: GATEWAY_TONE[r.decision] || 'grey',
      tag: GATEWAY_LABEL[r.decision] || (r.decision || 'event'),
      title: r.title || '—',
      sub: r.reason || '',
      ts: r.ts,
    })
  }
  for (const c of (git && git.commits) || []) {
    events.push({
      kind: 'commit',
      tone: c.commit_type === 'local' ? 'amber' : 'green',
      tag: c.short_hash || (c.hash || '').slice(0, 7),
      title: c.message || '—',
      sub: [c.repo, c.author].filter(Boolean).join(' · '),
      ts: c.committed_at,
    })
  }
  events.sort((a, b) => {
    const ta = a.ts ? new Date(a.ts).getTime() : 0
    const tb = b.ts ? new Date(b.ts).getTime() : 0
    return tb - ta // most recent first
  })
  const feed = events.slice(0, 40)

  return (
    <Section label="Activity Log" meta={feed.length ? `${feed.length} recent` : null}>
      {feed.length === 0 ? (
        <Empty>No recent system activity.</Empty>
      ) : feed.map((e, i) => (
        <Row key={`${e.kind}${i}`}>
          <span style={{ marginTop: 4 }}><Dot tone={e.tone} /></span>
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, minWidth: 74, textTransform: 'uppercase', letterSpacing: '0.04em', alignSelf: 'flex-start', marginTop: 1 }}>{e.tag}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13.5, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={e.title}>{e.title}</div>
            {e.sub && <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontFamily: MONO, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.sub}</div>}
          </div>
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, alignSelf: 'flex-start', marginTop: 1 }}>{fmtTime(e.ts)}</span>
        </Row>
      ))}
      <p style={{ fontSize: 10.5, color: 'var(--text-tertiary)', margin: '12px 8px 0', lineHeight: 1.5 }}>
        Source: the notify() gateway audit log + /git-activity/latest (kai-system + sonicink). Telegram is used only for approvals and break-glass; everything else is handled and recorded here.
      </p>
    </Section>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// 3. COST / SPEND — KAI's OWN operating cost. Not Leo's business finances.
// ══════════════════════════════════════════════════════════════════════════════
function CostSpend() {
  const [usage, setUsage] = useState(null)
  const [billing, setBilling] = useState(null)
  const [financial, setFinancial] = useState(null)

  useEffect(() => {
    api.getTokenUsage().then(setUsage).catch(() => setUsage(null))
    api.getAnthropicBilling(30).then(setBilling).catch(() => setBilling(null))
    api.getFinancial().then(setFinancial).catch(() => setFinancial(null))
  }, [])

  // Aggregate the internal token tracker over the last 30 days.
  const days = (usage && usage.days) || []
  const last30 = [...days].sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-30)
  let cost = 0, calls = 0, tin = 0, tout = 0
  const byProvider = {}
  for (const d of last30) {
    cost += d.cost_usd || 0
    calls += d.calls || 0
    tin += d.input || 0
    tout += d.output || 0
    for (const [k, v] of Object.entries(d.by_provider || {})) {
      const e = byProvider[k] || { cost: 0, calls: 0 }
      if (typeof v === 'number') { e.calls += v }
      else { e.cost += v.cost_usd || 0; e.calls += v.calls || 0 }
      byProvider[k] = e
    }
  }
  const providerRows = Object.entries(byProvider)
    .map(([k, v]) => ({ key: k, ...v }))
    .sort((a, b) => b.cost - a.cost)

  // Anthropic billed total — build (Claude Code) vs run (council · Buzz).
  const b = (billing && billing.buckets) || null
  const billingConfigured = billing && billing.configured !== false && !billing.error

  // Financial registry — metered MTD spend + fixed monthly (KAI's operating caps).
  const totals = (financial && financial.totals) || null

  const nothing = last30.length === 0 && !b && !totals

  return (
    <Section label="Cost / Spend" meta={last30.length ? `${last30.length}d` : null}>
      {nothing && <Empty>Operating-cost data unavailable right now.</Empty>}

      {/* Top-line — KAI's own 30d operating spend from the internal tracker */}
      {last30.length > 0 && (
        <div>
          <SubLabel>Operating spend · 30d</SubLabel>
          <Row>
            <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>Instrumented API cost</span>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>{calls.toLocaleString()} calls · {fmtKilo(tin)}/{fmtKilo(tout)} tok</span>
            <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--accent)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(cost)}</span>
          </Row>
          {providerRows.map((p) => (
            <Row key={p.key}>
              <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1, paddingLeft: 4 }}>{p.key}</span>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>{p.calls.toLocaleString()} calls</span>
              <span style={{ fontSize: 13, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(p.cost)}</span>
            </Row>
          ))}
        </div>
      )}

      {/* Anthropic billed total — the true build-vs-run split */}
      {b && (
        <div>
          <SubLabel>Anthropic billed · {billing.range_days || 30}d</SubLabel>
          {!billingConfigured ? (
            <Empty>Admin key not configured — build-vs-run total unavailable.</Empty>
          ) : (
            <>
              <Row>
                <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>Total billed</span>
                <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--accent)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(billing.total_usd)}</span>
              </Row>
              <Row>
                <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1, paddingLeft: 4 }}>Build (Claude Code)</span>
                <span style={{ fontSize: 13, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(b.build || 0)}</span>
              </Row>
              <Row>
                <span style={{ fontSize: 13.5, color: 'var(--text-secondary)', flex: 1, paddingLeft: 4 }}>Run (council · Buzz)</span>
                <span style={{ fontSize: 13, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(b.run || 0)}</span>
              </Row>
              {(b.unclassified != null) && (
                <Row>
                  <span style={{ fontSize: 13.5, color: 'var(--text-tertiary)', flex: 1, paddingLeft: 4 }}>Unclassified</span>
                  <span style={{ fontSize: 13, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(b.unclassified || 0)}</span>
                </Row>
              )}
            </>
          )}
        </div>
      )}

      {/* Provider caps — metered MTD + fixed monthly from the financial registry */}
      {totals && (
        <div>
          <SubLabel>Provider caps</SubLabel>
          <Row>
            <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>Metered spend (MTD)</span>
            <span style={{ fontSize: 13, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(totals.metered_mtd_usd)}</span>
          </Row>
          <Row>
            <span style={{ fontSize: 14, color: 'var(--text-primary)', flex: 1 }}>Fixed / recurring per month</span>
            <span style={{ fontSize: 13, color: 'var(--text-primary)', fontFamily: MONO, flexShrink: 0, minWidth: 64, textAlign: 'right' }}>{fmtUsd(totals.fixed_monthly_usd)}</span>
          </Row>
          {(financial && financial.providers) && (
            <Row>
              <span style={{ fontSize: 13.5, color: 'var(--text-tertiary)', flex: 1, paddingLeft: 4 }}>Providers tracked</span>
              <span style={{ fontSize: 13, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>{financial.providers.length}</span>
            </Row>
          )}
        </div>
      )}

      <p style={{ fontSize: 10.5, color: 'var(--text-tertiary)', margin: '12px 8px 0', lineHeight: 1.5 }}>
        KAI's own operating cost — API providers, tokens, and caps. Not Leo's business finances. Sources: /token-usage, /anthropic/billing, /orchestrator/financial.
      </p>
    </Section>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// 4. ABOUT — the pipeline-maintained "About KAI". Structure-first, honest, brief.
// ══════════════════════════════════════════════════════════════════════════════
function About() {
  // If the pipeline publishes an About/presentation asset, prefer it; else the
  // concise structural description below. Fail-soft on any fetch.
  const [about, setAbout] = useState(undefined) // undefined = loading, null = use fallback
  useEffect(() => {
    api.get('/about').then((d) => setAbout(d && (d.markdown || d.text || d.body) ? d : null)).catch(() => setAbout(null))
  }, [])

  const facts = [
    { k: 'What it is', v: 'A self-hosted personal-assistant stack — KAI — built to be JARVIS-like for Leo.' },
    { k: 'Where it runs', v: 'Own hardware on a private Tailscale network; no third-party host holds the system of record.' },
    { k: 'Council model', v: 'KAI orchestrates a council of advisors; work is tracked as structured tasks and gated on approval.' },
    { k: 'Comms', v: 'Buzz is the primary channel; Telegram is emergency backup only.' },
    { k: 'Operating principle', v: 'Read-only by default, acts under an explicit unlock, and audits its own surfaces.' },
  ]

  return (
    <Section label="About">
      {about ? (
        <div style={{ padding: '14px 8px', fontSize: 13.5, color: 'var(--text-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
          {about.markdown || about.text || about.body}
        </div>
      ) : (
        facts.map((f) => (
          <Row key={f.k} align="baseline">
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0, minWidth: 120, textTransform: 'uppercase', letterSpacing: '0.04em', alignSelf: 'flex-start', marginTop: 2 }}>{f.k}</span>
            <span style={{ fontSize: 13.5, color: 'var(--text-primary)', flex: 1, lineHeight: 1.55 }}>{f.v}</span>
          </Row>
        ))
      )}
      <p style={{ fontSize: 10.5, color: 'var(--text-tertiary)', margin: '12px 8px 0', lineHeight: 1.5 }}>
        The About view is pipeline-maintained so it stays current. Deeper presentation content is deferred (structure-first).
      </p>
    </Section>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
export default function System() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '40px 20px 56px' }}>
      <div style={{ fontSize: 26, fontWeight: 650, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>System</div>
      <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 5, fontFamily: MONO, letterSpacing: '0.02em' }}>
        BACK-OFFICE · ARCHITECTURE · ACTIVITY · COST · ABOUT
      </div>

      {/* Architecture + Activity side-by-side on desktop, stacked on phone */}
      <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, marginTop: 34, alignItems: 'start' }}>
        <Architecture />
        <ActivityLog />
      </div>

      {/* Cost + About side-by-side on desktop, stacked on phone */}
      <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 30, marginTop: 40, alignItems: 'start' }}>
        <CostSpend />
        <About />
      </div>
    </div>
  )
}
