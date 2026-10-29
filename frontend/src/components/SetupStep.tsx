import type { ReactNode } from 'react'
import type { BoardStatus } from '../types'
import { StatusDot } from './ui'

export type StepStatus = 'ok' | 'warn' | 'fail' | 'unknown'

const dot: Record<StepStatus, BoardStatus> = { ok: 'online', warn: 'degraded', fail: 'offline', unknown: 'unknown' }

export function SetupStep({ n, title, status, hint, children }: {
  n: number
  title: string
  status: StepStatus
  hint?: string | null
  children: ReactNode
}) {
  return (
    <section className="card space-y-3">
      <div className="flex items-center gap-2">
        <StatusDot status={dot[status]} />
        <h2 className="text-base font-semibold">{n}. {title}</h2>
      </div>
      {hint && <p className="rounded-lg border border-pumpkin/40 bg-pumpkin/10 p-2 text-xs">{hint}</p>}
      {children}
    </section>
  )
}
