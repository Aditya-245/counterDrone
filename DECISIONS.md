# DECISIONS

Format for every entry: **Decision** / **Alternatives** / **Reason**.

---

## D001 — Windows-first tooling, GNU Make 4.4 from WinGet, venv at repo root

**Decision.** The development machine is Windows 11 with PowerShell and no pre-existing Python.
Python 3.11.9 was installed via WinGet, a virtual environment lives at `.venv/`, and GNU Make 4.4.1
(`ezwinports.make`) provides the `make` targets the specification requires. A `make.cmd` shim is
also committed so `make` can be invoked as `.\make.cmd check` on machines without GNU Make.

**Alternatives.**
- PowerShell scripts instead of a Makefile — rejected, the spec explicitly requires `make dev`,
  `make check`, `make demo`.
- `pipx`/`uv` for environment management — rejected, adds a second tool to document for no gain.

**Reason.** The deliverable must satisfy "one command to run everything" literally. A committed
`make.cmd` shim keeps that promise on stock Windows, while the `Makefile` keeps it on Linux/macOS.

---

## D002 — `schema_version` on every payload, rejected at the boundary

**Decision.** All API request/response models inherit from `VersionedModel`, which carries
`schema_version: Literal[SCHEMA_VERSION]`. The FastAPI dependency `enforce_schema_version()` rejects
any body whose `schema_version` is missing or unknown with HTTP 422 and a machine-readable error.

**Alternatives.**
- Accept any version and coerce — rejected, silent schema drift is exactly the failure mode the
  spec asks us to prevent.
- Version only the session endpoints — rejected, scenario JSON files are also persisted and loaded
  from disk and must fail loudly.

**Reason.** "Add a schema-version field and reject unknown versions" is an explicit requirement, and
round-tripping scenario files through disk is the path most likely to meet an older schema.

---

## D003 — Ground truth is a separate, explicit view of the world

**Decision.** `WorldState` holds full entity truth internally, but the only thing that ever reaches
the WebSocket `state_frame` is a `TrackView` produced by the tracker. `core/oracle.py`,
`core/scoring.py` and `core/roe.py` take ground truth as an *argument*, never read it out of a
global. The API layer has no import path from a `WorldState` to a client payload except the tracker.

**Alternatives.**
- Keep ground truth on the entity and filter per-field at serialisation — rejected, it invites a
  single leaked field being a spoiler, and it is not testable.
- Two parallel state trees (truth + perceived) — rejected, it doubles the state-hash surface and makes
  determinism harder to argue.

**Reason.** "The trainee sees tracks only, never ground truth" is a core requirement of the product.
Making it structural rather than a filtering convention means a leak shows up as a test failure.

---

## D004 — Custom NumPy Kalman filter and M-of-N tracker instead of a library

**Decision.** `core/tracking.py` implements a constant-velocity Kalman filter in NumPy with
exposed state covariance (needed for the covariance ellipse in the UI), a Mahalanobis-gated
nearest-neighbour assignment solved by greedy global minimum (Hungarian-style on a 3-D cost matrix),
and M-of-N track initiation.

**Alternatives.**
- `filterpy` / `pykalman` — rejected, both are unmaintained or thin; the spec asks for "a small own
  Kalman filter implementation in NumPy" explicitly.
- SciPy `linear_sum_assignment` for the gate — actually adopted, as the assignment solver. Only the
  filter itself is ours.

**Reason.** The specification says so, and the tracker's covariance output is needed for the UI's
uncertainty ellipse and for the effectors' track-quality term.

---

## D005 — Effectors and sensors use abstract, non-attributable parameters

**Decision.** All sensor and effector parameters live in named tables in `core/sensors.py` /
`core/world.py` with generic magnitudes, and the class names are `Effector-S` and `Effector-H`.
No real RCS values, no real radar bands, no real jamming frequencies, no engagement ranges copied from
any published system. The parameter table header carries an explicit "synthetic / abstract" note and
is echoed into the README safety section.

