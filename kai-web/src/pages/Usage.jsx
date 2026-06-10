import { useState, useEffect, useMemo } from 'react'
import { api } from '../lib/api'
import { DollarSign, Activity, TrendingUp, RefreshCw, Zap, ChevronDown, ChevronRight } from 'lucide-react'

const RANGES = [
  { id: 'today',  label: 'Today',     days: 1   },
  { id: '7d',     label: '7d',        days: 7   },
  { id: '30d',    label: '30d',       days: 30  },
  { id: '365d',   label: '365d',      days: 365 },
  { id: 'all',    label: 'All',       days: null },
]

const TABS = ['advisor', 'model', 'provider', 'trigger', 'function', 'hour']

function fmtUsd(n) {
  if (n == null || isNaN(n)) return '$0'
  if (n === 0) return '$0'
  if (Math.abs(n) < 0.01) return '$' + n.toFixed(4)
  if (Math.abs(n) < 1)    return '$' + n.toFixed(3)
  if (Math.abs(n) < 100)  return '$' + n.toFixed(2)
  return '$' + Math.round(n).toLocaleString()
}

function fmtInt(n) {
  if (n == null) return '—'
  return n.toLocaleString()
}

function fmtKilo(n) {
  if (n == null || n === 0) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(1) + 'k'
  return (n / 1_000_000).toFixed(2) + 'M'
}

// Coerce legacy int values to the new {calls, cost_usd, input, output} dict shape
function asEntry(v) {
  if (typeof v === 'number') return { calls: v, cost_usd: 0, input: 0, output: 0 }
  if (v && typeof v === 'object') {
    return {
      calls: v.calls || 0,
      cost_usd: v.cost_usd || 0,
      input: v.input || 0,
      output: v.output || 0,
    }
  }
  return { calls: 0, cost_usd: 0, input: 0, output: 0 }
}

function mergeEntries(target, key, src) {
  const cur = target[key] || { calls: 0, cost_usd: 0, input: 0, output: 0 }
  cur.calls    += src.calls
  cur.cost_usd += src.cost_usd
  cur.input    += src.input
  cur.output   += src.output
  target[key] = cur
}

