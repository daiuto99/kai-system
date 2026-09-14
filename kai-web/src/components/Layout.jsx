import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { DesktopNav, MobileNav } from './Nav'
import CaptureModal from './CaptureModal'
import Presence from './Presence'
import { GROUPS } from '../lib/nav'

// Mobile header label — derived from the nav config plus a few leaf pages that live
// under a group hub. Keeps the title in step with the one nav (no hardcoded drift).
const PAGE_LABELS = {
  '/now': 'Now',
  '/today': 'Now',
  '/advisors': 'Advisors',
  '/work': 'Work',
  '/life': 'Life',
  '/system': 'System',
  '/more': 'More',
  '/settings': 'Settings',
}
GROUPS.forEach((g) => (g.members || []).forEach((m) => { PAGE_LABELS[m.path] = m.label }))

// Preserve the original framed set (Slice 1 = no visual regression on existing
// pages) and add only the new nav surfaces. Full-bleed pages (chat, wordpress,
// financial, system) keep their own layout; unifying their chrome is Slice 3.
const FRAMED_PAGES = [
  '/now', '/work', '/system',
  '/today', '/today-classic', '/harmony', '/tasks', '/habits', '/insights', '/settings',
  '/parking-lot', '/knowledge', '/models', '/plane', '/advisors', '/wiki', '/usage',
  '/build', '/life', '/system-hub', '/more',
]
function isFramed(pathname) {
  return FRAMED_PAGES.some((p) => pathname === p || pathname.startsWith(p + '/'))
}

function labelFor(pathname) {
  const hit = Object.entries(PAGE_LABELS).find(([k]) => pathname === k || pathname.startsWith(k + '/'))
  return hit ? ' — ' + hit[1] : ''
}

export default function Layout({ children }) {
  const [captureOpen, setCaptureOpen] = useState(false)
  const { pathname } = useLocation()
  const framed = isFramed(pathname)

  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflow: 'hidden' }}>

      {/* Desktop */}
      <div className="hidden md:flex md:flex-col" style={{ height: '100%', padding: '12px 12px 0 12px' }}>
        {framed ? (
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
            background: 'var(--bg-card)', borderRadius: '24px 24px 0 0',
            border: '1px solid var(--border)', borderBottom: 'none',
          }}>
            <DesktopNav onCapture={() => setCaptureOpen(true)} />
            <main style={{ flex: 1, overflowY: 'auto', background: 'var(--bg-screen)' }}>
              {children}
            </main>
          </div>
        ) : (
          <main style={{ flex: 1, overflow: 'hidden' }}>{children}</main>
        )}
      </div>

      {/* Mobile */}
      <div className="flex flex-col md:hidden" style={{ height: '100%' }}>
        <div style={{
          flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 16px', height: 48, background: 'var(--bg-card)', borderBottom: '1px solid var(--border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.02em', color: 'var(--text-primary)' }}>
              KAI{labelFor(pathname)}
            </span>
            <Presence />
          </div>
          <button onClick={() => setCaptureOpen(true)} style={{
            fontSize: 12, fontWeight: 500, background: 'none', border: 'none',
            cursor: 'pointer', fontFamily: 'inherit', color: 'var(--accent)',
          }}>
            + capture
          </button>
        </div>
        <main style={{ flex: 1, overflow: 'hidden', minHeight: 0 }}>{children}</main>
        <MobileNav onCapture={() => setCaptureOpen(true)} />
      </div>

      {captureOpen && <CaptureModal onClose={() => setCaptureOpen(false)} />}
    </div>
  )
}
