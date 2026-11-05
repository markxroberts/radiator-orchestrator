"""Pure cycle diagnostics are strictly read-only and report unknown honestly."""
from pathlib import Path
import importlib.util
import sys
import types


def helper():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator" / "cycle_monitor.py"
    spec = importlib.util.spec_from_file_location("cycle_monitor_isolated", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


class State:
    def __init__(self, state, **attributes):
        self.state = state
        self.attributes = attributes


def test_hive_setpoint_not_assumed_to_be_heat_action():
    c = helper()
    idle = State("heat", temperature=24, hvac_action="idle")
    assert c.hive_setpoint(idle) == 24
    assert c.hive_action(idle) == 0
    heating = State("heat", temperature=24, hvac_action="heating")
    assert c.hive_action(heating) == 1
    assert c.hive_action(State("unavailable", hvac_action="heating")) is None
    assert c.hive_action(State("heat", temperature=24)) is None
    assert c.hive_action(State("off", temperature=24)) == 0
    assert c.hive_setpoint(State("heat", temperature=float("nan"))) is None


def test_invalid_sources_never_report_off():
    c = helper()
    assert c.binary_state(None) is None
    assert c.binary_state(State("unavailable")) is None
    assert c.binary_state(State("unknown")) is None
    assert c.binary_state(State("off")) == 0
    assert c.binary_state(State("on")) == 1


def test_default_source_matches_actual_installation_and_options_override():
    c = helper()
    conf = {"heat_switch": "switch.boiler_controller"}
    ems = {"ems_gas": "binary_sensor.boiler_burngas"}
    assert c.cycle_source(conf, ems, "heating_switch") == "switch.boiler_controller"
    assert c.cycle_source(conf, ems, "hive_action") == "climate.boiler_controller_heat"
    assert c.cycle_source(conf, ems, "ems_ch") == "binary_sensor.boiler_heatingactive"
    assert c.cycle_source(conf, ems, "ems_dhw") == "binary_sensor.boiler_dhw_active"
    assert c.cycle_source(conf, ems, "ems_burner") == "binary_sensor.boiler_burngas"
    conf.update(cycle_hive_climate="climate.lounge",
                cycle_ems_ch_entity="binary_sensor.custom_ch",
                cycle_ems_dhw_entity="binary_sensor.custom_dhw")
    assert c.cycle_source(conf, ems, "hive_setpoint") == "climate.lounge"
    assert c.cycle_source(conf, ems, "ems_ch") == "binary_sensor.custom_ch"
    assert "climate.lounge" in c.cycle_source_entities(conf, ems)


def test_cycle_values_are_snapshot_only_with_no_services(house):
    c = helper()
    ha, settings, rooms, now, make = house
    ctrl = make()
    ha.states.set("climate.boiler_controller_heat", "heat",
                  temperature=24.0, hvac_action="heating")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_dhw_active", "off")
    assert c.cycle_value(ctrl, "heatflow_demand") == 0
    assert c.cycle_value(ctrl, "heating_switch") == 0
    assert c.cycle_value(ctrl, "hive_setpoint") == 24.0
    assert c.cycle_value(ctrl, "hive_action") == 1
    assert c.cycle_value(ctrl, "ems_burner") == 1
    assert c.cycle_value(ctrl, "ems_ch") == 1
    assert c.cycle_value(ctrl, "ems_dhw") == 0
    assert ha.services.actions == []


def test_watched_entity_ids_include_all_sources():
    c = helper()
    ids = c.cycle_source_entities(
        {"heat_switch":"switch.boiler_controller"},
        {"ems_gas":"binary_sensor.boiler_burngas"})
    assert ids == {
        "switch.boiler_controller",
        "climate.boiler_controller_heat",
        "binary_sensor.boiler_burngas",
        "binary_sensor.boiler_heatingactive",
        "binary_sensor.boiler_dhw_active",
    }


def test_sensor_entities_are_available_at_setup_before_evaluation(house, monkeypatch):
    ha, settings, rooms, now, make = house
    ctrl = make()
    cm = helper()
    import types
    def module(name, **attributes):
        m = types.ModuleType(name)
        m.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, m)
    module("homeassistant.components")
    module("homeassistant.components.sensor",
           SensorEntity=type("FakeSensor", (), {}),
           SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"),
           SensorDeviceClass=types.SimpleNamespace(TEMPERATURE="temperature"))
    module("homeassistant.helpers.entity",
           EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic"))
    module("radiator_orchestrator.entity",
           HeatFlowEntity=type("FakeEntity", (), {}))
    module("radiator_orchestrator.cycle_monitor",
           CYCLE_FIELDS=cm.CYCLE_FIELDS, cycle_source=cm.cycle_source,
           cycle_value=cm.cycle_value)
    module_path = Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator" / "sensor.py"
    spec = importlib.util.spec_from_file_location("radiator_orchestrator.sensor", module_path)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, m)
    spec.loader.exec_module(m)
    entities = [m.CycleTelemetrySensor(ctrl, key) for key in cm.CYCLE_FIELDS]
    assert len(entities) == 7
    assert entities[0].native_value == 0
    assert entities[0].available
    assert not entities[2].available  # Hive climate source has not been added yet
    assert entities[2].extra_state_attributes["source_entity"] == "climate.boiler_controller_heat"
