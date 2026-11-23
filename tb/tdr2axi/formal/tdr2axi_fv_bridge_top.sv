// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Formal harness for dmi_tdr_bridge alone, on TCK. The job cuts the three
//   cdc_2phase_clearable outputs the bridge reads (cutpoint on u_dut.dmi_req_ready,
//   u_dut.dmi_resp_valid and u_dut.dmi_resp), so the request CDC's ready and the response
//   CDC's valid and payload are free on every TCK cycle, under the assumptions below. The
//   CDCs' other outputs drive nothing here. The TDR value and both strobes are free on every
//   cycle; the model samples each once per TCK rising edge, the only edge the bridge
//   samples them on. Both resets are low until the first TCK rising edge.

module tdr2axi_fv_bridge_top (
    input logic        clk,
    input logic [40:0] dmi_tdr,
    input logic        dmi_tdr_capture,
    input logic        dmi_tdr_update
);

  // Both resets are low until the first TCK rising edge and high from then on
  logic init_q = 1'b1;
  logic rst_n;

  always_ff @(posedge clk) init_q <= 1'b0;
  assign rst_n = !init_q;

  dmi_tdr_bridge #(
      .SYNC_STAGES(3)
  ) u_dut (
      .i_clk            (clk),
      .i_arst_n         (rst_n),
      .i_tck            (clk),
      .i_arst_tck_n     (rst_n),
      .i_dmi_tdr        (dmi_tdr),
      .i_dmi_tdr_capture(dmi_tdr_capture),
      .i_dmi_tdr_update (dmi_tdr_update),
      .o_dmi_tdr        (),
      .o_dmi_req        (),
      .o_dmi_req_valid  (),
      .i_dmi_req_ready  (1'b0),
      .i_dmi_resp       ('0),
      .i_dmi_resp_valid (1'b0),
      .o_dmi_resp_ready ()
  );

  // ---------------------------------------------------------------------------
  // Environment of the CDC ends that the cut frees
  // ---------------------------------------------------------------------------
  logic          req_accept;
  logic          resp_valid;
  dm::dmi_resp_t resp;

  assign req_accept = u_dut.dmi_req_valid && u_dut.dmi_req_ready;
  assign resp_valid = u_dut.dmi_resp_valid;
  assign resp       = u_dut.dmi_resp;

  // A request is owed its response from the cycle after its handshake until the response
  logic owed_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      owed_q <= 1'b0;
    end else if (req_accept) begin
      owed_q <= 1'b1;
    end else if (resp_valid) begin
      owed_q <= 1'b0;
    end
  end

  // The response CDC delivers one response per request the request CDC delivered, and
  // dm_csrs answers each request once (S-TDR2AXI-0007)
  asm_bridge_resp_after_req :
  assume property (@(posedge clk) disable iff (!rst_n) !resp_valid || owed_q);

  // dm_csrs answers 0 or 3 (CHK-TDR-DM-RESP-CODES-SVA, tdr2axi_dm_resp_formal), and the
  // response CDC carries the code unchanged (S-TDR2AXI-0007)
  asm_bridge_resp_code :
  assume property (@(posedge clk) disable iff (!rst_n)
      !resp_valid || (resp.resp == dm::DTM_SUCCESS) || (resp.resp == dm::DTM_BUSY));

endmodule
