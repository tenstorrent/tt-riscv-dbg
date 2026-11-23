// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Request payload of an SBA access in flight across dmactive changes, bound by
//   tdr2axi_dmactive_inflight_sva_bind.sv. Each channel's payload is its whole request
//   struct from o_axi_req, in the AXI4 or AXI4-Lite form of the instance. Each failure
//   message starts with its check ID.

module tdr2axi_dmactive_inflight_sva #(
    parameter int unsigned AR_BITS = 1,
    parameter int unsigned AW_BITS = 1,
    parameter int unsigned W_BITS  = 1
) (
    input logic               clk_i,
    // Internal i_clk reset of tdr2axi
    input logic               rst_ni,
    input logic               dmactive_i,
    input logic               ar_valid_i,
    input logic               ar_ready_i,
    input logic [AR_BITS-1:0] ar_i,
    input logic               aw_valid_i,
    input logic               aw_ready_i,
    input logic [AW_BITS-1:0] aw_i,
    input logic               w_valid_i,
    input logic               w_ready_i,
    input logic [ W_BITS-1:0] w_i
);

  // CHK-DM-DMACTIVE-INFLIGHT-SVA: S-TDR2AXI-0056
  ast_dm_dmactive_inflight_ar_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && ar_valid_i && !ar_ready_i) || (ar_i == $past(ar_i)))
  else $error("CHK-DM-DMACTIVE-INFLIGHT-SVA FAIL: AR payload changed while ARVALID waited");

  // CHK-DM-DMACTIVE-INFLIGHT-SVA: S-TDR2AXI-0056
  ast_dm_dmactive_inflight_aw_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && aw_valid_i && !aw_ready_i) || (aw_i == $past(aw_i)))
  else $error("CHK-DM-DMACTIVE-INFLIGHT-SVA FAIL: AW payload changed while AWVALID waited");

  // CHK-DM-DMACTIVE-INFLIGHT-SVA: S-TDR2AXI-0056
  ast_dm_dmactive_inflight_w_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && w_valid_i && !w_ready_i) || (w_i == $past(w_i)))
  else $error("CHK-DM-DMACTIVE-INFLIGHT-SVA FAIL: W payload changed while WVALID waited");

  // dmactive falls while each channel waits for READY
  cov_dm_dmactive_inflight_ar_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      ar_valid_i && !ar_ready_i && !dmactive_i && $past(rst_ni && dmactive_i));

  cov_dm_dmactive_inflight_aw_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      aw_valid_i && !aw_ready_i && !dmactive_i && $past(rst_ni && dmactive_i));

  cov_dm_dmactive_inflight_w_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      w_valid_i && !w_ready_i && !dmactive_i && $past(rst_ni && dmactive_i));

endmodule
