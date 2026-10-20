/**
 * Procedural textures, painted on a canvas at start-up.
 *
 * The project is offline-first, so nothing is downloaded: every surface is drawn
 * from noise and rectangles at runtime. Textures are cached by key because the
 * same facade or roof is reused by every instance of a building archetype.
 */

import * as THREE from "three"

const cache = new Map<string, THREE.Texture>()

/** Deterministic value noise in [0, 1], so a texture never changes between runs. */
function noise2(x: number, y: number, seed: number): number {
  const n = Math.sin(x * 12.9898 + y * 78.233 + seed * 37.719) * 43758.5453
  return n - Math.floor(n)
}

function grain(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  amount: number,
  seed: number,
  cell = 1,
): void {
  const image = ctx.getImageData(0, 0, width, height)
  const data = image.data
  for (let y = 0; y < height; y += cell) {
    for (let x = 0; x < width; x += cell) {
      const n = (noise2(Math.floor(x / cell), Math.floor(y / cell), seed) - 0.5) * amount
      const i = (y * width + x) * 4
      const r = data[i] ?? 0
      const g = data[i + 1] ?? 0
      const b = data[i + 2] ?? 0
      data[i] = Math.max(0, Math.min(255, r + n))
      data[i + 1] = Math.max(0, Math.min(255, g + n))
      data[i + 2] = Math.max(0, Math.min(255, b + n))
    }
  }
  ctx.putImageData(image, 0, 0)
}

function canvas(width: number, height: number): CanvasRenderingContext2D {
  const element = document.createElement("canvas")
  element.width = width
  element.height = height
  const ctx = element.getContext("2d")
  if (!ctx) throw new Error("2D canvas is unavailable; the scene cannot paint its textures")
  return ctx
}

