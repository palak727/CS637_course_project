import time

from app.vision.analyzer import aggregate_detections
from app.models import ApproachState, Direction, Phase
from app.traffic.scheduler import AdaptiveScheduler, SchedulerConfig


def sample_traffic():
    return {
        Direction.N: ApproachState(weighted_vehicles=10, demand=10),
        Direction.S: ApproachState(weighted_vehicles=8, demand=8),
        Direction.E: ApproachState(weighted_vehicles=2, demand=2),
        Direction.W: ApproachState(weighted_vehicles=3, demand=3),
    }


def test_full_frame_eligibility_and_age_priority():
    measured = aggregate_detections(
        [{"class": "motorcycle", "box": [0, 0, 10, 10]}],
        width=100,
        height=100,
    )
    assert measured[Direction.N].vehicles == 1
    assert measured[Direction.N].weighted_vehicles == 0.5

    scheduler = AdaptiveScheduler(SchedulerConfig())
    traffic = scheduler.enrich_demands(measured)
    assert traffic[Direction.N].demand == 0.5
    assert scheduler.choose_phase(traffic) == Phase.N_GREEN

    empty = {direction: ApproachState() for direction in Direction}
    assert scheduler.choose_phase(empty) is None

    priority_scheduler = AdaptiveScheduler(SchedulerConfig())
    now = time.monotonic()
    priority_scheduler.mark_green_finished(Phase.N_GREEN, now - 90)
    for phase in (Phase.E_GREEN, Phase.S_GREEN, Phase.W_GREEN):
        priority_scheduler.mark_green_finished(phase, now)
    priority_traffic = {direction: ApproachState() for direction in Direction}
    priority_traffic[Direction.N] = ApproachState(demand=0.5)
    priority_traffic[Direction.E] = ApproachState(demand=1.0)
    assert priority_scheduler.choose_phase(priority_traffic) == Phase.N_GREEN
    priority_scheduler.mark_green_finished(Phase.N_GREEN, now)
    assert priority_scheduler.preview_phase(priority_traffic) == Phase.E_GREEN

    scheduler = AdaptiveScheduler(SchedulerConfig())
    traffic = sample_traffic()
    assert scheduler.green_duration(Phase.N_GREEN, traffic) > scheduler.green_duration(Phase.E_GREEN, traffic)


def test_green_duration_is_bounded():
    scheduler = AdaptiveScheduler(SchedulerConfig(min_green=10, max_green=40))
    traffic = sample_traffic()
    for state in traffic.values():
        state.weighted_vehicles = 1000
        state.demand = 1000
    assert scheduler.green_duration(Phase.N_GREEN, traffic) == 40

    invalid_lanes = {direction: 0 for direction in Direction}
    conservative = AdaptiveScheduler(SchedulerConfig(
        min_green=10,
        max_green=40,
        effective_lanes=invalid_lanes,
    ))
    assert conservative.green_duration(Phase.N_GREEN, traffic) == 10

