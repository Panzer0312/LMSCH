import { useEffect, useState, type ReactNode } from 'react'
import { jukeApi } from './api'
import { isLoopback, joinUrl } from './logic'
import type { JukeGuest, JukeSettings, SpotifyDevice } from './types'
import { UpNext } from './Guest'
import { copyText } from './hooks'
import { Banners, NowPlaying, Qr, type JukeCtx } from './ui'

function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-bold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

/** In-flight guard: a double tap on Skip must not skip two songs. */
function useGuard(c: JukeCtx) {
  const [busy, setBusy] = useState(false)
  const run = async <T,>(fn: () => Promise<T>, ok?: string) => {
    if (busy) return undefined
    setBusy(true)
    try { return await c.call(fn, ok) } finally { setBusy(false) }
  }
  return { busy, run }
}

export function HostView({ c }: { c: JukeCtx }) {
  const clear = useGuard(c)
  return (
    <div className="space-y-4">
      <Banners state={c.state} isAdmin />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <SpotifySetup c={c} />
          <Controls c={c} />
          <div className="card space-y-3">
            <UpNext c={c} admin />
            {c.state.queue.length > 0 && (
              <button className="btn btn-danger" disabled={clear.busy} onClick={() => {
                if (confirm('Clear the whole queue?')) void clear.run(() => jukeApi.admin.clear(), 'Queue cleared')
              }}>Clear queue</button>
            )}
          </div>
        </div>
        <div className="space-y-4">
          <JoinQr c={c} />
          <SettingsForm c={c} />
          <Guests c={c} />
        </div>
      </div>
    </div>
  )
}

function Step({ n, done, title, children }: { n: number; done: boolean; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className={`grid size-7 shrink-0 place-items-center rounded-full text-sm font-bold ${done ? 'bg-slime text-black' : 'bg-panel-2 text-mute'}`}>
        {done ? '✓' : n}
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <p className="font-semibold">{title}</p>
        {children}
      </div>
    </div>
  )
}

