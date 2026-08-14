import { useState, useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { Activity, RefreshCw, ArrowUpRight, EyeOff, Layout, AlertTriangle } from 'lucide-react'

// How each gateway decision reads at a glance.
const DECISION = {
  delivered:            { label: 'Reached you',   cls: 'text-emerald-400 bg-emerald-400/10', icon: ArrowUpRight },
  dashboard_only:       { label: 'Dashboard',     cls: 'text-sky-400 bg-sky-400/10',         icon: Layout },
  suppressed_synthetic: { label: 'Suppressed',    cls: 'text-zinc-400 bg-zinc-400/10',       icon: EyeOff },
  suppressed_dedup:     { label: 'Deduped',       cls: 'text-zinc-400 bg-zinc-400/10',       icon: EyeOff },
  send_failed:          { label: 'Send failed',   cls: 'text-red-400 bg-red-400/10',         icon: AlertTriangle },
}
const FILTERS = [
  { id: 'all',        label: 'All' },
  { id: 'delivered',  label: 'Reached you' },
  { id: 'dashboard',  label: 'Dashboard-only' },
  { id: 'suppressed', label: 'Suppressed' },
]

function decInfo(d) {
  return DECISION[d] || { label: d || 'unknown', cls: 'text-zinc-400 bg-zinc-400/10', icon: Activity }
}
function fmtTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return ts }
}

export default function System() {
  const [data, setData]     = useState(null)
  const [loading, setLoad]  = useState(true)
  const [error, setError]   = useState(null)
  const [filter, setFilter] = useState('all')

  async function load() {
    setLoad(true); setError(null)
    try { setData(await api.getSystemActivity()) }
    catch (e) { setError(e.message) }
    finally { setLoad(false) }
  }
  useEffect(() => { load() }, [])

  const s = data?.summary || {}
  const totals = useMemo(() => ({
    reached:   s.delivered || 0,
    dashboard: s.dashboard_only || 0,
    suppressed: (s.suppressed_synthetic || 0) + (s.suppressed_dedup || 0),
    failed:    s.send_failed || 0,
  }), [data])

  const rows = useMemo(() => {
    const r = data?.records || []
    if (filter === 'all') return r
    if (filter === 'delivered')  return r.filter(x => x.decision === 'delivered')
    if (filter === 'dashboard')  return r.filter(x => x.decision === 'dashboard_only')
    if (filter === 'suppressed') return r.filter(x => String(x.decision || '').startsWith('suppressed'))
    return r
  }, [data, filter])

  return (
    <div className="max-w-4xl mx-auto px-8 py-10">
      <div className="flex items-start justify-between mb-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Activity size={20} className="kai-text-subtle" /> System
          </h1>
          <p className="kai-text-subtle text-sm mt-1">
            Everything KAI's notification gateway handled — what reached you, what stayed here,
            and what it suppressed. {data?.count ? `${data.count} recent events.` : ''}
          </p>
        </div>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5 text-xs"><RefreshCw size={12} /></button>
      </div>

      <CurrencyBoard />

      {error && <div className="kai-card px-5 py-4 text-sm text-red-400 my-4">Failed to load: {error}</div>}

      {loading ? (
        <div className="kai-card px-5 py-12 text-center kai-text-subtle text-sm">Loading system activity…</div>
      ) : !data ? null : (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 my-5">
            <Stat label="Reached you"     value={totals.reached}    tone="text-emerald-400" />
            <Stat label="Dashboard-only"  value={totals.dashboard}  tone="text-sky-400" />
            <Stat label="Suppressed"      value={totals.suppressed} tone="text-zinc-400" />
            <Stat label="Send failures"   value={totals.failed}     tone={totals.failed ? 'text-red-400' : 'text-zinc-400'} />
          </div>

          {/* Filters */}
          <div className="flex gap-1.5 mb-4 flex-wrap">
            {FILTERS.map(f => (
              <button key={f.id} onClick={() => setFilter(f.id)}
                className={`text-xs px-3 py-1 rounded-full border transition-colors
                  ${filter === f.id ? 'border-kai-blue text-kai-blue bg-kai-blue/10'
                                    : 'border-white/10 kai-text-subtle hover:border-white/20'}`}>
                {f.label}
              </button>
            ))}
          </div>

          {/* Feed */}
          <div className="kai-card divide-y divide-white/5">
            {rows.length === 0 ? (
              <p className="text-xs kai-text-subtle py-10 text-center">No events for this filter.</p>
            ) : rows.map((r, i) => {
              const info = decInfo(r.decision)
              const Icon = info.icon
              return (
                <div key={i} className="flex items-start gap-3 px-4 py-3">
                  <span className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${info.cls}`}>
                    <Icon size={11} /> {info.label}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] leading-snug truncate" title={r.title}>{r.title || <span className="kai-text-subtle">—</span>}</div>
                    <div className="text-[11px] kai-text-subtle mt-0.5">
                      {r.reason ? <span className="font-mono">{r.reason}</span> : null}
                      {r.reason ? ' · ' : ''}{fmtTime(r.ts)}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <p className="text-[10px] kai-text-subtle mt-4">
            Source: the single notify() gateway audit log. Telegram is used only for approvals and
            break-glass; everything else is handled and recorded here.
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, tone }) {
  return (
    <div className="kai-card px-4 py-3">
      <div className="kai-text-subtle text-[11px] uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-semibold mt-1 tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}

const CUR_PILL = {
  fresh:          "text-emerald-400 bg-emerald-400/10",
  stale:          "text-amber-400 bg-amber-400/10",
  "not-checked":  "text-zinc-400 bg-zinc-400/10",
}
function curPill(s) { return CUR_PILL[s] || "text-zinc-400 bg-zinc-400/10" }

const CUR_LABEL = { os_apt: "OS packages (apt)", container_images: "Container images", tls_certs: "TLS certificates" }
const CUR_ORDER = ["os_apt", "container_images", "tls_certs"]

// System Currency board (CUR-1). Self-contained + fail-silent so it can never
// break the notification feed. Honest: not-checked renders grey, never green.
function CurrencyBoard() {
  const [cur, setCur] = useState(null)
  useEffect(() => { api.getCurrencyState().then(setCur).catch(() => setCur(null)) }, [])
  if (!cur || !cur.layers) return null
  const roll = cur.rollup || {}
  return (
    <div className="kai-card px-5 py-4 my-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold">System Currency</h2>
        <span className="text-[11px] kai-text-subtle tabular-nums">
          {roll.fresh || 0} fresh · {roll.stale || 0} stale · {roll.not_checked || 0} not-checked
        </span>
      </div>
      <div className="divide-y divide-white/5">
        {CUR_ORDER.filter(k => cur.layers[k]).map(k => {
          const L = cur.layers[k]
          return (
            <div key={k} className="flex items-start gap-3 py-2.5">
              <span className={"inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap " + curPill(L.status)}>
                {L.status}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] leading-snug">{CUR_LABEL[k] || k}</div>
                <div className="text-[11px] kai-text-subtle mt-0.5">{L.detail}</div>
              </div>
            </div>
          )
        })}
      </div>
      <p className="text-[10px] kai-text-subtle mt-3">
        Source: currency_scan.py (host, read-only, CUR-1). Not-checked means no live reader yet — never a faked pass.
      </p>
    </div>
  )
}
