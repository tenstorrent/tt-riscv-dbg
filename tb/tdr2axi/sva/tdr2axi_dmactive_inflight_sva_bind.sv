// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_dmactive_inflight_sva into every tdr2axi instance, on the request
//   structs of o_axi_req.

bind tdr2axi tdr2axi_dmactive_inflight_sva #(
    .AR_BITS($bits(o_axi_req.ar)),
    .AW_BITS($bits(o_axi_req.aw)),
    .W_BITS ($bits(o_axi_req.w))
) u_dmactive_inflight_sva (
    .clk_i     (i_clk),
    .rst_ni    (arst_clk_n),
    .dmactive_i(dmactive),
    .ar_valid_i(o_axi_req.ar_valid),
    .ar_ready_i(i_axi_rsp.ar_ready),
    .ar_i      (o_axi_req.ar),
    .aw_valid_i(o_axi_req.aw_valid),
    .aw_ready_i(i_axi_rsp.aw_ready),
    .aw_i      (o_axi_req.aw),
    .w_valid_i (o_axi_req.w_valid),
    .w_ready_i (i_axi_rsp.w_ready),
    .w_i       (o_axi_req.w)
);