function SpotifySetup({ c }: { c: JukeCtx }) {
  const sp = c.state.spotify
  const [clientId, setClientId] = useState('')
  const [device, setDevice] = useState(sp.device_name)
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  const [pasted, setPasted] = useState('')
  const [devices, setDevices] = useState<SpotifyDevice[] | null>(null)
  const [busy, setBusy] = useState(false)

  let redirectHost = ''
  try { redirectHost = new URL(sp.redirect_uri).hostname } catch { /* shown as-is */ }
  // Spotify sends the browser to the redirect URI; a loopback one only loads on the hub itself.
  const direct = !isLoopback(redirectHost) || isLoopback(location.hostname)

  const run = async <T,>(fn: () => Promise<T>, ok?: string) => {
    setBusy(true)
    const r = await c.call(fn, ok)
    setBusy(false)
    return r
  }

  const connect = async () => {
    const r = await run(() => jukeApi.admin.login())
    if (!r) return
    if (direct) location.assign(r.url)
    else setLoginUrl(r.url)
  }

  const finish = async () => {
    if (await run(() => jukeApi.admin.code(pasted.trim()), 'Spotify connected 🎉')) {
      setPasted('')
      setLoginUrl(null)
    }
  }

  return (
    <Card title="Spotify" right={sp.connected
      ? <span className="rounded-full bg-slime/20 px-3 py-1 text-xs font-semibold text-slime">Connected{sp.account ? ` · ${sp.account}` : ''}</span>
      : <span className="rounded-full bg-pumpkin/20 px-3 py-1 text-xs font-semibold text-pumpkin">Setup needed</span>}>
      {sp.error && <p className="rounded-lg bg-blood/15 p-2 text-sm text-blood">{sp.error}</p>}

      <Step n={1} done={sp.client_id_set} title="Create a Spotify app">
        <ol className="list-decimal space-y-1 pl-5 text-sm text-mute">
          <li>Open <a className="text-ghost underline" href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer">developer.spotify.com/dashboard</a> and create an app (Web API).</li>
          <li>Add this redirect URI:</li>
        </ol>
        <div className="flex gap-2">
          <input className="input font-mono text-xs" readOnly value={sp.redirect_uri} onFocus={(e) => e.currentTarget.select()} />
          <button className="btn shrink-0" onClick={() => void copyText(sp.redirect_uri).then((ok) =>
            c.toast(ok ? 'Copied' : 'Copy failed, select it manually', ok ? 'ok' : 'err'))}>Copy</button>
        </div>
        <p className="text-sm text-mute">Then paste the app’s Client ID{sp.client_id_set ? ' (saved; paste a new one to replace)' : ''}:</p>
        <div className="flex gap-2">
          <input className="input font-mono" placeholder="Client ID" autoComplete="off" spellCheck={false}
            value={clientId} onChange={(e) => setClientId(e.target.value)} />
          <button className="btn btn-primary shrink-0" disabled={busy || !clientId.trim()}
            onClick={() => void run(() => jukeApi.admin.spotify({ client_id: clientId.trim() }), 'Client ID saved').then((r) => r && setClientId(''))}>
            Save
          </button>
        </div>
      </Step>

      <Step n={2} done={sp.connected} title="Log in with the host’s Spotify (Premium)">
        {sp.connected ? (
          <button className="btn" disabled={busy} onClick={() => {
            if (confirm('Disconnect Spotify? Music stops.')) void run(() => jukeApi.admin.logout(), 'Spotify disconnected')
          }}>Disconnect</button>
        ) : (
          <>
            <button className="btn btn-primary" disabled={busy || !sp.client_id_set} onClick={() => void connect()}>Connect Spotify</button>
            {loginUrl && (
              <a className="btn ml-2" href={loginUrl} target="_blank" rel="noreferrer">Open Spotify login ↗</a>
            )}
            {(!direct || loginUrl) && (
              <div className="space-y-2 rounded-lg border border-line bg-bg p-3 text-sm">
                <p className="text-mute">
                  This browser isn’t on the hub, so after you click <b>Agree</b> Spotify opens an address starting
                  with <code className="text-ink">{sp.redirect_uri.split('?')[0].replace(/\/api\/.*/, '')}</code> that won’t load.
                  That’s expected: copy the whole address from the address bar and paste it here.
                </p>
                <div className="flex gap-2">
                  <input className="input font-mono text-xs" placeholder="http://127.0.0.1…/callback?code=…" autoComplete="off" spellCheck={false}
                    value={pasted} onChange={(e) => setPasted(e.target.value)} />
                  <button className="btn btn-primary shrink-0" disabled={busy || !pasted.includes('code=')} onClick={() => void finish()}>Finish</button>
                </div>
              </div>
            )}
          </>
        )}
      </Step>

      <Step n={3} done={sp.device_found} title={`Speaker “${sp.device_name}”`}>
        {sp.device_found
          ? <p className="text-sm text-slime">Found. Music plays through the hub.</p>
          : (
            <p className="text-sm text-mute">
              Not visible yet. One time only: on a phone in the same WiFi, open Spotify, tap the devices icon
              and pick <b className="text-ink">{sp.device_name}</b>. After that the hub finds it on its own.
            </p>
          )}
        <div className="flex gap-2">
          <input className="input" value={device} onChange={(e) => setDevice(e.target.value)} aria-label="Speaker name" />
          <button className="btn shrink-0" disabled={busy || !device.trim() || device.trim() === sp.device_name}
            onClick={() => void run(() => jukeApi.admin.spotify({ device_name: device.trim() }), 'Speaker name saved')}>Save</button>
        </div>
        {sp.connected && (
          <button className="btn" disabled={busy} onClick={() => void run(() => jukeApi.admin.devices()).then((d) => d && setDevices(d))}>
            Show Spotify devices
          </button>
        )}
        {devices && (
          devices.length === 0
            ? <p className="text-sm text-mute">Spotify reports no devices.</p>
            : (
              <ul className="space-y-1 text-sm">
                {devices.map((d) => (
                  <li key={d.id} className="flex justify-between gap-2 rounded-lg bg-bg px-3 py-1.5">
                    <span className="truncate">{d.name}</span>
                    <span className="shrink-0 text-mute">{d.type}{d.is_active ? ' · active' : ''}</span>
                  </li>
                ))}
              </ul>
            )
        )}
      </Step>
    </Card>
  )
}

