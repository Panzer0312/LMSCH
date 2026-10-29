import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { DisplayGeometry } from '../types'
import { clamp, inverseWarp, toGl, videoRect, warpQuad } from './geometry'
import { drawPattern, PATTERN_H, PATTERN_W } from './patterns'

/** Margin around the output frame (output fractions) so outward corners stay grabbable. */
const M = 0.08
const SPAN = 1 + 2 * M
const toPct = (v: number) => ((v + M) / SPAN) * 100

export type PreviewSource =
  | { kind: 'pattern'; id: string }
  | { kind: 'video'; url: string; w: number; h: number }
  | null

const VS = `
attribute vec2 a_pos;
varying vec2 v_out;
uniform float u_m;
void main() {
  vec2 n = a_pos * 0.5 + 0.5;
  v_out = vec2(n.x, 1.0 - n.y) * (1.0 + 2.0 * u_m) - u_m;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`

// Pure mimic of mpv placement + warp.glsl; the projector stays the truth.
const FS = `
precision mediump float;
varying vec2 v_out;
uniform sampler2D u_tex;
uniform float u_has;
uniform mat3 u_inv;
uniform vec4 u_rect;
uniform vec4 u_crop;
uniform float u_rot;
uniform vec2 u_flip;
uniform vec4 u_mask;
uniform float u_feather;
uniform vec4 u_img;
uniform float u_outline;
uniform float u_px;
void main() {
  vec2 s = v_out;
  bool inside = s.x >= 0.0 && s.y >= 0.0 && s.x <= 1.0 && s.y <= 1.0;
  vec3 col = inside ? vec3(0.0) : vec3(0.07, 0.065, 0.1);
  vec3 q3 = u_inv * vec3(s, 1.0);
  vec2 q = q3.xy / q3.z;
  if (q3.z > 0.0 && q.x >= 0.0 && q.y >= 0.0 && q.x <= 1.0 && q.y <= 1.0) {
    float f = max(u_feather, 0.0001);
    float a = clamp((q.y - u_mask.x) / f, 0.0, 1.0) * clamp((1.0 - u_mask.y - q.x) / f, 0.0, 1.0)
            * clamp((1.0 - u_mask.z - q.y) / f, 0.0, 1.0) * clamp((q.x - u_mask.w) / f, 0.0, 1.0);
    vec2 p = vec2(u_flip.x > 0.5 ? 1.0 - q.x : q.x, u_flip.y > 0.5 ? 1.0 - q.y : q.y);
    vec2 uv = (p - u_rect.xy) / u_rect.zw;
    vec3 c = vec3(0.0);
    if (u_has > 0.5 && uv.x >= 0.0 && uv.y >= 0.0 && uv.x <= 1.0 && uv.y <= 1.0) {
      vec2 o = uv;
      if (u_rot > 0.5 && u_rot < 1.5) o = vec2(uv.y, 1.0 - uv.x);
      else if (u_rot > 1.5 && u_rot < 2.5) o = vec2(1.0 - uv.x, 1.0 - uv.y);
      else if (u_rot > 2.5) o = vec2(1.0 - uv.y, uv.x);
      c = texture2D(u_tex, u_crop.xy + o * u_crop.zw).rgb;
      c = (c - 0.5) * (1.0 + u_img.y) + 0.5 + u_img.x;
      c = pow(clamp(c, 0.0, 1.0), vec3(exp2(-u_img.z * 2.0)));
      float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
      c = mix(vec3(l), c, 1.0 + u_img.w);
    }
    c *= a;
    if (u_outline > 0.5) {
      float d = min(min(abs(q.y - u_mask.x), abs(1.0 - u_mask.y - q.x)), min(abs(1.0 - u_mask.z - q.y), abs(q.x - u_mask.w)));
      if (d < u_px * 1.5) c = vec3(0.49, 0.89, 0.42);
    }
    col = inside ? c : mix(col, c, 0.35);
  }
  float e = min(min(abs(s.x), abs(s.x - 1.0)), min(abs(s.y), abs(s.y - 1.0)));
  if (e < u_px * 1.2 && s.x > -u_px && s.y > -u_px && s.x < 1.0 + u_px && s.y < 1.0 + u_px) col = vec3(1.0, 0.48, 0.1);
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`

interface GlState {
  gl: WebGLRenderingContext
  prog: WebGLProgram
  tex: WebGLTexture
  u: Record<string, WebGLUniformLocation | null>
}

