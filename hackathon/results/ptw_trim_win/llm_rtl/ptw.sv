module ptw 
import mmu_pkg::*;
#(
)(
    input logic clk_i,
    input logic rstn_i,

    // iTLB request-response
    input tlb_ptw_comm_t itlb_ptw_comm_i, 
    output ptw_tlb_comm_t ptw_itlb_comm_o,

    // dTLB request-response
    input tlb_ptw_comm_t dtlb_ptw_comm_i,
    output ptw_tlb_comm_t ptw_dtlb_comm_o,

    // dmem request-response
    input dmem_ptw_comm_t dmem_ptw_comm_i,
    output ptw_dmem_comm_t ptw_dmem_comm_o,

    // csr interface
    input csr_ptw_comm_t csr_ptw_comm_i,

    // pmu interface
    output logic pmu_ptw_hit_o,
    output logic pmu_ptw_miss_o
);

localparam [4:0] M_XRD = 5'b00000;
localparam [3:0] MT_D = 4'b0011;

typedef enum logic [2:0] {
    S_READY,
    S_REQ,
    S_WAIT,
    S_DONE,
    S_ERROR
} ptw_state;

logic unsigned [$clog2(LEVELS)-1:0] count_d, count_q;

ptw_tlb_comm_t ptw_tlb_comm; 
tlb_ptw_comm_t tlb_ptw_comm; 

ptw_state current_state, next_state;

logic ptw_ready;
tlb_ptw_req_t r_req;
pte_t r_pte;
pte_t pte;

logic [PAGE_LVL_BITS-1:0] vpn_req [LEVELS-1:0];
logic [PAGE_LVL_BITS-1:0] vpn_idx;

logic [SIZE_VADDR:0] pte_addr;
logic invalid_pte;
logic valid_pte_lvl [LEVELS-1:0];
logic is_pte_leaf, is_pte_table;

logic resp_err, resp_val;
logic [63:0] r_resp_ppn;
logic [PPN_SIZE-1:0] resp_ppn_lvl [LEVELS-1:0];
logic [PPN_SIZE-1:0] resp_ppn;

// PTW Arbiter
ptw_arb ptw_arb_inst(
    .clk_i(clk_i),
    .rstn_i(rstn_i),
    .itlb_ptw_comm_i(itlb_ptw_comm_i),
    .dtlb_ptw_comm_i(dtlb_ptw_comm_i),
    .ptw_itlb_comm_o(ptw_itlb_comm_o),
    .ptw_dtlb_comm_o(ptw_dtlb_comm_o),
    .ptw_tlb_comm_i(ptw_tlb_comm),
    .tlb_ptw_comm_o(tlb_ptw_comm)
);

// VPN indexation depending on the page level
genvar lvl;
generate
    for (lvl = 0; lvl < LEVELS; lvl++) begin
        logic [VPN_SIZE-1:0] aux_vpn_req;
        assign aux_vpn_req = (r_req.vpn[VPN_SIZE-1:0] >> ((LEVELS-lvl-1)*PAGE_LVL_BITS));
        assign vpn_req[lvl] = aux_vpn_req[PAGE_LVL_BITS-1:0];
    end
endgenerate
assign vpn_idx = vpn_req[count_q];

// Formatting pte data from dmem
assign pte.ppn = dmem_ptw_comm_i.resp.data[10+:(PPN_SIZE)];
assign pte.rfs = dmem_ptw_comm_i.resp.data[9:8];
assign pte.d = dmem_ptw_comm_i.resp.data[7];
assign pte.a = dmem_ptw_comm_i.resp.data[6];
assign pte.g = dmem_ptw_comm_i.resp.data[5];
assign pte.u = dmem_ptw_comm_i.resp.data[4];
assign pte.x = dmem_ptw_comm_i.resp.data[3];
assign pte.w = dmem_ptw_comm_i.resp.data[2];
assign pte.r = dmem_ptw_comm_i.resp.data[1];

