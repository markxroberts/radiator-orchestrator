"""No hardware: bounded ownership, recovery and comfort protection tests."""
import importlib
from types import SimpleNamespace
import pytest


@pytest.fixture
def rig(house):
    ha, settings, _, _, make = house
    settings.update(flow_temperature_enabled=True, flow_temperature_entity='number.boiler_selflowtemp')
    ctrl = make()
    ctrl.boiler_requested = True
    ctrl.hot_water_requested = False
    def room(primary=True):
        return SimpleNamespace(demand=True, effective_primary=primary, measured=20.,
            effective_target=20.5, sensor_grace_reason=None, manual_override_target=None,
            target_reason='normal')
    ctrl.rooms = {'a': room(), 'secondary': room(False)}
    for name in ('dhw_active', 'dhw_charging', 'dhw_3wayvalve'):
        ha.states.set('binary_sensor.boiler_'+name, 'off')
    ha.states.set('sensor.boiler_curflowtemp', 60)
    def selected(value, **extra):
        attrs = dict(min=30, max=90, step=1, unit_of_measurement='°C')
        attrs.update(extra)
        ha.states.set('number.boiler_selflowtemp', value, **attrs)
    selected(75)
    async def call(domain, action, data, blocking=True):
        assert domain == 'number' and action == 'set_value'
        ha.services.actions.append(data['value'])
        selected(data['value'])
    ha.services.async_call = call
    return ha, settings, ctrl, ctrl.flow_temperature, room, selected


async def reduce(m):
    await m.evaluate(0)
    await m.evaluate(180)


@pytest.mark.asyncio
async def test_disabled_or_unconfigured_never_writes(rig):
    ha, settings, _, m, _, _ = rig
    settings['flow_temperature_enabled'] = False
    await reduce(m)
    settings['flow_temperature_enabled'] = True
    settings.pop('flow_temperature_entity')
    await reduce(m)
    assert ha.services.actions == []


@pytest.mark.asyncio
async def test_bands_primary_only_and_hysteresis(rig):
    ha, _, ctrl, m, room, _ = rig
    await m.evaluate(0)
    await m.evaluate(179)
    assert not ha.services.actions
    await m.evaluate(180)
    assert ha.services.actions == [65]
    ctrl.rooms.update(b=room(), c=room())
    await m.evaluate(181)
    assert ha.services.actions == [65, 70]
    del ctrl.rooms['c']
    await m.evaluate(182)
    await m.evaluate(780)
    assert ha.services.actions == [65, 70]
    await m.evaluate(781)
    assert ha.services.actions[-1] == 65
    ctrl.rooms.update(c=room(), d=room(), e=room())
    await m.evaluate(782)
    assert ha.services.actions[-1] == 75
    assert not m.record


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['holiday_return_preheat', 'holiday_return_hold', 'morning_room_preheat',
    'evening_bedroom_preheat', 'work_return_preheat', 'linked_morning_hold'])
async def test_comfort_falls_back_immediately(rig, reason):
    ha, _, ctrl, m, _, _ = rig
    await reduce(m)
    ctrl.rooms['a'].target_reason = reason
    await m.evaluate(181)
    assert ha.services.actions == [65, 75]


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['sensor_loss', 'large_deficit', 'no_demand', 'manual', 'flow_loss', 'grace'])
async def test_fallback(rig, condition):
    ha, _, ctrl, m, _, _ = rig
    await reduce(m)
    if condition == 'sensor_loss': ctrl.rooms['a'].measured = None
    if condition == 'large_deficit': ctrl.rooms['a'].measured = 18
    if condition == 'no_demand': ctrl.boiler_requested = False
    if condition == 'manual': ctrl.rooms['a'].manual_override_target = 22
    if condition == 'flow_loss': ha.states.set('sensor.boiler_curflowtemp', 'unavailable')
    if condition == 'grace': ctrl.rooms['a'].sensor_grace_reason = 'lost'
    await m.evaluate(181)
    assert ha.services.actions == [65, 75]


@pytest.mark.asyncio
@pytest.mark.parametrize('value,attrs', [('unavailable', {}), (75, {'unit_of_measurement':'°F'}),
    (75, {'min':70}), (75, {'step':4}), (75, {'max':70}), ('nan', {})])
async def test_invalid_number_or_target_bounds(rig, value, attrs):
    ha, _, _, m, _, selected = rig
    selected(value, **attrs)
    await reduce(m)
    assert not ha.services.actions


@pytest.mark.asyncio
async def test_dhw_pauses_and_defers_disable_restoration(rig):
    ha, settings, _, m, _, _ = rig
    await reduce(m)
    ha.states.set('binary_sensor.boiler_dhw_charging', 'on')
    settings['flow_temperature_enabled'] = False
    await m.evaluate(181)
    assert ha.services.actions == [65]
    ha.states.set('binary_sensor.boiler_dhw_charging', 'off')
    await m.evaluate(182)
    assert ha.services.actions == [65, 75]


@pytest.mark.asyncio
async def test_unknown_dhw_never_reduces(rig):
    ha, _, _, m, _, _ = rig
    ha.states.set('binary_sensor.boiler_dhw_charging', 'unavailable')
    await reduce(m)
    assert not ha.services.actions


@pytest.mark.asyncio
async def test_external_change_relinquishes_without_overwrite(rig):
    ha, _, _, m, _, selected = rig
    await reduce(m)
    selected(72)
    await m.evaluate(181)
    assert m.status == 'waiting_for_boiler_setpoint_to_stabilise'
    await m.evaluate(360)
    assert m.fault is None
    await m.evaluate(362)
    await m.close()
    assert ha.services.actions == [65]
    assert m.fault == 'external_change_relinquished'


