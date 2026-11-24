// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   No combinational path from i_axi_rsp to o_axi_req, for a multiclock formal model in
//   which i_clk is a free clock and i_axi_rsp may change at every step. Bound by
//   tdr2axi_axi_comb_sva_bind.sv. The previous-step values come from tdr2axi_fv_gclk_dly
//   (formal/), which only the multiclock model provides. The assertion is immediate, so it
//   holds at every step of the model. Each failure message starts with its check ID.

module tdr2axi_axi_comb_sva #(
    parameter int unsigned REQ_BITS = 1,
    parameter int unsigned RSP_BITS = 1
) (
    input logic                clk_i,
    input logic [REQ_BITS-1:0] req_i,
    input logic [RSP_BITS-1:0] rsp_i,
    // A request VALID or a response READY is high
    input logic                busy_i
);

  logic                past_valid;
  logic                clk_past;
  logic [REQ_BITS-1:0] req_past;
  logic [RSP_BITS-1:0] rsp_past;

  tdr2axi_fv_gclk_dly u_past_valid (
      .d(1'b1),
      .q(past_valid)
  );

  tdr2axi_fv_gclk_dly u_clk_past (
      .d(clk_i),
      .q(clk_past)
  );

  for (genvar i = 0; i < REQ_BITS; i++) begin : g_req_past
    tdr2axi_fv_gclk_dly u_req_past (
        .d(req_i[i]),
        .q(req_past[i])
    );
  end

  for (genvar i = 0; i < RSP_BITS; i++) begin : g_rsp_past
    tdr2axi_fv_gclk_dly u_rsp_past (
        .d(rsp_i[i]),
        .q(rsp_past[i])
    );
  end

  wire clk_rose = clk_i && !clk_past;

  always_comb begin
    // CHK-AXI-NO-COMB-PATH-SVA: S-TDR2AXI-0034, S-TDR2AXI-0035, X-AXI4-no-comb-path,
    // X-AXI4_LITE-no-comb-path
    ast_axi_no_comb_path_req_stable :
    assert (!past_valid || clk_rose || (req_i == req_past))
    else $error("CHK-AXI-NO-COMB-PATH-SVA FAIL: o_axi_req changed between i_clk rising edges");

    // i_axi_rsp changes between edges while the manager waits on it
    cov_axi_no_comb_path_rsp_change :
    cover (past_valid && !clk_rose && busy_i && (rsp_i != rsp_past));

    cov_axi_no_comb_path_req_change : cover (past_valid && clk_rose && (req_i != req_past));
  end

endmodule
