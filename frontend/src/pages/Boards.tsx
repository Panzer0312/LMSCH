import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import { useGroups, useLive } from '../live'
import type { Board, BoardGroup, BoardStatus, FallbackResult, Recommendation } from '../types'
import { ErrorText, Section, SignalBars, StatusDot } from '../components/ui'
import { ago, useAction } from '../lib'

const statusText: Record<BoardStatus, string> = {
  online: 'text-slime', degraded: 'text-pumpkin', offline: 'text-blood', unknown: 'text-mute',
}

type CheckResult = Recommendation[] | 'loading' | { error: string }

function fmtMac(mac: string): string {
  const hex = mac.toLowerCase().replace(/[^0-9a-f]/g, '')
  return hex.length === 12 ? (hex.match(/.{2}/g) ?? []).join(':') : mac
}

function fmtErr(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return `${e.message} (refused: show playing, blackout or setup busy)`
  }
  if (e instanceof ApiError && e.status === 423) return 'Show lock is on'
  return e instanceof Error ? e.message : String(e)
}

type RoomFilter = 'all' | 'none' | number
const FILTER_KEY = 'spookhub.boards.room'

function loadFilter(): RoomFilter {
  try {
    const v = localStorage.getItem(FILTER_KEY)
    if (v === 'all' || v === 'none') return v
    const n = Number(v)
    return v && Number.isInteger(n) ? n : 'all'
  } catch {
    return 'all'
  }
}

// Hex + alpha suffix only works for #rrggbb; anything else gets no tint rather than a broken style.
function tint(color: string, alpha: string): string | undefined {
  return /^#[0-9a-f]{6}$/i.test(color) ? `${color}${alpha}` : undefined
}

function GroupRow({ group, ids, index }: { group: BoardGroup; ids: number[]; index: number }) {
  const act = useAction()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(group.name)
  const [color, setColor] = useState(group.color)
  // Groups have no live brightness of their own, so the slider starts mid-range.
  const [bri, setBri] = useState(128)

  const doSave = async (e: FormEvent) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    const r = await act.run(() => api.groups.update(group.id, { name: n, color, macs: group.macs }))
    if (r) setEditing(false)
  }

  const move = (dir: -1 | 1) => {
    const next = [...ids]
    const j = index + dir
    if (j < 0 || j >= next.length) return
    ;[next[index], next[j]] = [next[j], next[index]]
    void act.run(() => api.groups.reorder(next))
  }

  const doRemove = () => {
    if (!window.confirm(`Delete room "${group.name}"? Boards stay, only the room goes.`)) return
    void act.run(() => api.groups.remove(group.id))
  }

  const commitBri = (v: number) => act.run(() => api.groups.setState(group.id, { bri: v }))

  return (
    <li className="card flex min-w-0 flex-col gap-3">
      {editing ? (
        <form onSubmit={doSave} className="flex flex-wrap gap-2">
          <input
            type="color"
            className="h-10 w-12 shrink-0 cursor-pointer rounded border border-line bg-transparent"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            aria-label="Room color"
          />
          <input
            className="input min-w-0 flex-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Room name"
            autoFocus
          />
          <button type="submit" className="btn btn-primary" disabled={act.busy || !name.trim()}>Save</button>
          <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <span className="inline-block size-4 shrink-0 rounded border border-line" style={{ background: group.color }} />
          <span className="min-w-0 flex-1 truncate font-semibold">{group.name}</span>
          <span className="text-xs text-mute">{group.macs.length} board{group.macs.length === 1 ? '' : 's'}</span>
          <button className="btn min-h-10 px-2" disabled={act.busy || index === 0} aria-label={`Move ${group.name} up`} onClick={() => move(-1)}>↑</button>
          <button className="btn min-h-10 px-2" disabled={act.busy || index === ids.length - 1} aria-label={`Move ${group.name} down`} onClick={() => move(1)}>↓</button>
        </div>
      )}

      <div className="flex items-center gap-3">
        <input
          type="range"
          min={0}
          max={255}
          value={bri}
          aria-label={`${group.name} brightness`}
          className="min-w-0 flex-1 accent-pumpkin"
          onChange={(e) => setBri(Number(e.currentTarget.value))}
          onPointerUp={(e) => void commitBri(Number(e.currentTarget.value))}
          onKeyUp={(e) => void commitBri(Number(e.currentTarget.value))}
        />
        <span className="w-8 text-right text-xs tabular-nums text-mute">{bri}</span>
      </div>

      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.groups.setState(group.id, { on: true }))}>On</button>
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.groups.setState(group.id, { on: false }))}>Off</button>
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.groups.identify(group.id))}>Identify</button>
        <button className="btn" disabled={act.busy} title="Drop the manual override, back to show/profile" onClick={() => act.run(() => api.groups.clearState(group.id))}>Auto</button>
        {!editing && (
          <button className="btn" onClick={() => { setName(group.name); setColor(group.color); setEditing(true) }}>Edit</button>
        )}
        <button className="btn btn-danger" disabled={act.busy} onClick={doRemove}>Delete</button>
      </div>
      <ErrorText error={act.error} />
    </li>
  )
}

