// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   AXI manager properties of tdr2axi that need its internal memory port, bound by
//   tdr2axi_axi_resp_sva_bind.sv: dm_sba takes a response exactly at an R or B handshake.
//   The covers reach the antecedents of the tdr2axi_axi_mgr_sva assertions. Property
//   bodies are boolean, with $past for history, so the slang frontend of SymbiYosys loads
//   them. Each failure message starts with its check ID.

module tdr2axi_axi_resp_sva #(
    parameter int unsigned AXI_DATA_WIDTH = 64
) (
    input logic       clk_i,
    // Internal i_clk reset of tdr2axi
    input logic       rst_ni,
    input logic [2:0] sbaccess_i,
    input logic       ar_valid_i,
    input logic       ar_ready_i,
    input logic       aw_valid_i,
    input logic       aw_ready_i,
    input logic       w_valid_i,
    input logic       w_ready_i,
    input logic       r_valid_i,
    input logic       r_ready_i,
    input logic       b_valid_i,
    input logic       b_ready_i,
    // Response handed to dm_sba (master_r_valid_i)
    input logic       rsp_valid_i
);

  wire ar_hs = ar_valid_i && ar_ready_i;
  wire aw_hs = aw_valid_i && aw_ready_i;
  wire r_hs = r_valid_i && r_ready_i;
  wire b_hs = b_valid_i && b_ready_i;

  // Responses handed to dm_sba since reset, saturating at 3
  logic [1:0] rsp_count_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rsp_count_q <= '0;
    end else if (rsp_valid_i && (rsp_count_q != 2'd3)) begin
      rsp_count_q <= rsp_count_q + 2'd1;
    end
  end

  // ---------------------------------------------------------------------------
  // CHK-AXI-HANDSHAKE-SVA
  // ---------------------------------------------------------------------------
  // CHK-AXI-HANDSHAKE-SVA: S-TDR2AXI-0001, S-TDR2AXI-0034, S-TDR2AXI-0035
  ast_axi_handshake_resp_on_handshake :
  assert property (@(posedge clk_i) disable iff (!rst_ni) rsp_valid_i == (r_hs || b_hs))
  else $error("CHK-AXI-HANDSHAKE-SVA FAIL: dm_sba response %b, R handshake %b, B handshake %b",
              rsp_valid_i, r_hs, b_hs);

  cov_axi_handshake_resp_r :
  cover property (@(posedge clk_i) disable iff (!rst_ni) rsp_valid_i && r_hs);

  cov_axi_handshake_resp_b :
  cover property (@(posedge clk_i) disable iff (!rst_ni) rsp_valid_i && b_hs);

  cov_axi_handshake_r_wait :
  cover property (@(posedge clk_i) disable iff (!rst_ni) r_ready_i && !r_valid_i);

  cov_axi_handshake_b_wait :
  cover property (@(posedge clk_i) disable iff (!rst_ni) b_ready_i && !b_valid_i);

  // Antecedents of the VALID-held and payload-stable assertions, and a held request that
  // completes
  cov_axi_handshake_ar_wait :
  cover property (@(posedge clk_i) disable iff (!rst_ni) ar_valid_i && !ar_ready_i);

  cov_axi_handshake_aw_wait :
  cover property (@(posedge clk_i) disable iff (!rst_ni) aw_valid_i && !aw_ready_i);

  cov_axi_handshake_w_wait :
  cover property (@(posedge clk_i) disable iff (!rst_ni) w_valid_i && !w_ready_i);

  cov_axi_handshake_ar_wait_done :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      ar_hs && $past(rst_ni && ar_valid_i && !ar_ready_i));

  cov_axi_handshake_aw_wait_done :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      aw_hs && $past(rst_ni && aw_valid_i && !aw_ready_i));

  cov_axi_handshake_w_wait_done :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      w_valid_i && w_ready_i && $past(rst_ni && w_valid_i && !w_ready_i));

  cov_axi_handshake_in_reset : cover property (@(posedge clk_i) !rst_ni);

  // ---------------------------------------------------------------------------
  // CHK-AXI-ONE-OUTSTANDING-SVA: a request after a completed one
  // ---------------------------------------------------------------------------
  cov_axi_one_outstanding_ar_after_rsp :
  cover property (@(posedge clk_i) disable iff (!rst_ni) ar_hs && (rsp_count_q != 2'd0));

  cov_axi_one_outstanding_aw_after_rsp :
  cover property (@(posedge clk_i) disable iff (!rst_ni) aw_hs && (rsp_count_q != 2'd0));

  // ---------------------------------------------------------------------------
  // CHK-AXI-REQ-ATTRS-SVA and CHK-AXI-AXI4-SIZE-SVA: a request of each access size
  // ---------------------------------------------------------------------------
  cov_axi_req_attrs_w : cover property (@(posedge clk_i) disable iff (!rst_ni) w_valid_i);

  for (genvar size = 0; size <= $clog2(AXI_DATA_WIDTH / 8); size++) begin : g_size
    cov_axi_axi4_size_ar :
    cover property (@(posedge clk_i) disable iff (!rst_ni)
        ar_valid_i && (sbaccess_i == 3'(size)));

    cov_axi_axi4_size_aw :
    cover property (@(posedge clk_i) disable iff (!rst_ni)
        aw_valid_i && (sbaccess_i == 3'(size)));
  end

endmodule
