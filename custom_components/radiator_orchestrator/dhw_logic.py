"""Room-by-room hot-water occupancy decisions, independent of Home Assistant.

An occupancy-driven maximum run time is optional (zero means unlimited).
Each room has its own timer and lockout, so a sensor stuck on in one room
cannot block a different room from legitimately requesting hot water.
Holiday/away flags do not block an occupied room from requesting DHW.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping


@dataclass(frozen=True)
class DHWDecision:
    demand: bool
    reason: str
    configured_rooms: tuple[str, ...]
    active_rooms: tuple[str, ...]
    qualified_rooms: tuple[str, ...]
    unavailable_rooms: tuple[str, ...]
    wait_seconds: int | None = None
    # Each room can independently qualify, time out and re-arm.
    requesting_rooms: tuple[str, ...] = ()
    timed_out_rooms: tuple[str, ...] = ()


class OccupancyHotWater:
    """Track occupancy qualification and optional per-room run-time limits.

    A timed-out room re-arms only after its sensor explicitly reports OFF.
    An unknown/unavailable sensor does not clear the timeout, avoiding an
    inadvertent new run when an unreliable sensor reconnects as ON.
    """

    def __init__(self) -> None:
        self.presence_since: dict[str, float] = {}
        self.run_since: dict[str, float] = {}
        self.limit_reached: set[str] = set()

    def update(
        self,
        *,
        now: float,
        rooms: Iterable[Mapping[str, object]],
        state_of: Callable[[str], str | None],
        enabled: bool,
        min_seconds: int,
        max_minutes: int,
    ) -> DHWDecision:
        configs = [r for r in rooms if r.get("occupancy_triggers_hot_water") and r.get("occupancy")]
        names = tuple(str(r["name"]) for r in configs)
        configured = set(names)
        # Clear timers for removed, renamed or disabled rooms.
        for name in set(self.presence_since) - configured:
            self.presence_since.pop(name, None)
        for name in set(self.run_since) - configured:
            self.run_since.pop(name, None)
        self.limit_reached.intersection_update(configured)

        minimum = max(0, int(min_seconds))
        maximum = max(0, int(max_minutes)) * 60
        if not maximum:
            # Switching the maximum back to zero must also clear old lockouts.
            self.run_since.clear()
            self.limit_reached.clear()

        active: list[str] = []
        qualified: list[str] = []
        unavailable: list[str] = []
        requesting: list[str] = []
        timed_out: list[str] = []
        waits: list[int] = []

        for room in configs:
            name = str(room["name"])
            state = state_of(str(room["occupancy"]))
            if state == "off":
                self.presence_since.pop(name, None)
                self.run_since.pop(name, None)
                self.limit_reached.discard(name)
                continue
            if state != "on":
                unavailable.append(name)
                self.presence_since.pop(name, None)
                self.run_since.pop(name, None)
                # A previously timed-out room requires a confirmed OFF to
                # reset, so keep its latched lockout on sensor failure.
                continue

            active.append(name)
            self.presence_since.setdefault(name, now)
            elapsed = max(0.0, now - self.presence_since[name])
            if elapsed < minimum:
                waits.append(max(1, int(minimum - elapsed + 0.999999)))
                continue

            qualified.append(name)
            if name in self.limit_reached:
                timed_out.append(name)
                continue
            if maximum:
                self.run_since.setdefault(name, now)
                if now - self.run_since[name] >= maximum:
                    self.limit_reached.add(name)
                    timed_out.append(name)
                    continue
            requesting.append(name)

        def decision(demand: bool, reason: str, wait: int | None = None) -> DHWDecision:
            return DHWDecision(
                demand=demand, reason=reason,
                configured_rooms=names,
                active_rooms=tuple(active),
                qualified_rooms=tuple(qualified),
                unavailable_rooms=tuple(unavailable),
                wait_seconds=wait,
                requesting_rooms=tuple(requesting) if demand else (),
                timed_out_rooms=tuple(timed_out),
            )

        if not enabled:
            # Global OFF must not leave a room in a latched safety state.
            self.run_since.clear()
            self.limit_reached.clear()
            return decision(False, "disabled_at_house_level")
        if not configs:
            return decision(False, "no_rooms_enabled")
        if requesting:
            return decision(True, "occupied_room")
        if timed_out:
            return decision(False, "maximum_run_time_reached")
        if qualified:
            # Defensive: a qualified room should request unless timed out.
            return decision(False, "no_eligible_rooms")
        if active:
            return decision(False, "waiting_minimum_occupancy", min(waits) if waits else None)
        return decision(False, "occupancy_sensor_unavailable" if unavailable else "waiting_for_occupancy")
