import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Chat from './pages/Chat'
import Now from './pages/Now'
import Work from './pages/Work'
import Life from './pages/Life'
import Today from './pages/Today'
import Harmony from './pages/Harmony'
import ParkingLot from './pages/ParkingLot'
import Insights from './pages/Insights'
import Tasks from './pages/Tasks'
import More from './pages/More'
import Settings from './pages/Settings'
import Habits from './pages/Habits'
import Knowledge from './pages/Knowledge'
import Performance from './pages/Performance'
import PlaneTasks from './pages/PlaneTasks'
import Advisors from './pages/Advisors'
import Wiki from './pages/Wiki'
import WordPress from './pages/WordPress'
import Usage from './pages/Usage'
import Financial from './pages/Financial'
import System from './pages/System'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/now" replace />} />
        <Route path="/now" element={<Now />} />
        {/* back-compat: old /today route now redirects to the canonical /now */}
        <Route path="/today" element={<Navigate to="/now" replace />} />
        {/* KAI-1319 Slice 2 — old kitchen-sink home kept reachable for fallback/compare */}
        <Route path="/today-classic" element={<Today />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:advisorId" element={<Chat />} />

        {/* KAI-1319 — finalized life-assistant pages */}
        <Route path="/work" element={<Work />} />
        <Route path="/life" element={<Life />} />
        {/* back-compat: retired group-hub routes fold into the new pages */}
        <Route path="/build" element={<Navigate to="/work" replace />} />
        <Route path="/system-hub" element={<Navigate to="/system" replace />} />

        <Route path="/harmony" element={<Harmony />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/habits" element={<Habits />} />
        <Route path="/parking-lot" element={<ParkingLot />} />
        <Route path="/insights" element={<Insights />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/more" element={<More />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/models" element={<Performance />} />
        <Route path="/plane" element={<PlaneTasks />} />
        <Route path="/advisors" element={<Advisors />} />
        <Route path="/wiki" element={<Wiki />} />
        <Route path="/wordpress" element={<WordPress />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/financial" element={<Financial />} />
        <Route path="/system" element={<System />} />
      </Routes>
    </Layout>
  )
}
