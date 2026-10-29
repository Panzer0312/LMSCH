import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { ErrorText, Toggle } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import { useAdoptedBoards } from '../live'
import { CuePanel, MultiPanel } from '../timeline/CuePanel'
import { GenerateDialog } from '../timeline/GenerateDialog'
import type { GenCue } from '../timeline/generators'
import { ALL_LANE, clamp, fromServer, newKey, overlapKeys, round3, toServer, useHistory, lanesOf, segLaneId, targetFromLanes, type Doc, type Lane, type SetMode, type TLCue, isDragging } from '../timeline/model'
import { RemapDialog } from '../timeline/RemapDialog'
import { TemplateDialog } from '../timeline/TemplateDialog'
import { CUE_TEMPLATES, resolveTemplate } from '../timeline/templates'
import { CLIP_KEY, clipLanes, deleteRange, fromClip, insertTime, parseClip, rippleShift, timecode, toClip, type Clip } from '../timeline/timeops'
import { TimelineView } from '../timeline/TimelineView'
import type { CueTarget, LightState, Marker, Media, Onset, Profile, SavedTemplate, TemplateCue } from '../types'

// The media probe has no frame rate yet; 25 is the plan's fallback.
const FPS = 25
const FRAME = 1 / FPS
const NO_EFFECTS: string[] = []
const MOBILE_Q = '(max-width: 767px)'
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const laneTarget = (laneId: string): CueTarget => targetFromLanes([laneId])

export default function TimelineEditor() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const [data, setData] = useState<{ profile: Profile; media: Media | null } | null>(null)
  const { error, run } = useAction()

  useEffect(() => {
    run(async () => {
      const [profile, list] = await Promise.all([api.profiles.get(pid), api.media.list()])
      setData({ profile, media: list.find((m) => m.id === profile.media_id) ?? null })
    })
  }, [pid, run])

  const back = <Link to={`/profiles/${id}`} className="text-sm text-ghost hover:underline">← Back to profile</Link>
  if (error) return <div className="space-y-3">{back}<ErrorText error={error} /></div>
  if (!data || data.profile.id !== pid) return <p className="text-sm text-mute">Loading…</p>
  const { profile, media } = data
  if (profile.type !== 'video' || profile.media_id == null) {
    return (
      <div className="space-y-3">
        {back}
        <p className="text-sm text-mute">
          {profile.type !== 'video' ? 'The timeline editor is only available for video profiles.' : 'This profile has no video assigned yet.'}
        </p>
      </div>
    )
  }
  return <Editor key={profile.id} profile={profile} media={media} mediaId={profile.media_id} />
}

type Dialog =
  | { kind: 'templates' }
  | { kind: 'onsets' }
  | { kind: 'markers' }
  | { kind: 'remap'; clip: Clip; at: number }
  | null

