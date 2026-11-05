"""Home Assistant 2026.11+ native climate properties must keep HeatFlow semantics.

The previous climate API names log deprecation warnings in 2026.11 and will
stop working in 2027.11. Keep all control logic and exposed targets unchanged.
"""
from pathlib import Path
import importlib.util
import sys
import types
import pytest


def _module(monkeypatch, name, **symbols):
    m = types.ModuleType(name)
    m.__dict__.update(symbols)
    monkeypatch.setitem(sys.modules, name, m)
    return m


def _climate(monkeypatch):
    """Load the real climate platform with minimal Home Assistant stand-ins."""
    _module(monkeypatch, "homeassistant.components")
    class ClimateEntity:
        pass
    hvac_mode = types.SimpleNamespace(HEAT="heat", OFF="off")
    _module(monkeypatch, "homeassistant.components.climate",
            ClimateEntity=ClimateEntity, HVACMode=hvac_mode,
            HVACAction=types.SimpleNamespace(OFF="off", HEATING="heating", IDLE="idle"),
            ClimateEntityFeature=types.SimpleNamespace(TARGET_TEMPERATURE=1))
    _module(monkeypatch, "homeassistant.const",
            UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"),
            ATTR_TEMPERATURE="temperature")
    _module(monkeypatch, "radiator_orchestrator.entity",
            HeatFlowEntity=type("HeatFlowEntity", (), {}))

    path = Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator" / "climate.py"
    spec = importlib.util.spec_from_file_location(
        "radiator_orchestrator.climate", path)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, m)
    spec.loader.exec_module(m)
    return m


def test_native_climate_members_replace_deprecated_ones(house, monkeypatch):
    _, _, _, _, make = house
    platform = _climate(monkeypatch)
    entity = platform.RoomClimate(make(), "bed_a")
    assert platform.RoomClimate._attr_native_temperature_unit == "°C"
    assert entity.native_current_temperature is None
    assert entity.native_target_temperature == 0.0
    for legacy_name in ("_attr_temperature_unit", "current_temperature", "target_temperature"):
        assert legacy_name not in platform.RoomClimate.__dict__


@pytest.mark.asyncio
async def test_native_temperature_keeps_external_sensor_and_schedule(house, monkeypatch):
    ha, _, _, _, make = house
    platform = _climate(monkeypatch)
    ha.states.set("sensor.bed_a", "18.3")
    ctrl = make()
    await ctrl.async_evaluate()
    entity = platform.RoomClimate(ctrl, "bed_a")
    assert entity.native_current_temperature == pytest.approx(18.3)
    assert entity.native_target_temperature == ctrl.rooms["bed_a"].effective_target
    assert entity.native_target_temperature != ctrl.rooms["bed_a"].conf["target"]


@pytest.mark.asyncio
async def test_native_target_retains_manual_override(house, monkeypatch):
    ha, _, _, current, make = house
    from datetime import datetime
    from zoneinfo import ZoneInfo
    # Temporary overrides are deliberately ignored during the night
    # setback: test their existing behaviour inside a daytime/evening slot.
    current[0] = datetime(2026, 10, 12, 18, 0, tzinfo=ZoneInfo("Europe/London"))
    platform = _climate(monkeypatch)
    ctrl = make()
    await ctrl.async_evaluate()
    await ctrl.set_target("bed_a", 22.0)
    entity = platform.RoomClimate(ctrl, "bed_a")
    assert entity.native_target_temperature == 22.0
    assert entity.native_current_temperature == ctrl.rooms["bed_a"].measured


def test_native_target_in_manual_mode_is_configured_target(house, monkeypatch):
    ha, settings, _, _, make = house
    platform = _climate(monkeypatch)
    settings["enable_schedule"] = False
    ctrl = make()
    entity = platform.RoomClimate(ctrl, "bed_a")
    assert entity.native_target_temperature == ctrl.rooms["bed_a"].conf["target"]
    ctrl.rooms["bed_a"].conf["target"] = 21.5
    assert entity.native_target_temperature == 21.5
