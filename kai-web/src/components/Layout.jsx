import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import TopNav from './TopNav'
import BottomNav from './BottomNav'
import CaptureModal from './CaptureModal'

const LIGHT_PAGES = ['/today', '/harmony', '/tasks', '/insights', '/settings', '/parking-lot']

export default function Layout({ children }) {
  const [captureOpen, setCaptureOpen] = useState(false)
  const { pathname } = useLocation()
  const isLight = LIGHT_PAGES.some(p => pathname === p || pathname.startsWith(p))

  return (
    <div style={{ height: '100%', background: isLight ? '#f8f9fa' : '#060E1F', overflow: 'hidden' }}>

      {/* ── Desktop ── */}
      {/* NOTE: no display in inline style — Tailwind hidden/md:flex controls it */}
      <div className="hidden md:flex md:flex-col" style={{ height: '100%', padding: '12px 12px 0 12px' }}>
        {isLight ? (
          /* White frame: nav is the top of this container */
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            background: '#ffffff',
            borderRadius: '24px 24px 0 0',
            boxShadow: '0 -2px 0 #e8ecf1, 2px 0 0 #e8ecf1, -2px 0 0 #e8ecf1',
          }}>
            <TopNav onCapture={() => setCaptureOpen(true)} />
            <main style={{ flex: 1, overflowY: 'auto', background: '#f8f9fa' }}>
              {children}
            </main>
          </div>
        ) : (
          <main style={{ flex: 1, overflow: 'hidden' }}>{children}</main>
        )}
      </div>

      {/* ── Mobile ── */}
      {/* NOTE: no display in inline style — Tailwind flex/md:hidden controls it */}
      <div className="flex flex-col md:hidden" style={{ height: '100%' }}>
        <div style={{
          flexShrink: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', padding: '0 16px', height: 48,
          background: isLight ? '#ffffff' : '#0D1829',
          borderBottom: `1px solid ${isLight ? '#e8ecf1' : 'rgba(255,255,255,0.08)'}`,
        }}>
          <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.02em', color: isLight ? '#1f2937' : '#ffffff' }}>
            KAI
          </span>
          <button
            onClick={() => setCaptureOpen(true)}
            style={{
              fontSize: 12, fontWeight: 500, background: 'none', border: 'none',
              cursor: 'pointer', fontFamily: 'inherit',
              color: isLight ? '#c2410c' : '#3882F6',
            }}
          >
            + capture
          </button>
        </div>
        <main style={{ flex: 1, overflow: 'hidden', minHeight: 0 }}>{children}</main>
        <BottomNav onCapture={() => setCaptureOpen(true)} isLight={isLight} />
      </div>

      {captureOpen && <CaptureModal onClose={() => setCaptureOpen(false)} />}
    </div>
  )
}
