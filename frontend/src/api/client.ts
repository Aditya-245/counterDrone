/** Thin typed REST client. Every response is validated only by TypeScript; the
 *  server is the authority and rejects unknown `schema_version` values. */

import type {
  DifficultyVector,
  ForkResult,
  HealthResponse,
  LandscapeSpec,
  RedBrainReport,
  ReplaySession,
  Scenario,
  ScenarioList,
  ScoreBreakdown,
  SessionListItem,
  SessionRecord,
  StateFrame,
  TimeOfDay,
  TraineeAction,
  TraineeDashboard,
  UserOut,
} from "./types"

const BASE = ""

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    let code = "error"
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = (await res.json()) as { detail?: unknown; code?: string }
      if (typeof body?.code === "string") code = body.code
      if (typeof body?.detail === "string") detail = body.detail
      else if (body?.detail && typeof body.detail === "object") {
        detail = String((body.detail as { detail?: string }).detail ?? detail)
      }
    } catch {
      /* keep the status line */
    }
    throw new ApiError(res.status, code, detail)
  }
  return (await res.json()) as T
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  // --- scenarios -------------------------------------------------------
  scenarios: (params: { tag?: string; source?: string; trainee_id?: string } = {}) => {
    const q = new URLSearchParams()
    if (params.tag) q.set("tag", params.tag)
    if (params.source) q.set("source", params.source)
    if (params.trainee_id) q.set("trainee_id", params.trainee_id)
    const qs = q.toString()
    return request<ScenarioList>(`/api/scenarios${qs ? `?${qs}` : ""}`)
  },
  scenario: (id: string) => request<Scenario>(`/api/scenarios/${id}`),
  landscape: (id: string) => request<LandscapeSpec>(`/api/scenarios/${id}/environment`),
  ruleSet: (id: string) => request<Record<string, unknown>>(`/api/scenarios/rulesets/${id}`),
  validateScenario: (scenario: Scenario) =>
    request<{ valid: boolean; errors: string[]; sanity_ok: boolean; sanity_message: string }>(
      "/api/scenarios/validate",
      { method: "POST", body: JSON.stringify(scenario) },
    ),
  generate: (body: {
    seed?: number
    difficulty?: DifficultyVector
    tags?: string[]
    terrain_preset?: string | null
    duration_s?: number | null
    trainee_id?: string | null
    persist?: boolean
    time_of_day?: TimeOfDay | null
    threat_composition?: "single" | "swarm" | null
  }) =>
    request<Scenario>("/api/scenarios/generate", { method: "POST", body: JSON.stringify(body) }),

  // --- sessions --------------------------------------------------------
  createSession: (body: { scenario_id: string; trainee_id: string; allow_pause?: boolean }) =>
    request<SessionRecord>("/api/sessions", { method: "POST", body: JSON.stringify(body) }),
  sessions: (trainee_id?: string) =>
    request<{ items: SessionListItem[]; total: number }>(
      `/api/sessions${trainee_id ? `?trainee_id=${trainee_id}` : ""}`,
    ),
  liveSessions: () => request<SessionListItem[]>("/api/sessions/live"),
  session: (id: string) => request<SessionRecord>(`/api/sessions/${id}`),
  submitResult: (id: string, body: { final_state_hash: string; aborted?: boolean }) =>
    request<SessionRecord>(`/api/sessions/${id}/result`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- AAR -------------------------------------------------------------
  aar: (id: string) => request<ReplaySession>(`/api/aar/${id}`),
  heatmap: (id: string) =>
    request<{ sector_attention: number[]; labels: string[] }>(`/api/aar/${id}/heatmap`),
  fork: (body: {
    session_id: string
    fork_t: number
    replace_index?: number | null
    inject_action?: TraineeAction | null
    replace_all_from?: boolean
  }) => request<ForkResult>("/api/aar/fork", { method: "POST", body: JSON.stringify(body) }),

  // --- analytics -------------------------------------------------------
  dashboard: (traineeId: string) =>
    request<TraineeDashboard>(`/api/analytics/trainee/${traineeId}`),
  unitDashboard: (unitId: string) =>
    request<Record<string, unknown>>(`/api/analytics/unit/${unitId}`),
  exportCsvUrl: (traineeId: string) => `/api/analytics/export/${traineeId}.csv`,

  // --- users / instructor ---------------------------------------------
  users: (unitId?: string) => request<UserOut[]>(`/api/users${unitId ? `?unit_id=${unitId}` : ""}`),
  skills: (traineeId: string) => request<Record<string, unknown>>(`/api/users/${traineeId}/skills`),
  redbrain: (traineeId: string) => request<RedBrainReport>(`/api/users/${traineeId}/redbrain`),

  // --- instructor ------------------------------------------------------
  ruleSets: () =>
    request<{ items: { id: string; title: string; description: string }[] }>(
      "/api/instructor/rule-sets",
    ),
  createExercise: (body: {
    name: string
    seed: number
    difficulty: DifficultyVector
    rule_set: string
    trainee_ids: string[]
  }) =>
    request<Record<string, unknown>>("/api/instructor/exercises", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  roster: (unitId?: string) =>
    request<{ items: { id: string; display_name: string; role: string; synthetic: boolean }[] }>(
      `/api/instructor/roster${unitId ? `?unit_id=${unitId}` : ""}`,
    ),
}

export type { ScoreBreakdown, StateFrame }
