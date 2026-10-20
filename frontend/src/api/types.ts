/**
 * Typed mirrors of the backend Pydantic models.
 *
 * Field names match the wire format exactly (snake_case); nothing here is
 * invented, and every optional field is `| null` because the API sends `null`
 * rather than omitting keys.
 */

export const SCHEMA_VERSION = 3

export type TimeOfDay = "day" | "dusk" | "night"
export type TerrainPreset = "urban" | "rural" | "hills"
export type SensorType = "radar" | "eo_ir" | "rf" | "acoustic"
export type DegradationState = "none" | "partial" | "failed"
export type EntityClass =
  | "quad_small"
  | "fpv_fast"
  | "fixed_wing"
  | "bird_flock"
  | "kite_balloon"
  | "friendly_uav"
  | "civil_aircraft"
export type LabelType = "hostile" | "civil" | "friendly" | "bird"
/**
 * What a trainee can call a contact: one of the four identities, or an explicit
 * "cannot tell". The server has no `unknown` identity (`LabelType` is ground
 * truth), so an `unknown` call scores exactly like leaving the contact
 * unclassified - the confusion matrix's `unknown` column.
 */
export type TraineeCall = LabelType | "unknown"
export type RuleSetId = "STRICT" | "STANDARD" | "PERMISSIVE"
export type Verdict =
  | "CORRECT_ENGAGE"
  | "CORRECT_HOLD"
  | "LATE_ENGAGE"
  | "MISSED_THREAT"
  | "FALSE_ENGAGE_CIVIL"
  | "FALSE_ENGAGE_FRIENDLY"
  | "COLLATERAL_VIOLATION"
  | "PREMATURE_ENGAGE"
  | "UNNECESSARY_ESCALATION"

export type ActionType =
  | "ACK_TRACK"
  | "CLASSIFY"
  | "SET_PRIORITY"
  | "STEER_SENSOR"
  | "WARN"
  | "ENGAGE_S"
  | "ENGAGE_H"
  | "HOLD_FIRE"
  | "REQUEST_ID"
  | "PAUSE_REQUEST"

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface DifficultyVector {
  schema_version: number
  swarm_size: number
  approach_axes: number
  time_pressure: number
  sensor_degradation_level: number
  decoy_ratio: number
  visibility: number
  night: number
  ambiguity: number
  simultaneity: number
  roe_strictness: number
}

export const DIFFICULTY_AXES = [
  "swarm_size",
  "approach_axes",
  "time_pressure",
  "sensor_degradation_level",
  "decoy_ratio",
  "visibility",
  "night",
  "ambiguity",
  "simultaneity",
  "roe_strictness",
] as const satisfies readonly (keyof DifficultyVector)[]

export type DifficultyAxis = (typeof DIFFICULTY_AXES)[number]

export const DEFAULT_DIFFICULTY: DifficultyVector = {
  schema_version: SCHEMA_VERSION,
  swarm_size: 0.3,
  approach_axes: 0.3,
  time_pressure: 0.3,
  sensor_degradation_level: 0.2,
  decoy_ratio: 0.2,
  visibility: 0.7,
  night: 0,
  ambiguity: 0.2,
  simultaneity: 0.3,
  roe_strictness: 0.3,
}

export interface SensorDegradation {
  t: number
  state: DegradationState
  note: string
}

export interface SensorSpec {
  id: string
  type: SensorType
  x: number
  y: number
  z: number
  fov_deg: number
  min_range_m: number
  max_range_m: number
  initial_state: DegradationState
  degradations: SensorDegradation[]
  params: Record<string, unknown>
  drift_mps: number
}

export interface BehaviourSpec {
  kind: string
  speed_mps: number | null
  terminal_altitude_m: number
  separation_m: number
  terrain_following: boolean
  min_clearance_m: number
}

export interface BearingPoint {
  bearing_deg: number
  range_m: number
  altitude_m: number
}

