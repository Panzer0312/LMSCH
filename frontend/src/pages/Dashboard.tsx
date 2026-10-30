import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAdoptedBoards, useLive } from '../live'
import type { EventRow, EventStats, Phase, Profile, ShowEvent } from '../types'
import { BlackoutButton } from '../components/BlackoutButton'
import { ErrorText, Section, StatusDot } from '../components/ui'
import { fmtTime, useAction } from '../lib'

const phaseStyle: Record<Phase, string> = {
  none: 'border-line text-mute',
  lights: 'border-ghost/60 text-ghost',
  idle: 'border-slime/60 text-slime',
  playing: 'border-pumpkin/70 text-pumpkin',
  paused: 'border-pumpkin/40 text-pumpkin/70',
  blackout: 'border-white text-white',
}

function ActiveProfile() {
  const show = useLive().show
  const p = show?.profile ?? null
  if (!show || !p) {
    return (
      <div className="card flex items-center justify-between gap-3">
        <span className="text-mute">No profile active</span>
        <Link to="/profiles" className="btn">Profiles</Link>
      </div>
    )
  }
  const pos = show.position
  const dur = show.duration
  const pct = pos != null && dur != null && dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0
  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xl font-semibold">{p.name}</div>
          <div className="text-xs uppercase tracking-wide text-mute">{p.type} · {p.mode.replace('_', ' ')}</div>
        </div>
        <span className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase ${phaseStyle[show.phase]}`}>
          {show.phase}
        </span>
      </div>
      {dur != null && (
        <div className="space-y-1">
          <div className="h-2 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-pumpkin transition-[width] duration-200 ease-linear" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex justify-between text-xs tabular-nums text-mute">
            <span>{fmtTime(pos)}</span>
            <span>{fmtTime(dur)}</span>
          </div>
        </div>
      )}
      {show.cooldown_remaining > 0 && (
        <div className="text-sm text-pumpkin">Cooldown {show.cooldown_remaining.toFixed(1)} s</div>
      )}
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
        <button type="button" className="btn btn-primary h-20 text-lg font-bold" disabled={busy} onClick={trigger}>
          TRIGGER
        </button>
        <button
          type="button"
          className="btn h-20 text-lg"
          disabled={busy || !isVideo}
          onClick={() => run(() => (playing ? api.show.stop() : api.show.play(0)))}
        >
          {playing ? 'Stop' : 'Play'}
        </button>
        <button type="button" className="btn h-20 text-lg" disabled={busy} onClick={() => run(() => api.show.next())}>
          Next profile
        </button>
        <BlackoutButton big />
      </div>
      {msg && <p className="text-sm text-pumpkin">{msg}</p>}
      <ErrorText error={error} />
    </div>
  )
}

function SceneCard() {
  const { scene, profilesRev } = useLive()
  const [profiles, setProfiles] = useState<Profile[]>([])
  const load = useAction()
  const act = useAction()
  const runLoad = load.run
  const active = scene?.profile_id != null

  useEffect(() => {
    if (!active) return
    let cancelled = false
    void runLoad(() => api.profiles.list()).then((ps) => {
      if (!cancelled && ps) setProfiles(ps)
    })
    return () => { cancelled = true }
  }, [active, profilesRev, runLoad])

  if (!scene || scene.profile_id == null) return null
  const byId = new Map(profiles.map((p) => [p.id, p]))
  const name = (id: number | null) => (id == null ? '–' : byId.get(id)?.name ?? `#${id}`)
  const sceneProfile = byId.get(scene.profile_id)
  const itemIds = [...new Set((sceneProfile?.scene?.items ?? []).map((i) => i.profile_id))]
  const history = scene.history.slice(-5).reverse()

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wide text-mute">Scene · {name(scene.profile_id)}</div>
          <div className="truncate text-lg font-semibold">
            Next: {name(scene.next_id)}
            {scene.pinned != null && <span className="ml-2 text-xs uppercase text-pumpkin">pinned</span>}
          </div>
        </div>
        <button type="button" className="btn shrink-0" disabled={act.busy} onClick={() => void act.run(() => api.scene.skip())}>
          Skip
        </button>
      </div>
      <label className="block">
        <span className="label">Pin next</span>
        <select
          className="input w-full"
          disabled={act.busy}
          value={scene.pinned ?? ''}
          onChange={(e) => {
            const v = e.target.value
            void act.run(() => api.scene.pin(v === '' ? null : Number(v)))
          }}
        >
          <option value="">No pin</option>
          {itemIds.map((id) => <option key={id} value={id}>{name(id)}</option>)}
        </select>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="min-w-0">
          <div className="label">Upcoming</div>
          {scene.upcoming.length === 0 ? (
            <p className="text-sm text-mute">–</p>
          ) : (
            <ol className="list-decimal pl-5 text-sm">
              {scene.upcoming.map((id, i) => <li key={`${id}-${i}`} className="truncate">{name(id)}</li>)}
            </ol>
          )}
        </div>
        <div className="min-w-0">
          <div className="label">Recently played</div>
          {history.length === 0 ? (
            <p className="text-sm text-mute">–</p>
          ) : (
            <ul className="text-sm text-mute">
              {history.map((id, i) => <li key={`${id}-${i}`} className="truncate">{name(id)}</li>)}
            </ul>
          )}
        </div>
      </div>
      <ErrorText error={act.error ?? load.error} />
    </div>
  )
}

