import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  {
    to: '/today',
    label: 'Today',
    icon: (
      <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
          d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    to: '/harmony',
    label: 'Harmony',
    icon: (
      <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
          d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
      </svg>
    ),
  },
  {
    to: '/tasks',
    label: 'Tasks',
    icon: (
      <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
    ),
  },
]

export default function TopNav({ onCapture }) {
  return (
    <nav style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '12px 24px',
      background: 'linear-gradient(to right, #ffffff, #fafbfc)',
      borderBottom: '1px solid #e8ecf1',
    }}>
      {/* Left — primary tabs */}
      <div style={{ display: 'flex', gap: 6 }}>
        {NAV_ITEMS.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            style={({ isActive }) => ({
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 16px', borderRadius: 8, textDecoration: 'none',
              fontSize: 13, fontWeight: 500, transition: 'all 0.2s ease',
              background: isActive
                ? 'linear-gradient(135deg, #c2410c 0%, #9a3412 100%)'
                : 'transparent',
              color: isActive ? '#ffffff' : '#6b7280',
              boxShadow: isActive ? '0 2px 8px rgba(194,65,12,0.2)' : 'none',
            })}
          >
            {icon}
            {label}
          </NavLink>
        ))}
      </div>

      {/* Right — actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {/* Capture */}
        <button
          onClick={onCapture}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 14px', borderRadius: 8, border: '1px solid #e8ecf1',
            background: '#fafbfc', color: '#6b7280', fontSize: 13, fontWeight: 500,
            cursor: 'pointer', transition: 'all 0.2s ease', fontFamily: 'inherit',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#fff7ed'; e.currentTarget.style.borderColor = '#c2410c'; e.currentTarget.style.color = '#c2410c' }}
          onMouseLeave={e => { e.currentTarget.style.background = '#fafbfc'; e.currentTarget.style.borderColor = '#e8ecf1'; e.currentTarget.style.color = '#6b7280' }}
        >
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          Capture
        </button>

        {/* Settings gear */}
        <NavLink
          to="/settings"
          style={({ isActive }) => ({
            width: 36, height: 36, borderRadius: 8, border: 'none',
            background: isActive ? 'linear-gradient(135deg, #c2410c 0%, #9a3412 100%)' : '#fafbfc',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', transition: 'all 0.2s ease', textDecoration: 'none',
            color: isActive ? '#ffffff' : '#6b7280',
            flexShrink: 0,
          })}
          onMouseEnter={e => {
            if (!e.currentTarget.style.background.includes('c2410c')) {
              e.currentTarget.style.background = 'linear-gradient(135deg, #c2410c 0%, #9a3412 100%)'
              e.currentTarget.querySelector('svg').style.stroke = '#ffffff'
            }
          }}
          onMouseLeave={e => {
            if (!e.currentTarget.getAttribute('aria-current')) {
              e.currentTarget.style.background = '#fafbfc'
              e.currentTarget.querySelector('svg').style.stroke = '#6b7280'
            }
          }}
        >
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
              d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </NavLink>
      </div>
    </nav>
  )
}
