import { type ChangeEvent, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { CueList } from '../components/CueList'
import { LightStateEditor } from '../components/LightStateEditor'
import { ErrorText, Section, Toggle } from '../components/ui'
import { fmtTime, useAction } from '../lib'
import { useAdoptedBoards, useLive } from '../live'
import type { DisplayProfile, LightState, Media, PlaybackMode, Profile, ProfileExport, ProfileIn } from '../types'

const modeLabel: Record<PlaybackMode, string> = { once: 'Play once', loop: 'Loop', idle_trigger: 'Idle + trigger' }

const emptyDraft = (): ProfileIn => ({
  name: '', type: 'light', selection: { all: true, macs: [] }, base_state: { on: true, bri: 255 },
  overrides: {}, media_id: null, idle_media_id: null, playback_mode: 'once', cooldown_s: 0, display_profile_id: null,
})

const toDraft = (p: Profile): ProfileIn => ({
  name: p.name, type: p.type, selection: p.selection, base_state: p.base_state, overrides: p.overrides,
  media_id: p.media_id, idle_media_id: p.idle_media_id, playback_mode: p.playback_mode, cooldown_s: p.cooldown_s,
  // PUT without it clears the per-show display profile.
  display_profile_id: p.display_profile_id ?? null,
})

export default function Profiles() {
  const { id } = useParams<{ id: string }>()
  return id ? <ProfileEditor key={id} id={id} /> : <ProfileList />
}

function ProfileList() {
  const live = useLive()
  const navigate = useNavigate()
  const [profiles, setProfiles] = useState<Profile[] | null>(null)
  const { busy, error, run } = useAction()
  const activeId = live.show?.profile?.id

  const load = useCallback(() => run(async () => setProfiles(await api.profiles.list())), [run])
  useEffect(() => { load() }, [live.profilesRev, load])

  const act = (fn: () => Promise<unknown>) => run(async () => { await fn(); await load() })

  const [pending, setPending] = useState<{ data: ProfileExport; message: string } | null>(null)
  const [importMedia, setImportMedia] = useState<Media[]>([])
  const [mediaId, setMediaId] = useState<number | ''>('')

  const onImportFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setPending(null)
    const p = await run(async () => {
      let data: ProfileExport
      try {
        data = JSON.parse(await file.text()) as ProfileExport
      } catch {
        throw new Error('Not a valid export file')
      }
      try {
        return await api.profiles.import(data)
      } catch (err) {
        if (err instanceof ApiError && err.status === 422 && data?.profile?.type === 'video') {
          setImportMedia(await api.media.list())
          setMediaId('')
          setPending({ data, message: err.message })
          return undefined
        }
        throw err
      }
    })
    if (p) navigate(`/profiles/${p.id}`)
  }

  const importWithMedia = async () => {
    if (!pending || mediaId === '') return
    const p = await run(() => api.profiles.import(pending.data, mediaId))
    if (p) navigate(`/profiles/${p.id}`)
  }

  return (
    <Section title="Profiles" actions={
      <>
        <label className={`btn cursor-pointer ${busy ? 'pointer-events-none opacity-40' : ''}`}>
          Import…
          <input type="file" accept=".json,application/json" hidden onChange={onImportFile} />
        </label>
        <button className="btn btn-primary" onClick={() => navigate('/profiles/new')}>New profile</button>
      </>
    }>
      <ErrorText error={error} />
      {pending && (
        <div className="card space-y-3">
          <p className="text-sm">{pending.message}</p>
          <div>
            <span className="label">Video</span>
            <select className="input" value={mediaId} onChange={(e) => setMediaId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">Select a video…</option>
              {importMedia.map((m) => <option key={m.id} value={m.id}>{m.filename}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary" disabled={busy || mediaId === ''} onClick={importWithMedia}>Import with this video</button>
            <button className="btn" onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      )}
      {profiles == null && <p className="text-sm text-mute">Loading…</p>}
      {profiles?.length === 0 && <p className="text-sm text-mute">No profiles yet.</p>}
      <div className="space-y-2">
        {profiles?.map((p) => (
          <div key={p.id} className={`card space-y-3 p-3 ${p.id === activeId ? 'border-pumpkin' : ''}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">{p.name}</span>
              {p.id === activeId && <span className="rounded bg-pumpkin px-1.5 py-0.5 text-xs font-semibold text-black">ACTIVE</span>}
              <span className={`rounded border px-1.5 py-0.5 text-xs ${p.type === 'video' ? 'border-ghost text-ghost' : 'border-slime text-slime'}`}>
                {p.type}
              </span>
            </div>
            <div className="text-sm text-mute">
              {p.type === 'video' ? modeLabel[p.playback_mode] : 'static'} · {p.cues.length} cue{p.cues.length === 1 ? '' : 's'}
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" disabled={busy || p.id === activeId} onClick={() => act(() => api.profiles.activate(p.id))}>Activate</button>
              <button className="btn" onClick={() => navigate(`/profiles/${p.id}`)}>Edit</button>
              {p.type === 'video' && <button className="btn" onClick={() => navigate(`/profiles/${p.id}/timeline`)}>Timeline</button>}
              <button className="btn" disabled={busy} onClick={() => act(() => api.profiles.duplicate(p.id))}>Duplicate</button>
              <a className="btn" href={api.profiles.exportUrl(p.id)} download>Export</a>
              <button className="btn btn-danger" disabled={busy}
                onClick={() => confirm(`Delete profile "${p.name}"?`) && act(() => api.profiles.remove(p.id))}>Delete</button>
            </div>
          </div>
        ))}
      </div>
    </Section>
  )
}

function ProfileEditor({ id }: { id: string }) {
  const isNew = id === 'new'
  const navigate = useNavigate()
  const live = useLive()
  const adopted = useAdoptedBoards()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [draft, setDraft] = useState<ProfileIn | null>(isNew ? emptyDraft() : null)
  const [media, setMedia] = useState<Media[]>([])
  const [displays, setDisplays] = useState<DisplayProfile[]>([])
  const [saved, setSaved] = useState(false)
  const load = useAction()
  const save = useAction()
  const loadRun = load.run

  useEffect(() => {
    if (isNew) return
    loadRun(async () => {
      const p = await api.profiles.get(Number(id))
      setProfile(p)
      setDraft(toDraft(p))
    })
  }, [id, isNew, loadRun])

  useEffect(() => { api.media.list().then(setMedia).catch(() => setMedia([])) }, [live.mediaRev])
  useEffect(() => { api.display.profiles().then(setDisplays).catch(() => setDisplays([])) }, [live.displayProfilesRev])

  const back = <Link to="/profiles" className="text-sm text-ghost hover:underline">← Back to profiles</Link>

  if (!draft) {
    return (
      <div className="space-y-3">
        {back}
        <ErrorText error={load.error} />
        {!load.error && <p className="text-sm text-mute">Loading…</p>}
      </div>
    )
  }

  const set = (patch: Partial<ProfileIn>) => { setSaved(false); setDraft({ ...draft, ...patch }) }
  const selected = draft.selection.all ? adopted : adopted.filter((b) => draft.selection.macs.includes(b.mac))

  const toggleMac = (mac: string, on: boolean) => set({
    selection: { ...draft.selection, macs: on ? [...draft.selection.macs, mac] : draft.selection.macs.filter((m) => m !== mac) },
  })

  const setOverride = (mac: string, s: LightState | null) => {
    const o = { ...draft.overrides }
    if (s) o[mac] = s
    else delete o[mac]
    set({ overrides: o })
  }

  const submit = async () => {
    const p = await save.run(() => (isNew ? api.profiles.create(draft) : api.profiles.update(Number(id), draft)))
    if (!p) return
    if (isNew) {
      navigate(`/profiles/${p.id}`, { replace: true })
      return
    }
    setProfile(p)
    setDraft(toDraft(p))
    setSaved(true)
  }

  const mediaLabel = (m: Media) => `${m.filename} (${fmtTime(m.duration_s)})`

  return (
    <div className="space-y-6">
      {back}
      <Section title={isNew ? 'New profile' : `Edit: ${profile?.name ?? ''}`}>
        <form className="card space-y-4" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <div>
            <span className="label">Name</span>
            <input className="input" required value={draft.name} onChange={(e) => set({ name: e.target.value })} />
          </div>

          <div>
            <span className="label">Type</span>
            <div className="flex gap-4">
              {(['light', 'video'] as const).map((t) => (
                <label key={t} className="flex items-center gap-2 text-sm">
                  <input type="radio" name="ptype" checked={draft.type === t} onChange={() => set({ type: t })} />
                  {t === 'light' ? 'Light' : 'Video'}
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <span className="label">Boards</span>
            <Toggle checked={draft.selection.all} label="All boards"
              onChange={(v) => set({ selection: { ...draft.selection, all: v } })} />
            {!draft.selection.all && (
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {adopted.length === 0 && <span className="text-sm text-mute">No adopted boards.</span>}
                {adopted.map((b) => (
                  <label key={b.mac} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={draft.selection.macs.includes(b.mac)} onChange={(e) => toggleMac(b.mac, e.target.checked)} />
                    {b.name}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div>
            <span className="label">Base state</span>
            <div className="rounded-lg border border-line bg-panel-2 p-3">
              <LightStateEditor boards={selected} value={draft.base_state} onChange={(s) => set({ base_state: s })} />
            </div>
          </div>

          {selected.length > 0 && (
            <div className="space-y-2">
              <span className="label">Per-board overrides</span>
              {selected.map((b) => {
                const ov = draft.overrides[b.mac]
                return (
                  <details key={b.mac} className="rounded-lg border border-line bg-panel-2 p-3">
                    <summary className="cursor-pointer text-sm">
                      Override for {b.name}{ov != null && <span className="ml-2 text-pumpkin">●</span>}
                    </summary>
                    <div className="mt-3 space-y-3">
                      <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={ov != null}
                          onChange={(e) => setOverride(b.mac, e.target.checked ? structuredClone(draft.base_state) : null)} />
                        use override
                      </label>
                      {ov != null && <LightStateEditor boards={[b]} value={ov} onChange={(s) => setOverride(b.mac, s)} />}
                    </div>
                  </details>
                )
              })}
            </div>
          )}

          {draft.type === 'video' && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <span className="label">Media</span>
                <select className="input" value={draft.media_id ?? ''} onChange={(e) => set({ media_id: e.target.value === '' ? null : Number(e.target.value) })}>
                  <option value="">(none)</option>
                  {media.map((m) => <option key={m.id} value={m.id}>{mediaLabel(m)}</option>)}
                </select>
              </div>
              <div>
                <span className="label">Idle video</span>
                <select className="input" value={draft.idle_media_id ?? ''} onChange={(e) => set({ idle_media_id: e.target.value === '' ? null : Number(e.target.value) })}>
                  <option value="">None (black)</option>
                  {media.map((m) => <option key={m.id} value={m.id}>{mediaLabel(m)}</option>)}
                </select>
              </div>
              <div>
                <span className="label">Display profile</span>
                <select className="input" value={draft.display_profile_id ?? ''} onChange={(e) => set({ display_profile_id: e.target.value === '' ? null : Number(e.target.value) })}>
                  <option value="">Default (video or global)</option>
                  {displays.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              <div>
                <span className="label">Playback mode</span>
                <select className="input" value={draft.playback_mode} onChange={(e) => set({ playback_mode: e.target.value as PlaybackMode })}>
                  {(Object.keys(modeLabel) as PlaybackMode[]).map((m) => <option key={m} value={m}>{modeLabel[m]}</option>)}
                </select>
              </div>
              {draft.playback_mode === 'idle_trigger' && (
                <div>
                  <span className="label">Cooldown (s)</span>
                  <input type="number" min={0} step={1} className="input" value={draft.cooldown_s}
                    onChange={(e) => set({ cooldown_s: Number(e.target.value) || 0 })} />
                </div>
              )}
            </div>
          )}

          <ErrorText error={save.error} />
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" className="btn btn-primary" disabled={save.busy}>{save.busy ? 'Saving…' : isNew ? 'Create' : 'Save'}</button>
            {!isNew && <a className="btn" href={api.profiles.exportUrl(Number(id))} download>Export</a>}
            {saved && <span className="text-sm text-slime">Saved</span>}
          </div>
        </form>
      </Section>

      {!isNew && profile && profile.type === 'video' && (
        <>
          <div className="card flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-mute">Edit cues visually on the timeline (desktop), or use the quick list below.</p>
            <button type="button" className="btn btn-primary" onClick={() => navigate(`/profiles/${profile.id}/timeline`)}>Open timeline editor</button>
          </div>
          <CueList profile={profile} boards={selected} onSaved={(p) => setProfile(p)} />
        </>
      )}
    </div>
  )
}
