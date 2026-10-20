import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { VERDICT_COLOURS, compassName, heatColour } from "@/api/format"
import type { ForkResult, ReplaySession, Verdict } from "@/api/types"
import { engine } from "@/engine/client"
import { loadSession } from "@/engine/history"
import { ErrorNote, Panel, Spinner } from "@/components/ui"
import Timeline from "@/components/Timeline"

type Filter = "all" | "missed" | "late" | "wrong"

export default function AARPage(): JSX.Element {
  const { sessionId: routeSession } = useParams()
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.sessions() })
  const [sessionId, setSessionId] = useState(routeSession ?? "")
  const [playhead, setPlayhead] = useState(0)
  const [filter, setFilter] = useState<Filter>("all")
  const [fork, setFork] = useState<ForkResult | null>(null)
  const [forkBusy, setForkBusy] = useState(false)

  useEffect(() => {
    if (!sessionId && sessions.data && sessions.data.items.length > 0) {
      setSessionId(sessions.data.items[0]?.id ?? "")
    }
  }, [sessions.data, sessionId])

  /**
   * The replay comes from this browser when it is here.
   *
   * The browser played the session, so it captured the frames, the ghost and
   * the score as it went: the after-action review is instant and works
   * offline. A session played elsewhere falls back to the server's replay.
   */
  const replay = useQuery({
    queryKey: ["aar", sessionId],
    queryFn: async () => {
      const local = await loadSession(sessionId)
      if (local) return local.replay
      // The browser may still be handing the session over: the engine keeps
      // the capture in memory for a moment after the page that played it goes
      // away. Waiting for it beats replaying the whole session on the server.
      if (engine.getSessionId() === sessionId) {
        for (let attempt = 0; attempt < 80; attempt += 1) {
          const live = engine.getReplay()
          if (live && live.session_id === sessionId) return live
          await new Promise((resolve) => setTimeout(resolve, 250))
        }
      }
      return api.aar(sessionId)
    },
    enabled: sessionId.length > 0,
  })

  const data: ReplaySession | undefined = replay.data
  const duration = data?.duration_s ?? 0

  const verdicts = useMemo(() => {
    const all = data?.score.verdicts ?? []
    if (filter === "all") return all
    if (filter === "missed") return all.filter((v) => v.verdict === "MISSED_THREAT")
    if (filter === "late") return all.filter((v) => v.verdict === "LATE_ENGAGE")
    return all.filter(
      (v) =>
        v.verdict.startsWith("FALSE_") ||
        v.verdict === "COLLATERAL_VIOLATION" ||
        v.verdict === "PREMATURE_ENGAGE" ||
        v.verdict === "UNNECESSARY_ESCALATION",
    )
  }, [data, filter])

  /**
   * Fork the session from the playhead.
   *
   * When this browser played the session, the fork is simulated here: the
   * original run and the ghost are already known, so only the fork itself has
   * to be re-simulated. Otherwise the server does it.
   */
  const runFork = async (t: number): Promise<void> => {
    if (!sessionId) return
    setForkBusy(true)
    setFork(null)
    try {
      const local = await loadSession(sessionId)
      if (local) {
        const result = await engine.fork(
          local.scenario,
          local.actions,
          local.replay.ghost_actions,
          {
            session_id: sessionId,
            fork_t: t,
            replace_all_from: true,
            follow_with_ghost: true,
          },
          {
            hash: local.replay.final_state_hash,
            score: local.replay.score,
            outcome: local.outcome,
          },
        )
        setFork(result)
      } else {
        const result = await api.fork({
          session_id: sessionId,
          fork_t: t,
          replace_all_from: true,
          follow_with_ghost: true,
        } as never)
        setFork(result)
      }
    } catch {
      setFork(null)
    } finally {
      setForkBusy(false)
    }
  }

  if (!sessionId) {
    return (
      <Panel title="After-action review">
        <Spinner label="Waiting for a session" />
      </Panel>
    )
  }
  if (replay.isLoading) return <Spinner label="Replaying session" />
  if (replay.error instanceof Error) return <ErrorNote message={replay.error.message} />
  if (!data) return <ErrorNote message="No replay available." />

  const score = data.score

  return (
    <div className="grid">
      <Panel
        title="Replay"
        actions={
          <div className="row" style={{ gap: 8 }}>
            <select
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              style={{ width: 260 }}
            >
              {(sessions.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.scenario_name} — {s.trainee_name}
                  {s.synthetic ? " (demo)" : ""}
                </option>
              ))}
            </select>
            <Link className="btn" to={`/sim/${data.scenario_id}`}>
              Replay live
            </Link>
          </div>
        }
      >
        <Timeline
          duration={duration}
          playhead={playhead}
          traineeActions={data.trainee_actions}
          ghostActions={data.ghost_actions}
          events={data.events}
          onSeek={setPlayhead}
          onFork={runFork}
          forkBusy={forkBusy}
        />
        <div className="legend" style={{ marginTop: 6 }}>
          <span>
            <i style={{ background: "#ecdfcc" }} />
            trainee action
          </span>
          <span>
            <i style={{ background: "#b8a8d4" }} />
            optimal-decision ghost
          </span>
          <span>
            <i style={{ background: "#f62440" }} />
            event
          </span>
          <span className="mono faint">
            hash {data.final_state_hash.slice(0, 12)} · {data.frames.length} frames
          </span>
        </div>
      </Panel>

      <div className="grid cols-4">
        {[
          ["Total", score.total.toFixed(1)],
          ["Detection", `${score.detection.toFixed(1)}/20`],
          ["Classification", `${score.classification.toFixed(1)}/25`],
          ["Prioritisation", `${score.prioritisation.toFixed(1)}/15`],
          ["Engagement & ROE", `${score.engagement.toFixed(1)}/30`],
          ["Workload", `${score.workload.toFixed(1)}/10`],
        ].map(([label, value]) => (
          <div className="panel" key={label}>
            <div className="stat">
              <span className="value">{value}</span>
              <span className="label">{label}</span>
            </div>
          </div>
        ))}
        <div className="panel span-2">
          {score.capped ? (
            <div className="error">Score capped at 60 — {score.cap_reason}</div>
          ) : (
            <div className="hint">No score cap applied.</div>
          )}
        </div>
      </div>

      <div className="grid cols-2">
        <Panel title="Counterfactual fork">
          {fork ? (
            <div data-testid="fork-result">
              <div className={`badge ${fork.diverged ? "warn" : "ok"}`}>
                {fork.diverged ? "outcome changed" : "identical outcome"}
              </div>
              <p className="muted">{fork.explanation}</p>
              <div className="mono faint" style={{ fontSize: 11 }}>
                original {fork.original_hash.slice(0, 12)} → forked {fork.forked_hash.slice(0, 12)}{" "}
                · Δ {fork.score_delta >= 0 ? "+" : ""}
                {fork.score_delta.toFixed(1)}
              </div>
              {fork.ghost_actions.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <h3>What the ghost would have done</h3>
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {fork.ghost_actions
                      .filter((g) => g.t > fork.fork_t - 5)
                      .slice(0, 8)
                      .map((g, i) => (
                        <li key={`${g.t}-${i}`} className="muted">
                          <span className="mono">{g.t.toFixed(1)}s</span> {g.type} — {g.rationale}
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="hint">
              Drag the playhead and press <strong>Fork from here</strong> to re-simulate the rest of
              the session with the optimal-decision ghost taking over. The unforked replay
              reproduces the original hash exactly.
            </div>
          )}
        </Panel>

        <Panel title="Sector attention">
          <SectorHeat values={data.sector_attention} />
          <div className="hint" style={{ marginTop: 8 }}>
            Seconds of trainee attention per 45° sector, accumulated from sensor steering and from
            actions on tracks in that sector.
          </div>
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Coaching points">
          {score.coaching_points.length === 0 && <div className="faint">No coaching points.</div>}
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {score.coaching_points.map((c) => (
              <li key={c.code}>
                <span
                  className={`badge ${c.severity === "critical" ? "crit" : c.severity === "warn" ? "warn" : "ok"}`}
                >
                  {c.severity}
                </span>{" "}
                {c.text}
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Verdict breakdown">
          <table>
            <thead>
              <tr>
                <th>Verdict</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(score.verdict_counts).map(([v, n]) => (
                <tr key={v}>
                  <td>
                    <span
                      className="badge"
                      style={{
                        color: VERDICT_COLOURS[v as Verdict] ?? "#888",
                        borderColor: "currentColor",
                      }}
                    >
                      {v}
                    </span>
                  </td>
                  <td className="mono">{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <Panel
        title="Decision log"
        actions={
          <div className="row" style={{ gap: 4 }}>
            {(["all", "missed", "late", "wrong"] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                className="btn"
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
                style={filter === f ? { borderColor: "var(--accent)" } : undefined}
              >
                {f}
              </button>
            ))}
          </div>
        }
      >
        <div className="scroll" style={{ maxHeight: 380 }}>
          <table>
            <thead>
              <tr>
                <th>t</th>
                <th>Entity</th>
                <th>Verdict</th>
                <th>Points</th>
                <th>ROE tree path</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {verdicts.map((v, i) => (
                <tr key={`${v.entity_id}-${v.t}-${i}`}>
                  <td className="mono">{v.t.toFixed(1)}</td>
                  <td className="mono">
                    {v.entity_id}
                    {v.track_id ? ` (${v.track_id})` : ""}
                  </td>
                  <td>
                    <span
                      className="badge"
                      style={{ color: VERDICT_COLOURS[v.verdict], borderColor: "currentColor" }}
                    >
                      {v.verdict}
                    </span>
                  </td>
                  <td className="mono">{v.points.toFixed(2)}</td>
                  <td className="tree-path">{v.path.join(" › ")}</td>
                  <td className="muted">{v.explanation}</td>
                </tr>
              ))}
              {verdicts.length === 0 && (
                <tr>
                  <td colSpan={6} className="faint">
                    Nothing in this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function SectorHeat({ values }: { values: number[] }): JSX.Element {
  const max = Math.max(1e-6, ...values)
  return (
    <div className="heatstrip" data-testid="sector-heat">
      {Array.from({ length: 8 }, (_, i) => {
        const v = values[i] ?? 0
        const frac = v / max
        return (
          <div
            key={i}
            title={`${compassName(i)}: ${v.toFixed(1)} s`}
            style={{
              background: heatColour(frac),
              color: frac > 0.5 ? "var(--accent-ink)" : "var(--text-dim)",
            }}
          >
            {compassName(i)}
          </div>
        )
      })}
    </div>
  )
}