function Rooms() {
  const groups = useGroups()
  const act = useAction()
  const [name, setName] = useState('')
  const [color, setColor] = useState('#7c3aed')
  const ids = groups.map((g) => g.id)

  const doCreate = async (e: FormEvent) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    const r = await act.run(() => api.groups.create({ name: n, color, macs: [] }))
    if (r) setName('')
  }

  return (
    <Section title={`Rooms (${groups.length})`}>
      <form onSubmit={doCreate} className="flex flex-wrap gap-2">
        <input
          type="color"
          className="h-10 w-12 shrink-0 cursor-pointer rounded border border-line bg-transparent"
          value={color}
          onChange={(e) => setColor(e.target.value)}
          aria-label="New room color"
        />
        <input
          className="input min-w-0 flex-1 sm:w-44 sm:flex-none"
          placeholder="Living room"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="New room name"
        />
        <button type="submit" className="btn shrink-0" disabled={act.busy || !name.trim()}>
          {act.busy ? 'Adding…' : 'Add room'}
        </button>
      </form>
      <ErrorText error={act.error} />
      {groups.length > 0 && (
        <>
          <p className="text-xs text-mute">Later rooms win when a board is in two rooms with different overrides.</p>
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {groups.map((g, i) => <GroupRow key={g.id} group={g} ids={ids} index={i} />)}
          </ul>
        </>
      )}
    </Section>
  )
}

function GroupChips({ board }: { board: Board }) {
  const groups = useGroups()
  const act = useAction()
  if (groups.length === 0) return null

  const toggle = (g: BoardGroup) => {
    // Per-member calls: a full macs list from props would undo a parallel tap on another board.
    const member = g.macs.includes(board.mac)
    void act.run(() => (member ? api.groups.removeMember(g.id, board.mac) : api.groups.addMember(g.id, board.mac)))
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-2">
        {groups.map((g) => {
          const member = g.macs.includes(board.mac)
          return (
            <button
              key={g.id}
              className={`min-h-10 rounded-full border px-3 text-xs ${member ? 'font-semibold' : 'border-line text-mute'}`}
              style={member ? { borderColor: g.color, background: tint(g.color, '33') } : undefined}
              aria-pressed={member}
              disabled={act.busy}
              onClick={() => toggle(g)}
            >
              {g.name}
            </button>
          )
        })}
      </div>
      <ErrorText error={act.error} />
    </div>
  )
}

