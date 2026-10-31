import { useCallback, useEffect, useRef, useState } from 'react'

export function ago(ts: number | null): string {
  if (!ts) return 'never'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 5) return 'just now'
  if (s < 60) return `${Math.round(s)} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  return `${Math.round(s / 3600)} h ago`
}

export function fmtTime(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '–:––'
  const m = Math.floor(s / 60)
  const sec = s - m * 60
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`
}

/** Wrap an async action: tracks busy + error so every button shows failures. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return undefined
    } finally {
      setBusy(false)
    }
  }, [])
  return { busy, error, run, setError }
}

/**
 * The POST returns before the WS state changes, so a toggle button would re-enable showing its
 * old label and a second tap repeats the action. `pending` holds until `value` changes or `ms` pass.
 */
export function useSettle(value: unknown, ms = 1500) {
  const [armed, setArmed] = useState<{ v: unknown } | null>(null)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const arm = useCallback(() => {
    setArmed({ v: value })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setArmed(null), ms)
  }, [value, ms])
  return { pending: armed != null && Object.is(armed.v, value), arm }
}

/** One tap on these controls stops the show, so ask first while one is running. */
export function confirmStopShow(phase: string | null | undefined, what: string): boolean {
  if (phase !== 'playing' && phase !== 'paused') return true
  return window.confirm(`A show is running. ${what} stops it. Continue?`)
}

