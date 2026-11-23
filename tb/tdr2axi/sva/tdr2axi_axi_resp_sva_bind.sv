// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_axi_resp_sva into every tdr2axi instance, on its internal i_clk reset
//   and memory-port response.

bind tdr2axi tdr2axi_axi_resp_sva #(
    .AXI_DATA_WIDTH(AXI_DATA_WIDTH)
) u_axi_resp_sva (
    .clk_i      (i_clk),
    .rst_ni     (arst_clk_n),
    .sbaccess_i (sbaccess),
    .ar_valid_i (o_axi_req.ar_valid),
    .ar_ready_i (i_axi_rsp.ar_ready),
    .aw_valid_i (o_axi_req.aw_valid),
    .aw_ready_i (i_axi_rsp.aw_ready),
    .w_valid_i  (o_axi_req.w_valid),
    .w_ready_i  (i_axi_rsp.w_ready),
    .r_valid_i  (i_axi_rsp.r_valid),
    .r_ready_i  (o_axi_req.r_ready),
    .b_valid_i  (i_axi_rsp.b_valid),
    .b_ready_i  (o_axi_req.b_ready),
    .rsp_valid_i(mem_rsp_valid)
);