export interface ThreatSpec {
  name: string
  entity_class: EntityClass
  label: LabelType
  count: number
  spawn_t: number
  spawn_window_s: number
  spawn_bearing_deg: number
  spawn_jitter_deg: number
  behaviour: BehaviourSpec
  formation: string
  rf_active: boolean | null
  beacon_active: boolean | null
  hint_bias: number
}

export interface EffectorSpec {
  id: string
  kind: "soft" | "hard"
  max_range_m: number
  cooldown_s: number
  ammunition: number
  base_success: number
}

export interface CivilSector {
  bearing_deg: number
  half_width_deg: number
  inner_m: number
  outer_m: number
  label: string
}

export interface SiteSpec {
  protected_radius_m: number
  ring_distances_m: number[]
  civil_sectors: CivilSector[]
}

export interface EnvSpec {
  time_of_day: TimeOfDay
  visibility_m: number
  rain: number
  wind_speed_mps: number
  wind_heading_deg: number
}

export interface BuildingSpec {
  x: number
  y: number
  width_m: number
  depth_m: number
  height_m: number
  rotation_deg: number
}

/** Road classes, widest first: the width drives the carriageway. */
export type RoadKind = "arterial" | "street" | "lane" | "track"
/** Building archetypes the renderer builds procedurally. */
export type BuildingKind =
  | "house_gable"
  | "house_hip"
  | "house_flat"
  | "shop"
  | "apartment"
  | "warehouse"
  | "shed"
  | "tower"
export type TreeKind = "broadleaf" | "conifer" | "dead"
export type PropKind = "fence" | "wall" | "hedge" | "lamp" | "vehicle" | "haystack" | "silo"
export type WaterKind = "pond" | "stream"

export interface RoadSpec {
  kind: RoadKind
  width_m: number
  markings: boolean
  points: [number, number][]
}

export interface BuildingInstance {
  x: number
  y: number
  width_m: number
  depth_m: number
  height_m: number
  rotation_deg: number
  kind: BuildingKind
  occluder: boolean
}

export interface TreeInstance {
  x: number
  y: number
  height_m: number
  radius_m: number
  kind: TreeKind
}

export interface PropInstance {
  x: number
  y: number
  rotation_deg: number
  length_m: number
  kind: PropKind
}

export interface WaterSpec {
  kind: WaterKind
  width_m: number
  points: [number, number][]
}

export interface FieldPatch {
  index: number
  points: [number, number][]
}

/**
 * The generated world for one scenario: ground, roads, blocks, water.
 *
 * `heights_b64` is a little-endian int16 grid of centimetres and `landcover_b64`
 * a uint8 grid indexing `palette`; both are `grid_n` square.
 */
export interface LandscapeSpec {
  scenario_id: string
  seed: number
  size_m: number
  grid_n: number
  max_height_m: number
  heights_b64: string
  landcover_b64: string
  palette: string[]
  roads: RoadSpec[]
  buildings: BuildingInstance[]
  fields: FieldPatch[]
  trees: TreeInstance[]
  props: PropInstance[]
  water: WaterSpec[]
}

export interface TerrainSpec {
  preset: TerrainPreset
  size_m: number
  resolution: number
  scale_m: number
  roughness: number
  seed: number
  heightmap_path: string | null
  buildings: BuildingSpec[]
}

export interface RoeSpec {
  rule_set: RuleSetId
  require_id_before_engage: boolean
  allowed_engage_labels: LabelType[]
  friend_or_civil_warning_required: boolean
  collateral_sector_tolerance: number
  late_engage_threshold_s: number
  min_track_quality: number
  require_warning_before_engage: boolean
}

export interface ScenarioEvent {
  t: number
  kind: string
  target: string | null
  params: Record<string, unknown>
  description: string
}