genvar c;
generate
    for (c = 0; c < (LEVELS-1); c++) begin
        always_comb begin
            if (pte.r || pte.w || pte.x) begin
                valid_pte_lvl[c] = (pte.ppn[((LEVELS-c-1)*PAGE_LVL_BITS)-1:0] == '0) ? dmem_ptw_comm_i.resp.data[0] : 1'b0;
            end else begin
                valid_pte_lvl[c] = dmem_ptw_comm_i.resp.data[0];
            end
        end
    end
endgenerate
assign valid_pte_lvl[LEVELS-1] = dmem_ptw_comm_i.resp.data[0];
assign pte.v = valid_pte_lvl[count_q];

assign invalid_pte = (((dmem_ptw_comm_i.resp.data >> (PPN_SIZE+10)) != '0) ||
                      (is_pte_table & pte.v & (pte.d || pte.a || pte.u)) ||
                      (is_pte_leaf && !pte.r && pte.w)) ? 1'b1 : 1'b0;

assign is_pte_table = pte.v && !pte.x && !pte.w && !pte.r;
assign is_pte_leaf = pte.v && (pte.x || pte.w || pte.r);

// Page Table Entry pointer
logic [63:0] aux_pte_addr;
assign aux_pte_addr = {{(64-(PPN_SIZE+PAGE_LVL_BITS+$clog2(riscv_pkg::XLEN/8))){1'b0}}, {r_pte.ppn, vpn_idx, {{($clog2(riscv_pkg::XLEN/8))}{1'b0}}}};
assign pte_addr = aux_pte_addr[SIZE_VADDR:0];

// PTW Ready
assign ptw_ready = (current_state == S_READY);

// Catch Request from TLB(Arb) & PTE response from dmem
always_ff @(posedge clk_i, negedge rstn_i) begin
    if (!rstn_i) begin
        r_req <= '0;
        r_pte <= '0;
    end else begin
        if ((current_state == S_WAIT) && dmem_ptw_comm_i.resp.valid) begin
            r_pte <= pte;
        end else if (ptw_ready & tlb_ptw_comm.req.valid) begin
            r_req <= tlb_ptw_comm.req;
            r_pte.ppn <= csr_ptw_comm_i.satp[PPN_SIZE-1:0];
        end
    end
end

// dmem Request
assign ptw_dmem_comm_o.req.phys = 1'b1;
assign ptw_dmem_comm_o.req.cmd = M_XRD;
assign ptw_dmem_comm_o.req.typ = MT_D;
assign ptw_dmem_comm_o.req.addr = pte_addr;
assign ptw_dmem_comm_o.req.kill = 1'b0;
assign ptw_dmem_comm_o.req.data = '0;

// TLB Response
assign resp_err = (current_state == S_ERROR);
assign resp_val = (current_state == S_DONE) || resp_err;

assign r_resp_ppn = {{(64-(SIZE_VADDR-11)){1'b0}}, pte_addr[SIZE_VADDR:12]};
genvar j;
generate
    for (j = 0; j < (LEVELS-1); j++) begin
        logic [63:0] aux_resp_ppn_lvl;
        assign aux_resp_ppn_lvl = {{(64-$bits(r_resp_ppn[63:(LEVELS-j-1)*PAGE_LVL_BITS])-$bits(r_req.vpn[PAGE_LVL_BITS*(LEVELS-j-1)-1:0])){1'b0}}, 
                                    r_resp_ppn[63:(LEVELS-j-1)*PAGE_LVL_BITS], 
                                    r_req.vpn[PAGE_LVL_BITS*(LEVELS-j-1)-1:0]};
        assign resp_ppn_lvl[j] = aux_resp_ppn_lvl[PPN_SIZE-1:0];
    end
endgenerate
assign resp_ppn_lvl[LEVELS-1] = r_resp_ppn[PPN_SIZE-1:0];
assign resp_ppn = resp_ppn_lvl[count_q];

// Send TLB Response to Arb
assign ptw_tlb_comm.resp.valid = resp_val;
assign ptw_tlb_comm.resp.error = resp_err;
assign ptw_tlb_comm.resp.g_stage_error = 1'b0;
assign ptw_tlb_comm.resp.level = count_q;
assign ptw_tlb_comm.resp.pte.ppn = resp_ppn;
assign ptw_tlb_comm.resp.pte.rfs = r_pte.rfs;
assign ptw_tlb_comm.resp.pte.d = r_pte.d;
assign ptw_tlb_comm.resp.pte.a = r_pte.a;
assign ptw_tlb_comm.resp.pte.g = r_pte.g;
assign ptw_tlb_comm.resp.pte.u = r_pte.u;
assign ptw_tlb_comm.resp.pte.x = r_pte.x;
assign ptw_tlb_comm.resp.pte.w = r_pte.w;
assign ptw_tlb_comm.resp.pte.r = r_pte.r;
assign ptw_tlb_comm.resp.pte.v = r_pte.v;
assign ptw_tlb_comm.resp.virt_level = '0;
assign ptw_tlb_comm.resp.guest_pte = '0;
assign ptw_tlb_comm.ptw_ready = ptw_ready;
assign ptw_tlb_comm.ptw_mstatus = csr_ptw_comm_i.mstatus;
assign ptw_tlb_comm.ptw_vsstatus = csr_ptw_comm_i.vsstatus;
assign ptw_tlb_comm.invalidate_tlb = csr_ptw_comm_i.flush;
assign ptw_tlb_comm.invalidate_tlb_satp  = csr_ptw_comm_i.flush_satp;
assign ptw_tlb_comm.invalidate_tlb_vsatp = csr_ptw_comm_i.flush_vsatp;
assign ptw_tlb_comm.invalidate_tlb_hgatp = csr_ptw_comm_i.flush_hgatp;

// Page-Table Walker FSM 
always_ff @(posedge clk_i, negedge rstn_i) begin
    if (!rstn_i) begin
        current_state <= S_READY;
        count_q <= '0;
    end
    else begin
        current_state <= next_state;
        count_q <= count_d;
    end
end

always_comb begin
    count_d = count_q;
    pmu_ptw_hit_o = 1'b0;
    pmu_ptw_miss_o = 1'b0;
    ptw_dmem_comm_o.req.valid = 1'b0;
    next_state = current_state;
    
    case (current_state)
        S_READY : begin
            count_d = '0;
            if (tlb_ptw_comm.req.valid) begin
              next_state = S_REQ;
            end
            else begin
              next_state = S_READY;
            end
        end
        S_REQ : begin
            ptw_dmem_comm_o.req.valid = 1'b1;
            if (dmem_ptw_comm_i.dmem_ready) begin
                next_state = S_WAIT;
            end else begin
                next_state = S_REQ;
            end
        end
        S_WAIT : begin
            if (dmem_ptw_comm_i.resp.nack) begin
                next_state = S_REQ;
            end else if (dmem_ptw_comm_i.resp.valid) begin
                if (invalid_pte) begin
                    next_state = S_ERROR;
                end else if (is_pte_table && (count_q < $unsigned(LEVELS-1))) begin
                    count_d = count_q + 1'b1;
                    pmu_ptw_miss_o = 1'b1;
                    next_state = S_REQ;
                end else if (is_pte_leaf) begin
                    next_state = S_DONE;
                end 
                else begin
                    next_state = S_ERROR;
                end
            end
            else begin 
                next_state = S_WAIT;
            end
        end
        S_DONE : begin
            next_state = S_READY;
        end
        S_ERROR : begin
            next_state = S_READY;
        end
        default: next_state = S_READY;
    endcase
end

endmodule
