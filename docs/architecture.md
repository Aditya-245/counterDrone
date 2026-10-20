# SKYGYM architecture

SKYGYM is a **decision-quality gym**, not a shooting game. A browser-based desktop
simulator puts a trainee in front of abstract sensors, a track table and a rules of
engagement (ROE) tree, and scores the *decisions* they make.

**The browser owns the simulation.** The server owns the library, the records and
the authoritative score. That split is the whole point of the design: the
simulation is a pure, deterministic function of `(scenario seed, trainee action
log)`, so it can run anywhere - and the one place it must *not* run is next to
the trainee, because the score has to be the server's.

## At a glance

```
+------------------------------- browser (React 18 + Vite) -------------------------------+
|  Library   Briefing   Sim   AAR   Dashboard   Instructor                                |
|    zustand: live frame + UI selection         TanStack Query: REST cache                  |
|    @react-three/fiber scene (instanced meshes)   DOM HUD: scopes, tables, timeline        |
|  engine/worker.ts: Pyodide + app.core, the 10 Hz tick loop, the AAR replay and the fork  |
+-----------------^-----------------------------------------------|-------------------------+
                  | REST: library, records, profiles              | action log + hash
+-----------------|-----------------------------------------------v-------------------------+
|                             FastAPI application (offline, localhost)                       |
|  api/sessions.py   api/aar.py   api/scenarios.py   api/users.py                           |
|  api/analytics.py  api/instructor.py  api/health.py                                       |
|------------------------------------------ core/ -------------------------------------------|
|  engine.step()  sensors  tracking  roe  scoring  oracle  generator  skills  redbrain        |
|  worldgen (the landscape the client draws)    view.build_state_frame (trainee-visible)   |
|--------------------------------------------------------------------------------------------|
|  db/ (SQLAlchemy 2 models + Alembic)              data/skygym.db (SQLite, WAL journal)       |
+--------------------------------------------------------------------------------------------+
```

The browser runs the **same** `core/` source the server runs, under Pyodide
(CPython compiled to WebAssembly). `npm run sync:core` copies `backend/app/core`
into `frontend/public/pyapp/manifest.json`; the worker installs it into
Pyodide's virtual filesystem and imports it. One implementation, two runtimes -
not a port.

## The client-side engine

| Piece | Where | What it does |
| --- | --- | --- |
| `frontend/src/engine/worker.ts` | worker | Boots Pyodide, installs the core, drives the 10 Hz tick loop |
| `frontend/src/engine/py/skygym_engine.py` | in Pyodide | `start` / `tick` / `action` / `finish` / `landscape` / `fork` |
| `frontend/src/engine/client.ts` | page | Promise-based API over `postMessage` |
| `frontend/src/engine/history.ts` | page | The session the browser played, in IndexedDB |

The vendored Pyodide runtime (the wasm module, the Python standard library and
the numpy / pydantic / scipy wheels) lives in `frontend/public/pyodide/` and is
served from the app's own origin: **no CDN, no network at runtime**.

### Why the hashes still match

The determinism contract says the same seed plus the same action log always
produces the same `final_state_hash`. Moving the simulation from CPython to
CPython-in-WebAssembly could have broken that - different NumPy, different
`libm`, a different Python version. It does not, and the build proves it:

```
$ make verify-engine
verify-engine: comparing 2 scenario(s) across runtimes
  ok   scn_rural_day_patrol: 21aec1063e19a1e51b4c1bb34688170f
  ok   scn_urban_night_swarm: 51acd230cd888815525535b0fc7f9e43
verify-engine: the browser engine reproduces the server exactly
```

The check is part of `make check-all`: it plays every shipped scenario in the
browser engine and compares the hash with the one the server's own core
produces. If a future NumPy or Pyodide release changes a rounding, the build
fails rather than silently producing a different score.

### What the server still owns

| Concern | Where it lives now |
| --- | --- |
| The scenario library | server (`api/scenarios.py`) - instructors can edit it |
| The authoritative score | server - it re-runs the replay from the action log |
| Skill ratings, habits, Red Brain state | server - so they follow the trainee across devices |
| Session records and analytics | server |
| The simulation, the AAR replay, the fork | browser |
| The generated landscape | browser - from the same seed the simulation used |

The after-action review is captured *while* the session runs, so the browser
never re-simulates to show it. The counterfactual fork costs one simulation,
because the browser already has both the original run and the ghost's
decisions.

### The ground-truth boundary

`core/view.py` is still the single structural boundary between ground truth and
the client. What changed is *where* it runs: it runs in the browser, so the
trainee's browser holds the truth while they decide. That is a deliberate
trade - it is the price of a client-side simulation, and it is why the server
recomputes the score from the action log rather than accepting the browser's
number. The AAR still reveals the truth afterwards, which is the point of an
after-action review.

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
(D027). The browser captures the replay as it plays, so the review is instant;
the server's replay is the authority it is checked against.

**Cross-runtime determinism.** The browser runs this same code under Pyodide,
where the Python version, the NumPy version and the C library all differ from
the server's. The hash still matches, bit for bit - `make verify-engine` proves
it on every full check.

## The tick pipeline

`step()` runs the same phases every tick, in this order. It runs in the
browser's worker, at the canonical 10 Hz:

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
| `core/session.py` | `play()`, `replay()`, `replay_state()`, `fork_session()`, oracle/bad-trainee drivers |
| `db/` | SQLAlchemy 2 models, repositories, Alembic migrations |

There is no server-side live runtime: `live.py`, `clock.py`, `api/realtime.py`
and `api/teams.py` were removed when the simulation moved to the browser. A
session row with no end time is what "live" means now - a view over the
database rather than an in-process registry, which is what made the
serverless deployment fragile in the first place.

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
- **Ground truth has one boundary, and it is `view.build_state_frame()`.** Grading
  data (`true_label`, entity ids) is projected out of the engine state in one
  function, and everything the trainee is shown comes from that projection.
  Since D036 the projection runs in the browser, so the engine's state - truth
  included - is reachable from the page while the session runs. The server
  still recomputes the score from the action log, so a tampered *score* is
  refused; what is lost is the guarantee that a trainee cannot read the answer
  key. That trade is recorded in D036 rather than hidden.
