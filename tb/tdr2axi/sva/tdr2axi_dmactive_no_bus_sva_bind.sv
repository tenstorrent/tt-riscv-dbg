// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_dmactive_no_bus_sva into every tdr2axi instance.

bind tdr2axi tdr2axi_dmactive_no_bus_sva u_dmactive_no_bus_sva (
    .clk_i     (i_clk),
    .rst_ni    (arst_clk_n),
    .dmactive_i(dmactive),
    .ar_valid_i(o_axi_req.ar_valid),
    .aw_valid_i(o_axi_req.aw_valid)
);
