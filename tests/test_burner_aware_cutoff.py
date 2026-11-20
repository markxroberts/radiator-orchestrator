"""Regression tests for bounded burner-aware low-demand behaviour."""
import pytest
from test_low_demand_gate import setup, near_target


@pytest.mark.asyncio
async def test_active_verified_ch_flame_defers_cutoff_until_flame_stops(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_dhw_active", "off")
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "waiting_for_ch_burner_to_finish"
    assert ctrl.boiler_requested
    assert ha.states.get("switch.boiler").state == "on"
    assert ctrl.low_demand.burner_deferral_events == 1
    ha.states.set("binary_sensor.boiler_burngas", "off")
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "suppressed_low_demand"
    assert not ctrl.boiler_requested
    assert ha.states.get("switch.boiler").state == "off"
    assert ctrl.low_demand.suppression_events == 1


@pytest.mark.asyncio
async def test_burner_wait_cannot_continue_indefinitely(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "waiting_for_ch_burner_to_finish"
    ctrl.low_demand.burner_wait_since -= 181
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "suppressed_low_demand"
    assert not ctrl.boiler_requested
    assert ctrl.low_demand.burner_wait_remaining_seconds is None


@pytest.mark.asyncio
async def test_dhw_combustion_does_not_extend_heating_call(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_dhw_charging", "on")
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "suppressed_low_demand"
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_room_off_stops_demand_despite_active_flame(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "waiting_for_ch_burner_to_finish"
    for name in ("bed_a", "bed_b"):
        ctrl.rooms[name].mode = "off"
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "no_primary_demand"
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_window_open_stops_call_during_burner_wait(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ha.states.set("binary_sensor.boiler_burngas", "on")
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"binary_sensor.{name}_window", "on")
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "no_primary_demand"
    assert not ctrl.boiler_requested


@pytest.mark.asyncio
async def test_suppression_duration_counter_tracks_completed_periods(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    first = ctrl.low_demand.suppressed_since
    assert ctrl.low_demand.suppressed_seconds(first + 91) >= 90
    ctrl.low_demand._clear("test_complete", first + 91)
    assert ctrl.low_demand.suppressed_seconds(first + 100) >= 90
