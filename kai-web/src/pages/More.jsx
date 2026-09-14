// KAI-1319 Slice 1 — the mobile overflow. Now driven by lib/nav.js so it can never
// drift from the bottom bar again: it lists exactly the groups not on the bar
// (Life · System · Settings), each opening its hub.
import { NavLink } from 'react-router-dom'
import { MOBILE_OVERFLOW, groupByKey } from '../lib/nav'

export default function More() {
  const groups = MOBILE_OVERFLOW.map(groupByKey).filter(Boolean)
  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '28px 20px 40px' }}>
      <h1 style={{ fontSize: 22, fontWeight: 680, letterSpacing: '-0.02em', margin: '0 0 18px', color: 'var(--text-primary)' }}>More</h1>
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 16, overflow: 'hidden' }}>
        {groups.map((g, i) => {
          const Icon = g.icon
          const sub = g.members ? g.members.map((m) => m.label).join(' · ') : (g.blurb || '')
          return (
            <NavLink key={g.key} to={g.path} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '16px 18px',
              textDecoration: 'none', color: 'inherit', transition: 'background 0.15s',
              borderTop: i === 0 ? 'none' : '1px solid var(--border)',
            }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover-bg)' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
              <div style={{
                width: 38, height: 38, borderRadius: 11, flexShrink: 0, display: 'flex',
                alignItems: 'center', justifyContent: 'center', background: 'var(--accent-bg)', color: 'var(--accent)',
              }}>
                <Icon size={18} strokeWidth={1.85} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{g.label}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sub}</div>
              </div>
              <span style={{ color: 'var(--text-subtle)', fontSize: 18, flexShrink: 0 }}>›</span>
            </NavLink>
          )
        })}
      </div>
    </div>
  )
}
