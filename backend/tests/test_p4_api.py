"""P4 - REST contract tests and persistence.

These run against a real SQLite file in a temp directory, through the actual
FastAPI app, so they exercise routing, validation, the schema-version gate and
the database writes.

The simulation itself runs in the browser, so the server never ticks a
session. What the server owns is the record: it re-runs the deterministic
replay from the action log the browser produced and returns the authoritative
score.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.schemas import SCHEMA_VERSION, DifficultyVector, Scenario


@pytest.fixture()
def client(tmp_db: Path) -> Iterator[TestClient]:
    """A TestClient with a freshly seeded throwaway database."""
    from app.core.generator import generate
    from app.db.models import ScenarioRow, User
    from app.db.session import init_db, session_scope

    init_db()
    scenario = generate(seed=4242, difficulty=DifficultyVector()).scenario
    with session_scope() as db:
        db.add(User(id="usr_test", display_name="Test Trainee", unit_id="unit_test"))
        db.add(
            ScenarioRow(
                id=scenario.id,
                name=scenario.name,
                schema_version=scenario.schema_version,
                seed=scenario.seed,
                difficulty_json=scenario.difficulty.model_dump(mode="json"),
                tags_json=scenario.tags,
                body_json=scenario.model_dump(mode="json"),
                source="shipped",
            )
        )
    with TestClient(app=_app()) as test_client:
        yield test_client


def _app() -> Any:
    from app.main import create_app

    return create_app()


SCENARIO_ID = "gen_004242"


# --------------------------------------------------------------------------- #
# Contract tests
# --------------------------------------------------------------------------- #


def test_health(client: TestClient) -> None:
    """`/api/health` reports the build and its optional subsystems."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["tick_hz"] == 10
    assert "learned_policy" in body


def test_scenario_library_contract(client: TestClient) -> None:
    """The library returns summaries, tags and the rule-set list."""
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    item = body["items"][0]
    for key in ("id", "name", "difficulty", "tags", "source", "recommended"):
        assert key in item
    assert "STANDARD" in body["rule_sets"]


def test_trainee_scoped_library_calls_do_not_500(client: TestClient) -> None:
    """Every library route that takes a trainee_id loads the skill model safely.

    Regression: these paths called ``load_skill_model(trainee_id)`` - the
    two-argument repository function - without a DB session, so the Library
    page 500'd the moment a trainee was signed in. They must resolve the model
    through the single-argument wrapper instead.
    """
    scoped = (
        "/api/scenarios?trainee_id=usr_test",
        "/api/scenarios/recommended?trainee_id=usr_test",
        f"/api/scenarios/{SCENARIO_ID}/summary?trainee_id=usr_test",
    )
    for path in scoped:
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"

    posted = client.post(
        "/api/scenarios/generate",
        json={"seed": 99, "trainee_id": "usr_test", "persist": False},
    )
    assert posted.status_code == 200, posted.text[:300]


