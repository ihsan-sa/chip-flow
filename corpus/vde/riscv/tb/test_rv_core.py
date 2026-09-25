"""Visible tests for rv_core (docs/design.md section 2). Every program runs
on tb/rv_model.py first; the core's registers (and, where a test stores to
memory, all 128 bytes of memory) are then read back through the debug port
and compared with the model's.

Inputs are driven on the falling edge, the same discipline as corpus/vde/
counter8/tb/test_counter8.py. rst clears neither the registers nor the
memory (spec.md), so a Bench carries the model's registers and memory from
one program to the next, and every test starts by loading a known image and
zeroing x1..x15.

Deliberately never runs SRA or SRAI on a negative operand: the random
programs swap those for SRL/SRLI. holdout/test_rv_core_holdout.py checks the
sign fill, which is what the `holdout` gate's corpus fault targets."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

import rv_model as rv
from rv_model import (EBREAK, ECALL, MEM_BYTES, Model, branch, iop, jal,
                      jalr, li, load, rop, store, to_bytes)

CLK_PERIOD_NS = 10
CLOCKS_PER_STEP_MAX = 4  # spec.md: at most 4 clocks per instruction


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.regs = [0] * 16
        self.mem = bytearray(MEM_BYTES)

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, CLK_PERIOD_NS, unit="ns").start())
        await self.reset()
        # a known memory image and x1..x15 = 0, whatever power-up left
        clear = [iop("addi", r, 0, 0) for r in range(1, 16)] + [EBREAK]
        image = bytearray(to_bytes(clear))
        image += bytes(MEM_BYTES - len(image))
        await self.load(image)
        await self.run_until_halt(64)
        self.mem = image

    async def reset(self):
        dut = self.dut
        await FallingEdge(dut.clk)
        dut.rst.value = 1
        dut.we.value = 0
        dut.run.value = 0
        dut.din.value = 0
        for _ in range(2):
            await FallingEdge(dut.clk)
        dut.rst.value = 0

    async def load(self, image):
        dut = self.dut
        for byte in image:
            dut.din.value = byte
            dut.we.value = 1
            await FallingEdge(dut.clk)
        dut.we.value = 0
        dut.din.value = 0

    async def run_until_halt(self, max_cycles):
        dut = self.dut
        dut.run.value = 1
        for n in range(1, max_cycles + 1):
            await FallingEdge(dut.clk)
            if int(dut.halted.value):
                dut.run.value = 0
                return n
        dut.run.value = 0
        raise AssertionError(f"core did not halt within {max_cycles} clocks")

    async def peek(self, sel):
        self.dut.din.value = sel
        await Timer(1, unit="ns")
        return int(self.dut.dout.value)

    async def read_reg(self, index):
        value = 0
        for k in range(4):
            value |= await self.peek((index << 2) | k) << (8 * k)
        return value

    async def read_mem(self):
        return bytes([await self.peek(0x80 | i) for i in range(MEM_BYTES)])

    async def run(self, words, data=None, check_mem=False, what=""):
        """Load `words` from address 0 (plus `data`, {byte address: bytes}),
        run to the halt, and compare the core against the model."""
        image = bytearray(self.mem)
        prog = to_bytes(words)
        image[:len(prog)] = prog
        end = len(prog)
        for addr, blob in (data or {}).items():
            image[addr:addr + len(blob)] = blob
            end = max(end, addr + len(blob))
        model = Model(bytes(image))
        model.regs = list(self.regs)
        model.run()
        assert model.halted, f"{what}: the model itself never halted"

        await self.reset()
        assert int(self.dut.halted.value) == 0, f"{what}: halted after rst"
        assert int(self.dut.illegal.value) == 0, f"{what}: illegal after rst"
        await self.load(image[:end])
        cycles = await self.run_until_halt(
            CLOCKS_PER_STEP_MAX * (model.steps + 1) + 4)
        assert int(self.dut.illegal.value) == int(model.illegal), \
            f"{what}: illegal={int(self.dut.illegal.value)}, model {model.illegal}"
        for r in range(16):
            got = await self.read_reg(r)
            assert got == model.regs[r], \
                f"{what}: x{r}={got:#010x}, model {model.regs[r]:#010x}"
        if check_mem:
            got = await self.read_mem()
            assert got == bytes(model.mem), \
                f"{what}: memory differs from the model at bytes " \
                f"{[i for i in range(MEM_BYTES) if got[i] != model.mem[i]]}"
        self.regs = list(model.regs)
        self.mem = bytearray(model.mem)
        return model, cycles


SPECIAL = [0, 1, 2, 31, 32, 0x7FF, 0x800, 0xFFF, 0x7FFFFFFF, 0x80000000,
           0xFFFFFFFF, 0xFFFFF800, 0x12345678, 0xDEADBEEF]


def rand_value(rng):
    return rng.choice(SPECIAL) if rng.random() < 0.3 else rng.getrandbits(32)


def rand_reg(rng, allow_zero=True):
    return rng.randrange(0 if allow_zero and rng.random() < 0.1 else 1, 16)


def straight_line(rng, regs, n_ops, n_seed):
    """A random straight-line ALU program. A scratch model steps along as it
    is generated so that SRA/SRAI never see a negative operand."""
    words = []
    for r in rng.sample(range(1, 16), n_seed):
        words += li(r, rand_value(rng))
    scratch = Model()
    scratch.regs = list(regs)
    for w in words:
        _exec(scratch, w)
    kinds = list(rv.R_OPS) + list(rv.I_OPS) + list(rv.SHIFT_I) + ["lui", "auipc"]
    for _ in range(n_ops):
        kind = rng.choice(kinds)
        rd, rs1, rs2 = rand_reg(rng), rand_reg(rng), rand_reg(rng)
        if kind in ("sra", "srai") and scratch.regs[rs1] >> 31:
            kind = "srl" if kind == "sra" else "srli"
        if kind in rv.R_OPS:
            w = rop(kind, rd, rs1, rs2)
        elif kind in rv.SHIFT_I:
            w = iop(kind, rd, rs1, rng.randrange(32))
        elif kind in rv.I_OPS:
            w = iop(kind, rd, rs1, rng.randrange(-2048, 2048))
        elif kind == "lui":
            w = rv.lui(rd, rng.getrandbits(20))
        else:
            w = rv.auipc(rd, rng.getrandbits(20))
        words.append(w)
        _exec(scratch, w)
    return words + [EBREAK]


def _exec(model, word):
    i = model.pc * 4
    model.mem[i:i + 4] = word.to_bytes(4, "little")
    model.step()


# req: REQ-LOAD
# req: REQ-READBACK
@cocotb.test()
async def test_load_and_read_back_memory(dut):
    bench = Bench(dut)
    await bench.start()
    rng = random.Random(1)
    for rep in range(4):
        image = bytearray(rng.getrandbits(8) for _ in range(MEM_BYTES))
        image[0:4] = EBREAK.to_bytes(4, "little")
        await bench.reset()
        await bench.load(image)
        # not halted yet: dout reads 0 whatever din selects
        for sel in (0x80, 0x85, 0xFF, 0x07):
            assert await bench.peek(sel) == 0, f"rep {rep}: dout not 0 while idle"
        await bench.run_until_halt(8)
        got = await bench.read_mem()
        assert got == bytes(image), f"rep {rep}: memory read back wrong"
        bench.mem = image


# req: REQ-LOAD
@cocotb.test()
async def test_idle_until_run(dut):
    """Nothing executes while run is low, and a second load after rst
    starts again at address 0."""
    bench = Bench(dut)
    await bench.start()
    await bench.reset()
    await bench.load(to_bytes([iop("addi", 1, 0, 5), EBREAK]))
    for _ in range(20):
        await FallingEdge(dut.clk)
        assert int(dut.halted.value) == 0, "ran with run low"
    await bench.reset()
    await bench.load(to_bytes([iop("addi", 1, 0, 9)]))
    await bench.run_until_halt(16)
    assert await bench.read_reg(1) == 9
    bench.regs[1] = 9
    bench.mem[0:8] = to_bytes([iop("addi", 1, 0, 9), EBREAK])


# req: REQ-ALU
# req: REQ-UPPER
# req: REQ-X0
@cocotb.test()
async def test_random_alu_programs(dut):
    bench = Bench(dut)
    await bench.start()
    rng = random.Random(2)
    for n in range(28):
        words = straight_line(rng, bench.regs, n_ops=rng.randrange(14, 22),
                              n_seed=rng.randrange(2, 5))
        await bench.run(words, what=f"alu program {n}")


# req: REQ-ALU
@cocotb.test()
async def test_alu_directed(dut):
    bench = Bench(dut)
    await bench.start()
    cases = [(0x7FFFFFFF, 1), (0x80000000, 0xFFFFFFFF), (5, 7), (7, 5),
             (0xFFFFFFFE, 0xFFFFFFFE), (0, 0x80000000), (0x0F0F0F0F, 36)]
    for a, b in cases:
        words = li(1, a) + li(2, b)
        rd = 3
        for name in rv.R_OPS:
            if name == "sra" and a >> 31:
                continue
            words.append(rop(name, rd, 1, 2))
            rd = rd + 1 if rd < 15 else 3
        words.append(EBREAK)
        await bench.run(words, what=f"r-ops {a:#x},{b:#x}")
    for imm in (-2048, -1, 0, 1, 2047):
        words = li(1, 0x00000800) + li(2, 0xFFFFF7FF)
        rd = 3
        for name in rv.I_OPS:
            words.append(iop(name, rd, 1, imm))
            words.append(iop(name, rd + 6, 2, imm))
            rd += 1
        words.append(EBREAK)
        await bench.run(words, what=f"i-ops imm={imm}")


# req: REQ-UPPER
@cocotb.test()
async def test_lui_auipc(dut):
    bench = Bench(dut)
    await bench.start()
    words = [rv.lui(1, 0xFFFFF), rv.lui(2, 0x00001), rv.auipc(3, 0),
             rv.auipc(4, 0x80000), iop("addi", 0, 0, 0), rv.auipc(5, 0xFFFFF),
             EBREAK]
    model, _ = await bench.run(words, what="lui/auipc")
    assert model.regs[1:6] == [0xFFFFF000, 0x1000, 8, 0x8000000C, 0xFFFFF014]


# req: REQ-BRANCH
@cocotb.test()
async def test_branches(dut):
    bench = Bench(dut)
    await bench.start()
    pairs = [(5, 5), (5, 6), (6, 5), (0xFFFFFFFF, 1), (1, 0xFFFFFFFF),
             (0x80000000, 0x7FFFFFFF), (0xFFFFFFFD, 0xFFFFFFFD), (0, 0)]
    for name in rv.BRANCHES:
        for a, b in pairs:
            words = li(1, a) + li(2, b) + [
                iop("addi", 3, 0, 0),
                branch(name, 1, 2, 8),       # over the next instruction
                iop("addi", 3, 0, 1),
                iop("addi", 4, 3, 7),
                EBREAK]
            model, _ = await bench.run(words, what=f"{name} {a:#x},{b:#x}")
            taken = {"beq": a == b, "bne": a != b,
                     "blt": rv.signed(a) < rv.signed(b),
                     "bge": rv.signed(a) >= rv.signed(b),
                     "bltu": a < b, "bgeu": a >= b}[name]
            assert model.regs[3] == (0 if taken else 1)


# req: REQ-BRANCH
@cocotb.test()
async def test_loop_sum(dut):
    """sum 1..10 with a backward branch."""
    bench = Bench(dut)
    await bench.start()
    words = [iop("addi", 1, 0, 10),        # 0: i = 10
             iop("addi", 2, 0, 0),         # 1: s = 0
             rop("add", 2, 2, 1),          # 2: s += i
             iop("addi", 1, 1, -1),        # 3: i -= 1
             branch("bne", 1, 0, -8),      # 4: loop to 2
             EBREAK]
    model, _ = await bench.run(words, what="sum loop")
    assert model.regs[2] == 55


# req: REQ-JUMP
@cocotb.test()
async def test_jumps(dut):
    bench = Bench(dut)
    await bench.start()
    words = [jal(1, 12),                  # 0: call 3, x1 = 4
             EBREAK,                      # 1:
             EBREAK,                      # 2:
             iop("addi", 5, 0, 44),       # 3: x5 = 44 (address of word 11)
             iop("addi", 5, 5, 1),        # 4: set bit 0: jalr clears it
             jalr(6, 5, 0),               # 5: to 11, x6 = 24
             EBREAK,                      # 6:
             EBREAK, EBREAK, EBREAK, EBREAK,
             iop("addi", 7, 0, 3),        # 11:
             jalr(8, 1, 0),               # 12: return to 1, x8 = 52
             EBREAK]
    model, _ = await bench.run(words, what="jal/jalr")
    assert model.regs[1] == 4 and model.regs[6] == 24 and model.regs[8] == 52
    # jal x0 backwards, and jalr with rd == rs1
    words = [jal(0, 16),                  # 0: to 4
             EBREAK,                      # 1:
             iop("addi", 9, 0, 2),        # 2:
             EBREAK,                      # 3:
             iop("addi", 2, 0, 8),        # 4: x2 = 8
             jalr(2, 2, 0),               # 5: to 2, x2 = 24
             EBREAK]
    model, _ = await bench.run(words, what="jal back/jalr rd=rs1")
    assert model.regs[2] == 24 and model.regs[9] == 2


# req: REQ-JUMP
# req: REQ-UPPER
@cocotb.test()
async def test_top_of_memory_wraps(dut):
    """pc wraps from word 31 to word 0, and a link taken at word 31 is 128."""
    bench = Bench(dut)
    await bench.start()
    words = [jal(0, 116), EBREAK] + [EBREAK] * 27 + [
        iop("addi", 4, 0, 1),     # 29
        rv.auipc(5, 0),           # 30: x5 = 120
        jal(6, 8)]                # 31: to word 1 (wraps), x6 = 128
    model, _ = await bench.run(words, what="wrap")
    assert model.regs[5] == 120 and model.regs[6] == 128
    # falling through word 31 lands on word 0 (x8 is 0 after start())
    words = [branch("bne", 8, 0, 8),       # 0: second pass -> 2
             jal(0, 120),                  # 1: first pass -> 31
             iop("addi", 7, 0, 5),         # 2:
             EBREAK] + [EBREAK] * 27 + [
             iop("addi", 8, 0, 6)]         # 31: then on to word 0
    model, _ = await bench.run(words, what="fall-through")
    assert model.regs[7] == 5 and model.regs[8] == 6


# req: REQ-MEM
@cocotb.test()
async def test_random_loads_stores(dut):
    bench = Bench(dut)
    await bench.start()
    rng = random.Random(3)
    for n in range(16):
        words = []
        for r in (2, 3, 4, 5):
            words += li(r, rand_value(rng))
        words += li(1, 112)                      # base: data area 96..127
        body = []
        for _ in range(11):
            if rng.random() < 0.5:
                name = rng.choice(list(rv.STORES))
                body.append(store(name, rng.randrange(2, 6), 1,
                                  rng.randrange(-16, 16)))
            else:
                name = rng.choice(list(rv.LOADS))
                # loads may also reach below the data area, and wrap
                base = rng.choice([1, 1, 1, 0])
                imm = rng.randrange(-16, 16) if base else rng.randrange(-2048, 2048)
                body.append(load(name, rng.randrange(6, 16), base, imm))
        words += body + [EBREAK]
        data = {96: bytes(rng.getrandbits(8) for _ in range(32))}
        await bench.run(words, data=data, check_mem=True,
                        what=f"mem program {n}")


# req: REQ-MEM
@cocotb.test()
async def test_sign_extension_and_lanes(dut):
    bench = Bench(dut)
    await bench.start()
    words = [iop("addi", 1, 0, 96)]
    for off in range(4):
        words += [load("lb", 2 + off, 1, off), load("lbu", 6 + off, 1, off)]
    words += [load("lh", 10, 1, 0), load("lh", 11, 1, 2),
              load("lhu", 12, 1, 3), load("lw", 13, 1, 1),
              store("sb", 13, 1, 9), store("sh", 13, 1, 14),
              store("sh", 13, 1, 17), store("sw", 13, 1, 23),
              EBREAK]
    data = {96: bytes([0x80, 0x7F, 0xFF, 0x01, 0, 0, 0, 0,
                       0, 0, 0, 0, 0, 0, 0, 0,
                       0, 0, 0, 0, 0, 0, 0, 0])}
    model, _ = await bench.run(words, data=data, check_mem=True,
                               what="lanes")
    assert model.regs[2] == 0xFFFFFF80 and model.regs[6] == 0x80
    assert model.regs[11] == 0x000001FF and model.regs[12] == 0x000001FF


# req: REQ-MEM
@cocotb.test()
async def test_programs(dut):
    """Fibonacci into memory, and a byte copy."""
    bench = Bench(dut)
    await bench.start()
    fib = [iop("addi", 1, 0, 0),           # 0: a = 0
           iop("addi", 2, 0, 1),           # 1: b = 1
           iop("addi", 3, 0, 80),          # 2: p = 80
           iop("addi", 4, 0, 12),          # 3: n = 12
           store("sw", 1, 3, 0),           # 4: *p = a
           rop("add", 5, 1, 2),            # 5: t = a + b
           iop("addi", 1, 2, 0),           # 6: a = b
           iop("addi", 2, 5, 0),           # 7: b = t
           iop("addi", 3, 3, 4),           # 8: p += 4
           iop("addi", 4, 4, -1),          # 9: n -= 1
           branch("blt", 0, 4, -24),       # 10: while n > 0 -> 4
           EBREAK]
    model, _ = await bench.run(fib, check_mem=True, what="fibonacci")
    got = [int.from_bytes(model.mem[80 + 4 * i:84 + 4 * i], "little")
           for i in range(12)]
    assert got == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]
    copy = [iop("addi", 1, 0, 64),         # 0: src
            iop("addi", 2, 0, 96),         # 1: dst
            iop("addi", 3, 0, 20),         # 2: count
            load("lbu", 4, 1, 0),          # 3:
            store("sb", 4, 2, 0),          # 4:
            iop("addi", 1, 1, 1),          # 5:
            iop("addi", 2, 2, 1),          # 6:
            iop("addi", 3, 3, -1),         # 7:
            branch("bne", 3, 0, -20),      # 8: -> 3
            EBREAK]
    src = bytes(range(200, 220))
    model, _ = await bench.run(copy, data={64: src}, check_mem=True,
                               what="byte copy")
    assert bytes(model.mem[96:116]) == src


# req: REQ-HALT
@cocotb.test()
async def test_halt_is_final(dut):
    bench = Bench(dut)
    await bench.start()
    for stop in (EBREAK, ECALL):
        model, _ = await bench.run([iop("addi", 1, 0, 3), stop,
                                    iop("addi", 1, 0, 4), EBREAK],
                                   what=f"halt on {stop:#x}")
        assert model.regs[1] == 3
        assert int(dut.illegal.value) == 0
        before = await bench.read_mem()
        dut.run.value = 1
        for i in range(24):
            dut.we.value = 1
            dut.din.value = (i * 37) & 0xFF
            await FallingEdge(dut.clk)
            assert int(dut.halted.value) == 1, "left the halt without rst"
        dut.we.value = 0
        dut.run.value = 0
        assert await bench.read_mem() == before, "loader wrote while halted"
        assert await bench.read_reg(1) == 3


# req: REQ-ILLEGAL
@cocotb.test()
async def test_illegal_instructions(dut):
    bench = Bench(dut)
    await bench.start()
    bad = [0x00000000, 0xFFFFFFFF, 0x0000000F,            # fence-ish, junk
           0x30200073, 0x00200073, 0x00001073,            # mret, csr
           rop("add", 16, 1, 2), rop("add", 1, 17, 2), rop("add", 1, 2, 31),
           rv.r_type(0x20, 2, 1, 4, 3), rv.r_type(0x01, 2, 1, 0, 3),
           rv.i_type(0x400 | 3, 1, 1, 3, 0b0010011),      # slli, funct7 != 0
           rv.i_type(0x200 | 3, 1, 5, 3, 0b0010011),      # srli, bad funct7
           rv.i_type(0, 16, 0, 3, 0b0010011),             # addi rs1=x16
           rv.i_type(0, 1, 0, 20, 0b0010011),             # addi rd=x20
           rv.i_type(0, 1, 3, 3, 0b0000011),              # ld (rv64)
           rv.i_type(0, 1, 6, 3, 0b0000011),              # lwu (rv64)
           rv.i_type(0, 18, 2, 3, 0b0000011),             # lw rs1=x18
           rv.i_type(0, 1, 2, 19, 0b0000011),             # lw rd=x19
           rv.s_type(0, 2, 1, 3), rv.s_type(0, 2, 1, 4),  # sd, bad funct3
           rv.s_type(0, 20, 1, 2), rv.s_type(0, 2, 20, 2),
           rv.b_type(8, 2, 1, 2), rv.b_type(8, 2, 1, 3),  # no such branch
           rv.b_type(8, 21, 1, 0), rv.b_type(8, 1, 22, 0),
           rv.i_type(0, 1, 1, 3, 0b1100111),              # jalr funct3 != 0
           rv.i_type(0, 23, 0, 3, 0b1100111), rv.i_type(0, 1, 0, 24, 0b1100111),
           rv.u_type(1, 25, 0b0110111), rv.u_type(1, 26, 0b0010111),
           rv.j_type(8, 27)]
    for word in bad:
        assert not rv.legal(word), f"{word:#010x} is legal in the model"
        model, _ = await bench.run([iop("addi", 1, 0, 11), word,
                                    iop("addi", 1, 0, 22), EBREAK],
                                   what=f"illegal {word:#010x}")
        assert model.illegal and model.regs[1] == 11
        assert int(dut.illegal.value) == 1
    # rst clears illegal, and the next legal program ends with it low
    await bench.run([iop("addi", 1, 0, 1), EBREAK], what="after illegal")
    assert int(dut.illegal.value) == 0
