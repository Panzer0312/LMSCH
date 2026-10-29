import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { getDisplay, setDisplay, useDisplay } from '../live'
import type { DisplaySource, DisplayState } from '../types'

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
  const display = useDisplay()
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.display.state().then(
      (s) => { if (getDisplay() === null) setDisplay(s) },
      (e) => { if (alive) setError(e instanceof Error ? e.message : String(e)) },
    )
    return () => { alive = false }
  }, [])
  return { display, error: display ? null : error }
}

/**
 * At most one request in flight plus one latest-pending slot: values that pile up during a
 * slow request collapse to the newest, so an older value can never land last.
 * `fn` must handle its own errors; a rejection only advances the queue.
 */
export function useSerialSender<T>(fn: (v: T) => Promise<unknown>) {
  const f = useRef(fn)
  const busy = useRef(false)
  const next = useRef<{ v: T } | null>(null)
  useEffect(() => { f.current = fn }, [fn])
  return useCallback((v: T) => {
    next.current = { v }
    if (busy.current) return
    const pump = () => {
      const p = next.current
      if (!p) { busy.current = false; return }
      next.current = null
      busy.current = true
      f.current(p.v).then(pump, pump)
    }
    pump()
  }, [])
}

/**
 * Enter/leave setup mode from a page. If this page entered it, unmounting leaves it, also when
 * the enter request only resolves after the page is gone.
 */
export function useSetupSession() {
  const entered = useRef(false)
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (entered.current) {
        entered.current = false
        void api.display.setup(false).catch(() => undefined)
      }
    }
  }, [])
  /** Resolves false when the page unmounted meanwhile (setup was already left again). */
  const enter = useCallback(async (src: DisplaySource) => {
    const was = entered.current
    // Set before awaiting so an unmount during the request knows to leave.
    entered.current = true
    try {
      await api.display.setup(true, src)
    } catch (e) {
      if (mounted.current) entered.current = was
      throw e
    }
    if (!mounted.current) {
      entered.current = false
      void api.display.setup(false).catch(() => undefined)
      return false
    }
    return true
  }, [])
  const leave = useCallback(async () => {
    await api.display.setup(false)
    entered.current = false
  }, [])
  return { enter, leave }
}
