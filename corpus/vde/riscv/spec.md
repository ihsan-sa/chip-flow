# riscv

A small multi-cycle RISC-V core: the RV32I base integer instructions with
the RV32E register file (x0 to x15), one 128-byte memory shared by code and
data, a byte-wide loader to fill it and a byte-wide debug port to read the
registers and memory back once the program stops. Fourth and top rung on the
`/vde` corpus ladder (docs/design.md section 3). Its pins fit a Tiny Tapeout
tile, but its logic is larger than one GF180 tile holds.

## Behaviour

- **Reset.** Synchronous, active-high `rst` puts the core in the idle state:
  not halted, `illegal` low, the program counter at 0 and the loader at
  byte 0. `rst` does not clear the registers or the memory.
- **Loading.** While idle, each clock with `we` high writes `din` into the
  next memory byte, starting at byte 0 after `rst` and counting up (byte
  127 wraps to byte 0). `we` is ignored in every other state.
- **Running.** While idle, `run` high starts execution at byte address 0.
  After that `run` is ignored. The core takes at most 4 clocks per
  instruction; the exact count is not specified.
- **Halting.** `ECALL` (0x00000073) and `EBREAK` (0x00100073) halt the core
  with `illegal` low. Any other encoding outside the supported set halts it
  with `illegal` high, before that instruction changes any register or
  memory. A halted core stays halted, ignoring `run` and `we`, until `rst`.
- **Read-back.** While halted, `dout` shows byte `din[1:0]` (0 = least
  significant) of memory word `din[6:2]` when `din[7]` is 1, or of register
  `din[5:2]` when `din[7]` is 0 (`din[6]` is then ignored). It changes with
  `din` in the same clock. While not halted `dout` is 0.

## Instructions

Encodings and results are RV32I's (the RISC-V unprivileged spec, chapter
2), with the differences listed under Addresses.

- `LUI`, `AUIPC`, `JAL`, `JALR`
- `BEQ`, `BNE`, `BLT`, `BGE`, `BLTU`, `BGEU`
- `LB`, `LH`, `LW`, `LBU`, `LHU`, `SB`, `SH`, `SW`
- `ADDI`, `SLTI`, `SLTIU`, `XORI`, `ORI`, `ANDI`, `SLLI`, `SRLI`, `SRAI`
- `ADD`, `SUB`, `SLL`, `SLT`, `SLTU`, `XOR`, `SRL`, `SRA`, `OR`, `AND`
- `ECALL`, `EBREAK` (halt, as above)

Everything else is illegal, including `FENCE`, every CSR instruction, an
unused `funct3`/`funct7` combination, and any instruction whose `rd`, `rs1`
or `rs2` field (where the format has one) is 16 or more. `x0` always reads
as 0 and a write to it is discarded. `SRA` and `SRAI` are arithmetic:
they shift in copies of bit 31.

## Addresses

- The memory is 128 bytes, little-endian. A load or store address is
  `(rs1 + imm)` modulo 128.
- `LH`, `LHU` and `SH` ignore address bit 0; `LW` and `SW` ignore bits 1
  and 0. None of them trap.
- The program counter is a byte address modulo 128 whose low two bits are
  always 0. A jump or branch target has its low two bits dropped (`JALR`
  first clears bit 0, as RV32I does), and execution falls from byte 124 to
  byte 0.
- The value `AUIPC`, `JAL` and `JALR` compute from the program counter is
  the plain 32-bit sum, so a link taken at byte 124 is 128.

## Interface

| port    | dir | width | meaning |
|---|---|---|---|
| clk     | in  | 1 | free-running clock |
| rst     | in  | 1 | synchronous, active-high reset |
| we      | in  | 1 | while idle: write `din` to the next memory byte |
| run     | in  | 1 | while idle: start execution at address 0 |
| din     | in  | 8 | loader byte, and the read-back select while halted |
| dout    | out | 8 | the selected byte while halted, else 0 |
| halted  | out | 1 | the core has stopped |
| illegal | out | 1 | it stopped on an illegal instruction |

See `spec.yaml` for the machine-readable requirements this spec.md prose
maps to.
