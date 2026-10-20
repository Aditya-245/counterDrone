import { useMemo, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { useSimStore } from "@/state/simStore"
import { Panel, Spinner, ErrorNote } from "@/components/ui"

const SOURCES = [
  { key: "", label: "All" },
  { key: "shipped", label: "Shipped" },
  { key: "generated", label: "Generated" },
]

/** The statement asks for rural and urban, by day and night, single drone and swarm. */
const TERRAINS = [
  { key: "rural", label: "Rural" },
  { key: "urban", label: "Urban" },
]
const TIMES = [
  { key: "day", label: "Day" },
  { key: "dusk", label: "Dusk" },
  { key: "night", label: "Night" },
]
const COMPOSITIONS = [
  { key: "single", label: "Single drone" },
  { key: "swarm", label: "Swarm" },
]

export default function LibraryPage(): JSX.Element {
  const traineeId = useSimStore((s) => s.traineeId)
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.users() })
  const [tag, setTag] = useState("")
  const [source, setSource] = useState("")
  const [terrain, setTerrain] = useState("rural")
  const [timeOfDay, setTimeOfDay] = useState("day")
  const [composition, setComposition] = useState("single")
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ["scenarios", tag, source, traineeId],
    queryFn: () =>
      api.scenarios({ tag: tag || undefined, source: source || undefined, trainee_id: traineeId }),
  })

  const generate = useMutation({
    mutationFn: () =>
      api.generate({
        terrain_preset: terrain,
        time_of_day: timeOfDay as "day" | "dusk" | "night",
        threat_composition: composition as "single" | "swarm",
        trainee_id: traineeId,
        persist: true,
      }),
    onSuccess: async (scenario) => {
      await queryClient.invalidateQueries({ queryKey: ["scenarios"] })
      navigate(`/briefing/${scenario.id}`)
    },
  })

  const recommended = useMemo(() => (data?.items ?? []).filter((s) => s.recommended), [data])

  return (
    <div className="grid">
      <Panel title="Scenario library">
        <div className="toolbar">
          <label htmlFor="trainee" className="sr-only">
            Trainee
          </label>
          <select
            id="trainee"
            style={{ width: 200 }}
            value={traineeId}
            onChange={(e) => useSimStore.getState().setTrainee(e.target.value)}
          >
            {(users.data ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name}
                {u.synthetic ? " (demo)" : ""}
              </option>
            ))}
          </select>
          <select
            style={{ width: 150 }}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            aria-label="Source"
          >
            {SOURCES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
          <select
            style={{ width: 160 }}
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            aria-label="Tag"
          >
            <option value="">All tags</option>
            {(data?.tags ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <div style={{ flex: "1 1 auto" }} />
        </div>

        {isLoading && <Spinner label="Loading scenarios" />}
        {error instanceof Error && <ErrorNote message={error.message} />}
        {data && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Conditions</th>
                  <th>Length</th>
                  <th>Difficulty</th>
                  <th>Threats</th>
                  <th>ROE</th>
                  <th>Oracle</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.name}</strong>
                      {s.recommended && (
                        <>
                          {" "}
                          <span className="badge ok">for you</span>
                          <div className="faint" style={{ fontSize: 11, maxWidth: 340 }}>
                            {s.recommended_reason}
                          </div>
                        </>
                      )}
                    </td>
                    <td className="muted">
                      {s.terrain} · {s.time_of_day}
                    </td>
                    <td className="mono">{Math.round(s.duration_s / 60)} min</td>
                    <td className="mono">{s.difficulty.toFixed(2)}</td>
                    <td className="mono">
                      {s.hostile_count}
                      {s.decoy_count > 0 && <span className="faint"> +{s.decoy_count} decoy</span>}
                    </td>
                    <td className="mono">{s.rule_set}</td>
                    <td className="mono">{s.oracle_score?.toFixed(1) ?? "—"}</td>
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                        <Link className="btn" to={`/briefing/${s.id}`}>
                          Brief
                        </Link>
                        <Link className="btn primary" to={`/sim/${s.id}`}>
                          Play
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="faint">
                      No scenarios match this filter. Run <code>make seed</code> if the library is
                      empty.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {recommended.length > 0 && (
        <div className="hint">
          {recommended[0]?.recommended_reason ??
            "Your recommendation appears once you have completed at least one session."}
        </div>
      )}

      <Panel
        title="Generate a scenario"
        actions={
          <button
            type="button"
            className="btn primary"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
          >
            {generate.isPending ? "Generating…" : "Generate"}
          </button>
        }
      >
        <p className="faint" style={{ marginTop: 0 }}>
          Every scenario is generated from its seed, so the same choice always gives the same
          exercise. Pick the terrain, the light and how many drones are coming.
        </p>
        <div className="row wrap" style={{ gap: 12 }}>
          <label className="field">
            <span>Terrain</span>
            <select value={terrain} onChange={(e) => setTerrain(e.target.value)}>
              {TERRAINS.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Light</span>
            <select value={timeOfDay} onChange={(e) => setTimeOfDay(e.target.value)}>
              {TIMES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Threat</span>
            <select value={composition} onChange={(e) => setComposition(e.target.value)}>
              {COMPOSITIONS.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {generate.isError && <ErrorNote message={(generate.error as Error).message} />}
      </Panel>
    </div>
  )
}
