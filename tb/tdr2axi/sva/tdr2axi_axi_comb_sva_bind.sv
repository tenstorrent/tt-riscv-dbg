// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_axi_comb_sva into every tdr2axi instance, on the whole of o_axi_req and
//   i_axi_rsp.

bind tdr2axi tdr2axi_axi_comb_sva #(
    .REQ_BITS($bits(o_axi_req)),
    .RSP_BITS($bits(i_axi_rsp))
) u_axi_comb_sva (
    .clk_i (i_clk),
    .req_i (o_axi_req),
    .rsp_i (i_axi_rsp),
    .busy_i(o_axi_req.ar_valid || o_axi_req.aw_valid || o_axi_req.w_valid ||
            o_axi_req.r_ready || o_axi_req.b_ready)
);
