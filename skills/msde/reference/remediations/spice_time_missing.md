# spice_time_missing (cosim)

`spice_time_reached_ns` is missing or not a number, so there is no
evidence the analog bridge ran at all.

**Cheapest fix first:** record the bridge's own last synced SPICE time
(cocotbext-ams tracks it) into the measures file, as
`corpus/msde/ring_osc_div/tb/test_ring_osc_div.py` does.

**Trap:** a bench that toggles a digital counter from a cocotb clock with
no analog block behind it would pass every other check - this field is
what tells the two apart.
