// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Functional coverage of tdr2axi, instantiated in tb_top with .* so its ports are
//   the top's nets and observables of the same name. It reads DUT nets only, never
//   test variables. Each cover property is labeled with its point ID and covers one
//   continuous-assign wire, so every simulator samples the same expression; the
//   covergroups sit under `ifndef VERILATOR. Clock ratios come from the measured
//   periods of i_clk and i_tck, and a stopped clock from the edges of the other one.

module tdr2axi_fcov #(
    parameter bit          USE_AXI_LITE   = 1'b0,
    parameter int unsigned AXI_ADDR_WIDTH = 64,
    parameter int unsigned AXI_DATA_WIDTH = 64,
    parameter int unsigned SYNC_STAGES    = 3
) (
    input logic                        clk,
    input logic                        rst_n,
    input logic                        test_mode,
    input logic                        rst_scan_n,
    input logic                        tck,
    input logic [                40:0] tdr_upd_q,
    input logic                        tdr_upd,
    input logic                        tdr_cap,
    input logic [                40:0] tdr_in,
    input logic                        aw_valid,
    input logic                        aw_ready,
    input logic [  AXI_ADDR_WIDTH-1:0] aw_addr,
    input logic [                 3:0] aw_cache,
    input logic [                 2:0] aw_prot,
    input logic                        w_valid,
    input logic                        w_ready,
    input logic [AXI_DATA_WIDTH/8-1:0] w_strb,
    input logic                        b_valid,
    input logic                        b_ready,
    input logic [                 1:0] b_resp,
    input logic                        ar_valid,
    input logic                        ar_ready,
    input logic [  AXI_ADDR_WIDTH-1:0] ar_addr,
    input logic [                 2:0] ar_size,
    input logic [                 2:0] aw_size,
    input logic [                 3:0] ar_cache,
    input logic [                 2:0] ar_prot,
    input logic                        r_valid,
    input logic                        r_ready,
    input logic [                 1:0] r_resp,
    input logic                        dmi_req_valid,
    input logic                        dmi_req_ready,
    input logic [                 6:0] dmi_req_addr,
    input logic [                 1:0] dmi_req_op,
    input logic [                31:0] dmi_req_data,
    input logic                        dmi_resp_valid,
    input logic                        dmi_resp_ready,
    input logic [                 1:0] dmi_resp_resp,
    input logic [                 2:0] bridge_state,
    input logic [                 1:0] bridge_error,
    input logic                        cdc_req_src_valid,
    input logic                        cdc_req_src_ready,
    input logic                        cdc_resp_dst_valid,
    input logic                        cdc_req_src_clear_pending,
    input logic                        cdc_req_dst_clear_pending,
    input logic                        cdc_resp_src_clear_pending,
    input logic                        cdc_resp_dst_clear_pending,
    input logic                        arst_clk_n,
    input logic                        arst_tck_n,
    input logic                        dmactive,
    input logic [                 2:0] sbaccess,
    input logic                        sbautoincrement,
    input logic                        sbreadonaddr,
    input logic                        sbreadondata,
    input logic                        sbbusy,
    // dm_sba's error report, nonzero only on the cycle that sets sbcs.sberror
    input logic [                 2:0] sberror,
    // The sticky sbcs.sberror field, which blocks every new access while nonzero
    input logic [                 2:0] sbcs_sberror,
    // The sticky sbcs.sbbusyerror field; while it is set, u_dm_csrs answers busy to every
    // sbaddress write and sbdata access before it tests sberror
    input logic                        sbcs_sbbusyerror
);

  localparam int unsigned NBytes = AXI_DATA_WIDTH / 8;
  localparam logic [2:0] MaxSize = (AXI_DATA_WIDTH == 64) ? 3'd3 : 3'd2;
  localparam int unsigned MinAddrWidth = (AXI_DATA_WIDTH == 64) ? 3 : 2;

  // TDR ops and status, bridge states and errors (dmi_tdr_bridge encodings)
  localparam logic [1:0] OpNop = 2'd0;
  localparam logic [1:0] OpRead = 2'd1;
  localparam logic [1:0] OpWrite = 2'd2;
  localparam logic [1:0] OpClear = 2'd3;
  localparam logic [1:0] StatusOk = 2'd0;
  localparam logic [1:0] StatusBusy = 2'd3;
  localparam logic [2:0] StIdle = 3'd0;
  localparam logic [2:0] StRead = 3'd1;
  localparam logic [2:0] StWaitRead = 3'd2;
  localparam logic [2:0] StWrite = 3'd3;
  localparam logic [2:0] StWaitWrite = 3'd4;
  localparam logic [1:0] ErrNone = 2'd0;
  localparam logic [1:0] ErrBusy = 2'd3;
  localparam logic [1:0] DmiRespBusy = 2'd3;

  // DMI register addresses (RISC-V debug specification)
  localparam logic [6:0] AddrDmcontrol = 7'h10;
  localparam logic [6:0] AddrDmstatus = 7'h11;
  localparam logic [6:0] AddrAbstractcs = 7'h16;
  localparam logic [6:0] AddrCommand = 7'h17;
  localparam logic [6:0] AddrSbcs = 7'h38;
  localparam logic [6:0] AddrSbaddress0 = 7'h39;
  localparam logic [6:0] AddrSbaddress1 = 7'h3A;
  localparam logic [6:0] AddrSbdata0 = 7'h3C;
  localparam logic [6:0] AddrSbdata1 = 7'h3D;

  // ---------------------------------------------------------------------------
  // Events
  // ---------------------------------------------------------------------------
  wire        ar_hs = ar_valid && ar_ready;
  wire        aw_hs = aw_valid && aw_ready;
  wire        w_hs = w_valid && w_ready;
  wire        r_hs = r_valid && r_ready;
  wire        b_hs = b_valid && b_ready;
  wire        req_hs = ar_hs || aw_hs;
  wire        axi_hs = ar_hs || aw_hs || w_hs || r_hs || b_hs;
  wire        any_req_valid = ar_valid || aw_valid || w_valid;

  wire        dmi_hs = dmi_req_valid && dmi_req_ready;
  wire        dmi_rd = dmi_hs && (dmi_req_op == OpRead);
  wire        dmi_wr = dmi_hs && (dmi_req_op == OpWrite);
  wire        dmi_resp_hs = dmi_resp_valid && dmi_resp_ready;

  wire [ 1:0] upd_op = tdr_upd_q[1:0];
  wire [ 1:0] cap_status = tdr_in[1:0];
  wire        bridge_idle_clear = (bridge_state == StIdle) && (bridge_error == ErrNone);
  wire        bridge_waiting = (bridge_state == StWaitRead) || (bridge_state == StWaitWrite);

  wire [63:0] ar_addr64 = 64'(ar_addr);
  wire [63:0] aw_addr64 = 64'(aw_addr);

  // ---------------------------------------------------------------------------
  // Clock activity: measured periods, edges of each clock seen from the other
  // ---------------------------------------------------------------------------
  realtime     clk_rise_at;
  realtime     clk_gap;
  realtime     clk_period;
  realtime     tck_rise_at;
  realtime     tck_gap;
  realtime     tck_period;
  int unsigned clk_rises;
  int unsigned tck_rises;

  initial begin
    clk_rise_at = 0.0;
    clk_gap     = 0.0;
    clk_period  = 0.0;
    tck_rise_at = 0.0;
    tck_gap     = 0.0;
    tck_period  = 0.0;
    clk_rises   = 0;
    tck_rises   = 0;
  end

  // A period is taken from two consecutive gaps within a factor of two of each other,
  // so the time a clock spends stopped never counts as a period.
  always @(posedge clk) begin
    if ((clk_gap > 0.0) && (($realtime - clk_rise_at) <= 2.0 * clk_gap) &&
        (clk_gap <= 2.0 * ($realtime - clk_rise_at))) begin
      clk_period = $realtime - clk_rise_at;
    end
    clk_gap     = $realtime - clk_rise_at;
    clk_rise_at = $realtime;
    clk_rises   = clk_rises + 1;
  end

  always @(posedge tck) begin
    if ((tck_gap > 0.0) && (($realtime - tck_rise_at) <= 2.0 * tck_gap) &&
        (tck_gap <= 2.0 * ($realtime - tck_rise_at))) begin
      tck_period = $realtime - tck_rise_at;
    end
    tck_gap     = $realtime - tck_rise_at;
    tck_rise_at = $realtime;
    tck_rises   = tck_rises + 1;
  end

  wire ratio_known = (clk_period > 0.0) && (tck_period > 0.0);
  wire ratio_clk_much_faster = ratio_known && (tck_period >= 10.0 * clk_period);
  wire ratio_near_one = ratio_known && (tck_period <= 1.1 * clk_period) &&
      (clk_period <= 1.1 * tck_period);
  wire ratio_tck_faster = ratio_known && (clk_period >= 1.5 * tck_period) &&
      (clk_period <= 4.0 * tck_period);
  wire ratio_tck_much_faster = ratio_known && (clk_period >= 6.0 * tck_period);
  // The near-one band split by the faster clock: 20:19 and 20:21 fall on opposite sides
  wire ratio_clk_slightly_faster = ratio_near_one && (clk_period < tck_period);
  wire ratio_tck_slightly_faster = ratio_near_one && (tck_period < clk_period);

  // i_clk cycles since the last TCK edge, and TCK edges since the last i_clk edge
  int unsigned clk_since_tck;
  int unsigned tck_rises_seen_clk;
  int unsigned tck_since_clk;
  int unsigned clk_rises_seen_tck;
  int unsigned tck_stop_clks;

  initial begin
    clk_since_tck      = 0;
    tck_rises_seen_clk = 0;
    tck_since_clk      = 0;
    clk_rises_seen_tck = 0;
    tck_stop_clks      = 8;
  end

  always @(posedge clk) begin
    if (tck_rises != tck_rises_seen_clk) begin
      tck_rises_seen_clk <= tck_rises;
      clk_since_tck      <= 0;
    end else if (clk_since_tck != 32'hFFFF_FFFF) begin
      clk_since_tck <= clk_since_tck + 1;
    end
  end

  always @(posedge tck) begin
    if (clk_rises != clk_rises_seen_tck) begin
      clk_rises_seen_tck <= clk_rises;
      tck_since_clk      <= 0;
    end else if (tck_since_clk != 32'hFFFF_FFFF) begin
      tck_since_clk <= tck_since_clk + 1;
    end
  end

  // TCK counts as stopped after four of its periods without an edge, in i_clk cycles
  always @(posedge clk) begin
    if (ratio_known) tck_stop_clks = int'(4.0 * tck_period / clk_period) + 4;
  end

  wire tck_stopped = clk_since_tck >= tck_stop_clks;

  // ---------------------------------------------------------------------------
  // i_clk-side state: AXI transactions, DMI requests and SBA controls
  // ---------------------------------------------------------------------------
  int unsigned ar_wait_q;
  int unsigned aw_wait_q;
  int unsigned w_wait_q;
  int unsigned since_ar_hs_q;
  int unsigned since_w_hs_q;
  logic        ar_valid_q;
  logic        aw_valid_q;
  logic        ar_ready_q;
  logic        aw_ready_q;
  logic        b_valid_q;
  logic        r_valid_q;
  logic        aw_done_q;  // AW of the current write taken, B owed
  logic        r_owed_q;  // AR taken, R owed
  logic        b_owed_q;  // W taken, B owed
  logic [63:0] last_ar_addr_q;
  logic        ar_seen_q;
  logic [63:0] last_aw_addr_q;
  logic        aw_seen_q;
  int unsigned aw_run_q;
  logic        dmi_sbaddress0_wr_q;  // last DMI request wrote sbaddress0
  logic        dmi_sbdata0_wr_q;  // last DMI request wrote sbdata0
  logic        dmi_sbdata0_rod_q;  // last DMI request read sbdata0 with sbreadondata
  logic        rd_from_addr_q;  // the outstanding read was started by an sbaddress0 write
  logic        rd_from_data_q;  // the outstanding read was started by an sbdata0 read
  logic        wr_from_data_q;  // the outstanding write was started by an sbdata0 write
  logic        prev_wr_done_q;  // a write completed with no sbaddress0 write since
  logic        last_resp_r_q;  // the last response handshake was an R
  logic [ 2:0] sberror_q;
  int unsigned sbbusy_cycles_q;
  logic        dmactive_q;
  logic        first_access_q;  // dmactive rose, no request handshake since
  logic        port_ar_seen_q;
  logic        port_aw_seen_q;
  logic        sba_written_q;  // an SBA register was written since reset
  logic        resp_pending_tck_q;  // a DMI response waits for TCK

  wire         dmi_sba_reg = (dmi_req_addr >= AddrSbcs) && (dmi_req_addr <= AddrSbdata1);
  wire         aw_next_addr = aw_seen_q && (aw_addr64 == last_aw_addr_q + (64'd1 << sbaccess));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ar_wait_q           <= 0;
      aw_wait_q           <= 0;
      w_wait_q            <= 0;
      since_ar_hs_q       <= 0;
      since_w_hs_q        <= 0;
      ar_valid_q          <= 1'b0;
      aw_valid_q          <= 1'b0;
      ar_ready_q          <= 1'b0;
      aw_ready_q          <= 1'b0;
      b_valid_q           <= 1'b0;
      r_valid_q           <= 1'b0;
      aw_done_q           <= 1'b0;
      r_owed_q            <= 1'b0;
      b_owed_q            <= 1'b0;
      last_ar_addr_q      <= '0;
      ar_seen_q           <= 1'b0;
      last_aw_addr_q      <= '0;
      aw_seen_q           <= 1'b0;
      aw_run_q            <= 0;
      dmi_sbaddress0_wr_q <= 1'b0;
      dmi_sbdata0_wr_q    <= 1'b0;
      dmi_sbdata0_rod_q   <= 1'b0;
      rd_from_addr_q      <= 1'b0;
      rd_from_data_q      <= 1'b0;
      wr_from_data_q      <= 1'b0;
      prev_wr_done_q      <= 1'b0;
      last_resp_r_q       <= 1'b0;
      sberror_q           <= '0;
      sbbusy_cycles_q     <= 0;
      dmactive_q          <= 1'b0;
      first_access_q      <= 1'b0;
      sba_written_q       <= 1'b0;
      resp_pending_tck_q  <= 1'b0;
    end else begin
      ar_wait_q     <= (ar_valid && !ar_ready) ? ar_wait_q + 1 : 0;
      aw_wait_q     <= (aw_valid && !aw_ready) ? aw_wait_q + 1 : 0;
      w_wait_q      <= (w_valid && !w_ready) ? w_wait_q + 1 : 0;
      since_ar_hs_q <= ar_hs ? 1 : since_ar_hs_q + 1;
      since_w_hs_q  <= w_hs ? 1 : since_w_hs_q + 1;
      ar_valid_q    <= ar_valid;
      aw_valid_q    <= aw_valid;
      ar_ready_q    <= ar_ready;
      aw_ready_q    <= aw_ready;
      b_valid_q     <= b_valid;
      r_valid_q     <= r_valid;

      if (aw_hs) aw_done_q <= 1'b1;
      else if (b_hs) aw_done_q <= 1'b0;
      if (ar_hs) r_owed_q <= 1'b1;
      else if (r_hs) r_owed_q <= 1'b0;
      if (w_hs) b_owed_q <= 1'b1;
      else if (b_hs) b_owed_q <= 1'b0;

      if (ar_hs) begin
        last_ar_addr_q <= ar_addr64;
        ar_seen_q      <= 1'b1;
        rd_from_addr_q <= dmi_sbaddress0_wr_q;
        rd_from_data_q <= dmi_sbdata0_rod_q;
      end
      if (aw_hs) begin
        last_aw_addr_q <= aw_addr64;
        aw_seen_q      <= 1'b1;
        aw_run_q       <= (sbautoincrement && aw_next_addr) ? aw_run_q + 1 : 1;
        wr_from_data_q <= dmi_sbdata0_wr_q;
      end

      if (dmi_hs) begin
        dmi_sbaddress0_wr_q <= dmi_wr && (dmi_req_addr == AddrSbaddress0);
        dmi_sbdata0_wr_q    <= dmi_wr && (dmi_req_addr == AddrSbdata0);
        dmi_sbdata0_rod_q   <= dmi_rd && (dmi_req_addr == AddrSbdata0) && sbreadondata;
      end
      if (dmi_wr && dmi_sba_reg) sba_written_q <= 1'b1;

      if (b_hs) prev_wr_done_q <= 1'b1;
      else if (dmi_wr && (dmi_req_addr == AddrSbaddress0)) prev_wr_done_q <= 1'b0;
      if (r_hs) last_resp_r_q <= 1'b1;
      else if (b_hs) last_resp_r_q <= 1'b0;

      sberror_q       <= sberror;
      sbbusy_cycles_q <= sbbusy ? sbbusy_cycles_q + 1 : 0;
      dmactive_q      <= dmactive;
      if (dmactive && !dmactive_q) first_access_q <= 1'b1;
      else if (req_hs) first_access_q <= 1'b0;

      if (dmi_resp_hs) resp_pending_tck_q <= 1'b1;
      else if (!tck_stopped) resp_pending_tck_q <= 1'b0;
    end
  end

  // Sticky within one simulation, across resets
  initial begin
    port_ar_seen_q = 1'b0;
    port_aw_seen_q = 1'b0;
  end

  always @(posedge clk) begin
    if (ar_hs) port_ar_seen_q <= 1'b1;
    if (aw_hs) port_aw_seen_q <= 1'b1;
  end

  // ---------------------------------------------------------------------------
  // TCK-side state: updates, captures and bridge transitions
  // ---------------------------------------------------------------------------
  logic [1:0] last_op_q;  // op of the last update taken in IDLE with a clear status
  logic       last_op_valid_q;
  logic       upd_prev_q;  // update strobe high at the previous TCK edge
  logic       cap_in_scan_q;  // a capture since the last update
  logic       cap_ok_in_scan_q;  // that capture read status 0
  int unsigned cap_to_upd_q;  // TCK edges since that capture
  logic [1:0] resend_q;  // 1: op 3 seen, 2: then an update taken in IDLE
  logic       rel_no_req_q;  // TCK side released, no request taken since
  logic [1:0] op_since_cap_q;  // op of the last update, for the next capture
  logic       op_since_cap_valid_q;
  int unsigned latency_edges_q;
  logic       latency_pending_q;
  int unsigned resp_hs_count;
  int unsigned resp_hs_seen_tck;
  logic [2:0] bridge_state_q;

  initial resp_hs_count = 0;
  always @(posedge clk) if (dmi_resp_hs) resp_hs_count = resp_hs_count + 1;

  wire upd_taken = tdr_upd && bridge_idle_clear;
  wire read_done = (bridge_state_q == StWaitRead) && (bridge_state == StIdle);
  wire op_done = ((bridge_state_q == StWaitRead) || (bridge_state_q == StWaitWrite)) &&
      (bridge_state == StIdle);
  wire resp_reaches_tdr = bridge_waiting && cdc_resp_dst_valid;

  // Isolate requests of both CDCs; all four fall when the clear sequence after a
  // release ends
  wire  cdc_clear_any = cdc_req_src_clear_pending || cdc_req_dst_clear_pending ||
      cdc_resp_src_clear_pending || cdc_resp_dst_clear_pending;
  logic cdc_clear_q;

  always_ff @(posedge tck or negedge arst_tck_n) begin
    if (!arst_tck_n) cdc_clear_q <= 1'b1;
    else cdc_clear_q <= cdc_clear_any;
  end

  wire cdc_clear_done = cdc_clear_q && !cdc_clear_any;

  always_ff @(posedge tck or negedge arst_tck_n) begin
    if (!arst_tck_n) begin
      last_op_q            <= OpNop;
      last_op_valid_q      <= 1'b0;
      upd_prev_q           <= 1'b0;
      cap_in_scan_q        <= 1'b0;
      cap_ok_in_scan_q     <= 1'b0;
      cap_to_upd_q         <= 0;
      resend_q             <= 2'd0;
      rel_no_req_q         <= 1'b1;
      op_since_cap_q       <= OpNop;
      op_since_cap_valid_q <= 1'b0;
      bridge_state_q       <= StIdle;
    end else begin
      upd_prev_q     <= tdr_upd;
      bridge_state_q <= bridge_state;
      if (upd_taken) begin
        last_op_q       <= upd_op;
        last_op_valid_q <= 1'b1;
      end
      if (tdr_cap) begin
        cap_in_scan_q    <= 1'b1;
        cap_ok_in_scan_q <= (cap_status == StatusOk);
        cap_to_upd_q     <= 0;
      end else begin
        cap_to_upd_q <= cap_to_upd_q + 1;
      end
      if (tdr_upd) begin
        cap_in_scan_q        <= 1'b0;
        op_since_cap_q       <= upd_op;
        op_since_cap_valid_q <= 1'b1;
      end else if (tdr_cap) begin
        op_since_cap_valid_q <= 1'b0;
      end
      if (tdr_upd && (upd_op == OpClear)) resend_q <= 2'd1;
      else if ((resend_q == 2'd1) && upd_taken && (upd_op inside {OpRead, OpWrite}))
        resend_q <= 2'd2;
      else if ((resend_q == 2'd2) && tdr_cap && (cap_status == StatusOk)) resend_q <= 2'd0;
      if (cdc_req_src_valid && cdc_req_src_ready) rel_no_req_q <= 1'b0;
    end
  end

  // TCK edges from a DMI response handshake to the edge that loads it into o_dmi_tdr
  int unsigned latency_sample;
  logic        latency_sampled;

  initial begin
    resp_hs_seen_tck  = 0;
    latency_edges_q   = 0;
    latency_pending_q = 1'b0;
    latency_sample    = 0;
    latency_sampled   = 1'b0;
  end

  always @(posedge tck) begin
    latency_sampled = 1'b0;
    if (resp_hs_count != resp_hs_seen_tck) begin
      resp_hs_seen_tck  = resp_hs_count;
      latency_edges_q   = 1;
      latency_pending_q = 1'b1;
    end else if (latency_pending_q) begin
      latency_edges_q = latency_edges_q + 1;
    end
    if (latency_pending_q && resp_reaches_tdr) begin
      latency_sample    = latency_edges_q;
      latency_sampled   = 1'b1;
      latency_pending_q = 1'b0;
    end
  end

  // ---------------------------------------------------------------------------
  // Reset events: the state just before i_arst_n falls, and release order
  // ---------------------------------------------------------------------------
  typedef struct packed {
    logic ar_wait;
    logic w_wait;
    logic w_alone;
    logic r_pending;
    logic b_pending;
    logic bridge_busy;
    logic cdc_req_inflight;
    logic cdc_clear;
    logic cdc_clear_live;
    logic idle;
  } pre_rst_t;

  pre_rst_t pre_rst;

  always_latch begin
    if (rst_n) begin
      pre_rst.ar_wait          = ar_valid && !ar_ready;
      pre_rst.w_wait           = (aw_valid && !aw_ready) || (w_valid && !w_ready);
      // W waits on its own after its write's AW was taken
      pre_rst.w_alone          = w_valid && !w_ready && !aw_valid && aw_done_q;
      pre_rst.r_pending        = r_owed_q;
      pre_rst.b_pending        = b_owed_q;
      pre_rst.bridge_busy      = bridge_state != StIdle;
      pre_rst.cdc_req_inflight = cdc_req_src_valid;
      pre_rst.cdc_clear        = cdc_req_src_clear_pending || cdc_req_dst_clear_pending ||
          cdc_resp_src_clear_pending || cdc_resp_dst_clear_pending;
      // A clear sequence that runs: both sides out of reset, so its phases can advance
      pre_rst.cdc_clear_live   = cdc_clear_any && arst_clk_n && arst_tck_n;
      pre_rst.idle             = (bridge_state == StIdle) && !any_req_valid;
    end
  end

  int unsigned rst_falls;
  int unsigned rst_falls_seen;
  int unsigned tck_rel_count;
  int unsigned tck_rel_seen;
  int unsigned clk_rel_count;
  int unsigned clk_rel_seen;
  int unsigned tck_stopped_rel_count;
  int unsigned tck_stopped_rel_seen;
  int unsigned scan_rst_count;
  int unsigned scan_rst_seen;
  int unsigned trst_stop_count;
  int unsigned trst_stop_seen;
  logic        tck_rel_with_clk_up;
  logic        clk_rel_with_tck_up;
  logic        rst_rose_tck_stopped;
  logic        scan_fell_in_test_mode;

  initial begin
    rst_falls              = 0;
    rst_falls_seen         = 0;
    tck_rel_count          = 0;
    tck_rel_seen           = 0;
    clk_rel_count          = 0;
    clk_rel_seen           = 0;
    tck_stopped_rel_count  = 0;
    tck_stopped_rel_seen   = 0;
    scan_rst_count         = 0;
    scan_rst_seen          = 0;
    trst_stop_count        = 0;
    trst_stop_seen         = 0;
    tck_rel_with_clk_up    = 1'b0;
    clk_rel_with_tck_up    = 1'b0;
    rst_rose_tck_stopped   = 1'b0;
    scan_fell_in_test_mode = 1'b0;
  end

  always @(negedge rst_n) rst_falls = rst_falls + 1;

  always @(posedge rst_n) begin
    rst_rose_tck_stopped = (tck_period == 0.0) || ($realtime - tck_rise_at > 2.0 * tck_period);
  end

  always @(posedge arst_tck_n) begin
    tck_rel_with_clk_up = arst_clk_n;
    tck_rel_count       = tck_rel_count + 1;
    if (rst_rose_tck_stopped) tck_stopped_rel_count = tck_stopped_rel_count + 1;
  end

  always @(posedge arst_clk_n) begin
    clk_rel_with_tck_up = arst_tck_n;
    clk_rel_count       = clk_rel_count + 1;
  end

  always @(negedge rst_scan_n) scan_fell_in_test_mode = test_mode;

  always @(posedge rst_scan_n) begin
    if (test_mode && scan_fell_in_test_mode) scan_rst_count = scan_rst_count + 1;
  end

  // A TAP reset stop: no TCK edge, both strobes low, for 100 i_clk cycles with the
  // bridge outside IDLE
  always @(posedge clk) begin
    if ((clk_since_tck == 100) && (bridge_state != StIdle) && !tdr_cap && !tdr_upd) begin
      trst_stop_count = trst_stop_count + 1;
    end
  end

  wire rst_fell_new = rst_falls != rst_falls_seen;
  wire tck_rel_new = tck_rel_count != tck_rel_seen;
  wire clk_rel_new = clk_rel_count != clk_rel_seen;
  wire tck_stopped_rel_new = tck_stopped_rel_count != tck_stopped_rel_seen;
  wire scan_rst_new = scan_rst_count != scan_rst_seen;
  wire trst_stop_new = trst_stop_count != trst_stop_seen;

  always @(posedge clk) begin
    rst_falls_seen       <= rst_falls;
    tck_rel_seen         <= tck_rel_count;
    clk_rel_seen         <= clk_rel_count;
    tck_stopped_rel_seen <= tck_stopped_rel_count;
    scan_rst_seen        <= scan_rst_count;
  end

  always @(posedge tck) begin
    if (tdr_cap) trst_stop_seen <= trst_stop_count;
  end

  // ---------------------------------------------------------------------------
  // Cover properties: TDR and DMI
  // ---------------------------------------------------------------------------
  wire c_tdr_busy_on_capture_w = tdr_cap && (bridge_state inside {StRead, StWaitRead}) &&
      (bridge_error == ErrNone);
  wire c_tdr_busy_on_dm_answer_w = dmi_resp_hs && (dmi_resp_resp == DmiRespBusy);
  wire c_tdr_busy_on_nop_update_w = tdr_upd && (upd_op == OpNop) && (bridge_state != StIdle) &&
      (bridge_error == ErrNone);
  wire c_tdr_busy_on_update_w = tdr_upd && (upd_op inside {OpRead, OpWrite}) &&
      (bridge_state != StIdle) && (bridge_error == ErrNone);
  wire c_tdr_capture_after_nop_w = tdr_cap && (cap_status == StatusOk) && last_op_valid_q &&
      (last_op_q == OpNop);
  wire c_tdr_capture_at_bound_w = tdr_cap && resp_reaches_tdr;
  wire c_tdr_capture_at_bound_ss4_w = c_tdr_capture_at_bound_w && (SYNC_STAGES == 4);
  wire c_tdr_capture_next_op_w = upd_taken && (upd_op inside {OpRead, OpWrite}) &&
      cap_in_scan_q && cap_ok_in_scan_q && last_op_valid_q && (last_op_q == OpRead);
  wire c_tdr_capture_read_data_w = tdr_cap && (cap_status == StatusOk) &&
      (bridge_state == StIdle) && last_op_valid_q && (last_op_q == OpRead);
  wire c_tdr_capture_write_data_w = tdr_cap && (cap_status == StatusOk) &&
      (bridge_state == StIdle) && last_op_valid_q && (last_op_q == OpWrite);
  wire c_tdr_clk_stopped_op_pending_w = tdr_upd && (bridge_state != StIdle) && (tck_since_clk >= 4);
  wire c_tdr_nop_in_idle_w = upd_taken && (upd_op == OpNop);
  wire c_tdr_op3_clears_busy_w = tdr_upd && (upd_op == OpClear) && (bridge_error == ErrBusy);
  wire c_tdr_op3_in_idle_w = upd_taken && (upd_op == OpClear);
  wire c_tdr_ratio_clk_much_faster_w = read_done && ratio_clk_much_faster;
  wire c_tdr_ratio_clk_slightly_faster_w = read_done && ratio_clk_slightly_faster;
  wire c_tdr_ratio_near_one_w = read_done && ratio_near_one;
  wire c_tdr_ratio_tck_faster_w = read_done && ratio_tck_faster;
  wire c_tdr_ratio_tck_much_faster_w = read_done && ratio_tck_much_faster;
  wire c_tdr_ratio_tck_slightly_faster_w = read_done && ratio_tck_slightly_faster;
  wire c_tdr_read_in_idle_w = upd_taken && (upd_op == OpRead);
  wire c_tdr_round_trip_ss3_20to1_w = op_done && (SYNC_STAGES == 3) && ratio_clk_much_faster;
  wire c_tdr_round_trip_ss3_20to19_w = op_done && (SYNC_STAGES == 3) && ratio_clk_slightly_faster;
  wire c_tdr_round_trip_ss3_20to21_w = op_done && (SYNC_STAGES == 3) && ratio_tck_slightly_faster;
  wire c_tdr_round_trip_ss3_1to2_w = op_done && (SYNC_STAGES == 3) && ratio_tck_faster;
  wire c_tdr_round_trip_ss3_1to20_w = op_done && (SYNC_STAGES == 3) && ratio_tck_much_faster;
  wire c_tdr_round_trip_ss4_20to1_w = op_done && (SYNC_STAGES == 4) && ratio_clk_much_faster;
  wire c_tdr_round_trip_ss4_20to19_w = op_done && (SYNC_STAGES == 4) && ratio_clk_slightly_faster;
  wire c_tdr_round_trip_ss4_20to21_w = op_done && (SYNC_STAGES == 4) && ratio_tck_slightly_faster;
  wire c_tdr_round_trip_ss4_1to2_w = op_done && (SYNC_STAGES == 4) && ratio_tck_faster;
  wire c_tdr_round_trip_ss4_1to20_w = op_done && (SYNC_STAGES == 4) && ratio_tck_much_faster;
  wire c_tdr_read_resent_twice_w = ar_hs && ar_seen_q && (ar_addr64 == last_ar_addr_q);
  wire c_tdr_resend_completes_w = (resend_q == 2'd2) && tdr_cap && (cap_status == StatusOk);
  wire c_tdr_scan_via_pause_w = tdr_upd && cap_in_scan_q && (cap_to_upd_q > 50);
  wire c_tdr_tck_stop_bus_pending_w = axi_hs && (bridge_state != StIdle) && tck_stopped;
  wire c_tdr_tck_stop_result_pending_w = resp_pending_tck_q && (clk_since_tck == 100);
  wire c_tdr_tck_stop_result_pending_ss4_w = c_tdr_tck_stop_result_pending_w && (SYNC_STAGES == 4);
  wire c_tdr_update_two_edges_w = tdr_upd && upd_prev_q;
  wire c_tdr_update_while_sticky_w = tdr_upd && (upd_op inside {OpRead, OpWrite}) &&
      (bridge_error == ErrBusy);
  wire c_tdr_write_in_idle_w = upd_taken && (upd_op == OpWrite);

  c_tdr_busy_on_capture : cover property (@(posedge tck) c_tdr_busy_on_capture_w);
  c_tdr_busy_on_dm_answer : cover property (@(posedge clk) c_tdr_busy_on_dm_answer_w);
  c_tdr_busy_on_nop_update : cover property (@(posedge tck) c_tdr_busy_on_nop_update_w);
  c_tdr_busy_on_update : cover property (@(posedge tck) c_tdr_busy_on_update_w);
  c_tdr_capture_after_nop : cover property (@(posedge tck) c_tdr_capture_after_nop_w);
  c_tdr_capture_at_bound : cover property (@(posedge tck) c_tdr_capture_at_bound_w);
  c_tdr_capture_at_bound_ss4 : cover property (@(posedge tck) c_tdr_capture_at_bound_ss4_w);
  c_tdr_capture_next_op : cover property (@(posedge tck) c_tdr_capture_next_op_w);
  c_tdr_capture_read_data : cover property (@(posedge tck) c_tdr_capture_read_data_w);
  c_tdr_capture_write_data : cover property (@(posedge tck) c_tdr_capture_write_data_w);
  c_tdr_clk_stopped_op_pending : cover property (@(posedge tck) c_tdr_clk_stopped_op_pending_w);
  c_tdr_nop_in_idle : cover property (@(posedge tck) c_tdr_nop_in_idle_w);
  c_tdr_op3_clears_busy : cover property (@(posedge tck) c_tdr_op3_clears_busy_w);
  c_tdr_op3_in_idle : cover property (@(posedge tck) c_tdr_op3_in_idle_w);
  c_tdr_ratio_clk_much_faster : cover property (@(posedge tck) c_tdr_ratio_clk_much_faster_w);
  c_tdr_ratio_clk_slightly_faster :
  cover property (@(posedge tck) c_tdr_ratio_clk_slightly_faster_w);
  c_tdr_ratio_near_one : cover property (@(posedge tck) c_tdr_ratio_near_one_w);
  c_tdr_ratio_tck_faster : cover property (@(posedge tck) c_tdr_ratio_tck_faster_w);
  c_tdr_ratio_tck_much_faster : cover property (@(posedge tck) c_tdr_ratio_tck_much_faster_w);
  c_tdr_ratio_tck_slightly_faster :
  cover property (@(posedge tck) c_tdr_ratio_tck_slightly_faster_w);
  c_tdr_read_in_idle : cover property (@(posedge tck) c_tdr_read_in_idle_w);
  c_tdr_round_trip_ss3_20to1 : cover property (@(posedge tck) c_tdr_round_trip_ss3_20to1_w);
  c_tdr_round_trip_ss3_20to19 : cover property (@(posedge tck) c_tdr_round_trip_ss3_20to19_w);
  c_tdr_round_trip_ss3_20to21 : cover property (@(posedge tck) c_tdr_round_trip_ss3_20to21_w);
  c_tdr_round_trip_ss3_1to2 : cover property (@(posedge tck) c_tdr_round_trip_ss3_1to2_w);
  c_tdr_round_trip_ss3_1to20 : cover property (@(posedge tck) c_tdr_round_trip_ss3_1to20_w);
  c_tdr_round_trip_ss4_20to1 : cover property (@(posedge tck) c_tdr_round_trip_ss4_20to1_w);
  c_tdr_round_trip_ss4_20to19 : cover property (@(posedge tck) c_tdr_round_trip_ss4_20to19_w);
  c_tdr_round_trip_ss4_20to21 : cover property (@(posedge tck) c_tdr_round_trip_ss4_20to21_w);
  c_tdr_round_trip_ss4_1to2 : cover property (@(posedge tck) c_tdr_round_trip_ss4_1to2_w);
  c_tdr_round_trip_ss4_1to20 : cover property (@(posedge tck) c_tdr_round_trip_ss4_1to20_w);
  c_tdr_read_resent_twice : cover property (@(posedge clk) c_tdr_read_resent_twice_w);
  c_tdr_resend_completes : cover property (@(posedge tck) c_tdr_resend_completes_w);
  c_tdr_scan_via_pause : cover property (@(posedge tck) c_tdr_scan_via_pause_w);
  c_tdr_tck_stop_bus_pending : cover property (@(posedge clk) c_tdr_tck_stop_bus_pending_w);
  c_tdr_tck_stop_result_pending : cover property (@(posedge clk) c_tdr_tck_stop_result_pending_w);
  c_tdr_tck_stop_result_pending_ss4 :
  cover property (@(posedge clk) c_tdr_tck_stop_result_pending_ss4_w);
  c_tdr_update_two_edges : cover property (@(posedge tck) c_tdr_update_two_edges_w);
  c_tdr_update_while_sticky : cover property (@(posedge tck) c_tdr_update_while_sticky_w);
  c_tdr_write_in_idle : cover property (@(posedge tck) c_tdr_write_in_idle_w);

  // ---------------------------------------------------------------------------
  // Cover properties: AXI manager
  // ---------------------------------------------------------------------------
  // An i_axi_rsp READY or VALID change away from an i_clk rising edge
  int unsigned rsp_mid_changes;
  int unsigned rsp_mid_changes_seen;

  initial begin
    rsp_mid_changes      = 0;
    rsp_mid_changes_seen = 0;
  end

  always @(ar_ready or aw_ready or w_ready or r_valid or b_valid) begin
    if (($realtime != clk_rise_at) && any_req_valid) rsp_mid_changes = rsp_mid_changes + 1;
  end

  always @(posedge clk) rsp_mid_changes_seen <= rsp_mid_changes;

  wire c_axi4_narrow_read_w = !USE_AXI_LITE && ar_hs && (ar_size < MaxSize);
  wire c_axi4_narrow_write_w = !USE_AXI_LITE && aw_hs && (aw_size < MaxSize);
  wire c_axi_ar_wait_w = ar_hs && (ar_wait_q >= 2);
  wire c_axi_attr_read_w = ar_hs;
  wire c_axi_attr_write_w = aw_hs;
  wire c_axi_aw_wait_w = aw_hs && (aw_wait_q >= 2);
  wire c_axi_b_late_w = b_valid && !b_valid_q && (since_w_hs_q >= 8);
  wire c_axi_r_late_w = r_valid && !r_valid_q && (since_ar_hs_q >= 8);
  wire c_axi_ready_before_valid_w = (ar_valid && !ar_valid_q && ar_ready_q) ||
      (aw_valid && !aw_valid_q && aw_ready_q);
  wire c_axi_rsp_change_mid_cycle_w = rsp_mid_changes != rsp_mid_changes_seen;
  wire c_axi_w_before_aw_w = w_hs && !aw_done_q && !aw_hs;
  wire c_axi_w_wait_w = w_hs && (w_wait_q >= 2);
  wire c_axil_narrow_read_w = USE_AXI_LITE && ar_hs && (sbaccess < MaxSize);
  wire c_axil_narrow_write_w = USE_AXI_LITE && w_hs && (w_strb != '0) && (w_strb != '1);

  c_axi4_narrow_read : cover property (@(posedge clk) c_axi4_narrow_read_w);
  c_axi4_narrow_write : cover property (@(posedge clk) c_axi4_narrow_write_w);
  c_axi_ar_wait : cover property (@(posedge clk) c_axi_ar_wait_w);
  c_axi_attr_read : cover property (@(posedge clk) c_axi_attr_read_w);
  c_axi_attr_write : cover property (@(posedge clk) c_axi_attr_write_w);
  c_axi_aw_wait : cover property (@(posedge clk) c_axi_aw_wait_w);
  c_axi_b_late : cover property (@(posedge clk) c_axi_b_late_w);
  c_axi_r_late : cover property (@(posedge clk) c_axi_r_late_w);
  c_axi_ready_before_valid : cover property (@(posedge clk) c_axi_ready_before_valid_w);
  c_axi_rsp_change_mid_cycle : cover property (@(posedge clk) c_axi_rsp_change_mid_cycle_w);
  c_axi_w_before_aw : cover property (@(posedge clk) c_axi_w_before_aw_w);
  c_axi_w_wait : cover property (@(posedge clk) c_axi_w_wait_w);
  c_axil_narrow_read : cover property (@(posedge clk) c_axil_narrow_read_w);
  c_axil_narrow_write : cover property (@(posedge clk) c_axil_narrow_write_w);

  // ---------------------------------------------------------------------------
  // Cover properties: system bus access
  // ---------------------------------------------------------------------------
  wire bus_idle = !any_req_valid && !sbbusy;
  wire r_err = r_hs && r_resp[1];
  wire b_err = b_hs && b_resp[1];

  // An R or B handshake taken while sbbusy is high, until sbbusy falls or the next
  // access starts
  logic sbbusy_after_resp_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) sbbusy_after_resp_q <= 1'b0;
    else if (r_hs || b_hs) sbbusy_after_resp_q <= sbbusy;
    else if (!sbbusy || req_hs) sbbusy_after_resp_q <= 1'b0;
  end

  wire c_sba_access_64bit_w = req_hs && (sbaccess == 3'd3);
  wire c_sba_addr_above_32_w = (ar_hs && (ar_addr64[63:32] != '0)) ||
      (aw_hs && (aw_addr64[63:32] != '0));
  wire c_sba_addr_msb_w = (ar_hs && ar_addr[AXI_ADDR_WIDTH-1]) ||
      (aw_hs && aw_addr[AXI_ADDR_WIDTH-1]);
  wire c_sba_block_read_w = ar_hs && dmi_sbdata0_rod_q;
  wire c_sba_block_write_w = aw_hs && sbautoincrement && aw_next_addr && (aw_run_q >= 3);
  wire c_sba_block_write_error_mid_w = b_err && sbautoincrement && wr_from_data_q && prev_wr_done_q;
  wire c_sba_blocked_by_sberror_w = dmi_wr && (dmi_req_addr == AddrSbdata0) &&
      (sbcs_sberror != '0) && !sbcs_sbbusyerror;
  wire c_sba_blocked_read_on_addr_w = dmi_wr && (dmi_req_addr == AddrSbaddress0) && sbreadonaddr &&
      (sbcs_sberror != '0) && !sbcs_sbbusyerror;
  wire c_sba_blocked_read_on_data_w = dmi_rd && (dmi_req_addr == AddrSbdata0) && sbreadondata &&
      (sbcs_sberror != '0) && !sbcs_sbbusyerror;
  wire c_sba_read_error_autoinc_addr_w = r_err && sbautoincrement && rd_from_addr_q;
  wire c_sba_read_error_autoinc_data_w = r_err && sbreadondata && sbautoincrement && rd_from_data_q;
  wire c_sba_read_on_sbaddress0_w = ar_hs && dmi_sbaddress0_wr_q;
  wire c_sba_sbaddress0_no_read_w = dmi_wr && (dmi_req_addr == AddrSbaddress0) && !sbreadonaddr;
  wire c_sba_sbbusy_held_w = sbbusy && (sbbusy_cycles_q == 15);
  wire c_sba_sbbusy_read_after_response_w = dmi_rd && (dmi_req_addr == AddrSbcs) &&
      sbbusy_after_resp_q;
  wire c_sba_sbaddress_read_while_busy_w = dmi_rd && sbbusy &&
      (dmi_req_addr inside {AddrSbaddress0, AddrSbaddress1});
  wire c_sba_sbbusyerror_set_w = sbbusy &&
      ((dmi_wr && (dmi_req_addr inside {AddrSbaddress0, AddrSbaddress1, AddrSbdata0,
                                        AddrSbdata1})) ||
       (dmi_rd && (dmi_req_addr inside {AddrSbdata0, AddrSbdata1})));
  wire c_sba_sbcs_active_w = dmi_rd && (dmi_req_addr == AddrSbcs) && dmactive;
  wire c_sba_sbcs_write_while_busy_w = dmi_wr && sbbusy && (dmi_req_addr == AddrSbcs);
  wire c_sba_sbdata0_read_no_readondata_w = dmi_rd && (dmi_req_addr == AddrSbdata0) &&
      !sbreadondata;
  wire c_sba_sbdata1_no_bus_w = dmi_hs && (dmi_req_addr == AddrSbdata1) && bus_idle;
  wire c_sba_sberror_align_w = (sberror == 3'd3) && (sberror_q != 3'd3);
  wire c_sba_sberror_bus_read_w = (sberror == 3'd2) && (sberror_q != 3'd2) && last_resp_r_q;
  wire c_sba_sberror_bus_write_w = (sberror == 3'd2) && (sberror_q != 3'd2) && !last_resp_r_q;
  wire c_sba_sberror_size_w = (sberror == 3'd4) && (sberror_q != 3'd4);
  wire c_sba_write_error_autoinc_w = b_err && sbautoincrement;
  wire c_sba_write_on_sbdata0_w = aw_hs && dmi_sbdata0_wr_q;

  c_sba_access_64bit : cover property (@(posedge clk) c_sba_access_64bit_w);
  c_sba_addr_above_32 : cover property (@(posedge clk) c_sba_addr_above_32_w);
  c_sba_addr_msb : cover property (@(posedge clk) c_sba_addr_msb_w);
  c_sba_block_read : cover property (@(posedge clk) c_sba_block_read_w);
  c_sba_block_write : cover property (@(posedge clk) c_sba_block_write_w);
  c_sba_block_write_error_mid : cover property (@(posedge clk) c_sba_block_write_error_mid_w);
  c_sba_blocked_by_sberror : cover property (@(posedge clk) c_sba_blocked_by_sberror_w);
  c_sba_blocked_read_on_addr : cover property (@(posedge clk) c_sba_blocked_read_on_addr_w);
  c_sba_blocked_read_on_data : cover property (@(posedge clk) c_sba_blocked_read_on_data_w);
  c_sba_read_error_autoinc_addr : cover property (@(posedge clk) c_sba_read_error_autoinc_addr_w);
  c_sba_read_error_autoinc_data : cover property (@(posedge clk) c_sba_read_error_autoinc_data_w);
  c_sba_read_on_sbaddress0 : cover property (@(posedge clk) c_sba_read_on_sbaddress0_w);
  c_sba_sbaddress0_no_read : cover property (@(posedge clk) c_sba_sbaddress0_no_read_w);
  c_sba_sbaddress_read_while_busy :
  cover property (@(posedge clk) c_sba_sbaddress_read_while_busy_w);
  c_sba_sbbusy_held : cover property (@(posedge clk) c_sba_sbbusy_held_w);
  c_sba_sbbusy_read_after_response :
  cover property (@(posedge clk) c_sba_sbbusy_read_after_response_w);
  c_sba_sbbusyerror_set : cover property (@(posedge clk) c_sba_sbbusyerror_set_w);
  c_sba_sbcs_active : cover property (@(posedge clk) c_sba_sbcs_active_w);
  c_sba_sbcs_write_while_busy : cover property (@(posedge clk) c_sba_sbcs_write_while_busy_w);
  c_sba_sbdata0_read_no_readondata :
  cover property (@(posedge clk) c_sba_sbdata0_read_no_readondata_w);
  c_sba_sbdata1_no_bus : cover property (@(posedge clk) c_sba_sbdata1_no_bus_w);
  c_sba_sberror_align : cover property (@(posedge clk) c_sba_sberror_align_w);
  c_sba_sberror_bus_read : cover property (@(posedge clk) c_sba_sberror_bus_read_w);
  c_sba_sberror_bus_write : cover property (@(posedge clk) c_sba_sberror_bus_write_w);
  c_sba_sberror_size : cover property (@(posedge clk) c_sba_sberror_size_w);
  c_sba_write_error_autoinc : cover property (@(posedge clk) c_sba_write_error_autoinc_w);
  c_sba_write_on_sbdata0 : cover property (@(posedge clk) c_sba_write_on_sbdata0_w);

  // ---------------------------------------------------------------------------
  // Cover properties: Debug Module
  // ---------------------------------------------------------------------------
  // Addresses dm_csrs decodes (dm::dm_csr_e)
  wire dmi_addr_mapped = ((dmi_req_addr >= 7'h04) && (dmi_req_addr <= 7'h1D)) ||
      ((dmi_req_addr >= 7'h20) && (dmi_req_addr <= 7'h30)) || (dmi_req_addr == 7'h34) ||
      (dmi_req_addr == 7'h35) || ((dmi_req_addr >= 7'h37) && (dmi_req_addr <= 7'h40));
  wire dmactive_fall = !dmactive && dmactive_q;

  wire c_dm_command_write_w = dmi_wr && (dmi_req_addr == AddrCommand);
  wire c_dm_dmactive_fall_w = dmactive_fall;
  wire c_dm_dmactive_fall_ar_wait_w = dmactive_fall && ar_valid && !ar_ready;
  wire c_dm_dmactive_fall_aw_wait_w = dmactive_fall && aw_valid && !aw_ready;
  wire c_dm_dmactive_fall_w_wait_w = dmactive_fall && w_valid && !w_ready;
  wire c_dm_dmstatus_read_w = dmi_rd && (dmi_req_addr == AddrDmstatus);
  wire c_dm_hart_request_write_w = dmi_wr && (dmi_req_addr == AddrDmcontrol) &&
      (dmi_req_data[31] || dmi_req_data[30]);
  wire c_dm_ndmreset_set_w = dmi_wr && (dmi_req_addr == AddrDmcontrol) && dmi_req_data[1];
  wire c_dm_op3_dm_written_w = tdr_upd && (upd_op == OpClear) && sba_written_q;
  wire c_dm_sbaddress0_write_inactive_w = dmi_wr && (dmi_req_addr == AddrSbaddress0) && !dmactive;
  wire c_dm_sbdata0_write_inactive_w = dmi_wr && (dmi_req_addr == AddrSbdata0) && !dmactive;
  wire c_dm_unmapped_access_w = dmi_hs && !dmi_addr_mapped;

  c_dm_command_write : cover property (@(posedge clk) c_dm_command_write_w);
  c_dm_dmactive_fall : cover property (@(posedge clk) c_dm_dmactive_fall_w);
  c_dm_dmactive_fall_ar_wait : cover property (@(posedge clk) c_dm_dmactive_fall_ar_wait_w);
  c_dm_dmactive_fall_aw_wait : cover property (@(posedge clk) c_dm_dmactive_fall_aw_wait_w);
  c_dm_dmactive_fall_w_wait : cover property (@(posedge clk) c_dm_dmactive_fall_w_wait_w);
  c_dm_dmstatus_read : cover property (@(posedge clk) c_dm_dmstatus_read_w);
  c_dm_hart_request_write : cover property (@(posedge clk) c_dm_hart_request_write_w);
  c_dm_ndmreset_set : cover property (@(posedge clk) c_dm_ndmreset_set_w);
  c_dm_op3_dm_written : cover property (@(posedge tck) c_dm_op3_dm_written_w);
  c_dm_sbaddress0_write_inactive : cover property (@(posedge clk) c_dm_sbaddress0_write_inactive_w);
  c_dm_sbdata0_write_inactive : cover property (@(posedge clk) c_dm_sbdata0_write_inactive_w);
  c_dm_unmapped_access : cover property (@(posedge clk) c_dm_unmapped_access_w);

  // ---------------------------------------------------------------------------
  // Cover properties: reset
  // ---------------------------------------------------------------------------
  // Set from a dmactive fall with the bus idle until an SBA register access, a bus
  // request or dmactive rising
  logic dmactive_alone_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) dmactive_alone_q <= 1'b0;
    else if (dmactive_fall && bus_idle) dmactive_alone_q <= 1'b1;
    else if ((dmi_hs && dmi_sba_reg) || any_req_valid || dmactive) dmactive_alone_q <= 1'b0;
  end

  wire c_rst_ar_wait_w = rst_fell_new && pre_rst.ar_wait;
  wire c_rst_b_pending_w = rst_fell_new && pre_rst.b_pending;
  wire c_rst_bridge_busy_w = rst_fell_new && pre_rst.bridge_busy;
  wire c_rst_busy_after_release_w = tdr_cap && (cap_status == StatusBusy) && rel_no_req_q;
  wire c_rst_cdc_clear_20to1_w = cdc_clear_done && ratio_clk_much_faster;
  wire c_rst_cdc_clear_20to19_w = cdc_clear_done && ratio_clk_slightly_faster;
  wire c_rst_cdc_clear_20to21_w = cdc_clear_done && ratio_tck_slightly_faster;
  wire c_rst_cdc_clear_1to2_w = cdc_clear_done && ratio_tck_faster;
  wire c_rst_cdc_clear_1to20_w = cdc_clear_done && ratio_tck_much_faster;
  wire c_rst_cdc_req_inflight_w = rst_fell_new && pre_rst.cdc_req_inflight;
  wire c_rst_dmactive_alone_w = dmactive && !dmactive_q && dmactive_alone_q;
  wire c_rst_dmactive_idle_w = dmactive_fall && bus_idle && !r_owed_q && !b_owed_q;
  wire c_rst_dmactive_sba_access_w = dmactive_fall &&
      (sbbusy || any_req_valid || r_owed_q || b_owed_q);
  wire c_rst_during_cdc_clear_w = rst_fell_new && pre_rst.cdc_clear;
  wire c_rst_during_live_cdc_clear_w = rst_fell_new && pre_rst.cdc_clear_live;
  wire c_rst_idle_w = rst_fell_new && pre_rst.idle;
  wire c_rst_r_pending_w = rst_fell_new && pre_rst.r_pending;
  wire c_rst_release_clk_first_w = tck_rel_new && tck_rel_with_clk_up;
  wire c_rst_release_tck_first_w = clk_rel_new && clk_rel_with_tck_up;
  wire c_rst_release_tck_stopped_w = tck_stopped_rel_new;
  wire c_rst_release_test_mode_off_w = tck_rel_new && !test_mode;
  wire c_rst_scan_reset_w = scan_rst_new;
  wire c_rst_tck_release_ss3_w = tck_rel_new && (SYNC_STAGES == 3);
  wire c_rst_tck_release_ss4_w = tck_rel_new && (SYNC_STAGES == 4);
  wire c_rst_test_mode_access_w = req_hs && test_mode;
  wire c_rst_trst_op_in_flight_w = trst_stop_new && tdr_cap && (cap_status == StatusOk);
  wire c_rst_w_alone_w = rst_fell_new && pre_rst.w_alone;
  wire c_rst_w_wait_w = rst_fell_new && pre_rst.w_wait;

  c_rst_ar_wait : cover property (@(posedge clk) c_rst_ar_wait_w);
  c_rst_b_pending : cover property (@(posedge clk) c_rst_b_pending_w);
  c_rst_bridge_busy : cover property (@(posedge clk) c_rst_bridge_busy_w);
  c_rst_busy_after_release : cover property (@(posedge tck) c_rst_busy_after_release_w);
  c_rst_cdc_clear_20to1 : cover property (@(posedge tck) c_rst_cdc_clear_20to1_w);
  c_rst_cdc_clear_20to19 : cover property (@(posedge tck) c_rst_cdc_clear_20to19_w);
  c_rst_cdc_clear_20to21 : cover property (@(posedge tck) c_rst_cdc_clear_20to21_w);
  c_rst_cdc_clear_1to2 : cover property (@(posedge tck) c_rst_cdc_clear_1to2_w);
  c_rst_cdc_clear_1to20 : cover property (@(posedge tck) c_rst_cdc_clear_1to20_w);
  c_rst_cdc_req_inflight : cover property (@(posedge clk) c_rst_cdc_req_inflight_w);
  c_rst_dmactive_alone : cover property (@(posedge clk) c_rst_dmactive_alone_w);
  c_rst_dmactive_idle : cover property (@(posedge clk) c_rst_dmactive_idle_w);
  c_rst_dmactive_sba_access : cover property (@(posedge clk) c_rst_dmactive_sba_access_w);
  c_rst_during_cdc_clear : cover property (@(posedge clk) c_rst_during_cdc_clear_w);
  c_rst_during_live_cdc_clear : cover property (@(posedge clk) c_rst_during_live_cdc_clear_w);
  c_rst_idle : cover property (@(posedge clk) c_rst_idle_w);
  c_rst_r_pending : cover property (@(posedge clk) c_rst_r_pending_w);
  c_rst_release_clk_first : cover property (@(posedge clk) c_rst_release_clk_first_w);
  c_rst_release_tck_first : cover property (@(posedge clk) c_rst_release_tck_first_w);
  c_rst_release_tck_stopped : cover property (@(posedge clk) c_rst_release_tck_stopped_w);
  c_rst_release_test_mode_off : cover property (@(posedge clk) c_rst_release_test_mode_off_w);
  c_rst_scan_reset : cover property (@(posedge clk) c_rst_scan_reset_w);
  c_rst_tck_release_ss3 : cover property (@(posedge clk) c_rst_tck_release_ss3_w);
  c_rst_tck_release_ss4 : cover property (@(posedge clk) c_rst_tck_release_ss4_w);
  c_rst_test_mode_access : cover property (@(posedge clk) c_rst_test_mode_access_w);
  c_rst_trst_op_in_flight : cover property (@(posedge tck) c_rst_trst_op_in_flight_w);
  c_rst_w_alone : cover property (@(posedge clk) c_rst_w_alone_w);
  c_rst_w_wait : cover property (@(posedge clk) c_rst_w_wait_w);

  // ---------------------------------------------------------------------------
  // Cover properties: ports and build
  // ---------------------------------------------------------------------------
  wire [2:0] req_prot = ar_hs ? ar_prot : aw_prot;
  wire [3:0] req_cache = ar_hs ? ar_cache : aw_cache;

  wire c_cfg_addr_width_2_w = req_hs && (AXI_ADDR_WIDTH == 2);
  wire c_cfg_addr_width_3_w = req_hs && (AXI_ADDR_WIDTH == 3);
  wire c_cfg_addr_width_32_w = req_hs && (AXI_ADDR_WIDTH == 32);
  wire c_cfg_addr_width_48_w = req_hs && (AXI_ADDR_WIDTH == 48);
  wire c_cfg_addr_width_64_w = req_hs && (AXI_ADDR_WIDTH == 64);
  wire c_cfg_axi4_w = req_hs && !USE_AXI_LITE;
  wire c_cfg_axi4_lite_w = req_hs && USE_AXI_LITE;
  wire c_cfg_cache_0000_w = req_hs && !USE_AXI_LITE && (req_cache == 4'b0000);
  wire c_cfg_cache_1111_w = req_hs && !USE_AXI_LITE && (req_cache == 4'b1111);
  wire c_cfg_data_width_32_w = req_hs && (AXI_DATA_WIDTH == 32);
  wire c_cfg_data_width_64_w = req_hs && (AXI_DATA_WIDTH == 64);
  wire c_cfg_first_access_w = req_hs && first_access_q;
  wire c_cfg_min_addr_width_w = req_hs && (AXI_ADDR_WIDTH == MinAddrWidth);
  wire c_cfg_min_addr_width_sbcs_w = c_sba_sbcs_active_w && (AXI_ADDR_WIDTH == MinAddrWidth);
  wire c_cfg_prot_010_w = req_hs && (req_prot == 3'b010);
  wire c_cfg_prot_101_w = req_hs && (req_prot == 3'b101);
  wire c_port_axi_read_write_w = (ar_hs && port_aw_seen_q) || (aw_hs && port_ar_seen_q);
  wire c_port_tdr_scan_w = tdr_upd && cap_in_scan_q && !tdr_cap;

  c_cfg_addr_width_2 : cover property (@(posedge clk) c_cfg_addr_width_2_w);
  c_cfg_addr_width_3 : cover property (@(posedge clk) c_cfg_addr_width_3_w);
  c_cfg_addr_width_32 : cover property (@(posedge clk) c_cfg_addr_width_32_w);
  c_cfg_addr_width_48 : cover property (@(posedge clk) c_cfg_addr_width_48_w);
  c_cfg_addr_width_64 : cover property (@(posedge clk) c_cfg_addr_width_64_w);
  c_cfg_axi4 : cover property (@(posedge clk) c_cfg_axi4_w);
  c_cfg_axi4_lite : cover property (@(posedge clk) c_cfg_axi4_lite_w);
  c_cfg_cache_0000 : cover property (@(posedge clk) c_cfg_cache_0000_w);
  c_cfg_cache_1111 : cover property (@(posedge clk) c_cfg_cache_1111_w);
  c_cfg_data_width_32 : cover property (@(posedge clk) c_cfg_data_width_32_w);
  c_cfg_data_width_64 : cover property (@(posedge clk) c_cfg_data_width_64_w);
  c_cfg_first_access : cover property (@(posedge clk) c_cfg_first_access_w);
  c_cfg_min_addr_width : cover property (@(posedge clk) c_cfg_min_addr_width_w);
  c_cfg_min_addr_width_sbcs : cover property (@(posedge clk) c_cfg_min_addr_width_sbcs_w);
  c_cfg_prot_010 : cover property (@(posedge clk) c_cfg_prot_010_w);
  c_cfg_prot_101 : cover property (@(posedge clk) c_cfg_prot_101_w);
  c_port_axi_read_write : cover property (@(posedge clk) c_port_axi_read_write_w);
  c_port_tdr_scan : cover property (@(posedge tck) c_port_tdr_scan_w);

`ifndef VERILATOR
  // ---------------------------------------------------------------------------
  // Covergroups, closed over the merged database of the commercial simulator
  // ---------------------------------------------------------------------------
  localparam logic [8:0] CauseSbaddress0 = {AddrSbaddress0, OpWrite};
  localparam logic [8:0] CauseSbaddress1 = {AddrSbaddress1, OpWrite};
  localparam logic [8:0] CauseSbdata0Read = {AddrSbdata0, OpRead};
  localparam logic [8:0] CauseSbdata0Write = {AddrSbdata0, OpWrite};
  localparam logic [8:0] CauseSbdata1Read = {AddrSbdata1, OpRead};
  localparam logic [8:0] CauseSbdata1Write = {AddrSbdata1, OpWrite};

  wire [2:0] req_lane = ar_hs ? 3'(ar_addr64 % NBytes) : 3'(aw_addr64 % NBytes);
  wire [1:0] err_resp = r_hs ? r_resp : b_resp;

  covergroup axi_attr_cg @(posedge clk iff req_hs);
    cp_prot: coverpoint req_prot {bins prot_010 = {3'b010}; bins prot_101 = {3'b101};}
    cp_cache: coverpoint req_cache iff (!USE_AXI_LITE) {
      bins cache_0000 = {4'b0000}; bins cache_1111 = {4'b1111};
    }
  endgroup

  covergroup axi_cfg_cg @(posedge clk iff axi_hs);
    cp_lite: coverpoint USE_AXI_LITE {bins axi4 = {1'b0}; bins axi4_lite = {1'b1};}
  endgroup

  covergroup axi_size_cg @(posedge clk iff req_hs);
    cp_size: coverpoint sbaccess {bins size[] = {[0 : MaxSize]};}
    cp_lane: coverpoint req_lane {bins lane[] = {[0 : NBytes - 1]};}
    cp_dir: coverpoint ar_hs {bins write = {1'b0}; bins read = {1'b1};}
    cx_size_lane_dir_axi4: cross cp_size, cp_lane, cp_dir iff (!USE_AXI_LITE);
    cx_size_lane_dir_lite: cross cp_size, cp_lane, cp_dir iff (USE_AXI_LITE);
  endgroup

  covergroup dm_reg_cg @(posedge clk iff dmi_hs);
    cp_addr: coverpoint dmi_req_addr {
      bins dmcontrol = {AddrDmcontrol};
      bins dmstatus = {AddrDmstatus};
      bins abstractcs = {AddrAbstractcs};
      bins command = {AddrCommand};
      bins sbcs = {AddrSbcs};
      bins sbaddress0 = {AddrSbaddress0};
      bins sbaddress1 = {AddrSbaddress1};
      bins sbdata0 = {AddrSbdata0};
      bins sbdata1 = {AddrSbdata1};
      bins unmapped = {[7'h00 : 7'h03], 7'h1E, 7'h1F, [7'h31 : 7'h33], 7'h36, [7'h41 : 7'h7F]};
    }
    cp_op: coverpoint dmi_req_op {bins read = {OpRead}; bins write = {OpWrite};}
    cx_addr_op: cross cp_addr, cp_op;
  endgroup

  covergroup rst_cfg_cg @(posedge arst_clk_n);
    cp_sync_stages: coverpoint SYNC_STAGES {bins stages_3 = {3}; bins stages_4 = {4};}
  endgroup

  covergroup sba_block_cg with function sample (int unsigned len, bit readondata);
    cp_len: coverpoint len {
      bins one = {1}; bins two_to_four = {[2 : 4]}; bins five_to_16 = {[5 : 16]};
      bins above_16 = {[17 : $]};
    }
    cp_readondata: coverpoint readondata;
    cx_len_readondata: cross cp_len, cp_readondata;
  endgroup

  covergroup sba_busy_cg @(posedge clk iff (dmi_hs && sbbusy));
    cp_cause: coverpoint {dmi_req_addr, dmi_req_op} {
      bins sbaddress0 = {CauseSbaddress0};
      bins sbaddress1 = {CauseSbaddress1};
      bins sbdata0_read = {CauseSbdata0Read};
      bins sbdata0_write = {CauseSbdata0Write};
      bins sbdata1 = {CauseSbdata1Read, CauseSbdata1Write};
    }
  endgroup

  covergroup sba_cfg_cg @(posedge clk iff req_hs);
    cp_addr_width: coverpoint AXI_ADDR_WIDTH {
      bins width_2 = {2}; bins width_3 = {3}; bins width_32 = {32}; bins width_33_63 = {[33 : 63]};
      bins width_64 = {64};
    }
    cp_data_width: coverpoint AXI_DATA_WIDTH {bins width_32 = {32}; bins width_64 = {64};}
  endgroup

  covergroup sba_err_cg @(posedge clk iff (r_err || b_err));
    cp_resp: coverpoint err_resp {bins slverr = {2'd2}; bins decerr = {2'd3};}
    cp_dir: coverpoint r_err {bins write = {1'b0}; bins read = {1'b1};}
    cx_resp_dir: cross cp_resp, cp_dir;
  endgroup

  covergroup tdr_latency_cg with function sample (int unsigned edges);
    cp_edges: coverpoint edges {
      bins sync_plus_2 = {SYNC_STAGES + 2}; bins sync_plus_3 = {SYNC_STAGES + 3};
    }
  endgroup

  covergroup tdr_op_cg with function sample (logic [1:0] op, logic [1:0] status);
    cp_op: coverpoint op {
      bins nop = {OpNop}; bins rd = {OpRead}; bins wr = {OpWrite}; bins clr = {OpClear};
    }
    cp_status: coverpoint status {bins ok = {StatusOk}; bins busy = {StatusBusy};}
    cx_op_status: cross cp_op, cp_status;
  endgroup

  covergroup tdr_ratio_cg with function sample (int unsigned ratio);
    cp_ratio: coverpoint ratio {
      bins clk_much_faster = {0}; bins clk_slightly_faster = {1}; bins tck_faster = {2};
      bins tck_much_faster = {3}; bins other = {4}; bins tck_slightly_faster = {5};
    }
    cp_sync: coverpoint SYNC_STAGES {bins stages_3 = {3}; bins stages_4 = {4};}
    cx_ratio_sync: cross cp_ratio, cp_sync;
  endgroup

  axi_attr_cg    axi_attr_cg_inst = new();
  axi_cfg_cg     axi_cfg_cg_inst = new();
  axi_size_cg    axi_size_cg_inst = new();
  dm_reg_cg      dm_reg_cg_inst = new();
  rst_cfg_cg     rst_cfg_cg_inst = new();
  sba_block_cg   sba_block_cg_inst = new();
  sba_busy_cg    sba_busy_cg_inst = new();
  sba_cfg_cg     sba_cfg_cg_inst = new();
  sba_err_cg     sba_err_cg_inst = new();
  tdr_latency_cg tdr_latency_cg_inst = new();
  tdr_op_cg      tdr_op_cg_inst = new();
  tdr_ratio_cg   tdr_ratio_cg_inst = new();

  // A block is a run of accesses at consecutive addresses with sbautoincrement set,
  // sampled when an access breaks the run
  int unsigned block_len;
  bit          block_readondata;
  logic [63:0] block_next_addr;

  initial begin
    block_len        = 0;
    block_readondata = 1'b0;
    block_next_addr  = '0;
  end

  always @(posedge clk) begin
    if (req_hs) begin
      if ((block_len > 0) && sbautoincrement &&
          ((ar_hs ? ar_addr64 : aw_addr64) == block_next_addr)) begin
        block_len = block_len + 1;
      end else begin
        if (block_len > 0) sba_block_cg_inst.sample(block_len, block_readondata);
        block_len        = sbautoincrement ? 1 : 0;
        block_readondata = sbreadondata;
      end
      block_next_addr = (ar_hs ? ar_addr64 : aw_addr64) + (64'd1 << sbaccess);
    end
  end

  always @(posedge tck) begin
    if (latency_sampled) tdr_latency_cg_inst.sample(latency_sample);
    if (tdr_cap && op_since_cap_valid_q) tdr_op_cg_inst.sample(op_since_cap_q, cap_status);
    if (read_done) begin
      tdr_ratio_cg_inst.sample(ratio_clk_much_faster ? 0 : ratio_clk_slightly_faster ? 1 :
                               ratio_tck_slightly_faster ? 5 : ratio_tck_much_faster ? 3 :
                               ratio_tck_faster ? 2 : 4);
    end
  end
`endif

endmodule
