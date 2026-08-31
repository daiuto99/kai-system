import { useEffect, useState, useRef } from 'react'
import { api } from '../lib/api'

// Presence — the voice signal bus rendered as a live dot (KAI-1283 P-3 voice layer).
// Polls /orchestrator/presence and shows KAI's conversational state so the surface
// *feels* present. Fail-soft: any fetch error falls back to a calm idle dot; it never
// throws into the app shell. Compact by default (dot only); `showLabel` adds the word.

const STATES = {
  idle:      { color: '#3fb950', label: 'Idle',      pulse: false },
  listening: { color: '#58a6ff', label: 'Listening', pulse: true  },
  thinking:  { color: '#d29922', label: 'Thinking',  pulse: true  },
  speaking:  { color: 'var(--accent)', label: 'Speaking', pulse: true },
  error:     { color: '#f85149', label: 'Voice error', pulse: false },
}

export default function Presence({ showLabel = false }) {
  const [state, setState] = useState('idle')
  const [available, setAvailable] = useState(true)
  const timer = useRef(null)

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const p = await api.getPresence()
        if (!alive) return
        setState(STATES[p.state] ? p.state : 'idle')
        setAvailable(p.available !== false)
      } catch {
        if (alive) setState('idle')
      }
    }
    poll()
    timer.current = setInterval(poll, 2500)
    return () => { alive = false; clearInterval(timer.current) }
  }, [])

  // If the voice layer isn't wired at all, render nothing rather than a dead dot.
  if (!available) return null

  const s = STATES[state] || STATES.idle
  return (
    <div title={`KAI voice: ${s.label}`} style={{
      display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%', background: s.color,
        boxShadow: s.pulse ? `0 0 0 0 ${s.color}` : 'none',
        animation: s.pulse ? 'kai-presence-pulse 1.4s ease-out infinite' : 'none',
        flexShrink: 0,
      }} />
      {showLabel && (
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', letterSpacing: '0.02em' }}>
          {s.label}
        </span>
      )}
      <style>{`@keyframes kai-presence-pulse {
        0%   { box-shadow: 0 0 0 0 ${typeof s.color === 'string' && s.color.startsWith('#') ? s.color + '99' : 'rgba(240,120,32,0.6)'}; }
        70%  { box-shadow: 0 0 0 6px rgba(0,0,0,0); }
        100% { box-shadow: 0 0 0 0 rgba(0,0,0,0); }
      }`}</style>
    </div>
  )
}
