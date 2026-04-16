const LABELS = { green: 'On', yellow: 'Partial', red: 'Off' }

export function StatusBadge({ status }) {
  return <span className={`status-${status}`}>{LABELS[status]}</span>
}

export function StatusDot({ status, size = 8 }) {
  const colors = {
    green:  '#10b981',
    yellow: '#f59e0b',
    red:    '#ef4444',
  }
  return (
    <span
      style={{ display: 'inline-block', borderRadius: '50%', flexShrink: 0, width: size, height: size, backgroundColor: colors[status] || '#9ca3af' }}
    />
  )
}

export function StatusToggle({ status, onChange }) {
  const options = ['green', 'yellow', 'red']
  const active = {
    green:  { border: '#10b981', color: '#10b981', background: 'rgba(16,185,129,0.12)'  },
    yellow: { border: '#f59e0b', color: '#f59e0b', background: 'rgba(245,158,11,0.12)' },
    red:    { border: '#ef4444', color: '#ef4444', background: 'rgba(239,68,68,0.12)'   },
  }
  const labels = { green: 'G', yellow: 'Y', red: 'R' }

  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {options.map(s => {
        const isActive = status === s
        const a = active[s]
        return (
          <button
            key={s}
            onClick={() => onChange(s)}
            style={{
              fontSize: 11, padding: '2px 8px', borderRadius: 20,
              border: `1px solid ${isActive ? a.border : '#e8ecf1'}`,
              color: isActive ? a.color : '#9ca3af',
              background: isActive ? a.background : 'transparent',
              fontWeight: 500, cursor: 'pointer', transition: 'all 0.2s',
              fontFamily: 'inherit',
            }}
          >
            {labels[s]}
          </button>
        )
      })}
    </div>
  )
}