function ResultList({ results }: { results: FallbackResult[] }) {
  return (
    <ul className="space-y-1 rounded-lg border border-line p-2 text-xs">
      {results.map((r) => (
        <li key={r.mac} className="flex items-start gap-2">
          <span className={r.ok ? 'text-slime' : 'text-blood'} aria-label={r.ok ? 'ok' : 'error'}>{r.ok ? '✓' : '✗'}</span>
          <span className="min-w-0 break-words">
            <b>{r.name || fmtMac(r.mac)}</b>
            {!r.ok && r.error ? ` — ${r.error}` : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

function useFallback(run: () => Promise<{ results: FallbackResult[] }>, msg: string) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<FallbackResult[] | null>(null)
  const go = async () => {
    if (!window.confirm(msg)) return
    setBusy(true)
    setError(null)
    setResults(null)
    try {
      setResults((await run()).results)
    } catch (e) {
      setError(fmtErr(e))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, results, go }
}

function FallbackAll() {
  const locked = useLive().settings?.show_lock ?? false
  const fb = useFallback(
    () => api.boards.fallbackAll(),
    'Write fallback to ALL boards? This writes each board\'s flash (WLED preset 250 + boot preset) and needs an active profile. Not allowed while a show plays.',
  )
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={fb.busy || locked} onClick={() => void fb.go()}>
          {fb.busy ? 'Saving…' : 'Save fallback to boards'}
        </button>
        {locked && <span className="text-xs text-mute">Show lock is on</span>}
        <span className="text-xs text-mute">Boards run this if the Pi dies. Needs an active profile.</span>
      </div>
      <ErrorText error={fb.error} />
      {fb.results && <ResultList results={fb.results} />}
    </div>
  )
}

function OffsetField({ board }: { board: Board }) {
  const locked = useLive().settings?.show_lock ?? false
  const [draft, setDraft] = useState<string | null>(null)
  const [saved, setSaved] = useState<{ value: number; from: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Show saved value only until the board snapshot changes at all.
  const shown = draft ?? (saved && board.offset_ms === saved.from ? String(saved.value) : String(board.offset_ms))

  const commit = async () => {
    if (draft === null) return
    const n = Math.max(-500, Math.min(500, Math.round(Number(draft))))
    if (draft.trim() === '' || !Number.isFinite(n) || n === board.offset_ms) { setDraft(null); return }
    setBusy(true)
    setError(null)
    try {
      await api.boards.setOffset(board.mac, n)
      setSaved({ value: n, from: board.offset_ms })
      setDraft(null)
    } catch (e) {
      setError(fmtErr(e))
      setSaved(null)
      setDraft(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1">
      <label className="flex items-center gap-2 text-xs">
        <span className="text-mute">Offset (ms)</span>
        <input
          type="number"
          className="input w-24"
          min={-500}
          max={500}
          step={1}
          inputMode="numeric"
          value={shown}
          disabled={busy || locked}
          aria-label="Board offset in milliseconds"
          onChange={(e) => { setSaved(null); setDraft(e.target.value) }}
          onBlur={() => void commit()}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
        />
        {locked && <span className="text-mute">Show lock is on</span>}
      </label>
      <ErrorText error={error} />
    </div>
  )
}

function Toolbar() {
  const scan = useAction()
  const add = useAction()
  const [scanning, setScanning] = useState(false)
  const [ip, setIp] = useState('')

  useEffect(() => {
    if (!scanning) return
    const t = window.setTimeout(() => setScanning(false), 10000)
    return () => window.clearTimeout(t)
  }, [scanning])

  const doScan = async () => {
    const r = await scan.run(() => api.boards.scan())
    if (r) setScanning(true)
  }

  const doAdd = async (e: FormEvent) => {
    e.preventDefault()
    const v = ip.trim()
    if (!v) return
    const r = await add.run(() => api.boards.add(v))
    if (r) setIp('')
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" disabled={scan.busy || scanning} onClick={doScan}>
          {scanning ? 'Scanning…' : 'Scan network'}
        </button>
        <form onSubmit={doAdd} className="flex min-w-0 flex-1 gap-2 sm:flex-none">
          <input
            className="input min-w-0 sm:w-44"
            placeholder="192.168.1.50"
            inputMode="decimal"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            aria-label="Board IP address"
          />
          <button type="submit" className="btn shrink-0" disabled={add.busy || !ip.trim()}>
            {add.busy ? 'Adding…' : 'Add by IP'}
          </button>
        </form>
      </div>
      {scanning && <p className="text-sm text-mute">scanning… new boards appear below automatically.</p>}
      <ErrorText error={scan.error} />
      <ErrorText error={add.error} />
    </div>
  )
}

function DiscoveredRow({ board }: { board: Board }) {
  const act = useAction()
  const [adopting, setAdopting] = useState(false)
  const [name, setName] = useState(board.name)

  const confirmAdopt = async (e: FormEvent) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    const r = await act.run(() => api.boards.adopt(board.mac, n))
    if (r) setAdopting(false)
  }

  return (
    <li className="card space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusDot status={board.status} />
            <span className="truncate font-medium">{board.name || 'Unnamed'}</span>
          </div>
          <div className="mt-1 text-xs text-mute break-all">
            {board.ip} · {fmtMac(board.mac)} · {board.firmware || 'unknown fw'}
          </div>
        </div>
        {!adopting && (
          <div className="flex gap-2">
            <button className="btn btn-primary" disabled={act.busy} onClick={() => { setName(board.name); setAdopting(true) }}>
              Adopt
            </button>
            <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.boards.remove(board.mac))}>
              Ignore
            </button>
          </div>
        )}
      </div>
      {adopting && (
        <form onSubmit={confirmAdopt} className="flex flex-wrap gap-2">
          <input
            className="input min-w-0 flex-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Board name"
            autoFocus
          />
          <button type="submit" className="btn btn-primary" disabled={act.busy || !name.trim()}>Confirm</button>
          <button type="button" className="btn" onClick={() => setAdopting(false)}>Cancel</button>
        </form>
      )}
      <ErrorText error={act.error} />
    </li>
  )
}

function CheckList({ board, check, onCheck }: { board: Board; check: CheckResult; onCheck: () => Promise<void> }) {
  const fix = useAction()

  const doFix = async (rec: Recommendation) => {
    if (!window.confirm(`Write this setting to ${board.name}? The board may reconnect briefly.`)) return
    const r = await fix.run(() => api.boards.applyRecommendation(board.mac, rec.id))
    if (r) await onCheck()
  }

  return (
    <div className="space-y-2 rounded-lg border border-line p-3 text-xs">
      {check === 'loading' ? (
        <p className="text-mute">Checking settings…</p>
      ) : !Array.isArray(check) ? (
        <ErrorText error={check.error} />
      ) : check.length === 0 ? (
        <p className="text-mute">No checks available for this board.</p>
      ) : (
        <ul className="space-y-2">
          {check.map((rec) => (
            <li key={rec.id} className="flex items-start gap-2">
              <span className={rec.ok ? 'text-slime' : 'text-pumpkin'} aria-label={rec.ok ? 'ok' : 'needs attention'}>
                {rec.ok ? '✓' : '⚠'}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{rec.title}</p>
                {rec.detail && <p className="text-mute">{rec.detail}</p>}
              </div>
              {!rec.ok && rec.fixable && (
                <button className="btn shrink-0 px-2 py-1 text-xs" disabled={fix.busy} onClick={() => void doFix(rec)}>
                  Fix
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ErrorText error={fix.error} />
    </div>
  )
}

function BoardCard({ board, check, onCheck }: { board: Board; check: CheckResult | undefined; onCheck: () => Promise<void> }) {
  const act = useAction()
  const locked = useLive().settings?.show_lock ?? false
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(board.name)
  const [bri, setBri] = useState<number | null>(null)
  const live = board.live ?? {}
  const shownBri = bri ?? live.bri ?? 0
  const fxName = live.fx != null ? board.effects?.[live.fx] : undefined
  const fb = useFallback(
    () => api.boards.fallback(board.mac),
    `Write fallback to "${board.name}"? This writes the board's flash (WLED preset 250 + boot preset) and needs an active profile. Not allowed while a show plays.`,
  )
  const c = live.col?.[0]
  const swatch = c && c.length >= 3 ? `rgb(${c[0]},${c[1]},${c[2]})` : null

  const commitBri = async (v: number) => {
    await act.run(() => api.boards.setState(board.mac, { bri: v }))
    setBri(null)
  }

  const doRename = async (e: FormEvent) => {
    e.preventDefault()
    const n = name.trim()
    if (!n) return
    const r = await act.run(() => api.boards.rename(board.mac, n))
    if (r) setRenaming(false)
  }

  const doRemove = () => {
    if (!confirm(`Remove board "${board.name}"?`)) return
    act.run(() => api.boards.remove(board.mac))
  }

  return (
    <li className="card flex min-w-0 flex-col gap-3">
      {renaming ? (
        <form onSubmit={doRename} className="flex flex-wrap gap-2">
          <input
            className="input min-w-0 flex-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Board name"
            autoFocus
          />
          <button type="submit" className="btn btn-primary" disabled={act.busy || !name.trim()}>Save</button>
          <button type="button" className="btn" onClick={() => setRenaming(false)}>Cancel</button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <StatusDot status={board.status} />
          <span className="min-w-0 flex-1 truncate font-semibold">{board.name}</span>
          <span className={`text-xs ${statusText[board.status]}`}>{board.status}</span>
          <SignalBars signal={board.signal} rssi={board.rssi} />
        </div>
      )}

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt className="text-mute">IP</dt><dd className="break-all">{board.ip}</dd>
        <dt className="text-mute">MAC</dt><dd className="break-all font-mono">{fmtMac(board.mac)}</dd>
        <dt className="text-mute">Last seen</dt><dd>{ago(board.last_seen)}</dd>
        <dt className="text-mute">Firmware</dt><dd>{board.firmware || '–'}</dd>
        <dt className="text-mute">LEDs</dt><dd>{board.led_count}</dd>
        <dt className="text-mute">Latency</dt><dd>{board.latency_ms != null ? `${Math.round(board.latency_ms)} ms` : '–'}</dd>
      </dl>

      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-panel-2 px-3 py-2 text-xs">
        <span className={live.on ? 'text-slime' : 'text-mute'}>{live.on ? 'ON' : 'OFF'}</span>
        <span className="text-mute">·</span>
        <span>bri {live.bri ?? '–'}</span>
        <span className="text-mute">·</span>
        <span className="min-w-0 truncate">{fxName ?? (live.fx != null ? `fx ${live.fx}` : '–')}</span>
        {swatch && (
          <span className="ml-auto inline-block size-4 rounded border border-line" style={{ background: swatch }} title={swatch} />
        )}
      </div>

      <div className="flex items-center gap-3">
        <button
          className={`btn shrink-0 ${live.on ? 'btn-primary' : ''}`}
          aria-pressed={live.on}
          disabled={act.busy}
          onClick={() => act.run(() => api.boards.setState(board.mac, { on: !live.on }))}
        >
          {live.on ? 'Power: on' : 'Power: off'}
        </button>
        <input
          type="range"
          min={0}
          max={255}
          value={shownBri}
          aria-label="Brightness"
          className="min-w-0 flex-1 accent-pumpkin"
          onChange={(e) => setBri(Number(e.currentTarget.value))}
          onPointerUp={(e) => commitBri(Number(e.currentTarget.value))}
          onKeyUp={(e) => commitBri(Number(e.currentTarget.value))}
        />
        <span className="w-8 text-right text-xs tabular-nums text-mute">{shownBri}</span>
      </div>

      <OffsetField board={board} />

      <GroupChips board={board} />

      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.boards.identify(board.mac))}>Identify</button>
        <button className="btn" disabled={fb.busy || locked} onClick={() => void fb.go()}>{fb.busy ? 'Saving…' : 'Save fallback'}</button>
        {locked && <span className="self-center text-xs text-mute">Show lock is on</span>}
        <a className="btn" href={`http://${board.ip}`} target="_blank" rel="noreferrer">WLED UI</a>
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.boards.refresh(board.mac))}>Refresh</button>
        {!renaming && (
          <button className="btn" onClick={() => { setName(board.name); setRenaming(true) }}>Rename</button>
        )}
        <button className="btn" disabled={check === 'loading'} onClick={() => void onCheck()}>
          {check === 'loading' ? 'Checking…' : 'Check settings'}
        </button>
        <button className="btn btn-danger" disabled={act.busy} onClick={doRemove}>Remove</button>
      </div>
      <ErrorText error={act.error} />
      <ErrorText error={fb.error} />
      {fb.results && <ResultList results={fb.results} />}
      {check !== undefined && <CheckList board={board} check={check} onCheck={onCheck} />}
    </li>
  )
}

function AddHelp() {
  return (
    <details className="card">
      <summary className="cursor-pointer select-none font-medium">Add a new board</summary>
      <div className="mt-3 space-y-3 text-sm">
        <ol className="list-decimal space-y-1 pl-5">
          <li>
            Flash WLED with the official web installer:{' '}
            <a className="text-ghost underline" href="https://install.wled.me" target="_blank" rel="noreferrer">install.wled.me</a>
          </li>
          <li>Connect your phone to the board's <b>WLED-AP</b> Wi-Fi access point (default password <code>wled1234</code>).</li>
          <li>Open <a className="text-ghost underline" href="http://4.3.2.1" target="_blank" rel="noreferrer">4.3.2.1</a> and enter your home WLAN credentials.</li>
          <li>Once it joins the WLAN it appears here automatically under <b>Discovered</b> (or use Scan / Add by IP).</li>
        </ol>
        <div className="rounded-lg border border-pumpkin/40 bg-pumpkin/10 p-3 text-xs">
          <p><b>Tips:</b> reserve a DHCP lease / static IP for each board in your router.</p>
          <p className="mt-1">Disable WiFi sleep in WLED (Config → WiFi Setup → "Disable WiFi sleep") for low latency.</p>
        </div>
      </div>
    </details>
  )
}

export default function Boards() {
  const { boards, groups } = useLive()
  const [, setTick] = useState(0)
  const [filter, setFilterState] = useState<RoomFilter>(loadFilter)

  const setFilter = (f: RoomFilter) => {
    setFilterState(f)
    try { localStorage.setItem(FILTER_KEY, String(f)) } catch { /* private mode: filter just won't persist */ }
  }

  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 5000)
    return () => window.clearInterval(t)
  }, [])

  const [checks, setChecks] = useState<Record<string, CheckResult>>({})
  const [checkedAll, setCheckedAll] = useState<string[] | null>(null)

  const runCheck = useCallback(async (mac: string) => {
    setChecks((p) => ({ ...p, [mac]: 'loading' }))
    try {
      const r = await api.boards.recommendations(mac)
      setChecks((p) => ({ ...p, [mac]: r }))
    } catch (e) {
      setChecks((p) => ({ ...p, [mac]: { error: e instanceof Error ? e.message : String(e) } }))
    }
  }, [])

  const all = Object.values(boards).sort((a, b) => a.name.localeCompare(b.name))
  const discovered = all.filter((b) => !b.adopted)
  const adopted = all.filter((b) => b.adopted)
  const onlineMacs = adopted.filter((b) => b.status === 'online').map((b) => b.mac)
  // A stored room id may point at a deleted room; treat it as "All" instead of showing nothing.
  const activeGroup = typeof filter === 'number' ? groups.find((g) => g.id === filter) : undefined
  const effFilter: RoomFilter = typeof filter === 'number' && !activeGroup ? 'all' : filter
  const grouped = new Set(groups.flatMap((g) => g.macs))
  const shown = effFilter === 'all' ? adopted
    : effFilter === 'none' ? adopted.filter((b) => !grouped.has(b.mac))
    : adopted.filter((b) => activeGroup?.macs.includes(b.mac))
  const chip = (active: boolean) =>
    `min-h-10 rounded-full border px-3 text-xs ${active ? 'border-pumpkin bg-pumpkin/20 font-semibold' : 'border-line text-mute'}`

  const checkAll = async () => {
    setCheckedAll(onlineMacs)
    await Promise.all(onlineMacs.map((mac) => runCheck(mac)))
  }

  let summary: string | null = null
  if (checkedAll) {
    const res = checkedAll.map((mac) => checks[mac]).filter((r) => r !== undefined)
    if (checkedAll.length === 0) summary = 'No online boards to check.'
    else if (res.some((r) => r === 'loading')) summary = `Checking ${checkedAll.length} board${checkedAll.length === 1 ? '' : 's'}…`
    else {
      const need = res.filter((r) => Array.isArray(r) && r.some((x) => !x.ok)).length
      const failed = res.filter((r) => r !== 'loading' && !Array.isArray(r)).length
      const parts = [need === 0 ? 'All checked boards look good' : `${need} board${need === 1 ? '' : 's'} need${need === 1 ? 's' : ''} attention`]
      if (failed > 0) parts.push(`${failed} could not be checked`)
      summary = parts.join(' · ')
    }
  }
  const anyLoading = checkedAll?.some((mac) => checks[mac] === 'loading') ?? false

  return (
    <div className="space-y-6">
      <Section title="Boards" actions={<Toolbar />}>
        <div className="card space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Recommended settings</span>
            <button className="btn ml-auto" disabled={anyLoading || onlineMacs.length === 0} onClick={() => void checkAll()}>
              {anyLoading ? 'Checking…' : 'Check all boards'}
            </button>
          </div>
          {summary && <p className="text-sm text-mute">{summary}</p>}
          <p className="rounded-lg border border-pumpkin/40 bg-pumpkin/10 p-2 text-xs">
            <b>Recommended:</b> give every board a DHCP reservation / static IP in your router, so it keeps the same address and doesn't drop out of shows.
          </p>
          <FallbackAll />
        </div>
      </Section>

      {discovered.length > 0 && (
        <Section title={`Discovered (${discovered.length})`}>
          <ul className="space-y-2">
            {discovered.map((b) => <DiscoveredRow key={b.mac} board={b} />)}
          </ul>
        </Section>
      )}

      <Rooms />

      <Section title={`Adopted (${adopted.length})`}>
        {adopted.length === 0 ? (
          <p className="text-sm text-mute">No boards adopted yet. Scan the network or add one by IP.</p>
        ) : (
          <>
            {groups.length > 0 && (
              <div className="flex flex-wrap gap-2" role="group" aria-label="Filter boards by room">
                <button className={chip(effFilter === 'all')} aria-pressed={effFilter === 'all'} onClick={() => setFilter('all')}>All</button>
                {groups.map((g) => (
                  <button
                    key={g.id}
                    className={chip(effFilter === g.id)}
                    style={effFilter === g.id ? { borderColor: g.color, background: tint(g.color, '33') } : undefined}
                    aria-pressed={effFilter === g.id}
                    onClick={() => setFilter(g.id)}
                  >
                    {g.name}
                  </button>
                ))}
                <button className={chip(effFilter === 'none')} aria-pressed={effFilter === 'none'} onClick={() => setFilter('none')}>No room</button>
              </div>
            )}
            {shown.length === 0 ? (
              <p className="text-sm text-mute">No boards in this filter.</p>
            ) : (
              <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {shown.map((b) => (
                  <BoardCard key={b.mac} board={b} check={checks[b.mac]} onCheck={() => runCheck(b.mac)} />
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      <AddHelp />
    </div>
  )
}
