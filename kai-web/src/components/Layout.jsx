import { useState } from 'react'
import Sidebar from './Sidebar'
import BottomNav from './BottomNav'
import CaptureModal from './CaptureModal'

export default function Layout({ children, dark, onToggleTheme }) {
  const [captureOpen, setCaptureOpen] = useState(false)

  return (
    <div className="kai-screen flex h-screen overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden md:flex flex-shrink-0">
        <Sidebar
          dark={dark}
          onToggleTheme={onToggleTheme}
          onCapture={() => setCaptureOpen(true)}
        />
      </div>

      {/* Main content — extra bottom padding on mobile for nav */}
      <main className="flex-1 overflow-y-auto min-w-0 pb-[env(safe-area-inset-bottom)] md:pb-0">
        {children}
      </main>

      {/* Mobile bottom nav */}
      <div className="md:hidden">
        <BottomNav onCapture={() => setCaptureOpen(true)} />
      </div>

      {/* Capture modal */}
      {captureOpen && (
        <CaptureModal onClose={() => setCaptureOpen(false)} />
      )}
    </div>
  )
}