export default function Usage() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [range, setRange]     = useState('30d')
  const [tab, setTab]         = useState('advisor')
  const [expandedDate, setExpandedDate] = useState(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const d = await api.getTokenUsage()
      setData(d)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const sortedDays = useMemo(() => {
    if (!data?.days) return []
    return [...data.days].sort((a, b) => (a.date < b.date ? -1 : 1))
  }, [data])

  const days = useMemo(() => {
    const rangeCfg = RANGES.find(r => r.id === range)
    if (!rangeCfg.days) return sortedDays
    return sortedDays.slice(-rangeCfg.days)
  }, [sortedDays, range])

  const agg = useMemo(() => {
    const sum = {
      cost: 0, calls: 0, input: 0, output: 0,
      by_advisor: {}, by_model: {}, by_provider: {}, by_trigger: {},
      by_hour: {},
    }
    for (const d of days) {
      sum.cost   += d.cost_usd || 0
      sum.calls  += d.calls || 0
      sum.input  += d.input || 0
      sum.output += d.output || 0
      for (const [k, v] of Object.entries(d.by_advisor  || {})) mergeEntries(sum.by_advisor,  k, asEntry(v))
      for (const [k, v] of Object.entries(d.by_model    || {})) mergeEntries(sum.by_model,    k, asEntry(v))
      for (const [k, v] of Object.entries(d.by_provider || {})) mergeEntries(sum.by_provider, k, asEntry(v))
      for (const [k, v] of Object.entries(d.by_trigger  || {})) mergeEntries(sum.by_trigger,  k, asEntry(v))
      for (const [hr, h] of Object.entries(d.hours || {})) {
        const cur = sum.by_hour[hr] || { calls: 0, cost_usd: 0, input: 0, output: 0 }
        cur.calls    += h.calls || 0
        cur.cost_usd += h.cost_usd || 0
        cur.input    += h.input || 0
        cur.output   += h.output || 0
        sum.by_hour[hr] = cur
      }
    }
    return sum
  }, [days])

  // Group by_trigger by source prefix (before first ":") for the "trigger" tab
  const triggerSources = useMemo(() => {
    const groups = {}
    for (const [k, v] of Object.entries(agg.by_trigger)) {
      const source = k.split(':')[0] || k
      mergeEntries(groups, source, v)
    }
    return groups
  }, [agg])

  const topByCost = (src) => {
    const arr = Object.entries(src).map(([key, v]) => ({ key, ...v }))
    return arr.sort((a, b) => b.cost_usd - a.cost_usd)
  }
  const topAdvisorByCost = useMemo(() => topByCost(agg.by_advisor)[0], [agg])
  const topModelByCost   = useMemo(() => topByCost(agg.by_model)[0], [agg])

  const maxDailyCost = useMemo(() => Math.max(0.01, ...days.map(d => d.cost_usd || 0)), [days])

  // Planning metrics
  const planning = useMemo(() => {
    // 30d projection: avg of last-30d daily, × 30
    const last30 = sortedDays.slice(-30)
    const avgDaily30 = last30.length ? (last30.reduce((a, d) => a + (d.cost_usd || 0), 0) / last30.length) : 0
    const projectedMonth = avgDaily30 * 30
    // Week-over-week growth
    const thisWeek = sortedDays.slice(-7).reduce((a, d) => a + (d.cost_usd || 0), 0)
    const lastWeek = sortedDays.slice(-14, -7).reduce((a, d) => a + (d.cost_usd || 0), 0)
    const wow = lastWeek > 0 ? ((thisWeek / lastWeek) - 1) * 100 : null
    // 7d cost-per-call trend
    const cpc7 = sortedDays.slice(-7).map(d => ({ date: d.date, cpc: d.calls ? (d.cost_usd / d.calls) : 0 }))
    return { projectedMonth, wow, cpc7, thisWeek, lastWeek }
  }, [sortedDays])

  const breakdownData = useMemo(() => {
    if (tab === 'hour') {
      const items = []
      for (let h = 0; h < 24; h++) {
        const key = String(h)
        const cur = agg.by_hour[key] || { calls: 0, cost_usd: 0, input: 0, output: 0 }
        items.push({ key: `${h}:00`, ...cur })
      }
      const maxCost = Math.max(0.01, ...items.map(i => i.cost_usd))
      return items.map(i => ({ ...i, pct: (i.cost_usd / maxCost) * 100 }))
    }
    const src = tab === 'advisor'  ? agg.by_advisor
              : tab === 'model'    ? agg.by_model
              : tab === 'provider' ? agg.by_provider
              : tab === 'trigger'  ? triggerSources
              :                      agg.by_trigger  // 'function'
    const entries = Object.entries(src).map(([key, v]) => ({ key, ...v }))
      .sort((a, b) => b.cost_usd - a.cost_usd)
    const maxCost = Math.max(0.0001, ...entries.map(i => i.cost_usd))
    return entries.map(i => ({ ...i, pct: (i.cost_usd / maxCost) * 100 }))
  }, [tab, agg, triggerSources])

  return (
    <div className="max-w-4xl mx-auto px-8 py-10">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <DollarSign size={20} className="kai-text-subtle" />
            Usage
          </h1>
          <p className="kai-text-subtle text-sm mt-1">
            API cost across all connected providers. {data?.days?.length ? `${data.days.length} days tracked.` : ''}
          </p>
        </div>
        <button onClick={load} className="btn-ghost flex items-center gap-1.5 text-xs">
          <RefreshCw size={12} />
        </button>
      </div>

      {/* Range selector */}
      <div className="flex gap-1.5 mb-6 flex-wrap">
        {RANGES.map(r => (
          <button
            key={r.id}
            onClick={() => { setRange(r.id); setExpandedDate(null) }}
            className={`text-xs px-3 py-1 rounded-full border transition-colors
              ${range === r.id
                ? 'border-kai-blue text-kai-blue bg-kai-blue/10'
                : 'border-white/10 kai-text-subtle hover:border-white/20'}`}
          >
            {r.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="kai-card px-5 py-4 text-sm text-red-400 mb-4">
          Failed to load usage: {error}
        </div>
      )}

      {loading ? (
        <div className="kai-card px-5 py-12 text-center kai-text-subtle text-sm">
          Loading usage…
        </div>
      ) : !data ? null : (
        <>
          {/* Top-line cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <Card icon={<DollarSign size={14} />} label="Total spend"     value={fmtUsd(agg.cost)} />
            <Card icon={<Activity size={14} />}    label="Calls"           value={fmtInt(agg.calls)} />
            <Card icon={<Zap size={14} />}         label="Avg cost / call" value={fmtUsd(agg.calls ? agg.cost / agg.calls : 0)} />
            <Card icon={<TrendingUp size={14} />}  label="Top advisor ($)"
              value={topAdvisorByCost ? topAdvisorByCost.key : '—'}
              sub={topAdvisorByCost ? `${fmtUsd(topAdvisorByCost.cost_usd)} · ${topAdvisorByCost.calls} calls` : ''} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <Card label="Tokens in"  value={fmtKilo(agg.input)} />
            <Card label="Tokens out" value={fmtKilo(agg.output)} />
            <Card label="Top model ($)"
                  value={topModelByCost ? topModelByCost.key.split('/').pop() : '—'}
                  sub={topModelByCost ? `${fmtUsd(topModelByCost.cost_usd)} · ${topModelByCost.calls} calls` : ''} />
            <Card label="Days in range" value={String(days.length)} />
          </div>

          {/* Planning cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
            <Card icon={<TrendingUp size={14} />} label="Projected / month" value={fmtUsd(planning.projectedMonth)}
                  sub="based on 30d avg" />
            <Card icon={<TrendingUp size={14} />} label="Week-over-week"
                  value={planning.wow == null ? '—' : `${planning.wow > 0 ? '+' : ''}${planning.wow.toFixed(1)}%`}
                  sub={`${fmtUsd(planning.thisWeek)} vs ${fmtUsd(planning.lastWeek)}`} />
            <Card label="Avg cost/call · 7d"
                  value={
                    <Sparkline data={planning.cpc7.map(p => p.cpc)} />
                  }
                  sub={planning.cpc7.length ? fmtUsd(planning.cpc7[planning.cpc7.length - 1]?.cpc || 0) + ' last' : ''} />
          </div>

          {/* Daily cost chart */}
          <div className="kai-card px-5 py-5 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-medium">Daily cost</h2>
              <span className="text-xs kai-text-subtle">peak {fmtUsd(maxDailyCost)} · click bar to expand</span>
            </div>
            {days.length === 0 ? (
              <p className="text-xs kai-text-subtle py-6 text-center">No days in range.</p>
            ) : (
              <div className="flex items-end gap-1" style={{ height: 128 }}>
                {days.map(d => {
                  const h = Math.max(2, ((d.cost_usd || 0) / maxDailyCost) * 128)
                  const isActive = expandedDate === d.date
                  return (
                    <button
                      key={d.date}
                      onClick={() => setExpandedDate(isActive ? null : d.date)}
                      className="flex-1 flex flex-col justify-end group cursor-pointer h-full"
                      style={{ minWidth: 4 }}
                      title={`${d.date}: ${fmtUsd(d.cost_usd)} (${d.calls} calls) — click to expand`}
                    >
                      <div className={`w-full rounded-t transition-colors ${isActive ? 'bg-kai-blue' : 'bg-kai-blue/70 group-hover:bg-kai-blue'}`}
                           style={{ height: `${h}px`, minHeight: 2 }} />
                    </button>
                  )
                })}
              </div>
            )}
            <div className="flex justify-between text-[10px] kai-text-subtle mt-2">
              <span>{days[0]?.date || ''}</span>
              <span>{days[days.length - 1]?.date || ''}</span>
            </div>

            {/* Drill-down for clicked day */}
            {expandedDate && (() => {
              const day = days.find(d => d.date === expandedDate)
              if (!day) return null
              return (
                <div className="mt-5 pt-5 border-t border-white/10">
                  <div className="flex items-center justify-between mb-3">
                    <div className="text-sm font-medium">{day.date} · {fmtUsd(day.cost_usd)} · {day.calls} calls</div>
                    <button onClick={() => setExpandedDate(null)} className="kai-text-subtle text-xs">close</button>
                  </div>
                  <DayDrillDown day={day} />
                </div>
              )
            })()}
          </div>

          {/* Breakdown tabs */}
          <div className="kai-card px-5 py-5">
            <div className="mb-4">
              <h2 className="text-sm font-medium mb-3">Breakdown</h2>
              <div className="flex gap-1.5 flex-wrap">
                {TABS.map(t => (
                  <button key={t} onClick={() => setTab(t)}
                    className={`text-xs px-3 py-1 rounded-full border transition-colors capitalize
                      ${tab === t
                        ? 'border-kai-blue text-kai-blue bg-kai-blue/10'
                        : 'border-white/10 kai-text-subtle hover:border-white/20'}`}
                  >
                    {t === 'hour' ? 'Hour of day' : t}
                  </button>
                ))}
              </div>
            </div>
            {breakdownData.length === 0 ? (
              <p className="text-xs kai-text-subtle py-6 text-center">No data in range.</p>
            ) : (
              <div className="space-y-2">
                {breakdownData.map(item => (
                  <div key={item.key} className="flex items-center gap-3 text-xs">
                    <div className="w-40 truncate kai-text-subtle" title={item.key}>{item.key}</div>
                    <div className="flex-1 relative h-5 rounded bg-white/5 overflow-hidden">
                      <div className="absolute inset-y-0 left-0 bg-kai-blue/60 rounded"
                           style={{ width: `${item.pct}%` }} />
                    </div>
                    <div className="w-44 text-right tabular-nums text-[11px]">
                      <span className="font-medium">{fmtUsd(item.cost_usd)}</span>
                      <span className="kai-text-subtle"> · {item.calls} · {fmtKilo(item.input)}/{fmtKilo(item.output)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="text-[10px] kai-text-subtle mt-4">
              Sorted by cost. Format: <span className="font-medium">cost</span> · calls · tokens in / out.
              Historical days before USAGE-4 (2026-06-09) have estimated per-row cost from proportional distribution.
            </p>
          </div>
        </>
      )}
    </div>
  )
}

function Card({ icon, label, value, sub }) {
  return (
    <div className="kai-card px-4 py-3">
      <div className="flex items-center gap-1.5 kai-text-subtle text-[11px] uppercase tracking-wide">
        {icon}
        <span>{label}</span>
      </div>
      <div className="text-lg font-semibold mt-1 tabular-nums">{value}</div>
      {sub && <div className="text-[10px] kai-text-subtle mt-0.5">{sub}</div>}
    </div>
  )
}

function Sparkline({ data }) {
  if (!data || !data.length) return <span className="text-sm kai-text-subtle">—</span>
  const max = Math.max(0.0001, ...data)
  return (
    <div className="flex items-end gap-0.5 h-7">
      {data.map((v, i) => (
        <div key={i} className="w-1.5 bg-kai-blue/60 rounded-t"
             style={{ height: `${Math.max(8, (v / max) * 100)}%` }} />
      ))}
    </div>
  )
}

function DayDrillDown({ day }) {
  function topN(src, n = 5) {
    return Object.entries(src || {})
      .map(([key, v]) => ({ key, ...(typeof v === 'number' ? { calls: v, cost_usd: 0, input: 0, output: 0 } : v) }))
      .sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0))
      .slice(0, n)
  }
  const hours = []
  for (let h = 0; h < 24; h++) {
    const cur = day.hours?.[String(h)] || { calls: 0, cost_usd: 0 }
    hours.push({ hour: h, cost: cur.cost_usd || 0, calls: cur.calls || 0 })
  }
  const maxHourCost = Math.max(0.0001, ...hours.map(h => h.cost))

  return (
    <div className="space-y-4">
      {/* Hour-of-day mini-chart */}
      <div>
        <div className="text-[11px] uppercase tracking-wide kai-text-subtle mb-2">Hour of day</div>
        <div className="flex items-end gap-0.5 h-16">
          {hours.map(h => (
            <div key={h.hour} className="flex-1 flex flex-col items-center justify-end"
                 title={`${h.hour}:00 · ${fmtUsd(h.cost)} · ${h.calls} calls`}>
              <div className="w-full rounded-t bg-kai-blue/60"
                   style={{ height: `${Math.max(2, (h.cost / maxHourCost) * 100)}%` }} />
            </div>
          ))}
        </div>
        <div className="flex justify-between text-[9px] kai-text-subtle mt-1">
          <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
        </div>
      </div>

      {/* Top 5 grids */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <DrillList title="Top advisors"  rows={topN(day.by_advisor)} />
        <DrillList title="Top models"    rows={topN(day.by_model)} />
        <DrillList title="Top triggers"  rows={topN(day.by_trigger)} />
        <DrillList title="Top providers" rows={topN(day.by_provider)} />
      </div>
    </div>
  )
}

function DrillList({ title, rows }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide kai-text-subtle mb-1.5">{title}</div>
      {rows.length === 0 ? (
        <p className="text-xs kai-text-subtle">no data</p>
      ) : (
        <div className="space-y-1">
          {rows.map(r => (
            <div key={r.key} className="flex justify-between text-xs tabular-nums">
              <span className="truncate pr-3" title={r.key}>{r.key}</span>
              <span className="kai-text-subtle flex-shrink-0">{fmtUsd(r.cost_usd)} · {r.calls}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
