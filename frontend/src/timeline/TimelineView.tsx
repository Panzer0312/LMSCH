import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { BoardStatus } from '../types'
import { MediaStrip } from './Strip'
import { ALL_LANE, clamp, cueColor, lanesOf, round3, snap, snapCandidates, stateLabel, setDragging } from './model'
import type { TLCue, TimelineViewProps } from './model'

const RULER_H = 24
const STRIP_H = 88
const LANE_H = 44
const SNAP_PX = 8
const MIN_DUR = 0.05
const MIN_BLOCK_PX = 8
const STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 3600]

const DOT: Record<BoardStatus, string> = {
  online: 'bg-slime',
  degraded: 'bg-pumpkin',
  offline: 'bg-blood',
  unknown: 'bg-mute',
}

function fmt(t: number, step: number): string {
  const r = Math.round(t * 10) / 10
  const m = Math.floor(r / 60)
  const s = r - m * 60
  const ss = step < 1 ? s.toFixed(1).padStart(4, '0') : String(Math.round(s)).padStart(2, '0')
  return `${m}:${ss}`
}

type DragKind = 'move' | 'left' | 'right'

interface Drag {
  kind: DragKind
  pointerId: number
  x0: number
  pps: number
  anchor: string
  base: TLCue[]
  orig: Map<string, { start: number; dur: number | null }>
  rightBase: number
  cands: number[]
  snap: boolean
  latest: TLCue[] | null
}

interface LaneItem { cue: TLCue; end: number; open: boolean }

/** Smallest adjustment that snaps any of `values` onto a candidate, else 0. */
function snapAdjust(values: number[], cands: number[], thr: number): number {
  let best = 0
  let dist = Infinity
  for (const v of values) {
    const s = snap(v, cands, thr)
    if (s !== v && Math.abs(s - v) < dist) { dist = Math.abs(s - v); best = s - v }
  }
  return best
}