export interface Scenario {
  schema_version: number
  id: string
  name: string
  description: string
  seed: number
  duration_s: number
  terrain: TerrainSpec
  environment: EnvSpec
  site: SiteSpec
  sensors: SensorSpec[]
  threats: ThreatSpec[]
  decoys: ThreatSpec[]
  effectors: EffectorSpec[]
  roe: RoeSpec
  events: ScenarioEvent[]
  difficulty: DifficultyVector
  tags: string[]
  instructor_locked: boolean
  source: string
}

export interface ScenarioSummary {
  id: string
  name: string
  description: string
  seed: number
  duration_s: number
  difficulty: number
  tags: string[]
  source: string
  threat_count: number
  hostile_count: number
  decoy_count: number
  terrain: string
  time_of_day: string
  rule_set: string
  oracle_score: number | null
  recommended: boolean
  recommended_reason: string
}

export interface ScenarioList {
  items: ScenarioSummary[]
  total: number
  tags: string[]
  rule_sets: string[]
}

export interface TrackView {
  id: string
  x: number
  y: number
  z: number
  vx: number
  vy: number
  vz: number
  position_sigma_m: number
  velocity_sigma_mps: number
  quality: number
  age_s: number
  sensors: string[]
  suggested_class: EntityClass | null
  suggested_label: LabelType | null
  class_confidence: number
  range_m: number
  bearing_deg: number
  altitude_m: number
  speed_mps: number
  radial_mps: number
  time_to_perimeter_s: number | null
  acknowledged: boolean
  trainee_class: LabelType | null
  trainee_priority: number | null
  neutralised: boolean
  missed: boolean
  confirmed: boolean
}

export interface SensorHealthView {
  id: string
  type: SensorType
  state: DegradationState
  azimuth_deg: number
  fov_deg: number
  detections_last_step: number
  note: string
}

export interface EffectorView {
  id: string
  kind: "soft" | "hard"
  ready: boolean
  cooldown_remaining_s: number
  ammunition: number
  max_range_m: number
}

export interface SimEvent {
  t: number
  kind: string
  entity_id: string | null
  track_id: string | null
  message: string
  data: Record<string, unknown>
}

export interface StateFrame {
  session_id: string
  t: number
  step: number
  duration_s: number
  status: "running" | "paused" | "finished"
  tracks: TrackView[]
  sensors: SensorHealthView[]
  effectors: EffectorView[]
  events: SimEvent[]
  ammo_remaining: Record<string, number>
  paused: boolean
  allow_pause: boolean
}

export interface TraineeAction {
  t: number
  type: ActionType
  track_id?: string | null
  params: Record<string, unknown>
  source?: "trainee" | "oracle" | "scripted" | "instructor"
}

export interface VerdictRecord {
  entity_id: string
  track_id: string | null
  t: number
  verdict: Verdict
  points: number
  path: string[]
  explanation: string
  true_label: LabelType
  trainee_label: LabelType | null
  effector: string | null
  range_m: number | null
  collateral_risk: boolean
}

export interface CoachingPoint {
  code: string
  severity: "info" | "warn" | "critical"
  text: string
  related_entities: string[]
}

export interface EntityScore {
  entity_id: string
  entity_class: EntityClass
  true_label: LabelType
  t_first_detectable: number | null
  t_first_ack: number | null
  detection_latency_s: number | null
  trainee_label: LabelType | null
  t_classified: number | null
  classification_correct: boolean | null
  classification_calibration: string
  t_engaged: number | null
  engage_range_m: number | null
  effector: string | null
  neutralised: boolean
  reached_perimeter: boolean
  t_perimeter: number | null
  t_closest_approach: number | null
  ring_crossings: Record<string, number>
  verdicts: VerdictRecord[]
  score: number
}

export interface ScoreBreakdown {
  schema_version: number
  total: number
  raw_total: number
  detection: number
  classification: number
  prioritisation: number
  engagement: number
  workload: number
  entities: EntityScore[]
  verdicts: VerdictRecord[]
  verdict_counts: Record<string, number>
  coaching_points: CoachingPoint[]
  capped: boolean
  cap_reason: string | null
  stats: Record<string, unknown>
}

