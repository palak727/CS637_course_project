from collections import deque
from dataclasses import dataclass, field
import math
import statistics
import time

from app.models import ApproachState, Direction, Phase


PHASE_DIRECTION = {
    Phase.N_GREEN: Direction.N,
    Phase.E_GREEN: Direction.E,
    Phase.S_GREEN: Direction.S,
    Phase.W_GREEN: Direction.W,
}
PHASE_FOR_DIRECTION = {direction: phase for phase, direction in PHASE_DIRECTION.items()}
DIRECTION_ORDER = (Direction.N, Direction.E, Direction.S, Direction.W)


@dataclass(frozen=True)
class SchedulerConfig:
    min_green: int = 8
    max_green: int = 45
    startup_lost_time: float = 2.0
    saturation_flow_per_lane: float = 0.5
    arrival_rate_fraction: float = 0.2
    effective_lanes: dict[Direction, int] = field(
        default_factory=lambda: {direction: 1 for direction in Direction}
    )
    min_eligible_demand: float = 0.5
    age_weight: float = 0.1
    max_priority_age_seconds: float = 90.0
    median_samples: int = 5
    lambda_up: float = 0.5
    lambda_down: float = 0.2


class AdaptiveScheduler:
    """Smooth weighted demand and select phases using demand and waiting age."""

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self._samples = {
            direction: deque(maxlen=max(1, config.median_samples))
            for direction in Direction
        }
        self._filtered: dict[Direction, float] = {}
        now = time.monotonic()
        self._last_finished_green = {direction: now for direction in Direction}
        self._last_served: Direction | None = None

    def approach_demand(self, state: ApproachState) -> float:
        """Return the smoothed full-frame weighted demand for one approach."""
        return max(0.0, float(state.demand))

    def enrich_demands(
        self, traffic: dict[Direction, ApproachState]
    ) -> dict[Direction, ApproachState]:
        enriched = {}
        for direction in Direction:
            state = traffic[direction]
            sample = max(0.0, float(state.weighted_vehicles))
            samples = self._samples[direction]
            samples.append(sample)
            median = statistics.median(samples)
            previous = self._filtered.get(direction, median)
            alpha = self.config.lambda_up if median >= previous else self.config.lambda_down
            filtered = previous + alpha * (median - previous)
            if not math.isfinite(filtered):
                filtered = 0.0
            self._filtered[direction] = max(0.0, filtered)
            enriched[direction] = state.model_copy(update={"demand": round(self._filtered[direction], 2)})
        return enriched

    def age_seconds(self, direction: Direction, now: float | None = None) -> float:
        elapsed = (time.monotonic() if now is None else now) - self._last_finished_green[direction]
        return max(0.0, elapsed) if math.isfinite(elapsed) else 0.0

    def mark_green_finished(self, phase: Phase, when: float | None = None) -> None:
        direction = PHASE_DIRECTION.get(phase)
        if direction is not None:
            self._last_finished_green[direction] = time.monotonic() if when is None else when
            self._last_served = direction

    def _tie_order(self) -> tuple[Direction, ...]:
        if self._last_served is None:
            return DIRECTION_ORDER
        index = DIRECTION_ORDER.index(self._last_served)
        return DIRECTION_ORDER[index + 1:] + DIRECTION_ORDER[:index + 1]

    def choose_phase(self, traffic: dict[Direction, ApproachState]) -> Phase | None:
        return self._select_phase(traffic, commit=True)

    def preview_phase(self, traffic: dict[Direction, ApproachState]) -> Phase | None:
        """Predict the next eligible phase without changing tie-break state."""
        return self._select_phase(traffic, commit=False)

    def _select_phase(
        self, traffic: dict[Direction, ApproachState], commit: bool
    ) -> Phase | None:
        eligible = [
            direction for direction in DIRECTION_ORDER
            if self.approach_demand(traffic[direction]) > 0
            and self.approach_demand(traffic[direction]) >= max(0.0, self.config.min_eligible_demand)
        ]
        if not eligible:
            return None
        ages = {direction: self.age_seconds(direction) for direction in eligible}
        overdue = [d for d in eligible if ages[d] >= self.config.max_priority_age_seconds]
        tie_rank = {direction: rank for rank, direction in enumerate(self._tie_order())}
        if overdue:
            selected = max(overdue, key=lambda d: (ages[d], -tie_rank[d]))
        else:
            selected = max(
                eligible,
                key=lambda d: (
                    self.approach_demand(traffic[d]) + self.config.age_weight * ages[d],
                    -tie_rank[d],
                ),
            )
        if commit:
            self._last_served = selected
        return PHASE_FOR_DIRECTION[selected]

    def phase_score(self, phase: Phase, traffic: dict[Direction, ApproachState]) -> float:
        direction = PHASE_DIRECTION[phase]
        return self.approach_demand(traffic[direction]) + self.config.age_weight * self.age_seconds(direction)

    def green_duration(self, phase: Phase, traffic: dict[Direction, ApproachState]) -> int:
        direction = PHASE_DIRECTION[phase]
        minimum = max(1, int(self.config.min_green))
        maximum = max(minimum, int(self.config.max_green))
        demand = self.approach_demand(traffic[direction])
        try:
            lanes = int(self.config.effective_lanes.get(direction, 0))
            saturation = float(self.config.saturation_flow_per_lane) * lanes
            arrival = saturation * float(self.config.arrival_rate_fraction)
            denominator = saturation - arrival
            lost = float(self.config.startup_lost_time)
            target = (
                lost + demand / denominator
                if lanes > 0 and math.isfinite(denominator) and denominator > 0
                else float(minimum)
            )
            if math.isnan(target):
                target = float(minimum)
            elif not math.isfinite(target):
                target = float(maximum)
        except (TypeError, ValueError, ZeroDivisionError):
            target = float(minimum)
        return int(round(min(maximum, max(minimum, target))))
