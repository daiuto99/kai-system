import { NavLink } from 'react-router-dom'
import { MessageSquare, Sun, Activity, Plus, LayoutGrid } from 'lucide-react'

export default function BottomNav({ onCapture }) {
  const base = 'flex flex-col items-center gap-0.5 px-3 py-2 min-w-[52px]'
  const active = 'text-kai-blue'
  const inactive = 'text-white/35 light:text-gray-400'

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 border-t kai-divider"
      style={{
        backgroundColor: 'var(--nav-bg, #0D1829)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <div className="flex items-end justify-around px-1 pt-1.5 pb-1">
        <NavLink
          to="/chat"
          className={({ isActive }) => `${base} ${isActive ? active : inactive}`}
        >
          <MessageSquare size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">KAI</span>
        </NavLink>

        <NavLink
          to="/today"
          className={({ isActive }) => `${base} ${isActive ? active : inactive}`}
        >
          <Sun size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">Today</span>
        </NavLink>

        {/* Centre capture FAB */}
        <button
          onClick={onCapture}
          className="flex flex-col items-center gap-0.5 px-3 py-0"
        >
          <div className="w-12 h-12 bg-kai-blue rounded-full flex items-center justify-center shadow-lg -mt-5">
            <Plus size={24} strokeWidth={2.5} className="text-white" />
          </div>
          <span className="text-[10px] font-medium text-white/35 mt-0.5">Capture</span>
        </button>

        <NavLink
          to="/harmony"
          className={({ isActive }) => `${base} ${isActive ? active : inactive}`}
        >
          <Activity size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">Harmony</span>
        </NavLink>

        <NavLink
          to="/more"
          className={({ isActive }) => `${base} ${isActive ? active : inactive}`}
        >
          <LayoutGrid size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">More</span>
        </NavLink>
      </div>
    </nav>
  )
}