export function TimelineView(props: TimelineViewProps) {
  const {
    duration, cues, lanes, selected, playhead, pxPerSec, mediaId, overlaps, readOnly = false,
    snapEnabled, onSeek, onSelect, onChange, onZoom, onAddAt,
  } = props
  const editable = !readOnly

  const scrollRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const seekPointer = useRef<number | null>(null)
  const zoomAnchor = useRef<{ t: number; cx: number; pps: number } | null>(null)
  const lastPlayhead = useRef(playhead)

  const contentW = Math.max(duration, 1) * pxPerSec + 40
  const lanesH = lanes.length * LANE_H

  const laneItems = useMemo(() => {
    const m = new Map<string, LaneItem[]>()
    const lists = new Map<string, TLCue[]>()
    for (const l of lanes) lists.set(l.id, [])
    for (const c of cues) for (const id of lanesOf(c)) lists.get(id)?.push(c)
    for (const [id, list] of lists) {
      list.sort((a, b) => a.start_s - b.start_s)
      m.set(id, list.map((cue, i) => {
        if (cue.duration_s != null) return { cue, end: cue.start_s + cue.duration_s, open: false }
        let end = Math.max(duration, cue.start_s)
        for (let j = i + 1; j < list.length; j++) {
          if (list[j].start_s > cue.start_s) { end = list[j].start_s; break }
        }
        return { cue, end, open: true }
      }))
    }
    return m
  }, [cues, lanes, duration])

  const ticks = useMemo(() => {
    const step = STEPS.find((s) => s * pxPerSec >= 60) ?? STEPS[STEPS.length - 1]
    const minor = step / 5
    const n = Math.floor(Math.max(duration, 1) / minor + 1e-6)
    const out: { t: number; major: boolean }[] = []
    for (let i = 0; i <= n; i++) out.push({ t: i * minor, major: i % 5 === 0 })
    return { step, list: out }
  }, [duration, pxPerSec])

  // Keep the moving playhead in view (only reacts to playhead changes, not zoom).
  useEffect(() => {
    if (lastPlayhead.current === playhead) return
    lastPlayhead.current = playhead
    const el = scrollRef.current
    if (!el) return
    const x = playhead * pxPerSec
    const margin = el.clientWidth * 0.15
    if (x > el.scrollLeft + el.clientWidth - 16) el.scrollLeft = x - margin
    else if (x < el.scrollLeft) el.scrollLeft = Math.max(0, x - margin)
  }, [playhead, pxPerSec])

  // Ctrl/Meta + wheel zoom (native listener: React wheel handlers are passive).
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const pending = zoomAnchor.current
      const base = pending?.pps ?? pxPerSec
      const cx = pending ? pending.cx : e.clientX - el.getBoundingClientRect().left
      const t = pending ? pending.t : (el.scrollLeft + cx) / pxPerSec
      const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY
      const next = clamp(base * Math.exp(-dy * 0.0015), 5, 400)
      if (next === base) return
      zoomAnchor.current = { t, cx, pps: next }
      onZoom(next)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [pxPerSec, onZoom])

  useLayoutEffect(() => {
    const a = zoomAnchor.current
    const el = scrollRef.current
    if (!a || !el) return
    zoomAnchor.current = null
    el.scrollLeft = Math.max(0, a.t * pxPerSec - a.cx)
  }, [pxPerSec])

  const timeAt = (clientX: number, target: Element) =>
    clamp((clientX - target.getBoundingClientRect().left) / pxPerSec, 0, duration)

  // ---- seeking on ruler / strip ----
  const onSeekDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    seekPointer.current = e.pointerId
    onSeek(timeAt(e.clientX, e.currentTarget))
  }
  const onSeekMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (seekPointer.current !== e.pointerId) return
    onSeek(timeAt(e.clientX, e.currentTarget))
  }
  const onSeekUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (seekPointer.current === e.pointerId) seekPointer.current = null
  }

  // ---- lanes ----
  const onLaneClick = (e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return
    onSeek(timeAt(e.clientX, e.currentTarget))
    onSelect(new Set())
  }
  const onLaneDouble = (e: ReactMouseEvent<HTMLDivElement>, laneId: string) => {
    if (e.target !== e.currentTarget) return
    onAddAt(round3(timeAt(e.clientX, e.currentTarget)), laneId)
  }

  // ---- blocks ----
  const onBlockDown = (e: ReactPointerEvent<HTMLDivElement>, cue: TLCue, kind: DragKind, visualEnd: number) => {
    if (e.button !== 0) return
    e.stopPropagation()
    const toggle = e.shiftKey || e.ctrlKey || e.metaKey
    let next: Set<string>
    if (kind === 'move' && toggle) {
      next = new Set(selected)
      if (next.has(cue.key)) next.delete(cue.key)
      else next.add(cue.key)
    } else if (!selected.has(cue.key)) {
      next = new Set([cue.key])
    } else {
      next = selected
    }
    if (next !== selected) onSelect(next)
    if (!editable || !next.has(cue.key)) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const keys = kind === 'move' ? next : new Set([cue.key])
    const orig = new Map<string, { start: number; dur: number | null }>()
    for (const c of cues) if (keys.has(c.key)) orig.set(c.key, { start: c.start_s, dur: c.duration_s })
    // Lets the page suppress edit shortcuts mid-drag (the drag commits a snapshot on release).
    setDragging(true)
    drag.current = {
      kind,
      pointerId: e.pointerId,
      x0: e.clientX,
      pps: pxPerSec,
      anchor: cue.key,
      base: cues,
      orig,
      rightBase: cue.duration_s ?? Math.max(MIN_DUR, visualEnd - cue.start_s),
      cands: snapCandidates(cues, keys, playhead),
      snap: snapEnabled,
      latest: null,
    }
  }

  const onBlockMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointerId !== e.pointerId) return
    const o = d.orig.get(d.anchor)
    if (!o) return
    const dt = (e.clientX - d.x0) / d.pps
    const thr = SNAP_PX / d.pps
    const useSnap = d.snap && !e.altKey
    let next: TLCue[]
    if (d.kind === 'move') {
      let ns = o.start + dt
      if (useSnap) ns += snapAdjust(o.dur != null ? [ns, ns + o.dur] : [ns], d.cands, thr)
      const delta = ns - o.start
      next = d.base.map((c) => {
        const oo = d.orig.get(c.key)
        return oo ? { ...c, start_s: round3(clamp(oo.start + delta, 0, duration)) } : c
      })
    } else if (d.kind === 'left') {
      const end = o.start + (o.dur ?? 0)
      let ns = o.start + dt
      if (useSnap) ns = snap(ns, d.cands, thr)
      ns = clamp(ns, 0, Math.max(0, end - MIN_DUR))
      next = d.base.map((c) => (c.key === d.anchor
        ? { ...c, start_s: round3(ns), duration_s: round3(Math.max(MIN_DUR, end - ns)) }
        : c))
    } else {
      let ne = o.start + d.rightBase + dt
      if (useSnap) ne = snap(ne, d.cands, thr)
      ne = clamp(ne, o.start + MIN_DUR, Math.max(duration, o.start + MIN_DUR))
      next = d.base.map((c) => (c.key === d.anchor ? { ...c, duration_s: round3(ne - o.start) } : c))
    }
    const moved = next.some((c, i) => {
      const b = d.base[i]
      return c.start_s !== b.start_s || c.duration_s !== b.duration_s
    })
    d.latest = moved ? next : null
    onChange(next, false)
  }

  const onBlockUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.pointerId !== e.pointerId) return
    drag.current = null
    setDragging(false)
    if (d.latest) onChange(d.latest, true)
  }

  return (
    <div className="flex w-full select-none overflow-hidden rounded-xl border border-line bg-bg text-ink">
      {/* label column */}
      <div className="w-32 shrink-0 border-r border-line bg-panel">
        <div className="border-b border-line" style={{ height: RULER_H }} />
        <div className="flex items-center border-b border-line px-2 text-xs text-mute" style={{ height: STRIP_H }}>
          Media
        </div>
        {lanes.map((l, i) => (
          <div
            key={l.id}
            className={`flex items-center gap-2 border-b border-line px-2 text-xs ${
              l.id === ALL_LANE ? 'bg-ghost/10 font-semibold' : i % 2 ? 'bg-panel-2' : 'bg-panel'
            }`}
            style={{ height: LANE_H }}
            title={l.label}
          >
            {l.id !== ALL_LANE && <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[l.status ?? 'unknown']}`} />}
            <span className="truncate">{l.label}</span>
          </div>
        ))}
      </div>

      {/* scroll area */}
      <div ref={scrollRef} className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden">
        <div className="relative" style={{ width: contentW, height: RULER_H + STRIP_H + lanesH }}>
          <div
            className="cursor-pointer touch-none"
            onPointerDown={onSeekDown}
            onPointerMove={onSeekMove}
            onPointerUp={onSeekUp}
            onPointerCancel={onSeekUp}
          >
            {/* ruler */}
            <div className="relative border-b border-line bg-panel" style={{ height: RULER_H }}>
              {ticks.list.map(({ t, major }) => (
                <div
                  key={t}
                  className={`pointer-events-none absolute bottom-0 w-px ${major ? 'h-3 bg-mute' : 'h-1.5 bg-line'}`}
                  style={{ left: t * pxPerSec }}
                >
                  {major && (
                    <span className="absolute -top-2.5 left-1 whitespace-nowrap text-[10px] leading-none text-mute">
                      {fmt(t, ticks.step)}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div style={{ height: STRIP_H }}>
              <MediaStrip mediaId={mediaId} duration={duration} pxPerSec={pxPerSec} />
            </div>
          </div>

          {/* lanes */}
          {lanes.map((l, i) => (
            <div
              key={l.id}
              className={`relative border-b border-line ${
                l.id === ALL_LANE ? 'bg-ghost/10' : i % 2 ? 'bg-panel-2' : 'bg-panel'
              }`}
              style={{ height: LANE_H }}
              onClick={onLaneClick}
              onDoubleClick={(e) => onLaneDouble(e, l.id)}
            >
              {(laneItems.get(l.id) ?? []).map(({ cue, end, open }) => {
                const col = cueColor(cue)
                const isSel = selected.has(cue.key)
                const isOverlap = overlaps.has(cue.key)
                const width = Math.max(MIN_BLOCK_PX, (end - cue.start_s) * pxPerSec)
                const ring = isSel ? 'ring-2 ring-pumpkin z-10' : isOverlap ? 'ring-2 ring-blood/60' : ''
                return (
                  <div
                    key={`${cue.key}:${l.id}`}
                    className={`absolute flex items-center overflow-hidden rounded-sm text-[11px] leading-tight ${ring} ${
                      open ? 'border-r-2 border-dashed border-r-ink/50 opacity-60' : ''
                    } ${editable ? 'cursor-grab touch-none active:cursor-grabbing' : 'cursor-pointer'}`}
                    style={{
                      left: cue.start_s * pxPerSec,
                      width,
                      top: 4,
                      height: LANE_H - 8,
                      backgroundColor: `color-mix(in srgb, ${col} 35%, transparent)`,
                      backgroundImage: open
                        ? 'repeating-linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,0.07) 6px 12px)'
                        : undefined,
                      borderLeft: `3px solid ${col}`,
                    }}
                    title={stateLabel(cue, [])}
                    onPointerDown={(e) => onBlockDown(e, cue, 'move', end)}
                    onPointerMove={onBlockMove}
                    onPointerUp={onBlockUp}
                    onPointerCancel={onBlockUp}
                  >
                    <span className="pointer-events-none truncate px-1.5">{stateLabel(cue, [])}</span>
                    {isOverlap && (
                      <span className="pointer-events-none absolute right-0.5 top-0 text-[10px] text-blood">⚠</span>
                    )}
                    {editable && !open && (
                      <div
                        className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize touch-none hover:bg-ink/30"
                        onPointerDown={(e) => onBlockDown(e, cue, 'left', end)}
                      />
                    )}
                    {editable && (
                      <div
                        className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize touch-none hover:bg-ink/30"
                        onPointerDown={(e) => onBlockDown(e, cue, 'right', end)}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          ))}

          {/* playhead */}
          <div
            className="pointer-events-none absolute inset-y-0 z-20 w-0.5 -translate-x-1/2 bg-pumpkin"
            style={{ left: playhead * pxPerSec }}
          />
        </div>
      </div>
    </div>
  )
}
