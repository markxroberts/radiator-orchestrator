"""Opt-in tiny-load cutoff must be comfortable, predictable and fail open."""
import pytest


def setup(house, enabled=True):
    ha, settings, rooms, current, make = house
    settings.update(
        enable_schedule=False,
        low_demand_cutoff_enabled=enabled,
        low_demand_cutoff_c=0.15,
        low_demand_resume_c=0.60,
        low_demand_confirm_seconds=120,
        low_demand_max_calling_rooms=2,
    )
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "18")
        ha.states.set(f"binary_sensor.{name}", "on")
    ha.states.set("sensor.bath", "23")
    ha.states.set("binary_sensor.bath", "off")
    return ha, settings, make()


async def near_target(ha, ctrl):
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "19.95")
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "confirming_low_demand"


@pytest.mark.asyncio
async def test_disabled_by_default_and_does_not_affect_existing_heating(house):
    ha, settings, ctrl = setup(house, enabled=False)
    await ctrl.async_evaluate()
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "19.95")
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested
    assert ha.states.get("switch.boiler").state == "on"
    assert ctrl.low_demand.status == "disabled"


@pytest.mark.asyncio
async def test_negligible_call_stops_after_confirmation_and_restarts_on_deficit(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    assert ha.states.get("switch.boiler").state == "on"
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "suppressed_low_demand"
    assert ctrl.low_demand_raw_request
    assert not ctrl.boiler_requested
    assert ha.states.get("switch.boiler").state == "off"
    # A slight fluctuation must not restart the boiler.
    ha.states.set("sensor.bed_a", "19.65")
    await ctrl.async_evaluate()
    assert not ctrl.boiler_requested
    ha.states.set("sensor.bed_a", "19.35")
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested
    assert ctrl.low_demand.status == "resumed_high_demand"
    assert ha.states.get("switch.boiler").state == "on"


@pytest.mark.asyncio
async def test_never_cut_off_if_a_secondary_room_needs_significant_heat(house):
    ha, settings, ctrl = setup(house)
    await ctrl.async_evaluate()
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "19.95")
    ha.states.set("sensor.bath", "16")
    await ctrl.async_evaluate()
    assert ctrl.low_demand.status == "normal_demand"
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_manual_override_protects_existing_demand(house):
    ha, settings, ctrl = setup(house)
    await ctrl.async_evaluate()
    ctrl.rooms["bed_a"].manual_override_target = 20.0
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "19.95")
    await ctrl.async_evaluate()
    assert ctrl.boiler_requested
    assert ctrl.low_demand.status == "comfort_or_sensor_grace_protected"


@pytest.mark.asyncio
async def test_zigbee_sensor_grace_does_not_trigger_cutoff(house):
    ha, settings, ctrl = setup(house)
    await ctrl.async_evaluate()
    ha.states.set("sensor.bed_a", "unavailable")
    await ctrl.async_evaluate()
    assert ctrl.rooms["bed_a"].sensor_grace_reason
    assert ctrl.low_demand.status == "comfort_or_sensor_grace_protected"
    assert ctrl.boiler_requested


@pytest.mark.asyncio
async def test_holiday_preheat_is_never_suppressed(house):
    ha, settings, ctrl = setup(house)
    settings["enable_schedule"] = True
    settings["holiday_enable"] = "input_boolean.holiday"
    settings["holiday_return"] = "input_datetime.return"
    ha.states.set("input_boolean.holiday", "on")
    ha.states.set("input_datetime.return", "2026-10-12 08:30:00")
    await ctrl.async_evaluate()
    assert ctrl.holiday_return_preheating
    assert ctrl.boiler_requested
    assert ctrl.low_demand.status == "comfort_or_sensor_grace_protected"


@pytest.mark.asyncio
async def test_real_satisfaction_or_all_rooms_off_wins_even_after_cutoff(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    for name in ("bed_a", "bed_b"):
        ctrl.rooms[name].mode = "off"
    await ctrl.async_evaluate()
    assert not ctrl.boiler_requested
    assert ctrl.low_demand.status == "no_primary_demand"
    assert ha.states.get("switch.boiler").state == "off"


@pytest.mark.asyncio
async def test_unavailable_inputs_cannot_establish_low_demand_at_start(house):
    ha, settings, ctrl = setup(house)
    for name in ("bed_a", "bed_b"):
        ha.states.set(f"sensor.{name}", "unavailable")
    await ctrl.async_evaluate()
    assert not ctrl.boiler_requested
    assert not ctrl.low_demand.suppressed


@pytest.mark.asyncio
async def test_summer_or_away_setback_preempts_low_demand_gate(house):
    ha, settings, ctrl = setup(house)
    await near_target(ha, ctrl)
    ctrl.low_demand.low_since -= 121
    await ctrl.async_evaluate()
    ctrl.summer = True
    await ctrl.async_evaluate()
    assert not ctrl.boiler_requested
    assert ctrl.low_demand.status == "no_primary_demand"
