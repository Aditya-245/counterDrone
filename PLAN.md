# SKYGYM — Implementation Plan

**SIH 2026, Problem Statement 26247 — AI-Enabled Drone & Counter-Drone Threat Simulation Trainer**

This is the authoritative phase plan. Each phase lists its deliverables and its acceptance
checks. A phase is only closed when every acceptance check has been **executed and passed**.

Rule of engagement for this plan: *verify, don't assume*. The evidence for each closed phase is
recorded in `docs/evidence/PX.md`.

---

## Design decisions that shape every phase

| Concern | Decision |
|---|---|
| Determinism | Single `numpy.random.Generator` (PCG64) owned by `SimEngine`, threaded explicitly into every stochastic call. No `random`, no wall clock in `core/`. State hash = BLAKE2b over a canonical float64 byte encoding of the state. |
| State model | `WorldState` is a frozen-ish dataclass tree that is structurally serialisable (`to_dict`/`from_dict`), so `step()` is a pure function. |
| Terrain | Procedural value-noise + ridged noise heightfield over a 6 km × 6 km ENU area, `256×256` grid (23.4 m posts). Building boxes for `urban`. PNG heightmap import via Pillow. |
| Sensors | Four classes behind one ABC: `RadarSensor`, `EoIrSensor`, `RfSensor`, `AcousticSensor`. All subclass `AbstractSensor.observe(world, rng) -> list[Detection]`. |
| Tracking | Own constant-velocity Kalman filter (NumPy) + gated nearest-neighbour association + M-of-N confirmation + track deletion. No external filter library. |
| Ground truth | The engine never leaks ground truth to the client. `core/` has `ground_truth()` as an explicit, separate view used by ROE/scoring/oracle only. |
| Effectors | Abstract `Effector-S` (soft-kill) / `Effector-H` (hard-kill). Generic ranges only, documented as synthetic in `README.md`. |
| ML | Red Brain **Layer A** (own LinUCB in NumPy) is required and always available. **Layer B** (Gymnasium + SB3 PPO) is optional and lazily imported; the app must run without the policy file. |
| LLM | Optional Ollama client, `urllib`-based (no extra runtime dep), strict JSON-only parsing, never `eval`. Rule-based parser is the guaranteed fallback. |
| Frontend | React 18 + Vite 5 + TS strict + @react-three/fiber v8 (React 18 compatible) + drei v9 + Recharts + Zustand + TanStack Query. |
| Offline | No CDN, no remote fonts, no external API at runtime. All assets local. |

---

## Phase list

### P0 — Setup
**Deliverables**
- Repo skeleton, `pyproject.toml`, `backend/requirements.txt` (pinned), `frontend/package.json` (pinned),
  `Makefile` with `dev`, `check`, `demo`, `seed`, `test`, `lint`, `typecheck`, `install`.
- `backend/app/main.py` FastAPI app with `/api/health`, CORS, static frontend serving in production.
- `frontend` Vite scaffold with strict TS and a placeholder page.
- `ruff`, `mypy` (core only), `prettier`, `vitest` configured.

**Acceptance**
- `make check` → green.
- `make dev` → backend on :8000 serving `/api/health`, frontend on :5173 proxying `/api`.

---

### P1 — Core engine
**Deliverables**
- `core/schemas.py` — every Pydantic v2 model from section 4 + `SCHEMA_VERSION` + strict version
  rejection. JSON Schema exported to `docs/schema/`.
- `core/terrain.py` — value noise, ridged noise, presets, `TerrainSpec`, building boxes, LOS,
  heightmap import/export.
- `core/entities.py` — the class table (`quad_small`, `fpv_fast`, `fixed_wing`, `bird_flock`,
  `kite_balloon`, `friendly_uav`, `civil_aircraft`) with generic synthetic parameters.
- `core/world.py` — `WorldState`, `EntityState`, env, site, ring crossings, serialisation, hash.
- `core/engine.py` — fixed 10 Hz `step(state, actions, rng) -> state'`, event scheduling, spawn logic.
- `core/generator.py` — minimal seed → `Scenario` (full features in P7).

**Acceptance**
- Determinism test: same seed + same action log → identical `final_state_hash`, twice in a row and
  across a save/load round trip.
- `python -m app.core.cli_headless` prints entity paths for a sample scenario.

---

### P2 — Sensors + tracking
**Deliverables**
- `core/sensors.py` — radar, EO/IR, RF, acoustic; degradations; `SensorHealth`.
- `core/tracking.py` — Kalman filter, gating, M-of-N initiation, deletion, fusion, covariance ellipse.

**Acceptance**
- Sensor statistical tests pass (Pd vs range / RCS / fog; RF never detects `rf_active=False`).
- Tracking continuity test on a synthetic straight-line target; false-track rate bounded.
- Headless run produces tracks with a realistic false-alarm count.

---

### P3 — ROE + scoring + oracle
**Deliverables**
- `core/roe.py` — data-driven decision tree loaded from JSON rule sets, `STRICT` and `STANDARD`.
- `core/scoring.py` — ground-truth timeline, measured behaviour, `ScoreBreakdown`, coaching points.
- `core/oracle.py` — optimal-decision ghost with realistic perception delay.

