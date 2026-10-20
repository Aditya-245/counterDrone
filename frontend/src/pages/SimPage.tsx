import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { interpolateTracks, openSessionSocket, type SessionSocket } from "@/api/socket"
import type { ActionType, TraineeAction, TraineeCall } from "@/api/types"
import { useSimStore } from "@/state/simStore"
import TacticalScene from "@/scene/TacticalScene"
import AimOverlay from "@/hud/AimOverlay"
import { aim, resetAim } from "@/scene/aim"
import { requestView } from "@/scene/viewRequest"
import { CameraView, RadarScope } from "@/hud/ScopePanel"
import TrackTable from "@/hud/TrackTable"
import {
  EffectorStatus,
  KeyGuide,
  MissionClock,
  RoeReminder,
  SensorStrip,
  TrackReadout,
} from "@/hud/Hud"
import { ErrorNote, Panel, Spinner } from "@/components/ui"

/** Direct class hotkeys, matching the order of the on-screen chooser. */
const LABEL_FOR_NUMBER: Record<string, TraineeCall> = {
  "1": "hostile",
  "2": "civil",
  "3": "friendly",
  "4": "bird",
  "5": "unknown",
}

export default function SimPage(): JSX.Element {
  const { scenarioId = "" } = useParams()
  const navigate = useNavigate()
  const traineeId = useSimStore((s) => s.traineeId)
  const setTrainee = useSimStore((s) => s.setTrainee)
  const storeFrame = useSimStore((s) => s.setFrame)
  const setStatus = useSimStore((s) => s.setStatus)
  const selectedId = useSimStore((s) => s.selectedTrackId)
  const select = useSimStore((s) => s.select)
  const tracks = useSimStore((s) => s.tracks)
  const frame = useSimStore((s) => s.frame)
  const status = useSimStore((s) => s.status)

  const socketRef = useRef<SessionSocket | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [eoAzimuth, setEoAzimuth] = useState(0)
  const [interp, setInterp] = useState(1)
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.users() })

  const {
    data: scenario,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.scenario(scenarioId),
    enabled: scenarioId.length > 0,
  })

  // Fullscreen is the browser's state, so mirror it rather than track our own.
  const shellRef = useRef<HTMLDivElement | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  // Bumped on a double click in the scene: the aim overlay owns the chooser,
  // but the canvas owns the pointer, so the request travels as a prop.
  const [chooserRequest, setChooserRequest] = useState(0)

  const toggleFullscreen = useCallback((): void => {
    const shell = shellRef.current
    if (!shell) return
    if (document.fullscreenElement) {
      void document.exitFullscreen()
    } else {
      void shell.requestFullscreen().catch(() => undefined)
    }
  }, [])

  useEffect(() => {
    const sync = (): void => setFullscreen(document.fullscreenElement === shellRef.current)
    document.addEventListener("fullscreenchange", sync)
    return () => document.removeEventListener("fullscreenchange", sync)
  }, [])

  // The aim singleton outlives the page otherwise, and a stale lock would draw
  // a reticle over the next scenario.
  useEffect(() => resetAim, [])

  // Create the session (and therefore the authoritative runtime) on mount.
  useEffect(() => {
    if (!scenario || sessionId) return
    let cancelled = false
    void api
      .createSession({ scenario_id: scenario.id, trainee_id: traineeId })
      .then((rec) => {
        if (cancelled) return
        setSessionId(rec.id)
        useSimStore.getState().setSession(rec.id, scenario.id)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [scenario, sessionId, traineeId])

  // Open the socket and close it when the page unmounts.
  useEffect(() => {
    if (!sessionId) return
    const socket = openSessionSocket(sessionId, {
      onFrame: (f) => {
        storeFrame(f)
        setStatus("open")
      },
      onStatus: (s) => setStatus(s),
      onError: (m) => setStatus("error", m),
      onEnd: (f) => {
        storeFrame(f)
        setStatus("closed")
      },
      onResumed: (f) => storeFrame(f),
    })
    socketRef.current = socket
    return () => {
      socket.close()
      socketRef.current = null
    }
  }, [sessionId, storeFrame, setStatus])

  // Client-side interpolation between the last two 10 Hz frames.
  useEffect(() => {
    if (!frame) return
    let raf = 0
    const started = performance.now()
    const period = 100
    const tick = (): void => {
      const alpha = ((performance.now() - started) % period) / period
      setInterp(alpha)
      raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(raf)
  }, [frame?.step, frame])

  const prev = useSimStore((s) => s.previousFrame)
  const renderedTracks = useMemo(
    () => (prev && frame ? interpolateTracks(prev, frame, interp) : tracks),
    [prev, frame, interp, tracks],
  )
  const selected = useMemo(
    () => tracks.find((t) => t.id === selectedId) ?? null,
    [tracks, selectedId],
  )

  const send = useCallback((action: TraineeAction): void => {
    socketRef.current?.send(action)
  }, [])

  const actOnSelected = useCallback(
    (type: ActionType, params: Record<string, unknown> = {}): void => {
      if (!selectedId || !frame) return
      send({ t: frame.t, type, track_id: selectedId, params, source: "trainee" })
    },
    [selectedId, frame, send],
  )

  const steerEo = useCallback(
    (delta: number): void => {
      if (!frame) return
      const next = (((eoAzimuth + delta) % 360) + 360) % 360
      setEoAzimuth(next)
      send({
        t: frame.t,
        type: "STEER_SENSOR",
        params: { sensor: "eo1", az: next },
        source: "trainee",
      })
    },
    [eoAzimuth, frame, send],
  )

  /**
   * The crosshair reports whatever it has locked onto. Only non-null locks move
   * the selection, so sliding off the scene leaves the readout on the last
   * contact rather than blanking it.
   */
  const lockTrack = useCallback(
    (trackId: string | null): void => {
      if (trackId) select(trackId)
    },
    [select],
  )

  /** Acknowledge then classify a track - the action pair the chooser emits. */
  const classifyTrack = useCallback(
    (trackId: string, call: TraineeCall): void => {
      if (!frame) return
      send({ t: frame.t, type: "ACK_TRACK", track_id: trackId, params: {}, source: "trainee" })
      send({
        t: frame.t,
        type: "CLASSIFY",
        track_id: trackId,
        params: { class: call, confidence: 0.8 },
        source: "trainee",
      })
      select(trackId)
    },
    [frame, send, select],
  )

  // Keyboard shortcuts (spec 8), laid out like a laptop game: WASD pans (the
  // scene owns those keys), Enter identifies whatever the reticle is on, 1-5
  // pick the class, F toggles fullscreen. Pause is instructor-only and goes
  // through the action log, so a trainee can request it but the server decides.
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      // The chooser is modal and owns the keyboard while it is open.
      if (aim.chooserOpen) return
      if (e.key === " ") {
        e.preventDefault()
        if (frame) send({ t: frame.t, type: "PAUSE_REQUEST", params: {} })
        return
      }
      const key = e.key.toUpperCase()
      if (key === "TAB") {
        e.preventDefault()
        const ids = tracks.map((t) => t.id)
        if (ids.length === 0) return
        const idx = ids.indexOf(selectedId ?? "")
        select(ids[(idx + 1) % ids.length] ?? null)
        return
      }
      if (key === "F") return toggleFullscreen()
      if (key === "[") return steerEo(-5)
      if (key === "]") return steerEo(5)
      if (key === "E") return actOnSelected("ENGAGE_S", { effector: "eff_s" })
      if (key === "G") return actOnSelected("ENGAGE_H", { effector: "eff_h" })
      if (key === "X") return actOnSelected("HOLD_FIRE")
      if (key === "R") return actOnSelected("WARN")
      if (key === "I") return actOnSelected("REQUEST_ID")
      if (key === "Q") return actOnSelected("ACK_TRACK")
      const label = LABEL_FOR_NUMBER[key]
      if (label && selectedId) classifyTrack(selectedId, label)
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [
    actOnSelected,
    classifyTrack,
    frame,
    select,
    selectedId,
    send,
    steerEo,
    toggleFullscreen,
    tracks,
  ])

  if (isLoading) return <Spinner label="Loading scenario" />
  if (error instanceof Error) return <ErrorNote message={error.message} />
  if (!scenario) return <ErrorNote message="Scenario not found." />

  const eoSensor = frame?.sensors.find((s) => s.type === "eo_ir")
  const scopeSensor = frame?.sensors.find((s) => s.type === "radar")

  return (
    <div className={`sim${fullscreen ? " sim-fullscreen" : ""}`} ref={shellRef}>
      <div className="scene" onDoubleClick={() => setChooserRequest((n) => n + 1)}>
        <TacticalScene
          scenario={scenario}
          tracks={renderedTracks}
          selectedId={selectedId}
          onSelect={select}
        />
        <AimOverlay
          tracks={tracks}
          chooserRequest={chooserRequest}
          onLock={lockTrack}
          onClassify={classifyTrack}
        />
        <div className="scene-tools no-print">
          <div className="row" style={{ gap: 4 }}>
            <button
              type="button"
              className="btn"
              onClick={() => requestView("ground")}
              title="Ground-level view"
            >
              Ground
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => requestView("site")}
              title="Tactical site view"
            >
              Site
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => requestView("top")}
              title="Overview"
            >
              Top
            </button>
          </div>
          <button
            type="button"
            className="btn"
            onClick={toggleFullscreen}
            title="Toggle fullscreen (F)"
          >
            {fullscreen ? "Exit fullscreen" : "Fullscreen"}
          </button>
        </div>
      </div>

      <div className="side">
        <Panel
          title="Mission"
          actions={
            <span
              className={`badge ${status === "open" ? "ok" : status === "error" ? "crit" : "warn"}`}
            >
              {status}
            </span>
          }
        >
          <MissionClock frame={frame} paused={frame?.paused ?? false} />
          <div style={{ marginTop: 6 }}>
            <label htmlFor="sim-trainee" className="sr-only">
              Trainee
            </label>
            <select id="sim-trainee" value={traineeId} onChange={(e) => setTrainee(e.target.value)}>
              {(users.data ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name}
                </option>
              ))}
            </select>
          </div>
          <div style={{ marginTop: 8 }}>
            <SensorStrip frame={frame} />
          </div>
          <div style={{ marginTop: 8 }}>
            <RoeReminder scenario={scenario} />
          </div>
        </Panel>

        <Panel title="Selected track">
          <TrackReadout track={selected} />
          <div className="row wrap" style={{ marginTop: 8 }}>
            <button type="button" className="btn" onClick={() => actOnSelected("ACK_TRACK")}>
              Ack
            </button>
            {(["hostile", "civil", "friendly", "bird", "unknown"] as const).map((l) => (
              <button
                key={l}
                type="button"
                className="btn"
                onClick={() => actOnSelected("CLASSIFY", { class: l, confidence: 0.8 })}
              >
                {l}
              </button>
            ))}
          </div>
          <div className="row wrap" style={{ marginTop: 6 }}>
            <button type="button" className="btn" onClick={() => actOnSelected("WARN")}>
              Warn
            </button>
            <button type="button" className="btn" onClick={() => actOnSelected("HOLD_FIRE")}>
              Hold
            </button>
            <button
              type="button"
              className="btn primary"
              onClick={() => actOnSelected("ENGAGE_S", { effector: "eff_s" })}
            >
              Soft-kill
            </button>
            <button
              type="button"
              className="btn danger"
              onClick={() => actOnSelected("ENGAGE_H", { effector: "eff_h" })}
            >
              Hard-kill
            </button>
          </div>
          {frame?.allow_pause && (
            <button
              type="button"
              className="btn"
              style={{ marginTop: 6 }}
              onClick={() => send({ t: frame.t, type: "PAUSE_REQUEST", params: {} })}
            >
              Pause (instructor mode)
            </button>
          )}
        </Panel>

        <Panel title="Effectors">
          <EffectorStatus frame={frame} selected={selected} />
        </Panel>

        <Panel title="Controls">
          <KeyGuide />
        </Panel>

        <Panel title="Session">
          <div className="mono faint" style={{ fontSize: 11 }}>
            {sessionId ?? "creating…"}
          </div>
          <Link
            className="btn"
            to={`/aar/${sessionId ?? ""}`}
            style={{ marginTop: 6, display: "inline-block" }}
          >
            Open AAR
          </Link>
          <button
            type="button"
            className="btn"
            style={{ marginTop: 6, marginLeft: 6 }}
            onClick={() => navigate(`/briefing/${scenarioId}`)}
          >
            Briefing
          </button>
        </Panel>
      </div>

      <div className="bottom">
        <Panel title="Radar scope">
          <div className="scope">
            <RadarScope
              scenario={scenario}
              tracks={tracks}
              sensor={scopeSensor}
              selectedId={selectedId}
              onSelect={select}
            />
          </div>
        </Panel>
        <Panel
          title="EO/IR camera"
          actions={
            <div className="row" style={{ gap: 4 }}>
              <button type="button" className="btn" onClick={() => steerEo(-5)}>
                ◀
              </button>
              <span className="mono faint">{eoAzimuth.toFixed(0)}°</span>
              <button type="button" className="btn" onClick={() => steerEo(5)}>
                ▶
              </button>
            </div>
          }
        >
          <div className="camera">
            <CameraView
              scenario={scenario}
              tracks={tracks}
              sensor={eoSensor ? { ...eoSensor, azimuth_deg: eoAzimuth } : undefined}
              selectedId={selectedId}
              onSelect={select}
            />
          </div>
        </Panel>
      </div>

      <div style={{ gridColumn: "1 / -1" }}>
        <Panel title="Tracks">
          <TrackTable tracks={tracks} selectedId={selectedId} onSelect={select} />
        </Panel>
      </div>
    </div>
  )
}
