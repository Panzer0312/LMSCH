import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAdoptedBoards, useLive } from '../live'
import type { AudioDevice, Board, DrmOutput, LastTrigger, PatternInfo, Recommendation } from '../types'
import { ErrorText } from '../components/ui'
import { SetupStep, type StepStatus } from '../components/SetupStep'
import { ago, useAction } from '../lib'
import { useDisplayState, useSetupSession } from '../video/hooks'

const LOCK_HINT = 'A show is playing or setup is busy. Stop the show to change this.'
const SHOW_LOCK_HINT = 'Show lock is on — turn it off in Settings or Show mode'

function useLockHint() {
  const { settings } = useLive()
  return settings?.show_lock ? SHOW_LOCK_HINT : LOCK_HINT
}

/** Debounced async sender: latest value wins after `ms` of quiet. */
function useDebounced<T>(fn: (v: T) => void, ms = 250) {
  const t = useRef<number | undefined>(undefined)
  const pending = useRef<{ v: T } | null>(null)
  const f = useRef(fn)
  useEffect(() => { f.current = fn }, [fn])
  useEffect(() => () => {
    window.clearTimeout(t.current)
    if (pending.current) { const { v } = pending.current; pending.current = null; f.current(v) }
  }, [])
  return useCallback((v: T) => {
    window.clearTimeout(t.current)
    pending.current = { v }
    t.current = window.setTimeout(() => { pending.current = null; f.current(v) }, ms)
  }, [ms])
}

function Slider({ label, value, min, max, step, unit, disabled, onChange }: {
  label: string; value: number; min: number; max: number; step: number; unit: string
  disabled?: boolean; onChange: (v: number) => void
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="flex justify-between gap-2">
        <span className="min-w-0 truncate">{label}</span>
        <span className="font-mono text-mute">{value}{unit}</span>
      </span>
      <input type="range" className="w-full accent-pumpkin" min={min} max={max} step={step}
        value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  )
}

/** Local value, immediate UI, debounced send. */
function LiveSlider(props: Omit<Parameters<typeof Slider>[0], 'onChange'> & { send: (v: number) => Promise<unknown> }) {
  const { send, value, ...rest } = props
  const [v, setV] = useState(value)
  const dragging = useRef(false)
  const valueRef = useRef(value)
  useEffect(() => {
    valueRef.current = value
    if (!dragging.current) setV(value)
  }, [value])
  const deb = useDebounced((x: number) => {
    void send(x).then(() => undefined, () => setV(valueRef.current))
      .finally(() => { dragging.current = false })
  })
  return <Slider {...rest} value={v} onChange={(x) => { dragging.current = true; setV(x); deb(x) }} />
}

