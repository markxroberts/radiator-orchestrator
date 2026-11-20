"""Event-based boiler cycle classifications and firing diagnostics."""
from datetime import datetime, timedelta, timezone
import importlib


def t0():
    return datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)


def rig(house):
    ha, settings, rooms, clock, make = house
    ctrl = make()
    ha.states.set("binary_sensor.boiler_burngas", "off")
    ha.states.set("binary_sensor.boiler_heatingactive", "off")
    ha.states.set("binary_sensor.boiler_dhw_active", "off")
    ha.states.set("sensor.boiler_curflowtemp", "60")
    ha.states.set("sensor.boiler_curburnpow", "0")
    ha.states.set("sensor.boiler_heatingpumpmod", "65")
    cls = importlib.import_module("radiator_orchestrator.ch_cycle_recorder").CHCycleRecorder
    recorder = cls(ctrl)
    recorder.sample(t0())
    return ha, recorder


def test_ch_recognised_despite_ems_ch_lag_and_shutdown_off(house):
    ha, rec = rig(house)
    notices = []
    rec.subscribe(lambda: notices.append("completed"))
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=10))  # CH=off at flame start
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("sensor.boiler_curburnpow", "52")
    ha.states.set("sensor.boiler_curflowtemp", "68")
    rec.sample(t0() + timedelta(seconds=15))
    ha.states.set("sensor.boiler_curburnpow", "40")
    ha.states.set("sensor.boiler_curflowtemp", "76")
    rec.sample(t0() + timedelta(seconds=25))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    ha.states.set("binary_sensor.boiler_heatingactive", "off")
    ha.states.set("sensor.boiler_curburnpow", "0")
    rec.sample(t0() + timedelta(seconds=50))
    assert notices == ["completed"]
    assert rec.last["classification"] == "CH"
    assert rec.last["duration_seconds"] == 40
    assert rec.last["peak_flow_c"] == 76
    assert rec.last["observed_flow_rise_c"] == 16
    assert rec.last["min_observed_burner_power_pct"] == 40
    assert rec.last["max_observed_burner_power_pct"] == 52
    assert rec.last_confirmed_ch == rec.last
    assert rec.confirmed_ch_cycles == 1


def test_uncertain_cycle_does_not_erase_last_confirmed_ch(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=1))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=20))
    assert rec.last["classification"] == "CH"
    previous = rec.last_confirmed_ch
    ha.states.set("binary_sensor.boiler_heatingactive", "off")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=40))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=60))
    assert rec.last["classification"] == "uncertain_CH_DHW"
    assert rec.last_confirmed_ch is previous
    assert rec.uncertain_cycles == 1
    assert rec.last["classification_reason"] == (
        "neither_CH_nor_DHW_active_confirmed_during_firing")


def test_dhw_only_and_mixed_cycles_are_not_confirmed_ch(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_dhw_active", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=10))
    assert rec.last["classification"] == "DHW"
    assert rec.last_confirmed_ch is None
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=20))
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    rec.sample(t0() + timedelta(seconds=24))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=40))
    assert rec.last["classification"] == "mixed_CH_DHW"
    assert rec.last_confirmed_ch is None


def test_missing_dhw_signal_keeps_classification_uncertain(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_dhw_active", "unavailable")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("binary_sensor.boiler_dhw_active", "off")
    rec.sample(t0() + timedelta(seconds=10))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=15))
    assert rec.last["classification"] == "uncertain_CH_DHW"
    assert rec.last["classification_reason"] == "DHW_signals_and_counter_unavailable"


def test_unavailable_flame_aborts_without_fake_cycle(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=10))
    ha.states.set("binary_sensor.boiler_burngas", "unavailable")
    rec.sample(t0() + timedelta(seconds=12))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=13))
    assert rec.completed == 0
    assert rec.last is None


def set_boiler_counters(ha, ch=100, dhw=200, burner=300):
    ha.states.set("sensor.boiler_heatstarts", str(ch))
    ha.states.set("sensor.boiler_dhw_starts", str(dhw))
    ha.states.set("sensor.boiler_burnstarts", str(burner))


def test_dhw_starts_counter_overrides_stuck_off_dhw_flag(house):
    ha, rec = rig(house)
    set_boiler_counters(ha)
    rec.sample(t0() + timedelta(seconds=1))  # baseline while flame OFF
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=10))
    ha.states.set("sensor.boiler_dhw_starts", "201")
    ha.states.set("sensor.boiler_burnstarts", "301")
    rec.sample(t0() + timedelta(seconds=20))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=30))
    assert rec.last["classification"] == "DHW"
    assert rec.last["classification_reason"] == (
        "DHW_start_counter_increment_despite_inactive_DHW_flag")
    assert rec.last["counter_deltas_during_cycle"]["dhw_starts"] == 1
    assert rec.last_confirmed_ch is None


def test_dhw_charging_is_independent_evidence(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_dhw_charging", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=20))
    assert rec.last["classification"] == "DHW"
    assert rec.last["classification_reason"] == "DHW_charging_or_valve_signal_observed"


def test_dhw_valve_is_independent_evidence(house):
    ha, rec = rig(house)
    ha.states.set("binary_sensor.boiler_dhw_3wayvalve", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=20))
    assert rec.last["classification"] == "DHW"


def test_conflicting_dhw_counter_and_ch_activity_never_confirm_ch(house):
    ha, rec = rig(house)
    set_boiler_counters(ha)
    rec.sample(t0() + timedelta(seconds=1))
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("sensor.boiler_heatstarts", "101")
    ha.states.set("sensor.boiler_dhw_starts", "201")
    ha.states.set("sensor.boiler_burnstarts", "302")
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=30))
    assert rec.last["classification"] == "uncertain_CH_DHW"
    assert rec.last["classification_reason"] == "DHW_counter_conflicts_with_CH_evidence"


def test_counter_reset_ignored_in_cycle_classification(house):
    ha, rec = rig(house)
    set_boiler_counters(ha)
    rec.sample(t0() + timedelta(seconds=1))
    ha.states.set("binary_sensor.boiler_heatingactive", "on")
    ha.states.set("binary_sensor.boiler_burngas", "on")
    rec.sample(t0() + timedelta(seconds=5))
    ha.states.set("sensor.boiler_dhw_starts", "1")
    ha.states.set("binary_sensor.boiler_burngas", "off")
    rec.sample(t0() + timedelta(seconds=10))
    assert rec.last["classification"] == "CH"
    assert rec.last["counter_deltas_during_cycle"]["dhw_starts"] is None


def test_performance_normalisation_uses_actual_request_time(house):
    ha, rec = rig(house)
    set_boiler_counters(ha, ch=100, dhw=200, burner=300)
    rec.sample(t0() + timedelta(seconds=1))
    rec.ctrl.boiler_requested = True
    rec.ctrl.boiler_on = True
    rec.observe_controller(1000.0)
    rec.observe_controller(1250.0)
    rec.observe_controller(1490.0)
    rec.ctrl.boiler_requested = False
    rec.observe_controller(1750.0)
    set_boiler_counters(ha, ch=104, dhw=201, burner=305)
    rec.sample(t0() + timedelta(seconds=60))
    p = rec.performance()
    assert p["CH_counter_starts_since_reload"] == 4
    assert p["DHW_counter_starts_since_reload"] == 1
    assert p["heatflow_request_hours_since_reload"] > 0.2
    assert 10 < p["observed_CH_starts_per_demand_hour"] < 20
    assert p["low_demand_cutoff_suppression_events"] == 0
