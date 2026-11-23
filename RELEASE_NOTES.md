# HeatFlow v0.7.20

## CH/DHW classification

Isolated DHW three-way-valve activity no longer proves DHW firing or turns a CH cycle into mixed_CH_DHW. Valve activity remains in `firing_evidence.dhw_valve_on` and start/end snapshots. Independent DHW-active/charging evidence and start counters retain their existing roles; missing independent signals and counter conflicts remain uncertain. Asynchronous counters can still lag a cycle; these are observations, not definitive hydraulic diagnoses.

## Experimental selected CH flow temperature

Disabled by default. No entity is automatically selected. Existing heating, DHW, relay minimum-on/off and boiler protections are unchanged.

After explicitly selecting the EMS-ESP selected CH temperature number, HeatFlow captures its current setting (normally 75°C) as the baseline. One or two calling **primary** radiators request 65°C; three or four request 70°C; five or more request the captured baseline. Targets never exceed the captured baseline. Secondary radiators are excluded from the count. This is room demand, not measured valve position or radiator output.

Reductions require three minutes of stable demand and at least ten minutes since the last write. Increases and fallback to baseline take priority. Holiday/work-return, morning/evening preheat and comfort holds, manual overrides, sensor loss/grace, deficits of at least 1°C, no demand and missing flow observations prevent reduction. At observed flow ≥85°C the experiment stops and attempts baseline restoration; this is not a boiler safety cutoff.

DHW-active, charging and valve signals must all be known OFF and HeatFlow must have no hot-water request before any write, including restoration. Unavailable selected entities, invalid bounds/step/units and failed readback prevent continuing modulation. Each write has a bounded acknowledgement wait, with no resend loop. External changes relinquish ownership. Persistent ownership records support restart recovery without adopting a reduced baseline. Disable, unload and clean shutdown restore the baseline only while still owned and safe; DHW/unavailability defer restoration until a later evaluation or restart. A failed restoration is not retried automatically, including across restart, and needs manual checking. A timed-out command retains its journal for possible late acknowledgement.

**Activation:** Install v0.7.20 through HACS, restart Home Assistant, then open HeatFlow → Configure → Boiler settings. Select `number.boiler_selflowtemp` only after confirming it is the EMS-ESP **selected central-heating flow temperature**, then enable experimental CH flow temperature modulation. Verify its current baseline first; a validated Celsius baseline between 60 and 85 is required. Do not select a DHW temperature, power limit or pump setting. No automatic activation occurs on upgrade.

**Observe:** Experimental CH Flow Temperature exposes status, requested setpoint, actual readback, observed flow, captured baseline, ownership and fault separately. Compare with CH Starts per HeatFlow Demand Hour, confirmed CH durations/peaks and room comfort. Lower flow temperature can worsen cycling with too few emitters; no efficiency or cycling improvement is assumed. Disable the experiment if results worsen and verify baseline restoration. If a fault reports manual action, inspect the actual selected temperature and restore the recorded baseline manually when safe; do not repeatedly toggle/reload to force commands.

239 isolated automated tests pass, plus compilation and JSON validation. No physical-boiler validation or live setting changes were performed during repository work.
