import { useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { Board, BoardGroup, BoardStatus, Media, Waveform } from '../types'
import { MarkersLane, MARKERS_H } from './MarkersLane'
import { Minimap } from './Minimap'
import { MediaStrip } from './Strip'
import { timecode } from './timeops'
import { Ribbon } from './Ribbon'
import { lookAt, toSpecs } from './ribbon'
import type { ResolveCtx } from './ribbon'
import { Timeline } from './resolve'
import { ALL_LANE, clamp, cueColor, groupLaneId, isMacMuted, isSound, lanesOf, parseLaneId, round3, snap, snapCandidates, soundEnd, stateLabel, setDragging } from './model'
import type { Lane, TLCue, TimelineViewProps } from './model'

const RULER_H = 24
const STRIP_H = 88
const LANE_H = 44
const RIBBON_H = 8
// Board lanes carry a resolved-color ribbon below the cue blocks.
const laneHeight = (l: { kind: string }) => LANE_H + (l.kind === 'board' ? RIBBON_H : 0)
const NO_OVERRIDES = {}
const NO_BOARDS: never[] = []
const NO_LIST: never[] = []
const NO_SET = new Set<string>()
const NO_GROUPS: BoardGroup[] = []
const NO_AUDIO = new Map<number, Media>()
const NO_WAVES = new Map<number, Waveform>()
const noop = () => {}
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

function LanePreview({ ctx, board, t }: { ctx: ResolveCtx; board: Board; t: number }) {
  const l = lookAt(ctx, board, t)
  return (
    <div className="flex items-center gap-1 text-[10px] text-mute">
      <span className="h-2.5 w-2.5 shrink-0 rounded-sm border border-line" style={{ backgroundColor: l.color }} />
      <span className="truncate">{l.label}</span>
    </div>
  )
}

export function TimelineView(props: TimelineViewProps) {
  const {
    duration, cues, lanes, selected, playhead, pxPerSec, mediaId, overlaps,
    boards = NO_BOARDS, base, overrides = NO_OVERRIDES, readOnly = false,
    snapEnabled, onSeek, onSelect, onChange, onZoom, onAddAt,
    markers = NO_LIST, selectedMarkers = NO_SET, onMarkerSelect = noop, onMarkersChange = noop, onMarkerRename = noop,
    onsets = NO_LIST, snapExtra = NO_LIST, loop = null, fps = 25, muted = NO_SET, solo = NO_SET,
    onToggleMute, onToggleSolo, viewReq = null, expanded = NO_SET, onToggleExpand,
    audio = NO_AUDIO, waves = NO_WAVES, groups = NO_GROUPS,
  } = props
  const editable = !readOnly

  const scrollRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const seekPointer = useRef<number | null>(null)
  const zoomAnchor = useRef<{ t: number; cx: number; pps: number } | null>(null)
  const lastPlayhead = useRef(playhead)
  // Scroll viewport in px, fed by scroll/resize events; the minimap converts to seconds.
  const [vp, setVp] = useState({ left: 0, width: 0 })

  const contentW = Math.max(duration, 1) * pxPerSec + 40
  const lanesH = lanes.reduce((n, l) => n + laneHeight(l), 0)
  const isMuted = (l: Lane) => l.kind === 'group'
    ? (l.macs ?? []).every((m) => isMacMuted(m, lanes, muted, solo))
    : isMacMuted(l.id, lanes, muted, solo)
  const boardByMac = useMemo(() => new Map(boards.map((b) => [b.mac, b])), [boards])
  const boardsKey = boards.map((b) => b.mac).join(',')
  // Rebuilt on doc/base changes only (deferred so drags stay smooth); the playhead never invalidates it.
  const deferredCues = useDeferredValue(cues)
  const resolveCtx = useMemo<ResolveCtx>(() => ({
    timeline: new Timeline(toSpecs(deferredCues, groups), boardsKey ? boardsKey.split(',') : []),
    base: base ?? {},
    overrides,
  }), [deferredCues, boardsKey, base, overrides, groups])

  const laneItems = useMemo(() => {
    const m = new Map<string, LaneItem[]>()
    const lists = new Map<string, TLCue[]>()
    for (const l of lanes) lists.set(l.id, [])
    for (const c of cues) {
      // Segment cues of a collapsed board are drawn once on the board lane.
      const ids = new Set(lanesOf(c).map((id) => (lists.has(id) ? id : parseLaneId(id).mac)))
      for (const id of ids) lists.get(id)?.push(c)
    }
    for (const [id, list] of lists) {
      list.sort((a, b) => a.start_s - b.start_s)
      m.set(id, list.map((cue, i) => {
        if (isSound(cue)) return { cue, end: soundEnd(cue, audio), open: false }
        if (cue.duration_s != null) return { cue, end: cue.start_s + cue.duration_s, open: false }
        let end = Math.max(duration, cue.start_s)
        for (let j = i + 1; j < list.length; j++) {
          if (list[j].start_s > cue.start_s) { end = list[j].start_s; break }
        }
        return { cue, end, open: true }
      }))
    }
    return m
  }, [cues, lanes, duration, audio])

  // Mini waveform per sound cue in seconds-space (viewBox), so zoom never recomputes it.
  const wavePaths = useMemo(() => {
    const out = new Map<string, { len: number; pts: string }>()
    for (const c of cues) {
      const w = isSound(c) && c.sound ? waves.get(c.sound.media_id) : undefined
      if (!w || !w.peaks.length || w.rate <= 0) continue
      const len = soundEnd(c, audio) - c.start_s
      const mediaLen = w.peaks.length / w.rate
      const span = c.sound?.loop ? len : Math.min(len, mediaLen)
      const n = Math.max(2, Math.min(240, Math.ceil(span * w.rate)))
      const top: string[] = []
      const bot: string[] = []
      for (let i = 0; i <= n; i++) {
        const t = (span * i) / n
        const p = w.peaks[Math.min(w.peaks.length - 1, Math.floor((t % mediaLen) * w.rate))] ?? 0
        const x = round3(t)
        top.push(`${x},${round3(0.5 - p / 2)}`)
        bot.push(`${x},${round3(0.5 + p / 2)}`)
      }
      out.set(c.key, { len, pts: [...top, ...bot.reverse()].join(' ') })
    }
    return out
  }, [cues, waves, audio])
  const blockTitle = (cue: TLCue, laneId: string) => {
    if (!isSound(cue)) return cue.label || stateLabel(cue, boardByMac.get(laneId)?.effects ?? [])
    const name = cue.sound ? audio.get(cue.sound.media_id)?.filename ?? `media #${cue.sound.media_id}` : 'no media'
    return cue.label || `♪ ${name}`
  }

  const ticks = useMemo(() => {
    // Zoomed in far enough that single frames are distinguishable: tick per frame, label every 5.
    // Only the visible window (plus one screen each side): a full-length frame ruler is ~90k nodes per hour.
    if (pxPerSec / fps >= 8) {
      const n = Math.floor(Math.max(duration, 1) * fps + 1e-6)
      const w = vp.width || 2000
      const lo = Math.max(0, Math.floor(((vp.left - w) / pxPerSec) * fps))
      const hi = Math.min(n, Math.ceil(((vp.left + 2 * w) / pxPerSec) * fps))
      const out: { t: number; major: boolean }[] = []
      for (let i = lo - (lo % 5); i <= hi; i++) out.push({ t: i / fps, major: i % 5 === 0 })
      return { step: 0, list: out }
    }
    const step = STEPS.find((s) => s * pxPerSec >= 60) ?? STEPS[STEPS.length - 1]
    const minor = step / 5
    const n = Math.floor(Math.max(duration, 1) / minor + 1e-6)
    const out: { t: number; major: boolean }[] = []
    for (let i = 0; i <= n; i++) out.push({ t: i * minor, major: i % 5 === 0 })
    return { step, list: out }
  }, [duration, pxPerSec, fps, vp])

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

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setVp({ left: el.scrollLeft, width: el.clientWidth })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    el.addEventListener('scroll', update, { passive: true })
    return () => { ro.disconnect(); el.removeEventListener('scroll', update) }
  }, [])

  // Zoom to fit / zoom to selection: reuse the zoom anchor so the scroll lands after the re-render.
  const viewN = viewReq?.n
  useEffect(() => {
    const el = scrollRef.current
    if (!viewReq || !el) return
    const pad = el.clientWidth * 0.025
    const span = Math.max(viewReq.end - viewReq.start, 0.1)
    const next = clamp((el.clientWidth - 2 * pad) / span, 5, 400)
    if (next === pxPerSec) { el.scrollLeft = Math.max(0, viewReq.start * pxPerSec - pad); return }
    zoomAnchor.current = { t: viewReq.start, cx: pad, pps: next }
    onZoom(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fire once per request
  }, [viewN])

  useLayoutEffect(() => {
    const a = zoomAnchor.current
    const el = scrollRef.current
    if (!el) return
    if (a) {
      zoomAnchor.current = null
      el.scrollLeft = Math.max(0, a.t * pxPerSec - a.cx)
    }
    // Zooming doesn't always fire a scroll event; keep the minimap window in step.
    setVp({ left: el.scrollLeft, width: el.clientWidth })
  }, [pxPerSec])

  // Memoized: TimelineView re-renders every animation frame during playback.
  const onsetLayer = useMemo(() => onsets.map((o) => (
    <div key={o.t} className="pointer-events-none absolute bottom-0 w-px bg-slime"
      style={{ left: o.t * pxPerSec, height: `${Math.round(30 + o.strength * 70)}%`, opacity: 0.4 + o.strength * 0.6 }} />
  )), [onsets, pxPerSec])

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
      cands: [...snapCandidates(cues, keys, playhead), ...snapExtra],
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
    <div className="w-full select-none overflow-hidden rounded-xl border border-line bg-bg text-ink">
      <div className="flex border-b border-line">
        <div className="w-32 shrink-0 border-r border-line bg-panel px-2 text-[10px] leading-6 text-mute">Overview</div>
        <div className="min-w-0 flex-1">
          <Minimap duration={duration} cues={cues} markers={markers} playhead={playhead}
            viewStart={vp.left / pxPerSec} viewSpan={vp.width / pxPerSec}
            onScrollTo={(t) => { const el = scrollRef.current; if (el) el.scrollLeft = Math.max(0, t * pxPerSec) }} />
        </div>
      </div>
    <div className="flex w-full">
      {/* label column */}
      <div className="w-32 shrink-0 border-r border-line bg-panel">
        <div className="border-b border-line" style={{ height: RULER_H }} />
        <div className="flex items-center border-b border-line px-2 text-[10px] text-mute" style={{ height: MARKERS_H }}>
          Markers (M)
        </div>
        <div className="flex items-center border-b border-line px-2 text-xs text-mute" style={{ height: STRIP_H }}>
          Media
        </div>
        {lanes.map((l, i) => (
          <div
            key={l.id}
            className={`flex items-center gap-2 border-b border-line px-2 text-xs ${
              l.id === ALL_LANE ? 'bg-ghost/10 font-semibold' : i % 2 ? 'bg-panel-2' : 'bg-panel'
            }`}
            style={{ height: laneHeight(l) }}
            title={l.label}
          >
            {l.kind === 'board' && <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[l.status ?? 'unknown']}`} />}
            {l.kind === 'group' && <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: groups.find((g) => groupLaneId(g.id) === l.id)?.color }} />}
            {l.kind === 'segment' && <span className="w-3 shrink-0 text-mute">└</span>}
            {l.kind === 'sound' && <span className="shrink-0 text-mute">♪</span>}
            {l.kind === 'board' && onToggleExpand && (boardByMac.get(l.id)?.segments.length ?? 0) > 1 && (
              <button type="button" className="shrink-0 text-[10px] text-mute hover:text-ink" title="Show segments"
                onClick={() => onToggleExpand(l.id)}>{expanded.has(l.id) ? '▾' : '▸'}</button>
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate">{l.label}</div>
              {l.kind === 'board' && boardByMac.has(l.id) && (
                <LanePreview ctx={resolveCtx} board={boardByMac.get(l.id)!} t={playhead} />
              )}
            </div>
            {(l.kind === 'board' || l.kind === 'group') && onToggleMute && onToggleSolo && (
              <div className="flex shrink-0 flex-col gap-0.5">
                <button type="button" title="Mute in audition" onClick={() => onToggleMute(l.id)}
                  className={`rounded px-1 text-[9px] font-bold leading-tight ${muted.has(l.id) ? 'bg-blood text-white' : 'bg-panel-2 text-mute'}`}>M</button>
                <button type="button" title="Solo in audition" onClick={() => onToggleSolo(l.id)}
                  className={`rounded px-1 text-[9px] font-bold leading-tight ${solo.has(l.id) ? 'bg-slime text-black' : 'bg-panel-2 text-mute'}`}>S</button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* scroll area */}
      <div ref={scrollRef} className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden">
        <div className="relative" style={{ width: contentW, height: RULER_H + MARKERS_H + STRIP_H + lanesH }}>
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
                      {ticks.step === 0 ? timecode(t, fps) : fmt(t, ticks.step)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
          <div className="border-b border-line bg-panel">
            <MarkersLane markers={markers} duration={duration} pxPerSec={pxPerSec} selected={selectedMarkers}
              readOnly={readOnly}
              snap={(t, id) => (snapEnabled
                ? snap(t, [...snapCandidates(cues, NO_SET, playhead), ...snapExtra, ...markers.filter((m) => m.id !== id).map((m) => m.t)], SNAP_PX / pxPerSec)
                : t)}
              onSelect={onMarkerSelect} onChange={onMarkersChange} onRename={onMarkerRename} onSeek={onSeek} />
          </div>
          <div
            className="relative cursor-pointer touch-none"
            style={{ height: STRIP_H }}
            onPointerDown={onSeekDown}
            onPointerMove={onSeekMove}
            onPointerUp={onSeekUp}
            onPointerCancel={onSeekUp}
          >
            <MediaStrip mediaId={mediaId} duration={duration} pxPerSec={pxPerSec} />
            {onsetLayer}
          </div>

          {/* lanes */}
          {lanes.map((l, i) => (
            <div
              key={l.id}
              className={`relative border-b border-line ${
                l.id === ALL_LANE ? 'bg-ghost/10' : i % 2 ? 'bg-panel-2' : 'bg-panel'
              } ${(l.kind === 'board' || l.kind === 'group') && isMuted(l) ? 'opacity-40' : ''}`}
              style={{ height: laneHeight(l) }}
              onClick={onLaneClick}
              onDoubleClick={(e) => onLaneDouble(e, l.id)}
            >
              {l.kind === 'board' && boardByMac.has(l.id) && (
                <Ribbon ctx={resolveCtx} mac={l.id} effects={boardByMac.get(l.id)!.effects} presets={boardByMac.get(l.id)!.presets} duration={duration} pxPerSec={pxPerSec} height={RIBBON_H} />
              )}
              {(laneItems.get(l.id) ?? []).map(({ cue, end, open }) => {
                const col = cueColor(cue)
                const isSel = selected.has(cue.key)
                const isOverlap = overlaps.has(cue.key)
                const width = Math.max(MIN_BLOCK_PX, (end - cue.start_s) * pxPerSec)
                const wave = wavePaths.get(cue.key)
                const title = blockTitle(cue, l.id)
                // A sound cue always starts its media at 0, so a start-trim only makes sense with a fixed end.
                const leftHandle = !open && !(isSound(cue) && cue.duration_s == null)
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
                    title={title}
                    onPointerDown={(e) => onBlockDown(e, cue, 'move', end)}
                    onPointerMove={onBlockMove}
                    onPointerUp={onBlockUp}
                    onPointerCancel={onBlockUp}
                  >
                    {wave && (
                      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${wave.len} 1`} preserveAspectRatio="none">
                        <polygon points={wave.pts} fill={col} opacity={0.45} />
                      </svg>
                    )}
                    <span className="pointer-events-none relative truncate px-1.5">{title}</span>
                    {isOverlap && (
                      <span className="pointer-events-none absolute right-0.5 top-0 text-[10px] text-blood">⚠</span>
                    )}
                    {editable && leftHandle && (
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

          {loop && loop.b > loop.a && (
            <div className="pointer-events-none absolute inset-y-0 z-10 border-x border-ghost/70 bg-ghost/10"
              style={{ left: loop.a * pxPerSec, width: (loop.b - loop.a) * pxPerSec }} />
          )}

          {/* playhead */}
          <div
            className="pointer-events-none absolute inset-y-0 z-20 w-0.5 -translate-x-1/2 bg-pumpkin"
            style={{ left: playhead * pxPerSec }}
          />
        </div>
      </div>
    </div>
    </div>
  )
}
