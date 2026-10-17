import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import { DEFAULT_DIFFICULTY, DIFFICULTY_AXES, type DifficultyVector } from "@/api/types"
import { ErrorNote, Panel, Spinner } from "@/components/ui"

interface TreeNode {
  id: string
  condition: { op: string; value?: unknown }
  leaf?: Record<string, unknown>
  branches?: TreeNode[]
}

export default function InstructorPage(): JSX.Element {
  const [ruleSet, setRuleSet] = useState("STANDARD")
  const [seed, setSeed] = useState(20260001)
  const [difficulty, setDifficulty] = useState<DifficultyVector>(DEFAULT_DIFFICULTY)
  const [name, setName] = useState("Cohort exercise 01")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<Record<string, unknown> | null>(null)

  const ruleSets = useQuery({ queryKey: ["rule-sets"], queryFn: api.ruleSets })
  const tree = useQuery({
    queryKey: ["rule-set", ruleSet],
    queryFn: () => api.ruleSet(ruleSet),
  })
  const roster = useQuery({ queryKey: ["roster"], queryFn: () => api.roster() })
  const live = useQuery({
    queryKey: ["live-sessions"],
    queryFn: api.liveSessions,
    refetchInterval: 3000,
  })

  const createExercise = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      setCreated(
        await api.createExercise({
          name,
          seed,
          difficulty,
          rule_set: ruleSet,
          trainee_ids: (roster.data?.items ?? []).map((u) => u.id),
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const scenarioId = typeof created?.["scenario_id"] === "string" ? created["scenario_id"] : null

  return (
    <div className="grid cols-2">
      <Panel
        title="Rules of engagement"
        actions={
          <select
            value={ruleSet}
            onChange={(e) => setRuleSet(e.target.value)}
            style={{ width: 160 }}
          >
            {(ruleSets.data?.items ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.id}
              </option>
            ))}
          </select>
        }
      >
        {tree.isLoading && <Spinner label="Loading rule set" />}
        {tree.error instanceof Error && <ErrorNote message={tree.error.message} />}
        {tree.data && (
          <>
            <p className="muted">{String(tree.data["description"] ?? "")}</p>
            <TreeView node={tree.data["tree"] as TreeNode} depth={0} />
          </>
        )}
      </Panel>

      <Panel title="Locked-seed exercise">
        <div className="field">
          <label htmlFor="ex-name">Exercise name</label>
          <input id="ex-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ex-seed">Seed (locked — every trainee gets identical bytes)</label>
          <input
            id="ex-seed"
            type="number"
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value))}
          />
        </div>
        <div className="grid cols-2">
          {DIFFICULTY_AXES.map((axis) => (
            <div className="field" key={axis}>
              <label htmlFor={`ix-${axis}`}>{axis.replace(/_/g, " ")}</label>
              <input
                id={`ix-${axis}`}
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={difficulty[axis]}
                onChange={(e) => setDifficulty((d) => ({ ...d, [axis]: Number(e.target.value) }))}
              />
              <span className="mono faint">{difficulty[axis].toFixed(2)}</span>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() => void createExercise()}
        >
          {busy ? "Creating…" : "Create locked exercise"}
        </button>
        {error && <div className="error">{error}</div>}
        {created && (
          <div className="hint" style={{ marginTop: 10 }} data-testid="exercise-result">
            {String(created["message"] ?? "")}
            <div style={{ marginTop: 6 }}>
              ROE <strong>{String(created["rule_set"] ?? "")}</strong>, seed{" "}
              <span className="mono">{String(created["seed"] ?? "")}</span>
              {scenarioId && (
                <>
                  {" — "}
                  <Link to={`/briefing/${scenarioId}`}>brief</Link> /{" "}
                  <Link to={`/sim/${scenarioId}`}>play</Link>
                </>
              )}
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Roster">
        {roster.isLoading && <Spinner label="Loading roster" />}
        <table>
          <thead>
            <tr>
              <th>Trainee</th>
              <th>Role</th>
              <th>Data</th>
            </tr>
          </thead>
          <tbody>
            {(roster.data?.items ?? []).map((u) => (
              <tr key={u.id}>
                <td>{u.display_name}</td>
                <td className="mono">{u.role}</td>
                <td>{u.synthetic ? <span className="badge warn">synthetic</span> : "real"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="Live sessions">
        {(live.data ?? []).length === 0 ? (
          <div className="faint">Nobody is running a session right now.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Trainee</th>
                <th>Scenario</th>
                <th>Seed</th>
              </tr>
            </thead>
            <tbody>
              {(live.data ?? []).map((s) => (
                <tr key={s.id}>
                  <td>{s.trainee_name}</td>
                  <td>{s.scenario_name}</td>
                  <td className="mono">{s.seed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}

function TreeView({ node, depth }: { node: TreeNode; depth: number }): JSX.Element {
  if (!node) return <></>
  const value = node.condition?.value
  const label = `${node.condition?.op ?? "always"}${value !== undefined ? ` = ${JSON.stringify(value)}` : ""}`
  return (
    <div style={{ marginLeft: depth * 14 }}>
      <div className="mono" style={{ fontSize: 12 }}>
        <span style={{ color: "var(--accent)" }}>{node.id}</span>{" "}
        <span className="faint">{label}</span>
      </div>
      {node.leaf ? (
        <div className="mono" style={{ fontSize: 12, color: "var(--ok)" }}>
          → {String(node.leaf["verdict"])} ({String(node.leaf["points"])})
          <div className="faint">{String(node.leaf["explanation"] ?? "")}</div>
        </div>
      ) : (
        (node.branches ?? []).map((b) => <TreeView key={b.id} node={b} depth={depth + 1} />)
      )}
    </div>
  )
}
