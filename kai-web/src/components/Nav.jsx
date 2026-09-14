// KAI-1319 Slice 1 — the ONE nav, rendered from lib/nav.js at both breakpoints.
// DesktopNav: a labeled rail (fixes the old icon-only strip). MobileNav: a 5-slot
// bottom bar. Both derive "active" the same way, so a member page lights its group.
import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Plus, Moon, SunMedium, Share2, MoreHorizontal } from 'lucide-react'
import { GROUPS, MOBILE_PRIMARY, MOBILE_OVERFLOW, groupByKey } from '../lib/nav'
import Presence from './Presence'

// A group is "active" when the current path is the group itself, one of its member
// pages, or a sub-route of any of those.
function groupIsActive(group, pathname) {
  const paths = [group.path, ...((group.members || []).map((m) => m.path))]
  return paths.some((p) => pathname === p || pathname.startsWith(p + '/'))
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme !== 'light')
  function toggle() {
    const next = dark ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    localStorage.setItem('kai-theme', next)
    setDark(!dark)
  }
  return (
    <button onClick={toggle} title={dark ? 'Switch to light' : 'Switch to dark'} style={ctrlStyle}
      onMouseEnter={ctrlEnter} onMouseLeave={ctrlLeave}>
      {dark ? <SunMedium size={15} strokeWidth={1.9} /> : <Moon size={15} strokeWidth={1.9} />}
    </button>
  )
}

const ctrlStyle = {
  width: 34, height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'transparent',
  display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
  color: 'var(--text-secondary)', transition: 'all 0.2s', flexShrink: 0, textDecoration: 'none',
}
const ctrlEnter = (e) => { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.borderColor = 'var(--accent)' }
const ctrlLeave = (e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.borderColor = 'var(--border)' }

export function DesktopNav({ onCapture }) {
  const { pathname } = useLocation()
  return (
    <nav style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 20px', background: 'var(--bg-card)', borderBottom: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 }}>
        <img src="/icon-192.png" alt="KAI" style={{ width: 26, height: 26 }} />
        <div style={{ width: 1, height: 20, background: 'var(--border)', flexShrink: 0 }} />
        <div style={{ display: 'flex', gap: 4, overflowX: 'auto' }} className="no-scrollbar">
          {GROUPS.map((g) => {
            const Icon = g.icon
            const active = groupIsActive(g, pathname)
            return (
              <NavLink key={g.key} to={g.path} title={g.label} style={{
                display: 'flex', alignItems: 'center', gap: 7, height: 34, padding: '0 12px',
                borderRadius: 9, textDecoration: 'none', transition: 'all 0.18s', flexShrink: 0,
                fontSize: 13, fontWeight: 600, letterSpacing: '0.01em',
                background: active ? 'linear-gradient(135deg, var(--accent) 0%, var(--accent-dim) 100%)' : 'transparent',
                color: active ? '#ffffff' : 'var(--text-secondary)',
                boxShadow: active ? '0 2px 8px rgba(240,120,32,0.25)' : 'none',
              }}
                onMouseEnter={(e) => { if (!active) { e.currentTarget.style.background = 'var(--hover-bg)'; e.currentTarget.style.color = 'var(--accent)' } }}
                onMouseLeave={(e) => { if (!active) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' } }}>
                <Icon size={16} strokeWidth={1.9} />
                <span>{g.label}</span>
              </NavLink>
            )
          })}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Presence showLabel />
        <button onClick={onCapture} title="Capture" style={ctrlStyle} onMouseEnter={ctrlEnter} onMouseLeave={ctrlLeave}>
          <Plus size={16} strokeWidth={2.2} />
        </button>
        <a href="/architecture.html" target="_blank" rel="noopener" title="KAI Architecture" style={ctrlStyle} onMouseEnter={ctrlEnter} onMouseLeave={ctrlLeave}>
          <Share2 size={15} strokeWidth={1.9} />
        </a>
        <ThemeToggle />
      </div>
    </nav>
  )
}

export function MobileNav({ onCapture }) {
  const { pathname } = useLocation()
  const primary = MOBILE_PRIMARY.map(groupByKey).filter(Boolean)
  const overflowActive = MOBILE_OVERFLOW.map(groupByKey).filter(Boolean).some((g) => groupIsActive(g, pathname)) || pathname === '/more'

  const item = (active) => ({
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
    minWidth: 52, padding: '4px 6px', textDecoration: 'none',
    color: active ? 'var(--accent)' : 'var(--text-tertiary)', transition: 'color 0.18s',
  })

  return (
    <nav style={{
      flexShrink: 0, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-around',
      padding: '6px 4px calc(8px + env(safe-area-inset-bottom))', background: 'var(--bg-card)',
      borderTop: '1px solid var(--border)',
    }}>
      {primary.slice(0, 2).map((g) => {
        const Icon = g.icon
        const active = groupIsActive(g, pathname)
        return (
          <NavLink key={g.key} to={g.path} style={item(active)}>
            <Icon size={22} strokeWidth={1.85} />
            <span style={{ fontSize: 10, fontWeight: 600 }}>{g.label}</span>
          </NavLink>
        )
      })}
      <button onClick={onCapture} style={{ all: 'unset', cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
        <div style={{
          width: 48, height: 48, borderRadius: '50%', marginTop: -20, display: 'flex',
          alignItems: 'center', justifyContent: 'center', background: 'var(--accent)',
          boxShadow: '0 6px 16px rgba(240,120,32,0.4)',
        }}>
          <Plus size={24} strokeWidth={2.5} color="#fff" />
        </div>
        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)' }}>Capture</span>
      </button>
      {primary.slice(2).map((g) => {
        const Icon = g.icon
        const active = groupIsActive(g, pathname)
        return (
          <NavLink key={g.key} to={g.path} style={item(active)}>
            <Icon size={22} strokeWidth={1.85} />
            <span style={{ fontSize: 10, fontWeight: 600 }}>{g.label}</span>
          </NavLink>
        )
      })}
      <NavLink to="/more" style={item(overflowActive)}>
        <MoreHorizontal size={22} strokeWidth={1.85} />
        <span style={{ fontSize: 10, fontWeight: 600 }}>More</span>
      </NavLink>
    </nav>
  )
}
