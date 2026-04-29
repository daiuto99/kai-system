import { NavLink, useNavigate } from 'react-router-dom'
import {
  Sun, Moon, Home, Activity, CheckSquare,
  Inbox, Sparkles, Plus, MessageSquare, LayoutGrid
} from 'lucide-react'
import { ADVISORS } from '../lib/advisors'

const NAV = [
  { to: '/today',       icon: Home,          label: 'Today'       },
  { to: '/harmony',     icon: Activity,      label: 'Harmony'     },
  { to: '/tasks',       icon: CheckSquare,   label: 'Tasks'       },
  { to: '/parking-lot', icon: Inbox,         label: 'Lot Inventory' },
  { to: '/insights',    icon: Sparkles,      label: 'Insights'    },
]

export default function Sidebar({ dark, onToggleTheme, onCapture }) {
  const navigate = useNavigate()

  return (
    <aside className="w-56 flex-shrink-0 flex flex-col h-full border-r kai-divider py-5 px-3">
      {/* Wordmark */}
      <div className="px-3 mb-6 flex items-center gap-2">
        <span className="text-xl font-semibold tracking-tight">KAI</span>
        <span className="text-[10px] kai-text-subtle font-mono uppercase tracking-widest mt-0.5">
          System
        </span>
      </div>

      {/* Council team */}
      <div className="px-3 mb-4">
        <p className="text-[10px] font-semibold uppercase tracking-widest kai-text-subtle mb-2">
          Council
        </p>
        <div className="flex flex-wrap gap-1.5">
          {ADVISORS.map(a => (
            <button
              key={a.id}
              onClick={() => navigate(`/chat/${a.id}`)}
              title={`${a.name} — ${a.role}`}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium transition-colors hover:bg-white/8"
              style={{ color: a.color }}
            >
              <span>{a.emoji}</span>
              <span>{a.name}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="border-t kai-divider mb-3" />

      {/* Chat link */}
      <NavLink
        to="/chat"
        className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
      >
        <MessageSquare size={16} strokeWidth={1.75} />
        Chat
      </NavLink>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 mt-0.5">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Icon size={16} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Bottom */}
      <div className="space-y-0.5">
        <button onClick={onCapture} className="nav-item w-full">
          <Plus size={16} strokeWidth={1.75} />
          Quick Capture
        </button>
        <button onClick={onToggleTheme} className="nav-item w-full">
          {dark
            ? <><Sun size={16} strokeWidth={1.75} /> Light mode</>
            : <><Moon size={16} strokeWidth={1.75} /> Dark mode</>}
        </button>
      </div>
    </aside>
  )
}
