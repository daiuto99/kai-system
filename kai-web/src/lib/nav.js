// KAI-1319 Slice 1 — single source of truth for navigation.
// Retires the three divergent navs (desktop 11-icon strip, mobile 5-tab, More grid)
// into ONE config. 22 routes fold into 6 primary destinations; overlapping surfaces
// become members of a group hub. Every existing route stays reachable (reversible).
import {
  Sun, MessageSquare, LayoutGrid, Heart, Server, Settings,
  ClipboardList, Globe, CheckSquare, Inbox, Sparkles, Activity,
  Wallet, DollarSign, BarChart3, BookOpen, FileText, Users,
} from 'lucide-react'

// The six primary destinations. `hub: true` groups render a GroupHub launcher of
// their `members`; `now`/`chat`/`settings` route straight to their existing page.
export const GROUPS = [
  { key: 'now',   label: 'Now',      path: '/today',       icon: Sun,            primary: true },
  { key: 'chat',  label: 'Chat',     path: '/chat',        icon: MessageSquare,  primary: true },
  {
    key: 'build', label: 'Build',    path: '/build',       icon: LayoutGrid,     primary: true, hub: true,
    blurb: 'Ship the work — plan, sites, tasks.',
    members: [
      { label: 'Plan',        path: '/plane',       icon: ClipboardList, desc: 'Sprints & the roadmap board' },
      { label: 'WordPress',   path: '/wordpress',   icon: Globe,         desc: 'Governed site pipeline' },
      { label: 'Tasks',       path: '/tasks',       icon: CheckSquare,   desc: 'Todoist queue' },
      { label: 'Parking-Lot', path: '/parking-lot', icon: Inbox,         desc: 'Captured items awaiting triage' },
    ],
  },
  {
    key: 'life',  label: 'Life',     path: '/life',        icon: Heart,          primary: true, hub: true,
    blurb: 'The personal domains KAI helps you hold.',
    members: [
      { label: 'Harmony',  path: '/harmony',  icon: Activity, desc: 'Life-domain balance' },
      { label: 'Habits',   path: '/habits',   icon: Heart,    desc: 'Daily habits & streaks' },
      { label: 'Insights', path: '/insights', icon: Sparkles, desc: 'Ember observations' },
    ],
  },
  {
    key: 'system', label: 'System',  path: '/system-hub',  icon: Server,         primary: true, hub: true,
    blurb: 'How KAI runs — health, spend, knowledge.',
    members: [
      { label: 'Health',      path: '/system',    icon: Activity,  desc: 'Host health & ops state' },
      { label: 'Financial',   path: '/financial',  icon: Wallet,    desc: 'Providers, caps, access status' },
      { label: 'Usage',       path: '/usage',      icon: DollarSign, desc: 'API cost & token spend' },
      { label: 'Performance', path: '/models',     icon: BarChart3, desc: 'Model routing & latency' },
      { label: 'Knowledge',   path: '/knowledge',  icon: BookOpen,  desc: 'Decisions & session log' },
      { label: 'Wiki',        path: '/wiki',       icon: FileText,  desc: 'Knowledge vault' },
      { label: 'Advisors',    path: '/advisors',   icon: Users,     desc: 'Manage advisor personas' },
    ],
  },
  { key: 'settings', label: 'Settings', path: '/settings', icon: Settings, primary: true },
]

// Mobile bottom bar carries five slots: Now · Chat · ＋Capture · Build · More.
// "More" opens the overflow (Life / System / Settings) so the phone never hides a
// destination behind an unlabeled icon.
export const MOBILE_PRIMARY = ['now', 'chat', 'build']
export const MOBILE_OVERFLOW = ['life', 'system', 'settings']

export const groupByKey = (key) => GROUPS.find((g) => g.key === key) || null
