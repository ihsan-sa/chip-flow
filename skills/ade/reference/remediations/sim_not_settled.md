# sim_not_settled (sim_tt, sim_pvt)

A step bench printed `<measure> = not_settled`: the output was still
outside its settling band at the end of the window, or never moved, so
there is no settling time to score. Routes to `sizing`.

**Cheapest fix first:** compare the block's real time constant with the
one `spec/topology.md` predicted. The bench sizes its window from that
prediction (`skills/ade/templates/step_settle_tb.cir`), so a block that
does not settle inside it is slower than its own design equations say:
too much resistance or capacitance on the output node, or a driver
weaker than assumed.

**Trap:** don't lengthen the window to turn this into a number that then
fails its bound. If the prediction itself was wrong, that is the
bench-writer's to fix in `tb/` and the spec's to restate, so say so in
OPEN. A spec asking for a settling time the topology cannot reach (the
R-2R ladder's INL-versus-settling limit) is a question for the person.
