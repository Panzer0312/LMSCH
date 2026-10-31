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
import VideoSetup from './pages/VideoSetup'
import ShowMode from './pages/ShowMode'
import Moments from './pages/Moments'

const nav = [
  ['/', 'Dashboard'],
  ['/profiles', 'Profiles'],
  ['/boards', 'Boards'],
  ['/setup', 'Setup'],
  ['/video', 'Video'],
  ['/media', 'Media'],
  ['/moments', 'Moments'],
  ['/settings', 'Settings'],
] as const

export default function App() {
  const { connected, settings } = useLive()
  const locked = !!settings?.show_lock
  const items = locked ? nav.filter(([to]) => to !== '/profiles' && to !== '/media' && to !== '/video') : nav
  return (
    <div className="mx-auto flex min-h-full max-w-6xl flex-col">
      {/* Above the moments kiosk overlay (z-40): stale state there is as dangerous as anywhere. */}
      {!connected && (
        <div className="fixed inset-x-0 top-0 z-50 bg-blood px-4 py-1 text-center text-sm font-semibold text-white">
          Offline – reconnecting. Shown state may be stale.
        </div>
      )}
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
          <span className="text-lg font-bold tracking-tight">🎃 Spook<span className="text-pumpkin">hub</span></span>
          <span className={`text-xs ${connected ? 'text-slime' : 'text-blood'}`}>{connected ? '● live' : '● offline'}</span>
          {locked && <span className="rounded-full bg-pumpkin/20 px-2 py-0.5 text-xs font-semibold text-pumpkin">Locked</span>}
          <div className="ml-auto"><BlackoutButton /></div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 text-sm">
          {items.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) => `whitespace-nowrap rounded-lg px-3 py-1.5 ${isActive ? 'bg-panel-2 text-pumpkin' : 'text-mute hover:text-ink'}`}>
              {label}
            </NavLink>
          ))}
          {/* Full page load: /music lives outside the router and the PIN gate. */}
          <a href="/music" className="whitespace-nowrap rounded-lg px-3 py-1.5 text-mute hover:text-ink">Jukebox</a>
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
          <Route path="/video" element={<VideoSetup />} />
          <Route path="/show" element={<ShowMode />} />
          <Route path="/moments" element={<Moments />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
