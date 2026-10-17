/** Small shared formatting helpers used across the HUD and charts. */

import type { DegradationState, Verdict } from "./types"

/** Label colours; the machine's `unknown` has no label type of its own. */
export const LABEL_COLOURS: Record<string, string> = {
  hostile: "#f62440",
  friendly: "#9ec47a",
  civil: "#d9b26a",
  bird: "#b8a8d4",
  unknown: "#697565",
}

export const VERDICT_COLOURS: Record<Verdict, string> = {
  CORRECT_ENGAGE: "#9ec47a",
  CORRECT_HOLD: "#9ec47a",
  LATE_ENGAGE: "#d9b26a",
  MISSED_THREAT: "#f62440",
  FALSE_ENGAGE_CIVIL: "#f62440",
  FALSE_ENGAGE_FRIENDLY: "#f62440",
  COLLATERAL_VIOLATION: "#f62440",
  PREMATURE_ENGAGE: "#e0763c",
  UNNECESSARY_ESCALATION: "#e0763c",
}

export const SECTOR_NAMES = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"] as const

export function formatClock(seconds: number): string {
  const s = Math.max(0, seconds)
  const m = Math.floor(s / 60)
  const rest = s - m * 60
  return `${String(m).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`
}

export function formatRange(metres: number): string {
  if (metres >= 1000) return `${(metres / 1000).toFixed(2)} km`
  return `${Math.round(metres)} m`
}

export function formatBearing(deg: number): string {
  return `${String(Math.round(((deg % 360) + 360) % 360)).padStart(3, "0")}°`
}

export function sensorStateClass(state: DegradationState): string {
  return state === "none" ? "" : state
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

export function fmtPercent(v: number, digits = 0): string {
  return `${(v * 100).toFixed(digits)}%`
}

export function compassName(sector: number): string {
  return SECTOR_NAMES[((sector % 8) + 8) % 8] ?? "?"
}

/** [fraction, red, green, blue] - sage -> sand -> signal red. */
const HEAT_STOPS: readonly (readonly [number, number, number, number])[] = [
  [0, 105, 117, 101], // #697565 sage - barely attended
  [0.5, 217, 178, 106], // #d9b26a sand
  [1, 246, 36, 64], // #f62440 signal - fixated on
]

/** Heat colour for the sector-attention strip: sage -> sand -> signal red. */
export function heatColour(fraction: number): string {
  const f = clamp(fraction, 0, 1)
  if (f <= 0) return "var(--bg)"
  for (let i = 1; i < HEAT_STOPS.length; i += 1) {
    const cur = HEAT_STOPS[i]
    const prev = HEAT_STOPS[i - 1]
    if (!cur || !prev || f > cur[0]) continue
    const t = (f - prev[0]) / (cur[0] - prev[0])
    const r = Math.round(prev[1] + (cur[1] - prev[1]) * t)
    const g = Math.round(prev[2] + (cur[2] - prev[2]) * t)
    const b = Math.round(prev[3] + (cur[3] - prev[3]) * t)
    return `rgb(${r} ${g} ${b})`
  }
  const last = HEAT_STOPS[HEAT_STOPS.length - 1] ?? [1, 246, 36, 64]
  return `rgb(${last[1]} ${last[2]} ${last[3]})`
}
