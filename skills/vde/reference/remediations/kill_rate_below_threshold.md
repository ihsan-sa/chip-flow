# kill_rate_below_threshold (mutate)

The visible suite's kill rate over N yosys mutants is below 0.9. Routes to
`testbench` - always. See also the per-class `survivor_*.md` files, which
name specifically WHICH kind of mutant is surviving; read the survivor
findings alongside this rollup, not instead of them.

**Cheapest fix first:** look at which SOURCE LINES the surviving mutants
sit on (`m["src"]` in the gate's own facts) - mutants cluster where a test
never actually checks an output's value after a particular operation runs,
rather than being spread evenly. Add or strengthen assertions there first.

**Trap:** do not chase the number by adding assertions that duplicate
existing coverage on lines that already kill their mutants - check the
survivors-by-class breakdown, not just the aggregate rate, or you can
"fix" the number while leaving the actual gap (often a must-kill class)
untouched.
