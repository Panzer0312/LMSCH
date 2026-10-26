import { useCallback, useState } from 'react'

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

