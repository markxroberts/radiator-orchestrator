"""Opt-in lease renewal for EMS-ESP selected maximum burner power.

The HT3 selected-max-power command typically expires after five minutes.
This module maintains *only* an explicitly configured cap while CH is
requested. It never writes burner current modulation or burnmaxpower.
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
    except (ValueError, TypeError):
        return None


class SelectedBurnerPowerController:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.store = Store(ctrl.hass, 1, f"radiator_orchestrator_{ctrl.entry.entry_id}_selected_burner_power")
        self.lock = asyncio.Lock()
        self.record = None
        self.baseline = None
        self.actual = None
        self.target = None
        self.status = "disabled"
        self.fault = None
        self.closed = False
        self.recovering = False
        self.last_write = float("-inf")
        self.renew_interval = 240  # safely before observed 300s expiry
        self.renewal_count = 0
        self.expected_reversions = 0
        self._reversion_seen = False
        self.mismatch_since = None

    async def initialize(self):
        try:
            record = await self.store.async_load() or None
            if record and (
                not isinstance(record, dict)
                or record.get("entity") != "number.boiler_selburnpow"
                or any(finite(record.get(k)) is None for k in ("baseline", "target"))
                or not 50 <= record["target"] <= record["baseline"] <= 100
            ):
                raise ValueError("Invalid burner-power ownership journal")
            self.record = record
            self.recovering = bool(record)
        except Exception:
            self.fault = "ownership_journal_unavailable_manual_check_required"
            self.status = self.fault

    def read(self, entity):
        if entity != "number.boiler_selburnpow":
            return None
        state = self.ctrl.hass.states.get(entity)
        if state is None:
            return None
        value = finite(state.state)
        a = state.attributes
        lo, hi, step = (finite(a.get(k)) for k in ("min", "max", "step"))
        if (None in (value, lo, hi, step) or step <= 0 or lo >= hi
                or a.get("unit_of_measurement") != "%"
                or not lo <= value <= hi):
            return None
        return value, lo, hi, step

    def safe_dhw(self):
        ctrl = self.ctrl
        source = cycle_source(ctrl.settings, ctrl.ems.sources, "ems_dhw")
        entities = [source, "binary_sensor.boiler_dhw_charging",
                    "binary_sensor.boiler_dhw_3wayvalve"]
        return (bool(source) and not ctrl.hot_water_requested
                and all(binary_state(ctrl.hass.states.get(e)) == 0 for e in entities))

    async def clear(self):
        await self.store.async_save({})
        self.record = None

    async def write(self, entity, target, expected):
        reading = self.read(entity)
        if reading is None or not self.safe_dhw():
            return False
        value, lo, hi, step = reading
        if abs(value-expected) >= 0.01 or not lo <= target <= hi:
            return False
        if abs((target-lo)/step - round((target-lo)/step)) > 0.001:
            return False
        try:
            await asyncio.wait_for(self.ctrl.hass.services.async_call(
                "number", "set_value", {"entity_id": entity, "value": target},
                blocking=True), timeout=10)
            for _ in range(10):
                new = self.read(entity)
                self.actual = new[0] if new else None
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
        reading = self.read(r["entity"])
        if reading is None or not self.safe_dhw():
            self.status = "restore_pending_unavailable_or_DHW"
            return False
        current = reading[0]
        if abs(current-r["baseline"]) < 0.01:
            if r.get("write_uncertain"):
                self.status = "uncertain_write_waiting_for_late_acknowledgement"
                return False
            await self.clear()
            return True
        if abs(current-r["target"]) > 0.01:
            self.status = "restore_pending_unexpected_setpoint"
            return False
        if r.get("restore_attempted"):
            self.fault = "restore_failed_manual_action_required"
            self.status = self.fault
            return False
        r["restore_attempted"] = True
        await self.store.async_save(r)
        if await self.write(r["entity"], r["baseline"], current):
            await self.clear()
            return True
        self.fault = "restore_failed_manual_action_required"
        self.status = self.fault
        return False

    async def close(self):
        async with self.lock:
            self.closed = True
            await self.restore()

    async def evaluate(self, now):
        async with self.lock:
            if self.closed:
                return
            ctrl = self.ctrl
            enabled = bool(ctrl.settings.get("selected_burner_power_enabled", False))
            entity = ctrl.settings.get("selected_burner_power_entity")
            reading = self.read(entity)
            self.actual = reading[0] if reading else None
            if self.recovering or not enabled or self.fault:
                if await self.restore():
                    self.recovering = False
                self.status = self.fault or ("disabled" if not enabled else "restart_recovery")
                return
            if reading is None:
                self.status = "selected_burner_power_entity_unavailable"
                return
            if self.baseline is None:
                self.baseline = self.actual
            if not 50 <= self.baseline <= 100:
                self.status = "baseline_outside_safe_range"
                return
            desired = float(ctrl.settings.get("selected_burner_power_limit_pct", 90))
            if not 50 <= desired <= self.baseline or desired > reading[2] or desired < reading[1]:
                self.status = "invalid_power_limit_or_baseline"
                return
            # User's 90% cap is for CH requests. Never retain it across
            # unrelated DHW operation / no heating request.
            self.target = desired if ctrl.boiler_requested else self.baseline
            if not self.safe_dhw():
                self.status = "paused_DHW_or_unknown"
                return
            if self.record:
                recorded = self.record["target"]
                if abs(self.actual-recorded) < 0.01:
                    self._reversion_seen = False
                    self.mismatch_since = None
                elif abs(self.actual-self.baseline) < 0.01:
                    if not self._reversion_seen:
                        self.expected_reversions += 1
                    self._reversion_seen = True
                    self.mismatch_since = None
                else:
                    if self.mismatch_since is None:
                        self.mismatch_since = now
                    if now-self.mismatch_since < 180:
                        self.status = "waiting_for_transient_burner_power_override"
                        return
                    self.fault = "external_change_relinquished"
                    await self.clear()
                    self.status = self.fault
                    return
            if self.target == self.baseline:
                if await self.restore():
                    self.status = "baseline"
                return
            if self.record and self.record["target"] == self.target:
                if now-self.last_write < self.renew_interval:
                    self.status = "maintaining_power_limit"
                    return
                self.last_write = now
                if not await self.write(entity, self.target, self.actual):
                    self.fault = "renewal_or_readback_failed"
                    self.record["write_uncertain"] = True
                    await self.store.async_save(self.record)
                    await self.restore()
                    self.status = self.fault
                    return
                self.renewal_count += 1
                self._reversion_seen = False
                self.status = "power_limit_renewed"
                return
            if abs(self.actual-self.target) < 0.01:
                self.status = "power_limit_already_set"
                return
            self.record = {"entity": entity, "baseline": self.baseline,
                           "target": self.target}
            await self.store.async_save(self.record)
            self.last_write = now
            if not await self.write(entity, self.target, self.actual):
                self.fault = "write_or_readback_failed"
                self.record["write_uncertain"] = True
                await self.store.async_save(self.record)
                await self.restore()
                self.status = self.fault
                return
            self.status = "power_limit_active"