**Alternatives.**
- Realistic-but-fictional numbers in "plausible" ranges — rejected, this is indistinguishable from
  real performance data and serves no simulation purpose.
- A single generic "effector" — rejected, soft-kill vs hard-kill differ enough in cooldown,
  ammunition and collateral handling to be worth modelling separately.

**Reason.** Working rule 8 and the safety boundary in section 1 of the problem statement.

---

## D006 — Red Brain Layer B (PPO) is an optional extra, imported lazily

**Decision.** Gymnasium and Stable-Baselines3 are declared in `backend/requirements-ml.txt`, not in
the base requirements. `core/redbrain.py` imports them inside `_load_learned_policy()` behind a
`try/except ImportError` and registers the `learned-swarm` tactic only if loading succeeds. Training
lives in `backend/training/` and is never imported at runtime.

**Alternatives.**
- Hard dependency on SB3 — rejected, it drags in a ~250 MB PyTorch wheel for an explicitly optional
  feature and would make `make dev` heavy and network-dependent.
- Ship the trained weights in the repo — rejected, binary artefacts in the repo are heavy and the
  spec explicitly requires the app to run without them.

**Reason.** "The app must run correctly without the policy file."

---

## D007 — Ollama accessed over `urllib`, never `eval`, JSON parsed defensively

**Decision.** `core/nl_builder.py` talks to `http://localhost:11434/api/generate` with
`urllib.request`, times out at 30 s, and treats any failure (no server, wrong model, timeout,
malformed JSON) as "LLM unavailable". Model output is parsed with `json.loads` after stripping a
fenced-code block; there is no `eval`, `exec`, `pickle` or `yaml.load` anywhere on model output. The
JSON Schema of `Scenario` is embedded in the system prompt so the model is constrained by structure.

**Alternatives.**
- The `ollama` Python client package — rejected, an extra dependency for one HTTP call.
- Streaming responses — rejected, not needed for a single scenario JSON and it complicates timeout
  handling.

**Reason.** Offline-first requirement, plus the explicit "never execute or eval model output" rule.

---

## D008 — Frontend uses @react-three/fiber v8 with React 18

**Decision.** React 18.3 + `@react-three/fiber` ^8.17 + `@react-three/drei` ^9.114 + three ^0.169.
Vite 5, TypeScript 5.6 with `strict: true`, Vitest 2 + jsdom + React Testing Library.

**Alternatives.**
- React 19 + fiber v9 — rejected at planning time because fiber v9 forces the React 19 line and the
  drei version matrix with it; the spec pins React 18.
- Plain three.js without r3f — rejected, the spec names r3f and drei, and the declarative scene graph
  is what makes the LOD/instancing story tractable.

**Reason.** Tech stack is fixed by the spec. Versions chosen as a mutually compatible set.

---

## D009 — Server-authoritative 10 Hz WebSocket with client interpolation

**Decision.** The server steps the engine at a fixed 10 Hz and broadcasts a `state_frame` after every
step. The client holds the last two frames and interpolates entity positions to render rate, so a
40-dolly 3D scene stays smooth without the server running at 60 Hz. The server is authoritative:
client actions are queued and applied at the next tick boundary, never out of band.

**Alternatives.**
- Server at 30/60 Hz — rejected, it triples CPU for state the client can interpolate, and the spec
  says the client interpolates the 10 Hz stream.
- Client-side prediction — rejected, it would fork the deterministic state, which the determinism
  requirement forbids.

**Reason.** Spec section 3.1 and 8, plus the determinism hard requirement.

---

## D010 — State hash is BLAKE2b over a canonical little-endian float64 encoding

**Decision.** `WorldState.state_hash()` walks a fixed, declared field order, packs every float as
`<f8` little-endian and every integer as `<i8`, feeds the bytes to `hashlib.blake2b(digest_size=16)`,
and returns hex. `-0.0` is normalised to `0.0` so that arithmetically equal states hash equally.

