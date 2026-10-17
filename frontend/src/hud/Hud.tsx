/**
 * The operator HUD: mission clock, ROE reminder, sensor health strip, effector
 * status, selected-track readout and the keyboard cheat-sheet.
 */

import type { Scenario, StateFrame, TrackView } from "@/api/types"
import { formatBearing, formatRange } from "@/api/format"
import { LabelBadge } from "@/components/ui"

export function MissionClock({
  frame,
  paused,
}: {
  frame: StateFrame | null
  paused: boolean
}): JSX.Element {
  const t = frame?.t ?? 0
  const duration = frame?.duration_s ?? 0
  const mm = Math.floor(t / 60)
  const ss = t - mm * 60
  const remain = Math.max(0, duration - t)
  return (
    <div className="row">
      <span className="clock" data-testid="mission-clock">
        {String(mm).padStart(2, "0")}:{ss.toFixed(1).padStart(4, "0")}
      </span>
      <span className="faint mono">T-{formatRange(remain)}</span>
      {paused && <span className="badge warn">PAUSED</span>}
    </div>
  )
}

export function RoeReminder({ scenario }: { scenario: Scenario }): JSX.Element {
  const rules: string[] = []
  rules.push(
    scenario.roe.require_id_before_engage
      ? "positive ID required before any engagement"
      : "hostile assessment is sufficient to engage",
  )
  if (scenario.roe.require_warning_before_engage) rules.push("warning first")
  if (scenario.roe.collateral_sector_tolerance <= 0 && scenario.site.civil_sectors.length > 0) {
    rules.push("ZERO collateral tolerance")
  }
  rules.push(`authorised: ${scenario.roe.allowed_engage_labels.join("/")}`)
  return (
    <div className="mono" style={{ fontSize: 12 }}>
      <strong style={{ color: "var(--accent)" }}>ROE {scenario.roe.rule_set}</strong>
      <ul style={{ margin: "3px 0 0", paddingLeft: 16 }}>
        {rules.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </div>
  )
}

export function SensorStrip({ frame }: { frame: StateFrame | null }): JSX.Element {
  const sensors = frame?.sensors ?? []
  return (
    <div className="sensor-strip" data-testid="sensor-strip">
      {sensors.map((s) => (
        <span
          key={s.id}
          className={`sensor-chip ${s.state === "none" ? "" : s.state}`}
          title={`${s.type} · fov ${s.fov_deg}° · ${s.note}`}
        >
          <i className="dot" />
          {s.id}
          <span className="faint">{s.state === "none" ? "ok" : s.state}</span>
        </span>
      ))}
      {sensors.length === 0 && <span className="faint">waiting for sensors…</span>}
    </div>
  )
}

export function EffectorStatus({
  frame,
  selected,
}: {
  frame: StateFrame | null
  selected: TrackView | null
}): JSX.Element {
  const effectors = frame?.effectors ?? []
  const inRange = (maxRange: number): boolean => (selected ? selected.range_m <= maxRange : false)
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Effector</th>
            <th>State</th>
            <th>Range</th>
          </tr>
        </thead>
        <tbody>
          {effectors.map((e) => (
            <tr key={e.id}>
              <td className="mono">
                {e.id} <span className="faint">{e.kind}</span>
              </td>
              <td>
                {e.ready ? (
                  <span className="badge ok">{e.ammunition} ready</span>
                ) : e.ammunition <= 0 ? (
                  <span className="badge crit">dry</span>
                ) : (
                  <span className="badge warn">{e.cooldown_remaining_s.toFixed(1)}s</span>
                )}
              </td>
              <td className="mono nowrap">
                {(e.max_range_m / 1000).toFixed(1)} km
                {selected && (
                  <span className={inRange(e.max_range_m) ? "badge ok" : "badge crit"}>
                    {inRange(e.max_range_m) ? "in" : "out"}
                  </span>
                )}
              </td>
            </tr>
          ))}
          {effectors.length === 0 && (
            <tr>
              <td colSpan={3} className="faint">
                —
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function TrackReadout({ track }: { track: TrackView | null }): JSX.Element {
  if (!track) {
    return <div className="faint">No track selected — click a contact or press Tab.</div>
  }
  return (
    <div className="mono" style={{ fontSize: 12 }} data-testid="track-readout">
      <div className="row">
        <strong>{track.id}</strong>
        <LabelBadge label={track.trainee_class} />
        <span className="faint">hint: {track.suggested_class ?? "—"}</span>
      </div>
      <div>
        range {formatRange(track.range_m)} · brg {formatBearing(track.bearing_deg)} · alt{" "}
        {track.altitude_m.toFixed(0)} m · speed {track.speed_mps.toFixed(0)} m/s
      </div>
      <div>
        TTI {track.time_to_perimeter_s === null ? "—" : `${track.time_to_perimeter_s.toFixed(0)} s`}{" "}
        · quality {(track.quality * 100).toFixed(0)} · σ {track.position_sigma_m.toFixed(0)} m · age{" "}
        {track.age_s.toFixed(0)} s
      </div>
      <div className="faint">
        contributing: {track.sensors.join(", ") || "—"} · confidence{" "}
        {(track.class_confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}

export function KeyGuide(): JSX.Element {
  return (
    <div className="hud-keys" data-testid="key-guide">
      <div className="keys-group">
        <span className="keys-title">Identify</span>
        <div>
          <span className="kbd">Enter</span> classify what the reticle is on
        </div>
        <div>
          <span className="kbd">1</span> hostile · <span className="kbd">2</span> civil ·{" "}
          <span className="kbd">3</span> friendly · <span className="kbd">4</span> bird ·{" "}
          <span className="kbd">5</span> unknown
        </div>
        <div>
          <span className="kbd">Tab</span> next contact
        </div>
      </div>
      <div className="keys-group">
        <span className="keys-title">Engage</span>
        <div>
          <span className="kbd">E</span> soft-kill · <span className="kbd">G</span> hard-kill
        </div>
        <div>
          <span className="kbd">R</span> warn · <span className="kbd">X</span> hold fire
        </div>
        <div>
          <span className="kbd">I</span> request ID · <span className="kbd">Q</span> acknowledge
        </div>
      </div>
      <div className="keys-group">
        <span className="keys-title">View</span>
        <div>
          <span className="kbd">W</span>
          <span className="kbd">A</span>
          <span className="kbd">S</span>
          <span className="kbd">D</span> pan · <span className="kbd">[</span>
          <span className="kbd">]</span> steer EO
        </div>
        <div>
          <span className="kbd">F</span> fullscreen · <span className="kbd">Space</span> pause
          request
        </div>
      </div>
    </div>
  )
}
