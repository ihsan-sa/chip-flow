# engine_disagreement (formal)

abc pdr found a counterexample that smtbmc's k-induction did not. PDR is a
sound method for a safety property, so its counterexample is trusted, not
dismissed as noise - this is treated as a real failure. Routes to
`formal`.

**Cheapest fix first:** k-induction can fail to converge (report
"bounded", not "proven") on a property that needs an inductive invariant
it was not given - this disagreement is often exactly that case surfacing
differently: PDR found the real counterexample k-induction's own
insufficient induction step missed entirely. Read PDR's own trace first.

**Trap:** do not resolve this by trusting smtbmc's silence over PDR's
explicit counterexample - PDR is the one that actually found something
here.
