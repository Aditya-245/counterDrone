# Scoring specification

SKYGYM scores **decision quality**, not kills. A trainee who shoots down every
hostile after letting it inside the protected ring, or who lights up a friendly
first, scores worse than one who identifies contacts early, holds fire when the
rules require it, and warns.

Everything below is implemented in `backend/app/core/scoring.py`. The machine-
readable summary is `SCORING_SPEC`; this document explains it.

## The five axes

| Axis | Weight | What it measures |
| --- | --- | --- |
| Detection | 20 | How fast contacts are acknowledged after they become detectable |
| Classification | 25 | Whether the trainee's identity call matches ground truth (confusion-aware) |
| Prioritisation | 15 | Whether the trainee acts on the most urgent contact first |
| Engagement / ROE | 30 | The verdict at each decision: engage, hold, warn, or nothing |
| Workload | 10 | Whether the trainee keeps up under saturation without spamming |

`total` is the sum of the five axes (0–100), possibly capped.

### Detection

    detection = 20 * mean over entities of exp(-max(0, latency - 1 s) / 20 s)

*latency* is the time from an entity's first **detectable** moment (inside sensor
coverage, not merely spawned) to the trainee's first action on its track. The 1 s
grace makes instant acknowledgement free; 20 s late scores `e^-1 ≈ 0.37`. Entities
that are never acknowledged approach 0 but the mean keeps genuine
no-opportunity cases from dominating.

### Classification (confusion-matrix aware)

    classification = 25 * mean over scored entities of (1 - confusion_penalty(true, chosen))

Not every mistake costs the same. The penalty table is one-sided and ordered so
that false negatives on a threat matter more than false positives on a
non-threat:

| True label | Trainee called | Penalty |
| --- | --- | --- |
| hostile | friendly | **1.00** |
| hostile | civil | 0.85 |
| hostile | bird | 0.80 |
| hostile | unknown (or never called) | 0.45 |
| friendly | hostile | 0.35 |
| civil | hostile | 0.30 |
| bird | hostile | 0.25 |
| friendly/civil/bird | each other | 0.10–0.15 |

A call of **unknown** is a real call: the sim records *when* the trainee decided,
and "I looked and could not tell" occupies the same 0.45 penalty class as never
calling (see `DECISIONS.md` D026). Scoring only counts entities the trainee
classified; unclassified entitles are reported as coaching points.

### Prioritisation

    prioritisation = 15 * Spearman(order the trainee first acted on entities, ground-truth order of time-to-impact)

A trainee who handles the closest-to-perimeter threat first in every wave scores
near full marks. Ties and sparse action logs are handled by the rank correlation
itself.

### Engagement / ROE

Every decision is evaluated by the ROE decision tree, which records a **verdict**
plus the tree path that produced it (`VerdictRecord.path`) so the AAR can show
*why* it was scored the way it was:

| Verdict | Meaning |
| --- | --- |
| `CORRECT_ENGAGE` | Fired under a rule set that permitted it, inside the engagement window |
| `CORRECT_HOLD` | Declined engagement and that was right |
| `LATE_ENGAGE` | Correct target, wrong moment |
| `MISSED_THREAT` | A hostile reached the protected ring untouched |
| `FALSE_ENGAGE_CIVIL` | Engaged a civil contact |
| `FALSE_ENGAGE_FRIENDLY` | Engaged a friendly |
| `COLLATERAL_VIOLATION` | Fired into a declared no-fire / collateral sector |
| `PREMATURE_ENGAGE` | Fired before the ROE window opened |
| `UNNECESSARY_ESCALATION` | Used more force than the rules allow for the situation |

    engagement = 30 * clamp(mean verdict points, 0, 1)

**Caps.** Any `FALSE_ENGAGE_FRIENDLY` or `COLLATERAL_VIOLATION` caps the *total*
score at 60 regardless of the other axes (`capped` / `cap_reason` in the
breakdown). Three rule sets are available — `STRICT`, `STANDARD`, `PERMISSIVE` —
and the same action can be correct under one and premature under another; the
session records which rule set was in force.

### Workload

    workload = 10 * mean(share of confirmed tracks acknowledged,
                         1 - share of confirmed tracks never acted on,
                         reaction speed under saturation)

This is what stops "classify nothing, fire at everything" from scoring. Under a
ten-contact swarm, a trainee who works the list in order beats one who sits on
the first track.

## Coaching output

`ScoreBreakdown.coaching_points` is a list of `CoachingPoint(kind, severity,
message, entity_ids)` generated from the same evidence as the axes — missed
threats, unclassified detections, false engagements, late acknowledgements,
escalation. The AAR and the dashboard render them verbatim, so the score is always
explainable in the trainee's own terms.

## Targets: the oracle and the difficulty model

* **Oracle**: `core/oracle.py` plays a scenario with a scripted "good" policy
  (acknowledge on confirmation, classify from fused evidence, prioritise by
  time-to-impact, engage only when the rule set permits). Its score is the
  scenario's *target* score; it is stored on every shipped scenario and shown in
  the library. The oracle is also the counterfactual ghost in the AAR.
* **Difficulty**: a scenario's `DifficultyVector` is scored through the same
  pipeline; the generator's acceptance gate is an oracle score in the shipped
  band, and `verify_scenarios.py` re-runs it for every file on disk.
* **Skills**: after each session the five skills (`detection`, `classification`,
  `prioritisation`, `engagement`, `roe`) are updated with a logistic
  difficulty-response model: a good score on a hard scenario moves the rating more
  than the same score on an easy one. Skills stay in [0, 100] and drive the next
  scenario recommendation (weakest skill first).
* **Red Brain (Layer A)**: a contextual bandit over abstract tactics (feint,
  swarming, terrain masking, RF silence, decoys) learns which tactic the
  trainee's logged habits are most vulnerable to; the habit panel shows the same
  numbers the bandit used.
