from collections import defaultdict

from app.models import ApproachState, Direction


VEHICLE_WEIGHTS = {
    "motorcycle": 0.5,
    "car": 1.0,
    "bus": 2.5,
    "truck": 2.5,
}


def direction_from_center(x: float, y: float, width: int, height: int) -> Direction:
    """Assign a detection to its closest incoming edge of an overhead frame."""
    dx = x - width / 2
    dy = y - height / 2
    if abs(dx) > abs(dy):
        return Direction.E if dx > 0 else Direction.W
    return Direction.S if dy > 0 else Direction.N


def aggregate_detections(
    detections: list[dict], width: int, height: int
) -> dict[Direction, ApproachState]:
    """Aggregate every supported vehicle detection into a directional weighted demand total."""
    totals = defaultdict(lambda: {"vehicles": 0, "weighted": 0.0})
    for detection in detections:
        name = detection["class"]
        if name not in VEHICLE_WEIGHTS:
            continue
        x1, y1, x2, y2 = detection["box"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        direction = direction_from_center(cx, cy, width, height)
        totals[direction]["vehicles"] += 1
        totals[direction]["weighted"] += VEHICLE_WEIGHTS[name]

    return {
        direction: ApproachState(
            vehicles=totals[direction]["vehicles"],
            weighted_vehicles=round(totals[direction]["weighted"], 1),
        )
        for direction in Direction
    }

