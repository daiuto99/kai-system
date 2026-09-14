// KAI-1319 Slice 1 — single source of truth for navigation.
// Retires the three divergent navs (desktop 11-icon strip, mobile 5-tab, More grid)
// into ONE config. 22 routes fold into 6 primary destinations; overlapping surfaces
// become members of a group hub. Every existing route stays reachable (reversible).
import {
  Sun, MessageSquare, LayoutGrid, Heart, Server, Settings,
  ClipboardList, Globe, CheckSquare, Inbox, Sparkles, Activity,
  Wallet, DollarSign, BarChart3, BookOpen, FileText, Users,
} from 'lucide-react'

// KAI-1319 — the finalized life-assistant nav: six flat primary destinations.
// Now · Advisors · Work · Life · System · Settings. General team chat is NOT in
// the dashboard (KAI/Sky/Roads/Coach live on Buzz); Advisors carries the local
// pair. Legacy sub-routes (plane/wordpress/tasks/harmony/habits/usage/…) stay
// reachable by direct URL and via links inside the container pages.
export const GROUPS = [
  { key: 'now',      label: 'Now',      path: '/now',      icon: Sun,        primary: true },
  { key: 'advisors', label: 'Advisors', path: '/advisors', icon: Users,      primary: true },
  { key: 'work',     label: 'Work',     path: '/work',     icon: LayoutGrid, primary: true },
  { key: 'life',     label: 'Life',     path: '/life',     icon: Heart,      primary: true },
  { key: 'system',   label: 'System',   path: '/system',   icon: Server,     primary: true },
  { key: 'settings', label: 'Settings', path: '/settings', icon: Settings,   primary: true },
]

// Mobile bottom bar: Now · Advisors · ＋Capture · Work · More.
// "More" opens the overflow (Life / System / Settings).
export const MOBILE_PRIMARY = ['now', 'advisors', 'work']
export const MOBILE_OVERFLOW = ['life', 'system', 'settings']

export const groupByKey = (key) => GROUPS.find((g) => g.key === key) || null
