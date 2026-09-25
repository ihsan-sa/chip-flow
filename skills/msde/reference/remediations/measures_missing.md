# measures_missing (cosim)

`reports/cosim_measures.json` was never written. The bench did not get far
enough to record anything.

**Cheapest fix first:** the bench must write the measures file in a
`finally` path, before any assertion can raise. Read `log/cosim_build/`
for the Python or build error that stopped it before that point.

**Trap:** never write the file by hand or from a stub - the gate deletes
it before each run precisely so an old one cannot pass.
