import React, { useState, useEffect } from 'react'

const API = '/api'
const COUNCIL = '/council'

const PROVIDER_COLORS = {
  anthropic: '#6366f1',
  openai:    '#10a37f',
  ollama:    '#f59e0b',
}
const PROVIDER_LABELS = {
  anthropic: 'Anthropic Claude',
  openai:    'OpenAI GPT',
  ollama:    'Ollama (Local)',
}
const MODEL_OPTIONS = {
  anthropic: ['claude-sonnet-4-5', 'claude-sonnet-4-6', 'claude-opus-4-6', 'claude-haiku-4-5-20251001'],
  openai:    ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o1-mini'],
  ollama:    ['llama3.2', 'llama3.1:8b', 'mistral', 'phi3', 'gemma2:9b'],
}

function ProviderBadge({ provider, available }) {
  const color = PROVIDER_COLORS[provider] || '#6b7280'
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
      background: color + '18', color,
      border: `1px solid ${color}44`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: available ? '#10b981' : '#ef4444',
        flexShrink: 0,
      }} />
      {PROVIDER_LABELS[provider] || provider}
      {!available && <span style={{ opacity: 0.7 }}> — key needed</span>}
    </span>
  )
}

function AdvisorRow({ id, cfg, ollamaModels, onSave }) {
  const [editing, setEditing] = useState(false)
  const [provider, setProvider] = useState(cfg.provider || 'anthropic')
  const [model, setModel] = useState(cfg.model || 'claude-sonnet-4-5')
  const [saving, setSaving] = useState(false)

  const modelList = provider === 'ollama'
    ? [...(MODEL_OPTIONS.ollama), ...ollamaModels.filter(m => !MODEL_OPTIONS.ollama.includes(m))]
    : (MODEL_OPTIONS[provider] || [])

  const handleSave = async () => {
    setSaving(true)
    await fetch(`${COUNCIL}/models/config/advisor/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model }),
    })
    setSaving(false)
    setEditing(false)
    onSave()
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '12px 16px', borderRadius: 10,
      background: 'var(--bg-screen)', border: '1px solid var(--border)',
      marginBottom: 6,
    }}>
      <div style={{
        width: 8, height: 8, borderRadius: '50%',
        background: PROVIDER_COLORS[cfg.provider] || '#6b7280', flexShrink: 0,
      }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
          {id}
        </div>
        {cfg.notes && (
          <div style={{ fontSize: 11, color: 'var(--text-subtle)', marginTop: 1 }}>{cfg.notes}</div>
        )}
      </div>

      {editing ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select value={provider} onChange={e => { setProvider(e.target.value); setModel(MODEL_OPTIONS[e.target.value]?.[0] || '') }}
            style={{ fontSize: 12, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)' }}>
            {Object.keys(PROVIDER_LABELS).map(p => <option key={p} value={p}>{PROVIDER_LABELS[p]}</option>)}
          </select>
          <select value={model} onChange={e => setModel(e.target.value)}
            style={{ fontSize: 12, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text-primary)' }}>
            {modelList.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <button onClick={handleSave} disabled={saving} style={{
            padding: '4px 12px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: 'var(--accent)', color: '#fff', fontSize: 12, fontWeight: 600,
          }}>{saving ? '...' : 'Save'}</button>
          <button onClick={() => setEditing(false)} style={{
            padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)',
            background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text-secondary)',
          }}>Cancel</button>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>{cfg.model}</span>
          <ProviderBadge provider={cfg.provider} available={true} />
          <button onClick={() => setEditing(true)} style={{
            padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 500,
            border: '1px solid var(--border)', background: 'none', cursor: 'pointer',
            color: 'var(--text-secondary)',
          }}>Change</button>
        </div>
      )}
    </div>
  )
}

export default function Models() {
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState(null)
  const [usage, setUsage] = useState(null)

  const load = () => {
    fetch(`${COUNCIL}/models/config`).then(r => r.json()).then(setConfig).catch(() => {})
    fetch(`${COUNCIL}/models/status`).then(r => r.json()).then(setStatus).catch(() => {})
    fetch(`${API}/token-usage`).then(r => r.json()).then(setUsage).catch(() => {})
  }

  useEffect(() => { load() }, [])

  const ollamaModels = status?.providers?.ollama?.models || []
  const advisors = config?.advisors || {}

  const providerUsageToday = (() => {
    if (!usage?.days?.length) return {}
    const today = usage.days[usage.days.length - 1]
    return today?.by_provider || {}
  })()

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: 'var(--bg-screen)', padding: '24px' }}>
      <h1 style={{ margin: '0 0 4px', fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
        Models
      </h1>
      <p style={{ margin: '0 0 24px', fontSize: 13, color: 'var(--text-tertiary)' }}>
        Configure which AI model powers each advisor. Changes take effect immediately.
      </p>

      {/* Provider Status */}
      {status && (
        <div style={{ marginBottom: 28 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: 10 }}>
            Provider Status
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {Object.entries(status.providers || {}).map(([pid, pdata]) => (
              <div key={pid} style={{
                padding: '10px 16px', borderRadius: 10, border: '1px solid var(--border)',
                background: 'var(--bg-card)', minWidth: 180,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: pdata.available ? '#10b981' : '#ef4444', flexShrink: 0,
                  }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{pdata.label}</span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-subtle)' }}>
                  {pdata.available
                    ? (pid === 'ollama' ? `${(pdata.models||[]).length} model(s) loaded` : 'Connected')
                    : (pdata.error || 'API key needed')}
                </div>
                {pid === 'ollama' && pdata.available && pdata.models?.length > 0 && (
                  <div style={{ fontSize: 10, color: 'var(--text-subtle)', marginTop: 4 }}>
                    {pdata.models.join(', ')}
                  </div>
                )}
                {Object.entries(providerUsageToday).filter(([k]) => k.startsWith(pid)).map(([k, v]) => (
                  <div key={k} style={{ fontSize: 10, color: PROVIDER_COLORS[pid] || 'var(--accent)', marginTop: 2 }}>
                    {v} call{v !== 1 ? 's' : ''} today
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Advisor Model Config */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: 10 }}>
          Advisor Models
        </div>
        {Object.entries(advisors).map(([id, cfg]) => (
          <AdvisorRow key={id} id={id} cfg={cfg} ollamaModels={ollamaModels} onSave={load} />
        ))}
      </div>

      {/* Usage by Model Today */}
      {Object.keys(providerUsageToday).length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)', marginBottom: 10 }}>
            Today's Usage by Model
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {Object.entries(providerUsageToday).map(([key, count]) => {
              const [prov] = key.split('/')
              return (
                <div key={key} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '8px 14px', borderRadius: 8, background: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                }}>
                  <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{key}</span>
                  <span style={{
                    fontSize: 11, fontWeight: 700, color: PROVIDER_COLORS[prov] || 'var(--accent)',
                    background: (PROVIDER_COLORS[prov] || '#6366f1') + '18',
                    padding: '2px 8px', borderRadius: 10,
                  }}>{count} call{count !== 1 ? 's' : ''}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
