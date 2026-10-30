import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAction } from '../lib'
import type { Media, Moment, MomentAmbience, MomentBusy, MomentIn, MomentVariant, Profile } from '../types'
import { ErrorText } from './ui'

const emptyVariant = (): MomentVariant => ({
  video_profile_id: null, sound: null, light_profile_id: null, ambience: 'keep', ambience_media_id: null, weight: 1,
})

const blank = (): MomentIn => ({
  name: '', icon: '🎃', color: '#ff7a1a', cooldown_s: 10, busy: 'ignore', order: 'shuffle', variants: [emptyVariant()],
})

const toIn = (m: Moment): MomentIn => ({
  name: m.name, icon: m.icon, color: m.color, cooldown_s: m.cooldown_s, busy: m.busy, order: m.order,
  variants: m.variants.map((v) => ({ ...emptyVariant(), ...v })),
})

const idOrNull = (v: string) => (v === '' ? null : Number(v))
const stem = (f: string) => f.replace(/\.[^.]+$/, '')

function validate(m: MomentIn): string | null {
  if (!m.name.trim()) return 'Name required'
  if (m.variants.length === 0) return 'Add at least one variant'
  const bad = m.variants.findIndex((v) => v.video_profile_id == null && v.sound == null && v.light_profile_id == null)
  if (bad >= 0) return `Variant ${bad + 1}: pick a video, sound or light`
  const noAmb = m.variants.findIndex((v) => v.ambience === 'replace' && v.ambience_media_id == null)
  if (noAmb >= 0) return `Variant ${noAmb + 1}: pick a replacement ambience`
  return null
}

