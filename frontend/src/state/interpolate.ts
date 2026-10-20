/**
 * Interpolate between two authoritative frames.
 *
 * The engine runs at 10 Hz; the renderer runs at display rate. Without this the
 * whole scene would visibly step. `alpha` is in [0, 1] between `a` and `b`.
 */

import type { StateFrame } from "@/api/types"

export function interpolateTracks(
  a: StateFrame,
  b: StateFrame,
  alpha: number,
): StateFrame["tracks"] {
  const t = Math.min(1, Math.max(0, alpha))
  const byId = new Map(b.tracks.map((tr) => [tr.id, tr]))
  return a.tracks.map((prev) => {
    const next = byId.get(prev.id)
    if (!next) return prev
    return {
      ...next,
      x: prev.x + (next.x - prev.x) * t,
      y: prev.y + (next.y - prev.y) * t,
      z: prev.z + (next.z - prev.z) * t,
      // Everything except position comes from the newer frame: the engine is
      // the authority for status, quality and suggested class.
    }
  })
}
