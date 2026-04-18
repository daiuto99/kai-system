import { useState } from 'react'
import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/today',   label: 'Today',   icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg> },
  { to: '/harmony', label: 'Harmony', icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" /></svg> },
  { to: '/tasks',   label: 'Tasks',   icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" /></svg> },
  { to: '/habits',  label: 'Habits',  icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> },
  { to: '/knowledge', label: 'Knowledge', icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg> },
  { to: '/models', label: 'Models', icon: <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" /></svg> },
]

function ThemeToggle() {
  const [dark, setDark] = useState(
    () => document.documentElement.dataset.theme !== 'light'
  )
  function toggle() {
    const next = dark ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    localStorage.setItem('kai-theme', next)
    setDark(!dark)
  }
  return (
    <button
      onClick={toggle}
      title={dark ? 'Switch to light' : 'Switch to dark'}
      style={{
        width: 34, height: 34, borderRadius: 8, border: '1px solid var(--border)',
        background: 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer', color: 'var(--text-secondary)', transition: 'all 0.2s', flexShrink: 0,
      }}
      onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'var(--accent)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.borderColor = 'var(--border)' }}
    >
      {dark
        ? <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707M17.657 17.657l-.707-.707M6.343 6.343l-.707-.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
        : <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
      }
    </button>
  )
}

export default function TopNav({ onCapture }) {
  return (
    <nav style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 24px',
      background: 'var(--bg-card)',
      borderBottom: '1px solid var(--border)',
    }}>
      {/* Left — logo + primary nav */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <img src="/icon-192.png" alt="KAI" style={{ width: 28, height: 28, borderRadius: 0, background: 'none' }} />
        <div style={{ width: 1, height: 20, background: 'var(--border)', flexShrink: 0 }} />
        <div style={{ display: 'flex', gap: 4 }}>
        {NAV_ITEMS.map(({ to, label, icon }) => (
          <NavLink key={to + label} to={to} style={({ isActive }) => ({
            display: 'flex', alignItems: 'center', gap: 5,
            padding: '7px 14px', borderRadius: 8, textDecoration: 'none',
            fontSize: 13, fontWeight: 500, transition: 'all 0.2s',
            background: isActive ? 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)' : 'transparent',
            color: isActive ? '#ffffff' : 'var(--text-secondary)',
            boxShadow: isActive ? '0 2px 8px rgba(240,120,32,0.25)' : 'none',
          })}>
            {icon}{label}
          </NavLink>
        ))}
        </div>
      </div>

      {/* Right — Capture + Theme + Settings */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={onCapture} style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '7px 14px', borderRadius: 8, border: '1px solid var(--border)',
          background: 'transparent', color: 'var(--text-secondary)', fontSize: 13, fontWeight: 500,
          cursor: 'pointer', transition: 'all 0.2s', fontFamily: 'inherit',
        }}
          onMouseEnter={e => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
        >
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" /></svg>
          Capture
        </button>
        <ThemeToggle />
        <NavLink to="/settings" style={({ isActive }) => ({
          width: 34, height: 34, borderRadius: 8,
          background: isActive ? 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)' : 'transparent',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer', transition: 'all 0.2s', textDecoration: 'none',
          color: isActive ? '#ffffff' : 'var(--text-secondary)', flexShrink: 0,
          border: '1px solid var(--border)',
        })}>
          <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
        </NavLink>
      </div>
    </nav>
  )
}
