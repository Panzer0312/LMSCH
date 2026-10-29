import { useEffect, useState, type ChangeEvent } from 'react'
import { api, ApiError } from '../api'
import { useLive } from '../live'
import type { Settings as SettingsT } from '../types'
import { ErrorText, Section, Toggle } from '../components/ui'
import { useAction } from '../lib'

const LOG_LEVELS = ['debug', 'info', 'warning', 'error']

interface Form {
  sync_offset_ms: string
  health_interval_s: string
  default_transition_ms: string
  log_level: string
  mpv_vo: string
  mpv_gpu_context: string
  brightness_cap: string
  volume_cap: string
}

const toForm = (s: SettingsT): Form => ({
  sync_offset_ms: String(s.sync_offset_ms),
  health_interval_s: String(s.health_interval_s),
  default_transition_ms: String(s.default_transition_ms),
  log_level: s.log_level,
  mpv_vo: s.mpv_vo ?? '',
  mpv_gpu_context: s.mpv_gpu_context ?? '',
  brightness_cap: String(Math.round(s.brightness_cap * 100)),
  volume_cap: String(s.volume_cap),
})

export default function Settings() {
  const live = useLive()
  const [draft, setDraft] = useState<Form | null>(null)
  const [saved, setSaved] = useState(false)
  const load = useAction()
  const save = useAction()
  const tp = useAction()
  const sec = useAction()
  const tok = useAction()
  const bak = useAction()
  const lock = useAction()
  const [pinEnabled, setPinEnabled] = useState<boolean | null>(null)
  const [pin, setPin] = useState('')
  const [token, setToken] = useState('')
  const [tokenSaved, setTokenSaved] = useState(false)
  const [restoreNotes, setRestoreNotes] = useState<string[] | null>(null)

  const { run: secRun } = sec
  const { run: tokRun } = tok
  useEffect(() => {
    void secRun(() => api.auth.status()).then((r) => {
      if (r) setPinEnabled(r.pin_enabled)
    })
    void tokRun(() => api.auth.getTriggerToken()).then((r) => {
      if (r) setToken(r.token ?? '')
    })
  }, [secRun, tokRun])

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
    const bcap = Number(draft.brightness_cap)
    const vcap = Number(draft.volume_cap)
    if (draft.brightness_cap.trim() === '' || !Number.isFinite(bcap) || bcap < 0 || bcap > 100)
      return save.setError('Brightness cap must be 0–100 %.')
    if (draft.volume_cap.trim() === '' || !Number.isFinite(vcap) || vcap < 0 || vcap > 130)
      return save.setError('Volume cap must be 0–130.')
    const body: Partial<SettingsT> = {
      sync_offset_ms: Math.round(sync),
      health_interval_s: health,
      default_transition_ms: Math.round(trans),
      log_level: draft.log_level,
      mpv_vo: draft.mpv_vo.trim() || null,
      mpv_gpu_context: draft.mpv_gpu_context.trim() || null,
      brightness_cap: bcap / 100,
      volume_cap: vcap,
    }
    const r = await save.run(() => api.settings.update(body))
    if (r) {
      setDraft(toForm(r))
      setSaved(true)
    }
  }

  const onLock = async (on: boolean) => {
    await lock.run(async () => {
      try {
        await api.showLock(on)
      } catch (e) {
        if (on || !(e instanceof ApiError) || e.status !== 401) throw e
        const p = window.prompt('PIN to turn off show mode')
        if (p == null) return
        await api.showLock(false, p)
      }
    })
  }

  const onSetPin = async () => {
    if (!pin.trim()) return sec.setError('Enter a PIN first.')
    const r = await sec.run(() => api.auth.setPin(pin.trim()))
    if (r) {
      setPinEnabled(r.pin_enabled)
      setPin('')
    }
  }

  const onRemovePin = async () => {
    if (!confirm('Remove the UI PIN? Anyone on the WLAN can then control Spookhub.')) return
    const r = await sec.run(() => api.auth.setPin(null))
    if (r) setPinEnabled(r.pin_enabled)
  }

  const onSaveToken = async () => {
    setTokenSaved(false)
    const r = await tok.run(() => api.auth.setTriggerToken(token.trim() || null))
    if (r) {
      setToken(r.token ?? '')
      setTokenSaved(true)
    }
  }

  const onGenerateToken = () => {
    const b = new Uint8Array(12)
    crypto.getRandomValues(b)
    setTokenSaved(false)
    setToken(Array.from(b, (x) => x.toString(16).padStart(2, '0')).join(''))
  }

  const onRestore = async (e: ChangeEvent<HTMLInputElement>) => {
    const input = e.currentTarget
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!confirm('Replace all profiles, cues and settings with this backup? Spookhub restarts.')) return
    const r = await bak.run(() => api.backup.restore(file))
    if (r) {
      setRestoreNotes(r.notes ?? [])
      window.setTimeout(() => location.reload(), 8000)
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
            <label className="label" htmlFor="bcap">Brightness cap (%)</label>
            <input id="bcap" className="input" type="number" inputMode="numeric" min={0} max={100} step={1}
              value={draft.brightness_cap} onChange={(e) => set('brightness_cap', e.target.value)} />
            <p className="mt-1 text-xs text-mute">0–100 %, max for master brightness</p>
          </div>
          <div>
            <label className="label" htmlFor="vcap">Volume cap</label>
            <input id="vcap" className="input" type="number" inputMode="numeric" min={0} max={130} step={1}
              value={draft.volume_cap} onChange={(e) => set('volume_cap', e.target.value)} />
            <p className="mt-1 text-xs text-mute">0–130, max for master volume</p>
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
      </Section>

      <Section title="Projector">
        <div className="card space-y-2">
          <Toggle checked={live.show?.test_pattern ?? false} label="Test pattern"
            onChange={(v) => void tp.run(() => api.show.testPattern(v))} />
          <p className="text-xs text-mute">Alignment grid for keystone/focus. Stops any running show.</p>
          <ErrorText error={tp.error} />
        </div>
      </Section>

      <Section title="Security">
        <div className="card space-y-2">
          <Toggle checked={live.settings?.show_lock ?? false} label="Show mode (lock editing)"
            onChange={(v) => void onLock(v)} />
          <p className="text-xs text-mute">Blocks edits to profiles, cues, boards, media and settings. Turning off needs the PIN if one is set.</p>
          <ErrorText error={lock.error} />
        </div>

        <div className="card space-y-3">
          <div>
            <label className="label" htmlFor="pin">UI PIN</label>
            <p className="mb-2 text-sm">
              {pinEnabled == null ? 'Checking…' : pinEnabled
                ? <span className="text-slime">PIN enabled</span>
                : <span className="text-pumpkin">No PIN set</span>}
            </p>
            <input id="pin" className="input" type="password" inputMode="numeric" autoComplete="new-password"
              value={pin} onChange={(e) => setPin(e.target.value)} />
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary" disabled={sec.busy || !pin.trim()} onClick={() => void onSetPin()}>Set PIN</button>
            <button className="btn btn-danger" disabled={sec.busy || !pinEnabled} onClick={() => void onRemovePin()}>Remove PIN</button>
          </div>
          <p className="text-xs text-mute">No PIN = anyone on the WLAN can control Spookhub.</p>
          <ErrorText error={sec.error} />
        </div>

        <div className="card space-y-3">
          <div>
            <label className="label" htmlFor="token">Trigger token</label>
            <input id="token" className="input font-mono" type="text" autoComplete="off" spellCheck={false}
              placeholder="empty = no token required"
              value={token} onChange={(e) => { setTokenSaved(false); setToken(e.target.value) }} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-primary" disabled={tok.busy} onClick={() => void onSaveToken()}>Save</button>
            <button className="btn" disabled={tok.busy} onClick={onGenerateToken}>Generate</button>
            {tokenSaved && <span className="text-sm text-slime">Saved</span>}
          </div>
          <ErrorText error={tok.error} />
          <div>
            <p className="mb-1 text-xs text-mute">Example usage:</p>
            <code className="block whitespace-pre-wrap break-all rounded-lg border border-line bg-bg p-2 text-xs">
              {`curl -X POST http://${location.host}/api/trigger -H "X-Trigger-Token: ${token.trim() || '<token>'}"`}
            </code>
            <p className="mt-1 text-xs text-mute">ESPHome/Arduino sketches: see docs/trigger.</p>
          </div>
        </div>
      </Section>

      <Section title="Backup">
        <div className="card space-y-3">
          <div className="space-y-1">
            <a className="btn" href={api.backup.downloadUrl} download>Download backup</a>
            <p className="text-xs text-mute">database + config, without media files</p>
          </div>
          <div>
            <label className="label" htmlFor="restore">Restore</label>
            <input id="restore" className="input" type="file" accept=".zip" disabled={bak.busy || restoreNotes != null}
              onChange={(e) => void onRestore(e)} />
          </div>
          {bak.busy && <p className="text-sm text-mute">Restoring…</p>}
          {restoreNotes && (
            <div className="space-y-1">
              {restoreNotes.length > 0 && (
                <ul className="list-disc pl-5 text-sm">
                  {restoreNotes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
              <p className="text-sm text-pumpkin">Restarting… the page reloads in 8 s</p>
            </div>
          )}
          <ErrorText error={bak.error} />
        </div>
      </Section>

      {live.version && (
        <p className="text-center text-xs text-mute">
          Version {live.version.commit}{live.version.installed_at ? ` · installed ${live.version.installed_at}` : ''}
        </p>
      )}
    </div>
  )
}
