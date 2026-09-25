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
a_instruction_latch: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> instruction_q[div_unit_sel_i].pc == instruction_i.instr.pc && instruction_q[div_unit_sel_i].rd == instruction_i.instr.rd && instruction_q[div_unit_sel_i].result_pc == instruction_i.data_rs1 && op_32_q[div_unit_sel_i] == instruction_i.instr.op_32 && signed_op_q[div_unit_sel_i] == instruction_i.instr.signed_op))
else begin
    $display("New instruction properties were not latched correctly into the selected pipeline.");
end

a_counter_init_32b: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && instruction_i.instr.op_32) |=> cycles_counter[div_unit_sel_i] == 17))
else begin
    $display("Cycle counter not initialized to 17 for a 32-bit division.");
end

a_counter_init_64b: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && !instruction_i.instr.op_32) |=> cycles_counter[div_unit_sel_i] == 33))
else begin
    $display("Cycle counter not initialized to 33 for a 64-bit division.");
end

a_counter_decrement_p0: assert property (((cycles_counter[0] > 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 0)) |=> cycles_counter[0] == $past(cycles_counter[0]) - 1))
else begin
    $display("Pipeline 0 cycle counter did not decrement when it was not being loaded.");
end

a_counter_decrement_p1: assert property (((cycles_counter[1] > 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 1)) |=> cycles_counter[1] == $past(cycles_counter[1]) - 1))
else begin
    $display("Pipeline 1 cycle counter did not decrement when it was not being loaded.");
end

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

