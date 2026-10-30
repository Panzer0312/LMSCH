import { useCallback, useEffect, useRef, useState } from 'react'
import { initialLive, reduceJuke, type JukeLive, type JukeMsg } from './logic'

/**
 * Own WebSocket to /ws/jukebox (no PIN). Reconnects with backoff because phones sleep and
 * party WiFi drops. Reconnects when the token changes so the hub counts us as active.
 */
export function useJukeboxLive(token: string | null): JukeLive & { connected: boolean } {
  const [live, setLive] = useState<JukeLive>(initialLive)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: number | undefined
    let closed = false
    let retry = 500
    // Each socket owns its ping interval; only the current socket may schedule a reconnect.
    const connect = () => {
      window.clearTimeout(timer)
      const old = ws
      if (old) {
        old.onopen = old.onmessage = old.onclose = null
        old.close()
      }
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const q = token ? `?token=${encodeURIComponent(token)}` : ''
      const sock = new WebSocket(`${proto}://${location.host}/ws/jukebox${q}`)
      ws = sock
      let ping: number | undefined
      const stopPing = () => window.clearInterval(ping)
      sock.addEventListener('close', stopPing)
      sock.onopen = () => {
        retry = 500
        setConnected(true)
        ping = window.setInterval(() => sock.readyState === 1 && sock.send('ping'), 20000)
      }
      sock.onmessage = (e) => {
        let m: JukeMsg
        try { m = JSON.parse(e.data) as JukeMsg } catch { return }
        setLive((s) => reduceJuke(s, m, Date.now()))
      }
      sock.onclose = () => {
        if (ws !== sock) return
        setConnected(false)
        if (!closed) {
          timer = window.setTimeout(connect, retry)
          retry = Math.min(retry * 2, 10000)
        }
      }
    }
    connect()
    // Phones resume with a dead socket that hasn't noticed yet: reconnect immediately.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && ws && ws.readyState > 1) {
        retry = 500
        connect()
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      closed = true
      document.removeEventListener('visibilitychange', onVisible)
      window.clearTimeout(timer)
      ws?.close()
    }
  }, [token])

  return { ...live, connected }
}

/** Ticks only while `active`, so a paused player doesn't re-render. */
export function useTick(active: boolean, ms = 500): number {
  const [t, setT] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const i = window.setInterval(() => setT(Date.now()), ms)
    return () => window.clearInterval(i)
  }, [active, ms])
  return t
}

export interface Toast { id: number; text: string; kind: 'ok' | 'err' }

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const seq = useRef(0)
  const push = useCallback((text: string, kind: Toast['kind'] = 'ok') => {
    const id = ++seq.current
    setToasts((t) => [...t.slice(-2), { id, text, kind }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'err' ? 5000 : 3000)
  }, [])
  return { toasts, push }
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

/** Clipboard API needs a secure context; the hub is plain http on a LAN IP, so fall back. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through */ }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  ta.remove()
  return ok
}
