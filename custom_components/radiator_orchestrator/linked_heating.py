"""Pure helpers for linked bathrooms (safe to unit-test without Home Assistant)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class MorningWindow:
    preheating: bool
    morning_hold: bool
    preheat_minutes: int
    calculation: str


def preheat_allowance(conf: dict, measured: float | None, target: float,
                      outside: float | None, learning: dict | None,
                      reference_outside: float = 15.0) -> tuple[int, str]:
    """Estimate bathroom lead time; use validated learned rate when available.

    Temperatures and learning rates are approximate; clamp lead time to avoid
    an excessively early call for heat following poor sensor data.
    """
    base = max(0, int(conf.get("linked_preheat_minutes", 45)))
    maximum = max(0, min(240, int(conf.get("linked_preheat_max_minutes", 180))))
    extra_per_degree = max(0.0, float(conf.get("linked_extra_minutes_per_degree", 3)))
    fallback = base if outside is None else base + max(0.0, reference_outside - outside) * extra_per_degree
    result, method = fallback, "outdoor_fallback" if outside is not None else "fixed_fallback"
    sample = learning or {}
    if (measured is not None and outside is not None and sample.get("samples", 0) >= 3
            and abs(outside - float(sample.get("outside", outside))) <= 12):
        rate = float(sample.get("rate", 0))
        if 0.1 <= rate <= 8:
            margin = max(0, float(conf.get("linked_preheat_margin_minutes", 10)))
            result = max(0.0, target - measured) / rate * 60 + margin
            method = "learned"
    return min(maximum, max(0, int(round(result)))), method


def morning_window(now_minute: int, rise_minute: int, lead_minutes: int,
                   hold_minutes: int) -> MorningWindow:
    """Find upcoming rise and post-rise hold, including over midnight."""
    now_minute %= 1440
    rise_minute %= 1440
    until_rise = (rise_minute - now_minute) % 1440
    since_rise = (now_minute - rise_minute) % 1440
    lead_minutes = max(0, min(240, int(lead_minutes)))
    hold_minutes = max(0, min(240, int(hold_minutes)))
    return MorningWindow(
        preheating=0 < until_rise <= lead_minutes,
        morning_hold=0 <= since_rise < hold_minutes,
        preheat_minutes=lead_minutes,
        calculation="window",
    )


def preheat_authorisation(occupancy_entity: str | None, sensor_state: str | None,
                          house_present: bool, fallback: bool) -> tuple[bool, str]:
    """Authorise a selected room's morning warm-up.

    An input_boolean represents an explicit room-use choice, so its OFF or
    unavailable state must never be overridden by PIR/house presence fallback.
    Conventional binary sensors can use house presence when their PIR is off.
    """
    if not house_present:
        return False, "house_absent"
    if occupancy_entity and occupancy_entity.startswith("input_boolean."):
        if sensor_state == "on":
            return True, "input_boolean_on"
        return False, ("input_boolean_off" if sensor_state == "off"
                       else "input_boolean_unavailable")
    if sensor_state == "on":
        return True, "binary_sensor_on"
    if fallback:
        return True, "house_presence_fallback"
    return False, "no_room_presence"

@dataclass(frozen=True)
class EveningWindow:
    """One local day's bedroom warm-up, including bedtime after midnight."""
    preheating: bool
    holding: bool


def evening_window(now_minute: int, ready_minute: int, bedtime_minute: int,
                   lead_minutes: int) -> EveningWindow:
    """Warm up before ready time and hold only until the next bedtime.

    This is deliberately NOT a recurring modulo-24-hour preheat window:
    a room must never start its *evening* warm-up the next morning.
    A bedtime such as 00:30 is interpreted as after the evening ready time.
    """
    now = int(now_minute) % 1440
    ready = int(ready_minute) % 1440
    bedtime = int(bedtime_minute) % 1440
    lead = max(0, min(240, int(lead_minutes)))
    # If bedtime is during the same afternoon/evening, an even later ready
    # time is invalid: it must not accidentally stretch until next evening.
    if bedtime >= 12 * 60 and ready >= bedtime:
        return EveningWindow(False, False)
    if bedtime <= ready:
        bedtime += 1440
        if now < bedtime - 1440:
            now += 1440
    return EveningWindow(
        preheating=ready - lead <= now < ready,
        holding=ready <= now < bedtime,
    )
