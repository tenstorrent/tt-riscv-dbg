// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   cocotb top for tdr2axi: the DUT with every TDR and AXI field flattened into plain
//   nets, the reset and test-mode pins, the build parameters as read-only nets, and
//   observables of DUT-internal nets for checks and coverage. The test drives the TDR
//   side and plays the AXI subordinate. AXI4 and AXI4-Lite builds expose the same
//   names; a field AXI4-Lite lacks reads 0, and its response inputs are unused.
//   tdr2axi_axi_mgr_sva watches the AXI nets while sva_en is high; tdr2axi_fcov
//   collects the functional coverage in every simulator. Every assertion failure
//   increments the sva_fail_* counter of its check, which the test reads, so a failure
//   fails the running test whether or not the simulator stops on $error; the sva_hit_*
//   counters of each check count the cycles its antecedents held, and the end of the run
//   prints every sva_hit_* count. AXI_REQ_DATA_WIDTH or AXI_RSP_DATA_WIDTH at any value
//   other than 0 or AXI_DATA_WIDTH, and AXI_DEFAULT_TYPES = 1, build struct types the
//   parameter checks of tdr2axi reject.

`timescale 1ps / 1ps

`include "axi/typedef.svh"

module tb_top #(
    parameter bit          USE_AXI_LITE   = 1'b0,
    parameter int unsigned AXI_ADDR_WIDTH = 64,
    parameter int unsigned AXI_DATA_WIDTH = 64,
    parameter logic [2:0]  AXI_PROT       = 3'b010,
    parameter logic [3:0]  AXI_CACHE      = 4'b0000,
    parameter int unsigned SYNC_STAGES    = 3,
    // Data width of the W channel of the request struct and of the R channel of the
    // response struct handed to tdr2axi; 0 is AXI_DATA_WIDTH
    parameter int unsigned AXI_REQ_DATA_WIDTH = 0,
    parameter int unsigned AXI_RSP_DATA_WIDTH = 0,
    // 1 leaves the axi_req_t and axi_rsp_t of tdr2axi at their logic defaults
    parameter bit          AXI_DEFAULT_TYPES  = 1'b0
);

  localparam int unsigned StrbWidth = AXI_DATA_WIDTH / 8;
  localparam int unsigned ReqDataWidth =
      AXI_REQ_DATA_WIDTH != 0 ? AXI_REQ_DATA_WIDTH : AXI_DATA_WIDTH;
  localparam int unsigned RspDataWidth =
      AXI_RSP_DATA_WIDTH != 0 ? AXI_RSP_DATA_WIDTH : AXI_DATA_WIDTH;

  typedef logic [AXI_ADDR_WIDTH-1:0] addr_t;
  typedef logic [AXI_DATA_WIDTH-1:0] data_t;
  typedef logic [StrbWidth-1:0] strb_t;
  typedef logic [ReqDataWidth-1:0] req_data_t;
  typedef logic [ReqDataWidth/8-1:0] req_strb_t;
  typedef logic [RspDataWidth-1:0] rsp_data_t;
  typedef logic [0:0] id_t;
  typedef logic [0:0] user_t;

  `AXI_TYPEDEF_AW_CHAN_T(axi_aw_chan_t, addr_t, id_t, user_t)
  `AXI_TYPEDEF_W_CHAN_T(axi_w_chan_t, req_data_t, req_strb_t, user_t)
  `AXI_TYPEDEF_B_CHAN_T(axi_b_chan_t, id_t, user_t)
  `AXI_TYPEDEF_AR_CHAN_T(axi_ar_chan_t, addr_t, id_t, user_t)
  `AXI_TYPEDEF_R_CHAN_T(axi_r_chan_t, rsp_data_t, id_t, user_t)
  `AXI_TYPEDEF_REQ_T(axi_req_t, axi_aw_chan_t, axi_w_chan_t, axi_ar_chan_t)
  `AXI_TYPEDEF_RESP_T(axi_resp_t, axi_b_chan_t, axi_r_chan_t)

  `AXI_LITE_TYPEDEF_AW_CHAN_T(axil_aw_chan_t, addr_t)
  `AXI_LITE_TYPEDEF_W_CHAN_T(axil_w_chan_t, req_data_t, req_strb_t)
  `AXI_LITE_TYPEDEF_B_CHAN_T(axil_b_chan_t)
  `AXI_LITE_TYPEDEF_AR_CHAN_T(axil_ar_chan_t, addr_t)
  `AXI_LITE_TYPEDEF_R_CHAN_T(axil_r_chan_t, rsp_data_t)
  `AXI_LITE_TYPEDEF_REQ_T(axil_req_t, axil_aw_chan_t, axil_w_chan_t, axil_ar_chan_t)
  `AXI_LITE_TYPEDEF_RESP_T(axil_resp_t, axil_b_chan_t, axil_r_chan_t)

  // ---------------------------------------------------------------------------
  // Build configuration, read back by the test
  // ---------------------------------------------------------------------------
  wire        cfg_axi_lite = USE_AXI_LITE;
  wire [31:0] cfg_addr_width = AXI_ADDR_WIDTH;
  wire [31:0] cfg_data_width = AXI_DATA_WIDTH;
  wire [ 2:0] cfg_axi_prot = AXI_PROT;
  wire [ 3:0] cfg_axi_cache = AXI_CACHE;
  wire [31:0] cfg_sync_stages = SYNC_STAGES;

  // ---------------------------------------------------------------------------
  // Driven by the test
  // ---------------------------------------------------------------------------
  logic [8*32-1:0] test_name;  // ASCII name of the running test, for the waveform
  logic            sva_en;  // arms tdr2axi_axi_mgr_sva and the TDR update assertion

  // 0 from time 0 on every simulator, so the test's first writes make no reset or mode edge,
  // which the reset watchers would answer by reading an unknown rst_n
  logic            clk = 1'b0;
  logic            rst_n = 1'b0;
  logic            test_mode = 1'b0;
  logic            rst_scan_n = 1'b0;

  logic            tck;
  logic     [40:0] tdr_upd_q;
  logic            tdr_upd;
  logic            tdr_cap;

  // AXI response side
  logic            aw_ready;
  logic            w_ready;
  logic            b_valid;
  id_t             b_id;
  logic     [ 1:0] b_resp;
  user_t           b_user;
  logic            ar_ready;
  logic            r_valid;
  id_t             r_id;
  data_t           r_data;
  logic     [ 1:0] r_resp;
  logic            r_last;
  user_t           r_user;

  // ---------------------------------------------------------------------------
  // Read by the test
  // ---------------------------------------------------------------------------
  logic     [40:0] tdr_in;

  // AXI request side
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
  // DUT, in the bus form the build selects. Every branch shares the name g_bus.
  // ---------------------------------------------------------------------------
  if (AXI_DEFAULT_TYPES) begin : g_bus
    // tdr2axi rejects its default struct types at elaboration, so the request nets of
    // this branch stay undriven
    tdr2axi #(
        .USE_AXI_LITE  (USE_AXI_LITE),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .AXI_PROT      (AXI_PROT),
        .AXI_CACHE     (AXI_CACHE),
        .SYNC_STAGES   (SYNC_STAGES)
    ) u_dut (
        .i_clk            (clk),
        .i_arst_n         (rst_n),
        .i_test_mode      (test_mode),
        .i_arst_scan_n    (rst_scan_n),
        .i_tck            (tck),
        .i_dmi_tdr        (tdr_upd_q),
        .i_dmi_tdr_capture(tdr_cap),
        .i_dmi_tdr_update (tdr_upd),
        .o_dmi_tdr        (tdr_in),
        .o_axi_req        (),
        .i_axi_rsp        (1'b0)
    );
  end else if (!USE_AXI_LITE) begin : g_bus
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
        .i_test_mode      (test_mode),
        .i_arst_scan_n    (rst_scan_n),
        .i_tck            (tck),
        .i_dmi_tdr        (tdr_upd_q),
        .i_dmi_tdr_capture(tdr_cap),
        .i_dmi_tdr_update (tdr_upd),
        .o_dmi_tdr        (tdr_in),
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
        .i_test_mode      (test_mode),
        .i_arst_scan_n    (rst_scan_n),
        .i_tck            (tck),
        .i_dmi_tdr        (tdr_upd_q),
        .i_dmi_tdr_capture(tdr_cap),
        .i_dmi_tdr_update (tdr_upd),
        .o_dmi_tdr        (tdr_in),
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
  // Observables: DUT-internal nets for the checks and the coverage module
  // ---------------------------------------------------------------------------
  // DMI request and response between the bridge and u_dm_csrs (i_clk side)
  logic        dmi_req_valid;
  logic        dmi_req_ready;
  logic [ 6:0] dmi_req_addr;
  logic [ 1:0] dmi_req_op;
  logic [31:0] dmi_req_data;
  logic        dmi_resp_valid;
  logic        dmi_resp_ready;
  logic [31:0] dmi_resp_data;
  logic [ 1:0] dmi_resp_resp;
  // Bridge state machine and registers (TCK side)
  logic [ 2:0] bridge_state;
  logic [ 1:0] bridge_error;
  logic [ 6:0] bridge_address;
  logic [31:0] bridge_data;
  // Request offered to the request CDC, and the response leaving the response CDC (TCK)
  logic        cdc_req_src_valid;
  logic        cdc_req_src_ready;
  logic        cdc_resp_dst_valid;
  // Clear sequences of both CDCs
  logic        cdc_req_src_clear_pending;
  logic        cdc_req_dst_clear_pending;
  logic        cdc_resp_src_clear_pending;
  logic        cdc_resp_dst_clear_pending;
  // Internal resets
  logic        arst_clk_n;
  logic        arst_tck_n;
  // Debug Module and SBA controls
  logic        dmactive;
  logic [ 2:0] sbaccess;
  logic        sbautoincrement;
  logic        sbreadonaddr;
  logic        sbreadondata;
  logic        sbbusy;
  // dm_sba's error report, nonzero only on the cycle that sets sbcs.sberror
  logic [ 2:0] sberror;
  // sbcs.sberror as u_dm_csrs holds it, from the error until the debugger clears it
  logic [ 2:0] sbcs_sberror;
  // sbcs.sbbusyerror as u_dm_csrs holds it, from the refused access until the debugger
  // clears it
  logic        sbcs_sbbusyerror;

  assign dmi_req_valid              = g_bus.u_dut.dmi_req_valid;
  assign dmi_req_ready              = g_bus.u_dut.dmi_req_ready;
  assign dmi_req_addr               = g_bus.u_dut.dmi_req.addr;
  assign dmi_req_op                 = g_bus.u_dut.dmi_req.op;
  assign dmi_req_data               = g_bus.u_dut.dmi_req.data;
  assign dmi_resp_valid             = g_bus.u_dut.dmi_resp_valid;
  assign dmi_resp_ready             = g_bus.u_dut.dmi_resp_ready;
  assign dmi_resp_data              = g_bus.u_dut.dmi_resp.data;
  assign dmi_resp_resp              = g_bus.u_dut.dmi_resp.resp;
  assign bridge_state               = g_bus.u_dut.u_dmi_tdr_bridge.state_q;
  assign bridge_error               = g_bus.u_dut.u_dmi_tdr_bridge.error_q;
  assign bridge_address             = g_bus.u_dut.u_dmi_tdr_bridge.address_q;
  assign bridge_data                = g_bus.u_dut.u_dmi_tdr_bridge.data_q;
  assign cdc_req_src_valid          = g_bus.u_dut.u_dmi_tdr_bridge.dmi_req_valid;
  assign cdc_req_src_ready          = g_bus.u_dut.u_dmi_tdr_bridge.dmi_req_ready;
  assign cdc_resp_dst_valid         = g_bus.u_dut.u_dmi_tdr_bridge.dmi_resp_valid;
  assign cdc_req_src_clear_pending  = g_bus.u_dut.u_dmi_tdr_bridge.u_cdc_req.src_clear_pending_o;
  assign cdc_req_dst_clear_pending  = g_bus.u_dut.u_dmi_tdr_bridge.u_cdc_req.dst_clear_pending_o;
  assign cdc_resp_src_clear_pending = g_bus.u_dut.u_dmi_tdr_bridge.u_cdc_resp.src_clear_pending_o;
  assign cdc_resp_dst_clear_pending = g_bus.u_dut.u_dmi_tdr_bridge.u_cdc_resp.dst_clear_pending_o;
  assign arst_clk_n                 = g_bus.u_dut.arst_clk_n;
  assign arst_tck_n                 = g_bus.u_dut.arst_tck_n;
  assign dmactive                   = g_bus.u_dut.dmactive;
  assign sbaccess                   = g_bus.u_dut.sbaccess;
  assign sbautoincrement            = g_bus.u_dut.sbautoincrement;
  assign sbreadonaddr               = g_bus.u_dut.sbreadonaddr;
  assign sbreadondata               = g_bus.u_dut.sbreadondata;
  assign sbbusy                     = g_bus.u_dut.sbbusy;
  assign sberror                    = g_bus.u_dut.sberror;
  assign sbcs_sberror               = g_bus.u_dut.u_dm_csrs.sbcs_q.sberror;
  assign sbcs_sbbusyerror           = g_bus.u_dut.u_dm_csrs.sbcs_q.sbbusyerror;

  // ---------------------------------------------------------------------------
  // Assertions, and the failure and antecedent counts of each assertion check since
  // time 0
  // ---------------------------------------------------------------------------
  int unsigned sva_fail_handshake;
  int unsigned sva_fail_one_outstanding;
  int unsigned sva_fail_req_attrs;
  int unsigned sva_fail_axi4_size;
  int unsigned sva_fail_tdr_update_stable = 0;
  int unsigned sva_hit_handshake;
  int unsigned sva_hit_resp_owed;
  int unsigned sva_hit_one_outstanding;
  int unsigned sva_hit_req_attrs;
  int unsigned sva_hit_axi4_size;
  int unsigned sva_hit_tdr_update_stable = 0;

  assign sva_fail_handshake       = u_axi_mgr_sva.fail_handshake;
  assign sva_fail_one_outstanding = u_axi_mgr_sva.fail_one_outstanding;
  assign sva_fail_req_attrs       = u_axi_mgr_sva.fail_req_attrs;
  assign sva_fail_axi4_size       = u_axi_mgr_sva.fail_axi4_size;
  assign sva_hit_handshake        = u_axi_mgr_sva.hit_handshake;
  assign sva_hit_resp_owed        = u_axi_mgr_sva.hit_resp_owed;
  assign sva_hit_one_outstanding  = u_axi_mgr_sva.hit_one_outstanding;
  assign sva_hit_req_attrs        = u_axi_mgr_sva.hit_req_attrs;
  assign sva_hit_axi4_size        = u_axi_mgr_sva.hit_axi4_size;

  tdr2axi_axi_mgr_sva #(
      .USE_AXI_LITE  (USE_AXI_LITE),
      .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
      .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
      .AXI_PROT      (AXI_PROT),
      .AXI_CACHE     (AXI_CACHE)
  ) u_axi_mgr_sva (
      .clk_i     (clk),
      .rst_ni    (rst_n),
      .en_i      (sva_en),
      .sbaccess_i(sbaccess),
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

  // CHK-ENV-TDR-UPDATE-STABLE: i_dmi_tdr is known and unchanged from before each TCK
  // rising edge with i_dmi_tdr_update high until the falling edge after it.
  realtime     tdr_upd_q_changed_at;
  realtime     tck_rose_at;
  logic        tdr_upd_sampled;
  logic [40:0] tdr_upd_q_at_rise;

  always @(tdr_upd_q) tdr_upd_q_changed_at = $realtime;

  always @(posedge tck) begin
    tck_rose_at       = $realtime;
    tdr_upd_sampled   = sva_en && tdr_upd;
    tdr_upd_q_at_rise = tdr_upd_q;
    if (tdr_upd_sampled) begin
      sva_hit_tdr_update_stable++;
      a_tdr_update_setup :
      assert (!$isunknown(tdr_upd_q) && (tdr_upd_q_changed_at < tck_rose_at))
      else begin
        sva_fail_tdr_update_stable++;
        $error(
            "CHK-ENV-TDR-UPDATE-STABLE FAIL: i_dmi_tdr %h changed at %0t, update edge at %0t",
            tdr_upd_q,
            tdr_upd_q_changed_at,
            tck_rose_at
        );
      end
    end
  end

  always @(negedge tck) begin
    if (tdr_upd_sampled) begin
      a_tdr_update_hold :
      assert (tdr_upd_q === tdr_upd_q_at_rise)
      else begin
        sva_fail_tdr_update_stable++;
        $error(
            "CHK-ENV-TDR-UPDATE-STABLE FAIL: i_dmi_tdr %h at the update edge, %h when TCK falls",
            tdr_upd_q_at_rise,
            tdr_upd_q
        );
      end
    end
    tdr_upd_sampled = 1'b0;
  end

  // VCD of the bench and the DUT into the file +TDR2AXI_VCD names; Verilator dumps through
  // its own traced build instead
`ifndef VERILATOR
  initial begin
    string vcd_file;
    if ($value$plusargs("TDR2AXI_VCD=%s", vcd_file)) begin
      $dumpfile(vcd_file);
      $dumpvars(0, tb_top);
    end
  end
`endif

  final begin
    $display("tb_top assertion antecedent hits: handshake %0d resp_owed %0d one_outstanding %0d",
             sva_hit_handshake, sva_hit_resp_owed, sva_hit_one_outstanding);
    $display("tb_top assertion antecedent hits: req_attrs %0d axi4_size %0d tdr_update_stable %0d",
             sva_hit_req_attrs, sva_hit_axi4_size, sva_hit_tdr_update_stable);
  end

  // ---------------------------------------------------------------------------
  // Functional coverage
  // ---------------------------------------------------------------------------
  tdr2axi_fcov #(
      .USE_AXI_LITE  (USE_AXI_LITE),
      .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
      .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
      .SYNC_STAGES   (SYNC_STAGES)
  ) u_fcov (
      .*
  );

endmodule
