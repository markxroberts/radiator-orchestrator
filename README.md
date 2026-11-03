# HeatFlow v0.7.9 — fix startup-unavailable Heating Reason entities

This release fixes a startup-order regression introduced in v0.7.8. Home Assistant can read Heating Reason sensor attributes **before** HeatFlow completes its first control evaluation. The newly exposed `work_return_preheating` field was not initialised in the controller constructor and could raise `AttributeError` while Home Assistant was registering entities.

- Initialise `work_preheating` to `False` in the controller constructor, before any platform is forwarded or entity is registered.
- Make the diagnostic attribute defensive, so an unavailable or outdated controller property will not break the entire entity's state.
- Add a specific startup regression test that loads the Heating Reason sensor with isolated HA stubs, accesses its `native_value` and `extra_state_attributes` **before the first evaluation**, and repeats after the first control evaluation.
- Retain the v0.7.8 guest-bedroom exclusion during work-return preheating; all room schedules, boiler relays, DHW and monitor-only EMS behaviour remain unchanged.

**Upgrade:** Update the integration to v0.7.9 through HACS, then restart Home Assistant. Check that each room's Heating Reason sensor is available and that `work_return_preheating` is `false` until work-return preheating starts. If the entire integration still fails to load, review the Home Assistant log for the earliest `radiator_orchestrator` exception.

---

# HeatFlow v0.7.8 — guest bedrooms excluded from work-return preheating

This release corrects a regression in work-return preheating: **rooms marked as guest bedrooms no longer receive the scheduled heating target merely because HeatFlow is preparing for the household's return from work**.

- HeatFlow uses the same **Guest bedrooms: return preheating** list for work return and holiday return. In either case, a selected guest bedroom preheats **only when its own occupancy helper/sensor is ON**; missing, unavailable and OFF signals fail closed. No new guest-room configuration is required.
- **Ordinary bedrooms, living spaces and bathrooms are not occupancy-gated** during return preheating. They continue to warm even while the house is empty.
- A **bathroom linked only to unused guest bedrooms** is held at the normal empty-house frost target; one qualifying normal or occupied guest bedroom is sufficient to allow linked-bathroom return preheat.
- Excluded guest rooms keep the usual **empty-house frost target** during the work-return preheat window. This does not change the ordinary occupied-house setback after somebody returns home, or the separately configured evening bedroom warm-up.
- The adaptive work-return lead time also ignores unoccupied guest bedrooms. An unused cold guest room cannot cause the heating for the rest of the house to start too early.
- **Room Heating Reason** shows `work_return_preheating` and `work_return_authorisation`, with explicit `work_return_guest_unoccupied`, `work_return_guest_occupancy_unavailable`, and `work_return_linked_guest_bedrooms_unoccupied` target reasons.
- CH/DHW relay protections, holiday return, overnight setback, morning/evening bedroom warming and monitor-only EMS are unchanged.

The internal Home Assistant integration domain remains `radiator_orchestrator`. Update via HACS and restart Home Assistant. Review the existing list under **Configure → Guest bedrooms: return preheating**.

---

# HeatFlow v0.7.7 — evening bedroom preheating

HeatFlow has optional per-bedroom evening preheating. Go to **HeatFlow → Configure → Evening bedroom preheating**, select a bedroom and enable it. It is **disabled by default** for existing configurations. The existing `bedroom_target` helper still sets the desired temperature; no new temperature helper is required.

