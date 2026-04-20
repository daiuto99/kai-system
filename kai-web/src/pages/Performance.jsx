import React, { useState, useEffect, useCallback } from 'react'

const COUNCIL = '/council'
const API     = '/api'

const PROVIDER_COLOR = { anthropic: '#6366f1', openai: '#10a37f', ollama: '#f59e0b' }
const PROVIDER_LABEL = { anthropic: 'Anthropic', openai: 'OpenAI', ollama: 'Local' }

const TIER_STYLE = {
  cloud:   { bg: '#6366f118', color: '#6366f1', label: 'Cloud' },
  local:   { bg: '#f59e0b18', color: '#f59e0b', label: 'Local' },
  premium: { bg: '#ec489918', color: '#ec4899', label: 'Premium' },
}

function Dot({ color, size = 8, pulse }) {
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: '50%',
      background: color, flexShrink: 0,
      boxShadow: pulse ? `0 0 0 3px ${color}30` : 'none',
    }} />
  )
}

function Pill({ tier }) {
  const s = TIER_STYLE[tier] || TIER_STYLE.cloud
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 700,
      background: s.bg, color: s.color,
    }}>{s.label}</span>
  )
}

function SpeedBar({ ms, cloudFast }) {
  if (!ms && !cloudFast) return <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>—</span>
  if (cloudFast) {
    return <span style={{ fontSize: 11, color: '#6366f1', fontWeight: 600 }}>≈{cloudFast}</span>
  }
  const max = 35000
  const pct = Math.min(100, (ms / max) * 100)
  const color = ms < 12000 ? '#10b981' : ms < 25000 ? '#f59e0b' : '#ef4444'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 60, height: 5, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color, fontWeight: 600 }}>{(ms / 1000).toFixed(1)}s</span>
    </div>
  )
}