function initGl(cv: HTMLCanvasElement): GlState | null {
  const gl = cv.getContext('webgl', { antialias: false, preserveDrawingBuffer: false })
  if (!gl) return null
  const sh = (type: number, src: string) => {
    const s = gl.createShader(type)!
    gl.shaderSource(s, src)
    gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(s))
    return s
  }
  const prog = gl.createProgram()!
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS))
  gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS))
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.error(gl.getProgramInfoLog(prog)); return null }
  gl.useProgram(prog)
  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  const loc = gl.getAttribLocation(prog, 'a_pos')
  gl.enableVertexAttribArray(loc)
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
  const tex = gl.createTexture()!
  gl.bindTexture(gl.TEXTURE_2D, tex)
  // NPOT textures in WebGL1 need clamp + no mipmaps.
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  const names = ['u_m', 'u_tex', 'u_has', 'u_inv', 'u_rect', 'u_crop', 'u_rot', 'u_flip', 'u_mask', 'u_feather', 'u_img', 'u_outline', 'u_px']
  const u = Object.fromEntries(names.map((n) => [n, gl.getUniformLocation(prog, n)]))
  return { gl, prog, tex, u }
}

function draw(s: GlState, g: DisplayGeometry, srcW: number, srcH: number, has: boolean, outline: boolean) {
  const { gl, u } = s
  const cv = gl.canvas as HTMLCanvasElement
  gl.viewport(0, 0, cv.width, cv.height)
  const r = videoRect(g, srcW, srcH)
  gl.uniform1f(u.u_m, M)
  gl.uniform1i(u.u_tex, 0)
  gl.uniform1f(u.u_has, has ? 1 : 0)
  gl.uniformMatrix3fv(u.u_inv, false, toGl(inverseWarp(g)))
  gl.uniform4f(u.u_rect, r.x, r.y, r.w, r.h)
  gl.uniform4f(u.u_crop, g.crop.x, g.crop.y, g.crop.w, g.crop.h)
  gl.uniform1f(u.u_rot, g.rotate / 90)
  gl.uniform2f(u.u_flip, g.flip_h ? 1 : 0, g.flip_v ? 1 : 0)
  gl.uniform4f(u.u_mask, g.mask.top, g.mask.right, g.mask.bottom, g.mask.left)
  gl.uniform1f(u.u_feather, g.mask.feather)
  gl.uniform4f(u.u_img, g.brightness / 100, g.contrast / 100, g.gamma / 100, g.saturation / 100)
  gl.uniform1f(u.u_outline, outline ? 1 : 0)
  gl.uniform1f(u.u_px, SPAN / Math.max(1, cv.width))
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
}

const LABELS = ['1', '2', '3', '4']
const BASE: [number, number][] = [[0, 0], [1, 0], [1, 1], [0, 1]]

