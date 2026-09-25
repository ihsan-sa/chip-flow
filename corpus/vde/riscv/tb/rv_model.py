"""Reference model and a tiny assembler for rv_core (spec.md). Pure Python,
no cocotb: tb/test_rv_core.py runs every program here first and compares
the core's registers and memory against this model's afterwards.

The model follows spec.md literally: 15 registers plus x0, 128 bytes of
memory with addresses taken modulo 128, pc as a word index modulo 32, the
low two bits of a jump or branch target dropped, and the RV32E decode rules
(a register field of 16 or more is illegal)."""
MASK = 0xFFFFFFFF
MEM_BYTES = 128
MEM_WORDS = MEM_BYTES // 4


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def signed(value):
    return sext(value, 32)


# ---- encoders -----------------------------------------------------------
def r_type(f7, rs2, rs1, f3, rd, op=0b0110011):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def i_type(imm, rs1, f3, rd, op):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def s_type(imm, rs2, rs1, f3, op=0b0100011):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) \
        | ((imm & 0x1F) << 7) | op


def b_type(imm, rs2, rs1, f3, op=0b1100011):
    imm &= 0x1FFF
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) \
        | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (((imm >> 1) & 0xF) << 8) \
        | (((imm >> 11) & 1) << 7) | op


def u_type(imm20, rd, op):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | op


def j_type(imm, rd, op=0b1101111):
    imm &= 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) \
        | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) \
        | (rd << 7) | op


R_OPS = {"add": (0, 0), "sub": (0x20, 0), "sll": (0, 1), "slt": (0, 2),
         "sltu": (0, 3), "xor": (0, 4), "srl": (0, 5), "sra": (0x20, 5),
         "or": (0, 6), "and": (0, 7)}
I_OPS = {"addi": 0, "slti": 2, "sltiu": 3, "xori": 4, "ori": 6, "andi": 7}
SHIFT_I = {"slli": (0, 1), "srli": (0, 5), "srai": (0x20, 5)}
BRANCHES = {"beq": 0, "bne": 1, "blt": 4, "bge": 5, "bltu": 6, "bgeu": 7}
LOADS = {"lb": 0, "lh": 1, "lw": 2, "lbu": 4, "lhu": 5}
STORES = {"sb": 0, "sh": 1, "sw": 2}

EBREAK = 0x00100073
ECALL = 0x00000073


def rop(name, rd, rs1, rs2):
    f7, f3 = R_OPS[name]
    return r_type(f7, rs2, rs1, f3, rd)


def iop(name, rd, rs1, imm):
    if name in SHIFT_I:
        f7, f3 = SHIFT_I[name]
        return i_type((f7 << 5) | (imm & 0x1F), rs1, f3, rd, 0b0010011)
    return i_type(imm, rs1, I_OPS[name], rd, 0b0010011)


def lui(rd, imm20):
    return u_type(imm20, rd, 0b0110111)


def auipc(rd, imm20):
    return u_type(imm20, rd, 0b0010111)


def jal(rd, offset):
    return j_type(offset, rd)


def jalr(rd, rs1, imm):
    return i_type(imm, rs1, 0, rd, 0b1100111)


def branch(name, rs1, rs2, offset):
    return b_type(offset, rs2, rs1, BRANCHES[name])


def load(name, rd, rs1, imm):
    return i_type(imm, rs1, LOADS[name], rd, 0b0000011)


def store(name, rs2, rs1, imm):
    return s_type(imm, rs2, rs1, STORES[name])


def li(rd, value):
    """lui+addi pair that leaves `value` (any 32-bit) in rd."""
    value &= MASK
    lo = sext(value, 12)
    hi = ((value - lo) >> 12) & 0xFFFFF
    return [lui(rd, hi), iop("addi", rd, rd, lo)]


def to_bytes(words):
    out = bytearray()
    for w in words:
        out += (w & MASK).to_bytes(4, "little")
    return bytes(out)


