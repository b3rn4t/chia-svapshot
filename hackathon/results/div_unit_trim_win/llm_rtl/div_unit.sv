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
exe_wb_scalar_instr_t instruction_d[1:0];
exe_wb_scalar_instr_t instruction_q[1:0];
logic div_zero_d[1:0];
logic div_zero_q[1:0];

bus64_t remanent_out[1:0];
bus64_t dividend_quotient_out[1:0];
bus64_t divisor_out[1:0];
bus64_t remanent_q[1:0];
bus64_t dividend_quotient_q[1:0];
bus64_t divisor_q[1:0];
bus64_t dividend_d;
bus64_t divisor_d;
    
bus64_t quo0;
bus64_t quo1;

logic [5:0] cycles_counter[1:0];

assign data_src1 = instruction_i.data_rs1;
assign data_src2 = instruction_i.data_rs2;

//--------------------------------------------------------------------------------------------------
//----- INPUT LOGIC  -------------------------------------------------------------------------------
//--------------------------------------------------------------------------------------------------

    always_comb begin
        for (int i = 1; i >= 0; i--) begin
            instruction_d[i] = instruction_q[i];
            div_zero_d[i]    = div_zero_q[i];
        end
        if (instruction_i.instr.valid & (instruction_i.instr.unit == UNIT_DIV)) begin
            instruction_d[div_unit_sel_i].ex              = '0; // Divisions can not generate exceptions
            instruction_d[div_unit_sel_i].valid           = instruction_i.instr.valid & (instruction_i.instr.unit == UNIT_DIV);
            instruction_d[div_unit_sel_i].pc              = instruction_i.instr.pc;
            instruction_d[div_unit_sel_i].bpred           = instruction_i.instr.bpred;
            instruction_d[div_unit_sel_i].rs1             = instruction_i.instr.rs1;
            instruction_d[div_unit_sel_i].rd              = instruction_i.instr.rd;
            instruction_d[div_unit_sel_i].regfile_we      = instruction_i.instr.regfile_we;
            instruction_d[div_unit_sel_i].instr_type      = instruction_i.instr.instr_type;
            instruction_d[div_unit_sel_i].stall_csr_fence = instruction_i.instr.stall_csr_fence;
            instruction_d[div_unit_sel_i].csr_addr        = instruction_i.instr.imm[CSR_ADDR_SIZE-1:0];
            instruction_d[div_unit_sel_i].prd             = instruction_i.prd;
            instruction_d[div_unit_sel_i].checkpoint_done = instruction_i.checkpoint_done;
            instruction_d[div_unit_sel_i].chkp            = instruction_i.chkp;
            instruction_d[div_unit_sel_i].gl_index        = instruction_i.gl_index;
            instruction_d[div_unit_sel_i].branch_taken    = 1'b0;
            instruction_d[div_unit_sel_i].result_pc       = '0; // Unused
            instruction_d[div_unit_sel_i].mem_type        = instruction_i.instr.mem_type;
            instruction_d[div_unit_sel_i].vl              = instruction_i.instr.vl;
            instruction_d[div_unit_sel_i].sew             = instruction_i.instr.sew;
            `ifdef SIM_KONATA_DUMP
            instruction_d[div_unit_sel_i].id              = instruction_i.instr.id;
            `endif

            div_zero_d[div_unit_sel_i]     = (~(|data_src2));
        end 
    end

    // Only unsigned 64-bit division is supported
    assign dividend_d = data_src1;
    assign divisor_d  = data_src2;


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


    // Registers
    always_ff @(posedge clk_i, negedge rstn_i) begin
        if (~rstn_i) begin
            for (int i = 0; i <= 1; i++) begin
                instruction_q[i]        <= '0;
                div_zero_q[i]           <= 1'b0;
                remanent_q[i]           <= '0;
                dividend_quotient_q[i]  <= '0;
                divisor_q[i]            <= '0;
                cycles_counter[i]       <= 6'd0;
            end
        end else if (flush_div_i) begin
            for (int i = 0; i <= 1; i++) begin
                instruction_q[i].valid  <= 1'b0;
                div_zero_q[i]           <= 1'b0;
                remanent_q[i]           <= '0;
                dividend_quotient_q[i]  <= '0;
                divisor_q[i]            <= '0;
                cycles_counter[i]       <= 6'd0;
            end       
        end else if (instruction_i.instr.valid & (instruction_i.instr.unit == UNIT_DIV)) begin
            instruction_q[div_unit_sel_i]        <= instruction_d[div_unit_sel_i];
            div_zero_q[div_unit_sel_i]           <= div_zero_d[div_unit_sel_i];

            remanent_q[div_unit_sel_i]           <= '0;
            dividend_quotient_q[div_unit_sel_i]  <= dividend_d;
            divisor_q[div_unit_sel_i]            <= divisor_d;
            
            cycles_counter[div_unit_sel_i]        <= 6'd33; // 64-bit division
            
            instruction_q[~div_unit_sel_i]        <= instruction_d[~div_unit_sel_i];
            div_zero_q[~div_unit_sel_i]           <= div_zero_d[~div_unit_sel_i];

            remanent_q[~div_unit_sel_i]           <= remanent_out[~div_unit_sel_i];
            dividend_quotient_q[~div_unit_sel_i]  <= dividend_quotient_out[~div_unit_sel_i];
            divisor_q[~div_unit_sel_i]            <= divisor_out[~div_unit_sel_i];
            
            if (cycles_counter[~div_unit_sel_i] != 6'd0) begin
                cycles_counter[~div_unit_sel_i]   <= cycles_counter[~div_unit_sel_i] - 6'd1;
            end
        end else begin
            for (int i = 1; i >= 0; i--) begin
                instruction_q[i]        <= instruction_d[i];
                div_zero_q[i]           <= div_zero_d[i];

                remanent_q[i]           <= remanent_out[i];
                dividend_quotient_q[i]  <= dividend_quotient_out[i];
                divisor_q[i]            <= divisor_out[i];
                
                if (cycles_counter[i] != 6'd0) begin
                    cycles_counter[i]   <= cycles_counter[i] - 6'd1;
                end
            end
        end
    end


//--------------------------------------------------------------------------------------------------
//----- OUTPUT INSTRUCTION -------------------------------------------------------------------------
//--------------------------------------------------------------------------------------------------
                    
    assign quo0 = (div_zero_q[0]) ? 64'hFFFFFFFFFFFFFFFF : dividend_quotient_q[0];
    assign quo1 = (div_zero_q[1]) ? 64'hFFFFFFFFFFFFFFFF : dividend_quotient_q[1];

    always_comb begin
        instruction_o = '0;
        instruction_o.instr_type = ADD;
        instruction_o.mem_type   = NOT_MEM;
        instruction_o.sew        = SEW_8;

        if (cycles_counter[0] == 6'd1) begin
            instruction_o.valid           = instruction_q[0].valid;
            instruction_o.pc              = instruction_q[0].pc;
            instruction_o.bpred           = instruction_q[0].bpred;
            instruction_o.rs1             = instruction_q[0].rs1;
            instruction_o.rd              = instruction_q[0].rd;
            instruction_o.regfile_we      = instruction_q[0].regfile_we;
            instruction_o.instr_type      = instruction_q[0].instr_type;
            instruction_o.stall_csr_fence = instruction_q[0].stall_csr_fence;
            instruction_o.csr_addr        = instruction_q[0].csr_addr;
            instruction_o.prd             = instruction_q[0].prd;
            instruction_o.checkpoint_done = instruction_q[0].checkpoint_done;
            instruction_o.chkp            = instruction_q[0].chkp;
            instruction_o.gl_index        = instruction_q[0].gl_index;
            instruction_o.mem_type        = instruction_q[0].mem_type;
            instruction_o.vl              = instruction_q[0].vl;
            instruction_o.lmul            = instruction_q[0].lmul;
            instruction_o.sew             = instruction_q[0].sew;
            `ifdef SIM_KONATA_DUMP
            instruction_o.id              = instruction_q[0].id;
            `endif
            instruction_o.ex              = instruction_q[0].ex;

            case(instruction_q[0].instr_type)
                DIV,DIVU,DIVW,DIVUW: begin
                    instruction_o.result = quo0;
                end
                default: begin
                    instruction_o.result = '0;
                end
            endcase
        end 
        else if (cycles_counter[1] == 6'd1) begin
            instruction_o.valid           = instruction_q[1].valid;
            instruction_o.pc              = instruction_q[1].pc;
            instruction_o.bpred           = instruction_q[1].bpred;
            instruction_o.rs1             = instruction_q[1].rs1;
            instruction_o.rd              = instruction_q[1].rd;
            instruction_o.regfile_we      = instruction_q[1].regfile_we;
            instruction_o.instr_type      = instruction_q[1].instr_type;
            instruction_o.stall_csr_fence = instruction_q[1].stall_csr_fence;
            instruction_o.csr_addr        = instruction_q[1].csr_addr;
            instruction_o.prd             = instruction_q[1].prd;
            instruction_o.checkpoint_done = instruction_q[1].checkpoint_done;
            instruction_o.chkp            = instruction_q[1].chkp;
            instruction_o.gl_index        = instruction_q[1].gl_index;
            instruction_o.mem_type        = instruction_q[1].mem_type;
            instruction_o.vl              = instruction_q[1].vl;
            instruction_o.lmul            = instruction_q[1].lmul;
            instruction_o.sew             = instruction_q[1].sew;
            `ifdef SIM_KONATA_DUMP
            instruction_o.id              = instruction_q[1].id;
            `endif
            instruction_o.ex              = instruction_q[1].ex;

            case(instruction_q[1].instr_type)
                DIV,DIVU,DIVW,DIVUW: begin
                    instruction_o.result = quo1;
                end
                default: begin
                    instruction_o.result = '0;
                end
            endcase
        end
    end

endmodule
