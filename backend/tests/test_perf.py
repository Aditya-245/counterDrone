"""Performance: the server tick budget on a laptop.

Spec section 11 asks for "server tick for 40 entities and 4 sensors under 20 ms
on a laptop, record the number in README". This module builds that exact world -
the generator's own sensors, forty contacts of mixed classes and labels - and
measures the wall-clock cost of `step()`.

Wall-clock time is measured *here*, in a test. The simulation core never reads a
clock (spec 0.4): a tick is a pure function of the previous state, the actions
and the RNG, so this measurement describes speed, never outcomes.
"""

from __future__ import annotations

import statistics
import time

import pytest

from app.core.engine import SimEngine
from app.core.generator import generate
from app.core.schemas import EntityClass, LabelType, Scenario, ThreatSpec

#: Spec budget: one 10 Hz tick for 40 entities and 4 sensors.
TICK_BUDGET_MS = 20.0

#: Warm-up ticks are excluded: the first tick spawns the waves and fills caches.
WARMUP_TICKS = 5
MEASURED_TICKS = 60

CLASSES = [
    EntityClass.QUAD_SMALL,
    EntityClass.FPV_FAST,
    EntityClass.FIXED_WING,
    EntityClass.BIRD_FLOCK,
    EntityClass.KITE_BALLOON,
    EntityClass.FRIENDLY_UAV,
    EntityClass.CIVIL_AIRCRAFT,
]
LABELS = [LabelType.HOSTILE, LabelType.CIVIL, LabelType.FRIENDLY, LabelType.BIRD]


def crowded_scenario(count: int = 40) -> Scenario:
    """A generated scenario rewritten to launch `count` simultaneous contacts.

    Distinct spawn bearings spread the wave around the site so the sensors, the
    terrain masking and the tracker all do real work; every fifth drone is
    RF-silent so the RF detector has to miss it.
    """
    base = generate(seed=909).scenario
    threats = [
        ThreatSpec(
            name=f"w{i}",
            entity_class=CLASSES[i % len(CLASSES)],
            label=LABELS[i % len(LABELS)],
            count=1,
            spawn_t=0.0,
            spawn_bearing_deg=(i * (360.0 / count)) % 360.0,
            spawn_jitter_deg=0.0,
            rf_active=(i % 5 != 0),
        )
        for i in range(count)
    ]
    return base.model_copy(
        update={"id": f"{base.id}_crowd", "name": "40-contact tick budget", "threats": threats}
    )


def measured_ticks(scenario: Scenario, ticks: int = MEASURED_TICKS) -> list[float]:
    """Milliseconds per tick, warm-up excluded."""
    engine = SimEngine(scenario, dt=0.1)
    for _ in range(WARMUP_TICKS):
        engine.tick()
    samples: list[float] = []
    for _ in range(ticks):
        started = time.perf_counter()
        engine.tick()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


@pytest.mark.perf
def test_tick_budget_for_forty_entities_and_four_sensors() -> None:
    """`step()` stays inside the 20 ms budget on this laptop."""
    scenario = crowded_scenario()
    assert len(scenario.sensors) >= 4, "the generator must supply at least four sensors"
    samples = measured_ticks(scenario)
    mean = statistics.fmean(samples)
    worst = max(samples)
    print(
        f"\n  tick budget: {len(scenario.sensors)} sensors, 40 contacts"
        f"\n  mean {mean:.2f} ms   median {statistics.median(samples):.2f} ms"
        f"   p95 {sorted(samples)[int(len(samples) * 0.95) - 1]:.2f} ms   worst {worst:.2f} ms"
        f"\n  budget {TICK_BUDGET_MS:.0f} ms -> {'OK' if mean < TICK_BUDGET_MS else 'OVER'}"
    )
    assert (
        mean < TICK_BUDGET_MS
    ), f"mean tick {mean:.2f} ms exceeds the {TICK_BUDGET_MS:.0f} ms budget"


@pytest.mark.perf
def test_all_forty_contacts_exist_and_are_tracked() -> None:
    """The measurement is only meaningful if the world really is crowded."""
    scenario = crowded_scenario()
    engine = SimEngine(scenario, dt=0.1)
    for _ in range(WARMUP_TICKS):
        engine.tick()
    world = engine.state.world
    assert len(world.entities) == 40, f"expected 40 contacts, found {len(world.entities)}"
    for _ in range(40):
        engine.tick()
    confirmed = [t for t in engine.state.tracker_engine.state.tracks.values() if t.confirmed]
    assert world.entities, "the world emptied out"
    assert confirmed, "four sensors and forty contacts produced no confirmed tracks"
