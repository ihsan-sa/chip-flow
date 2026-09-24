# spi_fifo

An SPI-slave receiver backed by a 4-byte FIFO. Third rung on the `/vde`
corpus ladder (docs/design.md section 3) - one clock domain throughout
(sclk/cs_n are sampled synchronously in the `clk` domain; this rung takes
that simplification deliberately, not a real SPI slave's clock-domain
crossing), so the design stays small enough to harden quickly while still
exercising a real handshake (SPI framing) and real state (a circular
buffer) that counter8 and uart do not.

## Behaviour

- While `cs_n` is low, each rising edge of `sclk` shifts `mosi` into an
  8-bit shift register, MSB first.
- The 8th bit completes a byte: it is pushed onto the FIFO unless the FIFO
  is already full, in which case it is dropped and the FIFO's existing
  contents are unchanged.
- `rd_en` held high for one `clk` edge pops the oldest queued byte onto
  `rd_data` on the following edge, when the FIFO is not empty; `rd_en`
  while empty has no effect.
- `empty` is asserted iff the FIFO holds no bytes; `full` is asserted iff
  it holds 4.
- `rst` clears the FIFO, the shift register and the in-progress bit count.

## Interface

| port    | dir | width | meaning |
|---|---|---|---|
| clk     | in  | 1 | free-running clock |
| rst     | in  | 1 | synchronous, active-high reset |
| sclk    | in  | 1 | SPI clock, sampled in the clk domain |
| mosi    | in  | 1 | SPI data in |
| cs_n    | in  | 1 | SPI chip select, active low |
| rd_en   | in  | 1 | pop request, one clk pulse |
| rd_data | out | 8 | the popped byte |
| empty   | out | 1 | FIFO holds no bytes |
| full    | out | 1 | FIFO holds 4 bytes |

See `spec.yaml` for the machine-readable requirements this spec.md prose
maps to.