/** Alignment patterns go through setup mode, so they show with the saved geometry applied. */
function PatternPicker() {
  const { settings, show } = useLive()
  const { display } = useDisplayState()
  const [patterns, setPatterns] = useState<PatternInfo[] | null>(null)
  const act = useAction()
  const session = useSetupSession()
  useEffect(() => { api.display.patterns().then(setPatterns, () => setPatterns([])) }, [])
  const cur = display?.setup && display.source?.kind === 'pattern' ? display.source.id : ''
  // Setup mode would stop the running show; make that a deliberate step on /video instead.
  const playing = show?.phase === 'playing' || show?.phase === 'paused'
  const blocked = !!settings?.show_lock || !!show?.blackout || playing
  const pick = (id: string) => void act.run(async () => { if (id) await session.enter({ kind: 'pattern', id }); else await session.leave() })
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input min-w-0 flex-1" aria-label="Alignment pattern" value={cur}
          disabled={blocked || act.busy || !patterns?.length} onChange={(e) => pick(e.target.value)}>
          <option value="">{display?.setup ? 'Leave pattern (back to idle)' : 'Alignment pattern…'}</option>
          {(patterns ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <Link className="btn" to="/video">Open video setup</Link>
      </div>
      {playing && <p className="text-xs text-mute">A show is playing. Stop it to show alignment patterns.</p>}
      <ErrorText error={act.error} />
    </div>
  )
}

function ProjectorStep({ locked }: { locked: boolean }) {
  const { settings, show } = useLive()
  const [outputs, setOutputs] = useState<DrmOutput[] | null>(null)
  const act = useAction()
  const lockHint = useLockHint()
  const [conn, setConn] = useState('')
  const [mode, setMode] = useState('')

  useEffect(() => {
    api.setup.outputs().then(setOutputs).catch((e) => act.setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    setConn(settings?.mpv_drm_connector ?? '')
    setMode(settings?.mpv_drm_mode ?? '')
  }, [settings?.mpv_drm_connector, settings?.mpv_drm_mode])

  const sorted = [...(outputs ?? [])].sort((a, b) => Number(b.status === 'connected') - Number(a.status === 'connected'))
  const cur = sorted.find((o) => o.name === conn)
  const dirty = conn !== (settings?.mpv_drm_connector ?? '') || mode !== (settings?.mpv_drm_mode ?? '')
  const status: StepStatus = !settings ? 'unknown' : settings.mpv_drm_connector ? (show?.player.ready ? 'ok' : 'warn') : 'warn'

  const apply = () => {
    if (!window.confirm('Applying restarts the video player. Continue?')) return
    void act.run(() => api.settings.update({ mpv_drm_connector: conn || null, mpv_drm_mode: mode || null }))
  }

  return (
    <SetupStep n={1} title="Projector output" status={status} hint={locked ? lockHint : null}>
      <div className="grid gap-2 sm:grid-cols-2">
        <select className="input" value={conn} disabled={locked || !outputs}
          onChange={(e) => { setConn(e.target.value); setMode('') }}>
          <option value="">Auto (config default)</option>
          {sorted.map((o) => <option key={o.name} value={o.name}>{o.name} ({o.status})</option>)}
        </select>
        <select className="input" value={mode} disabled={locked || !cur} onChange={(e) => setMode(e.target.value)}>
          <option value="">Auto / preferred</option>
          {(cur?.modes ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={locked || act.busy || !dirty} onClick={apply}>Apply (restarts player)</button>
        <button className="btn" disabled={locked && !show?.test_pattern} onClick={() => void act.run(() => api.show.testPattern(!show?.test_pattern))}>
          {show?.test_pattern ? 'Hide test pattern' : 'Show test pattern on this output'}
        </button>
      </div>
      <PatternPicker />
      <ErrorText error={act.error} />
    </SetupStep>
  )
}

// Raw ALSA ports allow one opener: video, effects, ambience and the jukebox can't share them.
const EXCLUSIVE_AUDIO = /^alsa\/(hdmi|hw|plughw|iec958|front):/

function AudioStep({ locked }: { locked: boolean }) {
  const { settings } = useLive()
  const [devs, setDevs] = useState<AudioDevice[] | null>(null)
  const act = useAction()
  const sound = useAction()
  const lockHint = useLockHint()

  const load = useCallback(() => {
    api.setup.audioDevices().then(setDevs).catch((e) => act.setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(load, [load])

  const put = (p: Parameters<typeof api.settings.update>[0]) => {
    act.setError(null)
    return api.settings.update(p).catch((e) => { act.setError(e instanceof Error ? e.message : String(e)); throw e })
  }
  const status: StepStatus = !settings ? 'unknown' : settings.mpv_audio_device ? 'ok' : 'warn'

  return (
    <SetupStep n={2} title="Audio output" status={status} hint={locked ? lockHint : null}>
      <select className="input" value={settings?.mpv_audio_device ?? ''} disabled={locked || !devs}
        onChange={(e) => void put({ mpv_audio_device: e.target.value || null }).catch(() => undefined)}>
        <option value="">Default</option>
        {(devs ?? []).map((d) => <option key={d.name} value={d.name}>{d.description || d.name}</option>)}
      </select>
      {settings?.mpv_audio_device && EXCLUSIVE_AUDIO.test(settings.mpv_audio_device) && (
        <p className="text-sm text-pumpkin">
          This output is exclusive: sound effects, ambience and the jukebox can't play on it at the same
          time. Pick the matching “Spookhub shared: …” output instead.
        </p>
      )}
      <p className="text-xs text-mute">The jukebox (Spotify) plays on this output too.</p>
      {settings && (
        <>
          <LiveSlider label="Volume" value={settings.master_volume} min={0} max={settings.volume_cap || 130} step={1} unit="%"
            disabled={locked} send={(v) => put({ master_volume: v })} />
          <LiveSlider label="Audio delay" value={settings.audio_delay_s} min={-1} max={1} step={0.01} unit=" s"
            disabled={locked} send={(v) => put({ audio_delay_s: v })} />
        </>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={locked || sound.busy} onClick={() => void sound.run(() => api.setup.testSound())}>Play test sound</button>
        <button className="btn" disabled={act.busy} onClick={load}>Reload devices</button>
      </div>
      <ErrorText error={act.error ?? sound.error} />
    </SetupStep>
  )
}

type Findings = Record<string, Recommendation[] | { error: string }>

function BoardsStep({ locked }: { locked: boolean }) {
  const adopted = useAdoptedBoards()
  const online = adopted.filter((b) => b.status === 'online')
  const [res, setRes] = useState<Findings | null>(null)
  const [busy, setBusy] = useState(false)
  const fix = useAction()
  const lockHint = useLockHint()

  const check = async (boards: Board[]) => {
    setBusy(true)
    const entries = await Promise.all(boards.map(async (b): Promise<[string, Findings[string]]> => {
      try { return [b.mac, await api.boards.recommendations(b.mac)] }
      catch (e) { return [b.mac, { error: e instanceof Error ? e.message : String(e) }] }
    }))
    setRes((p) => ({ ...(p ?? {}), ...Object.fromEntries(entries) }))
    setBusy(false)
  }

  const apply = async (b: Board, r: Recommendation) => {
    if (!window.confirm(`Write this setting to ${b.name}? The board may reconnect briefly.`)) return
    const ok = await fix.run(() => api.boards.applyRecommendation(b.mac, r.id))
    if (ok) await check([b])
  }

  const issues = res ? Object.values(res).some((r) => !Array.isArray(r) || r.some((x) => !x.ok)) : false
  const status: StepStatus = adopted.length === 0 ? 'fail'
    : online.length < adopted.length ? 'warn' : res ? (issues ? 'warn' : 'ok') : 'ok'

  return (
    <SetupStep n={3} title="Boards" status={status} hint={locked ? lockHint : null}>
      <p className="text-sm">{online.length} online / {adopted.length} adopted</p>
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={locked || busy || online.length === 0} onClick={() => void check(online)}>
          {busy ? 'Checking…' : 'Check all settings'}
        </button>
        <Link className="btn" to="/boards">Open Boards</Link>
      </div>
      {res && (
        <ul className="space-y-2 text-xs">
          {online.filter((b) => res[b.mac]).map((b) => {
            const r = res[b.mac]
            return (
              <li key={b.mac} className="rounded-lg border border-line p-2">
                <p className="font-medium">{b.name}</p>
                {!Array.isArray(r) ? <ErrorText error={r.error} /> : r.every((x) => x.ok) ? (
                  <p className="text-slime">All good</p>
                ) : r.filter((x) => !x.ok).map((x) => (
                  <div key={x.id} className="mt-1 flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-pumpkin">{x.title}</p>
                      {x.detail && <p className="text-mute">{x.detail}</p>}
                    </div>
                    {x.fixable && (
                      <button className="btn shrink-0 px-2 py-1 text-xs" disabled={locked || fix.busy} onClick={() => void apply(b, x)}>Apply</button>
                    )}
                  </div>
                ))}
              </li>
            )
          })}
        </ul>
      )}
      <ErrorText error={fix.error} />
    </SetupStep>
  )
}

function TriggerStep() {
  const { show } = useLive()
  const [token, setToken] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [last, setLast] = useState<LastTrigger | null>(null)
  const act = useAction()
  const test = useAction()
  const [, setTick] = useState(0)

  useEffect(() => {
    api.auth.getTriggerToken().then((r) => { setToken(r.token); setLoaded(true) }).catch((e) => act.setError(e instanceof Error ? e.message : String(e)))
    api.setup.triggers().then((r) => setLast(r.last_trigger)).catch(() => undefined)
    const t = window.setInterval(() => setTick((n) => n + 1), 5000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const lt = show?.last_trigger ?? last
  const fresh = lt != null && Date.now() / 1000 - lt.ts < 10
  const status: StepStatus = !loaded ? 'unknown' : !token ? 'warn' : lt ? 'ok' : 'warn'
  const origin = window.location.origin
  const tok = token ?? '<token>'
  const curl = `curl -X POST -H "X-Trigger-Token: ${tok}" ${origin}/api/trigger`
  const esp = [
    'http_request:',
    '',
    'binary_sensor:',
    '  - platform: gpio',
    '    pin: GPIO13',
    '    name: "PIR"',
    '    device_class: motion',
    '    on_press:',
    '      then:',
    '        - http_request.post:',
    `            url: ${origin}/api/trigger`,
    ...(token ? ['            request_headers:', `              X-Trigger-Token: ${token}`] : []),
  ].join('\n')
  const sendTest = async () => {
    const r = await fetch('/api/trigger', { method: 'POST', headers: token ? { 'X-Trigger-Token': token } : {} })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text().catch(() => '')).slice(0, 200)}`.trim())
  }

  const gen = () => {
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    const t = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    void act.run(async () => { const r = await api.auth.setTriggerToken(t); setToken(r.token) })
  }

  return (
    <SetupStep n={4} title="Trigger devices" status={status}>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-lg bg-bg px-2 py-1 text-xs">{token ?? (loaded ? 'no token set' : '…')}</code>
        <button className="btn" disabled={act.busy} onClick={() => {
          if (token && !window.confirm('Replace the token? Existing devices must be updated.')) return
          gen()
        }}>{token ? 'New token' : 'Create token'}</button>
      </div>
      <pre className="overflow-x-auto rounded-lg bg-bg p-2 text-xs">{curl}</pre>
      <pre className="overflow-x-auto rounded-lg bg-bg p-2 text-xs">{esp}</pre>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`inline-block size-3 rounded-full ${fresh ? 'bg-slime' : 'bg-line'}`} />
        <span className="text-mute">
          {lt ? `Last trigger ${ago(lt.ts)} from ${lt.source}${lt.accepted ? '' : ' (rejected)'}` : 'waiting for trigger…'}
        </span>
        <button className="btn ml-auto" disabled={test.busy} onClick={() => void test.run(sendTest)}>Send test trigger</button>
      </div>
      <ErrorText error={act.error ?? test.error} />
    </SetupStep>
  )
}

function CalibrationStep({ locked, playing }: { locked: boolean; playing: boolean }) {
  const { show, settings } = useLive()
  const boards = useAdoptedBoards()
  const act = useAction()
  const lockHint = useLockHint()
  const on = show?.calibrating ?? false
  const track = <T,>(p: Promise<T>) => {
    act.setError(null)
    return p.catch((e) => { act.setError(e instanceof Error ? e.message : String(e)); throw e })
  }
  const put = (v: number) => track(api.settings.update({ sync_offset_ms: v }))
  const setOff = (mac: string, v: number) => track(api.boards.setOffset(mac, v))

  return (
    <SetupStep n={5} title="Sync calibration" status={on ? 'warn' : 'unknown'}
      hint={!on && locked ? lockHint : null}>
      <p className="text-sm text-mute">Watch projector and LEDs. Move slider until flashes match.</p>
      <div className="flex flex-wrap gap-2">
        {on ? (
          <button className="btn btn-danger" disabled={act.busy} onClick={() => void act.run(() => api.setup.calibration(false))}>Stop calibration</button>
        ) : (
          <button className="btn btn-primary" disabled={act.busy || locked || playing} onClick={() => void act.run(() => api.setup.calibration(true))}>Start calibration</button>
        )}
      </div>
      {settings && (
        <LiveSlider label="Global offset" value={settings.sync_offset_ms} min={-500} max={500} step={5} unit=" ms"
          disabled={!on && locked} send={put} />
      )}
      {boards.map((b) => (
        <LiveSlider key={b.mac} label={b.name} value={b.offset_ms} min={-500} max={500} step={5} unit=" ms"
          disabled={!on && locked} send={(v) => setOff(b.mac, v)} />
      ))}
      <ErrorText error={act.error} />
    </SetupStep>
  )
}

export default function Setup() {
  const { show } = useLive()
  const playing = show?.phase === 'playing' || show?.phase === 'paused'
  const { settings } = useLive()
  const locked = playing || (show?.setup_busy ?? false) || !!show?.blackout || !!show?.test_pattern
    || !!show?.audition || !!settings?.show_lock

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Setup</h1>
      <ProjectorStep locked={locked} />
      <AudioStep locked={locked} />
      <BoardsStep locked={locked} />
      <TriggerStep />
      <CalibrationStep locked={locked} playing={playing} />
    </div>
  )
}
