import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Ctx, initial, reduce, type Live } from './live'

/** Single WebSocket for all live state; reconnects with backoff (phones sleep). */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Live>(initial)
  const retry = useRef(500)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: number | undefined
    let ping: number | undefined
    let closed = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => {
        retry.current = 500
        setState((s) => ({ ...s, connected: true }))
        ping = window.setInterval(() => ws?.readyState === 1 && ws.send('ping'), 20000)
      }
      ws.onmessage = (e) => setState((s) => reduce(s, JSON.parse(e.data)))
      ws.onclose = () => {
        window.clearInterval(ping)
        setState((s) => ({ ...s, connected: false }))
        if (!closed) {
          timer = window.setTimeout(connect, retry.current)
          retry.current = Math.min(retry.current * 2, 5000)
        }
      }
    }
    connect()
    return () => { closed = true; window.clearTimeout(timer); window.clearInterval(ping); ws?.close() }
  }, [])

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>
}
