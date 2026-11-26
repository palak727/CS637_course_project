from dataclasses import dataclass
import math
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs)-> bool:
        return False


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SUMO_CFG = str(_PROJECT_ROOT / "sumo" / "cross.sumocfg")


_seen_env_files: set[Path] = set()
for _env_file in (
    _BACKEND_ROOT / ".env",
    _PROJECT_ROOT / ".env",
    Path.cwd() / ".env",
):
    _env_file = _env_file.resolve()
    if _env_file in _seen_env_files:
        continue
    _seen_env_files.add(_env_file)
    if _env_file.exists():
        load_dotenv(_env_file, override=False)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    traffic_mode: str = os.getenv("TRAFFIC_MODE", "mock").strip().lower()
    video_source: str = os.getenv("VIDEO_SOURCE", "0").strip()
    yolo_model: str = os.getenv("YOLO_MODEL", "yolo11n.pt").strip()
    yolo_confidence: float = _float("YOLO_CONFIDENCE", 0.35)
    serial_mode: str = os.getenv("SERIAL_MODE", "mock").strip().lower()
    serial_port: str = os.getenv("SERIAL_PORT", "COM5").strip()
    serial_baud: int = _int("SERIAL_BAUD", 115200)
    min_green: int = _int("MIN_GREEN", 8)
    max_green: int = _int("MAX_GREEN", 45)
    startup_lost_time: float = _float("STARTUP_LOST_TIME", 2.0)
    saturation_flow_per_lane: float = _float("SATURATION_FLOW_PER_LANE", 0.5)
    arrival_rate_fraction: float = _float("ARRIVAL_RATE_FRACTION", 0.2)
    effective_lanes: int = _int("EFFECTIVE_LANES", 1)
    effective_lanes_n: int = _int("EFFECTIVE_LANES_N", _int("EFFECTIVE_LANES", 1))
    effective_lanes_s: int = _int("EFFECTIVE_LANES_S", _int("EFFECTIVE_LANES", 1))
    effective_lanes_e: int = _int("EFFECTIVE_LANES_E", _int("EFFECTIVE_LANES", 1))
    effective_lanes_w: int = _int("EFFECTIVE_LANES_W", _int("EFFECTIVE_LANES", 1))
    min_eligible_demand: float = _float("MIN_ELIGIBLE_DEMAND", 0.5)
    max_priority_age_seconds: float = _float("MAX_PRIORITY_AGE_SECONDS", 90.0)
    demand_median_samples: int = _int("DEMAND_MEDIAN_SAMPLES", 5)
    demand_lambda_up: float = _float("DEMAND_LAMBDA_UP", 0.5)
    demand_lambda_down: float = _float("DEMAND_LAMBDA_DOWN", 0.2)
    yellow_seconds: int = _int("YELLOW_SECONDS", 3)
    all_red_seconds: int = _int("ALL_RED_SECONDS", 1)
    frontend_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    )
    # ── SUMO / TraCI integration (disabled by default) ───────────────────
    # Set SUMO_ENABLED=true to start SUMO alongside the adaptive controller.
    sumo_enabled: bool = os.getenv("SUMO_ENABLED", "false").lower() == "true"
    # Path to cross.sumocfg; defaults to <project_root>/sumo/cross.sumocfg.
    sumo_cfg: str = os.getenv("SUMO_CFG", _DEFAULT_SUMO_CFG)
    # Set SUMO_GUI=true to open the sumo-gui window (useful during development).
    sumo_gui: bool = os.getenv("SUMO_GUI", "false").lower() == "true"
    # Seconds of real time between consecutive traci.simulationStep() calls.
    sumo_step_length: float = _float("SUMO_STEP_LENGTH", 1.0)

    def __post_init__(self) -> None:
        numeric_values = (
            self.yolo_confidence,
            self.startup_lost_time,
            self.saturation_flow_per_lane,
            self.arrival_rate_fraction,
            self.min_eligible_demand,
            self.max_priority_age_seconds,
            self.demand_lambda_up,
            self.demand_lambda_down,
            self.sumo_step_length,
        )
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Floating-point settings must be finite")
        if self.traffic_mode not in {"mock", "yolo"}:
            raise ValueError("TRAFFIC_MODE must be 'mock' or 'yolo'")
        if self.serial_mode not in {"mock", "serial"}:
            raise ValueError("SERIAL_MODE must be 'mock' or 'serial'")
        if not 0 <= self.yolo_confidence <= 1:
            raise ValueError("YOLO_CONFIDENCE must be between 0 and 1")
        if self.min_green < 1 or self.max_green < self.min_green or self.max_green > 60:
            raise ValueError("Green durations must satisfy 1 <= MIN_GREEN <= MAX_GREEN <= 60")
        if not 1 <= self.yellow_seconds <= 60 or not 1 <= self.all_red_seconds <= 60:
            raise ValueError("YELLOW_SECONDS and ALL_RED_SECONDS must be between 1 and 60")
        if self.startup_lost_time < 0 or self.saturation_flow_per_lane <= 0:
            raise ValueError("Startup lost time must be nonnegative and saturation flow positive")
        if not 0 <= self.arrival_rate_fraction < 1:
            raise ValueError("ARRIVAL_RATE_FRACTION must be in [0, 1)")
        if self.min_eligible_demand < 0 or self.max_priority_age_seconds <= 0:
            raise ValueError("Demand threshold must be nonnegative and priority age positive")
        if self.demand_median_samples < 1:
            raise ValueError("DEMAND_MEDIAN_SAMPLES must be at least 1")
        if not 0 <= self.demand_lambda_up <= 1 or not 0 <= self.demand_lambda_down <= 1:
            raise ValueError("Demand EMA lambdas must be between 0 and 1")
        if self.serial_baud <= 0 or self.sumo_step_length <= 0:
            raise ValueError("SERIAL_BAUD and SUMO_STEP_LENGTH must be positive")


settings = Settings()

