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

**Decision.** The 12 hand-authored scenarios are JSON documents matching `Scenario.schema_version`.
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