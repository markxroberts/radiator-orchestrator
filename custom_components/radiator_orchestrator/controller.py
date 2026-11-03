"""Safety-conscious boiler arbitration and per-room PID controller."""
from __future__ import annotations
import asyncio
import logging
from collections import deque
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from homeassistant.util import dt as dt_util
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from .const import *
from .pid import PID, clamp
from .dhw_logic import OccupancyHotWater
from .ems_monitor import EMSMonitor
from .linked_heating import morning_window, evening_window, preheat_allowance, preheat_authorisation

_LOGGER = logging.getLogger(__name__)


def temperature(state):
    try:
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        val = float(state.state)
        return val if -30 < val < 60 else None
    except (ValueError, TypeError):
        return None


@dataclass
class Room:
    conf: dict
    pid: PID
    mode: str = "heat"
    demand: bool = False
    output: float = 0.0
    measured: float | None = None
    effective_target: float = 0.0
    blocked_reason: str | None = None
    last_write: float | None = None
    last_tick: float = field(default_factory=time.monotonic)
    last_occupancy: float | None = None
    schedule_phase: str = "manual"
    target_reason: str = "not_evaluated"
    demand_reason: str = "not_evaluated"
    effective_primary: bool = False
    linked_occupied: tuple[str, ...] = ()
    linked_heating: tuple[str, ...] = ()
    linked_preheat_minutes: int = 0
    linked_preheat_method: str = "not_applicable"
    trv_target: float = 5.0
    morning_preheat_minutes: int = 0
    morning_preheat_method: str = "not_applicable"
    morning_preheat_trigger: str = "not_configured"
    morning_preheat_status: str = "not_configured"
    evening_preheat_minutes: int = 0
    evening_preheat_method: str = "not_applicable"
    evening_preheat_status: str = "not_configured"
    evening_preheat_trigger: str = "not_configured"
    evening_ready_time: str = "20:00"
    manual_override_target: float | None = None
    last_occupancy_state: str | None = None
    holiday_return_authorisation: str = "not_in_return_window"


