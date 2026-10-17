import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { api } from "@/api/client"
import { SKILL_NAMES, type TraineeDashboard, type Verdict } from "@/api/types"
import { useSimStore } from "@/state/simStore"
import { useChartPalette } from "@/state/palette"
import { LABEL_COLOURS, VERDICT_COLOURS, compassName } from "@/api/format"
import { ErrorNote, Panel, Spinner } from "@/components/ui"

export default function DashboardPage(): JSX.Element {
  const traineeId = useSimStore((s) => s.traineeId)
  const setTrainee = useSimStore((s) => s.setTrainee)
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.users() })
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard", traineeId],
    queryFn: () => api.dashboard(traineeId),
  })

  if (isLoading) return <Spinner label="Loading dashboard" />
  if (error instanceof Error) return <ErrorNote message={error.message} />
  if (!data) return <ErrorNote message="No data for this trainee." />

  return <Dashboard data={data} users={users.data ?? []} onSelect={setTrainee} />
}

export function Dashboard({
  data,
  users,
  onSelect,
}: {
  data: TraineeDashboard
  users: { id: string; display_name: string; synthetic: boolean }[]
  onSelect: (id: string) => void
}): JSX.Element {
  const [verdictStack, setVerdictStack] = useState<Verdict>("CORRECT_ENGAGE")

  const skillRows = SKILL_NAMES.map((name) => ({
    skill: name,
    rating: data.skills[name],
    weakest: data.weakest_skill === name,
  }))

  const trend = data.sessions.map((s, i) => ({
    index: i + 1,
    total: Number(s["total"] ?? 0),
    detection: Number(s["detection"] ?? 0),
    classification: Number(s["classification"] ?? 0),
    engagement: Number(s["engagement"] ?? 0),
  }))

  const latencyBuckets = bucket(data.detection_latencies)
  const labels = ["hostile", "civil", "friendly", "bird", "unknown"]
  const confusion = labels.map((row) => {
    const entry: Record<string, string | number> = { truth: row }
    for (const col of labels) entry[col] = data.classification_confusion[row]?.[col] ?? 0
    return entry
  })

  const verdictStacked = (data.verdicts_over_time ?? []).map((v, i) => ({
    index: i + 1,
    ...(v.counts as Record<string, number>),
  }))

  const pal = useChartPalette()

  return (
    <div className="grid">
      {data.synthetic_note && (
        <div className="synthetic-banner no-print">{data.synthetic_note}</div>
      )}

      <Panel
        title={`Dashboard — ${data.trainee.display_name}`}
        actions={
          <div className="row no-print" style={{ gap: 8 }}>
            <select
              value={data.trainee.id}
              onChange={(e) => onSelect(e.target.value)}
              style={{ width: 200 }}
            >
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name}
                  {u.synthetic ? " (demo)" : ""}
                </option>
              ))}
            </select>
            <a className="btn" href={api.exportCsvUrl(data.trainee.id)}>
              Export CSV
            </a>
            <button type="button" className="btn" onClick={() => window.print()}>
              Print / PDF
            </button>
          </div>
        }
      >
        <div className="grid cols-4">
          <div className="stat">
            <span className="value">{data.mean_score?.toFixed(1) ?? "—"}</span>
            <span className="label">Mean score</span>
          </div>
          <div className="stat">
            <span className="value">{data.session_count}</span>
            <span className="label">Sessions</span>
          </div>
          <div className="stat">
            <span className="value">{data.weakest_skill ?? "—"}</span>
            <span className="label">Weakest skill</span>
          </div>
          <div className="stat">
            <span className="value">{data.habits?.sessions_observed ?? 0}</span>
            <span className="label">Sessions in habit model</span>
          </div>
        </div>
      </Panel>

      <div className="grid cols-2">
        <Panel title="Skill profile">
          <ResponsiveContainer width="100%" height={230}>
            <RadarChart data={skillRows} outerRadius="72%">
              <PolarGrid stroke={pal.grid} />
              <PolarAngleAxis dataKey="skill" tick={{ fill: pal.tick, fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={{ fill: pal.axis, fontSize: 9 }} />
              <Radar
                dataKey="rating"
                stroke={pal.series[0]}
                fill={pal.series[0]}
                fillOpacity={0.28}
              />
              <Tooltip />
            </RadarChart>
          </ResponsiveContainer>
          <table>
            <tbody>
              {skillRows.map((r) => (
                <tr key={r.skill}>
                  <td>{r.skill}</td>
                  <td className="mono">{r.rating.toFixed(1)}</td>
                  <td>{r.weakest ? <span className="badge warn">weakest</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Score trend">
          {trend.length === 0 ? (
            <div className="faint">No sessions yet.</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trend}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis dataKey="index" stroke={pal.axis} />
                <YAxis domain={[0, 100]} stroke={pal.axis} />
                <Tooltip />
                <Legend />
                <Line dataKey="total" stroke={pal.series[0]} dot />
                <Line dataKey="detection" stroke={pal.series[1]} dot={false} />
                <Line dataKey="classification" stroke={pal.series[2]} dot={false} />
                <Line dataKey="engagement" stroke={pal.series[3]} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Detection latency distribution (s)">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={latencyBuckets}>
              <CartesianGrid stroke={pal.grid} />
              <XAxis dataKey="bucket" stroke={pal.axis} />
              <YAxis stroke={pal.axis} />
              <Tooltip />
              <Bar dataKey="count" fill={pal.series[0]} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Classification confusion matrix">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={confusion}>
              <CartesianGrid stroke={pal.grid} />
              <XAxis dataKey="truth" stroke={pal.axis} />
              <YAxis stroke={pal.axis} />
              <Tooltip />
              <Legend />
              {labels.map((l) => (
                <Bar key={l} dataKey={l} stackId="a" fill={LABEL_COLOURS[l] ?? pal.axis} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      <Panel
        title="Verdicts over time"
        actions={
          <select
            value={verdictStack}
            // Options are built from VERDICT_COLOURS, so the value is always a Verdict.
            onChange={(e) => setVerdictStack(e.target.value as Verdict)}
            style={{ width: 220 }}
          >
            {Object.keys(VERDICT_COLOURS).map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        }
      >
        {verdictStacked.length === 0 ? (
          <div className="faint">No verdicts recorded yet.</div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={verdictStacked}>
              <CartesianGrid stroke={pal.grid} />
              <XAxis dataKey="index" stroke={pal.axis} />
              <YAxis stroke={pal.axis} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey={verdictStack} fill={VERDICT_COLOURS[verdictStack]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </Panel>

      <div className="grid cols-2">
        <Panel title="Habit insights">
          {!data.habits ? (
            <div className="faint">No habit data yet — play a session first.</div>
          ) : (
            <>
              <div className="heatstrip">
                {data.habits.sector_scan_share.map((v, i) => {
                  const max = Math.max(...data.habits!.sector_scan_share, 1e-6)
                  const frac = v / max
                  return (
                    <div
                      key={i}
                      title={`${compassName(i)}: ${(v * 100).toFixed(1)}% of your acknowledgements`}
                      style={{
                        background:
                          frac > 0.66 ? pal.series[0] : frac > 0.33 ? pal.series[2] : pal.grid,
                        color: frac > 0.33 ? "var(--accent-ink)" : "var(--text-dim)",
                      }}
                    >
                      {compassName(i)}
                    </div>
                  )
                })}
              </div>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr>
                    <td>least-scanned sector</td>
                    <td className="mono">{compassName(data.habits.least_scanned_sector)}</td>
                  </tr>
                  <tr>
                    <td>mean latency, worst sector</td>
                    <td className="mono">
                      {Math.max(...data.habits.mean_detection_latency_by_sector).toFixed(1)} s
                    </td>
                  </tr>
                  <tr>
                    <td>over-engagement tendency</td>
                    <td className="mono">{(data.habits.over_engage_rate * 100).toFixed(0)}%</td>
                  </tr>
                  <tr>
                    <td>under-engagement tendency</td>
                    <td className="mono">{(data.habits.under_engage_rate * 100).toFixed(0)}%</td>
                  </tr>
                  <tr>
                    <td>saturation reaction cost</td>
                    <td className="mono">{data.habits.saturation_reaction_ms.toFixed(0)} ms</td>
                  </tr>
                </tbody>
              </table>
            </>
          )}
        </Panel>

        <Panel title="Adversary adapted to you">
          <div className="mono faint" style={{ fontSize: 12 }}>
            tactic: {data.redbrain.tactic_name || "—"} · pulls {data.redbrain.total_pulls} · arms{" "}
            {data.redbrain.tactics_considered}
            {data.redbrain.learned_policy_available
              ? " · learned policy loaded"
              : " · base tactic set"}
          </div>
          {data.redbrain.exploits.length === 0 ? (
            <div className="hint" style={{ marginTop: 8 }}>
              Nothing to exploit yet. The adversary needs at least one logged session with a
              measurable sector bias or reaction pattern before it can claim it adapted.
            </div>
          ) : (
            <ul style={{ margin: "8px 0 0", paddingLeft: 16 }}>
              {data.redbrain.exploits.map((e) => (
                <li key={e.habit}>
                  {e.description}
                  <div className="faint mono" style={{ fontSize: 11 }}>
                    evidence: {JSON.stringify(e.evidence)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  )
}

function bucket(values: number[]): { bucket: string; count: number }[] {
  const edges = [0, 5, 10, 20, 30, 45, 60, 90, 120, 180]
  const out = edges.slice(0, -1).map((lo, i) => ({
    bucket: `${lo}-${edges[i + 1]}s`,
    count: 0,
  }))
  for (const v of values) {
    const idx = Math.min(out.length - 1, edges.findIndex((e) => v < e) - 1)
    const bucket = out[Math.max(0, idx)]
    if (bucket) bucket.count += 1
  }
  return out
}