- **Warm by**: pick the local time when the bedroom should be comfortable (default **20:00**).
- **Adaptive advance**: base **90 minutes** with **up to 240 minutes** total, adjusted for outdoor conditions or validated learned room warming rates. The heating window starts before the selected time, then maintains the target until the configured weekday/weekend bedtime. Normal overnight setback starts at bedtime.
- **Normal bedrooms**: preheat when the *house* is occupied, even when the bedroom's own motion sensor is off. This fixes the evening `room_unoccupied_setback` preventing warming in advance.
- **Guest bedrooms**: mark rooms in **Holiday return: guest bedrooms** as before. When evening warm-up is enabled for a marked guest bedroom, occupancy **must be ON** by default, ideally via an `input_boolean` indicating whether the guest room is in use. An unavailable or missing occupancy sensor fails safely to no preheat. You can explicitly disable this requirement per guest bedroom if desired.
- **Independent boiler call**: enabling **May request boiler** permits an opted-in bedroom to request the boiler during the evening window even if its usual room setting is secondary. It defaults on for *selected evening preheating*, but does not change normal scheduling.
- **Safety**: window-open protection, house-absent setback, Away, Summer Off and holiday modes are preserved. Holiday-return preheating retains priority. The evening feature does not affect hot-water controls or EMS.
- **Diagnostics**: the Bedroom Heating Reason sensor now shows `evening_preheat_enabled`, `evening_ready_time`, `evening_preheat_status`, `evening_preheat_trigger`, `evening_preheat_lead_minutes`, `evening_preheat_calculation`, `evening_can_start_boiler` and `evening_guest_occupancy_required`. Heating history records evening status transitions.

This requires heating scheduling enabled. Before enabling evening warm-up, check that your configured bedroom target and weekday/weekend bedtimes match the intended comfort window.

---

# HeatFlow v0.7.6 — EMS number and select entities

EMS monitoring now accepts Home Assistant `sensor`, `binary_sensor`, `switch`, `number`, `select`, `input_number`, `input_select` and `input_boolean` sources. This fixes the EMS configuration dropdowns not showing editable-number and select entities.

- Choose your `number` entities for selected flow temperature and other numeric measurements. **HeatFlow reads their state only; it never adjusts the EMS value.**
- Choose a `select` entity for a numeric field only if its current state is actually numeric. Otherwise the reading is shown as unavailable, not guessed.
- Two new optional read-only fields are provided for **Heating pump mode** and **DHW Comfort/ECO mode**. These accept categorical states such as `prop.high`, `prop.low`, `Hot` or `Eco` and show them in separate diagnostic text sensors. Previously observed inconsistencies with the Highflow's physical ECO setting still apply.
- The **EMS Monitoring Status** sensor includes `raw_source_states` as well as interpreted `current_values`, so unsupported states can be diagnosed without confusing them with valid off/zero readings.
- Gas, pump-running, fan and DHW status retain strict boolean interpretation. A mode such as `prop.high` **does not** establish that a pump is running.
- Updating preserves previous EMS mappings, heating-room settings and switch controls. The EMS integration remains **monitor-only**.

After updating through HACS and restarting Home Assistant, open **HeatFlow → Configure → EMS-ESP monitoring (read-only)** and choose your number/select sources. All new fields are optional.

---

# HeatFlow v0.7.5 — optional EMS-ESP monitoring (read-only)

HeatFlow can now observe the existing **EMS-ESP entities in Home Assistant** without altering how the boiler is controlled. The feature defaults to **off**, does not install or configure EMS-ESP, and never sends EMS commands or changes the CH/DHW relay logic.

### Setup

1. In **Settings → Devices & services → HeatFlow → Configure**, open **EMS-ESP monitoring (read-only)**.
2. Enable EMS monitoring, then select whichever of your existing entities are available. All individual mappings are optional. For your Greenstar Highflow, the most useful initial selections are **Current Flow Temperature**, **Burner Current Power / Modulation**, **Gas**, **Fan**, **Heating Pump**, and **DHW 3-way valve**. Map hot-water temperature if EMS-ESP exposes a reliable numeric value.
3. Save. HeatFlow reloads the integration and tracks those sources on change and on its minute timer.
4. In **HeatFlow Controller**, look for **EMS Monitoring Status**, the numeric measurements you mapped, and the boolean EMS diagnostic entities. The **Diagnostic History** sensor records observed status transitions; the recent-events attribute contains the last ten events.

### What it does and does not do

