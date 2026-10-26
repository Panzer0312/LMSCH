import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { ErrorText, Toggle } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import { useAdoptedBoards } from '../live'
import { CuePanel, MultiPanel } from '../timeline/CuePanel'
import { ALL_LANE, clamp, fromServer, newKey, overlapKeys, round3, toServer, useHistory, type Lane, type TLCue, isDragging } from '../timeline/model'
import { TimelineView } from '../timeline/TimelineView'
import type { CueTarget, Media, Profile } from '../types'

const FRAME = 1 / 30
const MOBILE_Q = '(max-width: 767px)'
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const laneTarget = (laneId: string): CueTarget => (laneId === ALL_LANE ? { all: true, macs: [] } : { all: false, macs: [laneId] })

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

function Editor({ profile, media, mediaId }: { profile: Profile; media: Media | null; mediaId: number }) {
  const pid = profile.id
  const adopted = useAdoptedBoards()
  const boards = profile.selection.all ? adopted : adopted.filter((b) => profile.selection.macs.includes(b.mac))
  const macsKey = boards.map((b) => b.mac).join(',')
  const lanes: Lane[] = [{ id: ALL_LANE, label: 'All selected' }, ...boards.map((b) => ({ id: b.mac, label: b.name, status: b.status }))]

  const [initial] = useState(() => fromServer(profile.cues))
  const { doc, set: setDoc, undo: undoDoc, redo: redoDoc, canUndo, canRedo } = useHistory(initial)
  const [rev, setRev] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [playhead, setPlayhead] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [videoDur, setVideoDur] = useState(0)
  const duration = media?.duration_s || videoDur
  const [pxPerSec, setPxPerSec] = useState(() => clamp(1100 / (media?.duration_s || 60), 5, 400))
  const [snapOn, setSnapOn] = useState(true)
  const [mobile, setMobile] = useState(() => window.matchMedia(MOBILE_Q).matches)
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlaps = useMemo(() => overlapKeys(doc, macsKey ? macsKey.split(',') : []), [doc, macsKey])
  const proj = useAction()

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_Q)
    const h = (e: MediaQueryListEvent) => setMobile(e.matches)
    mq.addEventListener('change', h)
    return () => mq.removeEventListener('change', h)
  }, [])

  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      const v = videoRef.current
      if (v) setPlayhead(v.currentTime)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  // --- autosave (only committed changes bump rev) ---
  const [savedRev, setSavedRev] = useState(0)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const saver = useRef({ doc: initial, pending: false })
  useEffect(() => { saver.current.doc = doc }, [doc])
  const save = useCallback((r: number) => {
    const s = saver.current
    s.pending = false
    api.profiles.setCues(pid, toServer(s.doc)).then(
      () => { setSaveErr(null); setSavedRev((x) => Math.max(x, r)) },
      (e) => { s.pending = true; setSaveErr(errMsg(e)) },
    )
  }, [pid])
  useEffect(() => {
    if (rev === 0) return
    saver.current.pending = true
    const t = setTimeout(() => save(rev), 800)
    return () => clearTimeout(t)
  }, [rev, save])
  useEffect(() => {
    const s = saver.current
    return () => { if (s.pending) api.profiles.setCues(pid, toServer(s.doc)).catch(() => {}) }
  }, [pid])

  // --- live audition ---
  const [audition, setAudition] = useState(false)
  const [auditionErr, setAuditionErr] = useState<string | null>(null)
  const sendAudition = useCallback((t: number, isPlaying: boolean) => {
    api.show.audition(pid, t, isPlaying).catch((e) => {
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

  // --- actions ---
  const now = () => videoRef.current?.currentTime ?? playhead
  const commit = (next: TLCue[]) => { setDoc(next); setRev((r) => r + 1) }
  const undo = () => { if (canUndo) { undoDoc(); setRev((r) => r + 1) } }
  const redo = () => { if (canRedo) { redoDoc(); setRev((r) => r + 1) } }
  const onViewChange = (cues: TLCue[], isCommit: boolean) => { if (isCommit) commit(cues); else setDoc(cues, true) }

  const seek = (t: number) => {
    const tt = duration > 0 ? clamp(t, 0, duration) : Math.max(0, t)
    const v = videoRef.current
    if (v) v.currentTime = tt
    setPlayhead(tt)
  }
  const togglePlay = () => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) v.play().catch(() => {})
    else v.pause()
  }
  const zoom = (f: number) => setPxPerSec((p) => clamp(p * f, 5, 400))

  const selCues = doc.filter((c) => selected.has(c.key))
  const addCue = (t: number, target: CueTarget) => {
    const c: TLCue = { key: newKey(), start_s: round3(Math.max(0, t)), duration_s: null, target, state: { on: true, bri: 255 }, transition_ms: null }
    commit([...doc, c])
    setSelected(new Set([c.key]))
  }
  const addAtPlayhead = () => {
    const one = selCues.length === 1 ? selCues[0] : null
    addCue(now(), one && !one.target.all && one.target.macs.length ? laneTarget(one.target.macs[0]) : laneTarget(ALL_LANE))
  }
  const deleteSel = () => {
    if (!selCues.length) return
    commit(doc.filter((c) => !selected.has(c.key)))
    setSelected(new Set())
  }
  const duplicateSel = () => {
    if (!selCues.length) return
    const copies = selCues.map((c) => ({ ...c, key: newKey(), start_s: round3(c.start_s + 0.5) }))
    commit([...doc, ...copies])
    setSelected(new Set(copies.map((c) => c.key)))
  }
  const nudgeSel = (d: number) => {
    if (!selCues.length) return
    commit(doc.map((c) => (selected.has(c.key) ? { ...c, start_s: round3(Math.max(0, c.start_s + d)) } : c)))
  }
  const updateCue = (next: TLCue) => commit(doc.map((c) => (c.key === next.key ? next : c)))

  // --- keyboard ---
  const onKey = (e: KeyboardEvent) => {
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
    if (k === 'Escape') { setSelected(new Set()); return }
    if (!mod && (k === '+' || k === '=')) { zoom(1.25); return }
    if (!mod && (k === '-' || k === '_')) { zoom(0.8); return }
    if (mobile) return
    const lk = k.toLowerCase()
    if (mod && lk === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return }
    if (mod && lk === 'y') { e.preventDefault(); redo(); return }
    if (mod && lk === 'd') { e.preventDefault(); duplicateSel(); return }
    if (mod) return
    if (lk === 'c') { addAtPlayhead(); return }
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
    <CuePanel key={selCues[0].key} cue={selCues[0]} boards={boards} lanes={lanes} readOnly={mobile}
      onChange={updateCue} onDelete={deleteSel} onDuplicate={duplicateSel} />
  ) : selCues.length > 1 ? (
    <MultiPanel count={selCues.length} readOnly={mobile} onDelete={deleteSel} onDuplicate={duplicateSel} onNudge={nudgeSel} />
  ) : (
    <div className="card text-sm text-mute">
      {mobile ? 'Tap a cue to see its details. Editing works on desktop.' : 'Select a cue to edit it. Double-click a lane or press C to add one.'}
    </div>
  )

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
            <span className="font-mono text-sm tabular-nums">{fmtTime(playhead)} / {fmtTime(duration)}</span>
            <div className="flex gap-1">
              <button type="button" className="btn" title="Zoom out (-)" onClick={() => zoom(0.8)}>−</button>
              <button type="button" className="btn" title="Zoom in (+)" onClick={() => zoom(1.25)}>+</button>
            </div>
            <Toggle checked={snapOn} onChange={setSnapOn} label="Snap" />
            {!mobile && (
              <>
                <button type="button" className="btn" title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={undo}>Undo</button>
                <button type="button" className="btn" title="Redo (Ctrl+Shift+Z)" disabled={!canRedo} onClick={redo}>Redo</button>
                <button type="button" className="btn" title="C" onClick={addAtPlayhead}>Add cue at playhead</button>
              </>
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
        </div>

        <aside className="w-full shrink-0 md:w-80">{panel}</aside>
      </div>

      <TimelineView
        duration={duration}
        cues={doc}
        lanes={lanes}
        selected={selected}
        playhead={playhead}
        pxPerSec={pxPerSec}
        mediaId={mediaId}
        overlaps={overlaps}
        readOnly={mobile}
        snapEnabled={snapOn}
        onSeek={seek}
        onSelect={setSelected}
        onChange={onViewChange}
        onZoom={(p) => setPxPerSec(clamp(p, 5, 400))}
        onAddAt={(t, laneId) => { if (!mobile) addCue(t, laneTarget(laneId)) }}
      />
    </div>
  )
}
