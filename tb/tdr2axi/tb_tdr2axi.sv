// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   cocotb top for tdr2axi: the DUT with its TDR and AXI ports flattened into plain
//   signals. test_tdr2axi.py drives the JTAG side and plays the AXI subordinate.
//   AXI4 and AXI4-Lite builds expose the same names; AxSIZE / AxLEN read 0 in AXI4-Lite.

`timescale 1ps / 1ps

`include "axi/typedef.svh"

module tb_tdr2axi #(
    parameter bit          AXI_LITE       = 1'b0,
    parameter int unsigned AXI_ADDR_WIDTH = 64,
    parameter int unsigned AXI_DATA_WIDTH = 64
);

  localparam int unsigned StrbWidth = AXI_DATA_WIDTH / 8;

  typedef logic [AXI_ADDR_WIDTH-1:0] addr_t;
  typedef logic [AXI_DATA_WIDTH-1:0] data_t;
  typedef logic [StrbWidth-1:0] strb_t;
  typedef logic [0:0] id_t;
  typedef logic [0:0] user_t;

  `AXI_TYPEDEF_ALL(axi, addr_t, id_t, data_t, strb_t, user_t)
  `AXI_LITE_TYPEDEF_ALL(axil, addr_t, data_t, strb_t)

  // Build configuration, read back by the test
  wire        cfg_axi_lite = AXI_LITE;
  wire [31:0] cfg_addr_width = AXI_ADDR_WIDTH;
  wire [31:0] cfg_data_width = AXI_DATA_WIDTH;

  // Driven by the test
  logic [8*24-1:0] test_name;  // ASCII name of the running test, for the waveform
  logic        clk;
  logic        rst_n;
  logic        tck;
  logic [40:0] tdr_upd_q;
  logic        tdr_upd;
  logic        tdr_cap;
  logic        ar_ready;
  logic        aw_ready;
  logic        w_ready;
  logic        r_valid;
  data_t       r_data;
  logic [ 1:0] r_resp;
  logic        b_valid;
  logic [ 1:0] b_resp;

  // Read by the test
  logic [40:0] tdr_in;
  logic        ar_valid;
  addr_t       ar_addr;
  logic [ 2:0] ar_size;
  logic [ 7:0] ar_len;
  logic        aw_valid;
  addr_t       aw_addr;
  logic [ 2:0] aw_size;
  logic [ 7:0] aw_len;
  logic        w_valid;
  data_t       w_data;
  strb_t       w_strb;
  logic        r_ready;
  logic        b_ready;

  // Both branches share the name g_bus
  if (!AXI_LITE) begin : g_bus
    axi_req_t  dut_req;
    axi_resp_t dut_rsp;

    tdr2axi #(
        .AXI_LITE      (1'b0),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .axi_req_t     (axi_req_t),
        .axi_rsp_t     (axi_resp_t)
    ) u_dut (
        .i_clk            (clk),
        .i_arst_n         (rst_n),
        .i_test_mode      (1'b0),
        .i_arst_scan_n    (1'b1),
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
      dut_rsp.ar_ready = ar_ready;
      dut_rsp.aw_ready = aw_ready;
      dut_rsp.w_ready  = w_ready;
      dut_rsp.r_valid  = r_valid;
      dut_rsp.r.data   = r_data;
      dut_rsp.r.resp   = r_resp;
      dut_rsp.r.last   = 1'b1;
      dut_rsp.b_valid  = b_valid;
      dut_rsp.b.resp   = b_resp;
    end

    assign ar_valid = dut_req.ar_valid;
    assign ar_addr  = dut_req.ar.addr;
    assign ar_size  = dut_req.ar.size;
    assign ar_len   = dut_req.ar.len;
    assign aw_valid = dut_req.aw_valid;
    assign aw_addr  = dut_req.aw.addr;
    assign aw_size  = dut_req.aw.size;
    assign aw_len   = dut_req.aw.len;
    assign w_valid  = dut_req.w_valid;
    assign w_data   = dut_req.w.data;
    assign w_strb   = dut_req.w.strb;
    assign r_ready  = dut_req.r_ready;
    assign b_ready  = dut_req.b_ready;
  end else begin : g_bus
    axil_req_t  dut_req;
    axil_resp_t dut_rsp;

    tdr2axi #(
        .AXI_LITE      (1'b1),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .axi_req_t     (axil_req_t),
        .axi_rsp_t     (axil_resp_t)
    ) u_dut (
        .i_clk            (clk),
        .i_arst_n         (rst_n),
        .i_test_mode      (1'b0),
        .i_arst_scan_n    (1'b1),
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
      dut_rsp.ar_ready = ar_ready;
      dut_rsp.aw_ready = aw_ready;
      dut_rsp.w_ready  = w_ready;
      dut_rsp.r_valid  = r_valid;
      dut_rsp.r.data   = r_data;
      dut_rsp.r.resp   = r_resp;
      dut_rsp.b_valid  = b_valid;
      dut_rsp.b.resp   = b_resp;
    end

    assign ar_valid = dut_req.ar_valid;
    assign ar_addr  = dut_req.ar.addr;
    assign ar_size  = '0;
    assign ar_len   = '0;
    assign aw_valid = dut_req.aw_valid;
    assign aw_addr  = dut_req.aw.addr;
    assign aw_size  = '0;
    assign aw_len   = '0;
    assign w_valid  = dut_req.w_valid;
    assign w_data   = dut_req.w.data;
    assign w_strb   = dut_req.w.strb;
    assign r_ready  = dut_req.r_ready;
    assign b_ready  = dut_req.b_ready;
  end

endmodule
