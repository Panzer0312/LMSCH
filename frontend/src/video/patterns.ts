/**
 * Browser stand-ins for the projector patterns. The hub renders the real ones; the
 * preview only needs something recognisable to show the transform on.
 */
export const PATTERN_W = 960
export const PATTERN_H = 540

// Mirrors the 'video' kinds in backend show/patterns.py; their real file is an mp4, not drawable here.
const VIDEO_PATTERNS = new Set(['edge_chase', 'scan_h', 'scan_v'])

export const patternFileUrl = (id: string) => `/api/display/patterns/${encodeURIComponent(id)}/file`

/**
 * Draws the stand-in at once, then replaces it with the hub's real render for image patterns.
 * Resolves true when the real image was drawn (caller should re-upload the canvas).
 */
export function loadPattern(id: string, cv: HTMLCanvasElement): Promise<boolean> {
  drawPattern(id, cv)
  if (VIDEO_PATTERNS.has(id)) return Promise.resolve(false)
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      const c = cv.getContext('2d')
      if (!c) { resolve(false); return }
      c.drawImage(img, 0, 0, cv.width, cv.height)
      resolve(true)
    }
    img.onerror = () => resolve(false)
    img.src = patternFileUrl(id)
  })
}

function grid(c: CanvasRenderingContext2D, W: number, H: number) {
  c.fillStyle = '#000'
  c.fillRect(0, 0, W, H)
  c.strokeStyle = '#fff'
  c.lineWidth = 1
  for (let i = 0; i <= 16; i++) {
    const x = Math.min(W - 0.5, (i * W) / 16 + 0.5)
    c.beginPath(); c.moveTo(x, 0); c.lineTo(x, H); c.stroke()
  }
  for (let i = 0; i <= 9; i++) {
    const y = Math.min(H - 0.5, (i * H) / 9 + 0.5)
    c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke()
  }
  c.strokeStyle = '#ff7a1a'
  c.lineWidth = 2
  c.beginPath(); c.moveTo(W / 2, 0); c.lineTo(W / 2, H); c.moveTo(0, H / 2); c.lineTo(W, H / 2); c.stroke()
}

export function drawPattern(id: string, cv: HTMLCanvasElement) {
  const W = (cv.width = PATTERN_W)
  const H = (cv.height = PATTERN_H)
  const c = cv.getContext('2d')
  if (!c) return
  c.fillStyle = '#000'
  c.fillRect(0, 0, W, H)
  c.strokeStyle = '#fff'
  c.fillStyle = '#fff'
  switch (id) {
    case 'corners': {
      c.lineWidth = 6
      c.strokeRect(3, 3, W - 6, H - 6)
      c.font = 'bold 90px sans-serif'
      c.textBaseline = 'middle'
      c.textAlign = 'center'
      const pts: [number, number][] = [[70, 70], [W - 70, 70], [W - 70, H - 70], [70, H - 70]]
      pts.forEach(([x, y], i) => c.fillText(String(i + 1), x, y))
      break
    }
    case 'circles': {
      c.lineWidth = 2
      for (let r = 40; r < H / 2; r += 40) { c.beginPath(); c.arc(W / 2, H / 2, r, 0, Math.PI * 2); c.stroke() }
      c.beginPath(); c.moveTo(W / 2, 0); c.lineTo(W / 2, H); c.moveTo(0, H / 2); c.lineTo(W, H / 2); c.stroke()
      break
    }
    case 'edge_chase':
      c.fillStyle = '#ff7a1a'
      c.fillRect(0, 0, W, 12); c.fillRect(0, H - 12, W, 12); c.fillRect(0, 0, 12, H); c.fillRect(W - 12, 0, 12, H)
      break
    case 'scan_h':
      c.fillRect(0, H / 2 - 3, W, 6)
      break
    case 'scan_v':
      c.fillRect(W / 2 - 3, 0, 6, H)
      break
    case 'focus': {
      c.lineWidth = 1
      for (let a = 0; a < 180; a += 6) {
        const r = (a * Math.PI) / 180
        c.beginPath()
        c.moveTo(W / 2 - Math.cos(r) * 240, H / 2 - Math.sin(r) * 240)
        c.lineTo(W / 2 + Math.cos(r) * 240, H / 2 + Math.sin(r) * 240)
        c.stroke()
      }
      break
    }
    case 'grey_ramp': {
      for (let i = 0; i < 32; i++) {
        const v = Math.round((i / 31) * 255)
        c.fillStyle = `rgb(${v},${v},${v})`
        c.fillRect((i * W) / 32, 0, W / 32 + 1, H / 2)
      }
      for (let i = 0; i < 8; i++) {
        const v = i * 3
        c.fillStyle = `rgb(${v},${v},${v})`
        c.fillRect((i * W) / 8, H / 2, W / 8 + 1, H / 2)
      }
      break
    }
    case 'checker': {
      const s = H / 9
      for (let y = 0; y < 9; y++) for (let x = 0; x < 16; x++) if ((x + y) % 2 === 0) c.fillRect(x * s, y * s, s, s)
      break
    }
    case 'white':
      c.fillRect(0, 0, W, H)
      break
    case 'black':
      break
    case 'safe_area':
      c.fillStyle = '#808080'
      c.fillRect(0, 0, W, H)
      break
    default:
      grid(c, W, H)
  }
}
