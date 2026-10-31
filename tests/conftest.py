"""Controller tests use minimal HA stand-ins; live HA integration testing is separate."""
import asyncio
import importlib.util
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator"


def register(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


@pytest.fixture
def controller_module(monkeypatch):
    for key in list(sys.modules):
        if key.startswith("homeassistant") or key.startswith("radiator_orchestrator"):
            monkeypatch.delitem(sys.modules, key)
    register("homeassistant")
    register("homeassistant.core", HomeAssistant=object)
    register("homeassistant.const", ATTR_ENTITY_ID="entity_id", STATE_ON="on", STATE_OFF="off")
    register("homeassistant.helpers")
    class Store:
        def __init__(self, *args): self.data = {}
        async def async_load(self): return self.data
        async def async_save(self, data): self.data = data
    register("homeassistant.helpers.storage", Store=Store)
    register("homeassistant.util")
    dt = register("homeassistant.util.dt")
    current = [datetime(2026, 10, 12, 6, 0, tzinfo=ZoneInfo("Europe/London"))]
    dt.now = lambda: current[0]
    dt.utcnow = lambda: current[0].astimezone(timezone.utc)
    dt.get_time_zone = lambda n: ZoneInfo(n)
    pkg = register("radiator_orchestrator")
    pkg.__path__ = [str(ROOT)]
    flow = register("radiator_orchestrator.config_flow")
    flow.SCHEDULE_TIMES = {
        "weekday_morning": "06:30", "weekday_work": "08:30",
        "weekday_evening": "17:00", "weekday_bed": "22:30",
        "weekend_rise": "08:00", "weekend_bed": "23:00", "early_start_time": "05:45"}
    flow.SCHEDULE_TARGETS = {
        "morning_target": 20., "evening_target": 21.,
        "day_bedroom_target": 16., "bedroom_target": 19.,
        "bathroom_morning_target": 22., "bathroom_evening_target": 22.,
        "bathroom_day_target": 17.}
    name = "radiator_orchestrator.controller"
    spec = importlib.util.spec_from_file_location(name, ROOT / "controller.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    yield mod, current


class State:
    def __init__(self, value, **attributes):
        self.state = str(value)
        self.attributes = attributes


class States:
    def __init__(self): self.items = {}
    def get(self, entity_id): return self.items.get(entity_id)
    def set(self, entity_id, state, **attr): self.items[entity_id] = State(state, **attr)


class Services:
    def __init__(self, states): self.states, self.actions = states, []
    async def async_call(self, domain, action, data, blocking=True):
        self.actions.append((domain, action, dict(data)))
        eid = data["entity_id"]
        if domain == "climate":
            self.states.set(eid, "heat", temperature=data["temperature"])
        else:
            self.states.set(eid, "on" if action == "turn_on" else "off")


class HA:
    def __init__(self):
        self.states = States()
        self.services = Services(self.states)
        self.config = types.SimpleNamespace(time_zone="Europe/London")


@pytest.fixture
def house(controller_module):
    mod, current = controller_module
    ha = HA()
    ha.states.set("switch.boiler", "off")
    ha.states.set("switch.dhw", "off")
    ha.states.set("binary_sensor.house", "on")
    ha.states.set("binary_sensor.workday", "on")
    ha.states.set("sensor.outside", "5")
    for name in ("bed_a", "bed_b", "bath"):
        ha.states.set(f"sensor.{name}", "20" if name != "bath" else "17")
        ha.states.set(f"binary_sensor.{name}", "off")
        ha.states.set(f"binary_sensor.{name}_window", "off")
        ha.states.set(f"climate.{name}", "heat", temperature=5)
    def room(name, kind, primary, links=None):
        result = dict(name=name, room_type=kind, trv=f"climate.{name}",
                      temperature=f"sensor.{name}", occupancy=f"binary_sensor.{name}",
                      window=f"binary_sensor.{name}_window", primary=primary,
                      target=20.0, unoccupied_target=16.0,
                      kp=1.5, ki=0.00015, kd=0.)
        if links is not None:
            result.update(linked_rooms=links, linked_morning_preheat=True,
                          linked_allow_morning_boiler=True, linked_opportunistic=True,
                          linked_house_presence_fallback=True, linked_preheat_minutes=45,
                          linked_preheat_max_minutes=180, linked_extra_minutes_per_degree=3,
                          linked_morning_hold_minutes=90)
        return result
    rooms = [room("bed_a", "bedroom", True),
             room("bed_b", "bedroom", True),
             room("bath", "bathroom", False, ["bed_a", "bed_b"])]
    settings = dict(rooms=rooms, heat_switch="switch.boiler", hot_water_switch="switch.dhw",
                    frost_temperature=8., min_on_seconds=0, min_off_seconds=0,
                    enable_schedule=True, house_occupancy="binary_sensor.house",
                    workday="binary_sensor.workday", outdoor_temperature="sensor.outside")
    entry = types.SimpleNamespace(entry_id="test")
    def make():
        ctrl = mod.HeatingController(ha, entry, settings)
        ctrl.last_transition = time.monotonic() - 7200
        return ctrl
    return ha, settings, rooms, current, make