// ── Section 1: All Models Catalog ─────────────────────────────────────────────
function ModelCatalog({ catalog }) {
  if (!catalog) return null
  const providers = catalog.providers || {}

  return (
    <div style={{ marginBottom: 32 }}>
      <SectionHeader label="All Models" sub="Every model available to the system — health + response time" />
      {Object.entries(providers).map(([pid, pdata]) => {
        const color  = PROVIDER_COLOR[pid] || '#6b7280'
        const ok     = pdata.available
        const models = pdata.models || []
        if (!models.length) return null

        return (
          <div key={pid} style={{ marginBottom: 20 }}>
            {/* Provider header */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8,
              padding: '8px 14px', borderRadius: 10,
              background: ok ? color + '0c' : 'var(--bg-card)',
              border: `1px solid ${ok ? color + '30' : 'var(--border)'}`,
            }}>
              <Dot color={ok ? color : '#ef4444'} pulse={ok} />
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{pdata.label}</span>
              <span style={{ fontSize: 11, color: 'var(--text-subtle)', marginLeft: 2 }}>
                {ok ? `${models.length} model${models.length !== 1 ? 's' : ''} available` : 'Unavailable — key needed'}
              </span>
              <span style={{ marginLeft: 'auto' }}><Pill tier={pdata.tier} /></span>
            </div>

            {/* Model rows */}
            <div style={{ display: 'grid', gap: 4, paddingLeft: 8 }}>
              {models.map(m => {
                const name = m.name || m
                return (
                  <div key={name} style={{
                    display: 'grid',
                    gridTemplateColumns: pid === 'ollama' ? '1fr 60px 80px 90px 80px 100px' : '1fr 60px 80px 90px',
                    alignItems: 'center', gap: 12,
                    padding: '9px 14px', borderRadius: 9,
                    background: 'var(--bg-screen)', border: '1px solid var(--border)',
                    opacity: ok ? 1 : 0.45,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Dot color={ok ? color : '#6b7280'} size={6} />
                      <span style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: 'var(--text-primary)' }}>{name}</span>
                      {m.label && m.label !== name && (
                        <span style={{ fontSize: 10, color: 'var(--text-subtle)' }}>{m.label}</span>
                      )}
                    </div>
                    <Pill tier={m.tier || pdata.tier} />
                    {pid === 'ollama' ? (
                      <SpeedBar ms={m.speed_ms} />
                    ) : (
                      <SpeedBar cloudFast={m.speed_label} />
                    )}
                    <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>
                      {pid === 'ollama' ? (m.tokens_per_sec ? `${m.tokens_per_sec} tok/s` : '—') : '~100 tok/s'}
                    </span>
                    {pid === 'ollama' && (
                      <>
                        <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{m.size_gb}GB · {m.params}</span>
                        <span style={{ fontSize: 10, color: 'var(--text-subtle)' }}>
                          {m.last_benchmarked ? `tested ${m.last_benchmarked.slice(0, 10)}` : `pulled ${m.modified}`}
                        </span>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Section 2: Functions → Model Mapping ─────────────────────────────────────
function FunctionMap({ catalog }) {
  if (!catalog?.function_map?.length) return null

  // Group functions by model
  const byModel = {}
  catalog.function_map.forEach(f => {
    const key = `${f.provider}/${f.model}`
    if (!byModel[key]) byModel[key] = { provider: f.provider, model: f.model, functions: [] }
    byModel[key].functions.push(f)
  })

  return (
    <div style={{ marginBottom: 32 }}>
      <SectionHeader label="Functions & Model Routing" sub="What each model is responsible for in the system" />
      <div style={{ display: 'grid', gap: 10 }}>
        {Object.entries(byModel).map(([key, group]) => {
          const color = PROVIDER_COLOR[group.provider] || '#6b7280'
          const advisors = group.functions.filter(f => f.is_advisor)
          const tools    = group.functions.filter(f => !f.is_advisor)
          return (
            <div key={key} style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0,
              borderRadius: 12, border: `1px solid ${color}30`,
              overflow: 'hidden',
            }}>
              {/* Left: Model identity */}
              <div style={{
                padding: '14px 18px',
                background: color + '0a',
                borderRight: `1px solid ${color}20`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <Dot color={color} size={8} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{group.model}</span>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <Pill tier={group.provider === 'ollama' ? 'local' : 'cloud'} />
                  <span style={{
                    padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 600,
                    background: 'var(--bg-screen)', color: 'var(--text-subtle)',
                    border: '1px solid var(--border)',
                  }}>{PROVIDER_LABEL[group.provider] || group.provider}</span>
                </div>
                {advisors.length > 0 && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-subtle)', marginBottom: 5 }}>Advisors</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {advisors.map(f => (
                        <span key={f.function} style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 20,
                          background: color + '18', color, fontWeight: 600,
                        }}>{f.function.replace(' Chat', '')}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Right: Functions list */}
              <div style={{ padding: '14px 18px', background: 'var(--bg-screen)' }}>
                {tools.length > 0 && (
                  <>
                    <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-subtle)', marginBottom: 8 }}>Functions</div>
                    {tools.map(f => (
                      <div key={f.function} style={{ marginBottom: 7 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 1 }}>{f.function}</div>
                        {f.description && (
                          <div style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{f.description}</div>
                        )}
                      </div>
                    ))}
                  </>
                )}
                {tools.length === 0 && advisors.length > 0 && (
                  <div style={{ fontSize: 12, color: 'var(--text-subtle)', paddingTop: 4 }}>
                    Conversational advisor{advisors.length > 1 ? 's' : ''} — no system tools
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Section 3: Benchmark Runner ───────────────────────────────────────────────
function Benchmarks({ catalog, onReload }) {
  const [running, setRunning] = useState({})
  if (!catalog?.providers?.ollama?.available) return null
  const models = catalog.providers.ollama.models || []
  const benches = catalog.benchmarks || {}

  const run = async (model) => {
    setRunning(r => ({ ...r, [model]: true }))
    await fetch(`${COUNCIL}/models/benchmarks/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    })
    setRunning(r => ({ ...r, [model]: false }))
    onReload()
  }

  return (
    <div style={{ marginBottom: 32 }}>
      <SectionHeader label="Local Model Benchmarks" sub="Speed tests on 4-core CPU worker · click Run to re-test" />
      {/* Header row */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 110px 90px 70px 120px 60px',
        gap: 12, padding: '5px 14px',
        fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--text-subtle)',
      }}>
        <span>Model</span><span>Speed</span><span>Latency</span><span>tok/s</span><span>Last tested</span><span />
      </div>
      {models.map(m => {
        const name = typeof m === 'string' ? m : m.name
        const b = benches[name] || benches[name?.split(':')[0]]
        const isRunning = running[name]
        const color = b?.status === 'ok' ? '#10b981' : b ? '#ef4444' : '#6b7280'
        return (
          <div key={name} style={{
            display: 'grid', gridTemplateColumns: '1fr 110px 90px 70px 120px 60px',
            alignItems: 'center', gap: 12,
            padding: '10px 14px', borderRadius: 10, marginBottom: 4,
            background: 'var(--bg-screen)', border: '1px solid var(--border)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Dot color={color} size={6} />
              <span style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: 'var(--text-primary)' }}>{name}</span>
            </div>
            <SpeedBar ms={b?.avg_ms} />
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{b?.avg_ms ? `${(b.avg_ms / 1000).toFixed(1)}s/turn` : '—'}</span>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{b?.tokens_per_sec ?? '—'}</span>
            <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>
              {b?.last_run ? b.last_run.replace('T', ' ').slice(0, 16) : 'Not tested'}
            </span>
            <button onClick={() => run(name)} disabled={isRunning} style={{
              padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600, border: 'none',
              background: isRunning ? '#f59e0b' : 'var(--bg-card)',
              color: isRunning ? '#fff' : 'var(--text-subtle)', cursor: isRunning ? 'not-allowed' : 'pointer',
              border: '1px solid var(--border)',
            }}>{isRunning ? '…' : 'Run'}</button>
          </div>
        )
      })}
    </div>
  )
}

// ── Section 4: Usage ──────────────────────────────────────────────────────────
function Usage({ days }) {
  const [period, setPeriod] = useState('day')
  const PERIODS = ['hour', 'day', 'week', 'month']

  const rows = (() => {
    const now = new Date()
    if (period === 'hour') {
      const todayStr = now.toISOString().slice(0, 10)
      const today = days.find(d => d.date === todayStr)
      if (!today?.hours) return []
      const hourNow = now.getHours()
      return Array.from({ length: hourNow + 1 }, (_, h) => {
        const key = String(h).padStart(2, '0')
        const hd = today.hours?.[key] || {}
        return { label: `${key}:00`, calls: hd.calls || 0, cost: hd.cost_usd || 0, input: hd.input || 0, output: hd.output || 0, by_model: hd.by_model || {} }
      }).reverse()
    }
    if (period === 'day') {
      return [...days].slice(-14).reverse().map(d => ({
        label: d.date, calls: d.calls, cost: d.cost_usd, input: d.input, output: d.output,
        by_model: d.by_model || d.by_provider || {},
      }))
    }
    if (period === 'week') {
      const weeks = {}
      days.forEach(d => {
        const dt = new Date(d.date)
        const ws = new Date(dt); ws.setDate(dt.getDate() - dt.getDay())
        const key = ws.toISOString().slice(0, 10)
        if (!weeks[key]) weeks[key] = { label: `w/o ${key}`, calls: 0, cost: 0, input: 0, output: 0, by_model: {} }
        weeks[key].calls += d.calls; weeks[key].cost += d.cost_usd || 0
        weeks[key].input += d.input; weeks[key].output += d.output
        Object.entries(d.by_model || d.by_provider || {}).forEach(([k, v]) => {
          weeks[key].by_model[k] = (weeks[key].by_model[k] || 0) + v
        })
      })
      return Object.values(weeks).reverse()
    }
    if (period === 'month') {
      const months = {}
      days.forEach(d => {
        const key = d.date.slice(0, 7)
        if (!months[key]) months[key] = { label: key, calls: 0, cost: 0, input: 0, output: 0, by_model: {} }
        months[key].calls += d.calls; months[key].cost += d.cost_usd || 0
        months[key].input += d.input; months[key].output += d.output
        Object.entries(d.by_model || d.by_provider || {}).forEach(([k, v]) => {
          months[key].by_model[k] = (months[key].by_model[k] || 0) + v
        })
      })
      return Object.values(months).reverse()
    }
    return []
  })()

  const totalCalls = rows.reduce((s, r) => s + r.calls, 0)
  const totalCost  = rows.reduce((s, r) => s + (r.cost || 0), 0)

  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <SectionHeader label="Usage" sub={null} noMargin />
        <div style={{ display: 'flex', gap: 2, background: 'var(--bg-card)', padding: 3, borderRadius: 8, border: '1px solid var(--border)' }}>
          {PERIODS.map(p => (
            <button key={p} onClick={() => setPeriod(p)} style={{
              padding: '4px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
              fontSize: 11, fontWeight: 600, textTransform: 'capitalize',
              background: period === p ? 'var(--accent)' : 'transparent',
              color: period === p ? '#fff' : 'var(--text-secondary)',
            }}>{p}</button>
          ))}
        </div>
      </div>

      {/* Summary */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 10 }}>
        {[
          { label: 'Calls', value: totalCalls },
          { label: 'Cost', value: totalCost > 0 ? `$${totalCost.toFixed(4)}` : 'Free', color: totalCost > 0 ? '#f59e0b' : '#10b981' },
        ].map(s => (
          <div key={s.label} style={{ padding: '10px 16px', borderRadius: 10, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color || 'var(--text-primary)' }}>{s.value}</div>
          </div>
        ))}
      </div>

      {rows.length === 0 ? (
        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-subtle)', fontSize: 13 }}>No usage data</div>
      ) : rows.map((row, i) => (
        <div key={i} style={{
          padding: '9px 14px', borderRadius: 10, marginBottom: 4,
          background: 'var(--bg-screen)', border: '1px solid var(--border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: 12, fontFamily: 'monospace', fontWeight: 600, color: 'var(--text-primary)' }}>{row.label}</span>
            <div style={{ display: 'flex', gap: 14 }}>
              <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{row.calls} call{row.calls !== 1 ? 's' : ''}</span>
              <span style={{ fontSize: 11, color: 'var(--text-subtle)' }}>{(((row.input || 0) + (row.output || 0)) / 1000).toFixed(1)}K tok</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: (row.cost || 0) > 0 ? '#f59e0b' : '#10b981' }}>
                {(row.cost || 0) > 0 ? `$${row.cost.toFixed(4)}` : 'Free'}
              </span>
            </div>
          </div>
          {Object.entries(row.by_model || {}).map(([mkey, count]) => {
            const [prov] = mkey.split('/')
            const color = PROVIDER_COLOR[prov] || '#6b7280'
            return (
              <div key={mkey} style={{ display: 'flex', gap: 8, paddingLeft: 8, marginTop: 3, borderLeft: `2px solid ${color}40` }}>
                <span style={{ fontSize: 10, fontWeight: 700, color, minWidth: 48 }}>
                  {prov === 'ollama' ? 'LOCAL' : prov.toUpperCase()}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-subtle)', fontFamily: 'monospace', flex: 1 }}>{mkey.split('/').slice(1).join('/')}</span>
                <span style={{ fontSize: 10, color: 'var(--text-subtle)' }}>{count} call{count !== 1 ? 's' : ''}</span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ── Shared ────────────────────────────────────────────────────────────────────
function SectionHeader({ label, sub, noMargin }) {
  return (
    <div style={{ marginBottom: noMargin ? 0 : 12 }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{label}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text-subtle)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function Performance() {
  const [catalog, setCatalog] = useState(null)
  const [usage, setUsage]     = useState(null)

  const load = useCallback(() => {
    fetch(`${COUNCIL}/models/catalog`).then(r => r.json()).then(setCatalog).catch(() => {})
    fetch(`${API}/token-usage`).then(r => r.json()).then(setUsage).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  // Overall health
  const ant_ok    = catalog?.providers?.anthropic?.available
  const ollama_ok = catalog?.providers?.ollama?.available
  const healthColor = ant_ok && ollama_ok ? '#10b981' : ant_ok ? '#f59e0b' : '#ef4444'
  const healthLabel = ant_ok && ollama_ok ? 'All Systems Operational' : ant_ok ? 'Local Offline' : 'Degraded'

  const days = usage?.days || []

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: 'var(--bg-screen)', padding: '24px 28px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Performance & Models
          </h1>
          <p style={{ margin: '3px 0 0', fontSize: 13, color: 'var(--text-tertiary)' }}>
            All models · function routing · speed benchmarks · usage
          </p>
        </div>
        {catalog && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px',
            borderRadius: 20, border: `1px solid ${healthColor}44`, background: healthColor + '12',
          }}>
            <Dot color={healthColor} pulse />
            <span style={{ fontSize: 12, fontWeight: 600, color: healthColor }}>{healthLabel}</span>
          </div>
        )}
      </div>

      <ModelCatalog catalog={catalog} />
      <FunctionMap catalog={catalog} />
      <Benchmarks catalog={catalog} onReload={load} />
      {days.length > 0 && <Usage days={days} />}
    </div>
  )
}