**Alternatives.**
- `repr()` of the dict — rejected, float repr is stable in CPython but the dict ordering and the
  formatting are not a contract, and it is slow.
- `json.dumps` with `sort_keys` — rejected, JSON float formatting is a re-serialisation risk; packing
  the actual IEEE-754 bytes is exact and fast.

**Reason.** Determinism has to be checkable byte-for-byte across a save/load round trip.

---

## D011 — Scenarios ship as JSON files in `scenarios/`, loaded and validated at import

**Decision.** The hand-authored scenarios are JSON documents matching `Scenario.schema_version`; the
library currently ships 8 of them (see D022, which reduced the original 12).
`app.db.seed` loads them with Pydantic validation, runs `validate_scenario()` (structural) and
`oracle_sanity()` (headless, ≤ 20 % of nominal duration, speed-limited) before inserting. A scenario
that fails either check aborts the seed with a clear message rather than being inserted anyway.

**Alternatives.**
- Python source files constructing the models — rejected, JSON is the interchange format the spec asks
  for and is what the NL builder produces.
- Insert without validation for speed — rejected, it would let an invalid shipped scenario reach the
  UI.

**Reason.** Spec section 10 plus "never an invalid one".

---

## D012 — Difficulty → success-rate inversion is a monotone logistic calibrated by the skill model

**Decision.** Each of the five skills has a logistic difficulty-response curve
`p = sigmoid(k * (x - theta))` fitted with fixed coefficients in `core/skills.py`. The generator
inverts the weakest skill's curve numerically (bisection, 60 iterations) to find the difficulty value
`x*` at which predicted success is ≈ 0.70, then clamps `x*` into the scenario's feasible range.

**Alternatives.**
- Linear map from skill rating to difficulty — rejected, it produces impossible scenarios near the
  ends and does not respect that some difficulty axes saturate.
- Purely random difficulty around the skill — rejected, it does not actually target a skill.

**Reason.** "Set the DifficultyVector so the predicted success rate on that skill is about 70 %".

---

## D013 — Habit features are computed from logged actions, never declared by the UI

**Decision.** `core/skills.py` derives habit features by replaying the stored action log:
`sector_scan_share[8]`, `mean_detection_latency_by_sector`, `engage_rate_by_class_guess`,
`saturation_reaction_ms`, `mean_actions_per_10s`. The Red Brain's context vector is built from
exactly these numbers, and the "Adversary adapted to you" panel renders the *stored* statistics
next to each claim so the user can verify it.

**Alternatives.**
- Let the UI assert habits — rejected, the spec explicitly says "Tell the truth: this must be driven
  by actual logged data".
- Client-side telemetry — rejected, it would not survive replay and would make the bandit
  non-reproducible.

**Reason.** Spec 7.5.

---

## D014 — Ambiguity in sensor class hints is injected at generation time as a per-entity bias

**Decision.** `DifficultyVector.ambiguity` (0..1) becomes a per-entity `hint_bias` sampled from the
scenario RNG at generation time and stored on the entity. The tracker's suggested class is then
computed from evidence but deliberately skewed toward `hostile` by `hint_bias` when the evidence is
weak. The skew is a property of the world, not of the client, so it replays identically.

**Alternatives.**
- Corrupting the hint randomly at render time — rejected, it would not replay.
- Removing class hints entirely when ambiguity is high — rejected, the interesting decision problem is
  exactly "do I trust this hint or not".

**Reason.** Spec 6.1 `ambiguity` and the requirement that "wrong or low-confidence suggestions must
be possible".

---

## D015 — WebSocket reconnect resumes from the server's authoritative step count

