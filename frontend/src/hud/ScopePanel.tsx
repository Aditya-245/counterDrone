/**
 * 2D radar scope (plan view) and the EO/IR camera view.
 *
 * Both are plain SVG so they stay crisp at any panel size and cost almost
 * nothing on integrated graphics.
 */

import { useMemo } from "react"
import type { Scenario, SensorHealthView, TrackView } from "@/api/types"
import { LABEL_COLOURS, formatBearing, formatRange } from "@/api/format"

const VIEW_RADIUS_M = 5000

export function RadarScope({
  scenario,
  tracks,
  sensor,
  selectedId,
  onSelect,
}: {
  scenario: Scenario
  tracks: TrackView[]
  sensor: SensorHealthView | undefined
  selectedId: string | null
  onSelect: (id: string) => void
}): JSX.Element {
  const size = 220
  const c = size / 2
  const rings = scenario.site.ring_distances_m
  const protectedRadius = scenario.site.protected_radius_m

  const scale = c / VIEW_RADIUS_M

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="Radar plan view"
      style={{ width: "100%", height: "100%", background: "#050b0d" }}
    >
      <defs>
        <radialGradient id="scope-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#0d2b2a" />
          <stop offset="100%" stopColor="#040a0c" />
        </radialGradient>
      </defs>
      <circle cx={c} cy={c} r={c} fill="url(#scope-glow)" />

      {rings.map((r) => (
        <circle key={r} cx={c} cy={c} r={r * scale} fill="none" stroke="#123" strokeWidth={0.8} />
      ))}
      <circle
        cx={c}
        cy={c}
        r={protectedRadius * scale}
        fill="none"
        stroke="#f2555a"
        strokeWidth={1}
      />

      {scenario.site.civil_sectors.map((s) => {
        const a0 = ((s.bearing_deg - s.half_width_deg) * Math.PI) / 180
        const a1 = ((s.bearing_deg + s.half_width_deg) * Math.PI) / 180
        const r = s.outer_m * scale
        const x0 = c + Math.cos(a0) * r
        const y0 = c - Math.sin(a0) * r
        const x1 = c + Math.cos(a1) * r
        const y1 = c - Math.sin(a1) * r
        return (
          <path
            key={s.label}
            d={`M ${c} ${c} L ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1} Z`}
            fill="#fbbf24"
            fillOpacity={0.12}
          />
        )
      })}

      {/* cross hairs + bearing ticks */}
      <line x1={c} y1={4} x2={c} y2={size - 4} stroke="#123" strokeWidth={0.6} />
      <line x1={4} y1={c} x2={size - 4} y2={c} stroke="#123" strokeWidth={0.6} />
      {Array.from({ length: 12 }, (_, i) => {
        const a = (i * 30 * Math.PI) / 180
        return (
          <text
            key={i}
            x={c + Math.cos(a) * (c - 9)}
            y={c - Math.sin(a) * (c - 9) + 3}
            fill="#5c7185"
            fontSize={7}
            textAnchor="middle"
          >
            {i * 30}
          </text>
        )
      })}

      {sensor && sensor.fov_deg < 359.9 && (
        <SectorWedge
          centre={sensor.azimuth_deg}
          half={sensor.fov_deg / 2}
          cx={c}
          cy={c}
          r={VIEW_RADIUS_M * scale}
        />
      )}

      {tracks.map((trk) => {
        const r = Math.min(Math.hypot(trk.x, trk.y), VIEW_RADIUS_M) * scale
        const a = (Math.atan2(trk.y, trk.x) * 180) / Math.PI
        const x = c + Math.cos((a * Math.PI) / 180) * r
        const y = c - Math.sin((a * Math.PI) / 180) * r
        const label = trk.trainee_class ?? trk.suggested_label ?? "unknown"
        const selected = trk.id === selectedId
        return (
          <g
            key={trk.id}
            onClick={() => onSelect(trk.id)}
            style={{ cursor: "pointer" }}
            data-testid={`scope-track-${trk.id}`}
          >
            <circle
              cx={x}
              cy={y}
              r={selected ? 5 : 3.2}
              fill={LABEL_COLOURS[label] ?? "#64748b"}
              stroke={selected ? "#fff" : "#040a0c"}
              strokeWidth={selected ? 1.4 : 0.6}
            />
            {selected && (
              <circle cx={x} cy={y} r={9} fill="none" stroke="#38bdf8" strokeWidth={0.8} />
            )}
          </g>
        )
      })}
    </svg>
  )
}

