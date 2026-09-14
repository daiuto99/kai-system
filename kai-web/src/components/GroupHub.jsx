// KAI-1319 Slice 1 — one launcher for a group's member surfaces.
// A safe, reversible regrouping: it links to the existing pages (which keep their
// own routes). Slice 3 promotes these into inline tabs; for now the pages are
// untouched and every surface stays reachable.
import { NavLink } from 'react-router-dom'
import { groupByKey } from '../lib/nav'

export default function GroupHub({ groupKey }) {
  const group = groupByKey(groupKey)
  if (!group || !group.members) return null
  const Icon = group.icon
  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '28px 20px 40px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 11, flexShrink: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center', background: 'var(--accent-bg)', color: 'var(--accent)',
        }}>
          <Icon size={20} strokeWidth={1.9} />
        </div>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 680, letterSpacing: '-0.02em', margin: 0, color: 'var(--text-primary)' }}>{group.label}</h1>
          {group.blurb && <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: '2px 0 0' }}>{group.blurb}</p>}
        </div>
      </div>

      <div style={{
        marginTop: 20, background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 16, overflow: 'hidden',
      }}>
        {group.members.map((m, i) => {
          const MIcon = m.icon
          return (
            <NavLink key={m.path} to={m.path} style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
              textDecoration: 'none', color: 'inherit', transition: 'background 0.15s',
              borderTop: i === 0 ? 'none' : '1px solid var(--border)',
            }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover-bg)' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
              <div style={{
                width: 36, height: 36, borderRadius: 10, flexShrink: 0, display: 'flex',
                alignItems: 'center', justifyContent: 'center', background: 'var(--bg-muted)', color: 'var(--text-secondary)',
              }}>
                {MIcon ? <MIcon size={17} strokeWidth={1.85} /> : null}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{m.label}</div>
                {m.desc && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 1 }}>{m.desc}</div>}
              </div>
              <span style={{ color: 'var(--text-subtle)', fontSize: 18, flexShrink: 0 }}>›</span>
            </NavLink>
          )
        })}
      </div>
    </div>
  )
}
