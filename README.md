# SKYGYM — drone & counter-drone decision trainer

**Not a shooting game, a decision-quality gym.** SKYGYM is a browser-based desktop
simulator where a trainee defends a site against single drones and swarms in
procedurally generated scenarios. The trainee sees only what the simulated sensors
report, has to detect, identify, prioritise and decide under a rules-of-engagement
(ROE) tree, and is scored on the *decisions* — not on kills. An after-action review
replays the session with a counterfactual ghost, a skill model tracks the trainee
across sessions, and an adaptive adversary ("Red Brain") learns the trainee's habits
so rote learning does not work.

Built for SIH 2026 problem statement 26247.

---

## Safety and scope boundary (read first)

This is a **training and decision-assessment tool**. It contains only generic,
synthetic, abstract parameters:

- Effectors are abstract and non-attributable: **Effector-S** (soft-kill) and
  **Effector-H** (hard-kill) with generic ranges and ammunition counts.
- Sensors are described by abstract detection parameters (range, dwell, detection
  probability curves). There are **no real radar, RF or jamming specifications,
  frequencies, signatures or waveforms**, and no instructions for building,
  defeating or countering any specific real system.
- Entity classes are synthetic categories (`quad_small`, `fpv_fast`, `fixed_wing`,
  `bird_flock`, `kite_balloon`, `friendly_uav`, `civil_aircraft`).

The system is **offline-first**: the application never contacts the internet at
runtime and requires no API keys. The only optional outbound request is to a
*local* Ollama server (`http://localhost:11434`) for the natural-language scenario
builder; with no Ollama installed the rule-based builder produces valid scenarios
and the UI says which path was used.

---

## Install and run

Windows-first (GNU Make 4.4, Python 3.11, Node 20). Everything is pinned.

```bash
make install          # python venv + pip deps + npm packages
make check            # lint + type checks + fast tests  (the CI gate)
make demo             # seed demo data, then print the demo URLs
make dev              # backend on :8000, frontend on :5173
```

Then open <http://127.0.0.1:5173>.

| Command | What it does |
| --- | --- |
| `make dev` | Start backend (`:8000`) and frontend (`:5173`) together |
| `make check` | Ruff + ESLint + mypy + tsc + fast pytest (the gate) |
| `make check-all` | `make check` + full pytest + prettier + vitest |
| `make test` / `make test-front` / `make test-full` | Backend fast / frontend / full backend incl. slow sweeps |
| `make seed` | Validate the shipped scenarios and load the demo unit (synthetic) |
| `make reset-db` | Delete the SQLite database and recreate the schema |
| `make migrate` / `make migration` | Apply / author Alembic migrations |
| `make schemas` | Export JSON Schema for every wire model into `docs/schema/` |
| `make headless` | Run one scenario headlessly from the command line |
| `make demo` | `make seed` + the demo path |
| `make e2e` | Playwright smoke test against a running stack |

---

## Architecture in one screen

```
browser (React 18 + Vite + @react-three/fiber)
   |  WebSocket: 10 Hz state_frame snapshots        ^ trainee actions
   v                                                |
FastAPI (offline, localhost)  --  live.py + clock.py: server-authoritative 10 Hz
   |  api/*: sessions, aar, scenarios, builder, users, analytics, instructor
   v
core/ (pure, deterministic)  engine.step() -> sensors -> tracking -> ROE -> scoring
   |                                                        oracle / generator / skills / redbrain
   v
SQLite (WAL) + SQLAlchemy 2 + Alembic migrations
```

Full detail: [`docs/architecture.md`](docs/architecture.md). Scoring:
[`docs/scoring.md`](docs/scoring.md). Demo: [`docs/demo_script.md`](docs/demo_script.md).

### Determinism (a hard requirement)

The same scenario seed and the same trainee action log always produce the identical
`final_state_hash`, in-process and across a save/load round trip:

- one seeded `numpy.random.Generator`, passed explicitly — no `random`, no wall
  clock inside `core/`;
- `step()` is pure, and the live session, the headless player, the AAR replay and
  the counterfactual fork all call the same function;