# ---- the model ----------------------------------------------------------
class Model:
    def __init__(self, image=b""):
        self.mem = bytearray(MEM_BYTES)
        self.mem[:len(image)] = image[:MEM_BYTES]
        self.regs = [0] * 16
        self.pc = 0          # word index
        self.halted = False
        self.illegal = False
        self.steps = 0

    def word(self, index):
        i = (index % MEM_WORDS) * 4
        return int.from_bytes(self.mem[i:i + 4], "little")

    def _set(self, rd, value):
        if rd:
            self.regs[rd] = value & MASK

    def step(self):
        ir = self.word(self.pc)
        op, rd, f3 = ir & 0x7F, (ir >> 7) & 0x1F, (ir >> 12) & 7
        rs1, rs2, f7 = (ir >> 15) & 0x1F, (ir >> 20) & 0x1F, ir >> 25
        if not legal(ir):
            self.halted = self.illegal = True
            return
        if op == 0b1110011:
            self.halted = True
            return
        # rs2 is part of the immediate for I-type and U/J-type words
        a, b = self.regs[rs1 & 15], self.regs[rs2 & 15]
        imm_i = sext(ir >> 20, 12)
        imm_s = sext(((ir >> 25) << 5) | ((ir >> 7) & 0x1F), 12)
        pc_byte = self.pc * 4
        next_pc = (self.pc + 1) % MEM_WORDS
        if op == 0b0110111:
            self._set(rd, ir & 0xFFFFF000)
        elif op == 0b0010111:
            self._set(rd, (ir & 0xFFFFF000) + pc_byte)
        elif op == 0b1101111:
            imm = sext((((ir >> 31) & 1) << 20) | (((ir >> 12) & 0xFF) << 12)
                       | (((ir >> 20) & 1) << 11) | (((ir >> 21) & 0x3FF) << 1), 21)
            self._set(rd, pc_byte + 4)
            next_pc = ((pc_byte + imm) & MASK) >> 2 & (MEM_WORDS - 1)
        elif op == 0b1100111:
            target = (a + imm_i) & MASK & ~1
            self._set(rd, pc_byte + 4)
            next_pc = (target >> 2) & (MEM_WORDS - 1)
        elif op == 0b1100011:
            take = {0: a == b, 1: a != b, 4: signed(a) < signed(b),
                    5: signed(a) >= signed(b), 6: a < b, 7: a >= b}[f3]
            if take:
                imm = sext((((ir >> 31) & 1) << 12) | (((ir >> 7) & 1) << 11)
                           | (((ir >> 25) & 0x3F) << 5) | (((ir >> 8) & 0xF) << 1), 13)
                next_pc = ((pc_byte + imm) & MASK) >> 2 & (MEM_WORDS - 1)
        elif op == 0b0000011:
            addr = (a + imm_i) % MEM_BYTES
            w = self.word(addr >> 2)
            byte = (w >> (8 * (addr & 3))) & 0xFF
            half = (w >> 16) if addr & 2 else (w & 0xFFFF)
            self._set(rd, {0: sext(byte, 8), 1: sext(half, 16), 2: w,
                           4: byte, 5: half}[f3])
        elif op == 0b0100011:
            addr = (a + imm_s) % MEM_BYTES
            if f3 == 0:
                self.mem[addr] = b & 0xFF
            elif f3 == 1:
                base = addr & ~1
                self.mem[base:base + 2] = (b & 0xFFFF).to_bytes(2, "little")
            else:
                base = addr & ~3
                self.mem[base:base + 4] = b.to_bytes(4, "little")
        else:  # op-imm / op
            is_op = op == 0b0110011
            y = b if is_op else imm_i & MASK
            sh = y & 0x1F
            alt = bool(f7 & 0x20)
            if f3 == 0:
                r = a - y if (is_op and alt) else a + y
            elif f3 == 1:
                r = a << sh
            elif f3 == 2:
                r = int(signed(a) < signed(y))
            elif f3 == 3:
                r = int(a < (y & MASK))
            elif f3 == 4:
                r = a ^ y
            elif f3 == 5:
                r = (signed(a) >> sh) if alt else (a >> sh)
            elif f3 == 6:
                r = a | y
            else:
                r = a & y
            self._set(rd, r)
        self.pc = next_pc

    def run(self, max_steps=2000):
        while not self.halted and self.steps < max_steps:
            self.step()
            self.steps += 1
        return self


def legal(ir):
    op, rd, f3 = ir & 0x7F, (ir >> 7) & 0x1F, (ir >> 12) & 7
    rs1, rs2, f7 = (ir >> 15) & 0x1F, (ir >> 20) & 0x1F, ir >> 25
    if op in (0b0110111, 0b0010111, 0b1101111):
        return rd < 16
    if op == 0b1100111:
        return rd < 16 and rs1 < 16 and f3 == 0
    if op == 0b1100011:
        return rs1 < 16 and rs2 < 16 and f3 not in (2, 3)
    if op == 0b0000011:
        return rd < 16 and rs1 < 16 and f3 in (0, 1, 2, 4, 5)
    if op == 0b0100011:
        return rs1 < 16 and rs2 < 16 and f3 in (0, 1, 2)
    if op == 0b0010011:
        if rd >= 16 or rs1 >= 16:
            return False
        if f3 == 1:
            return f7 == 0
        if f3 == 5:
            return f7 in (0, 0x20)
        return True
    if op == 0b0110011:
        return rd < 16 and rs1 < 16 and rs2 < 16 and (
            f7 == 0 or (f7 == 0x20 and f3 in (0, 5)))
    if op == 0b1110011:
        return ir in (ECALL, EBREAK)
    return False
