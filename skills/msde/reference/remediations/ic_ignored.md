# ic_ignored (cosim)

ngspice logged "IC on non-existent node - <n>, ignored": an `.ic` in the
analog model names a node that does not exist, and the initial condition
was silently dropped.

**Cheapest fix first:** cocotbext-ams instantiates the user subcircuit as
`x1`, so an internal node is `x1.<node>`, not the bare name. Fix the name
in the bench's analog model (`tb/`).

**Trap:** a ring oscillator that starts anyway because of numerical noise
passes the measures today and fails on the next corner or seed - fix the
`.ic`, do not rely on it starting by luck.