- the hash is BLAKE2b over a canonical little-endian float64 encoding;
- identity resolution inside the tracker is order-independent (smallest-id
  tie-break, blank ids never recorded) — this fixed a real bug where the same
  scenario scored differently under different `PYTHONHASHSEED` values.

The AAR displays the stored hash next to the replayed hash; `make check` runs the
determinism tests, including a multi-process hash-seed check.

---

## Scenario library

Eight shipped scenarios — **4 rural + 4 city** — plus scenarios generated on
demand (the recommendation path, the builder, and the natural-language builder).
City scenarios declare explicit building geometry, so the occlusions that make them
hard are authored rather than incidental.

| Scenario | Category | Focus |
| --- | --- | --- |
| `scn_solo_fpv_dusk` | rural | Single fast FPV at dusk; patience and identification |
| `scn_civil_overflight` | rural | Civil transit traffic; no-fire discipline |
| `scn_decoy_gauntlet` | rural | Decoys plus a real threat; prioritisation |
| `scn_friendly_lookalike` | rural | Friendly UAV that looks hostile; the trap scenario |
| `scn_urban_slow_quad` | city | Slow quad behind buildings; EO steering |
| `scn_city_canyon_run` | city | Terrain-masked run down a street canyon |
| `scn_city_rooftop_swarm` | city | Swarm over rooftops; workload under saturation |
| `scn_city_civil_corridor` | city | Civil corridor with collateral risk |

Every shipped scenario is validated (structure + oracle sanity) before it is
stored: `backend/scripts/verify_scenarios.py` re-checks all of them and prints the
oracle target table:

<!--ORACLE-TABLE-->

---

## Testing

`make check` runs the fast matrix; `make check-all` adds the slow sweeps, frontend
unit tests and formatting checks.

| Spec 11 suite | Where |
| --- | --- |
| Determinism (same hash, save/load, hash seeds) | `backend/tests/test_p1_core.py`, `test_p4_api.py` |
| Sensors (range / RCS / fog / RF-silent) | `backend/tests/test_p2_sensors_tracking.py` |
| Tracking (continuity, bounded false tracks, stable identity) | `backend/tests/test_p2_sensors_tracking.py` |
| ROE (every verdict, property test) | `backend/tests/test_p3_roe_scoring.py` |
| Scoring (monotonicity, caps, coaching) | `backend/tests/test_p3_roe_scoring.py` |
| Generator (many seeds valid + oracle-solvable + diverse) | `backend/tests/test_generator.py` |
| Skill model (rises/falls, bounded) | `backend/tests/test_p7_skills.py` |
| Red Brain (exploits a fixed blind spot) | `backend/tests/test_p8_redbrain.py` |
| NL builder (offline prompts, mocked LLM) | `backend/tests/test_p9_nl_builder.py` |
| API + WebSocket | `backend/tests/test_p4_api.py` |
| Frontend components | `frontend/src/**/*.test.tsx` (vitest) |
| Performance (40 contacts, 4 sensors, tick budget) | `backend/tests/test_perf.py` |

---

## Performance

Measured on the development laptop (Windows, integrated graphics, no GPU), by
`backend/tests/test_perf.py`:

<!--PERF-TABLE-->

The browser side draws the whole 3D scene in two instanced draw calls (contacts and
buildings) and never re-renders the page per frame.

---

## What is not implemented

Being explicit, as the brief demands:

- **Red Brain Layer B (PPO)** ships as an optional, lazily-imported extra
  (`make train`). The policy file is not trained in this build; the API reports
  `learned_policy.available: false` and the system runs entirely on Layer A (the
  contextual bandit).
- **Team mode** (multi-trainee sessions) is a stretch phase and is not built.
- **The site digital-twin editor** is not built; city scenarios ship with authored
  building geometry instead of a click-to-place editor.
- **12 shipped scenarios became 8** on explicit instruction (4 rural + 4 city). The
  removed four duplicated decision problems.
- **PDF export** is a print stylesheet plus CSV export, not a PDF renderer.
- The **natural-language builder** falls back to a rule-based parser when Ollama is
  unavailable; with a very small local model installed (1B class) the fallback is
  usually the path taken, and the UI reports which one produced the scenario.
