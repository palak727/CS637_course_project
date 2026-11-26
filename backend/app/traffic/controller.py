import asyncio
import math
import time
from uuid import uuid4

from app.hardware.esp32 import ESP32Bridge
from app.models import Phase
from app.state import StateStore
from app.traffic.safety import GREEN_PHASES, YELLOW_FOR_GREEN, signals_for, validate_transition
from app.traffic.scheduler import AdaptiveScheduler


class TrafficController:
    def __init__(
        self,
        store: StateStore,
        scheduler: AdaptiveScheduler,
        bridge: ESP32Bridge,
        yellow_seconds: int,
        all_red_seconds: int,
    ):
        self.store = store
        self.scheduler = scheduler
        self.bridge = bridge
        self.yellow_seconds = yellow_seconds
        self.all_red_seconds = all_red_seconds
        self._shutdown = False
        self._cycle_lock = asyncio.Lock()
        self._manual: asyncio.Queue[tuple[Phase, int]] = asyncio.Queue()

    async def request_manual(self, phase: Phase, duration: int) -> None:
        await self._manual.put((phase, duration))

    async def stop(self) -> None:
        await self.store.patch(running=False)
        async with self._cycle_lock:
            if (await self.store.snapshot()).phase != Phase.ALL_RED:
                await self._force_all_red()
            self._shutdown = True

    async def run(self) -> None:
        while not self._shutdown:
            should_wait = False
            async with self._cycle_lock:
                if self._shutdown:
                    break
                state = await self.store.snapshot()
                if not state.running:
                    if state.phase != Phase.ALL_RED:
                        await self._force_all_red()
                    should_wait = True
                elif not self._manual.empty():
                    requested, duration = await self._manual.get()
                    if requested in GREEN_PHASES:
                        await self.store.patch(next_phase=requested)
                        await self._safe_move_to(requested, duration)
                        self.scheduler.mark_green_finished(requested)
                        await self._update_next_phase()
                        await self._set_phase(YELLOW_FOR_GREEN[requested], self.yellow_seconds)
                        await self._set_phase(Phase.ALL_RED, self.all_red_seconds)
                        if not (await self.store.snapshot()).running:
                            await self.store.patch(next_phase=Phase.ALL_RED)
                else:
                    snapshot = await self.store.snapshot()
                    next_green = self.scheduler.choose_phase(snapshot.traffic)
                    if next_green is None:
                        await self.store.patch(next_phase=Phase.ALL_RED)
                        should_wait = True
                    else:
                        duration = self.scheduler.green_duration(next_green, snapshot.traffic)
                        await self.store.patch(next_phase=next_green)
                        await self._set_phase(next_green, duration)
                        self.scheduler.mark_green_finished(next_green)
                        await self._update_next_phase()
                        await self._set_phase(YELLOW_FOR_GREEN[next_green], self.yellow_seconds)
                        await self._set_phase(Phase.ALL_RED, self.all_red_seconds)
                        after = await self.store.snapshot()
                        if not after.running:
                            await self.store.patch(next_phase=Phase.ALL_RED)
                        await self.store.patch(cycle=after.cycle + 1)
            if should_wait:
                await asyncio.sleep(0.2)

    async def _safe_move_to(self, target: Phase, duration: int) -> None:
        current = (await self.store.snapshot()).phase
        if current == target:
            await self._set_phase(target, duration, allow_same=True)
            return
        if current in GREEN_PHASES:
            self.scheduler.mark_green_finished(current)
            await self._set_phase(YELLOW_FOR_GREEN[current], self.yellow_seconds)
        if (await self.store.snapshot()).phase != Phase.ALL_RED:
            await self._set_phase(Phase.ALL_RED, self.all_red_seconds)
        await self._set_phase(target, duration)

    async def _force_all_red(self) -> None:
        current = (await self.store.snapshot()).phase
        if current in GREEN_PHASES:
            await self._set_phase(YELLOW_FOR_GREEN[current], self.yellow_seconds)
        if (await self.store.snapshot()).phase != Phase.ALL_RED:
            await self._set_phase(Phase.ALL_RED, self.all_red_seconds)
        else:
            await self.bridge.set_phase(Phase.ALL_RED, 0)
        await self.store.patch(next_phase=Phase.ALL_RED)

    async def _update_next_phase(self) -> None:
        state = await self.store.snapshot()
        next_phase = self.scheduler.preview_phase(state.traffic) or Phase.ALL_RED
        await self.store.patch(next_phase=next_phase)

    async def _set_phase(self, phase: Phase, duration: int, allow_same: bool = False) -> None:
        current = (await self.store.snapshot()).phase
        if current != phase and not validate_transition(current, phase):
            raise RuntimeError(f"Unsafe transition rejected: {current} -> {phase}")
        if current == phase and not allow_same and phase != Phase.ALL_RED:
            raise RuntimeError(f"Duplicate phase rejected: {phase}")

        ok, ack = await self.bridge.set_phase(phase, duration)
        state = await self.store.snapshot()
        esp = state.esp32.model_copy(update={
            "connected": ok,
            "last_ack": ack if ok else state.esp32.last_ack,
            "last_error": None if ok else ack,
        })
        changes = dict(
            phase=phase,
            signals=signals_for(phase),
            remaining=duration,
            phase_duration=duration,
            esp32=esp,
            last_transition_id=uuid4().hex[:10],
        )
        await self.store.patch(**changes)

        deadline = time.monotonic() + duration
        last_remaining = duration
        while not self._shutdown:
            state = await self.store.snapshot()
            # A stop may shorten a green, but never the safety clearance phases.
            if not state.running and phase in GREEN_PHASES:
                break
            remaining = max(0, math.ceil(deadline - time.monotonic()))
            if remaining != last_remaining:
                await self.store.patch(remaining=remaining)
                last_remaining = remaining
            if remaining <= 0:
                break
            await asyncio.sleep(0.1)