function LevelSlider({ label, field, scale, max }: {
  label: string
  field: 'master_brightness' | 'master_volume'
  scale: number
  max: number
}) {
  const live = useLive().settings?.[field]
  const [val, setVal] = useState(() => Math.round((live ?? 1) * scale))
  const dragging = useRef(false)
  const lastSent = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<number | null>(null)
  const { error, run } = useAction()

  useEffect(() => {
    if (!dragging.current && live != null) setVal(Math.round(live * scale))
  }, [live, scale])

  useEffect(() => () => {
    window.clearTimeout(timer.current)
    if (pending.current != null) void api.settings.update({ [field]: pending.current / scale }).catch(() => {})
  }, [field, scale])

  const flush = () => {
    window.clearTimeout(timer.current)
    timer.current = undefined
    const v = pending.current
    if (v == null) return
    pending.current = null
    lastSent.current = Date.now()
    void run(() => api.settings.update({ [field]: v / scale }))
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

  const top = Math.max(1, Math.round(max))
  const shown = Math.min(val, top)

  return (
    <div className="card space-y-2">
      <div className="flex items-center justify-between">
        <span className="label mb-0">{label}</span>
        <span className="text-sm font-semibold tabular-nums">{shown} %</span>
      </div>
      <input
        type="range"
        min={0}
        max={top}
        step={1}
        value={shown}
        disabled={live == null}
        onChange={(e) => onInput(Number(e.target.value))}
        onPointerUp={end}
        onPointerCancel={end}
        onTouchEnd={end}
        onKeyUp={end}
        onBlur={end}
        className="h-8 w-full accent-pumpkin"
      />
      <ErrorText error={error} />
    </div>
  )
}

function ProfileSwitcher() {
  const { profilesRev, show } = useLive()
  const activeId = show?.profile?.id ?? null
  const [profiles, setProfiles] = useState<Profile[]>([])
  const load = useAction()
  const act = useAction()
  const runLoad = load.run

  useEffect(() => {
    let cancelled = false
    void runLoad(() => api.profiles.list()).then((ps) => {
      if (!cancelled && ps) setProfiles(ps)
    })
    return () => { cancelled = true }
  }, [profilesRev, runLoad])

  return (
    <Section title="Profiles" actions={<Link to="/profiles" className="btn">Manage</Link>}>
      {profiles.length === 0 && !load.busy ? (
        <p className="text-sm text-mute">No profiles yet.</p>
      ) : (
        <div className="-mx-4 flex snap-x gap-2 overflow-x-auto px-4 pb-1">
          {profiles.map((p) => {
            const active = p.id === activeId
            return (
              <button
                key={p.id}
                type="button"
                disabled={act.busy}
                onClick={() => void act.run(() => api.profiles.activate(p.id))}
                className={`btn h-14 shrink-0 snap-start flex-col gap-0 px-4 ${active ? 'border-pumpkin bg-pumpkin/15 text-pumpkin' : ''}`}
              >
                <span className="max-w-40 truncate">{p.name}</span>
                <span className="text-[10px] uppercase tracking-wide text-mute">{p.type}</span>
              </button>
            )
          })}
        </div>
      )}
      <ErrorText error={load.error ?? act.error} />
    </Section>
  )
}

function BoardStrip() {
  const boards = useAdoptedBoards()
  return (
    <Section title="Boards" actions={<Link to="/boards" className="btn">All boards</Link>}>
      {boards.length === 0 ? (
        <p className="text-sm text-mute">No adopted boards.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {boards.map((b) => (
            <Link
              key={b.mac}
              to="/boards"
              className="inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3 py-1 text-sm"
            >
              <StatusDot status={b.status} />
              <span className="max-w-32 truncate">{b.name}</span>
              <span className="text-xs tabular-nums text-mute">{b.latency_ms != null ? `${Math.round(b.latency_ms)} ms` : '–'}</span>
            </Link>
          ))}
        </div>
      )}
    </Section>
  )
}

const levelColor: Record<ShowEvent['level'], string> = { info: 'text-ink', warn: 'text-pumpkin', error: 'text-blood' }

function hms(ts: number) {
  const d = new Date(ts * 1000)
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
}

function EventLog() {
  const events = useLive().events.slice(-20).reverse()
  return (
    <Section title="Events">
      <div className="card p-0">
        {events.length === 0 ? (
          <p className="p-4 text-sm text-mute">Nothing yet.</p>
        ) : (
          <ul className="divide-y divide-line font-mono text-xs">
            {events.map((e, i) => (
              <li key={`${e.ts}-${i}`} className="flex gap-3 px-3 py-2">
                <span className="shrink-0 tabular-nums text-mute">{hms(e.ts)}</span>
                <span className={`min-w-0 break-words ${levelColor[e.level]}`}>{e.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}

function PreflightBanner() {
  const pf = useLive().preflight
  if (pf?.worst !== 'fail') return null
  const failing = pf.checks.filter((c) => c.status === 'fail')
  return (
    <div className="card flex items-start justify-between gap-3 border-blood text-blood">
      <div className="min-w-0">
        <div className="font-semibold">Pre-show check failed</div>
        <ul className="list-disc pl-5 text-sm">
          {failing.map((c) => <li key={c.id} className="break-words">{c.label}</li>)}
        </ul>
      </div>
      <Link to="/setup" className="btn shrink-0">Setup</Link>
    </div>
  )
}

function tonightStart() {
  const d = new Date()
  if (d.getHours() < 12) d.setDate(d.getDate() - 1)
  d.setHours(12, 0, 0, 0)
  return d.getTime() / 1000
}

function History() {
  const events = useLive().events
  const last = events[events.length - 1]
  const key = last ? `${last.ts}|${last.message}` : ''
  const [rows, setRows] = useState<EventRow[]>([])
  const [stats, setStats] = useState<EventStats | null>(null)
  const listAct = useAction()
  const statsAct = useAction()
  const runList = listAct.run
  const runStats = statsAct.run
  const first = useRef(true)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const [r, s] = await Promise.all([
        runList(() => api.events.list({ limit: 100 })),
        runStats(() => api.events.stats(tonightStart())),
      ])
      if (cancelled) return
      if (r) setRows(r)
      if (s) setStats(s)
    }
    const t = window.setTimeout(() => void load(), first.current ? 0 : 2000)
    first.current = false
    return () => {
      cancelled = true
      window.clearTimeout(t)
    }
  }, [key, runList, runStats])

  const bars = stats?.by_hour ?? []
  const peak = Math.max(1, ...bars.map((b) => b.count))
  return (
    <Section title="History">
      <div className="card space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="label mb-0">Triggers tonight</span>
          <span className="text-2xl font-bold tabular-nums text-pumpkin">{stats?.triggers ?? '–'}</span>
        </div>
        {bars.length > 0 && (
          <div className="space-y-1">
            {bars.map((b) => (
              <div key={b.hour_ts} className="flex items-center gap-2 text-xs tabular-nums">
                <span className="w-12 shrink-0 text-mute">{hms(b.hour_ts).slice(0, 5)}</span>
                <div className="h-3 min-w-0 flex-1 overflow-hidden rounded bg-line">
                  <div className="h-full rounded bg-pumpkin" style={{ width: `${(b.count / peak) * 100}%` }} />
                </div>
                <span className="w-8 shrink-0 text-right">{b.count}</span>
              </div>
            ))}
          </div>
        )}
        <ErrorText error={statsAct.error ?? listAct.error} />
      </div>
      <div className="card p-0">
        {rows.length === 0 ? (
          <p className="p-4 text-sm text-mute">No history.</p>
        ) : (
          <ul className="max-h-96 divide-y divide-line overflow-y-auto font-mono text-xs">
            {rows.map((e) => (
              <li key={e.id} className="flex gap-3 px-3 py-2">
                <span className="shrink-0 tabular-nums text-mute">{hms(e.ts)}</span>
                <span className={`min-w-0 break-words ${levelColor[e.level]}`}>{e.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}

export default function Dashboard() {
  const settings = useLive().settings
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PreflightBanner />
      <ActiveProfile />
      <SceneCard />
      <Controls />
      <Link to="/show" className="btn w-full">Show mode</Link>
      <div className="grid gap-3 sm:grid-cols-2">
        <LevelSlider label="Master brightness" field="master_brightness" scale={100}
          max={(settings?.brightness_cap ?? 1) * 100} />
        <LevelSlider label="Master volume" field="master_volume" scale={1}
          max={settings?.volume_cap ?? 100} />
      </div>
      <ProfileSwitcher />
      <BoardStrip />
      <EventLog />
      <History />
    </div>
  )
}
