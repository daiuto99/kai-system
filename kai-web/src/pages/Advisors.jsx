// Advisors (was "Chat") — talk to the LOCAL advisors + manage all advisors.
// This page exists because Ember + Doc are LOCAL-ONLY (they never leave Leo's
// environment, so they can't live on Buzz). KAI/Sky/Roads/Coach chat happens on
// Buzz; only the local pair is chatted with here. Text-forward / Vercel-style to
// match Now.jsx: hairlines not boxes, mono labels, terra accent. Avatar-forward
// cards under mono section labels, hairline-separated, responsive.
// Layout row order: 1) KAI  2) Doc + Ember  3) Roads + Sky  4) Coach.
// Fail-soft: a missing avatar falls back to initials; nothing errors to a wall.
//
// INTERACTION (KAI-1319): "Manage" opens a real edit surface backed by the live
// advisor routes — GET/PUT /advisors/{id} edits the persona (behavior/knowledge),
// GET/PUT /advisors/{id}/assets edits model + status. It replaces the earlier
// structural stub that just navigated to the chat surface.
import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessageSquare, SlidersHorizontal, X, Check, Loader2 } from 'lucide-react'
import { getAdvisor } from '../lib/advisors'
import { api } from '../lib/api'

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

// ── a labelled field wrapper for the Manage drawer ────────────────────────────
function Field({ label, hint, children }) {
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{
        fontSize: 10.5, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase',
        color: 'var(--text-tertiary)', fontFamily: MONO,
      }}>{label}</div>
      {hint && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.45 }}>{hint}</div>}
      <div style={{ marginTop: 8 }}>{children}</div>
    </div>
  )
}

const inputStyle = {
  width: '100%', boxSizing: 'border-box', fontFamily: MONO, fontSize: 12.5,
  color: 'var(--text-primary)', background: 'var(--bg-input)',
  border: '1px solid var(--border)', borderRadius: 8, padding: '9px 11px',
  letterSpacing: '0.02em', outline: 'none',
}

