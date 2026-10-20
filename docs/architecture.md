# SKYGYM architecture

SKYGYM is a **decision-quality gym**, not a shooting game. A browser-based desktop
simulator puts a trainee in front of abstract sensors, a track table and a rules of
engagement (ROE) tree, and scores the *decisions* they make. The server owns the
simulation; the browser owns only presentation and input.

## At a glance

```
+------------------------------- browser (React 18 + Vite) -------------------------------+
|  Library   Briefing   Sim   AAR   Dashboard   Instructor                                |
|    zustand: live frame + UI selection         TanStack Query: REST cache                  |
|    @react-three/fiber scene (instanced meshes)   DOM HUD: scopes, tables, timeline        |
+-----------------^-----------------------------------------------|-------------------------+
                  | WebSocket: state_frame snapshots               | trainee actions
+-----------------|-----------------------------------------------v-------------------------+
|                             FastAPI application (offline, localhost)                       |
|  api/realtime.py (WS)   api/sessions.py   api/aar.py   api/scenarios.py                   |
|  api/users.py  api/analytics.py  api/instructor.py  api/teams.py  api/health.py            |
|  live.py: LiveSession registry        clock.py: one 10 Hz tick task for every live session |
|------------------------------------------ core/ -------------------------------------------|
|  engine.step()  sensors  tracking  roe  scoring  oracle  generator  skills  redbrain        |
|  worldgen (the landscape the client draws)    view.build_state_frame (trainee-visible)   |
|--------------------------------------------------------------------------------------------|
|  db/ (SQLAlchemy 2 models + Alembic)              data/skygym.db (SQLite, WAL journal)       |
+--------------------------------------------------------------------------------------------+
```

## The determinism contract

Specification section 0.4 makes determinism a hard requirement: *the same scenario
seed plus the same trainee action log must always produce the identical
`final_state_hash`*. Four rules keep that true:

1. **One seeded generator, passed explicitly.** `core/rng.py` builds a
   `numpy.random.Generator` from the scenario seed; `step()` receives it as an
   argument. Nothing in `core/` calls `random`, `numpy.random` implicitly, or
   reads the clock.
2. **A pure tick.** `engine.step(state, actions, rng) -> EngineState` is a pure
   function of its inputs. The live session, the headless player, the after-action
   replay and the counterfactual fork all call *the same* function; the only
   difference is who supplies the actions.
3. **A canonical hash.** `EngineState.state_hash()` is BLAKE2b over a
   little-endian float64 canonical encoding of the world, the tracker and the
   annotations (see `DECISIONS.md` D010).
4. **Identity is resolved without set ordering.** The tracker's internal
   `entity_hint()` counts associations and breaks ties on the smallest id, and
   blank entity ids are never recorded. `set` iteration order follows
   `PYTHONHASHSEED`; before this rule the same scenario scored differently from one
   process to the next (D021).

The after-action review replays a stored session at the canonical 10 Hz
(`core.session.RECORD_DT`) and shows the reproduced hash next to the stored one
(D027).

## The tick pipeline

`step()` runs the same phases every tick, in this order:

1. **spawn** – threat waves whose `spawn_t` has arrived are instantiated
   deterministically (`_spawn_due_waves`).
2. **actions** – queued `TraineeAction`s due at this boundary are applied
   (acknowledge, classify, priority, effector, sensor steering, pause request).
3. **behaviour** – each entity moves by its scripted/procedural behaviour
   (waypoint, loiter, terminal dive, orbit-then-strike, decoy lure, swarm,
   transit), with per-entity RNG for jitter.
4. **sensors** – radar, RF, acoustic and EO/IR ignite probabilities from detection
   theory: range, RCS/`visual_size_m`, aspect, altitude, weather visibility, time of
   day, sensor degradation and terrain line-of-sight against the heightfield.
5. **tracking** – a constant-velocity Kalman filter per track, M-of-N confirmation,
   gating and association, track merging, ground-truth id association for `core`
   use only.
6. **fusion** – per-track identity evidence and the *suggested* label, deliberately
   ease-able toward `hostile` by the scenario's ambiguity axis.
7. **ROE** – the decision tree evaluates the situation and records a verdict with
   the path that produced it; effector effects resolve as soft-kill/hard-kill.
8. **scoring annotations** – first-detectable time, hits, neutralisation, breaches.
9. **view** – `view.build_state_frame()` projects the trainee-visible subset into a
   `StateFrame`; ground truth never crosses this boundary.

## Module map (backend)