- **Monitors:** actual flow temperature, optional return temperature, selected flow temperature, heating temperature setting, burner modulation, optional DHW temperature, gas/burner state, fan, heating pump, DHW valve, optional dedicated DHW charging state.
- **Observes burner activity:** session-only approximate burner starts, firing duration and accumulated time; values reset when HeatFlow is reloaded. The timing resolution is limited by incoming EMS-ESP entity updates and the controller's periodic evaluation.
- **Separates DHW valve position from charging:** a 3-way valve indicating DHW is **not** definitive evidence that the store is being charged. Only map *DHW charging* when you have a separate suitable source.
- **Handles unavailable data:** missing, unknown, invalid or out-of-range inputs become unavailable rather than defaulting to off. Partial gateway outages are displayed as **partial** or **sources_unavailable**.
- **Never writes EMS:** no direct selected-flow, burner, pump, comfort/ECO, one-time charge, or other EMS commands are issued. The existing heating and hot-water switches remain authoritative; a failed EMS gateway cannot alter heating demand.
- **Logs changes, not raw telemetry:** boolean transitions are written to bounded diagnostic history. Continuous numeric readings can use Home Assistant's Recorder/History; HeatFlow deliberately does not add a diagnostic entry on every temperature update.

This is an experimental custom integration; read-only EMS diagnostics have local unit and isolated-controller tests, not live verification on your Greenstar Highflow.

---

# HeatFlow — Home Assistant heating and hot-water management

**Experimental custom integration, v0.7.4**. The integration domain remains `radiator_orchestrator` for compatibility with existing installations.

HeatFlow coordinates room temperature sensors, radiator TRVs, a central-heating demand switch and a separate domestic hot-water switch. Features include configurable primary/secondary room demand, occupancy and open-window detection, heating schedules, workday/bank-holiday profiles, holiday-return preheating, linked bathrooms, adaptive warm-up estimates, guest-bedroom rules and diagnostic event history.

> **Safety:** This is experimental software controlling heating plant. Test against Home Assistant helper switches before enabling physical boiler outputs. Retain the boiler’s built-in circulation, overheat, pressure and hot-water protections. Do not use this integration as a safety controller.

## Installation

Copy `custom_components/radiator_orchestrator/` into your Home Assistant configuration at:

```text
/config/custom_components/radiator_orchestrator/
```

Restart Home Assistant, then add/configure **HeatFlow** from **Settings → Devices & services**. When upgrading, preserve the directory name and your existing integration entry.

For HACS, add `https://github.com/markxroberts/radiator-orchestrator` as a **custom integration repository** and then install HeatFlow (subject to HACS validation and compatibility).

## Development

Run isolated controller tests with `python -m pytest -q`. These tests use Home Assistant stand-ins and are **not** comprehensive integration or hardware tests.

## Changes and configuration reference

# HeatFlow v0.7.4 — early occupied-room heating before scheduled rising

**Fix:** A room reporting occupied in the early morning could previously remain at the 8°C night-protection setpoint until the scheduled rising time. This particularly affected the kitchen on weekends, even if a bedroom had already started its own morning preheating.

HeatFlow now permits a room's **confirmed `on` occupancy sensor** to enable its *morning comfort target* during a configurable early-morning period preceding the scheduled rising time. This is a **per-room occupancy trigger**, not a blanket rescheduling of every room. It operates independently of the selected-room morning preheat feature.

- Default earliest activation is **04:00**, and the window ends at the normal weekday or weekend rising time (or the enabled early-start time). Only after this earliest time does occupied-room heating take precedence over night setback. This avoids switching the boiler on from late-night kitchen activity after bedtime.
- The earliest time can be overridden with an existing time-only `input_datetime` under **Configure → Heating schedule and targets → Earliest occupancy-triggered morning heating**.
- **Living rooms and bathrooms** default to allowing early occupancy heating; **bedrooms** default to not allowing it (because their occupancy helpers may remain on while somebody is asleep). Edit any room to change **Heat when occupied before rising time (morning only)**. The room must have an occupancy sensor configured and showing `on`.
- **Primary rooms** may request the boiler as usual. **Secondary rooms** only warm when another room has called for boiler heat; the room's Heating Reason sensor identifies that distinction.
- Summer disable, holiday Away, open window, sensor failures, boiler minimum on/off times, and room HVAC Off continue to apply.
- The room's Heating Reason attributes include `room_occupancy_sensor_state`, `early_occupancy_window_active`, `early_occupancy_heating_enabled`, and the `early_occupied_morning` target reason when the exception is active.

