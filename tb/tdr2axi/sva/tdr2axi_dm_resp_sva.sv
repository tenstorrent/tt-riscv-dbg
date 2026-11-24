// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Response codes of the Debug Module on the i_clk side of tdr2axi, bound by
//   tdr2axi_dm_resp_sva_bind.sv. tdr2axi_bridge_sva assumes the same codes at the TCK end
//   of the response CDC, which carries the response unchanged. Each failure message starts
//   with its check ID.

module tdr2axi_dm_resp_sva (
    input logic       clk_i,
    // Internal i_clk reset of tdr2axi
    input logic       rst_ni,
    input logic       resp_valid_i,
    input logic [1:0] resp_i
);

  localparam logic [1:0] RespSuccess = 2'd0;
  localparam logic [1:0] RespBusy = 2'd3;

  // CHK-TDR-DM-RESP-CODES-SVA: S-TDR2AXI-0039, S-TDR2AXI-0042, X-RISCV_DEBUG-dmi-status
  ast_tdr_dm_resp_codes_dm_answer :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !resp_valid_i || (resp_i == RespSuccess) || (resp_i == RespBusy))
  else $error("CHK-TDR-DM-RESP-CODES-SVA FAIL: DMI response code %0d", resp_i);

  cov_tdr_dm_resp_codes_success :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      resp_valid_i && (resp_i == RespSuccess));

  cov_tdr_dm_resp_codes_busy :
  cover property (@(posedge clk_i) disable iff (!rst_ni) resp_valid_i && (resp_i == RespBusy));

endmodule
