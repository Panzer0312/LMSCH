import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api'
import { useAdoptedBoards, useGroups, useLive } from '../live'
import type { BoardGroup, CheckStatus, LightState, Media, Settings } from '../types'
import { BlackoutButton } from '../components/BlackoutButton'
import { ErrorText, StatusDot } from '../components/ui'
import { fmtTime, useAction, useSettle } from '../lib'

const dotColor: Record<CheckStatus, string> = { ok: 'bg-slime', warn: 'bg-pumpkin', fail: 'bg-blood' }

function Header() {
  const { show, settings, preflight } = useLive()
  const [lockBusy, setLockBusy] = useState(false)
  const [lockErr, setLockErr] = useState<string | null>(null)
  const locked = settings?.show_lock ?? false

  const toggleLock = async () => {
    setLockBusy(true)
    setLockErr(null)
    try {
      try {
        await api.showLock(!locked)
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 401 || !locked || !/wrong pin/i.test(e.message)) throw e
        const pin = window.prompt('PIN to unlock')
        if (pin == null) return
        await api.showLock(false, pin)
      }
    } catch (e) {
      setLockErr(e instanceof Error ? e.message : String(e))
    } finally {
      setLockBusy(false)
    }
  }

  const p = show?.profile ?? null
  const pos = show?.position
  const dur = show?.duration
  const pct = pos != null && dur != null && dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between gap-3">
        <Link to="/setup" className="inline-flex items-center gap-2 text-sm text-mute" title="Preflight">
          <span className={`inline-block size-4 rounded-full ${preflight ? dotColor[preflight.worst] : 'bg-mute'}`} />
          Preflight
        </Link>
        <div className="flex items-center gap-3">
          <Link to="/" className="text-sm text-mute underline">Dashboard</Link>
          <button
            type="button"
            className={`btn h-11 px-4 ${locked ? 'border-pumpkin bg-pumpkin/15 text-pumpkin' : ''}`}
            disabled={lockBusy || settings == null}
            onClick={toggleLock}
          >
            {locked ? 'Unlock' : 'Lock'}
          </button>
        </div>
      </div>
      <div className="min-w-0">
        <div className="truncate text-2xl font-semibold">{p ? p.name : 'No profile active'}</div>
        <div className="text-xs uppercase tracking-wide text-mute">{show?.phase ?? '–'}</div>
      </div>
      {dur != null && (
        <div className="space-y-1">
          <div className="h-3 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-pumpkin transition-[width] duration-200 ease-linear" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex justify-between text-xs tabular-nums text-mute">
            <span>{fmtTime(pos)}</span>
            <span>{fmtTime(dur)}</span>
          </div>
        </div>
      )}
      {show && show.cooldown_remaining > 0 && (
        <div className="text-sm text-pumpkin">Cooldown {show.cooldown_remaining.toFixed(1)} s</div>
      )}
      <ErrorText error={lockErr} />
    </div>
  )
}

function Controls() {
  const show = useLive().show
  const { busy, error, run } = useAction()
  const [msg, setMsg] = useState<string | null>(null)
  const msgTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(msgTimer.current), [])

  const playing = show?.phase === 'playing' || show?.phase === 'paused'
  const settle = useSettle(playing, 3000)
  const isVideo = show?.profile?.type === 'video'

  const trigger = async () => {
    const r = await run(() => api.show.trigger())
    if (r && !r.ok) {
      setMsg(r.message)
      window.clearTimeout(msgTimer.current)
      msgTimer.current = window.setTimeout(() => setMsg(null), 3000)
    }
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <button type="button" className="btn btn-primary h-28 text-2xl font-bold" disabled={busy} onClick={trigger}>
          TRIGGER
        </button>
        <button
          type="button"
          className="btn h-28 text-2xl"
          disabled={busy || settle.pending || !isVideo}
          onClick={() => { settle.arm(); void run(() => (playing ? api.show.stop() : api.show.play(0))) }}
        >
          {playing ? 'Stop' : 'Play'}
        </button>
        <button type="button" className="btn h-28 text-2xl" disabled={busy} onClick={() => run(() => api.show.next())}>
          Next
        </button>
        <BlackoutButton big />
      </div>
      {msg && <p className="text-sm text-pumpkin">{msg}</p>}
      <ErrorText error={error} />
    </div>
  )
}

