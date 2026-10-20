/**
 * Decoding and sampling of the generated height/land-cover grids.
 *
 * Kept out of the component file so that file only exports components, and shared
 * with the camera so the view can be clamped to the ground the simulation uses.
 */

import * as THREE from "three"
import type { LandscapeSpec } from "@/api/types"

export interface LandscapeGeometry {
  heights: Float32Array
  gridN: number
  sizeM: number
  /** Ground height in metres at world (x, y); the simulation's y is the scene's z. */
  heightAt: (x: number, y: number) => number
}

/** Decode the base64 int16 centimetre height grid into metres. */
export function decodeHeights(spec: LandscapeSpec): Float32Array {
  const binary = atob(spec.heights_b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  const view = new DataView(bytes.buffer)
  const count = spec.grid_n * spec.grid_n
  const out = new Float32Array(count)
  for (let i = 0; i < count; i += 1) out[i] = view.getInt16(i * 2, true) / 100
  return out
}

/** Decode the uint8 land-cover grid. */
export function decodeCover(spec: LandscapeSpec): Uint8Array {
  const binary = atob(spec.landcover_b64)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i)
  return out
}

/** Build the sampling helper for one landscape payload. */
export function landscapeGeometry(spec: LandscapeSpec): LandscapeGeometry {
  const heights = decodeHeights(spec)
  const gridN = spec.grid_n
  const sizeM = spec.size_m
  const half = sizeM / 2
  const step = sizeM / (gridN - 1)
  const heightAt = (x: number, y: number): number => {
    const gx = THREE.MathUtils.clamp((x + half) / step, 0, gridN - 1.001)
    const gy = THREE.MathUtils.clamp((y + half) / step, 0, gridN - 1.001)
    const i0 = Math.floor(gx)
    const j0 = Math.floor(gy)
    const fx = gx - i0
    const fy = gy - j0
    const i1 = Math.min(i0 + 1, gridN - 1)
    const j1 = Math.min(j0 + 1, gridN - 1)
    const h00 = heights[i0 + j0 * gridN] ?? 0
    const h10 = heights[i1 + j0 * gridN] ?? h00
    const h01 = heights[i0 + j1 * gridN] ?? h00
    const h11 = heights[i1 + j1 * gridN] ?? h00
    const top = h00 + (h10 - h00) * fx
    const bottom = h01 + (h11 - h01) * fx
    return top + (bottom - top) * fy
  }
  return { heights, gridN, sizeM, heightAt }
}
