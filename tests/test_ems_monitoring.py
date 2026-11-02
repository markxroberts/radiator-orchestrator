"""Regression tests for read-only EMS-ESP status and diagnostics."""
import importlib.util
from pathlib import Path
import pytest

# Load the pure monitoring module without importing HA-dependent package __init__.
_spec = importlib.util.spec_from_file_location("ems_monitor_isolated", Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator" / "ems_monitor.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
EMSMonitor, value_from_state = _module.EMSMonitor, _module.value_from_state


class State:
    def __init__(self, state):
        self.state = state


def test_parse_conservative_and_missing():
    assert value_from_state("ems_gas", State("on")) is True
    assert value_from_state("ems_gas", State("OFF")) is False
    assert value_from_state("ems_gas", State("unavailable")) is None
    assert value_from_state("ems_gas", State("hot")) is None
    assert value_from_state("ems_flow_temperature", State("60.12")) == 60.12
    assert value_from_state("ems_burner_power", State("37")) == 37
    assert value_from_state("ems_burner_power", State("nan")) is None
    assert value_from_state("ems_burner_power", State("140")) is None
    assert value_from_state("ems_flow_temperature", None) is None


def test_disabled_monitor_does_not_read_sources():
    m = EMSMonitor({"ems_monitor_enabled": False, "ems_gas": "binary_sensor.gas"})
    assert m.status == "disabled"
    assert m.sources == {}
    assert m.sample(lambda _: (_ for _ in ()).throw(RuntimeError("source read")), 1.0) == []


def test_ems_transition_logging_and_burner_session():
    state = {"binary_sensor.gas": State("off"), "binary_sensor.valve": State("off")}
    m = EMSMonitor({"ems_monitor_enabled": True, "ems_gas": "binary_sensor.gas",
                    "ems_dhw_valve": "binary_sensor.valve"})
    assert m.sample(state.get, 100.0) == []
    assert m.status == "monitoring"
    state["binary_sensor.gas"] = State("on")
    events = m.sample(state.get, 110.0)
    assert events == [{"signal": "ems_gas", "previous": False, "value": True}]
    assert m.burner_starts == 1
    assert m.runtime_seconds(150.0) == 40
    state["binary_sensor.valve"] = State("on")
    assert m.sample(state.get, 155.0) == [{"signal": "ems_dhw_valve", "previous": False, "value": True}]
    state["binary_sensor.gas"] = State("off")
    events = m.sample(state.get, 170.0)
    assert events[0]["duration_seconds"] == 60
    assert m.runtime_seconds(190.0) == 60
    assert m.burner_starts == 1
    assert m.last_burner_duration_seconds == 60


def test_dhw_valve_does_not_imply_charging():
    m = EMSMonitor({"ems_monitor_enabled": True, "ems_dhw_valve": "binary_sensor.valve"})
    m.sample(lambda _: State("on"), 0)
    assert m.values["ems_dhw_valve"] is True
    assert "ems_dhw_charging" not in m.values


@pytest.mark.asyncio
async def test_ems_observations_never_issue_hardware_commands(house):
    ha, settings, rooms, current, make = house
    settings["ems_monitor_enabled"] = True
    settings["ems_flow_temperature"] = "sensor.ems_flow"
    settings["ems_gas"] = "binary_sensor.ems_gas"
    ha.states.set("sensor.ems_flow", "60.1")
    ha.states.set("binary_sensor.ems_gas", "off")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.ems.values["ems_flow_temperature"] == 60.1
    assert ctrl.ems.values["ems_gas"] is False
    prior_calls = len(ha.services.actions)
    ha.states.set("sensor.ems_flow", "82")
    ha.states.set("binary_sensor.ems_gas", "on")
    await ctrl.async_evaluate()
    assert len(ha.services.actions) == prior_calls
    assert ctrl.ems.values["ems_gas"] is True
    assert any(x["type"] == "ems" and x["details"]["signal"] == "ems_gas"
               for x in ctrl.history)


@pytest.mark.asyncio
async def test_gateway_unavailable_keeps_room_control_running(house):
    ha, settings, rooms, current, make = house
    settings.update(ems_monitor_enabled=True, ems_gas="binary_sensor.ems_gas")
    ha.states.set("binary_sensor.ems_gas", "unknown")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.ems.status == "sources_unavailable"
    assert ctrl.ems.values["ems_gas"] is None
    assert isinstance(ctrl.boiler_requested, bool)
    assert isinstance(ctrl.hot_water_requested, bool)
