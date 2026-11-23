// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   AXI4 / AXI4-Lite manager properties of tdr2axi on the flattened AXI nets of
//   tb_top, in the form USE_AXI_LITE selects. Every property is gated by en_i, which
//   the bench raises after the first i_clk edge of reset; a formal harness ties it to
//   1 and drives the same ports. Property bodies are boolean, with $past for the
//   previous cycle, because the slang frontend of the open formal path accepts no
//   implication or sequence operator. Each failure message starts with its check ID, and
//   each failure increments the fail_* counter of its check. The hit_* counter of each
//   check counts the enabled cycles in which one of its antecedents holds.

module tdr2axi_axi_mgr_sva #(
    parameter bit          USE_AXI_LITE   = 1'b0,
    parameter int unsigned AXI_ADDR_WIDTH = 64,
    parameter int unsigned AXI_DATA_WIDTH = 64,
    parameter logic [2:0]  AXI_PROT       = 3'b010,
    parameter logic [3:0]  AXI_CACHE      = 4'b0000
) (
    input logic                        clk_i,
    input logic                        rst_ni,
    input logic                        en_i,
    input logic [                 2:0] sbaccess_i,
    input logic                        aw_valid,
    input logic                        aw_ready,
    input logic [                 0:0] aw_id,
    input logic [  AXI_ADDR_WIDTH-1:0] aw_addr,
    input logic [                 7:0] aw_len,
    input logic [                 2:0] aw_size,
    input logic [                 1:0] aw_burst,
    input logic                        aw_lock,
    input logic [                 3:0] aw_cache,
    input logic [                 2:0] aw_prot,
    input logic [                 3:0] aw_qos,
    input logic [                 3:0] aw_region,
    input logic [                 5:0] aw_atop,
    input logic [                 0:0] aw_user,
    input logic                        w_valid,
    input logic                        w_ready,
    input logic [  AXI_DATA_WIDTH-1:0] w_data,
    input logic [AXI_DATA_WIDTH/8-1:0] w_strb,
    input logic                        w_last,
    input logic [                 0:0] w_user,
    input logic                        b_valid,
    input logic                        b_ready,
    input logic                        ar_valid,
    input logic                        ar_ready,
    input logic [                 0:0] ar_id,
    input logic [  AXI_ADDR_WIDTH-1:0] ar_addr,
    input logic [                 7:0] ar_len,
    input logic [                 2:0] ar_size,
    input logic [                 1:0] ar_burst,
    input logic                        ar_lock,
    input logic [                 3:0] ar_cache,
    input logic [                 2:0] ar_prot,
    input logic [                 3:0] ar_qos,
    input logic [                 3:0] ar_region,
    input logic [                 0:0] ar_user,
    input logic                        r_valid,
    input logic                        r_ready
);

  // Failures of each check since time 0; tb_top exposes them and the base test fails the
  // test during which one rises
  int unsigned fail_handshake = 0;
  int unsigned fail_one_outstanding = 0;
  int unsigned fail_req_attrs = 0;
  int unsigned fail_axi4_size = 0;

  // Enabled cycles in which an antecedent of each check holds, so a test tells a check its
  // stimulus reached from a vacuous one. CHK-AXI-HANDSHAKE-SVA has two: hit_handshake
  // counts a request VALID waiting for its READY, hit_resp_owed an R or B owed. Each is
  // 2-state and starts at 0; a variable an always_ff drives takes no declaration initializer.
  int unsigned hit_handshake;
  int unsigned hit_resp_owed;
  int unsigned hit_one_outstanding;
  int unsigned hit_req_attrs;
  int unsigned hit_axi4_size;

  // Every request field of each channel, so one comparison covers the whole payload
  localparam int unsigned AwBits = AXI_ADDR_WIDTH + 37;
  localparam int unsigned ArBits = AXI_ADDR_WIDTH + 31;
  localparam int unsigned WBits = AXI_DATA_WIDTH + AXI_DATA_WIDTH / 8 + 2;

  wire [AwBits-1:0] aw_payload = {
    aw_id, aw_addr, aw_len, aw_size, aw_burst, aw_lock, aw_cache, aw_prot, aw_qos, aw_region,
    aw_atop, aw_user
  };
  wire [ArBits-1:0] ar_payload = {
    ar_id, ar_addr, ar_len, ar_size, ar_burst, ar_lock, ar_cache, ar_prot, ar_qos, ar_region,
    ar_user
  };
  wire [WBits-1:0] w_payload = {w_data, w_strb, w_last, w_user};

  wire ar_hs = ar_valid && ar_ready;
  wire aw_hs = aw_valid && aw_ready;
  wire r_hs = r_valid && r_ready;
  wire b_hs = b_valid && b_ready;

  // A read is owed its R from the AR handshake to the R handshake, a write its B from
  // the AW handshake to the B handshake.
  logic rd_owed_q;
  logic wr_owed_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rd_owed_q <= 1'b0;
      wr_owed_q <= 1'b0;
    end else begin
      if (ar_hs) rd_owed_q <= 1'b1;
      else if (r_hs) rd_owed_q <= 1'b0;
      if (aw_hs) wr_owed_q <= 1'b1;
      else if (b_hs) wr_owed_q <= 1'b0;
    end
  end

  // armed_q is high while rst_ni && en_i has held since the last rising edge. disable iff
  // ends a pending attempt of a next-cycle check the moment either falls, between edges
  // too, so each such check needs armed_q as well as its antecedent at the last edge.
  wire  armed = rst_ni && en_i;
  logic armed_q;

  always_ff @(posedge clk_i or negedge armed) begin
    if (!armed) armed_q <= 1'b0;
    else armed_q <= 1'b1;
  end

  wire any_valid = ar_valid || aw_valid || w_valid;
  // A request VALID waiting for its READY: the antecedent of the held-VALID and
  // stable-payload properties
  wire any_wait = (ar_valid && !ar_ready) || (aw_valid && !aw_ready) || (w_valid && !w_ready);

  always_ff @(posedge clk_i) begin
    if (rst_ni && en_i) begin
      if (any_wait) hit_handshake <= hit_handshake + 1;
      if (rd_owed_q || wr_owed_q) hit_resp_owed <= hit_resp_owed + 1;
      if (ar_hs || aw_hs) hit_one_outstanding <= hit_one_outstanding + 1;
      if (any_valid) hit_req_attrs <= hit_req_attrs + 1;
      if (!USE_AXI_LITE && (ar_valid || aw_valid)) hit_axi4_size <= hit_axi4_size + 1;
    end
  end

  // ---------------------------------------------------------------------------
  // CHK-AXI-HANDSHAKE-SVA
  // ---------------------------------------------------------------------------
  a_ar_valid_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(ar_valid && !ar_ready)) || ar_valid)
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: ARVALID dropped before ARREADY");
  end

  a_aw_valid_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(aw_valid && !aw_ready)) || aw_valid)
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: AWVALID dropped before AWREADY");
  end

  a_w_valid_held :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(w_valid && !w_ready)) || w_valid)
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: WVALID dropped before WREADY");
  end

  a_ar_payload_stable :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(ar_valid && !ar_ready)) || (ar_payload === $past(ar_payload)))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: AR payload changed while ARVALID waited for ARREADY");
  end

  a_aw_payload_stable :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(aw_valid && !aw_ready)) || (aw_payload === $past(aw_payload)))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: AW payload changed while AWVALID waited for AWREADY");
  end

  a_w_payload_stable :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !(armed_q && $past(w_valid && !w_ready)) || (w_payload === $past(w_payload)))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: W payload changed while WVALID waited for WREADY");
  end

  // The antecedent of the valid_held and payload_stable checks of each channel, armed_q
  // included
  cov_ar_valid_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      armed_q && $past(ar_valid && !ar_ready));

  cov_aw_valid_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      armed_q && $past(aw_valid && !aw_ready));

  cov_w_valid_held :
  cover property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      armed_q && $past(w_valid && !w_ready));

  a_no_valid_in_reset :
  assert property (@(posedge clk_i) disable iff (!en_i)
      rst_ni || (!ar_valid && !aw_valid && !w_valid))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: a request VALID is high in reset");
  end

  // R and B complete only at their handshake with RREADY and BREADY: with one transaction
  // outstanding, no AR or AW VALID rises while an R or B is owed
  a_no_req_while_rd_owed :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !rd_owed_q || (!ar_valid && !aw_valid))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: ARVALID or AWVALID before the R handshake of the read");
  end

  a_no_req_while_wr_owed :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !wr_owed_q || (!ar_valid && !aw_valid))
  else begin
    fail_handshake++;
    $error("CHK-AXI-HANDSHAKE-SVA FAIL: ARVALID or AWVALID before the B handshake of the write");
  end

  cov_no_req_while_rd_owed :
  cover property (@(posedge clk_i) disable iff (!rst_ni || !en_i) rd_owed_q);

  cov_no_req_while_wr_owed :
  cover property (@(posedge clk_i) disable iff (!rst_ni || !en_i) wr_owed_q);

  // ---------------------------------------------------------------------------
  // CHK-AXI-ONE-OUTSTANDING-SVA
  // ---------------------------------------------------------------------------
  a_ar_one_outstanding :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !ar_hs || (!rd_owed_q && !wr_owed_q))
  else begin
    fail_one_outstanding++;
    $error("CHK-AXI-ONE-OUTSTANDING-SVA FAIL: AR handshake while a response is owed");
  end

  a_aw_one_outstanding :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !aw_hs || (!rd_owed_q && !wr_owed_q))
  else begin
    fail_one_outstanding++;
    $error("CHK-AXI-ONE-OUTSTANDING-SVA FAIL: AW handshake while a response is owed");
  end

  if (!USE_AXI_LITE) begin : g_axi4_beats
    a_ar_single_beat :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !ar_valid || (ar_len == 8'd0))
    else begin
      fail_one_outstanding++;
      $error("CHK-AXI-ONE-OUTSTANDING-SVA FAIL: ARLEN %0d", ar_len);
    end

    a_aw_single_beat :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !aw_valid || (aw_len == 8'd0))
    else begin
      fail_one_outstanding++;
      $error("CHK-AXI-ONE-OUTSTANDING-SVA FAIL: AWLEN %0d", aw_len);
    end

    a_w_last :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i) !w_valid || w_last)
    else begin
      fail_one_outstanding++;
      $error("CHK-AXI-ONE-OUTSTANDING-SVA FAIL: WLAST low on a single-beat write");
    end
  end

  // ---------------------------------------------------------------------------
  // CHK-AXI-REQ-ATTRS-SVA
  // ---------------------------------------------------------------------------
  a_ar_prot :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !ar_valid || (ar_prot == AXI_PROT))
  else begin
    fail_req_attrs++;
    $error("CHK-AXI-REQ-ATTRS-SVA FAIL: ARPROT %b, AXI_PROT %b", ar_prot, AXI_PROT);
  end

  a_aw_prot :
  assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
      !aw_valid || (aw_prot == AXI_PROT))
  else begin
    fail_req_attrs++;
    $error("CHK-AXI-REQ-ATTRS-SVA FAIL: AWPROT %b, AXI_PROT %b", aw_prot, AXI_PROT);
  end

  if (!USE_AXI_LITE) begin : g_axi4_attrs
    a_ar_attrs :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !ar_valid || ((ar_cache == AXI_CACHE) && (ar_id == '0) && (ar_user == '0)))
    else begin
      fail_req_attrs++;
      $error("CHK-AXI-REQ-ATTRS-SVA FAIL: ARCACHE %b ARID %0d ARUSER %0d, AXI_CACHE %b",
                  ar_cache, ar_id, ar_user, AXI_CACHE);
    end

    a_aw_attrs :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !aw_valid || ((aw_cache == AXI_CACHE) && (aw_id == '0) && (aw_user == '0)))
    else begin
      fail_req_attrs++;
      $error("CHK-AXI-REQ-ATTRS-SVA FAIL: AWCACHE %b AWID %0d AWUSER %0d, AXI_CACHE %b",
                  aw_cache, aw_id, aw_user, AXI_CACHE);
    end

    a_w_user :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !w_valid || (w_user == '0))
    else begin
      fail_req_attrs++;
      $error("CHK-AXI-REQ-ATTRS-SVA FAIL: WUSER %0d", w_user);
    end

    // CHK-AXI-AXI4-SIZE-SVA
    a_ar_size :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !ar_valid || (ar_size == sbaccess_i))
    else begin
      fail_axi4_size++;
      $error("CHK-AXI-AXI4-SIZE-SVA FAIL: ARSIZE %0d, sbaccess %0d", ar_size, sbaccess_i);
    end

    a_aw_size :
    assert property (@(posedge clk_i) disable iff (!rst_ni || !en_i)
        !aw_valid || (aw_size == sbaccess_i))
    else begin
      fail_axi4_size++;
      $error("CHK-AXI-AXI4-SIZE-SVA FAIL: AWSIZE %0d, sbaccess %0d", aw_size, sbaccess_i);
    end
  end

endmodule
