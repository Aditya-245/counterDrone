import { describe, expect, it } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  EffectorStatus,
  KeyGuide,
  MissionClock,
  RoeReminder,
  SensorStrip,
  TrackReadout,
} from "@/hud/Hud"
import { RadarScope, CameraView } from "@/hud/ScopePanel"
import type { Scenario, StateFrame, TrackView } from "@/api/types"

const SCENARIO: Scenario = {
  schema_version: 3,
  id: "scn_test",
  name: "Test",
  description: "",
  seed: 1,
  duration_s: 300,
  terrain: {
    preset: "rural",
    size_m: 6000,
    resolution: 192,
    scale_m: 80,
    roughness: 0.5,
    seed: 1,
    buildings: [],
    heightmap_path: null,
  },
  environment: {
    time_of_day: "night",
    visibility_m: 5000,
    rain: 0.2,
    wind_speed_mps: 4,
    wind_heading_deg: 0,
  },
  site: {
    protected_radius_m: 150,
    ring_distances_m: [400, 900, 1800],
    civil_sectors: [
      { bearing_deg: 200, half_width_deg: 25, inner_m: 0, outer_m: 3000, label: "town" },
    ],
  },
  sensors: [
    {
      id: "radar1",
      type: "radar",
      x: 0,
      y: 0,
      z: 18,
      fov_deg: 360,
      min_range_m: 100,
      max_range_m: 4800,
      initial_state: "none",
      degradations: [],
      params: {},
      drift_mps: 0,
    },
    {
      id: "eo1",
      type: "eo_ir",
      x: 0,
      y: 0,
      z: 12,
      fov_deg: 70,
      min_range_m: 20,
      max_range_m: 1800,
      initial_state: "none",
      degradations: [],
      params: {},
      drift_mps: 0,
    },
  ],
  threats: [],
  decoys: [],
  effectors: [
    {
      id: "eff_s",
      kind: "soft",
      max_range_m: 1400,
      cooldown_s: 7,
      ammunition: 10,
      base_success: 0.78,
    },
  ],
  roe: {
    rule_set: "STRICT",
    require_id_before_engage: true,
    allowed_engage_labels: ["hostile"],
    friend_or_civil_warning_required: true,
    collateral_sector_tolerance: 0,
    late_engage_threshold_s: 20,
    min_track_quality: 0.2,
    require_warning_before_engage: true,
  },
  events: [],
  difficulty: {
    schema_version: 3,
    swarm_size: 0.3,
    approach_axes: 0.3,
    time_pressure: 0.3,
    sensor_degradation_level: 0.2,
    decoy_ratio: 0.2,
    visibility: 0.7,
    night: 1,
    ambiguity: 0.2,
    simultaneity: 0.3,
    roe_strictness: 0.7,
  },
  tags: [],
  instructor_locked: false,
  source: "shipped",
}

const TRACK: TrackView = {
  id: "T0001",
  x: 1200,
  y: 400,
  z: 90,
  vx: -12,
  vy: -3,
  vz: 0,
  position_sigma_m: 14,
  velocity_sigma_mps: 2,
  quality: 0.72,
  age_s: 30,
  sensors: ["radar1", "rf1"],
  suggested_class: "fpv_fast",
  suggested_label: "hostile",
  class_confidence: 0.82,
  range_m: 1265,
  bearing_deg: 18.4,
  altitude_m: 90,
  speed_mps: 12,
  radial_mps: -12,
  time_to_perimeter_s: 92,
  acknowledged: true,
  trainee_class: "hostile",
  trainee_priority: 1,
  neutralised: false,
  missed: false,
  confirmed: true,
}

const FRAME: StateFrame = {
  session_id: "ses_1",
  t: 125.4,
  step: 1254,
  duration_s: 300,
  status: "running",
  tracks: [TRACK],
  sensors: [
    {
      id: "radar1",
      type: "radar",
      state: "none",
      azimuth_deg: 0,
      fov_deg: 360,
      detections_last_step: 3,
      note: "ok",
    },
    {
      id: "eo1",
      type: "eo_ir",
      state: "partial",
      azimuth_deg: 42,
      fov_deg: 70,
      detections_last_step: 0,
      note: "degraded",
    },
    {
      id: "rf1",
      type: "rf",
      state: "failed",
      azimuth_deg: 0,
      fov_deg: 360,
      detections_last_step: 0,
      note: "failed",
    },
  ],
  effectors: [
    {
      id: "eff_s",
      kind: "soft",
      ready: true,
      cooldown_remaining_s: 0,
      ammunition: 8,
      max_range_m: 1400,
    },
    {
      id: "eff_h",
      kind: "hard",
      ready: false,
      cooldown_remaining_s: 4.2,
      ammunition: 0,
      max_range_m: 1900,
    },
  ],
  events: [],
  ammo_remaining: { eff_s: 8, eff_h: 0 },
  paused: false,
  allow_pause: true,
}