/** A forgotten setup action refuses every trigger; make it loud and clearable from here. */
function SetupBanner() {
  const show = useLive().show
  const { busy, error, run } = useAction()
  if (!show || !(show.setup_busy || show.calibrating || show.test_pattern)) return null
  const what = [
    show.calibrating ? 'Sync calibration' : show.setup_busy ? 'Setup test' : null,
    show.test_pattern ? 'Test pattern' : null,
  ].filter(Boolean).join(' + ')
  // Test sound / test-all have no stop endpoint; they end within seconds.
  const stoppable = show.calibrating || show.test_pattern
  const stop = async () => {
    if (show.calibrating) await api.setup.calibration(false)
    if (show.test_pattern) await api.show.testPattern(false)
  }
  return (
    <div className="card space-y-2 border-blood bg-blood/15">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-blood">{what} running</div>
          <div className="text-sm text-mute">Triggers are refused until it stops.</div>
        </div>
        {stoppable && (
          <button type="button" className="btn h-11 shrink-0 px-4" disabled={busy} onClick={() => void run(stop)}>
            Stop
          </button>
        )}
      </div>
      <ErrorText error={error} />
    </div>
  )
}

function LevelSlider({ label, field, scale, max, unit }: {
  label: string
  field: 'master_brightness' | 'master_volume'
  scale: number
  max: number
  unit: string
}) {
  const live = useLive().settings?.[field]
  const [val, setVal] = useState(() => Math.round((live ?? 1) * scale))
  const dragging = useRef(false)
  const lastSent = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<number | null>(null)
  const { error, run } = useAction()
  const liveRef = useRef(live)
  useEffect(() => { liveRef.current = live }, [live])

  useEffect(() => {
    if (!dragging.current && live != null) setVal(Math.round(live * scale))
  }, [live, scale])

  useEffect(() => () => {
    window.clearTimeout(timer.current)
    if (pending.current != null) void api.settings.update({ [field]: pending.current / scale } as Partial<Settings>).catch(() => {})
  }, [field, scale])

  const flush = () => {
    window.clearTimeout(timer.current)
    timer.current = undefined
    const v = pending.current
    if (v == null) return
    pending.current = null
    lastSent.current = Date.now()
    void run(() => api.settings.update({ [field]: v / scale } as Partial<Settings>)).then((r) => {
      // A refused update must not leave the slider showing a level the hub never applied.
      if (r === undefined && !dragging.current && liveRef.current != null) setVal(Math.round(liveRef.current * scale))
    })
  }

  const onInput = (v: number) => {
    dragging.current = true
    setVal(v)
    pending.current = v
    const wait = 150 - (Date.now() - lastSent.current)
    if (wait <= 0) flush()
    else if (timer.current === undefined) timer.current = window.setTimeout(flush, wait)
  }

  const end = () => {
    flush()
    dragging.current = false
  }

  const top = Math.max(1, Math.round(max * scale))

  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <span className="label mb-0">{label}</span>
        <span className="text-lg font-semibold tabular-nums">{Math.min(val, top)} {unit}</span>
      </div>
      <input
        type="range"
        min={0}
        max={top}
        step={1}
        value={Math.min(val, top)}
        disabled={live == null}
        onChange={(e) => onInput(Number(e.target.value))}
        onPointerUp={end}
        onPointerCancel={end}
        onTouchEnd={end}
        onKeyUp={end}
        onBlur={end}
        className="h-10 w-full accent-pumpkin"
      />
      <ErrorText error={error} />
    </div>
  )
}

function Soundboard() {
  const mediaRev = useLive().mediaRev
  const [sounds, setSounds] = useState<Media[]>([])
  const [open, setOpen] = useState(true)
  const [volume, setVolume] = useState(1)
  const [loadFailed, setLoadFailed] = useState(false)
  const [reload, setReload] = useState(0)
  const { busy, error, run } = useAction()
  const stop = useAction()

  useEffect(() => {
    api.media.list().then(
      (ms) => {
        setSounds(ms.filter((m) => m.kind === 'audio'))
        setLoadFailed(false)
      },
      () => setLoadFailed(true),
    )
  }, [mediaRev, reload])

  // A failed load must stay visible; vanishing silently looks like "no sounds" mid-show.
  const retry = (
    <div className="flex items-center gap-3">
      <ErrorText error="Couldn't load sounds" />
      <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>Retry</button>
    </div>
  )
  if (loadFailed && sounds.length === 0) {
    return (
      <div className="card space-y-2">
        <h2 className="text-lg font-semibold">Soundboard</h2>
        {retry}
      </div>
    )
  }
  if (sounds.length === 0) return null
  return (
    <div className="space-y-2">
      <button
        type="button"
        className="flex w-full items-center justify-between text-lg font-semibold"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span>Soundboard</span>
        <span className="text-sm text-mute">{open ? 'Hide' : 'Show'}</span>
      </button>
      {loadFailed && retry}
      {open && (
        <div className="card space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {sounds.map((m) => (
              <button
                key={m.id}
                type="button"
                className="btn h-20 break-words text-base"
                disabled={busy}
                onClick={() => void run(() => api.sounds.play(m.id, volume))}
              >
                {m.filename.replace(/\.[^.]+$/, '')}
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between">
            <span className="label mb-0">Sound volume</span>
            <span className="text-lg font-semibold tabular-nums">{Math.round(volume * 100)} %</span>
          </div>
          <input
            type="range"
            min={0}
            max={1.3}
            step={0.05}
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="h-10 w-full accent-pumpkin"
          />
          <button type="button" className="btn h-14 w-full text-base" disabled={stop.busy} onClick={() => void stop.run(() => api.sounds.stop())}>
            Stop sounds
          </button>
          <ErrorText error={error} />
          <ErrorText error={stop.error} />
        </div>
      )}
    </div>
  )
}

