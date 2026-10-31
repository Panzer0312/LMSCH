import { useMemo, useState, type ReactNode } from 'react'
import qrcode from 'qrcode-generator'
import { jukeApi } from './api'
import type { JukeboxState, JukeGuest, JukeGuestRef, JukeNow } from './types'
import type { Toast } from './hooks'
import { useTick } from './hooks'
import { fmtMs, progressMs, voteKey } from './logic'

/** What every jukebox view needs; built once in the page. */
export interface JukeCtx {
  state: JukeboxState
  recvMs: number
  /** Host only: pushed guest list and its local arrival time. */
  guests: JukeGuest[] | null
  guestsMs: number
  me: JukeGuestRef
  isAdmin: boolean
  /** Runs an API call; shows errors as toasts and handles 401 (back to join). */
  call: <T>(fn: () => Promise<T>, okText?: string) => Promise<T | undefined>
  toast: (text: string, kind?: 'ok' | 'err') => void
}

export function Art({ url, size = 'size-14' }: { url: string | null; size?: string }) {
  return url
    ? <img src={url} alt="" loading="lazy" className={`${size} shrink-0 rounded-lg object-cover`} />
    : <div className={`${size} grid shrink-0 place-items-center rounded-lg bg-panel-2 text-2xl`}>🎃</div>
}

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id}
          className={`max-w-md rounded-xl px-4 py-3 text-sm font-medium shadow-lg shadow-black/50 ${t.kind === 'err' ? 'bg-blood text-white' : 'bg-slime text-black'}`}>
          {t.text}
        </div>
      ))}
    </div>
  )
}

export function Banners({ state, isAdmin }: { state: JukeboxState; isAdmin: boolean }) {
  const items: ReactNode[] = []
  if (state.paused === 'show') items.push(<Banner key="show" tone="ghost">Paused for the show 👻 Music is back right after.</Banner>)
  else if (state.paused === 'admin') items.push(<Banner key="admin" tone="mute">⏸ The host paused the music.</Banner>)
  if (!state.settings.accepting) {
    items.push(<Banner key="closed" tone="pumpkin">
      🚫 Requests are closed{isAdmin ? ' for guests (you can still add).' : ' right now.'}
    </Banner>)
  }
  if (!state.spotify.connected && !isAdmin) items.push(<Banner key="sp" tone="mute">🎧 The DJ booth is still being set up.</Banner>)
  return items.length ? <div className="space-y-2">{items}</div> : null
}

function Banner({ tone, children }: { tone: 'ghost' | 'mute' | 'pumpkin'; children: ReactNode }) {
  const cls = { ghost: 'border-ghost/50 bg-ghost/15 text-ghost', mute: 'border-line bg-panel-2 text-ink', pumpkin: 'border-pumpkin/50 bg-pumpkin/15 text-pumpkin' }[tone]
  return <div className={`rounded-xl border px-4 py-3 text-sm font-medium ${cls}`}>{children}</div>
}

export function ProgressBar({ now, recvMs }: { now: JukeNow; recvMs: number }) {
  const t = useTick(now.is_playing)
  const p = progressMs(now, recvMs, t)
  const pct = now.duration_ms > 0 ? (p / now.duration_ms) * 100 : 0
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-line">
        <div className="h-full rounded-full bg-gradient-to-r from-pumpkin to-blood transition-[width] duration-500 ease-linear" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-xs tabular-nums text-mute">
        <span>{fmtMs(p)}</span><span>{fmtMs(now.duration_ms)}</span>
      </div>
    </div>
  )
}

