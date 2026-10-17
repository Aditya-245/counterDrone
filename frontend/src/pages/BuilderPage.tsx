import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"
import {
  DEFAULT_DIFFICULTY,
  DIFFICULTY_AXES,
  type DifficultyAxis,
  type DifficultyVector,
} from "@/api/types"
import { Panel, Spinner, ErrorNote } from "@/components/ui"

export default function BuilderPage(): JSX.Element {
  const [text, setText] = useState(
    "night attack on a fuel depot, 12 drones, 3 decoy birds, light fog, radar fails after 2 minutes",
  )
  const [seed, setSeed] = useState<number | "">("")
  const [useLlm, setUseLlm] = useState(true)
  const [difficulty, setDifficulty] = useState<DifficultyVector>(DEFAULT_DIFFICULTY)
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.buildFromText>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const status = useQuery({ queryKey: ["builder-status"], queryFn: api.builderStatus })
  const validate = useQuery({
    queryKey: ["validate", result?.scenario?.id ?? null],
    queryFn: () => api.validateScenario(result!.scenario!),
    enabled: Boolean(result?.scenario),
  })

  const build = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.buildFromText({
          text,
          seed: seed === "" ? undefined : Number(seed),
          use_llm: useLlm,
          persist: true,
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const generate = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      const scenario = await api.generate({
        seed: seed === "" ? undefined : Number(seed),
        difficulty,
        tags: ["generated"],
        persist: true,
      })
      setResult({
        schema_version: scenario.schema_version,
        scenario,
        path: "rule_based",
        explanation: "Generated from the structured difficulty controls.",
        attempts: 0,
        validation_errors: [],
        llm_available: false,
        sanity_ok: true,
        sanity_message: "",
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid cols-2">
      <Panel title="Natural language">
        <label htmlFor="nl-text">Describe the exercise</label>
        <textarea id="nl-text" rows={5} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <div>
            <label htmlFor="nl-seed">Seed (optional)</label>
            <input
              id="nl-seed"
              type="number"
              value={seed}
              onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))}
              style={{ width: 120 }}
            />
          </div>
          <label className="row" style={{ gap: 6, marginTop: 18 }}>
            <input
              type="checkbox"
              style={{ width: "auto" }}
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
            />
            try the local LLM first
          </label>
          <span className="spacer" />
          <button
            type="button"
            className="btn primary"
            disabled={busy}
            onClick={() => void build()}
          >
            {busy ? "Building…" : "Build"}
          </button>
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          {status.data
            ? status.data["llm_available"]
              ? "Local LLM is reachable — the builder will try it first and fall back to the deterministic parser if the output does not validate."
              : "No local LLM reachable — the deterministic rule-based parser will run. That is a supported path, not a degraded one."
            : "Checking local LLM availability…"}
        </div>
        {error && <div className="error">{error}</div>}
      </Panel>

      <Panel title="Structured difficulty">
        <div className="grid cols-2">
          {DIFFICULTY_AXES.map((axis: DifficultyAxis) => (
            <div className="field" key={axis}>
              <label htmlFor={`ax-${axis}`}>{axis.replace(/_/g, " ")}</label>
              <input
                id={`ax-${axis}`}
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
        <button type="button" className="btn" disabled={busy} onClick={() => void generate()}>
          Generate from these axes
        </button>
      </Panel>

      <Panel title="Result">
        {!result ? (
          <Spinner label="Nothing built yet" />
        ) : (
          <div data-testid="nl-result">
            <div className="row">
              <span className="badge ok">path: {result.path}</span>
              <span className={`badge ${result.sanity_ok ? "ok" : "crit"}`}>
                sanity: {result.sanity_ok ? "passed" : "failed"}
              </span>
              {result.llm_available && <span className="badge">llm tried</span>}
            </div>
            <p className="muted">{result.explanation}</p>
            {result.sanity_message && <div className="faint mono">{result.sanity_message}</div>}
            {validate.data && (
              <div className={validate.data.valid ? "badge ok" : "badge crit"}>
                server validation: {validate.data.valid ? "valid" : validate.data.errors.join("; ")}
              </div>
            )}
            {result.validation_errors.length > 0 && (
              <details>
                <summary className="faint">
                  {result.validation_errors.length} LLM validation error(s)
                </summary>
                <ul className="faint mono" style={{ fontSize: 11 }}>
                  {result.validation_errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </details>
            )}
            {result.scenario && (
              <div className="row" style={{ marginTop: 10 }}>
                <strong>{result.scenario.name}</strong>
                <span className="spacer" />
                <Link className="btn" to={`/briefing/${result.scenario.id}`}>
                  Brief it
                </Link>
                <Link className="btn primary" to={`/sim/${result.scenario.id}`}>
                  Play it
                </Link>
              </div>
            )}
          </div>
        )}
      </Panel>

      <Panel title="How the builder decides">
        <ol className="muted" style={{ paddingLeft: 18, margin: 0 }}>
          <li>
            Ask the local Ollama model for one Scenario JSON, constrained by the Pydantic schema.
          </li>
          <li>
            Validate with Pydantic. On failure, retry at most twice with the validation error
            appended.
          </li>
          <li>
            If the LLM is unavailable or still failing, fall back to the deterministic parser:
            keyword and number extraction into a DifficultyVector, then the procedural generator.
          </li>
          <li>
            Either way the result must pass a headless sanity simulation before it is offered.
          </li>
          <li>Model output is parsed as JSON only — never executed, never evaluated.</li>
        </ol>
        {error && <ErrorNote message={error} />}
      </Panel>
    </div>
  )
}
