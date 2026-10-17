import { useParams, Link, useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { Panel, Spinner, ErrorNote } from "@/components/ui"
import { useSimStore } from "@/state/simStore"

export default function BriefingPage(): JSX.Element {
  const { scenarioId = "" } = useParams()
  const navigate = useNavigate()
  const traineeId = useSimStore((s) => s.traineeId)
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.users() })
  const { data, isLoading, error } = useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.scenario(scenarioId),
    enabled: scenarioId.length > 0,
  })

  if (isLoading) return <Spinner label="Loading briefing" />
  if (error instanceof Error) return <ErrorNote message={error.message} />
  if (!data) return <ErrorNote message="Scenario not found." />

  const hostileWaves = data.threats.filter((w) => w.label === "hostile")
  const decoyCount = data.decoys.reduce((a, w) => a + w.count, 0)

  return (
    <div className="grid cols-2">
      <Panel title={`Briefing — ${data.name}`}>
        <p className="muted">{data.description}</p>

        <div className="grid cols-2" style={{ marginTop: 10 }}>
          <div>
            <h3>Environment</h3>
            <ul className="mono" style={{ paddingLeft: 16, margin: 0 }}>
              <li>time of day: {data.environment.time_of_day}</li>
              <li>visibility: {Math.round(data.environment.visibility_m)} m</li>
              <li>rain: {(data.environment.rain * 100).toFixed(0)}%</li>
              <li>wind: {data.environment.wind_speed_mps.toFixed(1)} m/s</li>
            </ul>
          </div>
          <div>
            <h3>Site</h3>
            <ul className="mono" style={{ paddingLeft: 16, margin: 0 }}>
              <li>protected radius: {Math.round(data.site.protected_radius_m)} m</li>
              <li>rings: {data.site.ring_distances_m.map((r) => `${r / 1000}km`).join(", ")}</li>
              <li>
                civil sectors:{" "}
                {data.site.civil_sectors.length
                  ? data.site.civil_sectors.map((s) => `${s.label} @ ${s.bearing_deg}°`).join(", ")
                  : "none declared"}
              </li>
            </ul>
          </div>
          <div>
            <h3>Threat picture</h3>
            <ul className="mono" style={{ paddingLeft: 16, margin: 0 }}>
              {hostileWaves.map((w) => (
                <li key={w.name}>
                  {w.count} × {w.entity_class} from {w.spawn_bearing_deg.toFixed(0)}° at t=
                  {Math.round(w.spawn_t)}s
                </li>
              ))}
              {decoyCount > 0 && <li>{decoyCount} decoy contact(s)</li>}
              <li>duration: {Math.round(data.duration_s / 60)} min</li>
              <li>seed: {data.seed}</li>
            </ul>
          </div>
          <div>
            <h3>Rules of engagement — {data.roe.rule_set}</h3>
            <ul className="mono" style={{ paddingLeft: 16, margin: 0 }}>
              <li>positive ID required: {data.roe.require_id_before_engage ? "YES" : "no"}</li>
              <li>warning required: {data.roe.require_warning_before_engage ? "YES" : "no"}</li>
              <li>authorised: {data.roe.allowed_engage_labels.join(", ")}</li>
              <li>
                collateral tolerance: {(data.roe.collateral_sector_tolerance * 100).toFixed(0)}%
              </li>
            </ul>
          </div>
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <label htmlFor="brief-trainee" className="sr-only">
            Trainee
          </label>
          <select
            id="brief-trainee"
            style={{ width: 200 }}
            value={traineeId}
            onChange={(e) => useSimStore.getState().setTrainee(e.target.value)}
          >
            {(users.data ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn primary"
            onClick={() => navigate(`/sim/${scenarioId}`)}
          >
            Begin
          </button>
          <Link className="btn" to="/">
            Back to library
          </Link>
        </div>

        <div className="hint" style={{ marginTop: 14 }}>
          You see <strong>tracks only</strong>. The simulator never shows you what a contact
          actually is — that is what the ROE decision tree scores you against, afterwards.
        </div>
      </Panel>

      <Panel title="Sensors and effectors">
        <table>
          <thead>
            <tr>
              <th>Sensor</th>
              <th>Type</th>
              <th>Range</th>
              <th>FOV</th>
              <th>Initial</th>
            </tr>
          </thead>
          <tbody>
            {data.sensors.map((s) => (
              <tr key={s.id}>
                <td className="mono">{s.id}</td>
                <td className="mono">{s.type}</td>
                <td className="mono">{(s.max_range_m / 1000).toFixed(1)} km</td>
                <td className="mono">{s.fov_deg.toFixed(0)}°</td>
                <td className="mono">{s.initial_state}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3 style={{ marginTop: 12 }}>Effectors</h3>
        <table>
          <thead>
            <tr>
              <th>Effector</th>
              <th>Kind</th>
              <th>Range</th>
              <th>Cooldown</th>
              <th>Ammo</th>
            </tr>
          </thead>
          <tbody>
            {data.effectors.map((e) => (
              <tr key={e.id}>
                <td className="mono">{e.id}</td>
                <td className="mono">{e.kind === "soft" ? "soft-kill" : "hard-kill"}</td>
                <td className="mono">{(e.max_range_m / 1000).toFixed(1)} km</td>
                <td className="mono">{e.cooldown_s.toFixed(0)} s</td>
                <td className="mono">{e.ammunition}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hint" style={{ marginTop: 8 }}>
          Effectors are abstract and synthetic. Ranges and success probabilities exist to make the
          decision problem playable; they describe nothing real.
        </div>
      </Panel>
    </div>
  )
}
