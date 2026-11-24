// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Formal harness for the i_clk side of tdr2axi. The DUT's AXI fields are flattened into
//   the plain nets tb_top uses, and AXI_MGR_SVA attaches tdr2axi_axi_mgr_sva to them.
//   Every job reads dmi_tdr_bridge as a black box and cuts it (read_slang
//   --blackboxed-module, cutpoint -blackbox), so the DMI request, its valid and the
//   response ready that tdr2axi takes from the bridge are free on every step; jobs that
//   cut dm_sba and dm_csrs the same way free the memory port and sbaccess. i_axi_rsp is
//   free on every step, the TCK side is tied off, and i_arst_n is low until the first
//   i_clk rising edge.

`include "axi/typedef.svh"

module tdr2axi_fv_top #(
    parameter bit          USE_AXI_LITE   = 1'b0,
    parameter int unsigned AXI_ADDR_WIDTH = 16,
    parameter int unsigned AXI_DATA_WIDTH = 32,
    parameter logic [2:0]  AXI_PROT       = 3'b010,
    parameter logic [3:0]  AXI_CACHE      = 4'b0000,
    parameter int unsigned SYNC_STAGES    = 3,
    // 1: tdr2axi_axi_mgr_sva checks the AXI nets
    parameter bit          AXI_MGR_SVA    = 1'b0,
    // 1: every accepted DMI write of dmcontrol sets dmactive
    parameter bit          DMACTIVE_HOLD  = 1'b0
) (
    input logic clk,

    // AXI response side
    input logic                      aw_ready,
    input logic                      w_ready,
    input logic                      b_valid,
    input logic [               0:0] b_id,
    input logic [               1:0] b_resp,
    input logic [               0:0] b_user,
    input logic                      ar_ready,
    input logic                      r_valid,
    input logic [               0:0] r_id,
    input logic [AXI_DATA_WIDTH-1:0] r_data,
    input logic [               1:0] r_resp,
    input logic                      r_last,
    input logic [               0:0] r_user
);

  localparam int unsigned StrbWidth = AXI_DATA_WIDTH / 8;

  typedef logic [AXI_ADDR_WIDTH-1:0] addr_t;
  typedef logic [AXI_DATA_WIDTH-1:0] data_t;
  typedef logic [StrbWidth-1:0] strb_t;
  typedef logic [0:0] id_t;
  typedef logic [0:0] user_t;

  `AXI_TYPEDEF_ALL(axi, addr_t, id_t, data_t, strb_t, user_t)
  `AXI_LITE_TYPEDEF_ALL(axil, addr_t, data_t, strb_t)

  // i_arst_n is low until the first i_clk rising edge and high from then on
  logic init_q = 1'b1;
  logic rst_n;

  always_ff @(posedge clk) init_q <= 1'b0;
  assign rst_n = !init_q;

  // AXI request side, in tb_top's names; a field AXI4-Lite lacks reads 0
  logic            aw_valid;
  id_t             aw_id;
  addr_t           aw_addr;
  logic     [ 7:0] aw_len;
  logic     [ 2:0] aw_size;
  logic     [ 1:0] aw_burst;
  logic            aw_lock;
  logic     [ 3:0] aw_cache;
  logic     [ 2:0] aw_prot;
  logic     [ 3:0] aw_qos;
  logic     [ 3:0] aw_region;
  logic     [ 5:0] aw_atop;
  user_t           aw_user;
  logic            w_valid;
  data_t           w_data;
  strb_t           w_strb;
  logic            w_last;
  user_t           w_user;
  logic            b_ready;
  logic            ar_valid;
  id_t             ar_id;
  addr_t           ar_addr;
  logic     [ 7:0] ar_len;
  logic     [ 2:0] ar_size;
  logic     [ 1:0] ar_burst;
  logic            ar_lock;
  logic     [ 3:0] ar_cache;
  logic     [ 2:0] ar_prot;
  logic     [ 3:0] ar_qos;
  logic     [ 3:0] ar_region;
  user_t           ar_user;
  logic            r_ready;

  // ---------------------------------------------------------------------------
  // DUT, in the bus form the build selects. Both branches share the name g_bus.
  // ---------------------------------------------------------------------------
  if (!USE_AXI_LITE) begin : g_bus
    axi_req_t  dut_req;
    axi_resp_t dut_rsp;

    tdr2axi #(
        .USE_AXI_LITE  (1'b0),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .AXI_PROT      (AXI_PROT),
        .AXI_CACHE     (AXI_CACHE),
        .SYNC_STAGES   (SYNC_STAGES),
        .axi_req_t     (axi_req_t),
        .axi_rsp_t     (axi_resp_t)
    ) u_dut (
        .i_clk            (clk),
        .i_arst_n         (rst_n),
        .i_test_mode      (1'b0),
        .i_arst_scan_n    (1'b1),
        .i_tck            (1'b0),
        .i_dmi_tdr        ('0),
        .i_dmi_tdr_capture(1'b0),
        .i_dmi_tdr_update (1'b0),
        .o_dmi_tdr        (),
        .o_axi_req        (dut_req),
        .i_axi_rsp        (dut_rsp)
    );

    always_comb begin
      dut_rsp          = '0;
      dut_rsp.aw_ready = aw_ready;
      dut_rsp.w_ready  = w_ready;
      dut_rsp.b_valid  = b_valid;
      dut_rsp.b.id     = b_id;
      dut_rsp.b.resp   = b_resp;
      dut_rsp.b.user   = b_user;
      dut_rsp.ar_ready = ar_ready;
      dut_rsp.r_valid  = r_valid;
      dut_rsp.r.id     = r_id;
      dut_rsp.r.data   = r_data;
      dut_rsp.r.resp   = r_resp;
      dut_rsp.r.last   = r_last;
      dut_rsp.r.user   = r_user;
    end

    assign aw_valid  = dut_req.aw_valid;
    assign aw_id     = dut_req.aw.id;
    assign aw_addr   = dut_req.aw.addr;
    assign aw_len    = dut_req.aw.len;
    assign aw_size   = dut_req.aw.size;
    assign aw_burst  = dut_req.aw.burst;
    assign aw_lock   = dut_req.aw.lock;
    assign aw_cache  = dut_req.aw.cache;
    assign aw_prot   = dut_req.aw.prot;
    assign aw_qos    = dut_req.aw.qos;
    assign aw_region = dut_req.aw.region;
    assign aw_atop   = dut_req.aw.atop;
    assign aw_user   = dut_req.aw.user;
    assign w_valid   = dut_req.w_valid;
    assign w_data    = dut_req.w.data;
    assign w_strb    = dut_req.w.strb;
    assign w_last    = dut_req.w.last;
    assign w_user    = dut_req.w.user;
    assign b_ready   = dut_req.b_ready;
    assign ar_valid  = dut_req.ar_valid;
    assign ar_id     = dut_req.ar.id;
    assign ar_addr   = dut_req.ar.addr;
    assign ar_len    = dut_req.ar.len;
    assign ar_size   = dut_req.ar.size;
    assign ar_burst  = dut_req.ar.burst;
    assign ar_lock   = dut_req.ar.lock;
    assign ar_cache  = dut_req.ar.cache;
    assign ar_prot   = dut_req.ar.prot;
    assign ar_qos    = dut_req.ar.qos;
    assign ar_region = dut_req.ar.region;
    assign ar_user   = dut_req.ar.user;
    assign r_ready   = dut_req.r_ready;
  end else begin : g_bus
    axil_req_t  dut_req;
    axil_resp_t dut_rsp;

    tdr2axi #(
        .USE_AXI_LITE  (1'b1),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .AXI_PROT      (AXI_PROT),
        .AXI_CACHE     (AXI_CACHE),
        .SYNC_STAGES   (SYNC_STAGES),
        .axi_req_t     (axil_req_t),
        .axi_rsp_t     (axil_resp_t)
    ) u_dut (
        .i_clk            (clk),
        .i_arst_n         (rst_n),
        .i_test_mode      (1'b0),
        .i_arst_scan_n    (1'b1),
        .i_tck            (1'b0),
        .i_dmi_tdr        ('0),
        .i_dmi_tdr_capture(1'b0),
        .i_dmi_tdr_update (1'b0),
        .o_dmi_tdr        (),
        .o_axi_req        (dut_req),
        .i_axi_rsp        (dut_rsp)
    );

    always_comb begin
      dut_rsp          = '0;
      dut_rsp.aw_ready = aw_ready;
      dut_rsp.w_ready  = w_ready;
      dut_rsp.b_valid  = b_valid;
      dut_rsp.b.resp   = b_resp;
      dut_rsp.ar_ready = ar_ready;
      dut_rsp.r_valid  = r_valid;
      dut_rsp.r.data   = r_data;
      dut_rsp.r.resp   = r_resp;
    end

    assign aw_valid  = dut_req.aw_valid;
    assign aw_id     = '0;
    assign aw_addr   = dut_req.aw.addr;
    assign aw_len    = '0;
    assign aw_size   = '0;
    assign aw_burst  = '0;
    assign aw_lock   = '0;
    assign aw_cache  = '0;
    assign aw_prot   = dut_req.aw.prot;
    assign aw_qos    = '0;
    assign aw_region = '0;
    assign aw_atop   = '0;
    assign aw_user   = '0;
    assign w_valid   = dut_req.w_valid;
    assign w_data    = dut_req.w.data;
    assign w_strb    = dut_req.w.strb;
    assign w_last    = '0;
    assign w_user    = '0;
    assign b_ready   = dut_req.b_ready;
    assign ar_valid  = dut_req.ar_valid;
    assign ar_id     = '0;
    assign ar_addr   = dut_req.ar.addr;
    assign ar_len    = '0;
    assign ar_size   = '0;
    assign ar_burst  = '0;
    assign ar_lock   = '0;
    assign ar_cache  = '0;
    assign ar_prot   = dut_req.ar.prot;
    assign ar_qos    = '0;
    assign ar_region = '0;
    assign ar_user   = '0;
    assign r_ready   = dut_req.r_ready;
  end

  // ---------------------------------------------------------------------------
  // Environment of the DMI port that the bridge cut frees
  // ---------------------------------------------------------------------------
  if (DMACTIVE_HOLD) begin : g_dmactive_hold
    logic         dmi_req_accept;
    dm::dmi_req_t dmi_req;

    assign dmi_req_accept = g_bus.u_dut.dmi_req_valid && g_bus.u_dut.dmi_req_ready;
    assign dmi_req        = g_bus.u_dut.dmi_req;

    // The debugger writes dmcontrol with dmactive = 1 (S-TDR2AXI-0064); clearing dmactive
    // belongs to the dmactive jobs.
    asm_dmcontrol_keeps_dmactive :
    assume property (@(posedge clk)
        !(dmi_req_accept && (dmi_req.op == dm::DTM_WRITE) && (dmi_req.addr == 7'h10)) ||
        dmi_req.data[0]);
  end

  // ---------------------------------------------------------------------------
  // AXI manager properties
  // ---------------------------------------------------------------------------
  if (AXI_MGR_SVA) begin : g_axi_mgr_sva
    tdr2axi_axi_mgr_sva #(
        .USE_AXI_LITE  (USE_AXI_LITE),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .AXI_PROT      (AXI_PROT),
        .AXI_CACHE     (AXI_CACHE)
    ) u_axi_mgr_sva (
        .clk_i     (clk),
        .rst_ni    (rst_n),
        .en_i      (1'b1),
        .sbaccess_i(g_bus.u_dut.sbaccess),
        .aw_valid  (aw_valid),
        .aw_ready  (aw_ready),
        .aw_id     (aw_id),
        .aw_addr   (aw_addr),
        .aw_len    (aw_len),
        .aw_size   (aw_size),
        .aw_burst  (aw_burst),
        .aw_lock   (aw_lock),
        .aw_cache  (aw_cache),
        .aw_prot   (aw_prot),
        .aw_qos    (aw_qos),
        .aw_region (aw_region),
        .aw_atop   (aw_atop),
        .aw_user   (aw_user),
        .w_valid   (w_valid),
        .w_ready   (w_ready),
        .w_data    (w_data),
        .w_strb    (w_strb),
        .w_last    (w_last),
        .w_user    (w_user),
        .b_valid   (b_valid),
        .b_ready   (b_ready),
        .ar_valid  (ar_valid),
        .ar_ready  (ar_ready),
        .ar_id     (ar_id),
        .ar_addr   (ar_addr),
        .ar_len    (ar_len),
        .ar_size   (ar_size),
        .ar_burst  (ar_burst),
        .ar_lock   (ar_lock),
        .ar_cache  (ar_cache),
        .ar_prot   (ar_prot),
        .ar_qos    (ar_qos),
        .ar_region (ar_region),
        .ar_user   (ar_user),
        .r_valid   (r_valid),
        .r_ready   (r_ready)
    );
  end

endmodule
