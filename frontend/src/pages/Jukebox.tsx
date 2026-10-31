import { useCallback, useEffect, useMemo, useState } from 'react'
import { jukeApi, JukeError, loadSaved, saveSaved, setToken, type Saved } from '../jukebox/api'
import { useJukeboxLive, useToasts } from '../jukebox/hooks'
import { friendlyError } from '../jukebox/logic'
import type { JukeGuestRef, JukeRole } from '../jukebox/types'
import { GuestView, JoinScreen } from '../jukebox/Guest'
import { HostView } from '../jukebox/Host'
import { Toasts, type JukeCtx } from '../jukebox/ui'

/** Guest page at /music. Rendered without LoginGate/LiveProvider: guests never see the PIN or main UI. */
export default function Jukebox() {
  const [saved, setSaved] = useState<Saved>(loadSaved)
  const [me, setMe] = useState<{ guest: JukeGuestRef; role: JukeRole } | null>(null)
  const [checkErr, setCheckErr] = useState<string | null>(null)
  const [checkRev, setCheckRev] = useState(0)
  // Spotify OAuth callback lands on /music?spotify=ok|error.
  const [spotifyResult] = useState(() => new URLSearchParams(location.search).get('spotify'))
  const [tab, setTab] = useState<'party' | 'host'>(spotifyResult === 'ok' ? 'host' : 'party')
  const live = useJukeboxLive(saved.token)
  const { toasts, push } = useToasts()

  useEffect(() => { document.title = 'Spookhub Jukebox 🎃' }, [])

  useEffect(() => {
    if (!spotifyResult) return
    if (spotifyResult === 'ok') push('Spotify connected 🎉')
    else push('Spotify login failed. Try again from the Host tab.', 'err')
    history.replaceState(null, '', '/music')
  }, [spotifyResult, push])

  const leave = useCallback(() => {
    setToken(null)
    setSaved((s) => {
      const n = { token: null, name: s.name }
      saveSaved(n)
      return n
    })
    setMe(null)
  }, [])

  useEffect(() => {
    if (!saved.token) return
    let alive = true
    jukeApi.me()
      .then((r) => { if (alive) setMe(r) })
      .catch((e: unknown) => {
        if (!alive) return
        if (e instanceof JukeError && e.status === 401) leave()
        else setCheckErr(e instanceof Error ? e.message : String(e))
      })
    return () => { alive = false }
  }, [saved.token, checkRev, leave])

  const join = useCallback(async (name: string): Promise<string | null> => {
    try {
      const r = await jukeApi.join(name)
      setToken(r.token)
      const n = { token: r.token, name }
      saveSaved(n)
      setSaved(n)
      setMe({ guest: r.guest, role: r.role })
      if (r.role === 'admin') push('You’re the host 👑')
      return null
    } catch (e) {
      return e instanceof JukeError ? friendlyError(e.status, e.message) : String(e)
    }
  }, [push])

  const call = useCallback(async <T,>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> => {
    try {
      const r = await fn()
      if (okText) push(okText)
      return r
    } catch (e) {
      if (e instanceof JukeError && e.status === 401) {
        leave()
        push('Your session expired. Join again.', 'err')
      } else if (e instanceof JukeError) push(friendlyError(e.status, e.message), 'err')
      else push(String(e), 'err')
      return undefined
    }
  }, [leave, push])

  const state = live.state
  // admin_id is live (hand-over, reset); the role from /me or /join only covers the first paint.
  const isAdmin = me ? (state ? state.admin_id === me.guest.id : me.role === 'admin') : false
  const ctx: JukeCtx | null = useMemo(() => (state && me
    ? { state, recvMs: live.recvMs, guests: live.guests, guestsMs: live.guestsMs, me: me.guest, isAdmin, call, toast: push }
    : null), [state, live.recvMs, live.guests, live.guestsMs, me, isAdmin, call, push])

  // Guests on the wrong WiFi would otherwise stare at the loading line forever.
  const loading = !!saved.token && !checkErr && !ctx
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (!loading) return
    const t = setTimeout(() => setSlow(true), 5000)
    return () => { clearTimeout(t); setSlow(false) }
  }, [loading])

  const claimHost = async () => {
    if (!me) return
    const r = await call(() => jukeApi.join(me.guest.name))
    if (r) {
      setMe({ guest: r.guest, role: r.role })
      push(r.role === 'admin' ? 'You’re the host 👑' : 'Someone else was faster', r.role === 'admin' ? 'ok' : 'err')
      if (r.role === 'admin') setTab('host')
    }
  }

  let body
  if (!saved.token) {
    body = <JoinScreen initialName={saved.name} state={state} onJoin={join} />
  } else if (checkErr) {
    body = (
      <div className="mx-auto max-w-md space-y-3 px-4 py-16 text-center">
        <p className="text-4xl">🕸️</p>
        <p className="text-blood">{checkErr}</p>
        <button className="btn btn-primary min-h-12" onClick={() => { setCheckErr(null); setCheckRev((r) => r + 1) }}>Try again</button>
      </div>
    )
  } else if (!ctx) {
    body = (
      <div className="space-y-2 px-4 py-16 text-center">
        <p className="text-mute">Summoning the jukebox…</p>
        {slow && <p className="text-sm text-blood">Can’t reach the hub – check you’re on the party WiFi.</p>}
      </div>
    )
  } else {
    const showHost = ctx.isAdmin && tab === 'host'
    body = (
      <div className={`mx-auto space-y-4 px-4 pb-24 pt-4 ${showHost ? 'max-w-6xl' : 'max-w-xl'}`}>
        {ctx.isAdmin && (
          <div className="grid grid-cols-2 gap-1 rounded-xl bg-panel p-1">
            {(['party', 'host'] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`min-h-11 rounded-lg text-sm font-semibold ${tab === t ? 'bg-pumpkin text-black' : 'text-mute'}`}>
                {t === 'party' ? '🎶 Party' : '👑 Host'}
              </button>
            ))}
          </div>
        )}
        {showHost ? <HostView c={ctx} /> : <GuestView c={ctx} />}
        {ctx.state.admin_id === null && (
          <p className="text-center text-sm text-mute">
            No host yet. <button className="text-ghost underline" onClick={() => void claimHost()}>Become host</button>
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="min-h-full bg-[radial-gradient(ellipse_at_top,rgba(255,122,26,0.12),transparent_60%)]">
      {saved.token && (
        <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
            <span className="text-lg font-black tracking-tight">🎃 <span className="text-pumpkin">Jukebox</span></span>
            <span className={`size-2 rounded-full ${live.connected ? 'bg-slime' : 'animate-pulse bg-blood'}`}
              title={live.connected ? 'live' : 'reconnecting'} />
            {me && (
              <span className="ml-auto max-w-[45%] truncate rounded-full bg-panel-2 px-3 py-1 text-sm">
                {isAdmin ? '👑 ' : '🦇 '}{me.guest.name}
              </span>
            )}
          </div>
          {!live.connected && <p className="pb-2 text-center text-xs text-blood">Reconnecting…</p>}
        </header>
      )}
      {body}
      <Toasts toasts={toasts} />
    </div>
  )
}
