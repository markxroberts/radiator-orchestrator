# HeatFlow v0.7.19 — burner-aware low-load heating and trustworthy cycling diagnostics

This release builds on v0.7.18 without modifying EMS-ESP flow-temperature,
burner-power or pump settings.

## CH burner-aware low-demand cutoff

When **Enable low-demand boiler cutoff** is already enabled, HeatFlow now
prefers to stop an unnecessary CH call **after the current confirmed CH burner
firing period ends**, rather than cutting the flame short immediately when
the 120-second small-load confirmation expires. A strict configurable
**maximum extra wait**, default **180 seconds**, prevents indefinite firing;
set it to **0** to disable the extra wait. Missing flame/CH evidence or active
DHW charging does not extend the heating call. Confirmed open windows, room
OFF, a genuine lost demand, Summer Off, unavailable-sensor precautions and
preheating/comfort safeguards still take priority. The existing 300-second
minimum-on/off relay protection remains in force.

The existing **Low Demand Boiler Call Status** sensor now reports
`waiting_for_ch_burner_to_finish`, the remaining maximum wait, total
suppression periods, CH-burner deferrals and minutes for which the controller
has suppressed an otherwise-present small-load CH request. These are
**observations, not a numerical claim of avoided starts or energy savings**.

## Multiple independent DHW signals

CH/DHW cycle diagnostics now combine the EMS CH and DHW-active flags with
`binary_sensor.boiler_dhw_charging`,
`binary_sensor.boiler_dhw_3wayvalve`, and the cumulative
`sensor.boiler_heatstarts`, `sensor.boiler_dhw_starts` and
`sensor.boiler_burnstarts` counters, when available.

This particularly addresses the observed situation where
`binary_sensor.boiler_dhw_active` remains OFF while the boiler's DHW
start counter advances. If a cycle's independent evidence conflicts
(for example a DHW counter increment accompanied by apparently pure CH
flags), HeatFlow explicitly records `uncertain_CH_DHW` instead of
silently declaring a confirmed CH firing. Counters can lag individual
burner transitions, so the per-cycle counter increments are best treated as
corroborating observations, not absolute proof. Existing historical
classifications and counter histories are not rewritten. The last
**confirmed** CH duration still persists across uncertain/DHW cycles.

## Demand-normalised performance

A new **CH Starts per HeatFlow Demand Hour** diagnostic measurement records
the boiler CH start-counter increase divided by the elapsed hours during
which HeatFlow requested heating (minimum ten recorded demand minutes).
Its attributes include:

- CH and DHW start-counter increments since integration reload;
- observed HeatFlow heating-request hours and relay-ON hours;
- confirmed/uncertain/DHW classifications since reload;
- median and 90th-percentile durations for recent confirmed CH cycles,
  the number shorter than two minutes, median peak flow temperature and
  median initial radiator demand proxy;
- low-demand suppression events, burner deferrals, suppressed minutes and
  burner starts observed while suppression was active.

This summary is **since integration reload**, not a rolling 24-hour metric,
and requires time to accumulate after restarting Home Assistant.
The recent-cycle statistics cover at most 20 completed cycles. Individual
start counters may lag; the 'starts per hour' rate is a diagnostic estimate,
not a billing or efficiency measurement. No return-temperature entity or
actual radiator valve-opening feedback is assumed to exist.

## Upgrade

Install **v0.7.19** from HACS and restart Home Assistant. Existing
settings are preserved: the low-demand cutoff remains at its previous
enabled/disabled setting, and the extra wait defaults to 180 seconds
when enabled. Review Boiler settings if you prefer 0 seconds or a
shorter limit. New diagnostics will start collecting at restart.

This version was tested with isolated Home Assistant stubs and CI.
Live boiler testing and a longer before/after evaluation are still needed.
