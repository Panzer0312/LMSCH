import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ThumbsMeta, Waveform } from '../types'

const THUMB_H = 40
const WAVE_H = 48
const CHUNK = 4096 // css px per canvas (browsers cap canvas size)
const WAVE_COLOR = 'rgba(180,156,255,0.7)'

interface Props { mediaId: number | null; duration: number; pxPerSec: number }

export function MediaStrip({ mediaId, duration, pxPerSec }: Props) {
  const [thumbsRes, setThumbsRes] = useState<{ id: number; meta: ThumbsMeta | null } | null>(null)
  const [waveRes, setWaveRes] = useState<{ id: number; wave: Waveform | null } | null>(null)
  const canvases = useRef<(HTMLCanvasElement | null)[]>([])

  useEffect(() => {
    if (mediaId == null) return
    let cancelled = false
    api.media.thumbs(mediaId)
      .then((meta) => { if (!cancelled) setThumbsRes({ id: mediaId, meta }) })
      .catch(() => { if (!cancelled) setThumbsRes({ id: mediaId, meta: null }) })
    api.media.waveform(mediaId)
      .then((wave) => { if (!cancelled) setWaveRes({ id: mediaId, wave }) })
      .catch(() => { if (!cancelled) setWaveRes({ id: mediaId, wave: null }) })
    return () => { cancelled = true }
  }, [mediaId])

  const thumbs = mediaId != null && thumbsRes?.id === mediaId ? thumbsRes.meta : null
  const wave = mediaId != null && waveRes?.id === mediaId ? waveRes.wave : null
  const hasWave = !!wave && wave.peaks.length > 0 && wave.rate > 0
  const loadingWave = mediaId != null && waveRes?.id !== mediaId

  const width = Math.max(0, Math.ceil(duration * pxPerSec))
  const chunks = hasWave ? Math.ceil(width / CHUNK) : 0

  useEffect(() => {
    if (!wave || !hasWave) return
    const dpr = window.devicePixelRatio || 1
    const mid = WAVE_H / 2
    const { peaks, rate } = wave
    for (let ci = 0; ci < chunks; ci++) {
      const cv = canvases.current[ci]
      if (!cv) continue
      const off = ci * CHUNK
      const w = Math.min(CHUNK, width - off)
      cv.width = Math.max(1, Math.round(w * dpr))
      cv.height = Math.round(WAVE_H * dpr)
      const ctx = cv.getContext('2d')
      if (!ctx) continue
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, WAVE_H)
      ctx.fillStyle = WAVE_COLOR
      for (let x = 0; x < w; x++) {
        const gx = off + x
        const i0 = Math.floor((gx / pxPerSec) * rate)
        if (i0 >= peaks.length) break
        const i1 = Math.min(peaks.length, Math.max(i0 + 1, Math.ceil(((gx + 1) / pxPerSec) * rate)))
        let m = 0
        for (let i = i0; i < i1; i++) if (peaks[i] > m) m = peaks[i]
        const h = Math.max(0.5, m * (mid - 1))
        ctx.fillRect(x, mid - h, 1, h * 2)
      }
    }
  }, [wave, hasWave, chunks, width, pxPerSec])

  const tiles: { i: number; left: number; w: number }[] = []
  let tileW = 0
  if (thumbs && thumbs.count > 0 && thumbs.tile_h > 0 && thumbs.interval > 0) {
    tileW = (thumbs.tile_w * THUMB_H) / thumbs.tile_h
    for (let i = 0; i < thumbs.count && i * thumbs.interval < duration; i++) {
      tiles.push({ i, left: i * thumbs.interval * pxPerSec, w: thumbs.interval * pxPerSec })
    }
  }

  return (
    <div className="relative select-none border-b border-line bg-bg" style={{ width, height: THUMB_H + WAVE_H }}>
      <div className="relative overflow-hidden" style={{ height: THUMB_H }}>
        {thumbs && mediaId != null && tiles.map((t) => (
          <div
            key={t.i}
            className="absolute top-0 border-r border-black/40"
            style={{
              left: t.left,
              width: t.w,
              height: THUMB_H,
              backgroundImage: `url(${api.media.thumbsUrl(mediaId)})`,
              backgroundSize: `${thumbs.count * tileW}px ${THUMB_H}px`,
              backgroundPosition: `-${t.i * tileW}px 0`,
              backgroundRepeat: 'no-repeat',
            }}
          />
        ))}
      </div>
      <div className="relative overflow-hidden" style={{ height: WAVE_H }}>
        {hasWave
          ? Array.from({ length: chunks }, (_, ci) => (
              <canvas
                key={ci}
                ref={(el) => { canvases.current[ci] = el }}
                className="absolute top-0"
                style={{ left: ci * CHUNK, width: Math.min(CHUNK, width - ci * CHUNK), height: WAVE_H }}
              />
            ))
          : (
            <div className="sticky left-0 flex h-full w-fit items-center px-2 text-xs text-mute">
              {mediaId == null ? 'no media' : loadingWave ? 'loading…' : 'no audio'}
            </div>
          )}
      </div>
    </div>
  )
}
