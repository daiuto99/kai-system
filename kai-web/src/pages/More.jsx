import { NavLink } from 'react-router-dom'
import { CheckSquare, Inbox, Sparkles, Activity } from 'lucide-react'

const ITEMS = [
  { to: '/tasks',       icon: CheckSquare, label: 'Tasks',       desc: 'Todoist queue'         },
  { to: '/parking-lot', icon: Inbox,       label: 'Parking Lot', desc: 'Captured items'        },
  { to: '/insights',    icon: Sparkles,    label: 'Insights',    desc: 'Ember observations'    },
  { to: '/harmony',     icon: Activity,    label: 'Harmony',     desc: 'Life domain balance'   },
]

export default function More() {
  return (
    <div className="max-w-lg mx-auto px-4 py-8">
      <h1 className="text-xl font-semibold mb-6">More</h1>
      <div className="kai-card divide-y kai-divider">
        {ITEMS.map(({ to, icon: Icon, label, desc }) => (
          <NavLink
            key={to}
            to={to}
            className="flex items-center gap-4 px-5 py-4 hover:bg-white/4 transition-colors"
          >
            <div className="w-9 h-9 rounded-xl bg-white/6 flex items-center justify-center flex-shrink-0">
              <Icon size={17} strokeWidth={1.75} className="kai-text-secondary" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-medium">{label}</p>
              <p className="text-xs kai-text-subtle mt-0.5">{desc}</p>
            </div>
            <span className="text-white/20 text-lg">›</span>
          </NavLink>
        ))}
      </div>
    </div>
  )
}
