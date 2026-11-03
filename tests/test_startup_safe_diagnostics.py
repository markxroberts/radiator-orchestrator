"""Regression: HA may read room diagnostic entities before first evaluation.

The platform is forwarded before async_setup_entry calls ctrl.async_evaluate().
The v0.7.8 work_return_preheating diagnostic had caused AttributeError.
"""
import importlib.util
from pathlib import Path
import sys
import types


def test_heating_reason_available_before_first_evaluation(house, monkeypatch):
    ha, settings, rooms, current, make = house
    ctrl = make()
    assert ctrl.work_preheating is False

    # Isolate the sensor platform using the same lightweight HA stand-ins as
    # the controller tests. This reproduces HA accessing entity properties at
    # registration time rather than merely testing controller evaluation.
    def module(name, **attributes):
        m = types.ModuleType(name)
        m.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    class FakeSensorEntity:
        pass

    class FakeHeatFlowEntity:
        pass

    module("homeassistant.components")
    module(
        "homeassistant.components.sensor",
        SensorEntity=FakeSensorEntity,
        SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"),
        SensorDeviceClass=types.SimpleNamespace(TEMPERATURE="temperature"),
    )
    module(
        "homeassistant.helpers.entity",
        EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic"),
    )
    module("radiator_orchestrator.entity", HeatFlowEntity=FakeHeatFlowEntity)

    path = (Path(__file__).resolve().parents[1] / "custom_components"
            / "radiator_orchestrator" / "sensor.py")
    spec = importlib.util.spec_from_file_location(
        "radiator_orchestrator.sensor", path,
    )
    sensor_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, sensor_module)
    spec.loader.exec_module(sensor_module)

    reasons = [
        sensor_module.RoomReasonSensor(ctrl, name)
        for name in ctrl.rooms
    ]
    assert len(reasons) == len(ctrl.rooms)
    for reason in reasons:
        assert reason.native_value == "temperature unavailable"
        attrs = reason.extra_state_attributes
        assert attrs["work_return_preheating"] is False
        assert attrs["work_return_authorisation"] == "not_in_work_return_window"
        assert attrs["target_reason"] == "not_evaluated"