function Editor({ profile, media, mediaId }: { profile: Profile; media: Media | null; mediaId: number }) {
  const pid = profile.id
  const adopted = useAdoptedBoards()
  const boards = profile.selection.all ? adopted : adopted.filter((b) => profile.selection.macs.includes(b.mac))
  const macsKey = boards.map((b) => b.mac).join(',')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const lanes: Lane[] = [{ kind: 'all', id: ALL_LANE, label: 'All selected' }]
  for (const b of boards) {
    lanes.push({ kind: 'board', id: b.mac, label: b.name, status: b.status })
    // Segment sub-lanes only make sense when the strip is actually split.
    if (expanded.has(b.mac) && b.segments.length > 1) {
      for (const sg of b.segments) {
        lanes.push({ kind: 'segment', id: segLaneId(b.mac, sg.id), label: sg.name || `Seg ${sg.id}`, status: b.status, mac: b.mac, seg: sg.id })
      }
    }
  }
  const effects = boards.find((b) => b.effects.length > 0)?.effects ?? NO_EFFECTS

  const [initial] = useState<Doc>(() => ({ cues: fromServer(profile.cues), markers: profile.markers ?? [] }))
  const { doc, set: setDoc, undo: undoDoc, redo: redoDoc, canUndo, canRedo } = useHistory<Doc>(initial)
  const cues = doc.cues
  const [rev, setRev] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [selMarkers, setSelMarkers] = useState<Set<string>>(() => new Set())
  const [playhead, setPlayhead] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [videoDur, setVideoDur] = useState(0)
  const duration = media?.duration_s || videoDur
  const [pxPerSec, setPxPerSec] = useState(() => clamp(1100 / (media?.duration_s || 60), 5, 400))
  const [snapOn, setSnapOn] = useState(true)
  const [mobile, setMobile] = useState(() => window.matchMedia(MOBILE_Q).matches)
  const [loop, setLoop] = useState<{ a: number; b: number } | null>(null)
  const [viewReq, setViewReq] = useState<{ start: number; end: number; n: number } | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [preview, setPreview] = useState<TLCue[] | null>(null)
  const [saved, setSaved] = useState<SavedTemplate[]>([])
  const [onsets, setOnsets] = useState<Onset[]>([])
  const [threshold, setThreshold] = useState(0.3)
  const [showOnsets, setShowOnsets] = useState(true)
  const [shiftBy, setShiftBy] = useState(1)
  const [muted, setMuted] = useState<Set<string>>(() => new Set())
  const [solo, setSolo] = useState<Set<string>>(() => new Set())
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlaps = useMemo(() => overlapKeys(cues, macsKey ? macsKey.split(',') : []), [cues, macsKey])
  const proj = useAction()
  const tools = useAction()

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_Q)
    const h = (e: MediaQueryListEvent) => setMobile(e.matches)
    mq.addEventListener('change', h)
    return () => mq.removeEventListener('change', h)
  }, [])

  useEffect(() => {
    api.media.onsets(mediaId).then((o) => setOnsets(o.onsets), () => {})
    api.templates.list().then(setSaved, () => {})
  }, [mediaId])

  // Loop A–B is enforced here (not via the video loop flag) so any range works.
  const loopRef = useRef(loop)
  useEffect(() => { loopRef.current = loop }, [loop])
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      const v = videoRef.current
      const l = loopRef.current
      if (v) {
        if (l && l.b > l.a && v.currentTime >= l.b) v.currentTime = l.a
        setPlayhead(v.currentTime)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  // --- autosave (only committed changes bump rev) ---
  const [savedRev, setSavedRev] = useState(0)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const saver = useRef({ doc: initial, pending: false, markers: initial.markers, chain: Promise.resolve() })
  useEffect(() => { saver.current.doc = doc }, [doc])
  const persist = useCallback(async () => {
    const s = saver.current
    const d = s.doc
    await api.profiles.setCues(pid, toServer(d.cues))
    // Markers have their own endpoint; skip it when untouched to avoid a redundant profiles_changed.
    if (d.markers !== s.markers) {
      await api.profiles.setMarkers(pid, d.markers)
      s.markers = d.markers
    }
  }, [pid])
  const save = useCallback((r: number) => {
    const s = saver.current
    s.pending = false
    // Serialized so an older PUT can never land after a newer one; each run sends the latest doc.
    const run = s.chain.then(persist)
    s.chain = run.catch(() => {})
    run.then(
      () => { setSaveErr(null); setSavedRev((x) => Math.max(x, r)) },
      (e) => { s.pending = true; setSaveErr(errMsg(e)) },
    )
  }, [persist])
  useEffect(() => {
    if (rev === 0) return
    saver.current.pending = true
    const t = setTimeout(() => save(rev), 800)
    return () => clearTimeout(t)
  }, [rev, save])
  useEffect(() => {
    const s = saver.current
    return () => { if (s.pending) persist().catch(() => {}) }
  }, [persist])

  // --- live audition ---
  const [audition, setAudition] = useState(false)
  const [auditionErr, setAuditionErr] = useState<string | null>(null)
  const mutedKey = macsKey.split(',').filter((m) => m && (muted.has(m) || (solo.size > 0 && !solo.has(m)))).join(',')
  const mutedRef = useRef<string[]>([])
  useEffect(() => { mutedRef.current = mutedKey ? mutedKey.split(',') : [] }, [mutedKey])
  const sendAudition = useCallback((t: number, isPlaying: boolean) => {
    api.show.audition(pid, t, isPlaying, mutedRef.current).catch((e) => {
      setAuditionErr(errMsg(e))
      // 409 = backend refused (show playing, blackout). Network blips just retry on next beat.
      if (e instanceof ApiError && e.status === 409) setAudition(false)
    })
  }, [pid])
  useEffect(() => {
    if (!audition) return
    const beat = () => { const v = videoRef.current; if (v) sendAudition(v.currentTime, !v.paused) }
    beat()
    const iv = setInterval(beat, 1000)
    return () => { clearInterval(iv); api.show.auditionStop().catch(() => {}) }
  }, [audition, sendAudition])
  // Mute/solo changes apply immediately instead of on the next heartbeat.
  useEffect(() => {
    if (!audition) return
    const v = videoRef.current
    if (v) sendAudition(v.currentTime, !v.paused)
  }, [mutedKey, audition, sendAudition])

  // --- actions ---
  const now = () => videoRef.current?.currentTime ?? playhead
  const lastEdit = useRef({ key: '', at: 0 })
  const commit = (next: Doc, mode: SetMode = false) => {
    if (mode !== 'amend') lastEdit.current = { key: '', at: 0 }
    setDoc(next, mode)
    setRev((r) => r + 1)
  }
  const commitCues = (next: TLCue[]) => commit({ ...doc, cues: next })
  // Coalescing must not reach across an undo/redo, or the amend would overwrite a restored step.
  const undo = () => { if (canUndo) { lastEdit.current = { key: '', at: 0 }; undoDoc(); setRev((r) => r + 1) } }
  const redo = () => { if (canRedo) { lastEdit.current = { key: '', at: 0 }; redoDoc(); setRev((r) => r + 1) } }
  const onViewChange = (next: TLCue[], isCommit: boolean) => {
    if (isCommit) commitCues(next)
    else setDoc({ ...doc, cues: next }, true)
  }
  const onMarkersChange = (markers: Marker[], isCommit: boolean) => {
    if (isCommit) commit({ ...doc, markers })
    else setDoc({ ...doc, markers }, true)
  }

  const seek = (t: number) => {
    const tt = duration > 0 ? clamp(t, 0, duration) : Math.max(0, t)
    const v = videoRef.current
    if (v) v.currentTime = tt
    setPlayhead(tt)
  }
  const togglePlay = () => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) {
      if (loop && loop.b > loop.a && (v.currentTime < loop.a || v.currentTime >= loop.b)) v.currentTime = loop.a
      v.play().catch(() => {})
    } else v.pause()
  }
  const zoom = (f: number) => setPxPerSec((p) => clamp(p * f, 5, 400))
  const fitView = (start: number, end: number) => setViewReq((r) => ({ start, end, n: (r?.n ?? 0) + 1 }))

  const selCues = cues.filter((c) => selected.has(c.key))
  const mkCue = (t: number, target: CueTarget, state: LightState, duration_s: number | null, extra: Partial<TLCue> = {}): TLCue =>
    ({ key: newKey(), start_s: round3(Math.max(0, t)), duration_s, target, state, transition_ms: null, ...extra })
  const addCues = (added: TLCue[]) => {
    if (!added.length) return
    commitCues([...cues, ...added])
    setSelected(new Set(added.map((c) => c.key)))
  }
  const addCue = (t: number, target: CueTarget, state: LightState = { on: true, bri: 255 }, duration_s: number | null = null) =>
    addCues([mkCue(t, target, state, duration_s)])
  const playheadTarget = () => {
    const one = selCues.length === 1 ? selCues[0] : null
    return laneTarget(one ? lanesOf(one)[0] ?? ALL_LANE : ALL_LANE)
  }
  const addAtPlayhead = () => addCue(now(), playheadTarget())
  const addTemplate = (tid: string) => {
    const t = CUE_TEMPLATES.find((x) => x.id === tid)
    if (t) addCue(now(), playheadTarget(), resolveTemplate(t, boards), t.duration_s)
  }
  const deleteSel = () => {
    if (!selCues.length && !selMarkers.size) return
    commit({ cues: cues.filter((c) => !selected.has(c.key)), markers: doc.markers.filter((m) => !selMarkers.has(m.id)) })
    setSelected(new Set())
    setSelMarkers(new Set())
  }
  const duplicateSel = () => {
    if (!selCues.length) return
    addCues(selCues.map((c) => ({ ...c, key: newKey(), start_s: round3(c.start_s + 0.5) })))
  }
  const nudgeSel = (d: number) => {
    if (!selCues.length) return
    commitCues(cues.map((c) => (selected.has(c.key) ? { ...c, start_s: round3(Math.max(0, c.start_s + d)) } : c)))
  }
  // A burst of edits to one cue (slider drag, typing) becomes a single undo step.
  const updateCue = (next: TLCue) => {
    const nowMs = Date.now()
    const amend = lastEdit.current.key === next.key && nowMs - lastEdit.current.at < 800
    commit({ ...doc, cues: cues.map((c) => (c.key === next.key ? next : c)) }, amend ? 'amend' : false)
    lastEdit.current = { key: next.key, at: nowMs }
  }

  // --- markers ---
  const addMarker = () => {
    const m: Marker = { id: newKey(), t: round3(now()), label: '', color: null }
    commit({ ...doc, markers: [...doc.markers, m].sort((a, b) => a.t - b.t) })
  }
  const renameMarker = (id: string, label: string) =>
    commit({ ...doc, markers: doc.markers.map((m) => (m.id === id ? { ...m, label } : m)) })

  // --- onsets ---
  const visibleOnsets = useMemo(() => (showOnsets ? onsets.filter((o) => o.strength >= threshold) : []), [onsets, threshold, showOnsets])
  const snapExtra = useMemo(() => [...doc.markers.map((m) => m.t), ...visibleOnsets.map((o) => o.t)], [doc.markers, visibleOnsets])

  const generate = (r: { times: number[]; templateId: string; laneId: string; durationOverride: number | null }) => {
    const t = CUE_TEMPLATES.find((x) => x.id === r.templateId)
    if (!t) return
    const state = resolveTemplate(t, boards)
    addCues(r.times.map((at) => mkCue(at, laneTarget(r.laneId), state, r.durationOverride ?? t.duration_s)))
    setDialog(null)
  }

  // --- templates ---
  const genToCues = (gcs: GenCue[], at: number, target: CueTarget): TLCue[] =>
    gcs.map((g) => mkCue(at + g.offset_s, g.lane === 'all' ? laneTarget(ALL_LANE) : target, g.state, g.duration_s,
      { transition_ms: g.transition_ms, label: g.label ?? null, color: g.color ?? null }))
  const insertGenerated = (gcs: GenCue[]) => { setPreview(null); addCues(genToCues(gcs, now(), playheadTarget())) }
  const saveAsTemplate = () => {
    if (!selCues.length) return
    const name = window.prompt('Template name')?.trim()
    if (!name) return
    const t0 = Math.min(...selCues.map((c) => c.start_s))
    const tcs: TemplateCue[] = selCues.map((c) => ({
      offset_s: round3(c.start_s - t0), duration_s: c.duration_s, lane: c.target.all ? 'all' : 'same',
      state: c.state, transition_ms: c.transition_ms, label: c.label ?? null, color: c.color ?? null,
    }))
    tools.run(async () => {
      const created = await api.templates.create(name, tcs)
      setSaved((s) => [...s, created])
    })
  }
  const deleteSaved = (id: number) => tools.run(async () => {
    await api.templates.remove(id)
    setSaved((s) => s.filter((t) => t.id !== id))
  })

  // --- clipboard ---
  const copySel = () => {
    if (!selCues.length) return
    try { localStorage.setItem(CLIP_KEY, JSON.stringify(toClip(selCues))) } catch { /* storage blocked */ }
  }
  const paste = () => {
    let raw: string | null = null
    try { raw = localStorage.getItem(CLIP_KEY) } catch { /* storage blocked */ }
    const clip = parseClip(raw)
    if (!clip) return
    const at = now()
    const known = new Set(lanes.map((l) => l.id))
    if (clipLanes(clip).every((id) => known.has(id))) addCues(fromClip(clip, at, {}))
    else setDialog({ kind: 'remap', clip, at })
  }

  // --- time editing ---
  const ripple = (delta: number) => {
    const keys = selCues.length ? selected : undefined
    const from = keys ? 0 : now()
    // Clamp once so nothing piles up at 0 and relative spacing survives.
    const starts = keys ? selCues.map((c) => c.start_s)
      : [...cues.map((c) => c.start_s), ...doc.markers.map((m) => m.t)].filter((t) => t >= from)
    const d = starts.length ? Math.max(delta, -Math.min(...starts)) : delta
    if (d !== 0) commit(rippleShift(cues, doc.markers, from, d, keys))
  }
  const insertGap = () => { if (shiftBy > 0) commit(insertTime(cues, doc.markers, now(), shiftBy)) }
  const deleteLoopRange = () => {
    if (!loop || loop.b <= loop.a) return
    commit(deleteRange(cues, doc.markers, loop.a, loop.b))
    setLoop(null)
  }
  const setLoopA = () => { const t = round3(now()); setLoop((l) => ({ a: t, b: l && l.b > t ? l.b : duration })) }
  const setLoopB = () => { const t = round3(now()); setLoop((l) => ({ a: l && l.a < t ? l.a : 0, b: t })) }
  const zoomToSelection = () => {
    if (!selCues.length) return
    const a = Math.min(...selCues.map((c) => c.start_s))
    const b = Math.max(...selCues.map((c) => c.start_s + (c.duration_s ?? 1)))
    fitView(a, b)
  }

  // --- keyboard ---
  const onKey = (e: KeyboardEvent) => {
    if (dialog) return
    const el = e.target as HTMLElement | null
    if (el && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
    if (isDragging()) return
    const mod = e.ctrlKey || e.metaKey
    const k = e.key
    if (k === ' ') { e.preventDefault(); togglePlay(); return }
    if (k === 'ArrowLeft' || k === 'ArrowRight') {
      e.preventDefault()
      const d = e.shiftKey ? FRAME : 0.1
      seek(now() + (k === 'ArrowLeft' ? -d : d))
      return
    }
    if (k === 'Escape') { setSelected(new Set()); setSelMarkers(new Set()); return }
    if (!mod && (k === '+' || k === '=')) { zoom(1.25); return }
    if (!mod && (k === '-' || k === '_')) { zoom(0.8); return }
    if (!mod && k === '[') { setLoopA(); return }
    if (!mod && k === ']') { setLoopB(); return }
    if (!mod && k === '\\') { setLoop(null); return }
    if (!mod && k === 'f') { fitView(0, duration); return }
    if (!mod && k === 'F') { zoomToSelection(); return }
    if (mobile) return
    const lk = k.toLowerCase()
    if (mod && lk === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return }
    if (mod && lk === 'y') { e.preventDefault(); redo(); return }
    if (mod && lk === 'd') { e.preventDefault(); duplicateSel(); return }
    if (mod && lk === 'c') { copySel(); return }
    if (mod && lk === 'v') { e.preventDefault(); paste(); return }
    if (mod) return
    if (lk === 'c') { addAtPlayhead(); return }
    if (lk === 'm') { addMarker(); return }
    if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); deleteSel() }
  }
  const keyRef = useRef(onKey)
  useEffect(() => { keyRef.current = onKey })
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyRef.current(e)
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  const status = saveErr ? (
    <span className="flex items-center gap-2 text-sm text-blood">
      Save failed: {saveErr}
      <button type="button" className="btn" onClick={() => save(rev)}>Retry</button>
    </span>
  ) : (
    <span className="text-sm text-mute">{rev > savedRev ? 'Saving…' : savedRev > 0 ? 'Saved ✓' : ''}</span>
  )

  const panel = selCues.length === 1 ? (
    <CuePanel key={selCues[0].key} cue={selCues[0]} boards={boards} lanes={lanes} readOnly={mobile} fps={FPS}
      onChange={updateCue} onDelete={deleteSel} onDuplicate={duplicateSel} />
  ) : selCues.length > 1 ? (
    <MultiPanel count={selCues.length} readOnly={mobile} onDelete={deleteSel} onDuplicate={duplicateSel} onNudge={nudgeSel} />
  ) : (
    <div className="card text-sm text-mute">
      {mobile ? 'Tap a cue to see its details. Editing works on desktop.' : 'Select a cue to edit it. Double-click a lane or press C to add one. M drops a marker.'}
    </div>
  )

  const laneOpts = lanes.map((l) => ({ id: l.id, label: l.label }))
  const boardName = (mac: string) => adopted.find((b) => b.mac === mac)?.name ?? mac
  const toggler = (set: (f: (s: Set<string>) => Set<string>) => void) => (id: string) =>
    set((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const pt = playheadTarget()

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <Link to={`/profiles/${pid}`} className="btn">← Back</Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold">{profile.name}</h1>
          <p className="truncate text-xs text-mute">{media?.filename ?? `media #${mediaId}`}</p>
        </div>
        {audition && <span className="rounded bg-blood px-1.5 py-0.5 text-xs font-semibold text-white">AUDITION LIVE</span>}
        {status}
      </header>

      <div className="flex flex-col gap-4 md:flex-row">
        <div className="min-w-0 flex-1 space-y-3">
          <video
            ref={videoRef}
            src={api.media.fileUrl(mediaId)}
            preload="auto"
            playsInline
            className="max-h-[38vh] w-full bg-black"
            onClick={togglePlay}
            onPlay={(e) => { setPlaying(true); if (audition) sendAudition(e.currentTarget.currentTime, true) }}
            onPause={(e) => {
              const t = e.currentTarget.currentTime
              setPlaying(false)
              setPlayhead(t)
              if (audition) sendAudition(t, false)
            }}
            onSeeked={(e) => {
              const t = e.currentTarget.currentTime
              setPlayhead(t)
              if (audition) sendAudition(t, !e.currentTarget.paused)
            }}
            onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => setVideoDur(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0)}
          />

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn btn-primary w-20" onClick={togglePlay}>{playing ? 'Pause' : 'Play'}</button>
            <span className="font-mono text-sm tabular-nums">{timecode(playhead, FPS)} / {fmtTime(duration)}</span>
            <div className="flex gap-1">
              <button type="button" className="btn" title="Zoom out (-)" onClick={() => zoom(0.8)}>−</button>
              <button type="button" className="btn" title="Zoom in (+)" onClick={() => zoom(1.25)}>+</button>
              <button type="button" className="btn" title="Zoom to fit (F)" onClick={() => fitView(0, duration)}>Fit</button>
              <button type="button" className="btn" title="Zoom to selection (Shift+F)" disabled={!selCues.length} onClick={zoomToSelection}>Sel</button>
            </div>
            <Toggle checked={snapOn} onChange={setSnapOn} label="Snap" />
            <div className="flex items-center gap-1 text-xs">
              <button type="button" className="btn" title="Loop start ([)" onClick={setLoopA}>A</button>
              <button type="button" className="btn" title="Loop end (])" onClick={setLoopB}>B</button>
              {loop && (
                <>
                  <span className="font-mono text-mute">{fmtTime(loop.a)}–{fmtTime(loop.b)}</span>
                  <button type="button" className="btn" title="Clear loop (\)" onClick={() => setLoop(null)}>×</button>
                </>
              )}
            </div>
            {!mobile && (
              <>
                <button type="button" className="btn" title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={undo}>Undo</button>
                <button type="button" className="btn" title="Redo (Ctrl+Shift+Z)" disabled={!canRedo} onClick={redo}>Redo</button>
                <button type="button" className="btn" title="C" onClick={addAtPlayhead}>Add cue at playhead</button>
                <select className="btn" value="" title="Insert template cue at playhead" onChange={(e) => addTemplate(e.target.value)}>
                  <option value="">Template ▾</option>
                  {CUE_TEMPLATES.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <button type="button" className="btn" onClick={() => setDialog({ kind: 'templates' })}>Templates…</button>
              </>
            )}
          </div>

          {!mobile && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <button type="button" className="btn" title="M" onClick={addMarker}>+ Marker</button>
              <button type="button" className="btn" disabled={!selMarkers.size} onClick={() => setDialog({ kind: 'markers' })}>
                Markers → cues{selMarkers.size ? ` (${selMarkers.size})` : ''}
              </button>
              <button type="button" className="btn" disabled={!selCues.length} title="Copy (Ctrl+C)" onClick={copySel}>Copy</button>
              <button type="button" className="btn" title="Paste at playhead (Ctrl+V)" onClick={paste}>Paste</button>
              <button type="button" className="btn" disabled={!selCues.length} onClick={saveAsTemplate}>Save as template</button>
              <span className="mx-1 h-5 w-px bg-line" />
              <label className="flex items-center gap-1 text-xs text-mute">
                Δ s
                <input type="number" className="input w-16" step={0.1} value={shiftBy}
                  onChange={(e) => setShiftBy(Number(e.target.value) || 0)} />
              </label>
              <button type="button" className="btn" title={selCues.length ? 'Shift selected cues earlier' : 'Shift everything after the playhead earlier'} onClick={() => ripple(-shiftBy)}>⇤ Shift</button>
              <button type="button" className="btn" title={selCues.length ? 'Shift selected cues later' : 'Shift everything after the playhead later'} onClick={() => ripple(shiftBy)}>Shift ⇥</button>
              <button type="button" className="btn" title="Insert Δ seconds at the playhead" onClick={insertGap}>Insert time</button>
              <button type="button" className="btn" disabled={!loop} title="Delete the A–B range and close the gap" onClick={deleteLoopRange}>Delete A–B</button>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Toggle checked={showOnsets} onChange={setShowOnsets} label={`Onsets (${visibleOnsets.length})`} />
            {showOnsets && (
              <label className="flex items-center gap-1 text-xs text-mute">
                Threshold
                <input type="range" min={0} max={1} step={0.01} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} />
              </label>
            )}
            {!mobile && (
              <button type="button" className="btn" disabled={!onsets.length} onClick={() => setDialog({ kind: 'onsets' })}>Generate cues…</button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Toggle checked={audition} onChange={(v) => { setAuditionErr(null); setAudition(v) }} label="Live audition" />
            <button type="button" className="btn" disabled={proj.busy} onClick={() => proj.run(() => api.profiles.playFrom(pid, now()))}>
              Play on projector from here
            </button>
            {overlaps.size > 0 && <span className="text-sm text-pumpkin">⚠ {overlaps.size} overlapping cues</span>}
          </div>
          <ErrorText error={auditionErr} />
          <ErrorText error={proj.error} />
          <ErrorText error={tools.error} />
        </div>

        <aside className="w-full shrink-0 md:w-80">{panel}</aside>
      </div>

      <TimelineView
        duration={duration}
        cues={preview ? [...cues, ...preview] : cues}
        lanes={lanes}
        selected={selected}
        playhead={playhead}
        pxPerSec={pxPerSec}
        mediaId={mediaId}
        boards={boards}
        base={profile.base_state}
        overrides={profile.overrides}
        overlaps={overlaps}
        readOnly={mobile}
        snapEnabled={snapOn}
        onSeek={seek}
        onSelect={(k) => { setSelected(k); setSelMarkers(new Set()) }}
        onChange={onViewChange}
        onZoom={(p) => setPxPerSec(clamp(p, 5, 400))}
        onAddAt={(t, laneId) => { if (!mobile) addCue(t, laneTarget(laneId)) }}
        markers={doc.markers}
        selectedMarkers={selMarkers}
        onMarkerSelect={(ids) => { setSelMarkers(ids); setSelected(new Set()) }}
        onMarkersChange={onMarkersChange}
        onMarkerRename={renameMarker}
        onsets={visibleOnsets}
        snapExtra={snapExtra}
        loop={loop}
        fps={FPS}
        muted={muted}
        solo={solo}
        onToggleMute={toggler(setMuted)}
        onToggleSolo={toggler(setSolo)}
        expanded={expanded}
        onToggleExpand={toggler(setExpanded)}
        viewReq={viewReq}
      />

      {dialog?.kind === 'templates' && (
        <TemplateDialog effects={effects} saved={saved}
          onPreview={(g) => setPreview(g ? genToCues(g, now(), pt) : null)}
          onInsert={insertGenerated} onDeleteSaved={deleteSaved}
          onClose={() => { setPreview(null); setDialog(null) }} />
      )}
      {dialog?.kind === 'onsets' && (
        <GenerateDialog title="Generate cues from onsets" points={onsets} threshold={threshold}
          range={loop ?? { a: 0, b: duration }} lanes={laneOpts} defaultLane={lanesOf({ target: pt })[0] ?? ALL_LANE}
          onGenerate={generate} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'markers' && (
        <GenerateDialog title="Markers → cues" points={doc.markers.filter((m) => selMarkers.has(m.id))} threshold={null}
          range={{ a: 0, b: duration }} lanes={laneOpts} defaultLane={ALL_LANE}
          onGenerate={generate} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'remap' && (
        <RemapDialog sources={clipLanes(dialog.clip).map((id) => ({ id, label: boardName(id) }))} targets={laneOpts}
          onApply={(remap) => { addCues(fromClip(dialog.clip, dialog.at, remap)); setDialog(null) }}
          onClose={() => setDialog(null)} />
      )}
    </div>
  )
}