function finish(ctx: CanvasRenderingContext2D, repeat = 1): THREE.CanvasTexture {
  const texture = new THREE.CanvasTexture(ctx.canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(repeat, repeat)
  texture.anisotropy = 4
  texture.needsUpdate = true
  return texture
}

/** Wall palettes per archetype: muted, believable, never saturated. */
export const WALL_COLOURS: Record<string, string[]> = {
  house_gable: ["#cfc4b0", "#c2b49c", "#d6cdba", "#b9ac95"],
  house_hip: ["#d2c8b6", "#bfb49f", "#c9bda6", "#dcd3c2"],
  house_flat: ["#c6c2ba", "#b4b0a8", "#cfcbc2", "#a8a49c"],
  shop: ["#b9b2a6", "#c4bdae", "#ada79b", "#cfc8b9"],
  apartment: ["#c0bcb2", "#b2aea4", "#cbc7bd", "#a6a29a"],
  warehouse: ["#a9aeb0", "#9aa1a4", "#b4b8b8", "#8f9799"],
  shed: ["#9c9284", "#a89c8c", "#8e8578", "#b3a795"],
  tower: ["#b8bcc0", "#adb2b6", "#c4c7ca", "#a3a8ac"],
}

const ROOF_COLOURS: Record<string, string> = {
  house_gable: "#7a4b41",
  house_hip: "#6f5a49",
  house_flat: "#5f6360",
  shop: "#6c5b52",
  apartment: "#5d6165",
  warehouse: "#7b8083",
  shed: "#6d6357",
  tower: "#4f5357",
}

/**
 * One texture per archetype: walls with windows below, roof covering above.
 *
 * Buildings map the wall faces into the lower part of the sheet and the roof into
 * the upper strip, so a whole archetype is a single draw call.
 */
export function facadeTexture(kind: string): THREE.Texture {
  const key = `facade:${kind}`
  const hit = cache.get(key)
  if (hit) return hit
  const ctx = canvas(256, 256)
  const walls = WALL_COLOURS[kind] ?? WALL_COLOURS["house_gable"] ?? ["#cfc4b0"]
  const wall = walls[Math.floor(noise2(3, 7, 1) * walls.length)] ?? "#cfc4b0"
  ctx.fillStyle = wall
  ctx.fillRect(0, 0, 256, 256)
  grain(ctx, 256, 256, 26, 3)

  // Brick or panel coursing.
  ctx.strokeStyle = "rgba(0,0,0,0.10)"
  ctx.lineWidth = 1
  for (let y = 0; y < 256; y += 16) {
    ctx.beginPath()
    ctx.moveTo(0, y + 0.5)
    ctx.lineTo(256, y + 0.5)
    ctx.stroke()
  }

  // Windows: 4 columns, sized by archetype.
  const floors = kind === "tower" || kind === "apartment" ? 4 : kind === "warehouse" ? 1 : 3
  const columns = kind === "warehouse" ? 2 : kind === "tower" ? 6 : 4
  const top = 14
  const height = 242 - top
  const floorH = height / floors
  const winW = 256 / columns
  for (let f = 0; f < floors; f += 1) {
    for (let c = 0; c < columns; c += 1) {
      const pad = winW * (kind === "warehouse" ? 0.3 : 0.26)
      const x = c * winW + pad
      const y = top + f * floorH + floorH * 0.22
      const w = winW - pad * 2
      const h = floorH * (kind === "warehouse" ? 0.62 : 0.5)
      ctx.fillStyle = "#2b3138"
      ctx.fillRect(x, y, w, h)
      // A sliver of sky reflection keeps glass from reading as a black hole.
      ctx.fillStyle = "rgba(160,190,210,0.35)"
      ctx.fillRect(x + 1, y + 1, w - 2, h * 0.35)
      ctx.strokeStyle = "rgba(255,255,255,0.22)"
      ctx.lineWidth = 2
      ctx.strokeRect(x, y, w, h)
    }
  }
  // Door on the ground floor of small buildings.
  if (kind === "house_gable" || kind === "house_hip" || kind === "house_flat" || kind === "shed") {
    ctx.fillStyle = "#4a3a2c"
    ctx.fillRect(256 / 2 - 12, 242 - 44, 24, 44)
  }

  // Roof strip across the top of the sheet.
  ctx.fillStyle = ROOF_COLOURS[kind] ?? "#6f5a49"
  ctx.fillRect(0, 0, 256, top)
  if (kind === "warehouse" || kind === "shed" || kind === "tower") {
    ctx.strokeStyle = "rgba(0,0,0,0.16)"
    for (let x = 0; x < 256; x += 8) {
      ctx.beginPath()
      ctx.moveTo(x + 0.5, 0)
      ctx.lineTo(x + 0.5, top)
      ctx.stroke()
    }
  } else {
    ctx.strokeStyle = "rgba(0,0,0,0.22)"
    for (let y = 0; y < top; y += 6) {
      ctx.beginPath()
      ctx.moveTo(0, y + 0.5)
      ctx.lineTo(256, y + 0.5)
      ctx.stroke()
    }
  }
  const texture = finish(ctx)
  cache.set(key, texture)
  return texture
}

/** Dark asphalt with a little wear, used for the road ribbons. */
export function roadTexture(): THREE.Texture {
  const key = "road"
  const hit = cache.get(key)
  if (hit) return hit
  const ctx = canvas(128, 128)
  ctx.fillStyle = "#31312f"
  ctx.fillRect(0, 0, 128, 128)
  grain(ctx, 128, 128, 34, 11)
  ctx.fillStyle = "rgba(255,255,255,0.03)"
  for (let i = 0; i < 40; i += 1) {
    const x = noise2(i, 1, 5) * 128
    const y = noise2(i, 2, 6) * 128
    ctx.fillRect(x, y, 2 + noise2(i, 3, 7) * 10, 2)
  }
  const texture = finish(ctx, 1)
  cache.set(key, texture)
  return texture
}

/** Gravel for farm tracks and yards. */
export function gravelTexture(): THREE.Texture {
  const key = "gravel"
  const hit = cache.get(key)
  if (hit) return hit
  const ctx = canvas(128, 128)
  ctx.fillStyle = "#8b8378"
  ctx.fillRect(0, 0, 128, 128)
  grain(ctx, 128, 128, 46, 17, 2)
  const texture = finish(ctx, 1)
  cache.set(key, texture)
  return texture
}

/** Foliage: a mottled green used on tree canopies and hedges. */
export function foliageTexture(): THREE.Texture {
  const key = "foliage"
  const hit = cache.get(key)
  if (hit) return hit
  const ctx = canvas(64, 64)
  ctx.fillStyle = "#6f9a55"
  ctx.fillRect(0, 0, 64, 64)
  grain(ctx, 64, 64, 60, 23)
  const texture = finish(ctx, 1)
  cache.set(key, texture)
  return texture
}

/** Sky gradient used as the scene environment (PBR reflections). */
export function skyGradient(top: string, horizon: string, ground: string): THREE.Texture {
  const key = `sky:${top}:${horizon}:${ground}`
  const hit = cache.get(key)
  if (hit) return hit
  const ctx = canvas(64, 256)
  const gradient = ctx.createLinearGradient(0, 0, 0, 256)
  gradient.addColorStop(0, top)
  gradient.addColorStop(0.52, horizon)
  gradient.addColorStop(0.62, ground)
  gradient.addColorStop(1, ground)
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 64, 256)
  const texture = new THREE.CanvasTexture(ctx.canvas)
  texture.mapping = THREE.EquirectangularReflectionMapping
  texture.colorSpace = THREE.SRGBColorSpace
  texture.needsUpdate = true
  cache.set(key, texture)
  return texture
}