**Decision.** Each live session holds a server-side `SessionRuntime` with its step index and full
state. The client sends `resume {last_step}` on reconnect; the server replies with the current
`state_frame` plus the full action replay list, and the client rebuilds its local view. No client
state is ever authoritative.

**Alternatives.**
- Let the client re-send its action log — rejected, the log lives server-side in SQLite already and
  re-sending risks duplicates.
- Drop the session on disconnect — rejected, spec requires reconnect with resume.

**Reason.** Spec section 8, "Server is authoritative. Include reconnect with resume."

---

## D016 — Ambiguous/no-degenerate policy: every phase ships even when a check is partially unmeetable

**Decision.** Where a requirement could not be met exactly on this hardware (notably measured 3D fps on
integrated graphics, and PyTorch install feasibility offline), the phase still ships with the feature
implemented, the measurement recorded honestly, and the shortfall listed in `README.md` § Known gaps
and in the final report.

**Reason.** Working rules 2 and 4 plus the explicit instruction in section 13 to "be honest; do not
hide gaps".

---

## D017 — Single SQLite file, WAL, `check_same_thread=False` with a lock

**Decision.** One `skygym.db` under `backend/data/`, created by Alembic. The engine uses
`create_engine(..., connect_args={"check_same_thread": False})` and all writes go through a single
session guarded by a `threading.Lock`, because the WebSocket handler and the request handlers share
one event loop thread and a worker pool.

**Alternatives.**
- Postgres — rejected, the spec says SQLite and "runs on an ordinary laptop" is a product requirement.
- Async SQLAlchemy (`aiosqlite`) — rejected, it adds an async driver for a workload that is dominated
  by in-memory NumPy stepping; the DB is never the bottleneck.

**Reason.** Simplicity and the offline requirement.

---

## D018 — Frontend dev proxy rather than CORS-heavy production hosting

**Decision.** In dev, Vite proxies `/api` and `/ws` to `127.0.0.1:8000`. In production, FastAPI mounts
the built `frontend/dist` and serves it as static files. CORS middleware is still enabled but scoped to
the dev origin only.

**Alternatives.**
- Two-process production hosting — rejected, it breaks the single-command promise.
- Full permissive CORS — rejected, unnecessary once the proxy is in place.

**Reason.** `make dev` must start both and the app must still be one command to run.

---

## D019 — Tests are split into fast unit tests and a marked `@pytest.mark.slow` integration set

**Decision.** `make test` runs the fast suite (pure `core/` unit tests + schema validation + tracked
generator seeds). `make check` runs the fast suite plus the slow marked tests with a bounded seed
count, so a full run stays under a few minutes. The 200-seed generator sweep lives behind
`@pytest.mark.slow` and is also runnable via `make test-full`.

**Alternatives.**
- One undifferentiated suite — rejected, the 200-seed sweep plus the 20-prompt NL suite would dominate
  the feedback loop.
- Shipping the sweep only in CI — rejected, there is no CI here; the acceptance checks must run locally.

**Reason.** Working rules 1, 2 and 6.

---

## D020 — `init_db()` stamps the Alembic head instead of running migrations

**Decision.** `app.db.session.init_db()` calls `create_all()` and then Alembic's own `command.stamp`
on the created database. A failure to stamp is swallowed: the database is fully usable without a
revision marker, and a broken `alembic.ini` must not stop the app from starting.

**Alternatives.**
- Run `command.upgrade(cfg, "head")` on every start — rejected, it makes application startup depend on
  the migration scripts staying importable, and a half-applied migration would be worse than a stamp.
- No Alembic at all — rejected, the spec asks for migrations and `make migrate` has to be meaningful.

**Reason.** Tests and `make seed` need a usable schema in milliseconds, while `make migrate` remains the
authoritative path. `backend/scripts/verify_migrations.py` proves the migration and `create_all()`
produce the same 10 tables.

---

## D021 — Tracking resolves identities deterministically: smallest-id tie-break, no blank ids

