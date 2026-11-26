import asyncio
import logging
import math
import random
import time
from pathlib import Path
from typing import AsyncIterator

from app.models import ApproachState, Direction, Phase
from app.state import StateStore
from app.traffic.scheduler import AdaptiveScheduler
from app.vision.analyzer import aggregate_detections


GREEN_FOR_DIRECTION = {
    Direction.N: Phase.N_GREEN,
    Direction.E: Phase.E_GREEN,
    Direction.S: Phase.S_GREEN,
    Direction.W: Phase.W_GREEN,
}
log = logging.getLogger(__name__)


class VisionService:
    def __init__(
        self,
        store: StateStore,
        scheduler: AdaptiveScheduler,
        mode: str,
        source: str,
        model_name: str,
        confidence: float,
    ):
        self.store = store
        self.scheduler = scheduler
        self.mode = mode
        self.source = source
        self.model_name = model_name
        self.confidence = confidence
        self._waiting_seconds = {direction: 0.0 for direction in Direction}
        self._running = False
        self._jpeg: bytes | None = None
        self._jpeg_lock = asyncio.Lock()

    async def run(self) -> None:
        self._running = True
        try:
            if self.mode == "yolo":
                await self._run_yolo()
            elif self.mode == "mock":
                await self._run_mock()
            else:
                raise RuntimeError(f"Unsupported TRAFFIC_MODE={self.mode!r}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Vision service stopped: %s", exc)
            await self.store.patch(ai_running=False, fps=0.0, vision_error=str(exc))
        finally:
            self._running = False
            await self.store.patch(ai_running=False)

    async def stop(self) -> None:
        self._running = False

    async def _run_mock(self) -> None:
        tick = 0
        previous_sample = time.perf_counter() - 1.0
        while self._running:
            now = time.perf_counter()
            elapsed = max(0.0, now - previous_sample)
            previous_sample = now
            state = await self.store.snapshot()
            traffic: dict[Direction, ApproachState] = {}
            for index, direction in enumerate(Direction):
                wave = (math.sin(tick / 8 + index * 1.7) + 1) / 2
                vehicles = max(0, round(3 + 13 * wave + random.uniform(-1.5, 1.5)))
                was_waiting = state.phase != GREEN_FOR_DIRECTION[direction]
                previous_wait = state.traffic[direction].waiting_seconds
                wait = previous_wait + elapsed if was_waiting and vehicles else 0.0
                traffic[direction] = ApproachState(
                    vehicles=vehicles,
                    weighted_vehicles=round(vehicles * random.uniform(0.9, 1.2), 1),
                    waiting_seconds=wait,
                )
            traffic = self.scheduler.enrich_demands(traffic)
            await self.store.update_traffic(traffic, fps=1 / max(elapsed, 0.001))
            tick += 1
            await asyncio.sleep(1)

    async def _run_yolo(self) -> None:
        try:
            import cv2
            from ultralytics import YOLO
            import torch
            torch.set_num_threads(2)
        except ImportError as exc:
            raise RuntimeError(
                "YOLO mode needs requirements-vision.txt. Use TRAFFIC_MODE=mock meanwhile."
            ) from exc

        model_path = Path(self.model_name)
        if not model_path.is_absolute() and not model_path.is_file():
            bundled_model = Path(__file__).resolve().parents[2] / model_path
            if bundled_model.is_file():
                model_path = bundled_model
        model = await asyncio.to_thread(YOLO, str(model_path))
        source = int(self.source) if self.source.isdigit() else self.source
        if isinstance(source, int):
            capture = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        else:
            capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Could not open VIDEO_SOURCE={self.source}")

        previous = time.perf_counter()
        failed_reads = 0
        try:
            while self._running:
                ok, frame = await asyncio.to_thread(capture.read)
                if not ok:
                    if isinstance(source, str):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        failed_reads += 1
                        if failed_reads >= 3:
                            raise RuntimeError(
                                f"VIDEO_SOURCE={self.source} produced no readable frames"
                            )
                        await asyncio.sleep(0.05)
                        continue
                    failed_reads += 1
                    if failed_reads >= 50:
                        raise RuntimeError(
                            f"Camera VIDEO_SOURCE={self.source} failed to deliver 50 consecutive frames"
                        )
                    await asyncio.sleep(0.1)
                    continue
                failed_reads = 0

                results = await asyncio.to_thread(
                    model.predict,
                    frame,
                    conf=self.confidence,
                    verbose=False,
                    classes=[2, 3, 5, 7],  # COCO: car, motorcycle, bus, truck
                )
                result = results[0]
                detections = []
                for box in result.boxes:
                    class_id = int(box.cls[0])
                    detections.append({
                        "class": model.names[class_id],
                        "confidence": float(box.conf[0]),
                        "box": [float(v) for v in box.xyxy[0]],
                    })

                height, width = frame.shape[:2]
                measured = aggregate_detections(detections, width, height)
                old = await self.store.snapshot()
                now = time.perf_counter()
                elapsed = max(0.0, now - previous)
                for direction, approach in measured.items():
                    green = old.phase == GREEN_FOR_DIRECTION[direction]
                    waiting = self._waiting_seconds[direction]
                    if green or approach.vehicles == 0:
                        waiting = 0.0
                    else:
                        waiting += elapsed
                    self._waiting_seconds[direction] = waiting
                    approach.waiting_seconds = waiting
                measured = self.scheduler.enrich_demands(measured)

                fps = 1 / max(now - previous, 0.001)
                previous = now
                await self.store.update_traffic(measured, fps)

                annotated = result.plot()
                cv2.line(annotated, (width // 2, 0), (width // 2, height), (75, 206, 151), 1)
                cv2.line(annotated, (0, height // 2), (width, height // 2), (75, 206, 151), 1)
                encoded, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if encoded:
                    async with self._jpeg_lock:
                        self._jpeg = jpeg.tobytes()
                await asyncio.sleep(0.04)
        except Exception:
            await self.store.patch(ai_running=False, fps=0.0)
            raise
        finally:
            capture.release()

    async def mjpeg(self) -> AsyncIterator[bytes]:
        while self._running:
            async with self._jpeg_lock:
                frame = self._jpeg
            if frame:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0.08)