**Upgrade:** Copy `radiator_orchestrator/*` into `/config/custom_components/radiator_orchestrator/` and restart Home Assistant. Do not rename the integration directory or delete the integration; existing configuration is preserved.

**Validation:** 64 isolated tests passed, including Saturday 06:35 with a kitchen occupied before 08:00, the 21°C input-number target, occupied/unoccupied transitions, late-night protection, bedroom defaults, primary/secondary heating and holiday/window restrictions. This is *not* an end-to-end Home Assistant or physical-boiler validation; inspect the room Heating Reason sensor and outputs after restarting.

---

# HeatFlow v0.7.3 — holiday-return guest bedrooms and temporary thermostat overrides

This version corrects the holiday-return occupancy rule: **most rooms preheat even while the house is empty**. Only bedrooms explicitly selected as *guest bedrooms* need an active room occupancy helper in order to warm during holiday-return preparation and the configurable arrival hold period. When a bathroom is linked to bedrooms, it preheats if **any linked bedroom qualifies**. A bathroom linked exclusively to guest bedrooms that are not in use remains at the holiday minimum temperature. Unlinked bathrooms, ordinary bedrooms and living spaces are not occupancy-gated during holiday return. All window-open, room-off, boiler cycling and summer checks remain in place.

**To set it up:** open **Settings → Devices & services → HeatFlow → Configure → Holiday return: guest bedrooms**. Select only the guest bedrooms. Use an `input_boolean` occupancy helper per guest room if possible: PIR motion sensors are not reliable indicators that a bedroom is in use while somebody sleeps. Without an available occupancy entity or if it is `off`, the selected guest bedroom cannot preheat. If no guest bedrooms are selected, existing behaviour is unchanged.

The **Holiday Return Heating Reason** sensor now reports guest bedrooms requiring occupancy and rooms withheld from return preheating. Each room's **Heating Reason** includes `holiday_return_authorisation` and whether it is flagged as a guest bedroom. Note that a bathroom with both normal and guest bedroom links can preheat when the normal bedroom qualifies.

**Temporary climate setpoint:** with automatic scheduling enabled, changing a room's HeatFlow climate setpoint makes a temporary override and does not alter your existing `input_number` schedule helpers or saved manual room target. It ends on the **next valid ON/OFF occupancy state transition** for that room, or at its scheduled bedtime, whichever comes first. Unknown/unavailable occupancy states do not cancel it. When scheduling is disabled, changing the climate setpoint retains the previous persistent behaviour. The climate attributes and room reason sensor expose the override state. A temporary setpoint never overrides window-open, summer mode, room-off or an unused guest-bedroom holiday-return restriction. Temporary overrides are held only in controller memory, so a Home Assistant restart also ends them.

**Upgrade:** copy the contents of the `radiator_orchestrator` directory over `/config/custom_components/radiator_orchestrator/`, then restart Home Assistant. The integration domain stays unchanged, so existing configuration is retained. As with previous releases, this is experimental boiler-control software: validate with output helpers before connecting a physical boiler relay.

**Verification:** 53 isolated controller tests and Python compilation pass. These are not live Home Assistant/hardware tests.

---

# HeatFlow v0.7.2 — daytime occupancy and holiday-return corrections

This experimental custom integration manages Home Assistant radiator TRVs, space-heating boiler demand and separately controlled domestic hot water. Its integration **domain remains `radiator_orchestrator`** for compatibility with the existing installation.


## Fixed in v0.7.2

### Room demand versus displayed temperature

On **weekday daytime**, v0.7.1 incorrectly chose the *unoccupied* living-room target before checking the room's occupancy. As a result, an occupied kitchen at **19.2°C** could be reported as "target satisfied" even though the manually configured temperature shown by its climate card was **21°C**. This is fixed: during weekday daytime an **occupied** living space uses the `evening_target` helper (21°C by default); an **unoccupied** room can still take its individual `unoccupied_target` setback. If the whole house is absent, the frost/away restrictions still take precedence (except an active return preheat window).