/** Now playing card; `children` adds host controls below. */
export function NowPlaying({ c, showVote = true, children }: { c: JukeCtx; showVote?: boolean; children?: ReactNode }) {
  const { state, recvMs } = c
  const now = state.now
  if (!now) {
    return (
      <div className="card flex items-center gap-4">
        <div className="grid size-20 shrink-0 place-items-center rounded-xl bg-panel-2 text-4xl">🦇</div>
        <div>
          <p className="font-semibold">Silence in the crypt…</p>
          <p className="text-sm text-mute">Add a song to get the party going.</p>
        </div>
      </div>
    )
  }
  return (
    <div className="card space-y-3 border-pumpkin/40 bg-gradient-to-br from-panel to-panel-2">
      <div className="flex items-center gap-4">
        <div className="relative">
          <Art url={now.art_url} size="size-20 sm:size-24" />
          {now.is_playing && !state.paused && <span className="absolute -right-1 -top-1 animate-bounce text-lg">🎵</span>}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-pumpkin">
            {state.paused ? 'Paused' : now.is_playing ? 'Now playing' : 'Paused'}
          </p>
          <p className="truncate text-lg font-bold leading-tight">{now.title}{now.explicit && <Explicit />}</p>
          <p className="truncate text-sm text-mute">{now.artist}</p>
          <p className="truncate text-xs text-mute">
            {now.requested_by ? <>added by <span className="text-ink">{now.requested_by.id === c.me.id ? 'you' : now.requested_by.name}</span></> : 'Spookhub radio'}
          </p>
        </div>
      </div>
      <ProgressBar now={now} recvMs={recvMs} />
      {showVote && <VoteSkip c={c} now={now} />}
      {children}
    </div>
  )
}

export const Explicit = () => <span title="explicit" className="ml-1.5 inline-block rounded bg-mute/30 px-1 align-middle text-[10px] font-bold text-mute">E</span>

// The vote lives in the server set, but the button state would reset on reload or tab switch.
const VOTE_STORE = 'juke.voted'
function readVoted(): string | null {
  try { return sessionStorage.getItem(VOTE_STORE) } catch { return null }
}

function VoteSkip({ c, now }: { c: JukeCtx; now: JukeNow }) {
  const [voted, setVoted] = useState<string | null>(readVoted)
  const [busy, setBusy] = useState(false)
  const key = voteKey(now)
  const mine = now.requested_by?.id === c.me.id
  const instant = mine || c.isAdmin
  const done = voted === key
  const onClick = async () => {
    setBusy(true)
    const r = await c.call(() => jukeApi.voteSkip())
    setBusy(false)
    if (!r) return
    setVoted(key)
    // Radio tracks have no entry id, so their key would also match a later replay of the same song.
    if (now.entry_id != null) {
      try { sessionStorage.setItem(VOTE_STORE, key) } catch { /* private mode: in-memory state still works */ }
    }
    if (r.skipped) c.toast(mine ? 'Skipped your song' : 'Skipped 💀')
    else c.toast(`Vote counted (${r.votes}/${r.votes_needed})`)
  }
  const label = mine ? 'Skip my song' : c.isAdmin ? 'Skip' : done ? `Voted · ${now.votes}/${now.votes_needed}` : `Vote skip · ${now.votes}/${now.votes_needed}`
  return (
    <button className="btn min-h-12 w-full text-base" disabled={busy || (done && !instant)} onClick={() => void onClick()}>
      ⏭ {label}
    </button>
  )
}

export function Qr({ text, className = '' }: { text: string; className?: string }) {
  const { n, d } = useMemo(() => {
    const q = qrcode(0, 'M')
    q.addData(text)
    q.make()
    const n = q.getModuleCount()
    let d = ''
    for (let r = 0; r < n; r++) for (let col = 0; col < n; col++) if (q.isDark(r, col)) d += `M${col},${r}h1v1h-1z`
    return { n, d }
  }, [text])
  return (
    <svg viewBox={`-4 -4 ${n + 8} ${n + 8}`} className={className} role="img" aria-label={`QR code for ${text}`} shapeRendering="crispEdges">
      <rect x={-4} y={-4} width={n + 8} height={n + 8} fill="#fff" />
      <path d={d} fill="#000" />
    </svg>
  )
}