class HeatingController:
    def __init__(self, hass: HomeAssistant, entry, settings: dict):
        self.hass = hass
        self.entry = entry
        self.settings = settings
        self.ems = EMSMonitor(settings)
        self.rooms = {x["name"]: Room(x, PID(x["kp"],x["ki"],x["kd"])) for x in settings.get(CONF_ROOMS,[]) if x.get("room_type") != "hot_water_only"}
        self.hot_water_rooms = [x for x in settings.get(CONF_ROOMS, []) if x.get("room_type") == "hot_water_only"]
        self.learning = {}
        self.history = deque(maxlen=300)
        self._diagnostic_snapshot = None
        self._history_dirty = False
        self._last_history_save = 0.0
        self.history_store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_diagnostics")
        self.last_observation = {}
        self.preheat_minutes = 0
        self.preheat_source = "fixed"
        self.house_absence_started = None
        self.raw_house_present = True
        self.hot_water_occupancy_requested = False
        self._dhw = OccupancyHotWater()
        self.hot_water_requested = False
        self.hot_water_relay_on = None
        self.hot_water_reason = "not_yet_evaluated"
        self.hot_water_decision = None
        self.away = False
        self.summer = False
        self.hot_water = False
        self.calibration_paused = False
        self.house_present = True
        self.preheating = False
        self.morning_preheating = False
        self.evening_preheating = False
        self.holiday_active = False
        self.holiday_return_preheating = False
        self.holiday_return_holding = False
        self.holiday_return_status = "not_configured"
        self.holiday_return_at = None
        self.boiler_requested = False
        self.boiler_on = False
        self.last_transition = time.monotonic() - 7200
        self.lock = asyncio.Lock()
        self.listeners = []
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")

    async def async_initialize(self):
        saved = await self.store.async_load() or {}
        history_data = await self.history_store.async_load() or {}
        self.history.extend(history_data.get("events", [])[-300:])
        self.learning = saved.get("learning", {})
        self.away = bool(saved.get("away",False))
        self.summer = bool(saved.get("summer",False))
        self.hot_water = bool(saved.get("hot_water",False))
        for name, room in self.rooms.items():
            previous = saved.get("rooms",{}).get(name,{})
            room.conf["target"] = float(previous.get("target",room.conf["target"]))
            room.mode = previous.get("mode","heat")
        current = self.hass.states.get(self.settings[CONF_HEAT_SWITCH])
        self.boiler_on = current is not None and current.state == STATE_ON
        # Controller takes over existing relay state; enforce min on/off interval conservatively.
        self.last_transition = time.monotonic()

    async def save(self):
        await self.store.async_save({"away":self.away,"summer":self.summer,"hot_water":self.hot_water,
            "rooms":{n:{"target":r.conf["target"],"mode":r.mode} for n,r in self.rooms.items()},
            "learning": self.learning})

    def _history_event(self, kind, message, *, room=None, details=None):
        """Record a bounded, non-periodic diagnostic event; no arbitrary entity IDs."""
        event = {"time": dt_util.utcnow().isoformat(), "type": kind,
                 "message": message}
        if room is not None:
            event["room"] = room
        if details:
            event["details"] = details
        self.history.append(event)
        self._history_dirty = True

    def _record_changes(self):
        """Only emit events on meaningful state transitions, never on each tick."""
        snapshot = {
            "house_present": self.house_present,
            "preheating": self.preheating,
            "morning_preheating": self.morning_preheating,
            "evening_preheating": self.evening_preheating,
            "holiday_active": self.holiday_active,
            "holiday_return_preheating": self.holiday_return_preheating,
            "holiday_return_holding": self.holiday_return_holding,
            "holiday_return_status": self.holiday_return_status,
            "boiler_requested": self.boiler_requested,
            "boiler_requesting_rooms": tuple(name for name, room in self.rooms.items()
                                              if room.demand and room.effective_primary),
            "boiler_on": self.boiler_on,
            "occupancy_dhw": self.hot_water_occupancy_requested,
            "hot_water_requested": self.hot_water_requested,
            "hot_water_relay_on": self.hot_water_relay_on,
            "hot_water_reason": self.hot_water_reason,
            "hot_water_rooms_requesting": tuple(self.hot_water_decision.requesting_rooms) if self.hot_water_decision else (),
            "hot_water_rooms_timed_out": tuple(self.hot_water_decision.timed_out_rooms) if self.hot_water_decision else (),
            "calibration_paused": self.calibration_paused,
            "preheat_source": self.preheat_source,
        }
        for name, room in self.rooms.items():
            snapshot[f"room:{name}"] = (
                room.blocked_reason or ("requesting heat" if room.demand else "target satisfied"),
                room.schedule_phase,
                room.target_reason,
                room.demand_reason,
                room.linked_occupied,
                room.linked_heating,
                room.effective_primary,
                room.morning_preheat_status,
                room.morning_preheat_trigger,
                room.evening_preheat_status,
                room.evening_preheat_trigger,
                room.holiday_return_authorisation,
                room.manual_override_target,
                round(room.effective_target, 2),
                room.demand,
            )
        previous = self._diagnostic_snapshot
        self._diagnostic_snapshot = snapshot
        if previous is None:
            self._history_event("startup", "Controller diagnostic tracking started",
                                details={"rooms": len(self.rooms)})
            return
        for key, value in snapshot.items():
            if previous.get(key) == value:
                continue
            if key.startswith("room:"):
                name = key[5:]
                room = self.rooms[name]
                self._history_event("room", f"{name}: {value[0]} ({value[1]})",
                    room=name, details={"temperature_c": room.measured,
                    "target_c": round(room.effective_target, 2),
                    "primary": bool(room.conf.get("primary", True)),
                    "effective_primary": room.effective_primary,
                    "target_reason": room.target_reason,
                    "demand_reason": room.demand_reason,
                    "linked_occupied": list(room.linked_occupied),
                    "linked_heating": list(room.linked_heating),
                    "morning_preheat_status": room.morning_preheat_status,
                    "morning_preheat_trigger": room.morning_preheat_trigger,
                    "evening_preheat_status": room.evening_preheat_status,
                    "evening_preheat_trigger": room.evening_preheat_trigger,
                    "evening_preheat_lead_minutes": room.evening_preheat_minutes,
                    "holiday_return_authorisation": room.holiday_return_authorisation,
                    "temporary_override_c": room.manual_override_target})
            else:
                details = {"previous": previous.get(key)}
                if key.startswith("boiler"):
                    details.update({"calling_rooms": [name for name, room in self.rooms.items()
                                   if room.demand and room.effective_primary],
                                    "minimum_on_s": int(self.settings.get(CONF_MIN_ON, DEFAULT_MIN_ON)),
                                    "minimum_off_s": int(self.settings.get(CONF_MIN_OFF, DEFAULT_MIN_OFF)),
                                    "relay_confirmed_on": self.boiler_on})
                if key.startswith("hot_water") or key == "occupancy_dhw":
                    details.update({"manual": self.hot_water,
                                    "occupancy_enabled": bool(self.settings.get("occupancy_hot_water", False)),
                                    "active_rooms": list(self.hot_water_decision.active_rooms) if self.hot_water_decision else [],
                                    "qualified_rooms": list(self.hot_water_decision.qualified_rooms) if self.hot_water_decision else [],
                                    "requesting_rooms": list(self.hot_water_decision.requesting_rooms) if self.hot_water_decision else [],
                                    "timed_out_rooms": list(self.hot_water_decision.timed_out_rooms) if self.hot_water_decision else [],
                                    "hot_water_switch": self.settings.get(CONF_HOT_WATER_SWITCH)})
                self._history_event("controller", f"{key.replace('_', ' ')}: {value}",
                                    details=details)

    async def _save_history_if_due(self, now):
        if self._history_dirty and now - self._last_history_save >= 60:
            await self.history_store.async_save({"events": list(self.history)})
            self._history_dirty = False
            self._last_history_save = now

    async def async_flush_history(self):
        if self._history_dirty:
            await self.history_store.async_save({"events": list(self.history)})
            self._history_dirty = False

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def publish(self):
        for callback in tuple(self.listeners):
            callback()

    async def async_event(self, event):
        await self.async_evaluate()

    async def async_tick(self, _):
        await self.async_evaluate()

    async def set_mode(self, name, mode):
        self.rooms[name].mode = mode
        self.rooms[name].pid.reset()
        await self.save()
        await self.async_evaluate()

    async def set_target(self, name, target):
        room = self.rooms[name]
        target = clamp(float(target), 5, 30)
        if self.settings.get("enable_schedule", False):
            # A climate-card adjustment is temporary. Do not change the
            # scheduled helpers or the room's persisted manual target.
            room.manual_override_target = target
            self._history_event("override", f"{name}: temporary target {target:.1f} °C",
                                room=name, details={"temperature_c": target})
        else:
            room.conf["target"] = target
            await self.save()
        await self.async_evaluate()

    async def set_global(self, key, value):
        setattr(self,key,bool(value))
        await self.save()
        await self.async_evaluate()

    def _helper(self, key, default):
        entity = self.settings.get(key)
        state = self.hass.states.get(entity) if entity else None
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _minutes(self, key, default):
        raw = str(self._helper(key, default))
        try:
            h, m, *_ = (int(v) for v in raw.split(":"))
            if not 0 <= h <= 23 or not 0 <= m <= 59:
                return self._minutes_default(default)
            return h * 60 + m
        except (TypeError, ValueError):
            return self._minutes_default(default)

    @staticmethod
    def _minutes_default(value):
        h,m = map(int,value.split(":"))
        return h*60+m

    def _enabled(self, entity):
        state = self.hass.states.get(entity) if entity else None
        return state is not None and state.state == STATE_ON

    def _presence(self):
        """Person entities use 'home', not 'on'; guest overrides win."""
        if self._enabled(self.settings.get("guest_override")):
            return True
        configured = False
        present = False
        entity = self.settings.get("house_occupancy")
        if entity:
            configured = True
            present |= self._enabled(entity)
        for person in self.settings.get("people", []):
            configured = True
            state = self.hass.states.get(person)
            present |= state is not None and state.state == "home"
        # Without configured occupancy tracking, preserve v0.2.2 behaviour.
        return present if configured else True

    def _holiday_return_room_authorisation(self, room):
        """Return (allowed, reason) without gating the empty house.

        Only *explicitly selected* guest bedrooms require their own room
        occupancy to preheat on return. A bathroom with bedroom links follows
        its linked bedrooms: normal bedrooms qualify automatically, guest
        bedrooms only if their room occupancy is ON. Unlinked bathrooms and
        all living spaces are never blocked by occupancy on holiday return.
        """
        guests = set(self.settings.get("holiday_guest_bedrooms") or [])
        conf = room.conf
        name = conf["name"]
        if conf.get("room_type") == "bedroom" and name in guests:
            entity = conf.get("occupancy")
            if not entity:
                return False, "guest_occupancy_not_configured"
            state = self.hass.states.get(entity)
            if state is None or state.state not in (STATE_ON, STATE_OFF):
                return False, "guest_occupancy_unavailable"
            return (True, "guest_occupied") if state.state == STATE_ON else (False, "guest_unoccupied")
        if conf.get("room_type") == "bathroom" and conf.get("linked_rooms"):
            linked = [self.rooms[n] for n in conf["linked_rooms"]
                      if n in self.rooms and self.rooms[n].conf.get("room_type") == "bedroom"]
            if linked:
                if any(self._holiday_return_room_authorisation(r)[0] for r in linked):
                    return True, "linked_bedroom_qualifies"
                return False, "linked_guest_bedrooms_unoccupied"
        return True, "holiday_return_unrestricted"

    def _return_comfort_target(self, room):
        """Occupied comfort target for return, regardless of weekday daytime setback.

        Without scheduling, preserve the room's configured manual target.
        """
        if not self.settings.get("enable_schedule", False):
            return clamp(float(room.conf["target"]), 5, 30)
        from .config_flow import SCHEDULE_TARGETS
        kind = room.conf.get("room_type", "living")
        key = {"bedroom": "bedroom_target", "bathroom": "bathroom_evening_target"}.get(
            kind, "evening_target"
        )
        try:
            return clamp(float(self._helper(key, SCHEDULE_TARGETS[key])), 5, 30)
        except (ValueError, TypeError):
            return float(SCHEDULE_TARGETS[key])

    def _preheat_minutes(self):
        temp = temperature(self.hass.states.get(self.settings.get("outdoor_temperature")))
        base = int(self.settings.get("preheat_base_minutes", 90))
        extra = float(self.settings.get("preheat_extra_minutes_per_degree", 8))
        reference = float(self.settings.get("preheat_reference_c", 15))
        allowance = base + max(0, reference - temp) * extra if temp is not None else base
        self.preheat_source = "outdoor_fallback"
        if self.settings.get("adaptive_preheat") and temp is not None:
            estimates = []
            for name, room in self.rooms.items():
                record = self.learning.get(name, {})
                if (not room.conf.get("primary") or record.get("samples", 0) < 3
                        or (self._enabled(self.settings.get("holiday_enable"))
                            and not self._holiday_return_room_authorisation(room)[0])):
                    continue
                # Ignore rates learned far from the current outside conditions.
                if abs(temp - record.get("outside", temp)) > 12:
                    continue
                rate = max(0.05, float(record.get("rate", 0)))
                current = temperature(self.hass.states.get(room.conf.get("temperature")))
                if current is not None:
                    estimates.append(max(0, (self._return_comfort_target(room) - current) / rate * 60))
            if estimates:
                allowance = max(estimates) + int(self.settings.get("preheat_target_margin_minutes", 15))
                self.preheat_source = "learned"
        self.preheat_minutes = max(0, min(int(self.settings.get("max_preheat_minutes", 360)), int(allowance)))
        return self.preheat_minutes

    def _occupied_with_delay(self, now):
        raw = self._presence()
        self.raw_house_present = raw
        if raw:
            self.house_absence_started = None
            return True
        if self.house_absence_started is None:
            self.house_absence_started = now
        delay = float(self.settings.get("absence_delay_minutes", 10)) * 60
        return (now - self.house_absence_started) < delay

    def _observe_heating(self, room, now, outside):
        """EMA of measured warming rate; only valid when boiler genuinely on.

        Ignore pauses, radiator shutdown, open windows and external-sensor gaps.
        Heating rates are conservative, clamped and require three samples.
        """
        name = room.conf["name"]
        valid = (room.measured is not None and outside is not None
                 and self.boiler_on and room.demand and not room.blocked_reason
                 and (room.effective_primary or self.boiler_requested)
                 and not self.calibration_paused and not self.holiday_active
                 and not self.away and not self.summer)
        if not valid:
            self.last_observation.pop(name, None)
            return
        prior = self.last_observation.get(name)
        if prior is None:
            self.last_observation[name] = (now, room.measured)
            return
        elapsed = now - prior[0]
        if elapsed < 240:
            return
        self.last_observation[name] = (now, room.measured)
        delta = room.measured - prior[1]
        if not (elapsed <= 1200 and 0.05 <= delta <= 1.5):
            return
        rate = 3600 * delta / elapsed
        if not 0.1 <= rate <= 8:
            return
        old = self.learning.get(name, {})
        self.learning[name] = {
            "rate": round((0.8 * old["rate"] + 0.2 * rate) if "rate" in old else rate, 3),
            "outside": round((0.8 * old["outside"] + 0.2 * outside) if "outside" in old else outside, 2),
            "samples": min(5000, int(old.get("samples", 0)) + 1),
        }

    def _holiday_window(self):
        """Return (on_holiday, warming_before_return, holding_for_arrival).

        input_datetime contains local wall-clock date/time. Compare timestamps to
        remain accurate over daylight-saving changes; never infer a return date
        from a time-only helper.
        """
        self.holiday_return_at = None
        if not self._enabled(self.settings.get("holiday_enable")):
            self.holiday_return_status = "holiday_switch_off"
            return False, False, False
        entity = self.settings.get("holiday_return")
        state = self.hass.states.get(entity) if entity else None
        if state is None or state.state in ("unknown", "unavailable"):
            self.holiday_return_status = "return_datetime_missing"
            return True, False, False
        try:
            value = datetime.fromisoformat(state.state)
            # Reject time-only and otherwise incomplete values.
            if value.tzinfo is None:
                value = value.replace(tzinfo=dt_util.get_time_zone(self.hass.config.time_zone))
            self.holiday_return_at = value.isoformat()
            seconds_until = value.timestamp() - dt_util.now().timestamp()
            before = seconds_until > 0
            allowance_seconds = self._preheat_minutes() * 60
            hold_seconds = max(0, int(self.settings.get("holiday_return_hold_minutes", 120))) * 60
            preheat = before and seconds_until <= allowance_seconds
            holding = not before and -seconds_until < hold_seconds
            if self.summer:
                self.holiday_return_status = "summer_mode_inhibits_return_heating"
                return before, False, False
            self.holiday_return_status = (
                "preheating_before_return" if preheat else
                "holding_for_arrival" if holding else
                "holiday_setback" if before else "return_window_ended"
            )
            return before, preheat, holding
        except (ValueError, TypeError, OverflowError):
            _LOGGER.warning("Holiday return must be an input_datetime with date and time")
            self.holiday_return_status = "return_datetime_invalid"
            return True, False, False

    def _is_workday(self):
        entity = self.settings.get("workday")
        if entity:
            state = self.hass.states.get(entity)
            # Missing Workday data must not be treated as a working day.
            return state is not None and state.state == STATE_ON
        return dt_util.now().weekday() < 5

    def _scheduled_target(self, room):
        """Returns (target, phase, nighttime) using HA-local time."""
        if not self.settings.get("enable_schedule", False):
            return float(room.conf["target"]), "manual", False
        from .config_flow import SCHEDULE_TIMES, SCHEDULE_TARGETS
        minute_now = dt_util.now().hour * 60 + dt_util.now().minute
        weekend = not self._is_workday() or self._enabled(self.settings.get("holiday_at_home"))
        timepoint = lambda k: self._minutes(k, SCHEDULE_TIMES[k])
        early = str(self._helper("early_start_enable", "off")) == STATE_ON and not self._enabled(self.settings.get("holiday_at_home"))
        start = timepoint("early_start_time") if early else (timepoint("weekend_rise") if weekend else timepoint("weekday_morning"))
        bedtime = timepoint("weekend_bed") if weekend else timepoint("weekday_bed")
        if minute_now < start or minute_now >= bedtime:
            return float(self.settings.get("night_frost_target", 8.0)), "night", True
        if weekend:
            phase = "morning" if minute_now < timepoint("weekday_evening") else "evening"
        elif minute_now < timepoint("weekday_work"):
            phase = "morning"
        elif minute_now < timepoint("weekday_evening"):
            phase = "day"
        else:
            phase = "evening"
        if getattr(self, "work_preheating", False) and phase == "day":
            phase = "evening"
        kind = room.conf.get("room_type", "living")
        if kind == "bathroom":
            key = {"morning":"bathroom_morning_target", "day":"bathroom_day_target", "evening":"bathroom_evening_target"}[phase]
        elif kind == "bedroom":
            key = "day_bedroom_target" if phase == "day" else "bedroom_target"
        else:
            # A living room occupied during the workday must not inherit the
            # unoccupied setback: occupancy arbitration happens later.
            key = "morning_target" if phase == "morning" else "evening_target"
        try:
            target = float(self._helper(key, SCHEDULE_TARGETS[key]))
        except (TypeError, ValueError):
            target = SCHEDULE_TARGETS[key]
        return clamp(target, 5, 30), phase, False

    def _rising_minute(self):
        """Use the same Workday, weekend and early-start helpers as schedules."""
        from .config_flow import SCHEDULE_TIMES
        at_home = self._enabled(self.settings.get("holiday_at_home"))
        weekend = not self._is_workday() or at_home
        early = self._enabled(self.settings.get("early_start_enable")) and not at_home
        key = "early_start_time" if early else ("weekend_rise" if weekend else "weekday_morning")
        return self._minutes(key, SCHEDULE_TIMES[key])

    def _early_occupancy_window(self, minute_now):
        """Morning-only occupancy exception, not a general night-time override.

        A person using a room early in the morning may heat that room before
        the scheduled rising time. The earliest allowed clock time is
        configurable; late-night activity must never defeat bedtime.
        """
        if not self.settings.get("enable_schedule", False):
            return False
        from .config_flow import SCHEDULE_TIMES
        earliest = self._minutes("early_occupancy_from", SCHEDULE_TIMES.get("early_occupancy_from", "04:00"))
        rising = self._rising_minute()
        # Both are morning wall-clock times. Do not wrap into the previous
        # evening if the configured earliest time is after rising.
        return earliest <= minute_now < rising

    def _linked_context(self, room, minute_now, outside):
        """Compute linked bedroom presence and scheduled morning preparation.

        A bedroom PIR is not a reliable sleeper detector. House presence can
        therefore authorise scheduled morning warming when explicitly enabled.
        Links are never a path around summer/holiday/window safety gates.
        """
        conf = room.conf
        names = [n for n in conf.get("linked_rooms", []) if n in self.rooms
                 and self.rooms[n].conf.get("room_type") == "bedroom" and n != conf["name"]]
        if not names:
            return False, False, 0, "no_valid_links"
        room.linked_occupied = tuple(n for n in names if
            self._enabled(self.rooms[n].conf.get("occupancy")))
        if not self.settings.get("enable_schedule", False):
            return False, False, 0, "schedule_disabled"
        # Scheduled preheating must not depend on bedroom motion sensors.
        # An input_boolean in a linked bedroom is an explicit request to
        # preheat; while any such helper is configured, a false value must not
        # be bypassed by a generic house-presence fallback.
        linked_booleans = [self.rooms[n].conf.get("occupancy") for n in names
                           if str(self.rooms[n].conf.get("occupancy", "")).startswith("input_boolean.")]
        linked_active = bool(room.linked_occupied)
        local_boolean = str(conf.get("occupancy", "")).startswith("input_boolean.")
        if local_boolean:
            authorised = self._enabled(conf.get("occupancy"))
        else:
            authorised = linked_active or (not linked_booleans and bool(
                conf.get("linked_house_presence_fallback", True)))
        authorised &= self.house_present and not self.away and not self.holiday_active and not self.summer
        target = self._bathroom_morning_target()
        allowance, method = preheat_allowance(conf, room.measured, target, outside,
            self.learning.get(conf["name"]), float(self.settings.get("preheat_reference_c", 15)))
        room.linked_preheat_minutes = allowance
        room.linked_preheat_method = method
        window = morning_window(minute_now, self._rising_minute(), allowance,
                                int(conf.get("linked_morning_hold_minutes", 90)))
        preparatory = authorised and bool(conf.get("linked_morning_preheat", False)) and window.preheating
        morning_hold = authorised and bool(conf.get("linked_morning_preheat", False)) and window.morning_hold
        return preparatory, morning_hold, allowance, method

    def _morning_target(self, room):
        """Morning setpoint for this type of room, from existing HA helpers."""
        from .config_flow import SCHEDULE_TARGETS
        kind = room.conf.get("room_type", "living")
        key = {"bathroom": "bathroom_morning_target",
               "bedroom": "bedroom_target"}.get(kind, "morning_target")
        try:
            return clamp(float(self._helper(key, SCHEDULE_TARGETS[key])), 5, 30)
        except (TypeError, ValueError):
            return SCHEDULE_TARGETS[key]

    def _room_morning_context(self, room, minute_now, outside):
        """Evaluate a room's selected morning schedule and occupancy trigger."""
        conf = room.conf
        if not conf.get("morning_preheat", False):
            return False, False
        if not self.settings.get("enable_schedule", False):
            room.morning_preheat_status = "schedule_disabled"
            return False, False
        if self.away or self.holiday_active or self.summer:
            room.morning_preheat_status = "heating_inhibited"
            return False, False
        entity = conf.get("occupancy")
        sensor = self.hass.states.get(entity) if entity else None
        state = sensor.state if sensor else None
        authorised, trigger = preheat_authorisation(
            entity, state, self.house_present,
            bool(conf.get("morning_house_presence_fallback", True)))
        room.morning_preheat_trigger = trigger

        target = self._morning_target(room)
        estimated_conf = {
            "linked_preheat_minutes": conf.get("morning_preheat_minutes", 45),
            "linked_preheat_max_minutes": conf.get("morning_preheat_max_minutes", 180),
            "linked_extra_minutes_per_degree": conf.get("morning_extra_minutes_per_degree", 3),
        }
        allowance, method = preheat_allowance(
            estimated_conf, room.measured, target, outside,
            self.learning.get(conf["name"]),
            float(self.settings.get("preheat_reference_c", 15)),
        )
        room.morning_preheat_minutes = allowance
        room.morning_preheat_method = method
        window = morning_window(minute_now, self._rising_minute(), allowance,
                                int(conf.get("morning_hold_minutes", 90)))
        if not authorised:
            room.morning_preheat_status = "not_authorised"
        elif window.preheating:
            room.morning_preheat_status = "active_preheat"
        elif window.morning_hold:
            room.morning_preheat_status = "active_morning_hold"
        else:
            room.morning_preheat_status = "outside_morning_window"
        return authorised and window.preheating, authorised and window.morning_hold


    @staticmethod
    def _clock_minute(raw: str, default: int) -> int:
        """Parse an HA time-selector value without requiring a helper."""
        try:
            hour, minute, *_ = (int(n) for n in str(raw).split(":"))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour * 60 + minute
        except (TypeError, ValueError):
            pass
        return default

    def _room_evening_context(self, room, minute_now, outside, *, holiday_return_active):
        """Optional bedroom comfort before evening use.

        Regular selected bedrooms preheat while the house is occupied even
        with their local occupancy OFF. Selected guest bedrooms instead require
        their own occupancy entity ON (input_boolean is recommended).
        """
        conf = room.conf
        if conf.get("room_type") != "bedroom" or not conf.get("evening_preheat", False):
            return False, False
        if not self.settings.get("enable_schedule", False):
            room.evening_preheat_status = "schedule_disabled"
            return False, False
        if self.away or self.holiday_active or self.summer or holiday_return_active:
            room.evening_preheat_status = "heating_inhibited"
            return False, False
        if not self.house_present:
            room.evening_preheat_status = "house_absent"
            return False, False
        if room.mode == "off":
            room.evening_preheat_status = "room_off"
            return False, False
        window_entity = conf.get("window")
        if window_entity:
            window_state = self.hass.states.get(window_entity)
            if window_state is None or window_state.state not in (STATE_ON, STATE_OFF):
                room.evening_preheat_status = "window_unavailable"
                return False, False
            if window_state.state == STATE_ON:
                room.evening_preheat_status = "window_open"
                return False, False
        is_guest = conf["name"] in self.settings.get("holiday_guest_bedrooms", [])
        guest_restriction = bool(conf.get("evening_guest_requires_occupancy", True))
        if is_guest and guest_restriction:
            entity = conf.get("occupancy")
            state = self.hass.states.get(entity) if entity else None
            if not entity:
                room.evening_preheat_trigger = "guest_occupancy_not_configured"
                room.evening_preheat_status = "guest_occupancy_not_configured"
                return False, False
            if state is None or state.state not in (STATE_ON, STATE_OFF):
                room.evening_preheat_trigger = "guest_occupancy_unavailable"
                room.evening_preheat_status = "guest_occupancy_unavailable"
                return False, False
            if state.state != STATE_ON:
                room.evening_preheat_trigger = "guest_unoccupied"
                room.evening_preheat_status = "guest_unoccupied"
                return False, False
            room.evening_preheat_trigger = "guest_room_occupied"
        else:
            room.evening_preheat_trigger = ("guest_occupancy_requirement_disabled" if is_guest
                                            else "house_present_non_guest")
        from .config_flow import SCHEDULE_TIMES
        weekend = not self._is_workday() or self._enabled(self.settings.get("holiday_at_home"))
        bedtime_key = "weekend_bed" if weekend else "weekday_bed"
        bedtime = self._minutes(bedtime_key, SCHEDULE_TIMES[bedtime_key])
        ready = self._clock_minute(conf.get("evening_ready_time", "20:00"), 1200)
        room.evening_ready_time = f"{ready//60:02d}:{ready%60:02d}"
        # The ready time is a local evening time; a midnight bedtime is
        # supported, but midnight *ready* is deliberately not.
        if not (12 * 60 <= ready < 24 * 60):
            room.evening_preheat_status = "invalid_evening_ready_time"
            return False, False
        goal = self._morning_target(room)  # bedroom_target shared with evening
        allowance_conf = {
            "linked_preheat_minutes": conf.get("evening_preheat_minutes", 90),
            "linked_preheat_max_minutes": conf.get("evening_preheat_max_minutes", 240),
            "linked_extra_minutes_per_degree": conf.get("evening_extra_minutes_per_degree", 4),
            "linked_preheat_margin_minutes": conf.get("evening_preheat_margin_minutes", 15),
        }
        allowance, method = preheat_allowance(
            allowance_conf, room.measured, goal, outside,
            self.learning.get(conf["name"]),
            float(self.settings.get("preheat_reference_c", 15)),
        )
        room.evening_preheat_minutes = allowance
        room.evening_preheat_method = method
        # Don't permit a readiness time at or after bedtime (same calendar
        # day), as this could cause unintended overnight warming.
        if ready == bedtime:
            room.evening_preheat_status = "ready_time_equals_bedtime"
            return False, False
        schedule = evening_window(minute_now, ready, bedtime, allowance)
        if schedule.preheating:
            room.evening_preheat_status = "active_preheat"
        elif schedule.holding:
            room.evening_preheat_status = "active_comfort_hold"
        else:
            room.evening_preheat_status = "outside_evening_window"
        return schedule.preheating, schedule.holding

    def _bathroom_morning_target(self):
        from .config_flow import SCHEDULE_TARGETS
        try:
            return clamp(float(self._helper("bathroom_morning_target",
                           SCHEDULE_TARGETS["bathroom_morning_target"])), 5, 30)
        except (ValueError, TypeError):
            return SCHEDULE_TARGETS["bathroom_morning_target"]

    @staticmethod
    def _update_room_demand(room, now, previous, nighttime):
        if room.blocked_reason:
            room.demand = False
            room.output = 0
            room.demand_reason = room.blocked_reason
            room.pid.reset()
            return
        error = room.effective_target - room.measured
        room.demand = error > (-0.25 if room.demand else 0.25)
        room.output = (room.pid.calculate(room.effective_target, room.measured, now-previous)
                       if room.demand and not nighttime else 0)
        if not room.demand or nighttime:
            room.pid.reset()
        room.demand_reason = room.target_reason if room.demand else "temperature_satisfied"

    async def async_evaluate(self):
        async with self.lock:
            now = time.monotonic()
            self.house_present = self._occupied_with_delay(now)
            self.holiday_active, holiday_preheat, holiday_hold = self._holiday_window()
            self.holiday_return_preheating = holiday_preheat
            self.holiday_return_holding = holiday_hold
            holiday_return_active = holiday_preheat or holiday_hold
            at_home_holiday = self._enabled(self.settings.get("holiday_at_home"))
            now_local = dt_util.now()
            # Preheat before the scheduled evening return from work even when away.
            evening = self._minutes("weekday_evening", "17:00")
            minute = now_local.hour * 60 + now_local.minute
            work_preheat = (self.settings.get("enable_schedule", False) and self._is_workday()
                and not at_home_holiday and not self.holiday_active and not self.house_present
                and not self.away and not self.summer
                and evening - self._preheat_minutes() <= minute < evening)
            self.work_preheating = work_preheat
            self.preheating = holiday_preheat or work_preheat
            heat_allowed = not self.summer or self.away or self.holiday_active
            self.calibration_paused = False
            # Read all room sensors first; evaluate linked bedrooms in a second
            # pass so configuration order cannot affect bathroom decisions.
            outside = temperature(self.hass.states.get(self.settings.get("outdoor_temperature")))
            minute_now = now_local.hour * 60 + now_local.minute
            snapshots = {}
            for room in self.rooms.values():
                conf = room.conf
                previous = room.last_tick
                room.last_tick = now
                room.measured = temperature(self.hass.states.get(conf["temperature"]))
                window = self.hass.states.get(conf.get("window")) if conf.get("window") else None
                occupied = self.hass.states.get(conf.get("occupancy")) if conf.get("occupancy") else None
                room.blocked_reason = None
                room.target_reason = "scheduled"
                room.linked_occupied = ()
                room.linked_heating = ()
                room.linked_preheat_minutes = 0
                room.linked_preheat_method = "not_applicable"
                room.morning_preheat_minutes = 0
                room.morning_preheat_method = "not_applicable"
                room.morning_preheat_trigger = "not_configured"
                room.morning_preheat_status = "not_configured"
                room.evening_preheat_minutes = 0
                room.evening_preheat_method = "not_applicable"
                room.evening_preheat_status = "not_configured"
                room.evening_preheat_trigger = "not_configured"
                room.evening_ready_time = str(conf.get("evening_ready_time", "20:00"))[:5]
                room.effective_primary = bool(conf.get("primary", True))
                scheduled, room.schedule_phase, nighttime = self._scheduled_target(room)
                # Only valid ON/OFF transitions clear an override. Missing
                # sensor states must not be mistaken for someone leaving.
                occ_state = occupied.state if occupied is not None else None
                if occ_state in (STATE_ON, STATE_OFF):
                    if (room.last_occupancy_state in (STATE_ON, STATE_OFF)
                            and occ_state != room.last_occupancy_state
                            and room.manual_override_target is not None):
                        room.manual_override_target = None
                        self._history_event("override", f"{conf['name']}: occupancy changed; override ended", room=conf['name'])
                    room.last_occupancy_state = occ_state
                # Bedtime always ends a temporary override, including when a
                # special holiday-return mode would otherwise override night.
                if nighttime and room.manual_override_target is not None:
                    room.manual_override_target = None
                    self._history_event("override", f"{conf['name']}: bedtime; override ended", room=conf['name'])
                room.holiday_return_authorisation = "not_in_return_window"
                if nighttime and not holiday_return_active:
                    self.calibration_paused = True
                if holiday_return_active:
                    # Holiday return is a deliberate comfort request even if
                    # Away remains enabled, the house is empty, or it's a weekday
                    # afternoon whose ordinary profile is a daytime setback.
                    nighttime = False
                    room.schedule_phase = ("holiday_return_preheat" if holiday_preheat
                                           else "holiday_return_hold")
                    permitted, reason = self._holiday_return_room_authorisation(room)
                    room.holiday_return_authorisation = reason
                    if permitted:
                        goal = self._return_comfort_target(room)
                        room.target_reason = ("holiday_return_preheat" if holiday_preheat
                                              else "holiday_return_hold")
                    else:
                        goal = float(self.settings.get(CONF_FROST, DEFAULT_FROST))
                        room.target_reason = "holiday_return_" + reason
                elif self.away or self.holiday_active:
                    goal = float(self.settings.get(CONF_FROST, DEFAULT_FROST))
                    room.target_reason = "holiday_minimum"
                elif self.preheating:
                    goal = scheduled
                    room.target_reason = "work_return_preheat"
                elif occupied is not None and occupied.state == STATE_ON:
                    room.last_occupancy = now
                    goal = scheduled
                    room.target_reason = "room_occupied"
                elif conf.get("occupancy") and (occupied is None or occupied.state not in (STATE_ON, STATE_OFF)):
                    goal = min(scheduled, float(conf["unoccupied_target"]))
                    room.target_reason = "room_occupancy_unavailable_setback"
                elif conf.get("occupancy") and (room.last_occupancy is None or now-room.last_occupancy > 900):
                    goal = min(scheduled, float(conf["unoccupied_target"]))
                    room.target_reason = "room_unoccupied_setback"
                else:
                    goal = scheduled
                    room.target_reason = "occupancy_grace_period" if conf.get("occupancy") else "scheduled"
                if not self.house_present and not self.preheating and not holiday_return_active and not at_home_holiday:
                    goal = min(goal, float(self.settings.get(CONF_FROST, DEFAULT_FROST)))
                    room.target_reason = "house_absent_setback"

                # Occupancy can bring forward heating for the *occupied room*
                # during the early-morning window, without changing other
                # rooms or overriding the previous evening's bedtime. Bedroom
                # occupancy input_booleans often remain on while asleep, so
                # this option defaults off for bedrooms, on for living rooms
                # and bathrooms. It is independent of scheduled preheating.
                early_allowed = conf.get(
                    "early_occupancy_heating",
                    conf.get("room_type", "living") in ("living", "bathroom"),
                )
                if (nighttime and early_allowed and occupied is not None
                        and occupied.state == STATE_ON and self._early_occupancy_window(minute_now)
                        and not self.away and not self.holiday_active and not self.summer
                        and room.mode != "off"):
                    goal = self._morning_target(room)
                    room.target_reason = "early_occupied_morning"
                    room.schedule_phase = "early_occupied_morning"
                    nighttime = False

                # Any selected bedroom, living room or bathroom can warm
                # before rising. For input_boolean occupancy this is permitted
                # only while that room's boolean is ON.
                morning_pre, morning_hold = self._room_morning_context(
                    room, minute_now, outside)
                if (morning_pre or morning_hold) and not holiday_return_active:
                    goal = max(goal, self._morning_target(room))
                    room.target_reason = ("morning_room_preheat" if morning_pre
                                          else "morning_room_hold")
                    if morning_pre:
                        room.schedule_phase = "morning_room_preheat"
                    if conf.get("morning_allow_boiler", False):
                        room.effective_primary = True
                    nighttime = False

                # Bedroom-specific evening warm-up overrides an unoccupied
                # setback before the configured ready time and maintains the
                # room's ordinary bedroom target until bedtime. It never
                # changes the separate overnight frost setback.
                evening_pre, evening_hold = self._room_evening_context(
                    room, minute_now, outside,
                    holiday_return_active=holiday_return_active,
                )
                if evening_pre or evening_hold:
                    goal = max(goal, self._morning_target(room))
                    room.target_reason = (
                        "evening_bedroom_preheat" if evening_pre
                        else "evening_bedroom_comfort_hold"
                    )
                    room.schedule_phase = (
                        "evening_bedroom_preheat" if evening_pre
                        else "evening_bedroom_hold"
                    )
                    if conf.get("evening_allow_boiler", True):
                        room.effective_primary = True
                    nighttime = False

                # A shared bathroom may link to several bedrooms. Any linked
                # occupied bedroom qualifies; optionally house presence alone
                # permits pre-rise heating (important for sleeping occupants).
                if conf.get("room_type") == "bathroom" and conf.get("linked_rooms"):
                    pre, hold, _, _ = self._linked_context(room, minute_now, outside)
                    if (pre or hold) and not holiday_return_active:
                        goal = max(goal, self._bathroom_morning_target())
                        room.target_reason = "linked_morning_preheat" if pre else "linked_morning_hold"
                        if pre:
                            room.schedule_phase = "linked_morning_preheat"
                        # Explicitly permitted bathroom boiler calls during the
                        # preheat/hold window, regardless of normal primary flag.
                        if conf.get("linked_allow_morning_boiler", False):
                            room.effective_primary = True
                        nighttime = False

                if (room.manual_override_target is not None and not nighttime
                        and not self.summer and (self.house_present or holiday_return_active)
                        and (not self.away and not self.holiday_active or holiday_return_active)
                        and (not holiday_return_active or self._holiday_return_room_authorisation(room)[0])):
                    goal = room.manual_override_target
                    room.target_reason = "temporary_thermostat_override"
                room.effective_target = goal
                if room.mode == "off" or not heat_allowed or (nighttime and not self.away and room.measured is not None and room.measured >= goal + 0.25):
                    room.blocked_reason = "off"
                elif window is not None and window.state == STATE_ON:
                    room.blocked_reason = "window_open"
                elif conf.get("window") and (window is None or window.state not in (STATE_ON, STATE_OFF)):
                    room.blocked_reason = "window_unavailable"
                elif room.measured is None:
                    room.blocked_reason = "temperature_unavailable"
                snapshots[room.conf["name"]] = (previous, nighttime)

            for room in self.rooms.values():
                if room.conf.get("room_type") != "bathroom":
                    previous, nighttime = snapshots[room.conf["name"]]
                    self._update_room_demand(room, now, previous, nighttime)

            # Bedrooms are evaluated before bathrooms, irrespective of the
            # order in which the user created their rooms.
            for room in self.rooms.values():
                conf = room.conf
                if conf.get("room_type") != "bathroom":
                    continue
                previous, nighttime = snapshots[conf["name"]]
                linked = [self.rooms[n] for n in conf.get("linked_rooms", [])
                          if n in self.rooms and self.rooms[n].conf.get("room_type") == "bedroom"]
                room.linked_heating = tuple(r.conf["name"] for r in linked if r.demand)
                # Opportunistic heat uses the *scheduled* bathroom target only
                # when a linked bedroom is already demanding heat. It never
                # runs at night outside the authorised morning preheat window.
                if (linked and room.linked_heating and conf.get("linked_opportunistic", True)
                        and self.house_present and not self.away and not self.holiday_active
                        and not self.summer and not nighttime
                        and room.target_reason not in ("linked_morning_preheat", "linked_morning_hold")
                        and room.target_reason not in ("room_occupied", "morning_room_preheat", "morning_room_hold")):
                    scheduled, _, _ = self._scheduled_target(room)
                    if scheduled > room.effective_target:
                        room.effective_target = scheduled
                        room.target_reason = "opportunistic_linked_bedroom"
                self._update_room_demand(room, now, previous, nighttime)

            self.morning_preheating = any(
                r.target_reason in ("morning_room_preheat", "linked_morning_preheat")
                for r in self.rooms.values()
            )
            self.evening_preheating = any(
                r.target_reason == "evening_bedroom_preheat"
                for r in self.rooms.values()
            )
            any_primary = any(r.demand and r.effective_primary for r in self.rooms.values())
            self.boiler_requested = any_primary
            for room in self.rooms.values():
                self._observe_heating(room, now, outside)
            # Secondary rooms cannot independently call heat; they only participate during a primary call.
            for room in self.rooms.values():
                if room.blocked_reason or not room.demand or (not room.effective_primary and not any_primary):
                    trv_target = 5.0
                else:
                    trv_target = clamp(room.effective_target + room.output, 5, 30)
                room.trv_target = trv_target
                await self._write_trv(room, trv_target)
            await self._set_relay(self.settings[CONF_HEAT_SWITCH], any_primary, now)
            # True room occupancy is sufficient for a DHW request, even if an
            # away/holiday flag has inadvertently been left on. This is an
            # independent control path from space-heating occupancy arbitration.
            self.hot_water_decision = self._dhw.update(
                now=now,
                rooms=self.settings.get(CONF_ROOMS, []),
                state_of=lambda entity_id: (
                    state.state if (state := self.hass.states.get(entity_id)) else None
                ),
                enabled=bool(self.settings.get("occupancy_hot_water", False)),
                min_seconds=int(self.settings.get("hot_water_min_occupancy_seconds", 60)),
                max_minutes=int(self.settings.get("hot_water_max_run_minutes", 0)),
            )
            self.hot_water_occupancy_requested = self.hot_water_decision.demand
            self.hot_water_requested = bool(self.hot_water or self.hot_water_occupancy_requested)
            hw_switch = self.settings.get(CONF_HOT_WATER_SWITCH)
            if hw_switch:
                await self._simple_relay(hw_switch, self.hot_water_requested)
                relay_state = self.hass.states.get(hw_switch)
                self.hot_water_relay_on = (
                    None if relay_state is None or relay_state.state not in (STATE_ON, STATE_OFF)
                    else relay_state.state == STATE_ON
                )
                if self.hot_water_relay_on is None:
                    self.hot_water_reason = "hot_water_switch_unavailable"
                elif self.hot_water_requested and not self.hot_water_relay_on:
                    self.hot_water_reason = "relay_not_confirmed_on"
                elif not self.hot_water_requested and self.hot_water_relay_on:
                    self.hot_water_reason = "relay_not_confirmed_off"
                elif self.hot_water and self.hot_water_occupancy_requested:
                    self.hot_water_reason = "manual_and_room_occupancy"
                elif self.hot_water:
                    self.hot_water_reason = "manual_demand"
                else:
                    self.hot_water_reason = self.hot_water_decision.reason
            else:
                self.hot_water_relay_on = None
                self.hot_water_reason = "no_hot_water_switch_configured"
            # EMS is read-only and never contributes to any heating or DHW
            # demand. Failures must never interrupt boiler control.
            try:
                for transition in self.ems.sample(self.hass.states.get, now):
                    signal = transition["signal"]
                    self._history_event(
                        "ems", f"EMS {signal.removeprefix('ems_')}: {transition['value']}",
                        details=transition,
                    )
            except Exception:
                _LOGGER.exception("EMS monitoring failed; heating control unaffected")
            self._record_changes()
            await self._save_history_if_due(now)
            self.publish()
            if any(self.learning.get(name, {}).get("samples", 0) % 10 == 0 and self.learning.get(name, {}).get("samples", 0) > 0 for name in self.rooms):
                # Store is asynchronous; save at a low frequency to avoid excess writes.
                if now - getattr(self, "_last_learning_save", 0) > 3600:
                    self._last_learning_save = now
                    await self.save()

    async def _write_trv(self, room, target):
        trv = room.conf["trv"]
        state = self.hass.states.get(trv)
        if state is None or state.state in ("unknown","unavailable"):
            return
        reported = state.attributes.get("temperature")
        if reported is not None:
            try:
                if abs(float(reported)-target) < 0.45:
                    return
            except (ValueError,TypeError):
                pass
        if room.last_write is not None and time.monotonic()-room.last_write < 60:
            return
        try:
            await self.hass.services.async_call("climate","set_temperature",{"entity_id":trv,"temperature":round(target*2)/2},blocking=True)
            room.last_write=time.monotonic()
        except Exception:
            _LOGGER.exception("Failed to write TRV %s",trv)

    async def _simple_relay(self, entity_id, desired):
        state=self.hass.states.get(entity_id)
        if state is None or state.state not in (STATE_ON,STATE_OFF):
            _LOGGER.warning("Relay %s unavailable; skipping operation",entity_id)
            return
        if (state.state==STATE_ON)==desired:
            return
        domain=entity_id.split(".")[0]
        await self.hass.services.async_call(domain,"turn_on" if desired else "turn_off",{ATTR_ENTITY_ID:entity_id},blocking=True)

    async def _set_relay(self, entity_id, desired, now):
        state=self.hass.states.get(entity_id)
        if state is None or state.state not in (STATE_ON,STATE_OFF):
            _LOGGER.warning("Heating relay %s unavailable",entity_id)
            return
        actual=state.state==STATE_ON
        if actual != self.boiler_on:
            self.boiler_on=actual
            self.last_transition=now
        if actual==desired:
            return
        minimum=self.settings.get(CONF_MIN_ON,DEFAULT_MIN_ON) if actual else self.settings.get(CONF_MIN_OFF,DEFAULT_MIN_OFF)
        if now-self.last_transition < minimum:
            return
        try:
            await self._simple_relay(entity_id,desired)
            self.boiler_on=desired
            self.last_transition=now
        except Exception:
            _LOGGER.exception("Heating relay write failed")
