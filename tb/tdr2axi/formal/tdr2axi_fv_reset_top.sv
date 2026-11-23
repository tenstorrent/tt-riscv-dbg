// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Formal harness for the reset synchronizers of tdr2axi in a multiclock model: i_clk,
//   TCK, i_arst_n, i_test_mode and i_arst_scan_n are free on every step, so either clock
//   may stop and the reset may change at any step. The job reads dmi_tdr_bridge, dm_csrs
//   and dm_sba as black boxes and cuts them; nothing the properties read depends on them.
//   tdr2axi_reset_sva checks the internal resets for the mode FUNC_MODE selects.

`include "axi/typedef.svh"

module tdr2axi_fv_reset_top #(
    parameter int unsigned SYNC_STAGES = 3,
    // 1: functional mode, i_test_mode = 0; 0: i_test_mode free
    parameter bit          FUNC_MODE   = 1'b1
) (
    input logic clk,
    input logic tck,
    input logic arst_n,
    input logic test_mode,
    input logic arst_scan_n
);

  typedef logic [15:0] addr_t;
  typedef logic [31:0] data_t;
  typedef logic [3:0] strb_t;

  `AXI_LITE_TYPEDEF_ALL(axil, addr_t, data_t, strb_t)

  axil_req_t  dut_req;
  axil_resp_t dut_rsp;

  assign dut_rsp = '0;

  tdr2axi #(
      .USE_AXI_LITE  (1'b1),
      .AXI_ADDR_WIDTH(16),
      .AXI_DATA_WIDTH(32),
      .SYNC_STAGES   (SYNC_STAGES),
      .axi_req_t     (axil_req_t),
      .axi_rsp_t     (axil_resp_t)
  ) u_dut (
      .i_clk            (clk),
      .i_arst_n         (arst_n),
      .i_test_mode      (test_mode),
      .i_arst_scan_n    (arst_scan_n),
      .i_tck            (tck),
      .i_dmi_tdr        ('0),
      .i_dmi_tdr_capture(1'b0),
      .i_dmi_tdr_update (1'b0),
      .o_dmi_tdr        (),
      .o_axi_req        (dut_req),
      .i_axi_rsp        (dut_rsp)
  );

  if (FUNC_MODE) begin : g_func_mode
    // i_test_mode is 0 outside DFT test (S-TDR2AXI-0027, RST-SCAN)
    always_comb asm_reset_functional_mode : assume (!test_mode);
  end

  tdr2axi_reset_sva #(
      .SYNC_STAGES(SYNC_STAGES),
      .FUNC_MODE  (FUNC_MODE)
  ) u_reset_sva (
      .clk_i       (clk),
      .tck_i       (tck),
      .arst_ni     (arst_n),
      .test_mode_i (test_mode),
      .arst_scan_ni(arst_scan_n),
      .arst_clk_ni (u_dut.arst_clk_n),
      .arst_tck_ni (u_dut.arst_tck_n)
  );

endmodule
