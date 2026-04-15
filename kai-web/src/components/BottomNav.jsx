import { NavLink } from 'react-router-dom'
import { MessageSquare, Sun, Activity, Plus, LayoutGrid } from 'lucide-react'

export default function BottomNav({ onCapture, isLight }) {
  const base = 'flex flex-col items-center gap-0.5 px-3 py-2 min-w-[52px] transition-colors'
  const active = isLight ? 'text-kai-terra' : 'text-kai-blue'
  const inactive = isLight ? 'text-[#9B9490]' : 'text-white/35'

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 border-t"
      style={{ backgroundColor: isLight ? '#FFFFFF' : '#0D1829', borderColor: isLight ? '#E8E5E0' : 'rgba(255,255,255,0.12)', paddingBottom: 'env(safe-area-inset-bottom)' }}>
      <div className="flex items-end justify-around px-1 pt-1.5 pb-1">
        <NavLink to="/today" className={({ isActive }) => `${base} ${isActive ? active : inactive}`}>
          <Sun size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">Today</span>
        </NavLink>
        <NavLink to="/chat" className={({ isActive }) => `${base} ${isActive ? active : inactive}`}>
          <MessageSquare size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">KAI</span>
        </NavLink>
        <button onClick={onCapture} className="flex flex-col items-center gap-0.5 px-3 py-0">
          <div className={`w-12 h-12 rounded-full flex items-center justify-center shadow-lg -mt-5 ${isLight ? 'bg-kai-terra' : 'bg-kai-blue'}`}>
            <Plus size={24} strokeWidth={2.5} className="text-white" />
          </div>
          <span className={`text-[10px] font-medium mt-0.5 ${inactive}`}>Capture</span>
        </button>
        <NavLink to="/harmony" className={({ isActive }) => `${base} ${isActive ? active : inactive}`}>
          <Activity size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">Harmony</span>
        </NavLink>
        <NavLink to="/more" className={({ isActive }) => `${base} ${isActive ? active : inactive}`}>
          <LayoutGrid size={22} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">More</span>
        </NavLink>
      </div>
    </nav>
  )
}
