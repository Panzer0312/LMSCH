import { useEffect, useState, type FormEvent } from 'react'
import { jukeApi, JukeError } from './api'
import { useDebounced } from './hooks'
import { fmtMs, friendlyError, isSpotifyTrackLink, myPending, queuedUris } from './logic'
import type { JukeboxState, JukeEntry, SearchResult } from './types'
import { Art, Banners, Explicit, NowPlaying, type JukeCtx } from './ui'

export function JoinScreen({ initialName, state, onJoin }: {
  initialName: string
  state: JukeboxState | null
  onJoin: (name: string) => Promise<string | null>
}) {
  const [name, setName] = useState(initialName)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return setErr('Tell us who you are first.')
    setBusy(true)
    setErr(await onJoin(n))
    setBusy(false)
  }
  return (
    <div className="mx-auto flex min-h-full max-w-md flex-col justify-center gap-6 px-4 py-10">
      <div className="text-center">
        <div className="mb-2 text-6xl">🎃🎶</div>
        <h1 className="text-3xl font-black tracking-tight">Spookhub <span className="text-pumpkin">Jukebox</span></h1>
        <p className="mt-2 text-mute">Pick the next song for the party. One queue, everyone’s songs take turns.</p>
      </div>
      {state?.now && (
        <div className="card flex items-center gap-3 text-sm">
          <Art url={state.now.art_url} size="size-10" />
          <div className="min-w-0">
            <p className="text-xs text-mute">Playing now</p>
            <p className="truncate font-semibold">{state.now.title} · <span className="font-normal text-mute">{state.now.artist}</span></p>
          </div>
        </div>
      )}
      <form onSubmit={(e) => void submit(e)} className="card space-y-3">
        <label className="label" htmlFor="jname">Your name</label>
        <input id="jname" className="input min-h-12 text-base" autoFocus autoComplete="nickname" maxLength={24}
          placeholder="e.g. Count Dracula" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn btn-primary min-h-12 w-full text-base font-bold" disabled={busy}>
          {busy ? 'Opening the crypt…' : 'Join the party 🦇'}
        </button>
        {err && <p className="text-sm text-blood">{err}</p>}
      </form>
    </div>
  )
}

export function GuestView({ c }: { c: JukeCtx }) {
  const { state, me, isAdmin } = c
  const mine = myPending(state, me.id)
  const limit = state.settings.per_guest_limit
  const full = !isAdmin && mine >= limit
  return (
    <div className="space-y-4">
      <Banners state={state} isAdmin={isAdmin} />
      <NowPlaying c={c} />
      <div className="flex items-center justify-between text-sm">
        <span className="text-mute">My songs in the queue</span>
        <span className={`rounded-full px-3 py-1 font-semibold tabular-nums ${full ? 'bg-blood/20 text-blood' : 'bg-panel-2'}`}>
          {mine} / {isAdmin ? '∞' : limit}
        </span>
      </div>
      <Search c={c} full={full} />
      <UpNext c={c} />
      <History state={state} />
    </div>
  )
}

