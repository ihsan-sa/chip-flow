module rv_core (
    input  wire       clk,
    input  wire       rst,
    input  wire       we,
    input  wire       run,
    input  wire [7:0] din,
    output wire [7:0] dout,
    output wire       halted,
    output reg        illegal
);
  // A multi-cycle RV32E subset core (spec.md): 15 general registers plus a
  // hard-wired x0, one 32-word (128-byte) memory shared by code and data, a
  // byte-wide loader in front of it and a byte-wide debug read-back behind
  // it. Two clocks per instruction: FETCH latches the word at pc into ir,
  // EXEC decodes, computes and writes back.
  //
  // pc is a 5-bit WORD index, never a 32-bit byte address: the memory has
  // 32 words, so every higher pc bit would be a register that can never
  // change (dead toggle surface for the `cover` gate and dead area for the
  // tile). Byte-address values the ISA exposes (auipc, jal/jalr's link) are
  // rebuilt from it where they are written back.
  localparam S_IDLE  = 2'd0;
  localparam S_FETCH = 2'd1;
  localparam S_EXEC  = 2'd2;
  localparam S_HALT  = 2'd3;

  localparam OP_LUI    = 7'b0110111;
  localparam OP_AUIPC  = 7'b0010111;
  localparam OP_JAL    = 7'b1101111;
  localparam OP_JALR   = 7'b1100111;
  localparam OP_BRANCH = 7'b1100011;
  localparam OP_LOAD   = 7'b0000011;
  localparam OP_STORE  = 7'b0100011;
  localparam OP_IMM    = 7'b0010011;
  localparam OP_OP     = 7'b0110011;
  localparam OP_SYSTEM = 7'b1110011;

  reg [1:0]  state;
  reg [4:0]  pc;
  reg [6:0]  ld_ptr;
  reg [31:0] ir;
  reg [31:0] mem [0:31];
  reg [31:0] rf  [1:15];

  assign halted = (state == S_HALT);

  // ---- decode --------------------------------------------------------
  wire [6:0] opcode = ir[6:0];
  wire [4:0] rd     = ir[11:7];
  wire [2:0] f3     = ir[14:12];
  wire [4:0] rs1    = ir[19:15];
  wire [4:0] rs2    = ir[24:20];
  wire [6:0] f7     = ir[31:25];

  wire [31:0] a = (rs1[3:0] == 4'd0) ? 32'd0 : rf[rs1[3:0]];
  wire [31:0] b = (rs2[3:0] == 4'd0) ? 32'd0 : rf[rs2[3:0]];

  wire [31:0] imm_i = {{20{ir[31]}}, ir[31:20]};
  wire [31:0] imm_s = {{20{ir[31]}}, ir[31:25], ir[11:7]};
  // branch/jump offsets only ever move a 5-bit word pc, so only bits [6:2]
  // of the byte offset matter (bit 1 is dropped, spec.md "Addresses").
  wire [4:0]  off_b = {ir[26:25], ir[11:9]};
  wire [4:0]  off_j = ir[26:22];

  reg legal;
  always @* begin
    case (opcode)
      OP_LUI, OP_AUIPC, OP_JAL:
        legal = !rd[4];
      OP_JALR:
        legal = !rd[4] && !rs1[4] && f3 == 3'b000;
      OP_BRANCH:
        legal = !rs1[4] && !rs2[4] && f3[2:1] != 2'b01;
      OP_LOAD:
        legal = !rd[4] && !rs1[4] && f3 != 3'b011 && f3[2:1] != 2'b11;
      OP_STORE:
        legal = !rs1[4] && !rs2[4] && !f3[2] && f3[1:0] != 2'b11;
      OP_IMM:
        legal = !rd[4] && !rs1[4] &&
                (f3 == 3'b001 ? f7 == 7'b0000000 :
                 f3 == 3'b101 ? (f7 == 7'b0000000 || f7 == 7'b0100000) :
                 1'b1);
      OP_OP:
        legal = !rd[4] && !rs1[4] && !rs2[4] &&
                (f7 == 7'b0000000 ||
                 (f7 == 7'b0100000 && (f3 == 3'b000 || f3 == 3'b101)));
      OP_SYSTEM:
        legal = ir[31:21] == 11'd0 && ir[19:7] == 13'd0;  // ecall/ebreak
      default:
        legal = 1'b0;
    endcase
  end

  // ---- ALU: op/op-imm by funct3; loads, stores and jalr use it to add --
  wire        is_op   = (opcode == OP_OP);
  wire        use_f3  = is_op || (opcode == OP_IMM);
  wire [31:0] alu_b   = is_op ? b : (opcode == OP_STORE ? imm_s : imm_i);
  // a wire of its own: inside the ternary below, next to the unsigned
  // logical shift, `$signed(a) >>> n` would be evaluated unsigned.
  wire [31:0] sra   = $signed(a) >>> alu_b[4:0];
  reg  [31:0] alu_y;
  always @* begin
    case (use_f3 ? f3 : 3'b000)
      3'b000:  alu_y = (is_op && ir[30]) ? a - alu_b : a + alu_b;
      3'b001:  alu_y = a << alu_b[4:0];
      3'b010:  alu_y = {31'd0, $signed(a) < $signed(alu_b)};
      3'b011:  alu_y = {31'd0, a < alu_b};
      3'b100:  alu_y = a ^ alu_b;
      3'b101:  alu_y = ir[30] ? sra : a >> alu_b[4:0];
      3'b110:  alu_y = a | alu_b;
      default: alu_y = a & alu_b;
    endcase
  end

  reg take;
  always @* begin
    case (f3)
      3'b000:  take = (a == b);
      3'b001:  take = (a != b);
      3'b100:  take = ($signed(a) <  $signed(b));
      3'b101:  take = ($signed(a) >= $signed(b));
      3'b110:  take = (a <  b);
      default: take = (a >= b);
    endcase
  end

  // ---- loads and stores (addresses wrap at 128 bytes) -----------------
  wire [6:0]  addr    = alu_y[6:0];
  wire [31:0] word    = mem[addr[6:2]];
  wire [7:0]  ld_byte = word[{addr[1:0], 3'b000} +: 8];
  wire [15:0] ld_half = addr[1] ? word[31:16] : word[15:0];
  reg  [31:0] ld_val;
  always @* begin
    case (f3)
      3'b000:  ld_val = {{24{ld_byte[7]}}, ld_byte};
      3'b001:  ld_val = {{16{ld_half[15]}}, ld_half};
      3'b100:  ld_val = {24'd0, ld_byte};
      3'b101:  ld_val = {16'd0, ld_half};
      default: ld_val = word;
    endcase
  end

  // ---- one memory write port with byte lanes: the loader while idle,
  // stores in EXEC. Lane enables rather than a read-modify-write of the
  // whole word, so no extra 32-bit read port is spent on either writer.
  wire        exec_ok  = (state == S_EXEC) && legal;
  wire        ld_write = (state == S_IDLE) && we;
  wire        st_write = exec_ok && (opcode == OP_STORE);
  reg  [3:0]  st_lanes;
  reg  [31:0] st_data;
  always @* begin
    case (f3[1:0])
      2'b00: begin
        st_lanes = 4'b0001 << addr[1:0];
        st_data  = {4{b[7:0]}};
      end
      2'b01: begin
        st_lanes = addr[1] ? 4'b1100 : 4'b0011;
        st_data  = {2{b[15:0]}};
      end
      default: begin
        st_lanes = 4'b1111;
        st_data  = b;
      end
    endcase
  end

  wire [4:0]  wr_index = ld_write ? ld_ptr[6:2] : addr[6:2];
  wire [3:0]  wr_lanes = ld_write ? (4'b0001 << ld_ptr[1:0])
                       : (st_write ? st_lanes : 4'b0000);
  wire [31:0] wr_data  = ld_write ? {4{din}} : st_data;
  integer lane;
  always @(posedge clk)
    for (lane = 0; lane < 4; lane = lane + 1)
      if (wr_lanes[lane])
        mem[wr_index][8*lane +: 8] <= wr_data[8*lane +: 8];

  // ---- register write-back -------------------------------------------
  wire [5:0] pc_next = {1'b0, pc} + 6'd1;  // link: (pc + 4) as a word count
  reg        wb_en;
  reg [31:0] wb_val;
  always @* begin
    wb_en  = 1'b1;
    case (opcode)
      OP_LUI:           wb_val = {ir[31:12], 12'd0};
      OP_AUIPC:         wb_val = {ir[31:12], 5'd0, pc, 2'b00};
      OP_JAL, OP_JALR:  wb_val = {24'd0, pc_next, 2'b00};
      OP_LOAD:          wb_val = ld_val;
      OP_IMM, OP_OP:    wb_val = alu_y;
      default: begin
        wb_en  = 1'b0;
        wb_val = alu_y;
      end
    endcase
  end

  always @(posedge clk)
    if (exec_ok && wb_en && rd[3:0] != 4'd0)
      rf[rd[3:0]] <= wb_val;

  // ---- control ---------------------------------------------------------
  always @(posedge clk) begin
    if (rst) begin
      state   <= S_IDLE;
      pc      <= 5'd0;
      ld_ptr  <= 7'd0;
      illegal <= 1'b0;
    end else begin
      case (state)
        S_IDLE: begin
          if (we)
            ld_ptr <= ld_ptr + 7'd1;
          if (run)
            state <= S_FETCH;
        end
        S_FETCH: begin
          ir    <= mem[pc];
          state <= S_EXEC;
        end
        S_EXEC: begin
          if (!legal) begin
            illegal <= 1'b1;
            state   <= S_HALT;
          end else if (opcode == OP_SYSTEM) begin
            state <= S_HALT;
          end else begin
            state <= S_FETCH;
            case (opcode)
              OP_JAL:    pc <= pc + off_j;
              OP_JALR:   pc <= alu_y[6:2];
              OP_BRANCH: pc <= take ? pc + off_b : pc_next[4:0];
              default:   pc <= pc_next[4:0];
            endcase
          end
        end
        default: ;  // S_HALT: stays until rst
      endcase
    end
  end

  // ---- debug read-back while halted -----------------------------------
  // din[7]=1: memory byte din[1:0] of word din[6:2]; din[7]=0: byte din[1:0]
  // of register din[5:2] (din[6] ignored). Zero whenever not halted.
  wire [31:0] dbg_reg  = (din[5:2] == 4'd0) ? 32'd0 : rf[din[5:2]];
  wire [31:0] dbg_word = din[7] ? mem[din[6:2]] : dbg_reg;
  assign dout = halted ? dbg_word[{din[1:0], 3'b000} +: 8] : 8'h00;
endmodule
