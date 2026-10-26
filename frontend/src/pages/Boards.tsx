import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { Board, BoardStatus } from '../types'
import { ErrorText, Section, SignalBars, StatusDot } from '../components/ui'
import { ago, useAction } from '../lib'

const statusText: Record<BoardStatus, string> = {
  online: 'text-slime', degraded: 'text-pumpkin', offline: 'text-blood', unknown: 'text-mute',
}

function fmtMac(mac: string): string {
  const hex = mac.toLowerCase().replace(/[^0-9a-f]/g, '')
  return hex.length === 12 ? (hex.match(/.{2}/g) ?? []).join(':') : mac
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

function BoardCard({ board }: { board: Board }) {
  const act = useAction()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(board.name)
  const [bri, setBri] = useState<number | null>(null)
  const live = board.live ?? {}
  const shownBri = bri ?? live.bri ?? 0
  const fxName = live.fx != null ? board.effects?.[live.fx] : undefined
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
          disabled={act.busy}
          onClick={() => act.run(() => api.boards.setState(board.mac, { on: !live.on }))}
        >
          {live.on ? 'On' : 'Off'}
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

      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.boards.identify(board.mac))}>Identify</button>
        <a className="btn" href={`http://${board.ip}`} target="_blank" rel="noreferrer">WLED UI</a>
        <button className="btn" disabled={act.busy} onClick={() => act.run(() => api.boards.refresh(board.mac))}>Refresh</button>
        {!renaming && (
          <button className="btn" onClick={() => { setName(board.name); setRenaming(true) }}>Rename</button>
        )}
        <button className="btn btn-danger" disabled={act.busy} onClick={doRemove}>Remove</button>
      </div>
      <ErrorText error={act.error} />
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
  const { boards } = useLive()
  const [, setTick] = useState(0)

  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 5000)
    return () => window.clearInterval(t)
  }, [])

  const all = Object.values(boards).sort((a, b) => a.name.localeCompare(b.name))
  const discovered = all.filter((b) => !b.adopted)
  const adopted = all.filter((b) => b.adopted)

  return (
    <div className="space-y-6">
      <Section title="Boards" actions={<Toolbar />}>
        {null}
      </Section>

      {discovered.length > 0 && (
        <Section title={`Discovered (${discovered.length})`}>
          <ul className="space-y-2">
            {discovered.map((b) => <DiscoveredRow key={b.mac} board={b} />)}
          </ul>
        </Section>
      )}

      <Section title={`Adopted (${adopted.length})`}>
        {adopted.length === 0 ? (
          <p className="text-sm text-mute">No boards adopted yet. Scan the network or add one by IP.</p>
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {adopted.map((b) => <BoardCard key={b.mac} board={b} />)}
          </ul>
        )}
      </Section>

      <AddHelp />
    </div>
  )
}
