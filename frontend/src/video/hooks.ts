import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useLive } from '../live'
import type { DisplayState } from '../types'

/**
 * Leading + trailing throttle: the first call goes out at once, the last one during a
 * drag always lands, nothing faster than `ms` in between.
 */
export function useThrottled<T>(fn: (v: T) => void, ms = 30) {
  const f = useRef(fn)
  const last = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<{ v: T } | null>(null)
  useEffect(() => { f.current = fn }, [fn])
  useEffect(() => () => window.clearTimeout(timer.current), [])
  return useCallback((v: T) => {
    pending.current = { v }
    if (timer.current !== undefined) return
    const fire = () => {
      timer.current = undefined
      const p = pending.current
      if (!p) return
      pending.current = null
      last.current = performance.now()
      f.current(p.v)
    }
    const wait = Math.max(0, ms - (performance.now() - last.current))
    if (wait === 0) fire()
    else timer.current = window.setTimeout(fire, wait)
  }, [ms])
}

/** WS state wins; the one-off GET only covers the gap before the first `display` message. */
export function useDisplayState(): { display: DisplayState | null; error: string | null } {
  const live = useLive().display
  const [fetched, setFetched] = useState<DisplayState | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.display.state().then((s) => { if (alive) setFetched(s) }, (e) => { if (alive) setError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [])
  return { display: live ?? fetched, error: live ? null : error }
}
