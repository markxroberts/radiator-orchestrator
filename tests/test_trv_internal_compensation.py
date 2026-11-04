"""Regression: a warm TRV's local sensor cannot prematurely close a cold room."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from pathlib import Path
import importlib.util
import sys


def load_compensation_helper():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "radiator_orchestrator" / "trv_compensation.py"
    spec = importlib.util.spec_from_file_location("trv_compensation_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_trv_internal_boost_is_above_local_sensor():
    helper = load_compensation_helper()
    setpoint, status = helper.trv_boosted_setpoint(23, 24.4, 1.5)
    assert (setpoint, status) == (26.0, "boosted")


def test_rounding_does_not_erase_opening_margin():
    helper = load_compensation_helper()
    setpoint, status = helper.trv_boosted_setpoint(23, 24.6, 1.5)
    assert setpoint == 26.5
    assert setpoint >= 24.6 + 1.5
    assert status == "boosted"


def test_temperature_missing_invalid_and_high_safety_cap():
    helper = load_compensation_helper()
    assert helper.trv_boosted_setpoint(23, None) == (23, "internal_temperature_unavailable")
    assert helper.trv_boosted_setpoint(23, float("nan")) == (23, "internal_temperature_unavailable")
    assert helper.trv_boosted_setpoint(23, 35)[0] == 30
    assert helper.trv_boosted_setpoint(23, 35)[1] == "max_setpoint_limited"


def test_no_unnecessary_boost_if_pid_is_already_high_enough():
    helper = load_compensation_helper()
    assert helper.trv_boosted_setpoint(25, 22.0) == (25, "pid_setpoint_sufficient")


def test_reading_rejects_missing_unavailable_and_non_numeric():
    helper = load_compensation_helper()
    class State:
        def __init__(self, state, **attrs):
            self.state = state
            self.attributes = attrs
    assert helper.read_trv_temperature(State("heat", current_temperature=24.4)) == 24.4
    assert helper.read_trv_temperature(State("unavailable", current_temperature=24.4)) is None
    assert helper.read_trv_temperature(State("heat", current_temperature="unknown")) is None
    assert helper.read_trv_temperature(State("heat", current_temperature=float("inf"))) is None
    assert helper.read_trv_temperature(State("heat")) is None


def configure_bedroom(house, *, enabled=True):
    ha, settings, rooms, current, make = house
    current[0] = datetime(2026, 11, 4, 21, 0, tzinfo=ZoneInfo("Europe/London"))
    rooms[0].update(
        evening_preheat=True, evening_allow_boiler=True,
        evening_ready_time="22:00:00",
        evening_preheat_minutes=90,
        evening_preheat_max_minutes=180,
        trv_internal_temp_compensation=enabled,
        trv_open_margin_c=1.5,
    )
    ha.states.set("sensor.bed_a", "17")
    ha.states.set("climate.bed_a", "heat", temperature=23, current_temperature=24.4)
    return ha, settings, rooms, current, make


@pytest.mark.asyncio
async def test_evening_preheat_boosts_warm_valve_and_calls_boiler(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.target_reason == "evening_bedroom_preheat"
    assert r.effective_target == 19
    assert r.demand
    assert r.effective_primary
    assert ctrl.boiler_requested
    assert r.trv_internal_temperature == 24.4
    assert r.trv_base_target < 26
    assert r.trv_target == 26.0
    assert r.trv_compensation_applied
    assert r.trv_compensation_status == "boosted"
    calls = [x for x in ha.services.actions if x[:2] == ("climate", "set_temperature")
             and x[2]["entity_id"] == "climate.bed_a"]
    assert calls and calls[-1][2]["temperature"] == 26.0


@pytest.mark.asyncio
async def test_compensation_opt_out_keeps_original_pid_logic(house):
    ha, settings, rooms, current, make = configure_bedroom(house, enabled=False)
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.demand
    assert r.trv_compensation_status == "disabled"
    assert not r.trv_compensation_applied
    assert r.trv_target == r.trv_base_target
    assert r.trv_target < 26


@pytest.mark.asyncio
async def test_at_target_valve_closes_even_when_own_sensor_hot(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    ha.states.set("sensor.bed_a", "19.3")
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert not r.demand
    assert r.trv_target == 5
    assert r.trv_compensation_status == "not_heating"
    assert not r.trv_compensation_applied


@pytest.mark.asyncio
async def test_window_open_prevents_compensated_valve_opening(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    ha.states.set("binary_sensor.bed_a_window", "on")
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.blocked_reason == "window_open"
    assert not r.demand
    assert r.trv_target == 5
    assert not r.trv_compensation_applied


@pytest.mark.asyncio
async def test_away_inhibits_compensation(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    ctrl = make()
    ctrl.away = True
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert not r.demand
    assert r.trv_target == 5
    assert not r.trv_compensation_applied


@pytest.mark.asyncio
async def test_secondary_without_primary_keeps_valve_closed(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    rooms[0]["primary"] = False
    rooms[0]["evening_allow_boiler"] = False
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.demand
    assert not r.effective_primary
    assert not ctrl.boiler_requested
    assert r.trv_target == 5
    assert not r.trv_compensation_applied


@pytest.mark.asyncio
async def test_missing_internal_sensor_falls_back_without_failure(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    ha.states.set("climate.bed_a", "heat", temperature=23)
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert r.trv_compensation_status == "internal_temperature_unavailable"
    assert r.trv_target == r.trv_base_target
    assert r.demand


@pytest.mark.asyncio
async def test_compensation_does_not_override_night_setback(house):
    ha, settings, rooms, current, make = configure_bedroom(house)
    current[0] = datetime(2026, 11, 4, 22, 31, tzinfo=ZoneInfo("Europe/London"))
    ctrl = make()
    await ctrl.async_evaluate()
    r = ctrl.rooms["bed_a"]
    assert not r.demand
    assert r.trv_target == 5
    assert not r.trv_compensation_applied
