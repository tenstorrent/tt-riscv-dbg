// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_dm_resp_sva into every tdr2axi instance, on the DMI response of
//   u_dm_csrs.

bind tdr2axi tdr2axi_dm_resp_sva u_dm_resp_sva (
    .clk_i       (i_clk),
    .rst_ni      (arst_clk_n),
    .resp_valid_i(dmi_resp_valid),
    .resp_i      (dmi_resp.resp)
);
