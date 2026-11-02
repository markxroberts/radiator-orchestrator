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

def test_number_and_select_domains_available_in_options_flow():
    from importlib import util
    spec = util.spec_from_file_location(
        "ems_monitor_domain_check",
        Path(__file__).resolve().parents[1] / "custom_components" /
        "radiator_orchestrator" / "ems_monitor.py",
    )
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {"number", "select", "input_number", "input_select"}.issubset(
        set(module.EMS_ENTITY_DOMAINS)
    )
    assert {"ems_heating_pump_mode", "ems_dhw_comfort_mode"}.issubset(
        set(module.EMS_FIELDS)
    )


def test_ems_number_and_numeric_select_states():
    assert value_from_state("ems_selected_flow_temperature", State("56")) == 56.0
    assert value_from_state("ems_burner_power", State("37.5")) == 37.5
    assert value_from_state("ems_selected_flow_temperature", State("Auto")) is None


def test_ems_categorical_select_preserves_mode_and_logs_changes():
    states = {
        "select.pump_mode": State("prop.high"),
        "select.dhw_comfort": State("Hot"),
    }
    m = EMSMonitor({
        "ems_monitor_enabled": True,
        "ems_heating_pump_mode": "select.pump_mode",
        "ems_dhw_comfort_mode": "select.dhw_comfort",
    })
    assert m.sample(states.get, 1.0) == []
    assert m.status == "monitoring"
    assert m.values["ems_heating_pump_mode"] == "prop.high"
    assert m.values["ems_dhw_comfort_mode"] == "Hot"
    states["select.pump_mode"] = State("prop.low")
    events = m.sample(states.get, 2.0)
    assert events == [{
        "signal": "ems_heating_pump_mode",
        "previous": "prop.high",
        "value": "prop.low",
    }]


def test_read_only_select_boolean_is_not_automatically_assumed():
    assert value_from_state("ems_heating_pump", State("prop.high")) is None
    assert value_from_state("ems_gas", State("enabled")) is True
    assert value_from_state("ems_gas", State("disabled")) is False


@pytest.mark.asyncio
async def test_ems_select_sources_never_send_hardware_commands(house):
    ha, settings, rooms, current, make = house
    settings.update(
        ems_monitor_enabled=True,
        ems_heating_pump_mode="select.pump_mode",
        ems_selected_flow_temperature="number.selected_flow",
    )
    ha.states.set("select.pump_mode", "prop.high")
    ha.states.set("number.selected_flow", "55")
    ctrl = make()
    await ctrl.async_evaluate()
    assert ctrl.ems.values["ems_heating_pump_mode"] == "prop.high"
    assert ctrl.ems.values["ems_selected_flow_temperature"] == 55.0
    actions_before = len(ha.services.actions)
    ha.states.set("select.pump_mode", "prop.low")
    ha.states.set("number.selected_flow", "50")
    await ctrl.async_evaluate()
    assert len(ha.services.actions) == actions_before
