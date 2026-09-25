module div_unit 
    import drac_pkg::*;
    import riscv_pkg::*;
(
    input  logic                 clk_i,          // Clock signal
    input  logic                 rstn_i,         // Negative reset  
    input  logic                 flush_div_i,     // Kill on fly instructions
    input  logic                 div_unit_sel_i, // Select divider module
    input  rr_exe_arith_instr_t  instruction_i,  // New incoming instruction
    output exe_wb_scalar_instr_t instruction_o   // Output instruction
);

    // Declarations
    bus64_t data_src1, data_src2;
    exe_wb_scalar_instr_t instruction_q[1:0];
    logic div_zero_q[1:0];
    logic same_sign_q[1:0];
    logic signed_op_q[1:0];
    logic op_32_q[1:0];

    bus64_t remanent_out[1:0];
    bus64_t dividend_quotient_out[1:0];
    bus64_t divisor_out[1:0];
    bus64_t remanent_q[1:0];
    bus64_t dividend_quotient_q[1:0];
    bus64_t divisor_q[1:0];
    
    logic [5:0] cycles_counter[1:0];

    assign data_src1 = instruction_i.data_rs1;
    assign data_src2 = instruction_i.data_rs2;

    //--------------------------------------------------------------------------------------------------
    //----- FIRST INSTRUCTION  -------------------------------------------------------------------------
    //--------------------------------------------------------------------------------------------------

    logic dividend_is_neg;
    logic divisor_is_neg;
    assign dividend_is_neg = instruction_i.instr.signed_op & (instruction_i.instr.op_32 ? data_src1[31] : data_src1[63]);
    assign divisor_is_neg  = instruction_i.instr.signed_op & (instruction_i.instr.op_32 ? data_src2[31] : data_src2[63]);

    bus64_t dividend_d;
    bus64_t divisor_d;
    assign dividend_d = dividend_is_neg ? (~data_src1 + 64'b1) : data_src1;
    assign divisor_d  = divisor_is_neg  ? (~data_src2 + 64'b1) : data_src2;

    logic div_zero_new;
    logic same_sign_new;
    assign div_zero_new  = instruction_i.instr.op_32 ? ~(|data_src2[31:0]) : ~(|data_src2);
    assign same_sign_new = instruction_i.instr.op_32 ? ~(data_src2[31] ^ data_src1[31]) : ~(data_src2[63] ^ data_src1[63]);

    logic [63:0] dividend_eff;
    logic [63:0] divisor_eff;
    assign dividend_eff = instruction_i.instr.op_32 ? {dividend_d[31:0], 32'b0} : dividend_d;
    assign divisor_eff  = instruction_i.instr.op_32 ? {32'b0, divisor_d[31:0]} : divisor_d;

    // Early-out optimization for PPA: skips cycles for div-by-0, div-by-1, or 0-dividend
    logic early_out;
    assign early_out = div_zero_new || (divisor_eff == 64'd1) || (dividend_eff == 64'd0);

    exe_wb_scalar_instr_t new_instr;
    always_comb begin
        new_instr = instruction_q[div_unit_sel_i];
        new_instr.ex              = '0; // Divisions can not generate exceptions
        new_instr.valid           = 1'b1;
        new_instr.pc              = instruction_i.instr.pc;
        new_instr.bpred           = instruction_i.instr.bpred;
        new_instr.rs1             = instruction_i.instr.rs1;
        new_instr.rd              = instruction_i.instr.rd;
        new_instr.regfile_we      = instruction_i.instr.regfile_we;
        new_instr.instr_type      = instruction_i.instr.instr_type;
        new_instr.stall_csr_fence = instruction_i.instr.stall_csr_fence;
        new_instr.csr_addr        = instruction_i.instr.imm[CSR_ADDR_SIZE-1:0];
        new_instr.prd             = instruction_i.prd;
        new_instr.checkpoint_done = instruction_i.checkpoint_done;
        new_instr.chkp            = instruction_i.chkp;
        new_instr.gl_index        = instruction_i.gl_index;
        new_instr.branch_taken    = 1'b0;
        new_instr.result_pc       = data_src1; // Store dividend in result_pc
        new_instr.mem_type        = instruction_i.instr.mem_type;
        new_instr.vl              = instruction_i.instr.vl;
        new_instr.sew             = instruction_i.instr.sew;
        `ifdef SIM_KONATA_DUMP
        new_instr.id              = instruction_i.instr.id;
        `endif
    end

    //--------------------------------------------------------------------------------------------------
    //----- PIPELINE -----------------------------------------------------------------------------------
    //--------------------------------------------------------------------------------------------------

    div_4bits div_4bits_int0 (
        .remanent_i(remanent_q[0]),
        .dividend_quotient_i(dividend_quotient_q[0]),
        .divisor_i(divisor_q[0]),
        .remanent_o(remanent_out[0]),
        .dividend_quotient_o(dividend_quotient_out[0]),
        .divisor_o(divisor_out[0])
    );
    
    div_4bits div_4bits_int1 (
        .remanent_i(remanent_q[1]),
        .dividend_quotient_i(dividend_quotient_q[1]),
        .divisor_i(divisor_q[1]),
        .remanent_o(remanent_out[1]),
        .dividend_quotient_o(dividend_quotient_out[1]),
        .divisor_o(divisor_out[1])
    );

    always_ff @(posedge clk_i or negedge rstn_i) begin
        if (~rstn_i) begin
            for (int i = 0; i <= 1; i++) begin
                instruction_q[i]        <= '0;
                div_zero_q[i]           <= 1'b0;
                same_sign_q[i]          <= 1'b0;
                op_32_q[i]              <= 1'b0;
                signed_op_q[i]          <= 1'b0;
                remanent_q[i]           <= '0;
                dividend_quotient_q[i]  <= '0;
                divisor_q[i]            <= '0;
                cycles_counter[i]       <= 6'd0;
            end
        end else if (flush_div_i) begin
            for (int i = 0; i <= 1; i++) begin
                instruction_q[i].valid  <= 1'b0;
                div_zero_q[i]           <= 1'b0;
                same_sign_q[i]          <= 1'b0;
                op_32_q[i]              <= 1'b0;
                signed_op_q[i]          <= 1'b0;
                remanent_q[i]           <= '0;
                dividend_quotient_q[i]  <= '0;
                divisor_q[i]            <= '0;
                cycles_counter[i]       <= 6'd0;
            end       
        end else begin
            for (int i = 0; i <= 1; i++) begin
                if (instruction_i.instr.valid & (instruction_i.instr.unit == UNIT_DIV) & (div_unit_sel_i == i[0])) begin
                    instruction_q[i]        <= new_instr;
                    div_zero_q[i]           <= div_zero_new;
                    same_sign_q[i]          <= same_sign_new;
                    op_32_q[i]              <= instruction_i.instr.op_32;
                    signed_op_q[i]          <= instruction_i.instr.signed_op;

                    remanent_q[i]           <= '0;
                    dividend_quotient_q[i]  <= dividend_eff;
                    divisor_q[i]            <= divisor_eff;
                    
                    cycles_counter[i]       <= early_out ? 6'd1 : (instruction_i.instr.op_32 ? 6'd17 : 6'd33);
                end else if (cycles_counter[i] != 6'd0) begin
                    // Clock gating optimization: only update pipeline registers when active
                    cycles_counter[i]       <= cycles_counter[i] - 6'd1;
                    remanent_q[i]           <= remanent_out[i];
                    dividend_quotient_q[i]  <= dividend_quotient_out[i];
                    divisor_q[i]            <= divisor_out[i];
                end
            end
        end
    end

    //--------------------------------------------------------------------------------------------------
    //----- OUTPUT INSTRUCTION -------------------------------------------------------------------------
    //--------------------------------------------------------------------------------------------------
    
    logic quo0_is_neg, quo1_is_neg;
    assign quo0_is_neg = signed_op_q[0] & ~same_sign_q[0];
    assign quo1_is_neg = signed_op_q[1] & ~same_sign_q[1];

    bus64_t quo0, quo1;
    assign quo0 = div_zero_q[0] ? 64'hFFFFFFFFFFFFFFFF : (quo0_is_neg ? (~dividend_quotient_q[0] + 64'b1) : dividend_quotient_q[0]);
    assign quo1 = div_zero_q[1] ? 64'hFFFFFFFFFFFFFFFF : (quo1_is_neg ? (~dividend_quotient_q[1] + 64'b1) : dividend_quotient_q[1]);

    logic rmd0_is_neg, rmd1_is_neg;
    assign rmd0_is_neg = signed_op_q[0] & (op_32_q[0] ? instruction_q[0].result_pc[31] : instruction_q[0].result_pc[63]);
    assign rmd1_is_neg = signed_op_q[1] & (op_32_q[1] ? instruction_q[1].result_pc[31] : instruction_q[1].result_pc[63]);

    bus64_t rmd0, rmd1;
    assign rmd0 = div_zero_q[0] ? instruction_q[0].result_pc : (rmd0_is_neg ? (~remanent_q[0] + 64'b1) : remanent_q[0]);
    assign rmd1 = div_zero_q[1] ? instruction_q[1].result_pc : (rmd1_is_neg ? (~remanent_q[1] + 64'b1) : remanent_q[1]);

    always_comb begin
        instruction_o = '0;
        instruction_o.instr_type = ADD;
        instruction_o.mem_type = NOT_MEM;
        instruction_o.sew = SEW_8;
        
        if (cycles_counter[0] == 6'd1) begin
            instruction_o = instruction_q[0];
            instruction_o.branch_taken = 1'b0;
            instruction_o.result_pc = '0;
            instruction_o.fp_status = '0;
            case(instruction_q[0].instr_type)
                DIV, DIVU, DIVW, DIVUW: instruction_o.result = op_32_q[0] ? {{32{quo0[31]}}, quo0[31:0]} : quo0;
                REM, REMU, REMW, REMUW: instruction_o.result = op_32_q[0] ? {{32{rmd0[31]}}, rmd0[31:0]} : rmd0;
                default: instruction_o.result = '0;
            endcase
        end else if (cycles_counter[1] == 6'd1) begin
            instruction_o = instruction_q[1];
            instruction_o.branch_taken = 1'b0;
            instruction_o.result_pc = '0;
            instruction_o.fp_status = '0;
            case(instruction_q[1].instr_type)
                DIV, DIVU, DIVW, DIVUW: instruction_o.result = op_32_q[1] ? {{32{quo1[31]}}, quo1[31:0]} : quo1;
                REM, REMU, REMW, REMUW: instruction_o.result = op_32_q[1] ? {{32{rmd1[31]}}, rmd1[31:0]} : rmd1;
                default: instruction_o.result = '0;
            endcase
        end
    end

endmodule
