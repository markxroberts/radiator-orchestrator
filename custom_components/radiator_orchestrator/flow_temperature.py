"""Explicit opt-in selected CH temperature experiment; never controls demand.

Journal ownership before writes so a restart can restore, not adopt, a reduced
setting. A fault latches until reload; only one bounded restoration is attempted.
"""
from __future__ import annotations
import asyncio
import math
from homeassistant.helpers.storage import Store
from .cycle_monitor import binary_state, cycle_source


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


class FlowTemperatureController:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.store = Store(ctrl.hass, 1, f"radiator_orchestrator_{ctrl.entry.entry_id}_flow")
        self.lock = asyncio.Lock()
        self.record = None
        self.baseline = None
        self.actual = None
        self.requested = None
        self.flow = None
        self.status = "disabled"
        self.fault = None
        self.candidate = None
        self.since = 0
        self.last_write = float('-inf')
        self.closed = False
        self.recovering = False
        # HT3 selected-flow requests typically expire after ~60 seconds.
        # Refresh the same safe target at most every 40 seconds, independent
        # of the 10-minute target-CHANGE cooldown.
        self.refresh_interval = 40
        self.refresh_count = 0
        self.expected_reversions = 0
        self._observed_reversion = False
        self.transient_mismatch_since = None
        self.transient_mismatch_seconds = 180

    async def initialize(self):
        try:
            record = await self.store.async_load() or None
            if record and (not isinstance(record, dict)
                    or not isinstance(record.get('entity'), str)
                    or not record['entity'].startswith('number.')
                    or any(finite(record.get(k)) is None for k in ('baseline', 'selected'))
                    or not 60 <= record['baseline'] <= 85
                    or not 60 <= record['selected'] <= record['baseline']):
                raise ValueError("Invalid ownership journal")
            self.record = record
            self.recovering = bool(self.record)
        except Exception:
            self.fault = 'ownership_journal_unavailable_manual_check_required'
            self.status = self.fault

    def read(self, entity):
        state = self.ctrl.hass.states.get(entity) if entity else None
        if not entity or not entity.startswith('number.') or state is None:
            return None
        value = finite(state.state)
        attrs = state.attributes
        lo, hi, step = (finite(attrs.get(k)) for k in ('min', 'max', 'step'))
        if (None in (value, lo, hi, step) or step <= 0 or lo >= hi
                or not lo <= value <= hi or attrs.get('unit_of_measurement') != '°C'):
            return None
        return value, lo, hi, step

    def safe_dhw(self):
        ctrl = self.ctrl
        entity = cycle_source(ctrl.settings, ctrl.ems.sources, 'ems_dhw')
        # Require independent charging and primary DHW signals to be known OFF.
        # Valve movement also pauses writes, but is not proof of DHW combustion.
        return (not ctrl.hot_water_requested and all(
            binary_state(ctrl.hass.states.get(e)) == 0 for e in
            (entity, 'binary_sensor.boiler_dhw_charging',
             'binary_sensor.boiler_dhw_3wayvalve') if e) and bool(entity))

    async def clear(self):
        await self.store.async_save({})
        self.record = None

    async def write(self, entity, target, expected):
        reading = self.read(entity)
        if reading is None or not self.safe_dhw():
            return False
        value, lo, hi, step = reading
        if abs(value-expected) >= 0.01:
            return False
        if not lo <= target <= hi or abs((target-lo)/step-round((target-lo)/step)) > 0.001:
            return False
        self.requested = target
        try:
            await asyncio.wait_for(self.ctrl.hass.services.async_call(
                'number', 'set_value', {'entity_id': entity, 'value': target},
                blocking=True), timeout=10)
            # EMS may acknowledge asynchronously. Bound the wait, never resend.
            for _ in range(10):
                reading = self.read(entity)
                self.actual = reading[0] if reading else None
                if self.actual is not None and abs(self.actual-target) < 0.01:
                    return True
                await asyncio.sleep(0.5)
        except Exception:
            return False
        return False

    async def restore(self):
        if not self.record:
            return True
        r = self.record
        reading = self.read(r['entity'])
        if reading is None or not self.safe_dhw():
            self.status = 'restore_pending_unavailable_or_DHW'
            return False
        current = reading[0]
        if abs(current-r['baseline']) < 0.01:
            if r.get('write_uncertain'):
                self.status = 'write_failed_waiting_for_possible_late_readback'
                return False
            await self.clear()
            return True
        if all(abs(current-v) >= 0.01 for v in (r['selected'], r.get('previous', r['selected']))):
            # The Highflow can temporarily report 53/85C during DHW work.
            # Don't overwrite or relinquish a known-safe baseline on a
            # single anomalous reading, even if the DHW flags already fell.
            self.status = 'restore_pending_transient_boiler_override'
            return False
        # Persist attempted recovery to prevent restart retry loops.
        if r.get('restore_attempted'):
            self.status = 'restore_failed_manual_action_required'
            self.fault = self.status
            return False
        r['restore_attempted'] = True
        await self.store.async_save(r)
        if await self.write(r['entity'], r['baseline'], current):
            await self.clear()
            return True
        self.fault = 'restore_failed_manual_action_required'
        self.status = self.fault
        return False

    async def close(self):
        async with self.lock:
            self.closed = True
            try:
                await self.restore()
            except Exception:
                self.fault = 'restore_failed_manual_action_required'
                self.status = self.fault

    async def evaluate(self, now, protected=False):
        async with self.lock:
            if self.closed:
                return
            ctrl = self.ctrl
            enabled = ctrl.settings.get('flow_temperature_enabled', False)
            entity = ctrl.settings.get('flow_temperature_entity')
            reading = self.read(entity)
            self.actual = reading[0] if reading else None
            state = ctrl.hass.states.get('sensor.boiler_curflowtemp')
            self.flow = finite(state.state) if state else None
            if self.recovering or not enabled or self.fault:
                if await self.restore():
                    self.recovering = False
                self.status = self.fault or ('disabled' if not enabled else 'restart_recovery')
                return
            if not reading:
                self.candidate = None
                self.status = 'selected_entity_invalid_or_unavailable'
                return
            # Classify expected boiler behaviour before diagnosing an external
            # controller. The 60-second return to our *captured baseline* is
            # an HT3 command expiry, not a third-party edit. Other values
            # (notably 53/85C during DHW) must be stable and unexplained for
            # three minutes before ownership is relinquished. Never write
            # while waiting for this distinction to resolve.
            expected = (
                self.record and any(abs(self.actual-v) < 0.01 for v in (
                    self.record['selected'], self.baseline,
                    self.record.get('previous', self.record['selected'])
                ))
            )
            abnormal = (
                (self.record is not None and not expected)
                or (self.record is None and self.baseline is not None
                    and abs(self.actual-self.baseline) >= 0.01)
            )
            if abnormal:
                if self.transient_mismatch_since is None:
                    self.transient_mismatch_since = now
                if not self.safe_dhw():
                    self.status = 'paused_transient_DHW_setpoint'
                    return
                if now-self.transient_mismatch_since < self.transient_mismatch_seconds:
                    self.status = 'waiting_for_boiler_setpoint_to_stabilise'
                    return
                self.fault = 'external_change_relinquished'
                if self.record:
                    await self.clear()
                self.status = self.fault
                return
            self.transient_mismatch_since = None
            if (self.record and self.baseline is not None
                    and self.record['selected'] < self.baseline
                    and abs(self.actual-self.baseline) < 0.01):
                if not self._observed_reversion:
                    self.expected_reversions += 1
                self._observed_reversion = True
            else:
                self._observed_reversion = False
            if self.baseline is None:
                if not self.safe_dhw():
                    self.status = 'paused_DHW_before_baseline_capture'
                    return
                self.baseline = self.actual
            if not 60 <= self.baseline <= 85:
                self.status = 'baseline_outside_experimental_range'
                return
            rooms = list(ctrl.rooms.values())
            calling = [r for r in rooms if r.demand and r.effective_primary]
            invalid = any(r.measured is None or r.effective_target is None
                or finite(r.measured) is None or finite(r.effective_target) is None
                or r.sensor_grace_reason or 'unavailable' in (getattr(r, 'blocked_reason', None) or '') for r in rooms)
            comfort = protected or any(r.manual_override_target is not None or
                any(word in r.target_reason for word in ('preheat', 'hold', 'early_occupied'))
                for r in rooms)
            large = any(r.effective_target-r.measured >= 1.0 for r in calling) if not invalid else True
            fallback = (invalid or comfort or large or not ctrl.boiler_requested or not calling
                        or self.flow is None or self.flow >= 85)
            target = self.baseline if fallback else min(self.baseline, 65 if len(calling)<=2 else 70 if len(calling)<=4 else self.baseline)
            self.requested = target
            if not self.safe_dhw():
                self.candidate = None
                self.status = 'paused_DHW_or_unknown'
                return
            if self.flow is not None and self.flow >= 85:
                self.fault = 'overtemperature_experiment_stopped'
                await self.restore()
                self.status = self.fault
                return
            if (self.record and target == self.record['selected']
                    and target < self.baseline):
                if now-self.last_write < self.refresh_interval:
                    self.status = 'modulating_waiting_renewal'
                    return
                # Renewal is NOT a target change. Repeat even when readback
                # still shows the target because the boiler command times out
                # independently of Home Assistant's entity state.
                self.last_write = now
                if not await self.write(entity, target, self.actual):
                    self.fault = 'renewal_or_readback_failed'
                    self.record['write_uncertain'] = True
                    await self.store.async_save(self.record)
                    await self.restore()
                    self.status = self.fault
                    return
                self.refresh_count += 1
                self._observed_reversion = False
                self.status = 'modulating_renewed'
                return
            if abs(target-self.actual) < 0.01:
                if target == self.baseline and self.record:
                    # The boiler may already have expired our temporary
                    # setting. Do not leave a stale ownership journal.
                    await self.clear()
                self.candidate = None
                self.status = 'baseline' if target == self.baseline else 'modulating'
                return
            # Increases and baseline fallback are immediate. Reductions require
            # three minutes stable demand and ten minutes since any write.
            if target < self.actual:
                if self.candidate != target:
                    self.candidate, self.since = target, now
                if now-self.since < 180 or now-self.last_write < 600:
                    self.status = 'confirming_lower_demand'
                    return
            self.record = {'entity': entity, 'baseline': self.baseline, 'selected': target, 'previous': self.actual}
            await self.store.async_save(self.record)
            self.last_write = now
            self.candidate = None
            if not await self.write(entity, target, self.record['previous']):
                self.fault = 'write_or_readback_failed'
                self.record['write_uncertain'] = True
                await self.store.async_save(self.record)
                await self.restore()
                self.status = self.fault
                return
            if target == self.baseline:
                await self.clear()
            self.status = 'baseline' if target == self.baseline else 'modulating'