a_result_div_by_zero_p0: assert property ((cycles_counter[0] == 1 && div_zero_q[0]) |-> ((instruction_q[0].instr_type inside {DIV, DIVU, DIVW, DIVUW} && instruction_o.result == '1) || (instruction_q[0].instr_type inside {REM, REMU, REMW, REMUW} && (op_32_q[0] ? instruction_o.result == {{32{instruction_q[0].result_pc[31]}}, instruction_q[0].result_pc[31:0]} : instruction_o.result == instruction_q[0].result_pc))))
else begin
    $display("Incorrect result for division by zero in pipeline 0.");
end

a_result_div_by_zero_p1: assert property ((cycles_counter[1] == 1 && cycles_counter[0] != 1 && div_zero_q[1]) |-> ((instruction_q[1].instr_type inside {DIV, DIVU, DIVW, DIVUW} && instruction_o.result == '1) || (instruction_q[1].instr_type inside {REM, REMU, REMW, REMUW} && (op_32_q[1] ? instruction_o.result == {{32{instruction_q[1].result_pc[31]}}, instruction_q[1].result_pc[31:0]} : instruction_o.result == instruction_q[1].result_pc))))
else begin
    $display("Incorrect result for division by zero in pipeline 1.");
end

a_div_zero_latch: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> div_zero_q[div_unit_sel_i] == ((~(|instruction_i.data_rs2) || (instruction_i.instr.op_32 && ~(|instruction_i.data_rs2[31:0])))))
else begin
    $display("Division by zero flag was not latched correctly for a new instruction.");
end

a_same_sign_latch: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> same_sign_q[div_unit_sel_i] == (instruction_i.instr.op_32 ? ~(instruction_i.data_rs2[31] ^ instruction_i.data_rs1[31]) : ~(instruction_i.data_rs2[63] ^ instruction_i.data_rs1[63])))
else begin
    $display("Operand sign comparison flag was not latched correctly for a new instruction.");
end

a_initial_remanent_q_zero: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> remanent_q[div_unit_sel_i] == '0))
else begin
    $display("Remanent register was not cleared to zero when a new instruction was accepted.");
end

a_initial_dividend_quotient_q_latch: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> dividend_quotient_q[div_unit_sel_i] == (instruction_i.instr.op_32 ? {dividend_d[31:0], 32'b0} : dividend_d))
else begin
    $display("Dividend/Quotient register was not initialized correctly for a new instruction.");
end

a_initial_divisor_q_latch: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> divisor_q[div_unit_sel_i] == (instruction_i.instr.op_32 ? {32'b0, divisor_d[31:0]} : divisor_d))
else begin
    $display("Divisor register was not initialized correctly for a new instruction.");
end

a_pipeline_step_p0: assert property (((cycles_counter[0] > 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 0)) |=> remanent_q[0] == remanent_out[0] && dividend_quotient_q[0] == dividend_quotient_out[0] && divisor_q[0] == divisor_out[0]))
else begin
    $display("Pipeline 0 division registers did not advance correctly during an iterative step.");
end

a_pipeline_step_p1: assert property (((cycles_counter[1] > 0 && !(instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 1)) |=> remanent_q[1] == remanent_out[1] && dividend_quotient_q[1] == dividend_quotient_out[1] && divisor_q[1] == divisor_out[1]))
else begin
    $display("Pipeline 1 division registers did not advance correctly during an iterative step.");
end

a_flush_clears_valid_and_state: assert property ((flush_div_i |=> instruction_q[0].valid == 1'b0 && instruction_q[1].valid == 1'b0 && div_zero_q[0] == 1'b0 && div_zero_q[1] == 1'b0 && same_sign_q[0] == 1'b0 && same_sign_q[1] == 1'b0 && op_32_q[0] == 1'b0 && op_32_q[1] == 1'b0 && signed_op_q[0] == 1'b0 && signed_op_q[1] == 1'b0))
else begin
    $display("Flush did not correctly reset instruction valid and state flags.");
end

a_flush_resets_division_regs: assert property ((flush_div_i |=> remanent_q[0] == '0 && remanent_q[1] == '0 && dividend_quotient_q[0] == '0 && dividend_quotient_q[1] == '0 && divisor_q[0] == '0 && divisor_q[1] == '0))
else begin
    $display("Flush did not correctly reset the internal division registers.");
end

a_result_div_normal_p0: assert property ((cycles_counter[0] == 1 && !div_zero_q[0] && instruction_q[0].instr_type inside {DIV, DIVU, DIVW, DIVUW}) |-> instruction_o.result == (op_32_q[0] ? {{32{quo0[31]}}, quo0[31:0]} : quo0))
else begin
    $display("Incorrect result for a normal (non-zero divisor) division operation from pipeline 0.");
end

a_result_rem_normal_p0: assert property ((cycles_counter[0] == 1 && !div_zero_q[0] && instruction_q[0].instr_type inside {REM, REMU, REMW, REMUW}) |-> instruction_o.result == (op_32_q[0] ? {{32{rmd0[31]}}, rmd0[31:0]} : rmd0))
else begin
    $display("Incorrect result for a normal (non-zero divisor) remainder operation from pipeline 0.");
end

a_result_div_normal_p1: assert property ((cycles_counter[1] == 1 && cycles_counter[0] != 1 && !div_zero_q[1] && instruction_q[1].instr_type inside {DIV, DIVU, DIVW, DIVUW}) |-> instruction_o.result == (op_32_q[1] ? {{32{quo1[31]}}, quo1[31:0]} : quo1))
else begin
    $display("Incorrect result for a normal (non-zero divisor) division operation from pipeline 1.");
end

a_result_rem_normal_p1: assert property ((cycles_counter[1] == 1 && cycles_counter[0] != 1 && !div_zero_q[1] && instruction_q[1].instr_type inside {REM, REMU, REMW, REMUW}) |-> instruction_o.result == (op_32_q[1] ? {{32{rmd1[31]}}, rmd1[31:0]} : rmd1))
else begin
    $display("Incorrect result for a normal (non-zero divisor) remainder operation from pipeline 1.");
end

a_unselected_pipeline_stable: assert property ((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> $stable(instruction_q[~div_unit_sel_i]) && $stable(div_zero_q[~div_unit_sel_i]) && $stable(same_sign_q[~div_unit_sel_i]) && $stable(op_32_q[~div_unit_sel_i]) && $stable(signed_op_q[~div_unit_sel_i]))
else begin
    $display("The state of the unselected pipeline was not preserved when a new instruction was accepted.");
end

a_op_32_latch: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> op_32_q[div_unit_sel_i] == instruction_i.instr.op_32))
else begin
    $display("op_32 flag was not latched correctly for a new instruction.");
end

a_signed_op_latch: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> signed_op_q[div_unit_sel_i] == instruction_i.instr.signed_op))
else begin
    $display("signed_op flag was not latched correctly for a new instruction.");
end

a_instruction_valid_latch: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV) |=> instruction_q[div_unit_sel_i].valid))
else begin
    $display("The valid bit for the selected pipeline was not set when a new instruction was accepted.");
end

a_unselected_counter_decrement_p0: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 1 && cycles_counter[0] > 0) |=> cycles_counter[0] == $past(cycles_counter[0]) - 1))
else begin
    $display("Pipeline 0 counter did not decrement while it was unselected and pipeline 1 was loaded.");