export interface SessionRecord {
  schema_version: number
  id: string
  scenario_id: string
  seed: number
  trainee_id: string
  started_at: string
  ended_at: string | null
  actions: TraineeAction[]
  events_log: SimEvent[]
  final_state_hash: string
  score: ScoreBreakdown | null
  difficulty: DifficultyVector
  aborted: boolean
  synthetic: boolean
}

export interface GhostAction {
  t: number
  type: ActionType
  track_id: string | null
  entity_id: string | null
  params: Record<string, unknown>
  rationale: string
}

export interface ReplayEntity {
  id: string
  entity_class: EntityClass
  label: LabelType
  path: { t: number; x: number; y: number; z: number }[]
  t_first_detectable: number | null
  ring_crossings: Record<string, number>
  t_closest_approach: number | null
  neutralised_t: number | null
  reached_perimeter_t: number | null
}

export interface ReplaySession {
  schema_version: number
  session_id: string
  scenario_id: string
  duration_s: number
  final_state_hash: string
  ticks_per_second: number
  trainee_actions: TraineeAction[]
  ghost_actions: GhostAction[]
  entities: ReplayEntity[]
  frames: StateFrame[]
  events: SimEvent[]
  sector_attention: number[]
  score: ScoreBreakdown
}

export interface ForkResult {
  schema_version: number
  fork_t: number
  original_hash: string
  forked_hash: string
  diverged: boolean
  original_score: number
  forked_score: number
  score_delta: number
  original_outcome: Record<string, unknown>
  forked_outcome: Record<string, unknown>
  explanation: string
  ghost_actions: GhostAction[]
  newly_appended_actions: TraineeAction[]
}

export interface HabitExploit {
  habit: string
  description: string
  evidence: Record<string, unknown>
  confidence: number
}

export interface RedBrainReport {
  trainee_id: string
  tactic_id: string
  tactic_name: string
  tactics_considered: number
  exploration_pulls: number
  total_pulls: number
  exploits: HabitExploit[]
  available_tactics: string[]
  learned_policy_available: boolean
}

export interface HabitFeatures {
  sector_scan_share: number[]
  mean_detection_latency_by_sector: number[]
  over_engage_rate: number
  under_engage_rate: number
  saturation_reaction_ms: number
  mean_actions_per_10s: number
  classify_early_rate: number
  hold_fire_rate: number
  least_scanned_sector: number
  most_scanned_sector: number
  sessions_observed: number
  total_actions: number
}

export interface SkillVector {
  schema_version: number
  detection: number
  classification: number
  prioritisation: number
  engagement: number
  roe: number
}

export const SKILL_NAMES = [
  "detection",
  "classification",
  "prioritisation",
  "engagement",
  "roe",
] as const satisfies readonly (keyof Omit<SkillVector, "schema_version">)[]

export interface HealthResponse {
  status: string
  app: string
  version: string
  schema_version: number
  python: string
  platform: string
  tick_hz: number
  database: string
  db_reachable: boolean
  learned_policy: { available: boolean; reason: string; path: string }
}

export interface UserOut {
  id: string
  display_name: string
  role: string
  unit_id: string | null
  synthetic: boolean
  session_count: number
}

export interface SessionListItem {
  id: string
  scenario_id: string
  scenario_name: string
  trainee_id: string
  trainee_name: string
  seed: number
  started_at: string
  total_score: number | null
  synthetic: boolean
}

export interface TraineeDashboard {
  trainee: { id: string; display_name: string; unit_id: string | null; synthetic: boolean }
  skills: SkillVector
  skill_history: Record<string, number>[]
  weakest_skill: string | null
  sessions: Record<string, number | string | boolean>[]
  session_count: number
  mean_score: number | null
  detection_latencies: number[]
  classification_confusion: Record<string, Record<string, number>>
  verdicts_over_time: { session_id: string; t: string; counts: Record<string, number> }[]
  habits: HabitFeatures | null
  redbrain: RedBrainReport
  synthetic_note: string | null
}
