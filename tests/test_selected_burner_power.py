"""Selected max burner-power command lease tests (no physical boiler)."""
from types import SimpleNamespace
import pytest


@pytest.fixture
def rig(house):
    ha, settings, _, _, make = house
    settings.update(selected_burner_power_enabled=True,
                    selected_burner_power_entity="number.boiler_selburnpow",
                    selected_burner_power_limit_pct=90)
    ctrl = make()
    ctrl.boiler_requested = True
    ctrl.hot_water_requested = False
    for name in ("dhw_active", "dhw_charging", "dhw_3wayvalve"):
        ha.states.set("binary_sensor.boiler_"+name,"off")
    def selected(value, **overrides):
        attrs = dict(min=0,max=100,step=1,unit_of_measurement="%")
        attrs.update(overrides)
        ha.states.set("number.boiler_selburnpow",value,**attrs)
    selected(100)
    async def call(domain, action, data, blocking=True):
        assert domain=="number" and action=="set_value"
        assert data["entity_id"] == "number.boiler_selburnpow"
        ha.services.actions.append(data["value"])
        selected(data["value"])
    ha.services.async_call = call
    m=ctrl.selected_burner_power
    return ha,settings,ctrl,m,selected


@pytest.mark.asyncio
async def test_initial_power_limit_and_240_second_renewals(rig):
    ha,_,_,m,_=rig
    await m.evaluate(0)
    assert ha.services.actions==[90]
    await m.evaluate(239)
    assert ha.services.actions==[90]
    await m.evaluate(240)
    assert ha.services.actions==[90,90]
    assert m.renewal_count==1


@pytest.mark.asyncio
async def test_five_minute_reversion_to_baseline_is_expected(rig):
    ha,_,_,m,selected=rig
    await m.evaluate(0)
    selected(100)  # HT3 expires after 300s
    await m.evaluate(300)
    assert ha.services.actions==[90,90]
    assert m.expected_reversions==1
    assert m.fault is None


@pytest.mark.asyncio
async def test_dhw_pauses_periodic_power_commands(rig):
    ha,_,_,m,selected=rig
    await m.evaluate(0)
    ha.states.set("binary_sensor.boiler_dhw_charging","on")
    selected(100)
    await m.evaluate(300)
    assert ha.services.actions==[90]
    ha.states.set("binary_sensor.boiler_dhw_charging","off")
    await m.evaluate(320)
    assert ha.services.actions==[90,90]
    assert m.fault is None


@pytest.mark.asyncio
async def test_no_ch_request_restores_baseline_and_stops_renewing(rig):
    ha,_,ctrl,m,_=rig
    await m.evaluate(0)
    ctrl.boiler_requested=False
    await m.evaluate(45)
    assert ha.services.actions==[90,100]
    assert m.record is None
    await m.evaluate(350)
    assert ha.services.actions==[90,100]


@pytest.mark.asyncio
async def test_disable_restores_when_safe_after_dhw(rig):
    ha,settings,_,m,_=rig
    await m.evaluate(0)
    ha.states.set("binary_sensor.boiler_dhw_charging","on")
    settings["selected_burner_power_enabled"]=False
    await m.evaluate(30)
    assert ha.services.actions==[90]
    ha.states.set("binary_sensor.boiler_dhw_charging","off")
    await m.evaluate(60)
    assert ha.services.actions==[90,100]


@pytest.mark.asyncio
async def test_power_setting_opt_in_and_exact_selected_entity(rig):
    ha,settings,_,m,_=rig
    settings["selected_burner_power_enabled"]=False
    await m.evaluate(0)
    assert ha.services.actions==[]
    settings["selected_burner_power_enabled"]=True
    settings["selected_burner_power_entity"]="number.boiler_burnmaxpower"
    await m.evaluate(60)
    assert ha.services.actions==[]
    assert m.status=="selected_burner_power_entity_unavailable"


@pytest.mark.asyncio
async def test_unexpected_power_edit_waits_then_relinquishes(rig):
    ha,_,_,m,selected=rig
    await m.evaluate(0)
    selected(80)
    await m.evaluate(240)
    assert m.status=="waiting_for_transient_burner_power_override"
    await m.evaluate(421)
    assert m.status=="external_change_relinquished"
    assert ha.services.actions==[90]


@pytest.mark.asyncio
async def test_restart_restores_before_new_cap(rig):
    ha,_,ctrl,m,_=rig
    await m.evaluate(0)
    new=type(m)(ctrl)
    new.store=m.store
    await new.initialize()
    assert new.recovering
    await new.evaluate(0)
    assert ha.services.actions==[90,100]
    await new.evaluate(20)
    assert ha.services.actions==[90,100,90]


@pytest.mark.asyncio
async def test_invalid_entities_and_percent_bounds_never_write(rig):
    ha,_,_,m,selected=rig
    selected(100,unit_of_measurement="°C")
    await m.evaluate(0)
    assert ha.services.actions==[]
    assert m.status=="selected_burner_power_entity_unavailable"


@pytest.mark.asyncio
async def test_ch_limit_write_failure_no_unbounded_retry(rig):
    ha,_,_,m,_=rig
    async def fail(*args,**kwargs):
        ha.services.actions.append("failed")
        raise TimeoutError()
    ha.services.async_call=fail
    await m.evaluate(0)
    for i in range(1,5):
        await m.evaluate(250*i)
    assert ha.services.actions==["failed"]
    assert m.fault=="write_or_readback_failed"


@pytest.mark.asyncio
async def test_power_baseline_not_captured_while_dhw_active(rig):
    ha,_,_,m,selected=rig
    ha.states.set("binary_sensor.boiler_dhw_charging","on")
    selected(90)
    await m.evaluate(0)
    assert m.baseline is None
    assert ha.services.actions==[]
    selected(100)
    ha.states.set("binary_sensor.boiler_dhw_charging","off")
    await m.evaluate(20)
    assert m.baseline==100
    assert ha.services.actions==[90]
