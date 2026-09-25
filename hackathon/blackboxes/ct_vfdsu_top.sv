// Empty stand-in for the C910 T-Head DivSqrt core.
// Default fpnew_top DivSqrtSel is THMULTI; the real ct_vfdsu_* tree
// also needs gated_clk_cell. VCF already black-boxes this instance.
(* blackbox *)
module ct_vfdsu_top (
  input            cp0_vfpu_icg_en,
  input            cp0_yy_clk_en,
  input            cpurst_b,
  input      [4:0] dp_vfdsu_ex1_pipex_dst_ereg,
  input      [6:0] dp_vfdsu_ex1_pipex_dst_vreg,
  input      [6:0] dp_vfdsu_ex1_pipex_iid,
  input      [2:0] dp_vfdsu_ex1_pipex_imm0,
  input            dp_vfdsu_ex1_pipex_sel,
  input     [63:0] dp_vfdsu_ex1_pipex_srcf0,
  input     [63:0] dp_vfdsu_ex1_pipex_srcf1,
  input            dp_vfdsu_fdiv_gateclk_issue,
  input            dp_vfdsu_idu_fdiv_issue,
  input            forever_cpuclk,
  input     [19:0] idu_vfpu_rf_pipex_func,
  input            idu_vfpu_rf_pipex_gateclk_sel,
  input            pad_yy_icg_scan_en,
  output     [4:0] pipex_dp_vfdsu_ereg,
  output     [4:0] pipex_dp_vfdsu_ereg_data,
  output    [63:0] pipex_dp_vfdsu_freg_data,
  output           pipex_dp_vfdsu_inst_vld,
  output     [6:0] pipex_dp_vfdsu_vreg,
  input            rtu_yy_xx_flush,
  output           vfdsu_dp_fdiv_busy,
  output           vfdsu_dp_inst_wb_req,
  output           vfdsu_ifu_debug_ex2_wait,
  output           vfdsu_ifu_debug_idle,
  output           vfdsu_ifu_debug_pipe_busy,
  input            vfpu_yy_xx_dqnan,
  input      [2:0] vfpu_yy_xx_rm
);
endmodule