A climate entity with *automatic scheduling enabled* now displays the **effective target actually used by HeatFlow**, not its potentially misleading unscheduled manual temperature. The configured manual target is retained in attributes. **Scheduled input_number helpers still control the schedule**; changing a room's climate temperature while scheduling is enabled changes its fallback manual setting, not the central scheduled profile. This is a known limitation pending a proper per-room manual override mechanism.

Room Heating Reason adds `configured_manual_target_c`, `effective_target_c`, and `error_to_effective_target_c`. It no longer calls an unsatisfied effective target "satisfied" and records target changes in the diagnostic event history.

### Holiday return

A holiday return is now a distinct **comfort-heating period** rather than inheriting the daytime/night temperature setback. The holiday detector (`holiday_enable`) **must be on** and `holiday_return` must contain a valid future **date and time**. HeatFlow computes the lead time using outdoor temperature or available learning. During the lead period it ignores room PIR inactivity, empty-house setback, and the separate Away toggle; it uses the proper living/bedroom/bathroom comfort targets. A room whose climate is deliberately switched off, or whose window is open, remains inhibited. Summer-off disables comfort preheating.

The new **Holiday return hold minutes** option (default **120 minutes**) maintains the comfort mode for a limited period after the scheduled arrival, *while the holiday detector remains on*. If the detector is automatically turned off at the return time, the normal house-occupancy rules resume; room occupancy should then maintain heating. The hold is bounded to avoid unlimited heat in an empty house.

New diagnostic entities **Holiday Return Heating Reason**, **Holiday Return Preheating**, and **Holiday Return Comfort Hold** distinguish missing return dates, holiday-detector disabled, summer mode, warming-before-return, and holding-for-arrival. They also list demanding rooms and primary rooms capable of starting the boiler.

**What to check before the next holiday:** confirm both the holiday detector and the date-and-time helper are selected; leave the detector on during the predicted preheat period; verify the Summer Heating Off toggle is off; and check the new reason sensor shows `preheating before return` before the return time. If the boiler is already running, check room temperatures. Actual heat still depends on the relay, valve response, and boiler safety limits.

**Testing:** 48 isolated/controller tests pass, including the user's kitchen scenario at 19.2°C/21°C, weekday and Sunday lunchtime holiday returns, preheating with the Away switch on, house absence, the October 2026 UK DST transition, window-open, summer-off and the limited return hold. No live Home Assistant or boiler test has been performed.

---

## New in v0.7.1: select rooms for morning warming

Open **Settings → Devices & services → HeatFlow → Configure → Morning preheating by room**. Select any radiator-equipped **bedroom, living room or bathroom** (hot-water-only rooms are deliberately excluded). Enable *Preheat this room before rising*. Repeat for each room you want warm in the morning. Existing room settings remain unchanged unless you opt in.

For a selected room with an occupancy entity of the form **`input_boolean.*`**, morning preparation is **explicitly controlled by that input boolean**:

- `on` — this room is eligible to warm in its morning preheat/hold window, provided the house is occupied and heating isn't blocked by holiday/away, summer mode, open window or unavailable room temperature.
- `off` or `unavailable` — no morning preheat for this room, **even if house presence is on**. The generic house-presence fallback cannot override it.
- **`binary_sensor.*`** (for example a PIR) — an `on` sensor authorises warming; when off, house occupancy may still authorise it if *Allow house-presence fallback* is enabled. This avoids requiring sleeping occupants to move.

Your existing `input_number` morning temperature helpers remain the targets: **bedroom target** for bedrooms, **living-room morning target** for living rooms and **bathroom morning target** for bathrooms. The Workday sensor (including configured UK bank holidays) and early-start/weekend `input_datetime` helpers determine rising time. The starting lead uses the configured per-room baseline plus outdoor compensation, or that room's learned rate after enough observations.