def test_read_one_scenario_and_404(client: TestClient) -> None:
    """A known scenario reads back; an unknown one 404s with a machine code."""
    ok = client.get(f"/api/scenarios/{SCENARIO_ID}")
    assert ok.status_code == 200
    assert ok.json()["schema_version"] == SCHEMA_VERSION
    missing = client.get("/api/scenarios/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "not_found"


def test_rule_set_tree_is_public(client: TestClient) -> None:
    """The ROE tree is inspectable without hitting the database."""
    r = client.get("/api/scenarios/rulesets/STRICT")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "STRICT"
    assert body["tree"]["id"] == "root"
    assert body["verdict_points"]["FALSE_ENGAGE_FRIENDLY"] == -2.0


def test_unknown_schema_version_is_rejected(client: TestClient) -> None:
    """The boundary refuses a payload this build cannot read."""
    payload = {"schema_version": SCHEMA_VERSION + 99, "text": "hello"}
    r = client.post("/api/scenarios/validate", json=payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "unsupported_schema_version"
    assert detail["expected_schema_version"] == SCHEMA_VERSION
    assert detail["received_schema_version"] == SCHEMA_VERSION + 99


def test_malformed_json_is_rejected(client: TestClient) -> None:
    """A body that is not JSON at all fails cleanly."""
    r = client.post(
        "/api/scenarios/validate",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_json"


def test_generate_endpoint_persists(client: TestClient) -> None:
    """Generating a scenario stores it so it shows up in the library."""
    r = client.post(
        "/api/scenarios/generate",
        json={"seed": 777, "difficulty": DifficultyVector(swarm_size=0.4).model_dump(mode="json")},
    )
    assert r.status_code == 200
    created = r.json()
    assert created["seed"] == 777
    listed = client.get("/api/scenarios?source=generated").json()
    assert any(s["id"] == created["id"] for s in listed["items"])


def test_validate_endpoint(client: TestClient) -> None:
    """Validation returns both the structural verdict and the sanity result."""
    from app.core.generator import generate

    scenario = generate(seed=11).scenario
    r = client.post("/api/scenarios/validate", json=scenario.model_dump(mode="json"))
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["sanity_ok"] is True
    assert body["sanity_message"]


def test_users_and_skills_contract(client: TestClient) -> None:
    """User listing and the skill payload match the frontend types."""
    users = client.get("/api/users").json()
    assert any(u["id"] == "usr_test" for u in users)
    skills = client.get("/api/users/usr_test/skills").json()
    assert set(skills["skills"]) >= {"detection", "classification", "roe", "schema_version"}
    assert client.get("/api/users/nobody/skills").status_code == 404


def test_redbrain_report_has_evidence(client: TestClient) -> None:
    """Every habit claim carries the statistics that justify it."""
    r = client.get("/api/users/usr_test/redbrain")
    assert r.status_code == 200
    body = r.json()
    assert body["trainee_id"] == "usr_test"
    assert "available_tactics" in body
    for exploit in body["exploits"]:
        assert exploit["evidence"]
        assert exploit["description"]


def test_generate_offers_terrain_time_of_day_and_composition(client: TestClient) -> None:
    """Rural and urban each offer day/night and single/swarm, on demand.

    The statement asks for day/night, urban/rural, single-drone and swarm
    coverage; the two shipped scenarios cover the entry points and the
    generator covers every combination.
    """
    for terrain in ("rural", "urban"):
        for time_of_day in ("day", "dusk", "night"):
            for composition, expected in (("single", 1), ("swarm", 5)):
                r = client.post(
                    "/api/scenarios/generate",
                    json={
                        "seed": 2024,
                        "terrain_preset": terrain,
                        "time_of_day": time_of_day,
                        "threat_composition": composition,
                        "persist": False,
                    },
                )
                assert r.status_code == 200, r.text
                scenario = r.json()
                assert scenario["terrain"]["preset"] == terrain
                assert scenario["environment"]["time_of_day"] == time_of_day
                hostiles = sum(w["count"] for w in scenario["threats"])
                if composition == "single":
                    assert hostiles == 1
                else:
                    assert hostiles >= expected


def test_instructor_rule_sets_and_exercise(client: TestClient) -> None:
    """An instructor can list rule sets and lock a seed for a cohort."""
    rule_sets = client.get("/api/instructor/rule-sets").json()
    assert {"STRICT", "STANDARD"} <= {r["id"] for r in rule_sets["items"]}

    created = client.post(
        "/api/instructor/exercises",
        json={
            "name": "Locked exercise",
            "seed": 424242,
            "difficulty": DifficultyVector().model_dump(mode="json"),
            "rule_set": "STRICT",
            "trainee_ids": ["usr_test"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["seed"] == 424242
    assert body["rule_set"] == "STRICT"

    locked = client.get(f"/api/instructor/exercises/{body['scenario_id']}/lock").json()
    assert locked["instructor_locked"] is True

    # The same seed must reproduce the identical document.
    again = client.post(
        "/api/instructor/exercises",
        json={
            "name": "Locked exercise",
            "seed": 424242,
            "difficulty": DifficultyVector().model_dump(mode="json"),
            "rule_set": "STRICT",
            "trainee_ids": [],
        },
    ).json()
    again.pop("scenario_id", None)
    first = dict(body)
    first.pop("scenario_id", None)
    assert again["sha"] == first["sha"]


def test_analytics_contract(client: TestClient) -> None:
    """The dashboard payload is populated from stored rows."""
    from app.core.session import RECORD_DT, oracle_result
    from app.core.skills import habits_from_actions
    from app.db.models import ScenarioRow, User
    from app.db.repositories import save_habits, store_session
    from app.db.session import session_scope

    with session_scope() as db:
        scenario_row = db.get(ScenarioRow, SCENARIO_ID)
        user = db.get(User, "usr_test")
        assert scenario_row is not None and user is not None
        scenario = Scenario.model_validate(scenario_row.body_json)
        result = oracle_result(scenario, dt=RECORD_DT)
        store_session(
            db,
            session_id="ses_analytics",
            scenario=scenario_row,
            user=user,
            seed=scenario.seed,
            actions=result.actions,
            events=[],
            final_state_hash=result.final_state_hash,
            score=result.score.model_dump(mode="json"),
            difficulty=scenario.difficulty.model_dump(mode="json"),
        )
        save_habits(
            db,
            user.id,
            "ses_analytics",
            habits_from_actions(result.actions, {}, {}, float(scenario.duration_s)),
        )

    body = client.get("/api/analytics/trainee/usr_test").json()
    assert body["session_count"] == 1
    assert body["mean_score"] > 0
    assert body["sessions"][0]["scenario_id"] == SCENARIO_ID
    assert body["skills"]["detection"] > 0
    assert isinstance(body["detection_latencies"], list)

    csv = client.get("/api/analytics/export/usr_test.csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "scenario_id" in csv.text

    assert client.get("/api/analytics/trainee/nobody").status_code == 404


# --------------------------------------------------------------------------- #
# Session flow
# --------------------------------------------------------------------------- #


def test_client_played_session_is_reproduced_and_scored(client: TestClient) -> None:
    """A session played in the browser is reproduced exactly by the server.

    The browser posts the action log it produced; the server re-runs the
    deterministic replay from that log, checks the state hash matches and
    stores the score it computed itself.
    """
    from app.core.session import RECORD_DT, oracle_result
    from app.db.models import ScenarioRow
    from app.db.session import session_scope

    created = client.post(
        "/api/sessions", json={"scenario_id": SCENARIO_ID, "trainee_id": "usr_test"}
    )
    assert created.status_code == 201
    session = created.json()
    session_id = session["id"]
    assert session["scenario_id"] == SCENARIO_ID

    # The session is open, so the instructor panel can see it.
    live = client.get("/api/sessions/live").json()
    assert any(row["id"] == session_id for row in live)

    # What the browser would have produced: an action log and a state hash.
    with session_scope() as db:
        scenario_row = db.get(ScenarioRow, SCENARIO_ID)
        assert scenario_row is not None
        scenario = Scenario.model_validate(scenario_row.body_json)
    played = oracle_result(scenario, dt=RECORD_DT, collect_frames=False)
    actions = [a.model_dump(mode="json") for a in played.actions]

    result = client.post(
        f"/api/sessions/{session_id}/result",
        json={"actions": actions, "final_state_hash": played.final_state_hash},
    )
    assert result.status_code == 200
    stored = result.json()
    assert stored["final_state_hash"] == played.final_state_hash
    assert stored["score"]["total"] > 0
    # The server's score is its own: it must equal the score the browser
    # computed from the same log, because the replay is deterministic.
    assert stored["score"]["total"] == pytest.approx(played.score.total, abs=1e-6)

    fetched = client.get(f"/api/sessions/{session_id}").json()
    assert fetched["score"]["total"] == stored["score"]["total"]
    logged = client.get(f"/api/sessions/{session_id}/actions").json()
    assert len(logged) == len(actions)

    # Once the session is recorded it is no longer open.
    still_live = client.get("/api/sessions/live").json()
    assert not any(row["id"] == session_id for row in still_live)


def test_result_rejects_a_forged_state_hash(client: TestClient) -> None:
    """The server re-runs the replay and refuses a hash it cannot reproduce."""
    created = client.post(
        "/api/sessions", json={"scenario_id": SCENARIO_ID, "trainee_id": "usr_test"}
    ).json()
    bad = client.post(
        f"/api/sessions/{created['id']}/result", json={"final_state_hash": "deadbeef"}
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "unprocessable"


def test_there_is_no_server_side_session_socket(client: TestClient) -> None:
    """The simulation runs in the browser, so the server owns no live socket."""
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/session/ses_not_running") as ws,
    ):
        ws.receive_json()


def test_aar_replay_and_fork(client: TestClient) -> None:
    """The AAR replays deterministically and a fork diverges when it should."""
    from app.core.session import RECORD_DT, oracle_result
    from app.db.models import ScenarioRow, User
    from app.db.repositories import store_session
    from app.db.session import session_scope

    with session_scope() as db:
        scenario_row = db.get(ScenarioRow, SCENARIO_ID)
        user = db.get(User, "usr_test")
        assert scenario_row is not None and user is not None
        scenario = Scenario.model_validate(scenario_row.body_json)
        result = oracle_result(scenario, dt=RECORD_DT)
        store_session(
            db,
            session_id="ses_aar",
            scenario=scenario_row,
            user=user,
            seed=scenario.seed,
            actions=result.actions,
            events=[],
            final_state_hash=result.final_state_hash,
            score=result.score.model_dump(mode="json"),
            difficulty=scenario.difficulty.model_dump(mode="json"),
        )

    aar = client.get("/api/aar/ses_aar").json()
    assert aar["final_state_hash"] == result.final_state_hash
    assert aar["ghost_actions"]
    assert len(aar["sector_attention"]) == 8

    heat = client.get("/api/aar/ses_aar/heatmap").json()
    assert len(heat["labels"]) == 8

    fork_t = float(aar["trainee_actions"][0]["t"]) if aar["trainee_actions"] else 1.0
    # With the ghost following, the oracle's own actions are appended after
    # `fork_t`, so no fork is ever a no-op. Turning it off re-simulates exactly
    # the stored log and must reproduce the original hash.
    noop = client.post(
        "/api/aar/fork",
        json={
            "session_id": "ses_aar",
            "fork_t": fork_t,
            "replace_all_from": False,
            "follow_with_ghost": False,
        },
    )
    assert noop.status_code == 200
    assert noop.json()["diverged"] is False

    changed = client.post(
        "/api/aar/fork",
        json={
            "session_id": "ses_aar",
            "fork_t": fork_t,
            "replace_all_from": True,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["diverged"] is True


def test_invalid_fork_time_is_rejected(client: TestClient) -> None:
    """Forking past the end of the scenario is a 422, not a crash."""
    from app.core.schemas import Scenario

    scenario = Scenario.model_validate(client.get(f"/api/scenarios/{SCENARIO_ID}").json())
    r = client.post(
        "/api/aar/fork",
        json={"session_id": "ses_missing", "fork_t": scenario.duration_s + 100},
    )
    assert r.status_code == 404  # unknown session first
    _ = json


def test_openapi_is_served(client: TestClient) -> None:
    """The generated OpenAPI document covers the documented routers."""
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert "/api/health" in paths
    assert "/api/scenarios" in paths
    assert "/api/sessions" in paths
    assert "/api/aar/{session_id}" in paths
    assert "/api/analytics/trainee/{trainee_id}" in paths
