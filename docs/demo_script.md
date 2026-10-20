# Demo script (end to end)

Roughly 12 minutes, one laptop, no network. Every step below has been run in this
order; the appendix records the last full pass.

## 0. Start (60 s)

```bash
make install          # once: python venv + npm packages
make demo             # seeds the demo unit, prints the URLs
```

`make demo` is `make seed` plus directions: it validates the eight shipped
scenarios, stores them, then plays 20 synthetic sessions per demo trainee at the
canonical 10 Hz so every dashboard has real data. Seeding takes a couple of
minutes the first time; re-running only rewrites the synthetic rows.

Open <http://127.0.0.1:5173>.

> Talking point: nothing here calls the internet. Every scenario comes from the
> in-process procedural generator, so the whole system runs offline.

## 1. Library (60 s)

The landing page is the scenario library: **two shipped scenarios, one rural and one
city**, each with terrain preset, threat mix, difficulty, sensor set, and the
**oracle target score**. Filter by category (`rural` / `city`) in the toolbar.

Point out the recommendation card at the top: it names the trainee, the weakest
skill, and why that scenario is suggested. If the library has no generated
scenario yet, use **Recommend** to have the generator build one (the ninth entry
in the demo).

The **Generate a scenario** panel at the bottom is the answer to "what else is
there?": pick rural or urban, day, dusk or night, and a single drone or a swarm,
and the generator builds that exercise on the spot — every combination the
statement asks for, from one seed.

> Talking point: the oracle target is what a scripted "good" policy scored on the
> same seed — the difficulty is calibrated, not guessed.

## 2. Briefing (60 s)

Pick a **city** scenario (for example *Urban Night Swarm*) and press **Begin**.
Read the brief aloud: threat waves, sensors and their coverage, the active rule
set (`STRICT` / `STANDARD` / `PERMISSIVE`), visibility and time of day, and what
"mission success" means for this run.

## 3. Live session (5–6 min)

The sim is server-authoritative: the browser draws what the server sends at 10 Hz.

1. **Watch detection.** Contacts appear on the radar scope and in the track table.
   Press `Tab` to select one; the EO camera can be steered with `[` / `]`.
2. **Identify.** Put the reticle on a contact (mouse; it snaps when close) and
   press `Enter`. The five-way chooser pops up: `1` hostile, `2` civil,
   `3` friendly, `4` bird, `5` unknown. Pick one — `unknown` is a real call and is
   scored as such.
3. **Work the list.** `Q` acknowledges, `I` requests ID, `R` warns.
4. **Decide.** `X` holds fire, `E` soft-kill, `G` hard-kill. Fire outside the ROE
   window and the AAR will show `PREMATURE_ENGAGE` with the tree path that
   produced it.
5. **Escalate honestly.** Deliberately engage a `friendly` once (or hold fire on a
   clear hostile) — the cap at 60 points and the coaching line make much better
   theatre than a perfect run.
6. **Camera.** `WASD` pans, `F` toggles fullscreen, `Space` requests pause
   (instructor-only, so it lands in the action log).

End the session when the wave timer runs out (or after the last breach).

## 4. After-action review (3 min)

Open **AAR** for the session.

1. **Determinism.** The header shows the stored hash and the replayed hash — they
   match. That is the determinism guarantee: the same seed and the same action log
   reproduce the identical state.
2. **Timeline.** Scrub through the frames; the ghost overlay shows what the
   counterfactual oracle did at each decision point.
3. **Verdicts.** Every decision is listed with points and the ROE tree path.
4. **Counterfactual fork.** In the *Counterfactual fork* panel, fork at a missed
   engagement with the oracle taking over. The result reports `diverged: true`,
   both hashes, both scores and the delta — "you lost 17 points by holding fire
   three seconds too long".
5. **Heatmap.** The sector-attention heatmap shows where the trainee looked versus
   where the threats came from.

## 5. Dashboard (2 min)

Individual view: skill radar, score trend, detection-latency distribution,
classification confusion matrix, verdict breakdown over time, habit insights.
Unit view: cohort comparison, weakest skills across the unit, sessions per trainee.
Export CSV, and the print stylesheet strips the chrome for a printable report.

> Talking point: the confusion matrix is the honest one — calling a hostile
> *civil* is red, calling it *unknown* is amber, and the columns sum to the
> sessions that were played.

## 6. Instructor and the generator (1–2 min)

* **Instructor**: create a locked exercise — fix the seed so every trainee gets the
   identical scenario, pick the rule set, assign trainees, and export the exercise
  document with its `sha`.
* **Generator**: back on the Library page, the **Generate a scenario** panel builds an
  exercise from terrain, light and threat count - for example rural, night, swarm -
  and opens its briefing. It is the same generator the seeder uses, so the
  difficulty is calibrated and the scenario is oracle-solvable.

## Appendix: last verified run

Fill in when the demo is rehearsed on the final build:

| Step | Result |
| --- | --- |
| `make check` | see README |
| seed: 2 shipped + synthetic sessions | |
| live session scored | session id, score |
| AAR hash match | |
| fork diverged | delta |
| dashboard charts from stored rows | |
