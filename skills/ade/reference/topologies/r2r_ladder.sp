* r2r_ladder.sp - 4-bit R2R ladder DAC (docs/design.md 5, "### M8.").
* Section 5's topology library. corpus/ade/r2r_dac is a simpler 2-bit
* realization of the same idea (self-contained, not a literal instantiation
* of this template - see that rung's own netlist for the worked, sim-tested
* case this template generalizes).
*
* TOPOLOGY: a resistor ladder where each bit node connects through an "R"
* leg to the ladder spine and each spine segment is a "2R" leg to the next
* bit (or to ground at the LSB end); a bit driven to VDD/0 by an ideal
* external source (this is a SIMULATION-ONLY model of the real switches -
* M9's layout milestone is what turns a bit input into an actual
* transmission-gate switch in silicon; nothing here claims to be that).
* Standard R2R math: each bit contributes VDD * 2^-k to vout (k = 0 at the
* MSB), giving vout = VDD * sum(bit_k * 2^(k-N)) for an N-bit code.
*
*   b3(MSB) --R-- n2 --2R-- gnd
*   b2      --R-- n1 --2R-- n2
*   b1      --R-- n0 --2R-- n1
*   b0(LSB) --R-- vout --2R-- n0
*                  |
*                (output, high impedance - buffer externally)
*
* DESIGN EQUATIONS:
*   vout (ideal, unloaded) = vdd * (b3/2 + b2/4 + b1/8 + b0/16)
*      where b_k in {0, 1} is that bit's driven logic level as a fraction
*      of vdd (i.e. bit value 0 or 1 exactly, ideal switches)
*   Resolution (1 LSB) = vdd / 2^N
*   INL/DNL depend ENTIRELY on the R:2R ratio actually realized - a ratio
*     error of x% produces a code-dependent output error on that order,
*     which is exactly what corpus/ade/r2r_dac's optimise.py numeric target
*     (sizing/sizing.yaml) tunes back to spec.
*
* SIZING BOUNDS (rm1, this PDK's poly resistor - see
* libs.tech/ngspice/sm141064.spice's own `.subckt rm1`): rm1's resistance
* is r_rsh0 * (r_length / r_width) (a sheet-resistance model), so the RATIO
* between two rm1 instances at the SAME r_width depends only on the RATIO
* of their r_length - exactly 2x for R and 2R when 2R's length is 2x R's,
* regardless of corner (rsh0 cancels in the ratio). This is why a real
* R2R ladder in this PDK is built by length, not by picking two absolute
* resistances:
*   r_width: 1e-6 to 4e-6 - wider trades area for better matching (lower
*      relative edge-effect variation between nominally-equal legs).
*   r_length (the "R" leg): 2e-6 to 2e-5 - sets absolute R (and so the
*      ladder's output impedance and any downstream RC settling time);
*      the "2R" leg is always 2x this by construction, never sized
*      independently in a correct ladder (corpus/ade/r2r_dac's
*      deliberately-wrong start sets it to 1x instead - the fault the
*      sizing/sizing.yaml optimise loop must correct).
*
* PARAMETERS:
*   r_width          shared width of every R and 2R leg
*   r_length         the "R" unit length; "2R" legs are always 2*r_length

.param r_width=2e-6 r_length=1e-5

.subckt r2r_ladder b0 b1 b2 b3 vout r_width={r_width} r_length={r_length}
xr_b0   b0   vout           rm1 r_width={r_width} r_length={r_length}
xr2_lsb vout n0             rm1 r_width={r_width} r_length='2*{r_length}'
xr_b1   b1   n0             rm1 r_width={r_width} r_length={r_length}
xr2_1   n0   n1             rm1 r_width={r_width} r_length='2*{r_length}'
xr_b2   b2   n1             rm1 r_width={r_width} r_length={r_length}
xr2_2   n1   n2             rm1 r_width={r_width} r_length='2*{r_length}'
xr_b3   b3   n2             rm1 r_width={r_width} r_length={r_length}
xr2_msb n2   0              rm1 r_width={r_width} r_length='2*{r_length}'
.ends r2r_ladder