export function Preview({ geometry, source, scrubT, onCorners }: {
  geometry: DisplayGeometry
  source: PreviewSource
  /** Media scrub position; the hidden video follows it. */
  scrubT: number | null
  onCorners: (corners: [number, number][]) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const gls = useRef<GlState | null>(null)
  const geo = useRef(geometry)
  const src = useRef<{ w: number; h: number; has: boolean; outline: boolean }>({ w: 1920, h: 1080, has: false, outline: false })
  const [noGl, setNoGl] = useState(false)
  const [drag, setDrag] = useState<number | null>(null)

  const redraw = () => {
    const s = gls.current
    if (s) draw(s, geo.current, src.current.w, src.current.h, src.current.has, src.current.outline)
  }

  useEffect(() => {
    const cv = canvas.current
    if (!cv) return
    const s = initGl(cv)
    if (!s) { setNoGl(true); return }
    gls.current = s
    const ro = new ResizeObserver(() => {
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      cv.width = Math.max(1, Math.round(cv.clientWidth * dpr))
      cv.height = Math.max(1, Math.round(cv.clientHeight * dpr))
      redraw()
    })
    ro.observe(cv)
    return () => { ro.disconnect(); gls.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    geo.current = geometry
    redraw()
  })

  // Pattern texture: drawn once per id. Video texture: re-uploaded every animation frame.
  const patternId = source?.kind === 'pattern' ? source.id : null
  const videoUrl = source?.kind === 'video' ? source.url : null
  const vw = source?.kind === 'video' ? source.w : 0
  const vh = source?.kind === 'video' ? source.h : 0
  useEffect(() => {
    const s = gls.current
    src.current = { w: 1920, h: 1080, has: false, outline: patternId === 'safe_area' }
    if (!s) return
    const { gl, tex } = s
    if (patternId !== null) {
      const cv = document.createElement('canvas')
      drawPattern(patternId, cv)
      gl.bindTexture(gl.TEXTURE_2D, tex)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, cv)
      src.current = { w: PATTERN_W * 2, h: PATTERN_H * 2, has: true, outline: patternId === 'safe_area' }
      redraw()
      return
    }
    if (videoUrl === null) { redraw(); return }
    src.current = { w: vw || 1920, h: vh || 1080, has: false, outline: false }
    let raf = 0
    const tick = () => {
      const v = video.current
      if (v && v.readyState >= 2) {
        gl.bindTexture(gl.TEXTURE_2D, tex)
        try {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v)
          src.current = { w: v.videoWidth || vw || 1920, h: v.videoHeight || vh || 1080, has: true, outline: false }
        } catch { /* frame not decodable yet */ }
        redraw()
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patternId, videoUrl, vw, vh])

  useEffect(() => {
    const v = video.current
    if (v && scrubT != null && Number.isFinite(scrubT)) v.currentTime = scrubT
  }, [scrubT, videoUrl])

  const moveTo = (i: number, clientX: number, clientY: number) => {
    const el = stage.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const ox = ((clientX - r.left) / r.width) * SPAN - M
    const oy = ((clientY - r.top) / r.height) * SPAN - M
    const next = geometry.corners.map((c) => [...c] as [number, number])
    next[i] = [clamp(ox - BASE[i][0], -0.5, 0.5), clamp(oy - BASE[i][1], -0.5, 0.5)]
    onCorners(next)
  }

  const onDown = (i: number) => (e: PointerEvent<HTMLButtonElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    e.currentTarget.focus()
    setDrag(i)
  }
  const onMove = (i: number) => (e: PointerEvent<HTMLButtonElement>) => {
    if (drag === i) moveTo(i, e.clientX, e.clientY)
  }
  const onKey = (i: number) => (e: KeyboardEvent<HTMLButtonElement>) => {
    const d = e.shiftKey ? 0.0005 : 0.005
    const delta: Record<string, [number, number]> = { ArrowLeft: [-d, 0], ArrowRight: [d, 0], ArrowUp: [0, -d], ArrowDown: [0, d] }
    const k = delta[e.key]
    if (!k) return
    e.preventDefault()
    const next = geometry.corners.map((c) => [...c] as [number, number])
    next[i] = [clamp(next[i][0] + k[0], -0.5, 0.5), clamp(next[i][1] + k[1], -0.5, 0.5)]
    onCorners(next)
  }

  const quad = warpQuad(geometry)
  return (
    <div ref={stage} className="relative aspect-video w-full touch-none select-none overflow-hidden rounded-lg border border-line bg-bg">
      <canvas ref={canvas} className="absolute inset-0 size-full" />
      {videoUrl && (
        <video ref={video} src={videoUrl} muted loop playsInline autoPlay preload="auto"
          className="pointer-events-none absolute left-0 top-0 size-px opacity-0" />
      )}
      {noGl && <p className="absolute inset-x-0 top-2 text-center text-xs text-mute">WebGL not available: preview off. Handles still work.</p>}
      <svg className="pointer-events-none absolute inset-0 size-full" viewBox={`${-M} ${-M} ${SPAN} ${SPAN}`} preserveAspectRatio="none">
        <polygon points={quad.map(([x, y]) => `${x},${y}`).join(' ')} fill="none" stroke="#b49cff" strokeWidth={0.004} strokeDasharray="0.015 0.01" vectorEffect="non-scaling-stroke" />
      </svg>
      {quad.map(([x, y], i) => (
        <button key={i} type="button" aria-label={`Corner ${LABELS[i]}`}
          title={`Corner ${LABELS[i]}: drag, or focus and use arrow keys (Shift = fine)`}
          className={`absolute flex size-8 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full border-2 text-sm font-bold shadow outline-none focus-visible:ring-2 focus-visible:ring-ghost ${drag === i ? 'border-white bg-pumpkin text-black' : 'border-pumpkin bg-bg/80 text-pumpkin'}`}
          style={{ left: `${toPct(x)}%`, top: `${toPct(y)}%` }}
          onPointerDown={onDown(i)} onPointerMove={onMove(i)}
          onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}
          onKeyDown={onKey(i)}>
          {LABELS[i]}
        </button>
      ))}
    </div>
  )
}
