# no_supply (spec_lint)

spec.yaml has no `supply.vdd`, or it isn't a positive number. `sim_run`
derives every corner's VDD from it (the +/-10% supply corners scale this
value), so no simulation gate can run without it.

**Cheapest fix first:** the brief's rail, in volts: `supply: {vdd: 3.3}`
for the 3.3 V gf180mcu devices (`nfet_03v3`/`pfet_03v3`).

**Trap:** the devices' voltage rating has to match. A 5 V or 6 V supply
with 3.3 V models is a netlist problem `netlist_lint` will not see.