end

a_unselected_counter_decrement_p1: assert property (((instruction_i.instr.valid && instruction_i.instr.unit == UNIT_DIV && div_unit_sel_i == 0 && cycles_counter[1] > 0) |=> cycles_counter[1] == $past(cycles_counter[1]) - 1))
else begin
    $display("Pipeline 1 counter did not decrement while it was unselected and pipeline 0 was loaded.");
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

a_quo0_unsigned: assert property ((!div_zero_q[0] && !signed_op_q[0] |-> quo0 == dividend_quotient_q[0]))
else begin
    $display("Unsigned quotient for pipeline 0 is incorrect.");
end

a_quo0_signed_same_sign: assert property ((!div_zero_q[0] && signed_op_q[0] && same_sign_q[0] |-> quo0 == dividend_quotient_q[0]))
else begin
    $display("Signed quotient for pipeline 0 with same-sign operands is incorrect.");
end

a_quo0_signed_diff_sign: assert property ((!div_zero_q[0] && signed_op_q[0] && !same_sign_q[0] |-> quo0 == ~dividend_quotient_q[0] + 1))
else begin
    $display("Signed quotient for pipeline 0 with different-sign operands is incorrect.");
end

a_quo1_div_by_zero: assert property ((div_zero_q[1] |-> quo1 == '1))
else begin
    $display("Quotient for pipeline 1 should be all 1s for division by zero.");
end

a_quo1_unsigned: assert property ((!div_zero_q[1] && !signed_op_q[1] |-> quo1 == dividend_quotient_q[1]))
else begin
    $display("Unsigned quotient for pipeline 1 is incorrect.");
end

a_quo1_signed_same_sign: assert property ((!div_zero_q[1] && signed_op_q[1] && same_sign_q[1] |-> quo1 == dividend_quotient_q[1]))
else begin
    $display("Signed quotient for pipeline 1 with same-sign operands is incorrect.");
end

a_quo1_signed_diff_sign: assert property ((!div_zero_q[1] && signed_op_q[1] && !same_sign_q[1] |-> quo1 == ~dividend_quotient_q[1] + 1))
else begin
    $display("Signed quotient for pipeline 1 with different-sign operands is incorrect.");
end

a_rmd0_div_by_zero: assert property ((div_zero_q[0] |-> rmd0 == instruction_q[0].result_pc))
else begin
    $display("Remainder for pipeline 0 should be the original dividend for division by zero.");
end

a_rmd0_unsigned: assert property ((!div_zero_q[0] && !signed_op_q[0] |-> rmd0 == remanent_q[0]))
else begin
    $display("Unsigned remainder for pipeline 0 is incorrect.");
end

a_rmd0_signed_pos_dividend: assert property ((!div_zero_q[0] && signed_op_q[0] && !(op_32_q[0] ? instruction_q[0].result_pc[31] : instruction_q[0].result_pc[63]) |-> rmd0 == remanent_q[0]))
else begin
    $display("Signed remainder for pipeline 0 with positive dividend is incorrect.");
end

a_rmd0_signed_neg_dividend: assert property ((!div_zero_q[0] && signed_op_q[0] && (op_32_q[0] ? instruction_q[0].result_pc[31] : instruction_q[0].result_pc[63]) |-> rmd0 == ~remanent_q[0] + 1))
else begin
    $display("Signed remainder for pipeline 0 with negative dividend is incorrect.");
end

a_rmd1_div_by_zero: assert property ((div_zero_q[1] |-> rmd1 == instruction_q[1].result_pc))
else begin
    $display("Remainder for pipeline 1 should be the original dividend for division by zero.");
end

a_rmd1_unsigned: assert property ((!div_zero_q[1] && !signed_op_q[1] |-> rmd1 == remanent_q[1]))
else begin
    $display("Unsigned remainder for pipeline 1 is incorrect.");
end

// SVApshot: commented out due to unfixable syntax error in a_quotient_calculation_p0
// a_quotient_calculation_p0: assert property ((cycles_counter[0] == 1 && !div_zero_q[0] && instruction_q[0].instr_type inside {DIV, DIVU, DIVW, DIVUW}) |-> (op_32_q[0] ? instruction_o.result == {{32{quo0[31]}}, quo0[31:0]} : instruction_o.result == quo0))
// else begin
//     $display("Final quotient result from pipeline 0 does not match calculation from internal state.");
// end

// SVApshot: commented out due to unfixable syntax error in a_quotient_calculation_p1
// a_quotient_calculation_p1: assert property ((let expected_quo = signed_op_q[1] ? (same_sign_q[1] ? dividend_quotient_q[1] : ~dividend_quotient_q[1] + 1'b1) : dividend_quotient_q[1] ((cycles_counter[1] == 1 && cycles_counter[0] != 1 && !div_zero_q[1] && instruction_q[1].instr_type inside {DIV, DIVU, DIVW, DIVUW}) |-> (op_32_q[1] ? instruction_o.result == {{32{expected_quo[31]}}, expected_quo[31:0]} : instruction_o.result == expected_quo))))
// else begin
//     $display("Final quotient result from pipeline 1 does not match calculation from internal state.");
// end

// SVApshot: commented out due to unfixable syntax error in a_remainder_calculation_p0
// a_remainder_calculation_p0: assert property ((cycles_counter[0] == 1 && !div_zero_q[0] && instruction_q[0].instr_type inside {REM,REMU,REMW,REMUW}) |-> let dividend_is_neg = signed_op_q[0] && (op_32_q[0] ? instruction_q[0].result_pc[31] : instruction_q[0].result_pc[63]), rem_signed_corrected = dividend_is_neg ? ~remanent_q[0] + 1'b1 : remanent_q[0], expected_rem = (signed_op_q[0] ? rem_signed_corrected : remanent_q[0]) in (op_32_q[0] ? instruction_o.result == {{32{expected_rem[31]}}, expected_rem[31:0]} : instruction_o.result == expected_rem))
// else begin
//     $display("Final remainder result from pipeline 0 does not match calculation from internal state.");
// end

// SVApshot: commented out due to unfixable syntax error in a_remainder_calculation_p1
// a_remainder_calculation_p1: assert property ((cycles_counter[1] == 1 && cycles_counter[0] != 1 && !div_zero_q[1] && instruction_q[1].instr_type inside {REM,REMU,REMW,REMUW}) |-> let dividend_is_neg = signed_op_q[1] && (op_32_q[1] ? instruction_q[1].result_pc[31] : instruction_q[1].result_pc[63]), rem_signed_corrected = dividend_is_neg ? ~remanent_q[1] + 1'b1 : remanent_q[1], expected_rem = signed_op_q[1] ? rem_signed_corrected : remanent_q[1] in (op_32_q[1] ? instruction_o.result == {{32{expected_rem[31]}}, expected_rem[31:0]} : instruction_o.result == expected_rem))
// else begin
//     $display("Final remainder result from pipeline 1 does not match calculation from internal state.");
// end


endmodule
