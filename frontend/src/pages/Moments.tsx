import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { Moment, MomentState } from '../types'
import { BlackoutButton } from '../components/BlackoutButton'
import { MomentEditor } from '../components/MomentEditor'
import { ErrorText } from '../components/ui'
import { useAction } from '../lib'

/** Pick readable text on arbitrary user colors. */
function inkFor(hex: string) {
  const n = Number.parseInt(hex.replace('#', ''), 16)
  if (Number.isNaN(n)) return '#ffffff'
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? '#000000' : '#ffffff'
}

const R = 18
const CIRC = 2 * Math.PI * R

function Ring({ frac, color, label, spin }: { frac: number; color: string; label?: string; spin?: boolean }) {
  const f = Math.max(0, Math.min(1, frac))
  return (
    <span className="absolute right-2 top-2 inline-flex size-11 items-center justify-center rounded-full bg-black/50">
      <svg viewBox="0 0 44 44" className={`absolute inset-0 size-11 -rotate-90 ${spin ? 'animate-spin' : ''}`}>
        <circle cx={22} cy={22} r={R} fill="none" stroke="rgba(255,255,255,.2)" strokeWidth={4} />
        <circle
          cx={22} cy={22} r={R} fill="none" stroke={color} strokeWidth={4} strokeLinecap="round"
          strokeDasharray={CIRC} strokeDashoffset={CIRC * (1 - f)}
          className="transition-[stroke-dashoffset] duration-200 ease-linear"
        />
      </svg>
      {label && <span className="relative text-xs font-bold tabular-nums text-white">{label}</span>}
    </span>
  )
}

