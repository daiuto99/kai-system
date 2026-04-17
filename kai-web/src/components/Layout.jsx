import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import TopNav from './TopNav'
import BottomNav from './BottomNav'
import CaptureModal from './CaptureModal'

const FRAMED_PAGES = ['/today', '/harmony', '/tasks', '/habits', '/insights', '/settings', '/parking-lot']

export default function Layout({ children }) {
  const [captureOpen, setCaptureOpen] = useState(false)
  const { pathname } = useLocation()
  const isFramed = FRAMED_PAGES.some(p => pathname === p || pathname.startsWith(p))

  return (
    <div style={{ height: '100%', background: 'var(--bg-screen)', overflow: 'hidden' }}>

      {/* Desktop */}
      <div className="hidden md:flex md:flex-col" style={{ height: '100%', padding: '12px 12px 0 12px' }}>
        {isFramed ? (
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
            background: 'var(--bg-card)',
            borderRadius: '24px 24px 0 0',
            border: '1px solid var(--border)',
            borderBottom: 'none',
          }}>
            <TopNav onCapture={() => setCaptureOpen(true)} />
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
          flexShrink: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', padding: '0 16px', height: 48,
          background: 'var(--bg-card)',
          borderBottom: '1px solid var(--border)',
        }}>
          <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.02em', color: 'var(--text-primary)' }}>
            KAI
          </span>
          <button
            onClick={() => setCaptureOpen(true)}
            style={{
              fontSize: 12, fontWeight: 500, background: 'none', border: 'none',
              cursor: 'pointer', fontFamily: 'inherit', color: 'var(--accent)',
            }}
          >
            + capture
          </button>
        </div>
        <main style={{ flex: 1, overflow: 'hidden', minHeight: 0 }}>{children}</main>
        <BottomNav onCapture={() => setCaptureOpen(true)} />
      </div>

      {captureOpen && <CaptureModal onClose={() => setCaptureOpen(false)} />}
    </div>
  )
}