**Decision.** `Track.entity_hint()` counts the ground-truth ids a track has been associated with and
breaks ties on the **smallest id**. Blank ids are never recorded. Both rules are load-bearing for the
determinism contract.

**Alternatives.**
- Leave the tie to `max(set(ids), key=count)` — rejected: `set` iteration order follows
  `PYTHONHASHSEED`, so the same scenario and the same action log scored 68.1 / 98.1 / 68.1 across
  hash seeds. This was found by running one shipped scenario under five seeds.
- Drop the hint entirely — rejected: the oracle's notion of "which entity is this track" is the point
  of the hint, and it is `core`-internal (never shown to the trainee).

**Reason.** Spec 0.6: the same seed and the same action log must produce the same `final_state_hash`,
in-process and across a save/load round trip. A clutter return that reached `entity_ids` as `''` also
resolved engagements to a phantom entity, burning `MAX_SHOTS_PER_TARGET` and turning a winnable
scenario into `MISSED_THREAT`.

---

## D022 — The shipped library is 8 scenarios: 4 rural + 4 city, with explicit city buildings

**Decision.** `scenarios/` holds 8 documents: `scn_solo_fpv_dusk`, `scn_civil_overflight`,
`scn_decoy_gauntlet`, `scn_friendly_lookalike`, `scn_urban_slow_quad`, `scn_city_canyon_run`,
`scn_city_rooftop_swarm`, `scn_city_civil_corridor` — four rural and four city. `rural` / `city` is the
single reliable category facet in `tags`, enforced by `backend/scripts/verify_scenarios.py`. City
scenarios declare an explicit `BuildingSpec` list instead of relying on the urban preset's random
scatter, so the occlusion that makes them hard is authored rather than incidental. The ninth entry the
demo shows is generated on demand by the recommendation flow, not shipped.

**Alternatives.**
- Keep the original 12 and add a category filter later — rejected: three of them duplicated each
  other's decision problem and the library page could not be honest about categories.
- Let city scenarios inherit the preset's buildings — rejected: the terrain preset's scatter is
  seeded but arbitrary, so a scenario's difficulty would drift when unrelated terrain code changed.

**Reason.** User request: cut the library to 9 (8 shipped + 1 recommended) and separate rural from city
with real buildings. D011 is amended accordingly.

---

## D023 — Game-style keymap replaces the spec's letter shortcuts