export default function Moments() {
  const { moment, momentsRev, settings } = useLive()
  const locked = settings?.show_lock ?? false
  const [list, setList] = useState<Moment[]>([])
  const [edit, setEdit] = useState(false)
  const [kiosk, setKiosk] = useState(false)
  const [editing, setEditing] = useState<Moment | 'new' | null>(null)
  const [flash, setFlash] = useState<{ id: number; msg: string } | null>(null)
  const flashTimer = useRef<number | undefined>(undefined)
  const wentFs = useRef(false)
  // Seconds since the current `moment` message arrived; ticks belonging to an older message read as 0.
  const [tick, setTick] = useState<{ of: MomentState | null; s: number }>({ of: null, s: 0 })
  const { busy, error, run } = useAction()
  const listAct = useAction()
  const runList = listAct.run

  const showEdit = edit && !locked && !kiosk

  useEffect(() => {
    void runList(() => api.moments.list().then(setList))
  }, [momentsRev, runList])

  // Backend sends `moment` only on state change; cooldowns and progress advance locally from receipt.
  useEffect(() => {
    const t = Date.now()
    const max = Math.max(0, ...Object.values(moment?.cooldown ?? {}))
    const playing = moment?.playing_id != null
    if (!playing && max <= 0) return
    const runLeft = moment?.playing_id != null && moment.duration_s != null ? moment.duration_s - moment.elapsed_s : 0
    const iv = window.setInterval(() => {
      const s = (Date.now() - t) / 1000
      setTick({ of: moment, s })
      if (s >= max && s >= runLeft) window.clearInterval(iv)
    }, playing ? 250 : 1000)
    return () => window.clearInterval(iv)
  }, [moment])

  useEffect(() => () => window.clearTimeout(flashTimer.current), [])

  useEffect(() => {
    const onFs = () => {
      if (!document.fullscreenElement && wentFs.current) {
        wentFs.current = false
        setKiosk(false)
      }
    }
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  const remaining = (id: number) => {
    const cd = moment?.cooldown[String(id)] ?? 0
    return Math.max(0, cd - (tick.of === moment ? tick.s : 0))
  }

  // null = unknown duration, render indeterminate.
  const progress = moment?.duration_s != null && moment.duration_s > 0
    ? Math.min(1, (moment.elapsed_s + (tick.of === moment ? tick.s : 0)) / moment.duration_s)
    : null

  const showFlash = (id: number, msg: string) => {
    setFlash({ id, msg })
    window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(null), 3000)
  }

  const press = async (m: Moment) => {
    const rem = remaining(m.id)
    if (rem > 0) {
      showFlash(m.id, `cooldown ${Math.ceil(rem)} s`)
      return
    }
    try {
      await api.moments.play(m.id)
    } catch (e) {
      showFlash(m.id, e instanceof Error ? e.message : String(e))
    }
  }

  const toggleKiosk = () => {
    if (kiosk) {
      setKiosk(false)
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined)
      wentFs.current = false
      return
    }
    setKiosk(true)
    // iOS Safari has no element fullscreen; the fixed overlay still hides the nav there.
    const el = document.documentElement
    if (el.requestFullscreen) {
      el.requestFullscreen().then(() => { wentFs.current = true }, () => undefined)
    }
  }

  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= list.length) return
    const next = [...list]
    ;[next[i], next[j]] = [next[j], next[i]]
    setList(next)
    void run(() => api.moments.reorder(next.map((m) => m.id)))
  }

  const remove = (m: Moment) => {
    if (!window.confirm(`Delete moment "${m.name}"?`)) return
    void run(() => api.moments.remove(m.id).then(() => setList((l) => l.filter((x) => x.id !== m.id))))
  }

  const onSaved = (m: Moment) => {
    setEditing(null)
    setList((l) => (l.some((x) => x.id === m.id) ? l.map((x) => (x.id === m.id ? m : x)) : [...l, m]))
  }

  return (
    <div className={kiosk ? 'fixed inset-0 z-40 overflow-y-auto bg-bg p-3' : 'space-y-4'}>
      <div className="sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-2 bg-bg/95 px-1 py-2 backdrop-blur">
        <button type="button" className="btn h-14 flex-1 text-lg sm:flex-none sm:px-8" disabled={busy} onClick={() => void run(() => api.moments.stop())}>
          Stop
        </button>
        <BlackoutButton />
        <div className="ml-auto flex gap-2">
          {!locked && !kiosk && (
            <button type="button" className={`btn h-11 ${edit ? 'border-pumpkin text-pumpkin' : ''}`} onClick={() => setEdit((e) => !e)}>
              {edit ? 'Done' : 'Edit'}
            </button>
          )}
          <button type="button" className="btn h-11" onClick={toggleKiosk}>{kiosk ? 'Exit kiosk' : 'Kiosk'}</button>
        </div>
      </div>
      <ErrorText error={error ?? listAct.error} />
      {list.length === 0 && !listAct.busy && (
        <p className="text-sm text-mute">No moments yet.{!locked && ' Tap Edit, then Add moment.'}</p>
      )}
      <div className={`grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6 ${kiosk ? 'mt-3' : ''}`}>
        {list.map((m, i) => {
          const playing = moment?.playing_id === m.id
          const rem = remaining(m.id)
          const ink = inkFor(m.color)
          return (
            <div key={m.id} className="space-y-1">
              <button
                type="button"
                className={`relative flex h-32 w-full flex-col items-center justify-center gap-1 rounded-xl border-2 p-2 text-center transition active:scale-[.97] md:h-36 ${playing ? 'border-white' : 'border-transparent'} ${rem > 0 && !playing ? 'opacity-60' : ''}`}
                style={{ background: m.color, color: ink }}
                onClick={() => void press(m)}
              >
                <span className="text-4xl leading-none">{m.icon}</span>
                <span className="line-clamp-2 break-words text-base font-semibold">{m.name}</span>
                {playing && (progress == null
                  ? <Ring frac={0.25} color="#7ee36b" spin />
                  : <Ring frac={progress} color="#7ee36b" />)}
                {!playing && rem > 0 && <Ring frac={m.cooldown_s > 0 ? rem / m.cooldown_s : 0} color="#ff7a1a" label={String(Math.ceil(rem))} />}
                {flash?.id === m.id && (
                  <span className="absolute inset-x-1 bottom-1 rounded bg-black/75 px-1 py-0.5 text-xs text-white">{flash.msg}</span>
                )}
              </button>
              {showEdit && (
                <div className="grid grid-cols-4 gap-1">
                  <button type="button" className="btn px-0" disabled={busy || i === 0} aria-label="Move up" onClick={() => move(i, -1)}>↑</button>
                  <button type="button" className="btn px-0" disabled={busy || i === list.length - 1} aria-label="Move down" onClick={() => move(i, 1)}>↓</button>
                  <button type="button" className="btn px-0" onClick={() => setEditing(m)}>Edit</button>
                  <button type="button" className="btn px-0 text-blood" disabled={busy} onClick={() => remove(m)}>Del</button>
                </div>
              )}
            </div>
          )
        })}
        {showEdit && (
          <button
            type="button"
            className="flex h-32 w-full items-center justify-center rounded-xl border-2 border-dashed border-line text-mute md:h-36"
            onClick={() => setEditing('new')}
          >
            + Add moment
          </button>
        )}
      </div>
      {editing && showEdit && (
        <MomentEditor moment={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={onSaved} />
      )}
    </div>
  )
}