**Acceptance**
- ROE table-driven tests for every verdict + hypothesis property test.
- Scoring monotonicity test.
- Oracle ≥ 85 on all shipped scenarios; deliberately bad scripted trainee < 40.

---

### P4 — Backend API + realtime
**Deliverables**
- `app/db/` — SQLAlchemy 2 models, Alembic migration, session management.
- `app/api/` — `scenarios`, `sessions`, `aar`, `analytics`, `users`, `builder`, `instructor`.
- WebSocket `/ws/session/{id}` — authoritative server, `state_frame` out, `TraineeAction` in,
  reconnect-with-resume.
- `make seed` — demo unit, 6 trainees, 20 synthetic sessions flagged synthetic.

**Acceptance**
- API contract tests for every endpoint + WebSocket flow.
- Scripted client plays a session over WebSocket; score stored in SQLite.

---

### P5 — Frontend core
**Deliverables**
- `Library`, `Briefing`, `Sim` pages.
- `scene/` r3f tactical view (instanced, LOD), `hud/` scope + camera + track table + sensor strip,
  `charts/`.

**Acceptance**
- A human can play a full scenario in the browser end to end.
- Frame budget met (60 fps target for ≤ 40 drones; measured and recorded).
- Component tests for the track table and HUD.

---

### P6 — AAR + counterfactual fork
**Deliverables**
- Replay from seed + action log, synchronised timeline, ghost track overlay, scrubber.
- Fork endpoint: re-simulate from any decision point with an injected action.
- Sector-attention heatmap.

**Acceptance**
- Forking at a missed engagement shows a different, correct outcome.
- Unforked replay reproduces the original `final_state_hash` exactly.

---

### P7 — Skill model + generator + adaptive selection + dashboard
**Deliverables**
- `core/skills.py` — Elo/Bayesian skill update, habit features, history.
- Full `core/generator.py` — difficulty-driven, validated, oracle-sanity-checked.
- `generator.next_for(trainee)` targeting ~70 % predicted success on the weakest skill.
- `Dashboard` page: skill radar, score trend, latency histogram, confusion matrix, verdict breakdown,
  habit insights; CSV export + print stylesheet; unit view.

**Acceptance**
- After 10 simulated sessions the recommended scenario targets the weakest skill.
- 200 random seeds all valid and oracle-solvable; pairwise structural distance above threshold.
- Skill rating rises with repeated good performance, falls with repeated poor performance, stays in [0,100].
- Dashboard renders every chart from real stored data.

---

### P8 — Red Brain Layer A + habit panel
**Deliverables**
- `core/redbrain.py` — tactic space, LinUCB, reward, per-trainee state persistence, seeded selection.
- "Adversary adapted to you" panel backed by the logged data.

**Acceptance**
- Bandit test: with a simulated trainee that has a fixed blind spot, the chosen tactic exploits that
  blind spot more often after N sessions than at the start.
- Every claim in the UI panel is backed by a stored statistic.

---

### P9 — NL builder + Builder page + Instructor page
**Deliverables**
- `core/nl_builder.py` — Ollama (optional) → validate → ≤2 retries → deterministic rule-based parser.
- `Builder` page (structured form + NL box + seed + preview), `Instructor` page (ROE pick, force
  difficulty, lock seed, live session list).

**Acceptance**
- 20 sample prompts produce valid scenarios on the LLM-disabled path.
- LLM path tested with a mocked client including malformed-JSON retries.
- Instructor can lock a seed for a cohort; all cohort members get a byte-identical scenario.

---

### P10 — Digital twin editor + 12 shipped scenarios
**Deliverables**
- Site editor: place protected zone, rings, civil sectors, sensors; save as scenario template.
- 12 hand-authored scenarios in `scenarios/` covering the section-10 list.

**Acceptance**
- All shipped scenarios pass `validate()` and are oracle-solvable (score ≥ 85).

---

### P11 — (optional) Red Brain Layer B, then team mode
**Deliverables**
- `backend/training/` Gymnasium env + SB3 PPO training script + saved policy; `redbrain.py` lazy loader.
- WebSocket team rooms with three roles and role-handoff scoring.

**Acceptance**
- The app still runs correctly with the policy file absent (tests cover both paths).

---

### P12 — Polish + docs
**Deliverables**
- `README.md`, `docs/architecture.md` (+ diagram), `docs/scoring.md`, `docs/demo_script.md`,
  screenshots, `DECISIONS.md` completed.

**Acceptance**
- `make check` green from a fresh clone; a newcomer can reach `make dev` using only the README.

---

## Definition of done (final checklist, section 13)

- [ ] `make check` output, all green
- [ ] Determinism test output
- [ ] Oracle score table for all 12+ shipped scenarios
- [ ] Performance numbers (server tick ms, frontend fps)
- [ ] Honest list of anything not implemented, with reasons
- [ ] `DECISIONS.md` completed
- [ ] Demo script tested end to end