function VariantRow({ v, index, videos, lights, sounds, weighted, canTest, onChange, onRemove, onTest }: {
  v: MomentVariant
  index: number
  videos: Profile[]
  lights: Profile[]
  sounds: Media[]
  weighted: boolean
  canTest: boolean
  onChange: (v: MomentVariant) => void
  onRemove: () => void
  onTest: () => void
}) {
  const set = (p: Partial<MomentVariant>) => onChange({ ...v, ...p })
  const snd = v.sound
  return (
    <div className="space-y-3 rounded-lg border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">Variant {index + 1}</span>
        <div className="flex gap-2">
          <button type="button" className="btn" disabled={!canTest} title={canTest ? '' : 'Save first'} onClick={onTest}>Test</button>
          <button type="button" className="btn" onClick={onRemove}>Remove</button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="label">Video</span>
          <select className="input" value={v.video_profile_id ?? ''} onChange={(e) => set({ video_profile_id: idOrNull(e.target.value) })}>
            <option value="">None</option>
            {videos.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="label">Light</span>
          <select className="input" value={v.light_profile_id ?? ''} onChange={(e) => set({ light_profile_id: idOrNull(e.target.value) })}>
            <option value="">None</option>
            {lights.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>
      <label className="block">
        <span className="label">Sound</span>
        <select
          className="input"
          value={snd?.media_id ?? ''}
          onChange={(e) => {
            const id = idOrNull(e.target.value)
            set({ sound: id == null ? null : { volume: 1, fade_in_ms: 0, fade_out_ms: 0, ...snd, media_id: id } })
          }}
        >
          <option value="">None</option>
          {sounds.map((m) => <option key={m.id} value={m.id}>{stem(m.filename)}</option>)}
        </select>
      </label>
      {snd && (
        <div className="grid grid-cols-3 gap-3">
          <label className="block">
            <span className="label">Vol {Math.round(snd.volume * 100)} %</span>
            <input
              type="range" min={0} max={1.3} step={0.05} value={snd.volume}
              className="h-10 w-full accent-pumpkin"
              onChange={(e) => set({ sound: { ...snd, volume: Number(e.target.value) } })}
            />
          </label>
          <label className="block">
            <span className="label">Fade in ms</span>
            <input
              type="number" min={0} step={50} className="input" value={snd.fade_in_ms}
              onChange={(e) => set({ sound: { ...snd, fade_in_ms: Math.max(0, Number(e.target.value) || 0) } })}
            />
          </label>
          <label className="block">
            <span className="label">Fade out ms</span>
            <input
              type="number" min={0} step={50} className="input" value={snd.fade_out_ms}
              onChange={(e) => set({ sound: { ...snd, fade_out_ms: Math.max(0, Number(e.target.value) || 0) } })}
            />
          </label>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="label">Ambience</span>
          <select
            className="input"
            value={v.ambience}
            onChange={(e) => {
              const a = e.target.value as MomentAmbience
              set({ ambience: a, ambience_media_id: a === 'replace' ? v.ambience_media_id ?? null : null })
            }}
          >
            <option value="keep">Keep</option>
            <option value="mute">Mute</option>
            <option value="replace">Replace</option>
          </select>
        </label>
        {v.ambience === 'replace' && (
          <label className="block">
            <span className="label">Replacement ambience</span>
            <select className="input" value={v.ambience_media_id ?? ''} onChange={(e) => set({ ambience_media_id: idOrNull(e.target.value) })}>
              <option value="">Pick…</option>
              {sounds.map((m) => <option key={m.id} value={m.id}>{stem(m.filename)}</option>)}
            </select>
          </label>
        )}
      </div>
      {weighted && (
        <label className="block">
          <span className="label">Weight</span>
          <input
            type="number" min={0.1} step={0.1} className="input" value={v.weight}
            onChange={(e) => set({ weight: Math.max(0.1, Number(e.target.value) || 0.1) })}
          />
        </label>
      )}
    </div>
  )
}

export function MomentEditor({ moment, onClose, onSaved }: {
  moment: Moment | null
  onClose: () => void
  onSaved: (m: Moment) => void
}) {
  const [draft, setDraft] = useState<MomentIn>(() => (moment ? toIn(moment) : blank()))
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [sounds, setSounds] = useState<Media[]>([])
  const [invalid, setInvalid] = useState<string | null>(null)
  const { busy, error, run } = useAction()
  const test = useAction()

  useEffect(() => {
    api.profiles.list().then(setProfiles, () => undefined)
    api.media.list().then((ms) => setSounds(ms.filter((m) => m.kind === 'audio')), () => undefined)
  }, [])

  const videos = profiles.filter((p) => p.type === 'video')
  const lights = profiles.filter((p) => p.type === 'light')
  const set = (p: Partial<MomentIn>) => setDraft((d) => ({ ...d, ...p }))
  const setVariant = (i: number, v: MomentVariant) => setDraft((d) => ({ ...d, variants: d.variants.map((x, j) => (j === i ? v : x)) }))
  // Test plays the saved variant by index, so any unsaved variant edit would hit the wrong one.
  const variantsDirty = moment == null || JSON.stringify(toIn(moment).variants) !== JSON.stringify(draft.variants)

  const save = async () => {
    const msg = validate(draft)
    setInvalid(msg)
    if (msg) return
    const body = { ...draft, name: draft.name.trim() }
    const r = await run(() => (moment ? api.moments.update(moment.id, body) : api.moments.create(body)))
    if (r) onSaved(r)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-2 sm:p-6" role="dialog" aria-modal="true">
      <div className="card w-full max-w-2xl space-y-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">{moment ? 'Edit moment' : 'New moment'}</h2>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="grid grid-cols-[4rem_1fr_4rem] gap-3">
          <label className="block">
            <span className="label">Icon</span>
            <input className="input text-center text-xl" value={draft.icon} maxLength={8} onChange={(e) => set({ icon: e.target.value })} />
          </label>
          <label className="block">
            <span className="label">Name</span>
            <input className="input" value={draft.name} onChange={(e) => set({ name: e.target.value })} />
          </label>
          <label className="block">
            <span className="label">Color</span>
            <input type="color" className="h-9 w-full cursor-pointer rounded-lg border border-line bg-bg" value={draft.color} onChange={(e) => set({ color: e.target.value })} />
          </label>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <label className="block">
            <span className="label">Cooldown s</span>
            <input
              type="number" min={0} step={1} className="input" value={draft.cooldown_s}
              onChange={(e) => set({ cooldown_s: Math.max(0, Number(e.target.value) || 0) })}
            />
          </label>
          <label className="block">
            <span className="label">When busy</span>
            <select className="input" value={draft.busy} onChange={(e) => set({ busy: e.target.value as MomentBusy })}>
              <option value="ignore">Ignore</option>
              <option value="interrupt">Interrupt</option>
              <option value="queue">Queue</option>
            </select>
          </label>
          <label className="block">
            <span className="label">Order</span>
            <select className="input" value={draft.order} onChange={(e) => set({ order: e.target.value as MomentIn['order'] })}>
              <option value="shuffle">Shuffle</option>
              <option value="weighted">Weighted</option>
            </select>
          </label>
        </div>
        <div className="space-y-3">
          {draft.variants.map((v, i) => (
            <VariantRow
              key={i}
              v={v}
              index={i}
              videos={videos}
              lights={lights}
              sounds={sounds}
              weighted={draft.order === 'weighted'}
              canTest={!variantsDirty && !test.busy}
              onChange={(nv) => setVariant(i, nv)}
              onRemove={() => setDraft((d) => ({ ...d, variants: d.variants.filter((_, j) => j !== i) }))}
              onTest={() => { if (moment) void test.run(() => api.moments.play(moment.id, i)) }}
            />
          ))}
          <button type="button" className="btn w-full" onClick={() => set({ variants: [...draft.variants, emptyVariant()] })}>
            + Add variant
          </button>
        </div>
        <ErrorText error={test.error} />
        <ErrorText error={invalid ?? error} />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>Save</button>
        </div>
      </div>
    </div>
  )
}
