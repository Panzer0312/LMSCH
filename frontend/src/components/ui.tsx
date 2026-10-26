import type { ReactNode } from 'react'
import type { BoardStatus } from '../types'

const statusColor: Record<BoardStatus, string> = {
  online: 'bg-slime', degraded: 'bg-pumpkin', offline: 'bg-blood', unknown: 'bg-mute',
}

export function StatusDot({ status }: { status: BoardStatus }) {
  return <span title={status} className={`inline-block size-2.5 shrink-0 rounded-full ${statusColor[status]}`} />
}

/** WLED reports signal quality 0–100; fall back to RSSI dBm. */
export function SignalBars({ signal, rssi }: { signal: number | null; rssi: number | null }) {
  const q = signal ?? (rssi == null ? null : Math.max(0, Math.min(100, 2 * (rssi + 100))))
  const bars = q == null ? 0 : q > 75 ? 4 : q > 50 ? 3 : q > 25 ? 2 : 1
  return (
    <span className="inline-flex items-end gap-0.5" title={rssi != null ? `${rssi} dBm` : 'no signal data'}>
      {[1, 2, 3, 4].map((i) => (
        <span key={i} className={`w-1 rounded-sm ${i <= bars ? 'bg-ghost' : 'bg-line'}`} style={{ height: 3 + i * 3 }} />
      ))}
    </span>
  )
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

export function ErrorText({ error }: { error: string | null }) {
  return error ? <p className="text-sm text-blood">{error}</p> : null
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition ${checked ? 'bg-pumpkin' : 'bg-line'}`}
      >
        <span className={`absolute top-0.5 size-5 rounded-full bg-white transition ${checked ? 'left-5.5' : 'left-0.5'}`} />
      </button>
      {label}
    </label>
  )
}
