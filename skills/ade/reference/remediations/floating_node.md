# floating_node (netlist_lint)

A node touches fewer than two device terminals and is not a subckt pin or
a rail. Routes to `netlist`.

**Cheapest fix first:** a misspelt node name (`iout` vs `i_out`) is the
usual cause. Read the device line the node appears on and the one it was
meant to join.

**Trap:** a floating gate can still "converge": this PDK has produced an
exit-0 `.op` through a singular matrix (simlib.py's docstring has the
transcript). A clean sim_tt does not clear this finding.
