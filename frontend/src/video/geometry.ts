import type { DisplayGeometry } from '../types'

export const IDENTITY: DisplayGeometry = {
  zoom: 0, pan_x: 0, pan_y: 0, scale_x: 1, scale_y: 1, align_x: 0, align_y: 0, rotate: 0, aspect: null,
  crop: { x: 0, y: 0, w: 1, h: 1 },
  brightness: 0, contrast: 0, gamma: 0, saturation: 0,
  corners: [[0, 0], [0, 0], [0, 0], [0, 0]],
  mask: { top: 0, right: 0, bottom: 0, left: 0, feather: 0 },
  flip_h: false, flip_v: false,
}

export const OUTPUT_ASPECT = 16 / 9

/** Fill gaps so partial/older payloads never crash the editor. */
export function normalize(g: Partial<DisplayGeometry> | null | undefined): DisplayGeometry {
  const c = g?.corners
  return {
    ...IDENTITY,
    ...(g ?? {}),
    crop: { ...IDENTITY.crop, ...(g?.crop ?? {}) },
    mask: { ...IDENTITY.mask, ...(g?.mask ?? {}) },
    corners: [0, 1, 2, 3].map((i) => [c?.[i]?.[0] ?? 0, c?.[i]?.[1] ?? 0] as [number, number]),
  }
}

export function sameGeometry(a: DisplayGeometry, b: DisplayGeometry): boolean {
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b))
}

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export type Group = 'frame' | 'keystone' | 'mask' | 'image'

/** Reset one control group back to identity, keeping the others. */
export function resetGroup(g: DisplayGeometry, group: Group): DisplayGeometry {
  switch (group) {
    case 'frame':
      return { ...IDENTITY, corners: g.corners, mask: g.mask, brightness: g.brightness, contrast: g.contrast, gamma: g.gamma, saturation: g.saturation }
    case 'keystone':
      return { ...g, corners: IDENTITY.corners }
    case 'mask':
      return { ...g, mask: IDENTITY.mask }
    case 'image':
      return { ...g, brightness: 0, contrast: 0, gamma: 0, saturation: 0 }
  }
}

/** 3×3 row-major matrix. */
export type Mat3 = [number, number, number, number, number, number, number, number, number]

/** Unit-square → quad projective map (Heckbert). Quad order TL, TR, BR, BL. */
export function squareToQuad(q: [number, number][]): Mat3 {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q
  const dx3 = x0 - x1 + x2 - x3
  const dy3 = y0 - y1 + y2 - y3
  if (Math.abs(dx3) < 1e-12 && Math.abs(dy3) < 1e-12) {
    return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0, 0, 1]
  }
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2
  const den = dx1 * dy2 - dx2 * dy1
  const g = (dx3 * dy2 - dx2 * dy3) / den
  const h = (dx1 * dy3 - dx3 * dy1) / den
  return [
    x1 - x0 + g * x1, x3 - x0 + h * x3, x0,
    y1 - y0 + g * y1, y3 - y0 + h * y3, y0,
    g, h, 1,
  ]
}

export function invert3(m: Mat3): Mat3 | null {
  const [a, b, c, d, e, f, g, h, i] = m
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g
  const det = a * A + b * B + c * C
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null
  const k = 1 / det
  return [
    A * k, -(b * i - c * h) * k, (b * f - c * e) * k,
    B * k, (a * i - c * g) * k, -(a * f - c * d) * k,
    C * k, -(a * h - b * g) * k, (a * e - b * d) * k,
  ]
}

export function apply3(m: Mat3, x: number, y: number): [number, number] {
  const w = m[6] * x + m[7] * y + m[8]
  return [(m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w]
}

const BASE: [number, number][] = [[0, 0], [1, 0], [1, 1], [0, 1]]

/** Output-space quad (y down, 0..1) the warped frame lands in. */
export function warpQuad(g: DisplayGeometry): [number, number][] {
  return BASE.map(([x, y], i) => [x + (g.corners[i]?.[0] ?? 0), y + (g.corners[i]?.[1] ?? 0)])
}

/** Inverse warp: output point → position in the unwarped frame. Identity when degenerate. */
export function inverseWarp(g: DisplayGeometry): Mat3 {
  return invert3(squareToQuad(warpQuad(g))) ?? [1, 0, 0, 0, 1, 0, 0, 0, 1]
}

/** Column-major Float32Array for gl.uniformMatrix3fv(…, false, …). */
export function toGl(m: Mat3): Float32Array {
  return new Float32Array([m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]])
}

/**
 * Where the (cropped, rotated) source sits inside the unwarped output, in output fractions.
 * Mimics mpv: fit inside, zoom/scale, align, then pan in units of the scaled video size.
 */
export function videoRect(g: DisplayGeometry, srcW: number, srcH: number): { x: number; y: number; w: number; h: number } {
  const cw = Math.max(1, g.crop.w * srcW)
  const ch = Math.max(1, g.crop.h * srcH)
  const rot = g.rotate === 90 || g.rotate === 270
  let a = g.aspect ?? cw / ch
  if (rot) a = 1 / a
  let w = 1, h = 1
  if (a > OUTPUT_ASPECT) h = OUTPUT_ASPECT / a
  else w = a / OUTPUT_ASPECT
  const z = 2 ** g.zoom
  w *= z * g.scale_x
  h *= z * g.scale_y
  const x = ((1 - w) * (g.align_x + 1)) / 2 + g.pan_x * w
  const y = ((1 - h) * (g.align_y + 1)) / 2 + g.pan_y * h
  return { x, y, w, h }
}
