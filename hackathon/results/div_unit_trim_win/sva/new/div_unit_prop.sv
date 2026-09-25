module div_unit_prop

// Automatically detected package imports
import drac_pkg::*;
import riscv_pkg::*;

#(
		parameter ASSERT_INPUTS = 0)
(
		input  logic                 clk_i,          // Clock signal
		input  logic                 rstn_i,         // Negative reset  
		input  logic                 flush_div_i,     // Kill on fly instructions
		input  logic                 div_unit_sel_i, // Select divider module
		input  rr_exe_arith_instr_t  instruction_i,  // New incoming instruction
		input  exe_wb_scalar_instr_t instruction_o, // Output instruction //output
		input logic [5:0] cycles_counter [1:0], // internal (density bind)
		input logic div_zero_q [1:0], // internal (density bind)
		input bus64_t dividend_quotient_q [1:0], // internal (density bind)
		input exe_wb_scalar_instr_t instruction_q [1:0], // internal (density bind)
		input logic op_32_q [1:0], // internal (density bind)
		input bus64_t remanent_q [1:0], // internal (density bind)
		input logic same_sign_q [1:0], // internal (density bind)
		input logic signed_op_q [1:0], // internal (density bind)
		input bus64_t dividend_d, // internal (density bind)
		input bus64_t dividend_quotient_out [1:0], // internal (density bind)
		input bus64_t divisor_d, // internal (density bind)
		input bus64_t divisor_out [1:0], // internal (density bind)
		input bus64_t divisor_q [1:0], // internal (density bind)
		input bus64_t quo0, // internal (density bind)
		input bus64_t quo1, // internal (density bind)
		input bus64_t remanent_out [1:0], // internal (density bind)
		input bus64_t rmd0, // internal (density bind)
		input bus64_t rmd1 // internal (density bind)
	);

//==============================================================================
// Local Parameters
//==============================================================================

genvar j;
default clocking cb @(posedge clk_i);
endclocking
default disable iff (!rstn_i);

// Re-defined wires 

// Symbolics and Handshake signals

//==============================================================================
// Modeling
//==============================================================================


//====DESIGNER-ADDED-SVA====//
a_flush_resets_counters: assert property ((flush_div_i |=> cycles_counter[0] == 0 && cycles_counter[1] == 0))
else begin
    $display("Flush did not reset pipeline counters to zero.");
end

a_output_valid_logic: assert property ((instruction_o.valid == (cycles_counter[0] == 1 || cycles_counter[1] == 1)))
else begin
    $display("Output valid signal is not asserted if and only if a pipeline is finishing.");
end

a_output_priority: assert property (((cycles_counter[0] == 1 && cycles_counter[1] == 1) |-> instruction_o.pc == instruction_q[0].pc))
else begin
    $display("Output MUX did not prioritize pipeline 0 when both pipelines finished simultaneously.");
end

a_output_metadata_p0: assert property (((cycles_counter[0] == 1) |-> instruction_o.pc == instruction_q[0].pc && instruction_o.rd == instruction_q[0].rd && instruction_o.instr_type == instruction_q[0].instr_type))
else begin
    $display("Output instruction metadata does not match the finishing instruction from pipeline 0.");
end

a_output_metadata_p1: assert property (((cycles_counter[1] == 1 && cycles_counter[0] != 1) |-> instruction_o.pc == instruction_q[1].pc && instruction_o.rd == instruction_q[1].rd && instruction_o.instr_type == instruction_q[1].instr_type))
else begin
    $display("Output instruction metadata does not match the finishing instruction from pipeline 1.");
end

a_flush_resets_division_regs: assert property ((flush_div_i |=> remanent_q[0] == '0 && remanent_q[1] == '0 && dividend_quotient_q[0] == '0 && dividend_quotient_q[1] == '0 && divisor_q[0] == '0 && divisor_q[1] == '0))
else begin
    $display("Flush did not correctly reset the internal division registers.");
end

a_counter_p0_stays_zero: assert property (((cycles_counter[0] == 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 0)) |=> cycles_counter[0] == 0))
else begin
    $display("Pipeline 0 counter changed from zero without being loaded.");
end

a_counter_p1_stays_zero: assert property (((cycles_counter[1] == 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 1)) |=> cycles_counter[1] == 0))
else begin
    $display("Pipeline 1 counter changed from zero without being loaded.");
end

a_idle_output_zero: assert property ((!(cycles_counter[0] == 1 || cycles_counter[1] == 1) |-> instruction_o.result == '0 && instruction_o.pc == '0 && !instruction_o.regfile_we))
else begin
    $display("When no instruction is finishing, output fields were not idle/zero.");
end

a_dividend_d_logic: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |-> dividend_d == ((instruction_i.instr.signed_op && (instruction_i.instr.op_32 ? instruction_i.data_rs1[31] : instruction_i.data_rs1[63])) ? (~instruction_i.data_rs1 + 1) : instruction_i.data_rs1))
else begin
    $display("Intermediate dividend_d was not calculated correctly (absolute value).");
end

a_divisor_d_logic: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |-> divisor_d == ((instruction_i.instr.signed_op && (instruction_i.instr.op_32 ? instruction_i.data_rs2[31] : instruction_i.data_rs2[63])) ? (~instruction_i.data_rs2 + 1) : instruction_i.data_rs2))
else begin
    $display("Intermediate divisor_d was not calculated correctly (absolute value).");
end

a_quo0_div_by_zero: assert property ((div_zero_q[0] |-> quo0 == '1))
else begin
    $display("Quotient for pipeline 0 should be all 1s for division by zero.");
end

a_quo1_div_by_zero: assert property ((div_zero_q[1] |-> quo1 == '1))
else begin
    $display("Quotient for pipeline 1 should be all 1s for division by zero.");
end

endmodule