function JoinQr({ c }: { c: JukeCtx }) {
  const url = joinUrl(location, c.state.port80)
  const [big, setBig] = useState(false)
  const local = isLoopback(location.hostname)
  return (
    <Card title="Invite guests" right={<button className="btn" onClick={() => setBig(true)}>Show big</button>}>
      {local && (
        <p className="rounded-lg bg-pumpkin/15 p-2 text-sm text-pumpkin">
          Open this page via the hub’s IP so the QR works (this browser uses {location.hostname}).
        </p>
      )}
      <div className="flex flex-col items-center gap-3 sm:flex-row">
        <Qr text={url} className="size-48 shrink-0 rounded-xl" />
        <div className="min-w-0 text-center sm:text-left">
          <p className="text-sm text-mute">Scan or open</p>
          <p className="break-all text-2xl font-black text-pumpkin">{url}</p>
          <p className="mt-1 text-sm text-mute">{c.state.guests_active} guest{c.state.guests_active === 1 ? '' : 's'} active</p>
        </div>
      </div>
      {big && (
        <button type="button" onClick={() => setBig(false)} aria-label="Close"
          className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-bg p-6 text-center">
          <p className="text-3xl font-black sm:text-5xl">🎃 Pick the music! 🎶</p>
          <Qr text={url} className="aspect-square w-full max-w-[min(80vw,60vh)] rounded-2xl" />
          <p className="break-all text-3xl font-black text-pumpkin sm:text-6xl">{url}</p>
          <p className="text-sm text-mute">Tap anywhere to close</p>
        </button>
      )}
    </Card>
  )
}

function Controls({ c }: { c: JukeCtx }) {
  const { state } = c
  const [vol, setVol] = useState<number | null>(null)
  const shown = vol ?? state.settings.volume
  const commit = async () => {
    if (vol == null) return
    const v = vol
    await c.call(() => jukeApi.admin.settings({ volume: v }))
    setVol(null)
  }
  const paused = state.paused === 'admin'
  const g = useGuard(c)
  return (
    <NowPlaying c={c} showVote={false}>
      <div className="grid grid-cols-2 gap-2">
        <button className="btn min-h-12 text-base" disabled={g.busy || state.paused === 'show'}
          onClick={() => void g.run(() => (paused ? jukeApi.admin.resume() : jukeApi.admin.pause()))}>
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        <button className="btn min-h-12 text-base" disabled={g.busy || (!state.now && state.queue.length === 0)}
          onClick={() => void g.run(() => jukeApi.admin.skip(state.now?.uri))}>⏭ Skip</button>
      </div>
      {state.paused === 'show' && <p className="text-xs text-mute">The show has the speaker. Music resumes when it ends.</p>}
      <label className="flex items-center gap-3 text-sm">
        <span className="w-14 text-mute">Volume</span>
        <input type="range" min={0} max={100} step={1} className="flex-1 accent-pumpkin" value={shown}
          onChange={(e) => setVol(Number(e.target.value))}
          onPointerUp={() => void commit()} onKeyUp={() => void commit()} onTouchEnd={() => void commit()} />
        <span className="w-8 text-right tabular-nums">{shown}</span>
      </label>
    </NowPlaying>
  )
}

interface Draft { per_guest_limit: string; fallback_uri: string; skip_pct: string; max_track_min: string }
const toDraft = (s: JukeSettings): Draft => ({
  per_guest_limit: String(s.per_guest_limit),
  fallback_uri: s.fallback_uri ?? '',
  skip_pct: String(Math.round(s.skip_vote_ratio * 100)),
  max_track_min: String(s.max_track_min),
})

function SettingsForm({ c }: { c: JukeCtx }) {
  const s = c.state.settings
  const [draft, setDraft] = useState<Draft>(() => toDraft(s))
  const [err, setErr] = useState<string | null>(null)
  const set = (k: keyof Draft, v: string) => setDraft({ ...draft, [k]: v })
  const g = useGuard(c)
  const toggle = (k: 'accepting' | 'allow_explicit') => void g.run(() => jukeApi.admin.settings({ [k]: !s[k] }))

  const save = async () => {
    const limit = Number(draft.per_guest_limit)
    const pct = Number(draft.skip_pct)
    const max = Number(draft.max_track_min)
    if (!Number.isInteger(limit) || limit < 1 || limit > 20) return setErr('Songs per guest: 1–20.')
    if (!Number.isFinite(pct) || pct < 10 || pct > 100) return setErr('Skip votes: 10–100 %.')
    if (!Number.isInteger(max) || max < 1 || max > 30) return setErr('Max song length: 1–30 min.')
    setErr(null)
    const r = await g.run(() => jukeApi.admin.settings({
      per_guest_limit: limit, skip_vote_ratio: pct / 100, max_track_min: max,
      fallback_uri: draft.fallback_uri.trim() || null,
    }), 'Settings saved')
    if (r) setDraft(toDraft(r))
  }

  return (
    <Card title="Rules">
      <div className="flex flex-col gap-3">
        <Switch on={s.accepting} onClick={() => toggle('accepting')} label="Guests can add songs" />
        <Switch on={s.allow_explicit} onClick={() => toggle('allow_explicit')} label="Allow explicit songs" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Songs per guest" hint="1–20, host unlimited">
          <input className="input" type="number" inputMode="numeric" min={1} max={20} value={draft.per_guest_limit}
            onChange={(e) => set('per_guest_limit', e.target.value)} />
        </Field>
        <Field label="Max length (min)" hint="1–30">
          <input className="input" type="number" inputMode="numeric" min={1} max={30} value={draft.max_track_min}
            onChange={(e) => set('max_track_min', e.target.value)} />
        </Field>
        <Field label="Skip votes (%)" hint="of active guests, min 2">
          <input className="input" type="number" inputMode="numeric" min={10} max={100} step={5} value={draft.skip_pct}
            onChange={(e) => set('skip_pct', e.target.value)} />
        </Field>
      </div>
      <Field label="Fallback playlist" hint="Spotify playlist or album link, plays shuffled when the queue is empty">
        <input className="input" placeholder="https://open.spotify.com/playlist/…" autoComplete="off" spellCheck={false}
          value={draft.fallback_uri} onChange={(e) => set('fallback_uri', e.target.value)} />
      </Field>
      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={g.busy} onClick={() => void save()}>Save rules</button>
        <button className="btn" onClick={() => { setDraft(toDraft(s)); setErr(null) }}>Reset</button>
      </div>
      {err && <p className="text-sm text-blood">{err}</p>}
    </Card>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-mute">{hint}</span>}
    </label>
  )
}

