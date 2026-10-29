import { NavLink, Route, Routes } from 'react-router-dom'
import { BlackoutButton } from './components/BlackoutButton'
import { useLive } from './live'
import Dashboard from './pages/Dashboard'
import Profiles from './pages/Profiles'
import Boards from './pages/Boards'
import MediaPage from './pages/Media'
import SettingsPage from './pages/Settings'
import TimelineEditor from './pages/TimelineEditor'
import Setup from './pages/Setup'
import ShowMode from './pages/ShowMode'

const nav = [
  ['/', 'Dashboard'],
  ['/profiles', 'Profiles'],
  ['/boards', 'Boards'],
  ['/setup', 'Setup'],
  ['/media', 'Media'],
  ['/settings', 'Settings'],
] as const

export default function App() {
  const { connected, settings } = useLive()
  const locked = !!settings?.show_lock
  const items = locked ? nav.filter(([to]) => to !== '/profiles' && to !== '/media') : nav
  return (
    <div className="mx-auto flex min-h-full max-w-6xl flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
        <div className="flex items-center gap-3 px-4 py-2">
          <span className="text-lg font-bold tracking-tight">🎃 Spook<span className="text-pumpkin">hub</span></span>
          <span className={`text-xs ${connected ? 'text-slime' : 'text-blood'}`}>{connected ? '● live' : '● offline'}</span>
          {locked && <span className="rounded-full bg-pumpkin/20 px-2 py-0.5 text-xs font-semibold text-pumpkin">Show mode on</span>}
          <div className="ml-auto"><BlackoutButton /></div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 text-sm">
          {items.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) => `whitespace-nowrap rounded-lg px-3 py-1.5 ${isActive ? 'bg-panel-2 text-pumpkin' : 'text-mute hover:text-ink'}`}>
              {label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="flex-1 px-4 py-4">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/profiles" element={<Profiles />} />
          <Route path="/profiles/:id" element={<Profiles />} />
          <Route path="/profiles/:id/timeline" element={<TimelineEditor />} />
          <Route path="/boards" element={<Boards />} />
          <Route path="/media" element={<MediaPage />} />
          <Route path="/setup" element={<Setup />} />
          <Route path="/show" element={<ShowMode />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
