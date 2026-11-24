// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   TCK-side properties of dmi_tdr_bridge, bound by tdr2axi_bridge_sva_bind.sv: the op
//   decode of an update, the sticky status and the status a capture reads. state_i and
//   error_i carry the bridge's state_e and dmi_error_e encodings. The never-failed
//   assertion holds while the DMI response code is 0 or 3, which tdr2axi_dm_resp_sva
//   proves of dm_csrs. Each failure message starts with its check ID.

module tdr2axi_bridge_sva (
    input logic        clk_i,
    input logic        rst_ni,
    // TDR side: update-stage value, strobes and the capture image
    input logic [40:0] tdr_i,
    input logic        capture_i,
    input logic        update_i,
    input logic [40:0] tdr_in_i,
    // FSM state and sticky status
    input logic [ 2:0] state_i,
    input logic [ 1:0] error_i,
    // Request offered to, and response taken from, the CDCs
    input logic        req_valid_i,
    input logic [ 6:0] req_addr_i,
    input logic [ 1:0] req_op_i,
    input logic [31:0] req_data_i,
    input logic        resp_valid_i,
    input logic [ 1:0] resp_code_i
);

  localparam logic [2:0] StIdle = 3'd0;
  localparam logic [2:0] StRead = 3'd1;
  localparam logic [2:0] StWaitRead = 3'd2;
  localparam logic [2:0] StWaitWrite = 3'd4;

  localparam logic [1:0] OpNop = 2'd0;
  localparam logic [1:0] OpRead = 2'd1;
  localparam logic [1:0] OpWrite = 2'd2;
  localparam logic [1:0] OpClear = 2'd3;

  localparam logic [1:0] StatusOk = 2'd0;
  localparam logic [1:0] StatusFailed = 2'd2;
  localparam logic [1:0] StatusBusy = 2'd3;

  wire  [ 6:0] tdr_addr = tdr_i[40:34];
  wire  [31:0] tdr_data = tdr_i[33:2];
  wire  [ 1:0] tdr_op = tdr_i[1:0];
  wire  [ 1:0] status = tdr_in_i[1:0];

  wire         pending = state_i != StIdle;
  wire         read_pending = (state_i == StRead) || (state_i == StWaitRead);
  wire         resp_waited = (state_i == StWaitRead) || (state_i == StWaitWrite);
  wire         clear = update_i && (tdr_op == OpClear);
  // An update in IDLE with the status clear starts the operation its op names
  wire         start = update_i && !pending && (error_i == StatusOk);

  // ---------------------------------------------------------------------------
  // CHK-TDR-OP-DECODE-SVA
  // ---------------------------------------------------------------------------
  // CHK-TDR-OP-DECODE-SVA: S-TDR2AXI-0030, S-TDR2AXI-0036, S-TDR2AXI-0039,
  // X-RISCV_DEBUG-dmi-format, X-RISCV_DEBUG-dmi-op
  ast_tdr_op_decode_read :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && start && (tdr_op == OpRead)) ||
      (req_valid_i && (req_op_i == OpRead) && (req_addr_i == $past(tdr_addr)) &&
       (req_data_i == $past(tdr_data))))
  else $error("CHK-TDR-OP-DECODE-SVA FAIL: op 1 offered valid %b op %0d addr %h data %h",
              req_valid_i, req_op_i, req_addr_i, req_data_i);

  // CHK-TDR-OP-DECODE-SVA: S-TDR2AXI-0030, S-TDR2AXI-0036, S-TDR2AXI-0039,
  // X-RISCV_DEBUG-dmi-format, X-RISCV_DEBUG-dmi-op
  ast_tdr_op_decode_write :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && start && (tdr_op == OpWrite)) ||
      (req_valid_i && (req_op_i == OpWrite) && (req_addr_i == $past(tdr_addr)) &&
       (req_data_i == $past(tdr_data))))
  else $error("CHK-TDR-OP-DECODE-SVA FAIL: op 2 offered valid %b op %0d addr %h data %h",
              req_valid_i, req_op_i, req_addr_i, req_data_i);

  // CHK-TDR-OP-DECODE-SVA: S-TDR2AXI-0039, S-TDR2AXI-0041, X-RISCV_DEBUG-dmi-op
  ast_tdr_op_decode_no_req :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && start && ((tdr_op == OpNop) || (tdr_op == OpClear))) || !req_valid_i)
  else $error("CHK-TDR-OP-DECODE-SVA FAIL: op 0 or 3 offered a request");

  cov_tdr_op_decode_read :
  cover property (@(posedge clk_i) disable iff (!rst_ni) start && (tdr_op == OpRead));

  cov_tdr_op_decode_write :
  cover property (@(posedge clk_i) disable iff (!rst_ni) start && (tdr_op == OpWrite));

  cov_tdr_op_decode_nop :
  cover property (@(posedge clk_i) disable iff (!rst_ni) start && (tdr_op == OpNop));

  cov_tdr_op_decode_clear :
  cover property (@(posedge clk_i) disable iff (!rst_ni) start && (tdr_op == OpClear));

  // ---------------------------------------------------------------------------
  // CHK-TDR-STATUS-SVA
  // ---------------------------------------------------------------------------
  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0007, S-TDR2AXI-0042, S-TDR2AXI-0043
  ast_tdr_status_busy_on_update :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && update_i && pending && !clear) || (error_i == StatusBusy))
  else $error("CHK-TDR-STATUS-SVA FAIL: status %0d after an update while pending", error_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0007, S-TDR2AXI-0042, S-TDR2AXI-0043
  ast_tdr_status_busy_on_capture :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && capture_i && read_pending && !clear) || (error_i == StatusBusy))
  else $error("CHK-TDR-STATUS-SVA FAIL: status %0d after a capture while a read is pending",
              error_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0042, S-TDR2AXI-0043, X-RISCV_DEBUG-dmi-status
  ast_tdr_status_busy_on_dm_busy :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && resp_valid_i && resp_waited && (resp_code_i == StatusBusy) && !clear) ||
      (error_i == StatusBusy))
  else $error("CHK-TDR-STATUS-SVA FAIL: status %0d after a busy DMI response", error_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0043, S-TDR2AXI-0045
  ast_tdr_status_sticky :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && (error_i == StatusBusy) && !clear) || (error_i == StatusBusy))
  else $error("CHK-TDR-STATUS-SVA FAIL: busy status %0d dropped without op 3", error_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0041, S-TDR2AXI-0045
  ast_tdr_status_op3_clears :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !$past(rst_ni && clear) || (error_i == StatusOk))
  else $error("CHK-TDR-STATUS-SVA FAIL: status %0d after op 3", error_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0039, S-TDR2AXI-0042, S-TDR2AXI-0043
  ast_tdr_status_capture_ok :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !(capture_i && (status == StatusOk)) || ((error_i == StatusOk) && !read_pending))
  else $error("CHK-TDR-STATUS-SVA FAIL: capture read 0 with status %0d, state %0d",
              error_i, state_i);

  // CHK-TDR-STATUS-SVA: S-TDR2AXI-0039, S-TDR2AXI-0043
  ast_tdr_status_capture_busy :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !(capture_i && (error_i == StatusBusy)) || (status == StatusBusy))
  else $error("CHK-TDR-STATUS-SVA FAIL: capture read %0d with the status busy", status);

  cov_tdr_status_busy_on_update :
  cover property (@(posedge clk_i) disable iff (!rst_ni) update_i && pending && !clear);

  cov_tdr_status_busy_on_capture :
  cover property (@(posedge clk_i) disable iff (!rst_ni) capture_i && read_pending);

  cov_tdr_status_busy_on_dm_busy :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      resp_valid_i && resp_waited && (resp_code_i == StatusBusy));

  cov_tdr_status_sticky :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      (error_i == StatusBusy) && update_i && !clear);

  cov_tdr_status_op3_clears :
  cover property (@(posedge clk_i) disable iff (!rst_ni) clear && (error_i == StatusBusy));

  cov_tdr_status_op3_clears_pending :
  cover property (@(posedge clk_i) disable iff (!rst_ni) clear && pending);

  cov_tdr_status_capture_ok :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      capture_i && (status == StatusOk) && $past(rst_ni && resp_valid_i));

  cov_tdr_status_capture_busy :
  cover property (@(posedge clk_i) disable iff (!rst_ni) capture_i && (status == StatusBusy));

  // ---------------------------------------------------------------------------
  // CHK-TDR-DM-RESP-CODES-SVA, TCK side
  // ---------------------------------------------------------------------------
  // CHK-TDR-DM-RESP-CODES-SVA: S-TDR2AXI-0039, S-TDR2AXI-0042, X-RISCV_DEBUG-dmi-status
  ast_tdr_dm_resp_codes_never_failed :
  assert property (@(posedge clk_i) disable iff (!rst_ni) status != StatusFailed)
  else $error("CHK-TDR-DM-RESP-CODES-SVA FAIL: o_dmi_tdr status 2, sticky status %0d",
              error_i);

  cov_tdr_dm_resp_codes_resp_success :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      resp_valid_i && resp_waited && (resp_code_i == StatusOk));

endmodule