function Switch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={onClick}
      className="flex min-h-11 items-center justify-between gap-3 rounded-lg bg-bg px-3 text-left text-sm">
      {label}
      <span className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? 'bg-pumpkin' : 'bg-line'}`}>
        <span className={`absolute top-0.5 size-5 rounded-full bg-white transition-all ${on ? 'left-5.5' : 'left-0.5'}`} />
      </span>
    </button>
  )
}

function Guests({ c }: { c: JukeCtx }) {
  const { call } = c
  // The host's socket pushes `jukebox_guests`; the fetch covers first paint and Refresh.
  // Whichever arrived last wins.
  const [fetched, setFetched] = useState<{ list: JukeGuest[]; at: number } | null>(null)
  const [rev, setRev] = useState(0)
  useEffect(() => {
    let live = true
    void call(() => jukeApi.admin.guests()).then((g) => { if (live && g) setFetched({ list: g, at: Date.now() }) })
    return () => { live = false }
  }, [rev, call])
  const guests = c.guests && (!fetched || c.guestsMs >= fetched.at) ? c.guests : fetched?.list ?? null

  const sorted = guests && [...guests].sort((a, b) => Number(b.is_admin) - Number(a.is_admin) || b.last_seen - a.last_seen)
  const ban = async (g: JukeGuest) => {
    if (!g.banned && !confirm(`Ban ${g.name}? Their queued songs are removed.`)) return
    if (await call(() => jukeApi.admin.ban(g.id, !g.banned), g.banned ? `${g.name} unbanned` : `${g.name} banned`)) setRev((r) => r + 1)
  }
  const promote = async (g: JukeGuest) => {
    if (!confirm(`Make ${g.name} the host? You lose host controls.`)) return
    await call(() => jukeApi.admin.makeAdmin(g.id), `${g.name} is the host now`)
  }
  return (
    <Card title="Guests" right={<button className="btn" onClick={() => setRev((r) => r + 1)}>Refresh</button>}>
      {!sorted ? <p className="text-sm text-mute">Loading…</p> : sorted.length === 0 ? <p className="text-sm text-mute">No guests yet.</p> : (
        <ul className="space-y-1.5">
          {sorted.map((g) => (
            <li key={g.id} className={`flex flex-wrap items-center gap-2 rounded-lg bg-bg px-3 py-2 ${g.banned ? 'opacity-60' : ''}`}>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">
                  {g.name}
                  {g.id === c.me.id && <span className="text-mute"> (you)</span>}
                  {g.is_admin && <span className="ml-2 rounded bg-pumpkin/20 px-1.5 text-xs text-pumpkin">host</span>}
                  {g.banned && <span className="ml-2 rounded bg-blood/20 px-1.5 text-xs text-blood">banned</span>}
                </p>
                <p className="text-xs text-mute">{g.pending} queued · seen {ago(g.last_seen)}</p>
              </div>
              {!g.is_admin && (
                <>
                  <button className="btn min-h-10" onClick={() => void promote(g)} disabled={g.banned}>Make host</button>
                  <button className={`btn min-h-10 ${g.banned ? '' : 'btn-danger'}`} onClick={() => void ban(g)}>{g.banned ? 'Unban' : 'Ban'}</button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)} h ago`
}