You can separately allow each room to **start the boiler during morning warm-up**. Leave that option off if the room should merely take advantage of heating demanded by another primary room. The room's normal *May request boiler heat* setting continues to apply. HeatFlow also adds **Morning Room Preheating** as a binary sensor, separate from the existing general work-return/holiday preheat indicator.

For **linked bathrooms**, the earlier multiple-bedroom feature is preserved. A linked bedroom whose occupancy is an `input_boolean` also acts as an explicit trigger: the bathroom's generic house-presence fallback is not used if any linked bedroom has an `input_boolean` configured. A shared bathroom can still preheat when *any* of its linked bedrooms reports occupied. Bathroom hot-water demand remains controlled independently by the bathroom's own occupancy and the house-level DHW setting.

Each room's **Heating Reason** sensor now exposes `morning_preheat_enabled`, `morning_preheat_trigger`, `morning_preheat_status`, `morning_preheat_lead_minutes` and `morning_preheat_calculation`, in addition to the existing linked-bathroom diagnostics. Room morning preparation is disabled unless the automatic schedule is enabled.

### Per-room morning options

| Setting | Default | Meaning |
|---|---:|---|
| Preheat this room before rising | Off | Opt into individual morning warm-up |
| May start boiler during morning warm-up | Off | Allow independent boiler demand in the morning window |
| House-presence fallback for binary/motion sensors | On | Does **not** override an `input_boolean` that is off |
| Base advance warming time | 45 min | Used when learning hasn't enough valid observations |
| Maximum advance warming time | 180 min | Prevents excessive preheating |
| Extra minutes per °C below reference | 3 | Outdoor-temperature allowance |
| Keep warm after rising | 90 min | Hold morning comfort target beyond scheduled rising |

## What's new

- **One bathroom → multiple bedrooms:** link any number of existing *bedroom* room configurations to one bathroom. **Any** qualifying linked bedroom can influence its bathroom; all bedroom links are optional.
- **Morning preparation:** optional bathroom heating before the existing weekday/weekend/Workday rising time. House presence can authorise morning preparation even when bedroom PIR sensors detect no movement while people sleep. Set *Warm bathroom before scheduled rising time* and *Bathroom may start boiler during morning warm-up* to allow a secondary bathroom to trigger heating in that period.
- **Variable lead time:** starts with a configurable base (45 min) and outdoor-temperature allowance (3 extra min/°C below the house preheat reference, default 15°C; capped at 180 min). If at least three valid warming observations exist close to the present outside temperature, a learned bathroom warming rate can be used instead. These are estimates and may need tuning.
- **Morning hold:** keeps the bathroom at its morning target after rising (default 90 min) while heating remains authorised by house presence or linked bedroom occupancy.
- **Opportunistic heating:** outside the nighttime/preheat period, a secondary bathroom can raise its target to the appropriate existing *scheduled bathroom target* while a linked bedroom **actually requests** boiler heat. It **does not start the boiler** unless the bathroom's existing Primary setting or the explicit morning-boiler override permits it.
- **Clear diagnostics:** a bathroom's *Heating Reason* entity now reports `linked morning preheat`, `linked morning hold`, `opportunistic linked bedroom`, `room occupied` or `room unoccupied setback` rather than only `requesting heat`. It also exposes linked bedrooms, which bedrooms are occupied or requesting heat, effective target, and whether the bathroom can start the boiler. Changes enter the persistent 300-event diagnostic history.

**Hot-water safety:** linked bedroom presence/preheating does **not** automatically trigger hot water. Only the bathroom's own occupancy entity can do that, when its *Room occupancy triggers hot water* and the separate house-level hot-water occupancy permission are enabled.

## Installation / upgrade

1. Back up Home Assistant and your current `/config/custom_components/radiator_orchestrator` directory.
2. Extract the archive and **copy the contents** of `radiator_orchestrator/` into that existing directory, replacing matching files.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → HeatFlow → Configure → Link bathrooms to bedrooms**.
5. Select a bathroom and then select **one or several bedrooms**. Configure preheating and the other options. Repeat for another bathroom if needed.
6. Confirm that **Heating schedule and targets → Enable automatic schedule** is on; pre-rise warming uses the existing weekday/weekend/Workday and `input_datetime` settings. The outdoor temperature sensor is selected under House occupancy.

