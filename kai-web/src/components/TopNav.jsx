import { NavLink } from 'react-router-dom'
import { Settings } from 'lucide-react'

export default function TopNav({ onCapture }) {
  const navLink = ({ isActive }) =>
    `px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
      isActive
        ? 'bg-kai-terra text-white'
        : 'text-kai-light-muted hover:text-kai-light-text'
    }`

  return (
    <nav className="flex-shrink-0 flex items-center justify-between px-6 h-12 bg-white border-b border-kai-light-border">
      <div className="flex items-center gap-1">
        <NavLink to="/today" className={navLink}>Today</NavLink>
        <NavLink to="/harmony" className={navLink}>Harmony</NavLink>
        <NavLink to="/tasks" className={navLink}>Tasks</NavLink>
      </div>
      <div className="flex items-center gap-5">
        <NavLink
          to="/insights"
          className="text-sm text-kai-light-muted hover:text-kai-light-text transition-colors"
        >
          Insights
        </NavLink>
        <button
          onClick={onCapture}
          className="text-sm text-kai-light-muted hover:text-kai-light-text transition-colors"
        >
          capture
        </button>
        <NavLink
          to="/settings"
          className="text-kai-light-subtle hover:text-kai-light-text transition-colors"
        >
          <Settings size={17} strokeWidth={1.75} />
        </NavLink>
      </div>
    </nav>
  )
}