const OFF: LightState = { on: false }
const WHITE: LightState = { on: true, bri: 255, colors: [[255, 255, 255]], fx: 0 }

// Own action per board so one slow or failing board neither blocks nor mislabels the others.
function BoardRow({ board: b }: { board: ReturnType<typeof useAdoptedBoards>[number] }) {
  const { busy, error, run } = useAction()
  return (
    <div className="card space-y-2">
      <div className="flex items-center gap-2">
        <StatusDot status={b.status} />
        <span className="truncate font-semibold">{b.name}</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {([['Auto', null], ['Off', OFF], ['White', WHITE]] as const).map(([name, st]) => (
          <button
            key={name}
            type="button"
            className="btn h-14 text-base"
            disabled={busy}
            onClick={() => void run(() => (st ? api.boards.setState(b.mac, st) : api.boards.clearManual(b.mac)))}
          >
            {name}
          </button>
        ))}
      </div>
      <ErrorText error={error} />
    </div>
  )
}

function GroupHeader({ group: g }: { group: BoardGroup }) {
  const { busy, error, run } = useAction()
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-block size-4 shrink-0 rounded-full" style={{ backgroundColor: g.color }} />
        <span className="min-w-0 flex-1 truncate font-semibold">{g.name}</span>
        <div className="flex flex-wrap gap-2">
          {([['Auto', null], ['Off', OFF], ['White', WHITE]] as const).map(([name, st]) => (
            <button
              key={name}
              type="button"
              className="btn h-11 px-4"
              disabled={busy}
              onClick={() => void run(() => (st ? api.groups.setState(g.id, st) : api.groups.clearState(g.id)))}
            >
              {name}
            </button>
          ))}
        </div>
      </div>
      <ErrorText error={error} />
    </div>
  )
}

function BoardPanel() {
  const boards = useAdoptedBoards()
  const groups = useGroups()
  if (boards.length === 0) return null
  if (groups.length === 0) {
    return (
      <div className="space-y-2">
        <h2 className="text-lg font-semibold">Boards</h2>
        {boards.map((b) => (
          <BoardRow key={b.mac} board={b} />
        ))}
      </div>
    )
  }
  const grouped = new Set(groups.flatMap((g) => g.macs))
  const other = boards.filter((b) => !grouped.has(b.mac))
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Boards</h2>
      {groups.map((g) => {
        const members = boards.filter((b) => g.macs.includes(b.mac))
        // A room with no adopted member has nothing to switch; hiding it avoids dead buttons mid-show.
        if (members.length === 0) return null
        return (
          <div key={g.id} className="space-y-2">
            <GroupHeader group={g} />
            {members.map((b) => (
              <BoardRow key={b.mac} board={b} />
            ))}
          </div>
        )
      })}
      {other.length > 0 && (
        <div className="space-y-2">
          <div className="font-semibold text-mute">Other</div>
          {other.map((b) => (
            <BoardRow key={b.mac} board={b} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ShowMode() {
  const settings = useLive().settings
  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6rem)] max-w-xl flex-col gap-4">
      <Header />
      <SetupBanner />
      <Link to="/moments" className="btn h-14 w-full text-lg font-semibold">🔔 Moments</Link>
      <Controls />
      <LevelSlider label="Brightness" field="master_brightness" scale={100} max={settings?.brightness_cap ?? 1} unit="%" />
      <LevelSlider label="Volume" field="master_volume" scale={1} max={settings?.volume_cap ?? 100} unit="%" />
      <Soundboard />
      <BoardPanel />
    </div>
  )
}