Existing radiator, occupancy, PID, boiler, holiday, and hot-water configuration should remain intact. Linked morning preheating is **disabled for existing rooms by default** until explicitly configured. *Opportunistic linked heating* defaults on when links are first added, but does nothing until a bedroom is linked. Room editing and renaming preserve existing bedroom links.

### Linked-bathroom options

| Option | Default | Meaning |
|---|---:|---|
| Linked bedrooms | none | Multi-select, any number of bedrooms |
| Warm bathroom before scheduled rising | Off | Start before rise using outdoor/learned timing |
| Bathroom may start boiler during morning warm-up | Off | Temporary primary status for the preheat & morning hold window |
| Warm while a linked bedroom calls for heat | On | Secondary/opportunistic heat (only when bedroom demand exists) |
| Use house presence if bedroom motion off | On | Avoid requiring PIR motion from sleeping occupants |
| Base lead | 45 min | Minimum starting estimate if not learned, adjusted for outdoor temperature |
| Maximum lead | 180 min | Upper cap of estimated start time |
| Extra min/°C below reference | 3 | Outdoor compensation coefficient |
| Morning hold after rising | 90 min | Post-rise scheduled bathroom comfort window |

### Recommended example

For **Main en suite**, link **Main bedroom**. Enable morning preparation, independent boiler demand in morning, and house-presence fallback.

For a **shared bathroom**, link **Bedroom 2** and **Bedroom 3**. The same rules apply: either bedroom qualifies and house occupancy optionally provides the morning fallback.

## Troubleshooting: a cold bathroom at rising

Check **Developer Tools → States → `Bathroom heating reason`** and inspect the following attributes:

- `target_reason`: *why* HeatFlow chose the target (`room_unoccupied_setback` is the typical old behaviour)
- `demand_reason`: why the room is requesting heat or satisfied
- `schedule_phase` and `effective_target_c`
- `linked_bedrooms_occupied` and `linked_bedrooms_calling_heat`
- `can_start_boiler_now`, `boiler_requested` and `boiler_relay_on`
- `linked_morning_preheat_minutes` and `linked_preheat_calculation`

If the boiler has recently been switched off, anti-short-cycle minimum-off timing may delay actual boiler activation even when the bathroom is requesting heat. TRV commands may be rate limited to one per minute. Investigate missing/unavailable room sensors or windows stuck open before changing targets.

## Safety and limitations

This is an **experimental release**, not a verified boiler-control safety system. Test using `input_boolean` output helpers or other non-energising outputs before relying on live boiler switches. The boiler's own flow/pump protection, high-limit thermostat, safety interlocks and hot-water thermostatic controls remain essential. Ensure adequate water circulation if all TRVs close. The holiday return comfort window intentionally overrides the holiday/Away setback, but must not defeat a room-off setting, window-open inhibition or summer-off comfort restriction.

Rising-day selection follows the current state of the existing HA Workday binary sensor; bank holiday behaviour depends on its configuration. For unusual rising times close to midnight, verify the schedule manually. The learning model is an initial estimate, not a guarantee of reaching target by the requested time. House presence fallback must be configured sensibly: if **no house-presence entities are supplied**, the legacy controller assumes the house is occupied, so scheduled preheating can still occur when the house is actually empty.

## Tests

48 local automated tests cover the pure timing logic and simulated controller behaviour (including multi-bedroom linking, room occupancy trigger diagnosis, scheduled preheating, window-open, independent DHW, summer/away, the Workday sensor and opportunistic heating). All passed, including explicit input_boolean off/on gating for morning preheat. This code has **not** been tested inside a live Home Assistant instance or with physical boiler/TRV hardware.

To run the provided isolated tests on a machine with Python and `pytest` / `pytest-asyncio` installed:

```bash
python -m pytest -q tests
```

Home Assistant itself is not required for these stand-in tests.
