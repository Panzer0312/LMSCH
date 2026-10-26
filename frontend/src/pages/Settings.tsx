import { useEffect, useState } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { Settings as SettingsT } from '../types'
import { ErrorText, Section } from '../components/ui'
import { useAction } from '../lib'

const LOG_LEVELS = ['debug', 'info', 'warning', 'error']

interface Form {
  sync_offset_ms: string
  health_interval_s: string
  default_transition_ms: string
  log_level: string
  mpv_vo: string
  mpv_gpu_context: string
}

const toForm = (s: SettingsT): Form => ({
  sync_offset_ms: String(s.sync_offset_ms),
  health_interval_s: String(s.health_interval_s),
  default_transition_ms: String(s.default_transition_ms),
  log_level: s.log_level,
  mpv_vo: s.mpv_vo ?? '',
  mpv_gpu_context: s.mpv_gpu_context ?? '',
})

export default function Settings() {
  const live = useLive()
  const [draft, setDraft] = useState<Form | null>(null)
  const [saved, setSaved] = useState(false)
  const load = useAction()
  const save = useAction()

  const [hadLiveSettings] = useState(() => live.settings != null)

  if (!draft && live.settings) setDraft(toForm(live.settings))

  const { run: loadRun } = load
  useEffect(() => {
    if (hadLiveSettings) return
    void loadRun(() => api.settings.get()).then((s) => {
      if (s) setDraft((d) => d ?? toForm(s))
    })
  }, [loadRun, hadLiveSettings])

  useEffect(() => {
    if (!saved) return
    const t = window.setTimeout(() => setSaved(false), 2500)
    return () => window.clearTimeout(t)
  }, [saved])

  if (!draft) {
    return (
      <Section title="Settings">
        <p className="text-sm text-mute">Loading…</p>
        <ErrorText error={load.error} />
      </Section>
    )
  }

  const set = (k: keyof Form, v: string) => {
    setSaved(false)
    setDraft({ ...draft, [k]: v })
  }

  const onSave = async () => {
    const sync = Number(draft.sync_offset_ms)
    const health = Number(draft.health_interval_s)
    const trans = Number(draft.default_transition_ms)
    if (draft.sync_offset_ms.trim() === '' || !Number.isFinite(sync)) return save.setError('Sync offset must be a number.')
    if (!Number.isFinite(health) || health < 0.5 || health > 60) return save.setError('Health interval must be 0.5–60 s.')
    if (draft.default_transition_ms.trim() === '' || !Number.isFinite(trans) || trans < 0)
      return save.setError('Default transition must be a non-negative number.')
    const body: Partial<SettingsT> = {
      sync_offset_ms: Math.round(sync),
      health_interval_s: health,
      default_transition_ms: Math.round(trans),
      log_level: draft.log_level,
      mpv_vo: draft.mpv_vo.trim() || null,
      mpv_gpu_context: draft.mpv_gpu_context.trim() || null,
    }
    const r = await save.run(() => api.settings.update(body))
    if (r) {
      setDraft(toForm(r))
      setSaved(true)
    }
  }

  return (
    <div className="space-y-6">
      <Section title="Settings">
        <div className="card space-y-4">
          <div>
            <label className="label" htmlFor="sync">Global sync offset (ms)</label>
            <input id="sync" className="input" type="number" inputMode="numeric" step={1}
              value={draft.sync_offset_ms} onChange={(e) => set('sync_offset_ms', e.target.value)} />
            <p className="mt-1 text-xs text-mute">positive = lights fire earlier</p>
          </div>
          <div>
            <label className="label" htmlFor="health">Health interval (s)</label>
            <input id="health" className="input" type="number" inputMode="decimal" min={0.5} max={60} step={0.5}
              value={draft.health_interval_s} onChange={(e) => set('health_interval_s', e.target.value)} />
            <p className="mt-1 text-xs text-mute">0.5–60 s between board polls</p>
          </div>
          <div>
            <label className="label" htmlFor="trans">Default transition (ms)</label>
            <input id="trans" className="input" type="number" inputMode="numeric" min={0} step={1}
              value={draft.default_transition_ms} onChange={(e) => set('default_transition_ms', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="log">Log level</label>
            <select id="log" className="input" value={draft.log_level} onChange={(e) => set('log_level', e.target.value)}>
              {LOG_LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        </div>

        <div className="card space-y-4">
          <p className="text-sm text-pumpkin">Changing these restarts the player.</p>
          <div>
            <label className="label" htmlFor="vo">mpv video output (vo)</label>
            <input id="vo" className="input" type="text" placeholder="gpu (config default)"
              value={draft.mpv_vo} onChange={(e) => set('mpv_vo', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="gpuctx">mpv GPU context</label>
            <input id="gpuctx" className="input" type="text" placeholder="drm"
              value={draft.mpv_gpu_context} onChange={(e) => set('mpv_gpu_context', e.target.value)} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-primary" disabled={save.busy} onClick={() => void onSave()}>
            {save.busy ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="text-sm text-slime">Saved</span>}
        </div>
        <ErrorText error={save.error} />

        <p className="text-xs text-mute">UI PIN, test pattern, backups arrive in a later version.</p>
      </Section>
    </div>
  )
}