function Search({ c, full }: { c: JukeCtx; full: boolean }) {
  const { state } = c
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim().slice(0, 200), 350)
  // Keyed by query so stale responses and an emptied box need no effect-time resets.
  const [res, setRes] = useState<{ q: string; results: SearchResult[] | null; err: string | null }>({ q: '', results: null, err: null })
  const [adding, setAdding] = useState<string | null>(null)
  const { call } = c

  useEffect(() => {
    if (!dq) return
    const ac = new AbortController()
    // Errors stay inline, not as toasts; `call` is only used for the 401 ⇒ rejoin path.
    jukeApi.search(dq, ac.signal)
      .then((r) => setRes({ q: dq, results: r, err: null }))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return
        if (e instanceof JukeError && e.status === 401) void call(() => Promise.reject(e))
        setRes({ q: dq, results: null, err: e instanceof JukeError ? friendlyError(e.status, e.message) : String(e) })
      })
    return () => ac.abort()
  }, [dq, call])
  const current = dq && res.q === dq
  const loading = !!dq && !current
  const results = current ? res.results : null
  const err = current ? res.err : null

  const queued = queuedUris(state)
  const playing = state.now?.uri
  const maxMs = state.settings.max_track_min * 60_000

  const add = async (t: SearchResult) => {
    setAdding(t.uri)
    const r = await c.call(() => jukeApi.add(t.uri))
    setAdding(null)
    if (!r) return
    c.toast(`Added “${t.title}” 🎃`)
    if (isSpotifyTrackLink(q)) setQ('')
  }

  return (
    <section className="space-y-2">
      <input className="input min-h-12 text-base" type="search" enterKeyHint="search" autoComplete="off"
        placeholder={state.spotify.connected ? 'Search songs or paste a Spotify link' : 'Search opens once Spotify is connected'}
        disabled={!state.spotify.connected} value={q} onChange={(e) => setQ(e.target.value)} />
      {loading && <p className="text-sm text-mute">Summoning results…</p>}
      {err && <p className="text-sm text-blood">{err}</p>}
      {results && !loading && results.length === 0 && <p className="text-sm text-mute">Nothing found. Try another spell.</p>}
      {results && results.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-panel">
          {results.map((t) => {
            const isQueued = queued.has(t.uri)
            const blocked = !c.isAdmin && state.settings.accepting === false ? 'Closed'
              : isQueued ? 'Queued ✓'
              : t.uri === playing ? 'Playing'
              : t.explicit && !state.settings.allow_explicit ? 'Explicit off'
              : t.duration_ms > maxMs ? 'Too long'
              : null
            return (
              <li key={t.uri} className="flex items-center gap-3 p-2.5">
                <Art url={t.art_url} size="size-12" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{t.title}{t.explicit && <Explicit />}</p>
                  <p className="truncate text-sm text-mute">{t.artist} · {fmtMs(t.duration_ms)}</p>
                </div>
                <button className={`btn min-h-11 min-w-20 ${blocked ? '' : 'btn-primary'}`}
                  disabled={!!blocked || adding != null || full}
                  title={full ? 'You reached your song limit' : undefined}
                  onClick={() => void add(t)}>
                  {adding === t.uri ? '…' : blocked ?? (full ? 'Limit' : 'Add')}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {full && <p className="text-sm text-pumpkin">You hit your limit. Add more once one of yours has played.</p>}
    </section>
  )
}

export function UpNext({ c, admin = false }: { c: JukeCtx; admin?: boolean }) {
  const { state, me } = c
  const [busy, setBusy] = useState<number | null>(null)
  const act = async (e: JukeEntry, kind: 'remove' | 'bump') => {
    if (kind === 'remove' && !window.confirm(`Remove "${e.title}"?`)) return
    setBusy(e.id)
    await c.call(() => (kind === 'remove' ? jukeApi.remove(e.id) : jukeApi.admin.bump(e.id)),
      kind === 'remove' ? 'Removed' : `“${e.title}” plays next`)
    setBusy(null)
  }
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-bold">Up next <span className="text-sm font-normal text-mute">({state.queue.length})</span></h2>
      {state.queue.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-mute">
          Queue is empty.{state.settings.fallback_uri ? ' Playlist keeps the vibes going.' : ''}
        </p>
      ) : (
        <ol className="space-y-1.5">
          {state.queue.map((e, i) => {
            const yours = e.guest.id === me.id
            return (
              <li key={e.id} className={`flex items-center gap-3 rounded-xl border p-2 ${yours ? 'border-pumpkin/60 bg-pumpkin/10' : 'border-line bg-panel'}`}>
                <span className="w-6 shrink-0 text-center text-sm font-bold tabular-nums text-mute">{i + 1}</span>
                <Art url={e.art_url} size="size-11" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{e.title}{e.explicit && <Explicit />}</p>
                  <p className="truncate text-xs text-mute">
                    {e.artist} · {yours ? <span className="font-semibold text-pumpkin">yours</span> : e.guest.name}
                    {e.bumped && <span className="text-ghost"> · bumped</span>}
                  </p>
                </div>
                {admin && i > 0 && (
                  <button className="btn min-h-11 min-w-11 px-2.5" title="Play next" disabled={busy === e.id} onClick={() => void act(e, 'bump')}>⤒</button>
                )}
                {(yours || admin) && (
                  <button className="btn min-h-11 min-w-11 px-2.5" title="Remove" aria-label={`Remove ${e.title}`} disabled={busy === e.id}
                    onClick={() => void act(e, 'remove')}>✕</button>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}

function History({ state }: { state: JukeboxState }) {
  if (state.history.length === 0) return null
  return (
    <details className="rounded-xl border border-line bg-panel p-3">
      <summary className="cursor-pointer text-sm font-semibold text-mute">Recently played</summary>
      <ul className="mt-2 space-y-1.5">
        {state.history.map((h, i) => (
          <li key={`${h.played_at}-${i}`} className="flex items-center gap-2 text-sm">
            <Art url={h.art_url} size="size-8" />
            <span className="min-w-0 flex-1 truncate">{h.title} <span className="text-mute">· {h.artist}</span></span>
            {h.guest && <span className="shrink-0 text-xs text-mute">{h.guest.name}</span>}
          </li>
        ))}
      </ul>
    </details>
  )
}