describe("HUD", () => {
  it("renders the mission clock from the server frame", () => {
    render(<MissionClock frame={FRAME} paused={false} />)
    expect(screen.getByTestId("mission-clock").textContent).toBe("02:05.4")
  })

  it("shows PAUSED only when the server says so", () => {
    const { rerender } = render(<MissionClock frame={FRAME} paused={false} />)
    expect(screen.queryByText("PAUSED")).toBeNull()
    rerender(<MissionClock frame={FRAME} paused />)
    expect(screen.getByText("PAUSED")).toBeInTheDocument()
  })

  it("renders one chip per sensor with its degradation state", () => {
    render(<SensorStrip frame={FRAME} />)
    const strip = screen.getByTestId("sensor-strip")
    expect(strip.textContent).toContain("radar1")
    expect(strip.textContent).toContain("partial")
    expect(strip.textContent).toContain("failed")
    expect(strip.querySelectorAll(".sensor-chip")).toHaveLength(3)
  })

  it("summarises effector readiness and in-range state", () => {
    render(<EffectorStatus frame={FRAME} selected={TRACK} />)
    // Ammo now rides along in the readiness badge ("8 ready") rather than
    // needing its own column in the narrow side panel.
    expect(screen.getByText("8 ready")).toBeInTheDocument()
    expect(screen.getByText("dry")).toBeInTheDocument()
    // Both effectors cover the selected contact.
    expect(screen.getAllByText("in")).toHaveLength(2)
  })

  it("states the active ROE requirements", () => {
    render(<RoeReminder scenario={SCENARIO} />)
    expect(screen.getByText(/ROE STRICT/)).toBeInTheDocument()
    expect(screen.getByText(/positive ID required/)).toBeInTheDocument()
    expect(screen.getByText(/ZERO collateral tolerance/)).toBeInTheDocument()
  })

  it("shows the selected track readout and an empty state otherwise", () => {
    const { rerender } = render(<TrackReadout track={TRACK} />)
    const readout = screen.getByTestId("track-readout")
    expect(readout.textContent).toContain("T0001")
    expect(readout.textContent).toContain("radar1, rf1")
    rerender(<TrackReadout track={null} />)
    expect(screen.getByText(/No track selected/)).toBeInTheDocument()
  })

  it("lists the keyboard shortcuts", () => {
    render(<KeyGuide />)
    const guide = screen.getByTestId("key-guide")
    for (const key of [
      "classify",
      "hostile",
      "civil",
      "bird",
      "friendly",
      "unknown",
      "soft-kill",
      "hold fire",
      "fullscreen",
      "pause",
    ]) {
      expect(guide.textContent).toContain(key)
    }
  })
})

describe("Scope panels", () => {
  it("draws every confirmed track on the scope and selects on click", () => {
    const picked: string[] = []
    render(
      <RadarScope
        scenario={SCENARIO}
        tracks={[TRACK]}
        sensor={FRAME.sensors[0]}
        selectedId={null}
        onSelect={(id) => picked.push(id)}
      />,
    )
    fireEvent.click(screen.getByTestId("scope-track-T0001"))
    expect(picked).toEqual(["T0001"])
  })

  it("only shows camera contacts inside the steered FOV", () => {
    const wide = render(
      <CameraView
        scenario={SCENARIO}
        tracks={[TRACK]}
        sensor={{ ...FRAME.sensors[1]!, azimuth_deg: 18 }}
        selectedId="T0001"
        onSelect={() => {}}
      />,
    )
    expect(screen.getByTestId("camera-track-T0001")).toBeInTheDocument()
    wide.unmount()

    render(
      <CameraView
        scenario={SCENARIO}
        tracks={[TRACK]}
        sensor={{ ...FRAME.sensors[1]!, azimuth_deg: 200 }}
        selectedId={null}
        onSelect={() => {}}
      />,
    )
    expect(screen.queryByTestId("camera-track-T0001")).toBeNull()
  })
})
