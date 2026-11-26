import asyncio
import logging

from app.hardware.esp32 import ESP32Bridge
from app.models import Phase

log = logging.getLogger(__name__)


class CompositeBridge(ESP32Bridge):
    """
    Fan-out bridge: calls connect/set_phase/close on every child bridge
    in parallel so the TrafficController drives both ESP32 and SUMO with
    a single bridge reference.

    On set_phase() the first successful (ok=True) result is returned.
    If all bridges fail the last failure is returned.
    On connect() each bridge is attempted independently; failures are
    logged as warnings so a SUMO startup error never kills the app.
    """

    def __init__(self, *bridges):
        self._bridges = list(bridges)
        self.primary_connected = False
        self.primary_error = None

    async def connect(self):
        log.info("CompositeBridge: connecting %d bridges", len(self._bridges))
        results = await asyncio.gather(
            *(b.connect() for b in self._bridges),
            return_exceptions=True,
        )
        for bridge, result in zip(self._bridges, results):
            if isinstance(result, Exception):
                log.warning(
                    "CompositeBridge: %s.connect() failed: %s",
                    type(bridge).__name__,
                    result,
                )
                if bridge is self._bridges[0]:
                    self.primary_connected = False
                    self.primary_error = str(result)
            else:
                log.info("CompositeBridge: %s.connect() OK", type(bridge).__name__)
                if bridge is self._bridges[0]:
                    self.primary_connected = True
                    self.primary_error = None

    async def set_phase(self, phase, duration):
        log.info("CompositeBridge: forwarding phase=%s duration=%s to %d bridges", phase, duration, len(self._bridges))
        results = await asyncio.gather(
            *(b.set_phase(phase, duration) for b in self._bridges),
            return_exceptions=True,
        )
        primary_result = (False, "no-bridges")
        for bridge, r in zip(self._bridges, results):
            if isinstance(r, Exception):
                log.debug(
                    "CompositeBridge: %s.set_phase(%s) raised: %s",
                    type(bridge).__name__, phase, r,
                )
                result = (False, str(r))
            else:
                result = r
            if bridge is self._bridges[0]:
                primary_result = result
            elif not result[0]:
                log.warning("CompositeBridge secondary %s failed: %s", type(bridge).__name__, result[1])
        return primary_result

    async def close(self):
        await asyncio.gather(
            *(b.close() for b in self._bridges),
            return_exceptions=True,
        )
