import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import TopNav from './TopNav'
import BottomNav from './BottomNav'
import CaptureModal from './CaptureModal'

const LIGHT_PAGES = ['/today', '/harmony', '/tasks', '/insights', '/settings']

export default function Layout({ children }) {
  const [captureOpen, setCaptureOpen] = useState(false)
  const { pathname } = useLocation()
  const isLight = LIGHT_PAGES.some(p => pathname === p || pathname.startsWith(p))

  return (
    <div className={`flex flex-col h-screen overflow-hidden ${isLight ? 'bg-kai-light-bg' : 'bg-kai-dark-bg'}`}>
      {isLight && (
        <div className="hidden md:block">
          <TopNav onCapture={() => setCaptureOpen(true)} />
        </div>
      )}
      <div className={`md:hidden flex-shrink-0 flex items-center justify-between px-4 h-12 border-b ${isLight ? 'bg-white border-kai-light-border' : 'bg-kai-dark-card border-kai-dark-border'}`}>
        <span className={`text-sm font-semibold tracking-wide ${isLight ? 'text-kai-light-text' : 'text-white'}`}>KAI</span>
        <button onClick={() => setCaptureOpen(true)} className={`text-xs font-medium ${isLight ? 'text-kai-terra' : 'text-kai-blue'}`}>+ capture</button>
      </div>
      <main className="flex-1 overflow-hidden min-w-0">{children}</main>
      <div className="md:hidden">
        <BottomNav onCapture={() => setCaptureOpen(true)} isLight={isLight} />
      </div>
      {captureOpen && <CaptureModal onClose={() => setCaptureOpen(false)} />}
    </div>
  )
}
