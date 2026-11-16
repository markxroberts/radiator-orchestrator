"""Exercise the coroutine listener contract for cycle telemetry."""
import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch


class CycleEventTests(IsolatedAsyncioTestCase):
    def setUp(self):
        package = ModuleType("cycle_test_integration")
        package.__path__ = [str(Path(__file__).resolve().parents[1]
                                / "custom_components" / "radiator_orchestrator")]
        with patch.dict(sys.modules, {package.__name__: package}):
            module = importlib.import_module(f"{package.__name__}.ch_cycle_recorder")
        self.states = {}
        ctrl = SimpleNamespace(hass=SimpleNamespace(states=self.states), settings={},
                               rooms={}, ems=SimpleNamespace(sources={}), boiler_requested=False)
        self.recorder = module.CHCycleRecorder(ctrl)

    async def emit(self, flame):
        self.states["binary_sensor.boiler_burngas"] = SimpleNamespace(state=flame)
        listener = self.recorder.async_handle_state_change
        # Model HA's dispatch of coroutine listeners and plain synchronous jobs.
        if inspect.iscoroutinefunction(listener):
            await listener(None)
        else:
            await asyncio.to_thread(listener, None)

    async def test_completed_cycle_publishes_once_on_event_loop(self):
        loop = asyncio.get_running_loop()
        writes = []

        def write_state():
            writes.append((asyncio.get_running_loop(), self.recorder.completed))

        self.recorder.subscribe(write_state)
        for flame in ("off", "on", "on", "off", "off"):
            await self.emit(flame)
        self.assertEqual(writes, [(loop, 1)])

    async def test_removed_sensor_receives_no_write(self):
        writes = []
        remove = self.recorder.subscribe(lambda: writes.append(self.recorder.completed))
        await self.emit("off")
        await self.emit("on")
        remove()
        await self.emit("off")
        self.assertEqual(self.recorder.completed, 1)
        self.assertEqual(writes, [])