**Decision.** The sim is driven like a laptop game: **WASD** pans the view (the scene owns those keys),
**Enter** opens the class chooser on the contact under the reticle, **1–5** pick
hostile/civil/friendly/bird/unknown, **Tab** cycles contacts, **E/G** soft-kill/hard-kill, **R** warn,
**X** hold fire, **I** request ID, **Q** acknowledge, **[**/**]** steer the EO sensor, **Space**
requests pause, **F** toggles fullscreen, **Esc** closes the chooser. The old letter aliases (H/C/B/F/U
for classes, W hold fire, M warn, A acknowledge) are retired; the chooser is modal and owns the keyboard
while it is open.

**Alternatives.**
- Keep the spec's H/C/B/F/U — rejected on the user's explicit instruction, and 1–5 is what the chooser
  popup can label consistently. The spec's letters survive as the *displayed* labels in the guide.
- Let the trainee pause directly — rejected: spec 7.4 makes pause instructor-only, so `Space` sends a
  `PAUSE_REQUEST` through the action log and the server decides.

**Reason.** User request ("laptop-game-style controls"), and the coaching text in `core/scoring.py`
still teaches the letter taxonomy, so both vocabularies are explained in the UI.

---

## D024 — Aim assist: the canvas owns the pointer, the reticle overlay is passive

**Decision.** `scene/aim.ts` holds a mutable aim singleton (`pointer`, `drawn`, `projected`,
`lockedId`, `chooserOpen`). The scene projects every contact to NDC once per rendered frame and snaps
the reticle to the nearest contact within `AIM_SNAP_RADIUS = 0.09` NDC. Pointer events stay on the
canvas (orbit, zoom, picking all keep working); the DOM overlay has `pointer-events: none` and only the
chooser popup takes clicks back. A double click anywhere in the scene posts a `chooserRequest` counter
to the overlay, which is how the popup opens without the overlay owning the mouse.

**Alternatives.**
- A full-size interactive overlay — rejected: it swallows orbit/zoom/click on the 3D scene.
- Track hover with React state per frame — rejected: that re-renders the page at 60 fps.

**Reason.** User request for a crosshair that pops the class chooser, without losing the existing
camera controls.

---

## D025 — The 3D scene keeps its own dark palette in both UI themes

**Decision.** `TacticalScene` uses fixed instrument colours (sage rings, cream radar markers, signal-red
protected ring, near-black ground) regardless of the UI theme. Only the surrounding DOM panels follow
the light/dark palette.

**Alternatives.**
- Theme the scene too — rejected: a lit-room light theme over a dark ground reads as a rendering bug,
  and a radar console in a lit room stays dark.

**Reason.** Visual honesty plus legibility of the overlay; the palette is still the project's four
colours.

---

## D026 — `unknown` is a trainee call, not a ground-truth label

**Decision.** The chooser offers five calls: the four identities plus `unknown`. The engine accepts
`CLASSIFY` with `params.class == "unknown"` as a deliberate "cannot identify this contact" call: it
records the call time and clears any earlier call, while leaving `TrackAnnotation.trainee_class` empty.
There is no `unknown` member of `LabelType`, and scoring already buckets an unlabelled contact into the
confusion matrix's `unknown` column. Any other unrecognised label still logs a `SCENARIO_EVENT`.

**Alternatives.**
- Add `UNKNOWN` to `LabelType` — rejected: `LabelType` is ground truth, and a spawned entity always has
  a real identity; only the trainee's report can be uncertain.
- Reject the call and let the trainee simply not classify — rejected: "I looked and could not tell" is a
  different decision from "I never looked", and spec 7.2 scores `hostile -> unknown` as its own outcome.

**Reason.** The chooser must not silently drop a key press, and the trainee's uncertainty has to be
recorded rather than inferred from silence.

---

## D027 — Sessions are recorded at the canonical 10 Hz; the AAR replays at that rate

**Decision.** `core/session.py` exports `RECORD_DT = 0.1`, the tick rate every recorded session runs at.
The result check (`POST /api/sessions/{id}/result`) and the after-action review replay at `RECORD_DT`,
which is what makes a stored `final_state_hash` reproducible. A session driven at another `dt` cannot be
reproduced by the server, so the API never creates one.

**Alternatives.**
- Persist a per-session `dt` — rejected: the spec fixes the simulation at 10 Hz, so a per-session tick
  rate would be a second degree of freedom nothing in the product can exercise.
- Replay at whatever rate is convenient (the P4 tests used 0.3 for speed) — rejected: it produced a
  session whose hash the server could never match, which is exactly the determinism guarantee the AAR
  exists to demonstrate.

**Reason.** Spec 0.6. The P4 AAR test now builds its session at `RECORD_DT` and asserts the replay
reproduces the hash.

---

## D028 — A fork's counterfactual ghost is requested, not implicit

**Decision.** `ForkRequest.follow_with_ghost` (default true) controls whether the counterfactual oracle
takes over at `fork_t` and appends its own actions to the fork log. With it off, the stored log is
re-simulated unchanged and must reproduce the original hash exactly. The AAR page sends `true`, which is
the counterfactual it displays.

**Alternatives.**
- Keep the ghost hardcoded on in `api/aar.py` — rejected: a fork could then never be a no-op, the
  "diverged" flag carried no information for an unchanged log, and the frontend already sent a
  `follow_with_ghost` field that the API silently ignored.

**Reason.** The API should be explicit about the only behaviour that makes `diverged: false` reachable,
and a silently ignored request field is a contract bug.

---

## D029 — `make seed` prunes retired shipped scenarios, cascading to their sessions

**Decision.** After upserting, `backend/scripts/seed.py` deletes shipped scenario rows whose ids are no
longer present in `scenarios/`, along with the sessions, actions, events, scores and skill history that
reference them, and prints every id it retired with its session count.

**Alternatives.**
- Upsert only (the previous behaviour) — rejected: the 7 retired scenario files stayed in the database
  and the library page kept offering scenarios that no longer exist on disk.
- Mark the rows `retired` and hide them in the UI — rejected: it leaves dead scenario documents in the
  library table and needs a filter in three places to stay honest.
- Refuse to prune when sessions reference the scenario — rejected for this database: the demo database is
  disposable and fully reproducible with `make seed`, and refusing would leave the library permanently
  wrong on any developer machine.

**Reason.** `make seed` must be able to shrink the library, and the cascade is reported rather than
silent.

---

## D030 — Six trainees with a full 20-session synthetic history

**Decision.** `make seed` gives each of the six demo trainees 20 synthetic sessions, matching the spec's
"demo unit with 6 trainees and 20 historical sessions of plausible synthetic data". The count can be
lowered with `SKYGYM_DEMO_SESSIONS` for a quick smoke database. Every seeded session is flagged
`synthetic=True` and the UI marks it as such.

**Alternatives.**
- 3 sessions per trainee (the previous default) — rejected: the per-trainee trend charts and the unit
  comparison both looked empty on the demo.
- 20 sessions total across the unit — rejected: that is 3 per trainee again, and the spec's sentence is
  about populating the dashboard, which is per-trainee.

**Reason.** Spec 9 (seed data) and the demo requirement that the dashboard be populated. Seeding cost is
paid once and reported by the script.

---

## D031 — Repeat `make seed` runs skip synthetic sessions that already exist

**Decision.** `seed_demo_unit` checks for each `seed_<trainee>_<n>` id before simulating it and only calls
`oracle_result` for the sessions that are actually missing. When a trainee has nothing missing, the script
also leaves that trainee's stored skills and bandit alone, and `main` prints how many sessions were
simulated versus kept.

**Alternatives.**
- Always re-simulate all 120 sessions — rejected: `make dev` calls `make seed`, so every developer
  start-up paid a measured ~8 minutes of CPU to rewrite data that never changes.
- Cut the history to a few sessions per trainee — rejected: the dashboard loses its trends and D030's
  20-session requirement is deliberate.
- Cache oracle results in a file keyed by scenario and seed — rejected: a second store to invalidate for a
  cost that only exists on the first run.

**Reason.** The first seed on an empty database still simulates everything, but every later run is a no-op
instead of a rebuild. Filling a gap left by an interrupted run can give that filled session a different
difficulty, because the skill model is rebuilt from scratch; the docstring says so.

---

## D032 — `make live-demo` plays a session through the public API

**Decision.** `backend/scripts/live_demo.py` creates a session over HTTP, reacts to authoritative
WebSocket frames with a small scripted trainee (acknowledge, classify from the sensor hint, engage
hostile-suggested tracks inside the perimeter guard band), asks the server to finish, and prints the
replayed score, verdict counts and final state hash. It never imports `app.core`, so a green run proves
the API, the runtime, the stored action log and the replay agree.

**Alternatives.**
- Drive the run with `oracle_result` in-process — rejected: it would bypass the API the demo is meant to
  exercise, and the oracle is already the seed's driver.
- Keep the driver out of the repository as a scratch file — rejected: the checklist asks for a tested
  end-to-end demo, and a reviewer needs one command to reproduce the run.

**Reason.** One command exercises every layer a demo audience cares about, with the engine's
determinism as the last visible output.