@pytest.mark.asyncio
async def test_capture_non75_baseline_and_unload(rig):
    ha, _, _, m, _, selected = rig
    selected(72)
    await reduce(m)
    await m.close()
    await m.evaluate(900)
    assert ha.services.actions == [65, 72]


@pytest.mark.asyncio
async def test_restart_restores_journal_before_recapturing(rig):
    ha, _, ctrl, m, _, _ = rig
    await reduce(m)
    new = type(m)(ctrl)
    new.store = m.store
    await new.initialize()
    await new.evaluate(0)
    assert ha.services.actions == [65, 75]
    await new.evaluate(1)
    assert new.baseline == 75


@pytest.mark.asyncio
async def test_overtemperature_stops_experiment(rig):
    ha, _, _, m, _, _ = rig
    await reduce(m)
    ha.states.set('sensor.boiler_curflowtemp', 86)
    await m.evaluate(181)
    await m.evaluate(999)
    assert ha.services.actions == [65, 75]
    assert m.fault == 'overtemperature_experiment_stopped'


@pytest.mark.asyncio
async def test_write_failure_latches_no_runaway(rig):
    ha, _, _, m, _, _ = rig
    async def fail(*args, **kwargs):
        ha.services.actions.append('failed')
        raise RuntimeError('offline')
    ha.services.async_call = fail
    await reduce(m)
    for n in range(10): await m.evaluate(1000+n*600)
    assert ha.services.actions == ['failed']
    assert m.fault == 'write_or_readback_failed'


@pytest.mark.asyncio
async def test_readback_failure_bounded(rig, monkeypatch):
    ha, _, _, m, _, _ = rig
    module = importlib.import_module('radiator_orchestrator.flow_temperature')
    async def sleep(_): pass
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)
    async def ignore(*args, **kwargs): ha.services.actions.append('ignored')
    ha.services.async_call = ignore
    await reduce(m)
    await m.evaluate(1000)
    assert ha.services.actions == ['ignored']
    assert m.fault == 'write_or_readback_failed'


@pytest.mark.asyncio
async def test_failed_restore_persisted_no_retry_after_restart(rig):
    ha, _, ctrl, m, _, _ = rig
    await reduce(m)
    async def fail(*args, **kwargs):
        ha.services.actions.append('failed')
        raise RuntimeError()
    ha.services.async_call = fail
    await m.close()
    new = type(m)(ctrl)
    new.store = m.store
    await new.initialize()
    await new.evaluate(0)
    await new.evaluate(1000)
    assert ha.services.actions == [65, 'failed']
    assert new.fault == 'restore_failed_manual_action_required'


@pytest.mark.asyncio
async def test_late_write_after_timeout_remains_owned(rig):
    ha, _, _, m, _, selected = rig
    async def fail(*args, **kwargs): raise TimeoutError()
    original = ha.services.async_call
    ha.services.async_call = fail
    await reduce(m)
    assert m.record is not None
    selected(65)  # delayed EMS acknowledgement arrives after failure
    ha.services.async_call = original
    await m.evaluate(999)
    assert ha.services.actions == [75]
    assert m.record is None
    assert m.fault == 'write_or_readback_failed'


@pytest.mark.asyncio
async def test_reassert_flow_request_before_60_second_expiry(rig):
    ha, _, _, m, _, _ = rig
    await reduce(m)
    assert ha.services.actions == [65]
    await m.evaluate(219)
    assert ha.services.actions == [65]
    await m.evaluate(220)
    assert ha.services.actions == [65, 65]
    assert m.refresh_count == 1
    assert m.fault is None


@pytest.mark.asyncio
async def test_expected_60_second_baseline_reversion_is_not_external_change(rig):
    ha, _, _, m, _, selected = rig
    await reduce(m)
    selected(75)  # boiler's timed return, not user intervention
    await m.evaluate(240)
    assert ha.services.actions == [65, 65]
    assert m.expected_reversions == 1
    assert m.fault is None
    assert m.status == 'modulating_renewed'


@pytest.mark.asyncio
async def test_dhw_transient_setpoint_does_not_latch_external_error(rig):
    ha, _, _, m, _, selected = rig
    await reduce(m)
    ha.states.set('binary_sensor.boiler_dhw_charging', 'on')
    selected(85)
    await m.evaluate(220)
    assert m.fault is None
    assert ha.services.actions == [65]
    ha.states.set('binary_sensor.boiler_dhw_charging', 'off')
    await m.evaluate(241)
    assert m.status == 'waiting_for_boiler_setpoint_to_stabilise'
    selected(75)
    await m.evaluate(260)
    assert m.fault is None
    assert ha.services.actions[-1] == 65


@pytest.mark.asyncio
async def test_dhw_startup_does_not_capture_85_as_baseline(rig):
    ha, _, _, m, _, selected = rig
    selected(85)
    ha.states.set('binary_sensor.boiler_dhw_charging', 'on')
    await m.evaluate(0)
    assert m.baseline is None
    assert m.status == 'paused_DHW_before_baseline_capture'
    selected(75)
    ha.states.set('binary_sensor.boiler_dhw_charging', 'off')
    await m.evaluate(1)
    assert m.baseline == 75


@pytest.mark.asyncio
async def test_expired_reduction_already_at_baseline_clears_journal(rig):
    ha, _, ctrl, m, _, selected = rig
    await reduce(m)
    selected(75)
    ctrl.boiler_requested = False
    await m.evaluate(240)
    assert m.record is None
    assert ha.services.actions == [65]
