/** Synchronised AAR timeline: trainee actions, ghost actions and events. */

import type { GhostAction, SimEvent, TraineeAction } from "@/api/types"

export interface TimelineProps {
  duration: number
  playhead: number
  traineeActions: TraineeAction[]
  ghostActions: GhostAction[]
  events: SimEvent[]
  onSeek: (t: number) => void
  onFork: (t: number) => void
  forkBusy?: boolean
}

const ACTION_COLOURS: Record<string, string> = {
  ENGAGE_S: "#f62440",
  ENGAGE_H: "#c4162f",
  CLASSIFY: "#ecdfcc",
  ACK_TRACK: "#ecdfcc",
  WARN: "#d9b26a",
  HOLD_FIRE: "#9ec47a",
  REQUEST_ID: "#b8a8d4",
  SET_PRIORITY: "#b8a8d4",
  STEER_SENSOR: "#697565",
  PAUSE_REQUEST: "#697565",
}

export default function Timeline({
  duration,
  playhead,
  traineeActions,
  ghostActions,
  events,
  onSeek,
  onFork,
  forkBusy = false,
}: TimelineProps): JSX.Element {
  const pct = (t: number): number => (duration > 0 ? (t / duration) * 100 : 0)

  return (
    <div>
      <div
        className="timeline"
        data-testid="timeline"
        role="slider"
        tabIndex={0}
        aria-label="Replay position"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(playhead)}
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          const frac = (e.clientX - rect.left) / rect.width
          onSeek(Math.max(0, Math.min(duration, frac * duration)))
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") onSeek(Math.max(0, playhead - 5))
          if (e.key === "ArrowRight") onSeek(Math.min(duration, playhead + 5))
        }}
      >
        <div className="lane" style={{ top: "22%" }} />
        <div className="lane" style={{ top: "52%" }} />
        <div className="lane" style={{ top: "78%" }} />

        {traineeActions.map((a, i) => (
          <div
            key={`t-${i}`}
            className="event"
            title={`${a.t.toFixed(1)}s ${a.type} ${a.track_id ?? ""}`}
            style={{
              left: `${pct(a.t)}%`,
              top: "12%",
              height: "20%",
              background: ACTION_COLOURS[a.type] ?? "#38bdf8",
            }}
          />
        ))}

        {ghostActions.map((g, i) => (
          <div
            key={`g-${i}`}
            className="ghost"
            title={`${g.t.toFixed(1)}s GHOST ${g.type} — ${g.rationale}`}
            style={{
              left: `${pct(g.t)}%`,
              top: "44%",
              height: "18%",
              background: "#b8a8d4",
            }}
          />
        ))}

        {events
          .filter(
            (e) =>
              e.kind === "ring_crossing" ||
              e.kind === "sensor_degraded" ||
              e.kind === "engagement_result",
          )
          .map((e, i) => (
            <div
              key={`e-${i}`}
              className="event"
              title={`${e.t.toFixed(1)}s ${e.message || e.kind}`}
              style={{
                left: `${pct(e.t)}%`,
                top: "70%",
                height: "22%",
                background: e.kind === "ring_crossing" ? "#f62440" : "#d9b26a",
              }}
            />
          ))}

        <div className="playhead" style={{ left: `${pct(playhead)}%` }} />
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <span className="mono faint">
          {playhead.toFixed(1)}s / {duration.toFixed(0)}s
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="btn primary"
          disabled={forkBusy}
          onClick={() => onFork(playhead)}
        >
          {forkBusy ? "Forking…" : "Fork from here"}
        </button>
      </div>
    </div>
  )
}
