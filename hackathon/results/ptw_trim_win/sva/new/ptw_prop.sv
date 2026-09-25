// DUT-local enum types (not visible via package import).
// Copied so density-bind ports and enum literals compile in the checker.
typedef enum logic [2:0] {
    S_READY,
    S_REQ,
    S_WAIT,
    S_VS_TO_G,
    S_G_TO_VS,
    S_DONE,
    S_ERROR
} ptw_state;

module ptw_prop

// Automatically detected package imports
import riscv_pkg::*;
import mmu_pkg::*;

#(
		parameter ASSERT_INPUTS = 0
)(
		input logic clk_i,
		input logic rstn_i,
		
		// iTLB request-response
		input tlb_ptw_comm_t itlb_ptw_comm_i, 
		input  ptw_tlb_comm_t ptw_itlb_comm_o, //output
		
		// dTLB request-response
		input tlb_ptw_comm_t dtlb_ptw_comm_i,
		input  ptw_tlb_comm_t ptw_dtlb_comm_o, //output
		
		// dmem request-response
		input dmem_ptw_comm_t dmem_ptw_comm_i,
		input  ptw_dmem_comm_t ptw_dmem_comm_o, //output
		
		// csr interface
		input csr_ptw_comm_t csr_ptw_comm_i,
		
		// pmu interface
		input  logic pmu_ptw_hit_o, //output
		input  logic pmu_ptw_miss_o, //output
		input logic [63:0] aux_pte_addr, // internal (density bind)
		input logic [$clog2(LEVELS)-1:0] count_d, // internal (density bind)
		input logic [$clog2(LEVELS)-1:0] count_q, // internal (density bind)
		input logic [2:0] current_state, // internal (density bind)
		input logic invalid_pte, // internal (density bind)
		input logic is_g_stage_d, // internal (density bind)
		input logic is_g_stage_q, // internal (density bind)
		input logic is_pte_leaf, // internal (density bind)
		input logic is_pte_table, // internal (density bind)
		input logic is_virtualized_env, // internal (density bind)
		input logic is_vs_stage, // internal (density bind)
		input logic last_g_stage_q, // internal (density bind)
		input logic pte_cache_hit, // internal (density bind)
		input logic ptw_ready, // internal (density bind)
		input ptw_tlb_comm_t ptw_tlb_comm, // internal (density bind)
		input pte_t r_pte, // internal (density bind)
		input tlb_ptw_req_t r_req, // internal (density bind)
		input tlb_ptw_comm_t tlb_ptw_comm, // internal (density bind)
		input logic [$clog2(LEVELS)-1:0] vs_count_reg_d, // internal (density bind)
		input logic [$clog2(LEVELS)-1:0] vs_count_reg_q, // internal (density bind)
		input logic [SIZE_VADDR:0] vs_pte_addr_reg_d, // internal (density bind)
		input logic allow_vs_req_q, // internal (density bind)
		input logic [PPN_SIZE-1:0] g_resp_ppn_lvl [LEVELS-1:0], // internal (density bind)
		input logic last_g_stage_d, // internal (density bind)
		input logic [2:0] next_state, // internal (density bind)
		input pte_t pte, // internal (density bind)
		input logic [SIZE_VADDR:0] pte_addr, // internal (density bind)
		input logic [PPN_SIZE-1:0] resp_ppn_lvl [LEVELS-1:0] // internal (density bind)
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
a_itlb_req_when_ready_gets_response: assert property (($rose(itlb_ptw_comm_i.req.valid) && ptw_itlb_comm_o.ptw_ready |=> ##[1:$] ptw_itlb_comm_o.resp.valid))
else begin
    $display("iTLB made a valid request when the PTW was ready, but no response was ever sent.");
end

a_dtlb_req_when_ready_gets_response: assert property (($rose(dtlb_ptw_comm_i.req.valid) && ptw_dtlb_comm_o.ptw_ready |=> ##[1:$] ptw_dtlb_comm_o.resp.valid))
else begin
    $display("dTLB made a valid request when the PTW was ready, but no response was ever sent.");
end

a_resp_exclusive: assert property ((!(ptw_itlb_comm_o.resp.valid && ptw_dtlb_comm_o.resp.valid)))
else begin
    $display("PTW sent a response to both iTLB and dTLB in the same cycle.");
end

a_itlb_resp_is_single_cycle: assert property ((ptw_itlb_comm_o.resp.valid |=> !ptw_itlb_comm_o.resp.valid))
else begin
    $display("iTLB response valid was asserted for more than one consecutive cycle.");
end

a_dtlb_resp_is_single_cycle: assert property ((ptw_dtlb_comm_o.resp.valid |=> !ptw_dtlb_comm_o.resp.valid))
else begin
    $display("dTLB response valid was asserted for more than one consecutive cycle.");
end

a_itlb_error_is_valid_resp: assert property ((ptw_itlb_comm_o.resp.error |-> ptw_itlb_comm_o.resp.valid))
else begin
    $display("An iTLB error response was signaled without the valid signal being asserted.");
end

a_dtlb_error_is_valid_resp: assert property ((ptw_dtlb_comm_o.resp.error |-> ptw_dtlb_comm_o.resp.valid))
else begin
    $display("A dTLB error response was signaled without the valid signal being asserted.");
end

a_itlb_g_stage_error_is_error: assert property ((ptw_itlb_comm_o.resp.g_stage_error |-> ptw_itlb_comm_o.resp.error))
else begin
    $display("An iTLB G-stage error was signaled without the main error signal being asserted.");
end

a_dtlb_g_stage_error_is_error: assert property ((ptw_dtlb_comm_o.resp.g_stage_error |-> ptw_dtlb_comm_o.resp.error))
else begin
    $display("A dTLB G-stage error was signaled without the main error signal being asserted.");
end

a_dmem_req_is_physical: assert property ((ptw_dmem_comm_o.req.valid |-> ptw_dmem_comm_o.req.phys))
else begin
    $display("PTW initiated a non-physical request to DMEM.");
end

a_dmem_req_is_read: assert property ((ptw_dmem_comm_o.req.valid |-> ptw_dmem_comm_o.req.cmd == 5'b00000))
else begin
    $display("PTW initiated a DMEM request with a command other than read.");
end

a_pmu_hit_miss_exclusive: assert property ((!(pmu_ptw_hit_o && pmu_ptw_miss_o)))
else begin
    $display("PMU hit and miss signals were asserted simultaneously.");
end

a_invalidate_tlb_passthrough: assert property ((csr_ptw_comm_i.flush == (ptw_itlb_comm_o.invalidate_tlb && ptw_dtlb_comm_o.invalidate_tlb)))
else begin
    $display("The global flush signal was not passed through to both TLB interfaces.");
end

a_invalidate_tlb_satp_passthrough: assert property ((csr_ptw_comm_i.flush_satp == (ptw_itlb_comm_o.invalidate_tlb_satp && ptw_dtlb_comm_o.invalidate_tlb_satp)))
else begin
    $display("The SATP flush signal was not passed through to both TLB interfaces.");
end

a_invalidate_tlb_vsatp_passthrough: assert property ((csr_ptw_comm_i.flush_vsatp == (ptw_itlb_comm_o.invalidate_tlb_vsatp && ptw_dtlb_comm_o.invalidate_tlb_vsatp)))
else begin
    $display("The VSATP flush signal was not passed through to both TLB interfaces.");
end

a_invalidate_tlb_hgatp_passthrough: assert property ((csr_ptw_comm_i.flush_hgatp == (ptw_itlb_comm_o.invalidate_tlb_hgatp && ptw_dtlb_comm_o.invalidate_tlb_hgatp)))
else begin
    $display("The HGATP flush signal was not passed through to both TLB interfaces.");
end

a_ready_to_req_on_valid: assert property (((current_state == S_READY) && tlb_ptw_comm.req.valid |=> current_state == S_REQ))
else begin
    $display("PTW in READY state with a valid request did not transition to REQ state.");
end

a_req_to_wait_on_dmem_ready: assert property (((current_state == S_REQ) && ptw_dmem_comm_o.req.valid && dmem_ptw_comm_i.dmem_ready |=> current_state == S_WAIT))
else begin
    $display("PTW in REQ state with a valid DMEM request and DMEM ready did not transition to WAIT state.");
end

a_wait_stays_wait: assert property (((current_state == S_WAIT) && !dmem_ptw_comm_i.resp.nack && !dmem_ptw_comm_i.resp.valid |=> current_state == S_WAIT))
else begin
    $display("PTW in WAIT state should remain in WAIT state until a DMEM response or NACK is received.");
end

a_wait_to_req_on_table: assert property (((current_state == S_WAIT) && dmem_ptw_comm_i.resp.valid && !invalid_pte && is_pte_table && (count_q < $unsigned(LEVELS-1)) |=> current_state == S_REQ))
else begin
    $display("PTW received a valid table PTE but did not transition to REQ to walk the next level.");
end

a_done_to_ready: assert property ((current_state == S_DONE |=> current_state == S_READY))
else begin
    $display("PTW did not transition from DONE to READY state in the next cycle.");
end

a_error_to_ready: assert property ((current_state == S_ERROR |=> current_state == S_READY))
else begin
    $display("PTW did not transition from ERROR to READY state in the next cycle.");
end

a_ptw_ready_in_s_ready_only: assert property ((ptw_ready == (current_state == S_READY)))
else begin
    $display("ptw_ready signal is not correctly reflecting the S_READY state.");
end

a_dmem_req_in_s_req_only: assert property ((ptw_dmem_comm_o.req.valid |-> current_state == S_REQ))
else begin
    $display("A DMEM request was made when the PTW was not in the REQ state.");
end

a_pmu_hit_in_s_req_only: assert property ((pmu_ptw_hit_o |-> current_state == S_REQ))
else begin
    $display("A PMU hit was reported when the PTW was not in the REQ state.");
end

a_pmu_miss_in_s_wait_only: assert property ((pmu_ptw_miss_o |-> current_state == S_WAIT))
else begin
    $display("A PMU miss was reported when the PTW was not in the WAIT state.");
end

a_count_resets_on_new_req: assert property (((current_state == S_READY) && tlb_ptw_comm.req.valid |=> count_q == 0))
else begin
    $display("Page walk level counter did not reset to zero for a new request.");
end

a_resp_valid_in_final_states_only: assert property ((ptw_tlb_comm.resp.valid == ((current_state == S_DONE) || (current_state == S_ERROR))))
else begin
    $display("TLB response valid was asserted outside of DONE or ERROR states.");
end

a_resp_error_in_error_state_only: assert property ((ptw_tlb_comm.resp.error == (current_state == S_ERROR)))
else begin
    $display("TLB response error was asserted when not in ERROR state, or not asserted when in ERROR state.");
end

a_dmem_req_type_is_doubleword: assert property ((ptw_dmem_comm_o.req.valid |-> ptw_dmem_comm_o.req.typ == 4'b0011))
else begin
    $display("PTW initiated a DMEM request with a type other than doubleword.");
end

a_dmem_req_kill_is_false: assert property ((!ptw_dmem_comm_o.req.kill))
else begin
    $display("PTW asserted the kill signal on a DMEM request.");
end

a_leaf_and_table_are_exclusive: assert property ((dmem_ptw_comm_i.resp.valid |-> !(is_pte_leaf && is_pte_table)))
else begin
    $display("A PTE from memory was interpreted as both a leaf and a table pointer.");
end

a_request_is_latched: assert property (($rose(tlb_ptw_comm.req.valid) && $past(current_state == S_READY) |=> r_req == $past(tlb_ptw_comm.req)))
else begin
    $display("PTW accepted a new request but did not latch it correctly.");
end

a_initial_pte_ppn_from_csr: assert property (($rose(tlb_ptw_comm.req.valid) && $past(current_state == S_READY) |=> r_pte.ppn == (($past(tlb_ptw_comm.req.en_vs) || $past(tlb_ptw_comm.req.en_g)) ? $past(csr_ptw_comm_i.vsatp[PPN_SIZE-1:0]) : $past(csr_ptw_comm_i.satp[PPN_SIZE-1:0]))))
else begin
    $display("The initial PPN for a page walk was not correctly loaded from the corresponding SATP/VSATP register.");
end

a_dmem_req_addr_is_pte_addr: assert property ((ptw_dmem_comm_o.req.valid |-> ptw_dmem_comm_o.req.addr == pte_addr))
else begin
    $display("DMEM request address does not match the calculated pte_addr");
end

a_dmem_nack_causes_retry: assert property ((dmem_ptw_comm_i.resp.nack |=> ##[1:$] ptw_dmem_comm_o.req.valid))
else begin
    $display("PTW did not eventually retry a DMEM request after receiving a NACK.");
end

a_wait_to_req_on_nack: assert property (((current_state == S_WAIT) && dmem_ptw_comm_i.resp.nack |-> next_state == S_REQ))
else begin
    $display("PTW in WAIT state received a DMEM NACK, but the FSM did not combinationally target the REQ state in the same cycle.");
end

endmodule
