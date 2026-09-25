// Port-level commit/exception bind for MegaBOOM v3 Rob (rob.scala).
// Not the whole ROB: only the extractable retire vs exception interface.
// Signal names match elaborated Rob.sv so this can later `bind Rob`.
module CommitExceptionPorts (
    input        clock,
    input        reset,
    input        io_commit_valids_0,
    input        io_commit_valids_1,
    input        io_commit_valids_2,
    input        io_commit_valids_3,
    input        io_commit_uops_0_ldst_val,
    input        io_commit_uops_1_ldst_val,
    input        io_commit_uops_2_ldst_val,
    input        io_commit_uops_3_ldst_val,
    input  [4:0] io_commit_uops_0_ldst,
    input  [4:0] io_commit_uops_1_ldst,
    input  [4:0] io_commit_uops_2_ldst,
    input  [4:0] io_commit_uops_3_ldst,
    input        io_com_xcpt_valid,
    output       exception_with_rf_write
);
    // Dest-write on a committing exception. The snapshot contract is that
    // this is never 1: an excepting retire must not write the register file.
    wire dest_write = (io_commit_valids_0 & io_commit_uops_0_ldst_val)
                    | (io_commit_valids_1 & io_commit_uops_1_ldst_val)
                    | (io_commit_valids_2 & io_commit_uops_2_ldst_val)
                    | (io_commit_valids_3 & io_commit_uops_3_ldst_val);
    assign exception_with_rf_write = io_com_xcpt_valid & dest_write;
endmodule
