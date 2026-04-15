import { NavLink } from 'react-router-dom'

export default function TopNav({ onCapture }) {
  return (
    <nav style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '12px 24px',
      background: 'linear-gradient(to right, #ffffff, #fafbfc)',
      borderRadius: '24px 24px 0 0',
      borderBottom: '1px solid #e8ecf1',
      marginBottom: 0,
    }}>
      <div style={{ display: 'flex', gap: 6 }}>
        {[
          { to: '/today', label: 'Today' },
          { to: '/harmony', label: 'Harmony' },
          { to: '/tasks', label: 'Tasks' },
        ].map(({ to, label }) => (
          <NavLink key={to} to={to} style={({ isActive }) => ({
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, textDecoration: 'none',
            fontSize: 13, fontWeight: 500, transition: 'all 0.2s ease', border: 'none',
            background: isActive ? 'linear-gradient(135deg, #c2410c 0%, #9a3412 100%)' : 'transparent',
            color: isActive ? '#ffffff' : '#6b7280',
            boxShadow: isActive ? '0 2px 8px rgba(194,65,12,0.2)' : 'none',
          })}>
            {label}
          </NavLink>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
        <NavLink to="/insights" style={{ fontSize: 13, color: '#6b7280', textDecoration: 'none', fontWeight: 500 }}
          onMouseEnter={e => e.target.style.color = '#c2410c'}
          onMouseLeave={e => e.target.style.color = '#6b7280'}
        >Insights</NavLink>
        <button onClick={onCapture} style={{
          fontSize: 13, color: '#6b7280', fontWeight: 500, background: 'none', border: 'none',
          cursor: 'pointer', fontFamily: 'inherit', padding: 0,
        }}
          onMouseEnter={e => e.target.style.color = '#c2410c'}
          onMouseLeave={e => e.target.style.color = '#6b7280'}
        >capture</button>
        <NavLink to="/settings" style={{ fontSize: 13, color: '#6b7280', textDecoration: 'none', fontWeight: 500 }}
          onMouseEnter={e => e.target.style.color = '#c2410c'}
          onMouseLeave={e => e.target.style.color = '#6b7280'}
        >⚙</NavLink>
      </div>
    </nav>
  )
}
