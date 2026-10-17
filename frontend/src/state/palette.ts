/**
 * Theme-aware colours for Recharts.
 *
 * SVG presentation attributes do not resolve CSS custom properties, so charts
 * need concrete colour strings. This reads the live values off `:root` and
 * re-reads them whenever the theme flips.
 */

import { useMemo } from "react"
import { useSimStore } from "@/state/simStore"

export interface ChartPalette {
  grid: string
  axis: string
  tick: string
  series: string[]
}

function token(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

export function readChartPalette(): ChartPalette {
  return {
    grid: token("--border", "#333a2f"),
    axis: token("--text-faint", "#697565"),
    tick: token("--text-dim", "#a3ac9b"),
    series: [
      token("--accent", "#ecdfcc"),
      token("--ok", "#9ec47a"),
      token("--warn", "#d9b26a"),
      token("--bird", "#b8a8d4"),
    ],
  }
}

export function useChartPalette(): ChartPalette {
  const theme = useSimStore((s) => s.theme)
  // The colours come from CSS custom properties, not from `theme` directly, so
  // the linter cannot see that the theme attribute is what invalidates them.
  // It is the real cache key: without it the charts keep the previous theme's
  // colours after a toggle.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readChartPalette(), [theme])
}
