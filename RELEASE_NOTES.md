# HeatFlow v0.7.21 — renew temporary Worcester Bosch HT3/EMS-ESP commands

The Worcester Bosch Greenstar Highflow 440CDi ErP accepts selected CH flow-temperature commands, but its `selflowtemp` request **expires after approximately 60 seconds**. The selected maximum burner power (`selburnpow`, not actual modulation or `burnmaxpower`) may likewise revert after about **five minutes**. A one-time write cannot sustain either setting.

**Selected CH flow temperature (existing opt-in experiment)**

- Renews an established, actively required 65°C/70°C CH target at most every **40 seconds**, on an independent 20-second maintenance check. Reassertion of the SAME target is distinct from reducing to a NEW target: the original 3-minute low-demand confirmation and 10-minute interval between reductions remain.
- A return to the captured baseline (normally 75°C) after an earlier command is treated as an expected expiry, not a user intervention. Unexplained third-party values must persist at least 180 seconds outside known DHW activity before HeatFlow relinquishes control. DHW-related 53°C/85°C excursions pause adjustments rather than causing an immediate permanent fault.
- No temperature command is sent while the DHW-active, charging or three-way-valve signals indicate DHW use or are unavailable. Baseline capture also waits for those signals to be safely OFF. Existing protections for CH demand, comfort, sensor loss, preheat and high observed flow remain in force.
- Existing baseline-ownership journal and safe restore-on-disable/unload/restart protections are retained. An uncertain write latches a fault without unbounded retries. The diagnostic **Experimental CH Flow Temperature** now exposes successful renewals and expected setpoint reversions.

**Selected maximum burner power (separate optional control)**

- Under **HeatFlow → Configure → Boiler settings**, explicitly enable **Maintain selected maximum burner power during CH**, select **`number.boiler_selburnpow`** and set a target (default **90%**). **OFF by default:** upgrading does not modify burner power.
- On a valid CH heating request with independent DHW signals known OFF, this requests the selected **maximum** burner power percentage, renewing at most every **240 seconds** (four minutes), faster than the observed five-minute expiry.
- It does **not** command actual burner modulation, permanent `number.boiler_burnmaxpower`, pump mode, or DHW operation. It cannot override physical boiler safety systems and should be tested for room warm-up comfort.
- During DHW it pauses; when CH ends, the option is disabled or HA unloads, it restores its captured initial setting when safe. A durable ownership journal allows conservative restoration on restart. An unexpected persistent third-party edit causes it to relinquish control rather than repeatedly overwrite the new value.
- A new **Selected Burner Maximum Power Control** diagnostic sensor reports actual, target, captured baseline, renewal count, expected expiries and fault state.

**Experimental caution:** No live boiler commands were issued in preparing this update. Both controls require explicit enabling and correctly selected EMS-ESP entities. Use the independent readbacks and the CH Starts per HeatFlow Demand Hour sensor to judge whether cycling and room warm-up improve. In particular, lowering CH flow temperature may reduce radiator heat output and worsen low-load cycling.

Update through HACS to **v0.7.21** and restart Home Assistant to initialise the new lease-renewal timers.

