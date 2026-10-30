import { StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// /music is the guest jukebox: no PIN, no main UI, and its bundle stays small for phones.
if (/^\/jukebox(\/|$)/.test(location.pathname)) history.replaceState(null, '', `/music${location.search}`)
const isJukebox = /^\/music(\/|$)/.test(location.pathname)

const root = createRoot(document.getElementById('root')!)
const render = (node: ReactNode) => root.render(<StrictMode>{node}</StrictMode>)

if (isJukebox) {
  void import('./pages/Jukebox').then(({ default: Jukebox }) => render(<Jukebox />))
} else {
  void Promise.all([import('react-router-dom'), import('./App'), import('./store'), import('./components/LoginGate')])
    .then(([{ BrowserRouter }, { default: App }, { LiveProvider }, { LoginGate }]) => render(
      <BrowserRouter>
        <LoginGate>
          <LiveProvider>
            <App />
          </LiveProvider>
        </LoginGate>
      </BrowserRouter>,
    ))
}
