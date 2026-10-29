import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { DisplayGeometry, DisplayProfile, DisplaySource, Media, PatternInfo, Profile } from '../types'
import { ErrorText } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import { Controls } from '../video/Controls'
import { ProfileBar } from '../video/ProfileBar'
import { Preview, type PreviewSource } from '../video/Preview'
import { useDisplayState, useSerialSender, useSetupSession, useThrottled } from '../video/hooks'
import { IDENTITY, needsWarp, normalize, sameGeometry } from '../video/geometry'

const DEFAULT_SOURCE: DisplaySource = { kind: 'pattern', id: 'grid' }

const srcKey = (s: DisplaySource | null) =>
  !s ? '' : s.kind === 'pattern' ? `pattern:${s.id}` : s.kind === 'media' ? `media:${s.id}` : 'idle'
const parseKey = (k: string): DisplaySource | null => {
  if (k === 'idle') return { kind: 'idle' }
  if (k.startsWith('pattern:')) return { kind: 'pattern', id: k.slice(8) }
  if (k.startsWith('media:')) return { kind: 'media', id: Number(k.slice(6)) }
  return null
}

export default function VideoSetup() {
  const { settings, show, mediaRev, displayProfilesRev } = useLive()
  const { display, error: stateError } = useDisplayState()
  const [profiles, setProfiles] = useState<DisplayProfile[] | null>(null)
  const [patterns, setPatterns] = useState<PatternInfo[]>([])
  const [media, setMedia] = useState<Media[]>([])
  const [showProfile, setShowProfile] = useState<Profile | null>(null)
  /** undefined = follow the profile the hub resolved for the current source. */
  const [pickedId, setPickedId] = useState<number | null | undefined>(undefined)
  const [edit, setEdit] = useState<DisplayGeometry | null>(null)
  const [ab, setAb] = useState<'A' | 'B'>('B')
  const [scrubT, setScrubT] = useState<number | null>(null)
  const [previewOpen, setPreviewOpen] = useState(true)
  const act = useAction()
  const live = useAction()
  const session = useSetupSession()

  const loadProfiles = useCallback(async () => setProfiles(await api.display.profiles()), [])
  const { run } = act
  useEffect(() => { api.display.patterns().then(setPatterns, () => undefined) }, [])
  useEffect(() => { void run(loadProfiles) }, [run, loadProfiles, displayProfilesRev])
  // Deleting a display profile clears media.display_profile_id, so refetch media on that too.
  useEffect(() => { api.media.list().then(setMedia, () => undefined) }, [mediaRev, displayProfilesRev])
  const activeShowId = settings?.active_profile_id ?? null
  useEffect(() => {
    if (activeShowId == null) return
    let alive = true
    api.profiles.get(activeShowId).then((p) => { if (alive) setShowProfile(p) }, () => undefined)
    return () => { alive = false }
  }, [activeShowId])

  const idleProfile = activeShowId != null && showProfile?.id === activeShowId ? showProfile : null
  const setupOn = !!display?.setup
  const source = display?.source ?? null
  const defaultId = settings?.display_profile_id ?? null
  const effectiveId = pickedId !== undefined ? pickedId : (display?.resolved_profile_id ?? defaultId)
  const selected = profiles?.find((p) => p.id === effectiveId) ?? null
  const saved = useMemo(() => (selected ? normalize(selected.geometry) : IDENTITY), [selected])
  const edited = edit ?? saved
  const dirty = edit !== null && !sameGeometry(edit, saved)
  const shown = ab === 'A' && dirty ? saved : edited

  const { setError: setLiveError } = live
  const postLive = useSerialSender((g: DisplayGeometry) =>
    api.display.live(g).then(() => setLiveError(null), (e) => setLiveError(e instanceof Error ? e.message : String(e))))
  const sendLive = useThrottled(postLive, 30)
  useEffect(() => { if (setupOn) sendLive(shown) }, [setupOn, shown, sendLive])

  const sendSeek = useThrottled((t: number) => { void api.display.seek(t).catch(() => undefined) }, 120)

  const change = (g: DisplayGeometry) => {
    if (pickedId === undefined) setPickedId(effectiveId)
    setEdit(g)
    setAb('B')
  }

  const mediaSrc = source?.kind === 'media' ? media.find((m) => m.id === source.id) ?? null
    : source?.kind === 'idle' && idleProfile?.idle_media_id != null ? media.find((m) => m.id === idleProfile.idle_media_id) ?? null
      : null
  const preview: PreviewSource = !source ? { kind: 'pattern', id: 'grid' }
    : source.kind === 'pattern' ? { kind: 'pattern', id: source.id }
      : mediaSrc ? { kind: 'video', url: api.media.fileUrl(mediaSrc.id), w: mediaSrc.width, h: mediaSrc.height } : null

  const enter = (src: DisplaySource) => act.run(async () => {
    if (await session.enter(src)) setScrubT(null)
  })
  const leave = () => act.run(session.leave)

  const select = (id: number | null) => {
    if (dirty && !window.confirm('Discard unsaved changes?')) return
    setPickedId(id)
    setEdit(null)
    setAb('B')
  }
  const save = () => act.run(async () => {
    if (!selected) return
    await api.display.updateProfile(selected.id, { geometry: edited })
    await loadProfiles()
    setEdit(null)
  })
  const saveAs = () => {
    const name = window.prompt('Name for the new display profile', selected ? `${selected.name} copy` : 'Projector setup')?.trim()
    if (!name) return
    void act.run(async () => {
      const p = await api.display.createProfile(name, edited)
      await loadProfiles()
      setPickedId(p.id)
      setEdit(null)
    })
  }
  const rename = () => {
    if (!selected) return
    const name = window.prompt('Rename display profile', selected.name)?.trim()
    if (!name || name === selected.name) return
    void act.run(async () => { await api.display.updateProfile(selected.id, { name }); await loadProfiles() })
  }
  const remove = () => {
    if (!selected || !window.confirm(`Delete display profile "${selected.name}"? Videos and shows using it fall back to the default.`)) return
    void act.run(async () => {
      await api.display.deleteProfile(selected.id)
      await loadProfiles()
      setPickedId(undefined)
      setEdit(null)
    })
  }
  const setDefault = () => { if (selected) void act.run(() => api.settings.update({ display_profile_id: selected.id })) }
  // Pinning needs an explicit pick: the inherited/resolved profile may just be the default.
  const pinId = pickedId ?? null
  const useForMedia = (on: boolean) => {
    if (!mediaSrc || (on && pinId === null)) return
    void act.run(async () => {
      const m = await api.media.setDisplayProfile(mediaSrc.id, on ? pinId : null)
      setMedia((l) => l.map((x) => (x.id === m.id ? { ...x, display_profile_id: m.display_profile_id } : x)))
    })
  }

  const locked = !!settings?.show_lock
  const blackout = !!show?.blackout
  const duration = mediaSrc?.duration_s ?? 0
  const scrubbable = setupOn && source?.kind === 'media' && duration > 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">Video setup</h1>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${setupOn ? 'bg-slime/20 text-slime' : 'bg-line text-mute'}`}>
          {setupOn ? 'setup mode on' : 'setup mode off'}
        </span>
        <div className="ml-auto flex gap-2">
          {setupOn ? (
            <button className="btn btn-danger" disabled={act.busy} onClick={() => void leave()}>Leave setup mode</button>
          ) : (
            <button className="btn btn-primary" disabled={act.busy || locked || blackout}
              title={blackout ? 'Blackout is on' : locked ? 'Show mode is on' : 'Stops any running show'}
              onClick={() => void enter(DEFAULT_SOURCE)}>Start setup mode</button>
          )}
        </div>
      </div>
      {locked && <p className="text-sm text-pumpkin">Show mode is on. Turn it off in Settings to adjust the projector.</p>}
      {!setupOn && !locked && <p className="text-sm text-mute">Starting setup mode stops any show. Leaving restores the idle state. Edits and saving work without it, but only setup mode shows them live.</p>}
      <ErrorText error={act.error ?? live.error ?? stateError} />
      {display && !display.warp_supported && needsWarp(edited) && (
        <p className="rounded-lg border border-pumpkin/50 bg-pumpkin/10 p-2 text-sm text-pumpkin">
          Keystone, mask and flip need mpv vo = gpu-next (Settings). The projector ignores them right now.
        </p>
      )}

      <div className="card space-y-2">
        <label className="label" htmlFor="vs-source">Source on projector</label>
        <select id="vs-source" className="input" value={srcKey(source)} disabled={act.busy || locked || blackout}
          onChange={(e) => { const s = parseKey(e.target.value); if (s) void enter(s) }}>
          {!source && <option value="">(choose to start setup mode)</option>}
          <optgroup label="Patterns">
            {patterns.map((p) => <option key={p.id} value={`pattern:${p.id}`}>{p.label}{p.kind === 'video' ? ' (moving)' : ''}</option>)}
          </optgroup>
          <optgroup label="Show">
            <option value="idle">Active profile idle{idleProfile ? ` (${idleProfile.name})` : ''}</option>
          </optgroup>
          <optgroup label="Media">
            {media.map((m) => <option key={m.id} value={`media:${m.id}`}>{m.filename}</option>)}
          </optgroup>
        </select>
        {scrubbable && (
          <label className="block space-y-1 text-sm">
            <span className="flex justify-between"><span>Scrub</span><span className="font-mono text-mute">{fmtTime(scrubT ?? 0)} / {fmtTime(duration)}</span></span>
            <input type="range" className="w-full accent-pumpkin" min={0} max={duration} step={0.1} value={scrubT ?? 0}
              onChange={(e) => { const t = Number(e.target.value); setScrubT(t); sendSeek(t) }} />
          </label>
        )}
      </div>

      <ProfileBar profiles={profiles ?? []} selectedId={effectiveId} defaultId={defaultId} dirty={dirty} busy={act.busy}
        mediaName={mediaSrc?.filename ?? null}
        mediaUsesSelected={mediaSrc != null && mediaSrc.display_profile_id != null && mediaSrc.display_profile_id === effectiveId}
        canPin={pinId !== null}
        ab={ab} onSelect={select} onSave={() => void save()} onSaveAs={saveAs} onRename={rename} onDelete={remove}
        onSetDefault={setDefault} onUseForMedia={useForMedia} onAb={setAb} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="space-y-2 lg:sticky lg:top-28 lg:self-start">
          <button type="button" className="btn w-full sm:hidden" onClick={() => setPreviewOpen((o) => !o)}>
            {previewOpen ? 'Hide preview' : 'Show preview'}
          </button>
          <div className={`${previewOpen ? '' : 'hidden'} space-y-1 sm:block`}>
            <Preview geometry={shown} editCorners={edited.corners} source={preview} scrubT={scrubT}
              onCorners={(corners) => change({ ...edited, corners })} />
            <p className="text-xs text-mute">Browser mimic. Orange = output bounds; the projector is the truth.{ab === 'A' && dirty ? ' Showing A (saved).' : ''}</p>
          </div>
        </div>
        <Controls g={edited} onChange={change} />
      </div>
    </div>
  )
}
