import { useState, useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { DollarSign, RefreshCw, AlertTriangle, CheckCircle2, HelpCircle, XCircle, Clock } from 'lucide-react'

// KAI-1283 — the trustworthy financial view. Every provider: what it costs, spend
// this month, the cap + reset countdown, and whether it's live right now. Each figure
// carries a freshness stamp; anything older than 24h shows a visible warning.

function fmtUsd(n) {
  if (n == null || isNaN(n)) return '—'
  if (n === 0) return '$0'
  if (Math.abs(n) < 0.01) return '$' + n.toFixed(4)
  if (Math.abs(n) < 1)    return '$' + n.toFixed(3)
  if (Math.abs(n) < 100)  return '$' + n.toFixed(2)
  return '$' + Math.round(n).toLocaleString()
}
function fmtInt(n) { return n == null ? '—' : n.toLocaleString() }

function stampAge(iso) {
  if (!iso) return { txt: 'unknown', stale: true }
  try {
    const then = new Date(iso).getTime()
    const mins = Math.round((Date.now() - then) / 60000)
    if (mins < 1) return { txt: 'just now', stale: false }
    if (mins < 60) return { txt: `${mins}m ago`, stale: false }
    const hrs = Math.round(mins / 60)
    return { txt: `${hrs}h ago`, stale: hrs >= 24 }
  } catch { return { txt: iso, stale: true } }
}

const ACCESS = {
  'live':         { label: 'Live',        cls: 'text-emerald-400 bg-emerald-400/10', Icon: CheckCircle2 },
  'assumed-live': { label: 'Assumed live', cls: 'text-sky-400 bg-sky-400/10',        Icon: CheckCircle2 },
  'see-baseline': { label: 'See baseline', cls: 'text-amber-400 bg-amber-400/10',    Icon: HelpCircle },
  'dead-key':     { label: 'Dead key',    cls: 'text-red-400 bg-red-400/10',         Icon: XCircle },
  'unknown':      { label: 'Unknown',     cls: 'text-zinc-400 bg-zinc-400/10',       Icon: HelpCircle },
}
function accessInfo(a) { return ACCESS[a] || ACCESS.unknown }

const CAP = {
  ok:    'text-emerald-400',
  tight: 'text-amber-400',
  over:  'text-red-400',
  unset: 'text-zinc-500',
}

const BILLING_LABEL = {
  metered: 'Metered',
  subscription: 'Subscription',
  free_tier: 'Free tier',
  variable_infrastructure: 'Variable (infra)',
  annual: 'Annual',
}

export default function Financial() {
  const [data, setData]    = useState(null)
  const [loading, setLoad] = useState(true)
  const [error, setError]  = useState(null)

  async function load() {
    setLoad(true); setError(null)
    try { setData(await api.getFinancial()) }
    catch (e) { setError(e.message) }
    finally { setLoad(false) }
  }
  useEffect(() => { load() }, [])

  const providers = data?.providers || []
  const metered = useMemo(() => providers.filter(p => p.billing_model === 'metered'), [providers])
  const recurring = useMemo(() => providers.filter(p => p.billing_model !== 'metered'), [providers])
  const age = stampAge(data?.verified_at)

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <div className="flex items-start justify-between mb-1">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <DollarSign size={20} className="kai-text-subtle" /> Financial
          </h1>
          <p className="kai-text-subtle text-sm mt-1">
            Every provider — cost, month-to-date spend, cap &amp; reset, and whether it's live right now.
          </p>
        </div>
        <button onClick={load} disabled={loading}
          className="flex items-center gap-1.5 text-xs kai-text-secondary hover:text-white px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 transition-colors">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Freshness stamp */}
      <div className={`text-xs mb-5 flex items-center gap-1.5 ${age.stale ? 'text-amber-400' : 'kai-text-subtle'}`}>
        <Clock size={12} />
        {data ? <>Verified {age.txt}{data.month ? ` · ${data.month}` : ''}{data.registry_version ? ` · registry v${data.registry_version}` : ''}</> : 'Loading…'}
        {age.stale && <span className="ml-1">— stale (&gt;24h), refresh</span>}
      </div>

      {error && (
        <div className="kai-card p-4 mb-5 text-sm text-red-400 flex items-center gap-2">
          <AlertTriangle size={15} /> {error}
        </div>
      )}

      {/* Totals strip */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-5">
          <div className="kai-card p-4">
            <p className="text-xs kai-text-subtle">Metered spend (MTD)</p>
            <p className="text-xl font-semibold mt-1">{fmtUsd(data.totals?.metered_mtd_usd)}</p>
          </div>
          <div className="kai-card p-4">
            <p className="text-xs kai-text-subtle">Fixed / recurring / mo</p>
            <p className="text-xl font-semibold mt-1">{fmtUsd(data.totals?.fixed_monthly_usd)}</p>
          </div>
          <div className="kai-card p-4 col-span-2 sm:col-span-1">
            <p className="text-xs kai-text-subtle">Providers tracked</p>
            <p className="text-xl font-semibold mt-1">{providers.length}</p>
          </div>
        </div>
      )}

      {/* Warnings */}
      {data?.warnings?.length > 0 && (
        <div className="kai-card p-4 mb-5 border border-amber-400/20">
          <p className="text-xs font-medium text-amber-400 flex items-center gap-1.5 mb-2">
            <AlertTriangle size={13} /> {data.warnings.length} thing{data.warnings.length > 1 ? 's' : ''} to know
          </p>
          <ul className="space-y-1">
            {data.warnings.map((w, i) => (
              <li key={i} className="text-xs kai-text-secondary leading-relaxed">• {w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Metered providers */}
      {metered.length > 0 && (
        <section className="mb-6">
          <h2 className="text-xs font-semibold uppercase tracking-wide kai-text-subtle mb-2">Metered — pay per use</h2>
          <div className="kai-card divide-y kai-divider overflow-x-auto">
            {metered.map(p => {
              const ai = accessInfo(p.access_status)
              const capCls = CAP[p.cap_status] || 'text-zinc-500'
              return (
                <div key={p.id} className="px-4 py-3 flex items-center gap-4 min-w-[560px]">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{p.label}</p>
                    <p className="text-xs kai-text-subtle truncate">{p.account} · {p.calls_mtd != null ? fmtInt(p.calls_mtd) + ' calls' : ''}</p>
                  </div>
                  <div className="text-right w-24">
                    <p className="text-sm font-semibold">{fmtUsd(p.spend_mtd_usd)}</p>
                    <p className="text-[10px] kai-text-subtle">MTD spend</p>
                  </div>
                  <div className="text-right w-28">
                    {p.cap_status === 'unset'
                      ? <p className={`text-sm font-medium ${capCls}`}>cap unset</p>
                      : <p className={`text-sm font-medium ${capCls}`}>{fmtUsd(p.headroom_usd)} left</p>}
                    <p className="text-[10px] kai-text-subtle">
                      {p.cap_usd != null ? `of ${fmtUsd(p.cap_usd)}` : 'no cap set'}
                      {p.days_to_reset != null ? ` · resets ${p.days_to_reset}d` : ''}
                    </p>
                  </div>
                  <div className={`flex items-center gap-1 text-xs px-2 py-1 rounded-md ${ai.cls} w-24 justify-center`}>
                    <ai.Icon size={12} /> {ai.label}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Recurring / fixed / free */}
      {recurring.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wide kai-text-subtle mb-2">Subscriptions, infra &amp; free tier</h2>
          <div className="kai-card divide-y kai-divider overflow-x-auto">
            {recurring.map(p => {
              const ai = accessInfo(p.access_status)
              return (
                <div key={p.id} className="px-4 py-3 flex items-center gap-4 min-w-[520px]">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{p.label}</p>
                    <p className="text-xs kai-text-subtle truncate">{BILLING_LABEL[p.billing_model] || p.billing_model} · {p.account}</p>
                  </div>
                  <div className="text-right w-24">
                    <p className="text-sm font-semibold">{p.monthly_usd ? fmtUsd(p.monthly_usd) + '/mo' : 'Free'}</p>
                  </div>
                  <div className={`flex items-center gap-1 text-xs px-2 py-1 rounded-md ${ai.cls} w-24 justify-center`}>
                    <ai.Icon size={12} /> {ai.label}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {data?.totals?.combined_note && (
        <p className="text-[11px] kai-text-subtle mt-4 leading-relaxed">{data.totals.combined_note}</p>
      )}
    </div>
  )
}