| Module | Responsibility |
| --- | --- |
| `core/schemas.py` | Every wire/db model (Pydantic v2), `schema_version` gated |
| `core/engine.py` | `step()`, `SimEngine`, actions, spawns, effects, state hash |
| `core/world.py` | World state, entity physics, sensor runtime, terrain sampling |
| `core/terrain.py` | Deterministic heightfield + line-of-sight, `BuildingSpec` boxes |
| `core/worldgen.py` | Procedural landscape: roads, blocks, settlements, fields, water (D033) |
| `core/sensors.py` | Radar / RF / acoustic / EO-IR detection models |
| `core/tracking.py` | Kalman filter, M-of-N tracker, identity hints |
| `core/roe.py` | ROE documents, rule sets (STRICT/STANDARD/PERMISSIVE), evaluator |
| `core/scoring.py` | Five sub-scores, verdict weights, coaching guidance |
| `core/oracle.py` | The counterfactual "good" policy used for targets and ghosts |
| `core/generator.py` | Procedural scenarios from a seed + `DifficultyVector` |
| `core/skills.py` | Five per-trainee skills, logistic difficulty response, habits |
| `core/redbrain.py` | Layer A (contextual bandit) tactic choice |
| `core/session.py` | `play()`, `replay()`, `fork_session()`, oracle/bad-trainee drivers |
| `live.py` + `clock.py` | Server-authoritative live sessions at 10 Hz |
| `db/` | SQLAlchemy 2 models, repositories, Alembic migrations |

## Frontend

- **Pages**: Library (`/`), Briefing (`/briefing/:id`), Sim
  (`/sim/:id`), AAR (`/aar/:id`), Dashboard (`/dashboard`), Instructor
  (`/instructor`).
- **State**: zustand holds the live frame and UI selection (updated at 10 Hz);
  TanStack Query caches REST resources. The 3D scene never re-renders per frame.
- **Sim page**: the r3f canvas draws one `InstancedMesh` for all contacts, and the
  world (terrain, roads, buildings, vegetation, water, sky) comes from
  `scene/world/`, which renders the layout the backend generated for this
  scenario. The HUD draws radar/EO scopes as canvas 2D, a track table, the
  timeline and the effector panel. A reticle overlay provides aim assist and the
  contact class chooser (`hud/AimOverlay.tsx`, `scene/aim.ts`); WASD pans the
  view, `Ground`/`Site`/`Top` move the camera between inspection views, and `F`
  toggles fullscreen (D023, D024, D033).
- **Theme**: dark `#181C14 #3C3D37 #697565 #ECDFCC`, light
  `#FFFAF3 #FFF2DB #FFE5BF #F62440`. The 3D scene keeps the dark instrument
  palette in both themes (D025). No external fonts or CDNs.

## The world

The scene used to be an analytic sine-bump plane with wireframe boxes. It is now
generated, once, by the backend, from the same inputs the simulation uses:

```
scenario (seed, TerrainSpec)
   └─ core/terrain.build_terrain ──► heightfield + authoritative occluders
        └─ core/worldgen.generate_landscape
             ├─ land-cover raster (grass, crop, soil, paved, gravel, water, asphalt)
             ├─ road network (arterials, streets, lanes, tracks) + kerbs + markings
             ├─ city blocks / village clusters / farmsteads, rotated to front a street
             ├─ field patchwork with hedgerow outlines, ponds and streams
             └─ trees, hedges, walls, lamps, vehicles
                  └─ GET /api/scenarios/{id}/environment  (one payload, ~110-200 kB)
                       └─ scene/world/Landscape.tsx  (≈20 draw calls, all instanced)
```

Two rules keep the picture honest:

- **The ground is the simulation's ground.** The payload carries the server's
  heightfield, so what masks a radar ray is what the trainee sees.
- **Only authored footprints occlude.** Buildings carried by the scenario's
  `terrain.buildings` are the ones the line-of-sight test knows about; everything
  the generator invents for context is scenery and is flagged as such.


## Performance approach

- One draw call per class of object in the 3D scene; no React components inside the
  canvas for per-track objects. The world itself is ~20 instanced or merged draw
  calls: terrain, kerbs, roads, markings, water, hedgerows, one per building
  archetype, one per tree species, one per prop kind.
- The HUD updates by direct DOM writes from a `requestAnimationFrame` loop where a
  re-render per frame would be wasteful (reticle, scopes).
- The server tick budget is measured by `backend/tests/test_perf.py`
  (40 contacts, 4 sensors) and recorded in `README.md`.
- Everything runs on one laptop with no GPU requirement, no network, and no API
  keys. The world is generated server-side in ~0.2 s per scenario and cached, so
  the browser only pays for decoding the payload.

## Offline and safety boundaries

- **Offline first.** The application never contacts the internet at runtime and
  needs no local model either: every scenario comes from the in-process
  procedural generator, so the whole product runs with the network switched off.
- **Synthetic parameters only.** Effectors are abstract ("Effector-S soft-kill",
  "Effector-H hard-kill") with generic ranges; sensors are described by abstract
  detection parameters. There are no real weapon, radar, RF or jamming
  specifications anywhere in the code or data (D005).
- **Ground truth stays server-side.** `view.build_state_frame()` is the single
  boundary; grading data (`true_label`, entity ids) never reaches the browser until
  the after-action review of a finished session.