// ── Manage drawer — real editing of persona (behavior) + model/status ─────────
// Right-side sheet. Loads the live persona markdown + assets, saves via PUT.
// Fail-soft: a missing persona hides the editor but still allows settings edits;
// a failed save surfaces a hairline error and never loses the typed content.
function ManageDrawer({ id, name, onClose }) {
  const [content, setContent] = useState(null)   // null=loading, string once loaded
  const [assets, setAssets] = useState(null)      // null=loading
  const [personaMissing, setPersonaMissing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    api.get(`/advisors/${id}`)
      .then((d) => { if (alive) setContent(d.content || '') })
      .catch(() => { if (alive) { setPersonaMissing(true); setContent('') } })
    api.get(`/advisors/${id}/assets`)
      .then((d) => { if (alive) setAssets(d.assets || {}) })
      .catch(() => { if (alive) setAssets({}) })
    return () => { alive = false }
  }, [id])

  const close = useCallback(() => { if (!saving) onClose() }, [saving, onClose])

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [close])

  const setAsset = (k, v) => setAssets((a) => ({ ...(a || {}), [k]: v }))

  const save = async () => {
    setSaving(true); setErr(''); setSaved(false)
    try {
      if (!personaMissing) await api.put(`/advisors/${id}`, { content: content || '' })
      if (assets) {
        await api.put(`/advisors/${id}/assets`, {
          status: assets.status,
          default_model: assets.default_model,
          research_model: assets.research_model,
          sidekick_enabled: !!assets.sidekick_enabled,
        })
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 2200)
    } catch (e) {
      setErr(`Save failed — ${e.message || 'unknown error'}`)
    } finally {
      setSaving(false)
    }
  }

  const loading = content === null || assets === null

  return (
    <div
      onClick={close}
      style={{
        position: 'fixed', inset: 0, zIndex: 60,
        background: 'rgba(0,0,0,0.42)', display: 'flex', justifyContent: 'flex-end',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(640px, 100vw)', height: '100%', background: 'var(--bg-card)',
          borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column',
          boxShadow: '-16px 0 40px rgba(0,0,0,0.18)',
        }}
      >
        {/* header */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '20px 24px',
          borderBottom: '1px solid var(--border)', flexShrink: 0,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 650, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>Manage {name}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontFamily: MONO, letterSpacing: '0.04em', marginTop: 2 }}>BEHAVIOR · KNOWLEDGE · MODEL</div>
          </div>
          <button onClick={close} aria-label="Close" style={{
            all: 'unset', cursor: 'pointer', padding: 6, borderRadius: 8, color: 'var(--text-tertiary)',
            display: 'inline-flex',
          }}><X size={18} strokeWidth={2} /></button>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 24px 24px' }}>
          {loading ? (
            <div style={{ padding: '40px 0', color: 'var(--text-tertiary)', fontSize: 13, fontFamily: MONO, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Loader2 size={14} className="spin" /> loading…
            </div>
          ) : (
            <>
              <Field
                label="Behavior & knowledge"
                hint={personaMissing
                  ? 'No persona file exists for this advisor yet — behavior editing is unavailable; settings below still apply.'
                  : "This is the advisor's persona — how it thinks, what it knows, its corrections. Edits take effect on the next turn."}
              >
                {personaMissing ? (
                  <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', padding: '10px 0' }}>—</div>
                ) : (
                  <textarea
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    spellCheck={false}
                    style={{ ...inputStyle, minHeight: 320, resize: 'vertical', lineHeight: 1.55 }}
                  />
                )}
              </Field>

              <Field label="Status" hint="Active advisors participate; paused ones are held out of routing.">
                <select
                  value={assets.status || 'active'}
                  onChange={(e) => setAsset('status', e.target.value)}
                  style={{ ...inputStyle, cursor: 'pointer' }}
                >
                  <option value="active">active</option>
                  <option value="paused">paused</option>
                </select>
              </Field>

              <Field label="Default model" hint="The model this advisor answers with (e.g. claude-opus-4-8, claude-sonnet-5, qwen2.5:3b).">
                <input
                  type="text"
                  value={assets.default_model || ''}
                  onChange={(e) => setAsset('default_model', e.target.value)}
                  spellCheck={false}
                  style={inputStyle}
                />
              </Field>

              <Field label="Research model" hint="The heavier model used for deep/research turns.">
                <input
                  type="text"
                  value={assets.research_model || ''}
                  onChange={(e) => setAsset('research_model', e.target.value)}
                  spellCheck={false}
                  style={inputStyle}
                />
              </Field>

              <Field label="Sidekick" hint="Whether this advisor runs a background sidekick.">
                <button
                  onClick={() => setAsset('sidekick_enabled', !assets.sidekick_enabled)}
                  style={{
                    all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 8,
                    fontFamily: MONO, fontSize: 12.5, letterSpacing: '0.04em',
                    padding: '7px 14px', borderRadius: 8,
                    color: assets.sidekick_enabled ? 'var(--accent)' : 'var(--text-tertiary)',
                    background: assets.sidekick_enabled ? 'var(--accent-bg)' : 'transparent',
                    border: `1px solid ${assets.sidekick_enabled ? 'var(--accent)' : 'var(--border)'}`,
                  }}
                >{assets.sidekick_enabled ? 'enabled' : 'disabled'}</button>
              </Field>
            </>
          )}
        </div>

        {/* footer */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '16px 24px',
          borderTop: '1px solid var(--border)', flexShrink: 0,
        }}>
          {err && <span style={{ fontSize: 12, color: '#ef4444', flex: 1, minWidth: 0 }}>{err}</span>}
          {saved && !err && <span style={{ fontSize: 12, color: 'var(--accent)', fontFamily: MONO, flex: 1, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Check size={13} strokeWidth={2.4} /> saved</span>}
          {!err && !saved && <span style={{ flex: 1 }} />}
          <AdvisorButton icon={X} label="Cancel" onClick={close} />
          <button
            onClick={save}
            disabled={saving || loading}
            style={{
              all: 'unset', cursor: saving || loading ? 'default' : 'pointer', boxSizing: 'border-box',
              display: 'inline-flex', alignItems: 'center', gap: 7,
              padding: '7px 16px', borderRadius: 8, fontSize: 12.5, fontWeight: 600,
              color: '#fff', background: 'var(--accent)', border: '1px solid var(--accent)',
              opacity: saving || loading ? 0.6 : 1, transition: 'opacity 120ms',
            }}
          >
            {saving ? <Loader2 size={13} className="spin" /> : <Check size={13} strokeWidth={2.4} />}
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── one advisor card — avatar + name + role, hairline separated (not a box) ───
function AdvisorCard({ id }) {
  const advisor = getAdvisor(id)
  const nav = useNavigate()
  const isLocal = LOCAL_ONLY.has(id)
  const [manage, setManage] = useState(false)
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
        <AdvisorButton icon={SlidersHorizontal} label="Manage" onClick={() => setManage(true)} />
      </div>
      {manage && <ManageDrawer id={id} name={advisor.name} onClose={() => setManage(false)} />}
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