function SectorWedge({
  centre,
  half,
  cx,
  cy,
  r,
}: {
  centre: number
  half: number
  cx: number
  cy: number
  r: number
}): JSX.Element {
  const a0 = ((centre - half) * Math.PI) / 180
  const a1 = ((centre + half) * Math.PI) / 180
  const x0 = cx + Math.cos(a0) * r
  const y0 = cy - Math.sin(a0) * r
  const x1 = cx + Math.cos(a1) * r
  const y1 = cy - Math.sin(a1) * r
  return (
    <path
      d={`M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1} Z`}
      fill="#38bdf8"
      fillOpacity={0.07}
      stroke="#38bdf8"
      strokeOpacity={0.25}
      strokeWidth={0.6}
    />
  )
}

export function CameraView({
  scenario,
  tracks,
  sensor,
  selectedId,
  onSelect,
}: {
  scenario: Scenario
  tracks: TrackView[]
  sensor: SensorHealthView | undefined
  selectedId: string | null
  onSelect: (id: string) => void
}): JSX.Element {
  const w = 300
  const h = 190
  const night = scenario.environment.time_of_day === "night"
  const fov = sensor?.fov_deg ?? 70
  const centre = sensor?.azimuth_deg ?? 0
  const sensorState = sensor?.state ?? "none"
  const maxRange = useMemo(() => {
    const eo = scenario.sensors.find((s) => s.type === "eo_ir")
    return eo?.max_range_m ?? 1500
  }, [scenario])

  const visible = useMemo(() => {
    const half = fov / 2
    return tracks
      .filter((t) => {
        if (t.range_m > maxRange) return false
        if (fov >= 359.9) return true
        const delta = ((((t.bearing_deg - centre + 180) % 360) + 360) % 360) - 180
        return Math.abs(delta) <= half
      })
      .sort((a, b) => a.range_m - b.range_m)
      .slice(0, 40)
  }, [tracks, fov, centre, maxRange])

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        background: night ? "#050608" : "#1a2230",
      }}
    >
      <svg
        viewBox={`0 0 ${w} ${h}`}
        style={{ width: "100%", height: "100%" }}
        role="img"
        aria-label="EO/IR camera view"
      >
        {Array.from({ length: 8 }, (_, i) => (
          <line
            key={`h${i}`}
            x1={0}
            y1={(h / 8) * i}
            x2={w}
            y2={(h / 8) * i}
            stroke="#ffffff"
            strokeOpacity={0.05}
          />
        ))}
        {Array.from({ length: 10 }, (_, i) => (
          <line
            key={`v${i}`}
            x1={(w / 10) * i}
            y1={0}
            x2={(w / 10) * i}
            y2={h}
            stroke="#ffffff"
            strokeOpacity={0.05}
          />
        ))}
        <rect x={0.5} y={0.5} width={w - 1} height={h - 1} fill="none" stroke="#1e2f3d" />
        <text x={6} y={14} fill="#8ba3b8" fontSize={9} fontFamily="monospace">
          EO/IR · {fov.toFixed(0)}° FOV · az {centre.toFixed(0)}°
        </text>
        {sensorState !== "none" && (
          <text x={6} y={h - 8} fill="#f59e0b" fontSize={10} fontFamily="monospace">
            {sensorState.toUpperCase()}
          </text>
        )}

        {visible.map((trk) => {
          const delta = ((((trk.bearing_deg - centre + 180) % 360) + 360) % 360) - 180
          const px = w / 2 + (delta / fov) * w
          const py = h / 2 - ((trk.altitude_m - 40) / 300) * h
          if (px < 0 || px > w || py < 0 || py > h) return null
          const size = Math.max(3, 14 / Math.max(1, trk.range_m / 300))
          const label = trk.trainee_class ?? trk.suggested_label ?? "unknown"
          return (
            <g
              key={trk.id}
              onClick={() => onSelect(trk.id)}
              style={{ cursor: "pointer" }}
              data-testid={`camera-track-${trk.id}`}
            >
              <rect
                x={px - size / 2}
                y={py - size / 2}
                width={size}
                height={size}
                fill="none"
                stroke={LABEL_COLOURS[label] ?? "#64748b"}
                strokeWidth={trk.id === selectedId ? 2 : 1}
              />
              {trk.id === selectedId && (
                <text x={px + size} y={py - 3} fill="#fff" fontSize={8} fontFamily="monospace">
                  {trk.id} {formatRange(trk.range_m)} {formatBearing(trk.bearing_deg)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
