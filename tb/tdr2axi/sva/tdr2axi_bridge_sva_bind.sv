// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Binds tdr2axi_bridge_sva into every dmi_tdr_bridge instance, on its TCK-side FSM and
//   the CDC ends it drives and reads.

bind dmi_tdr_bridge tdr2axi_bridge_sva u_bridge_sva (
    .clk_i       (i_tck),
    .rst_ni      (i_arst_tck_n),
    .tdr_i       (i_dmi_tdr),
    .capture_i   (i_dmi_tdr_capture),
    .update_i    (i_dmi_tdr_update),
    .tdr_in_i    (o_dmi_tdr),
    .state_i     (state_q),
    .error_i     (error_q),
    .req_valid_i (dmi_req_valid),
    .req_addr_i  (dmi_req.addr),
    .req_op_i    (dmi_req.op),
    .req_data_i  (dmi_req.data),
    .resp_valid_i(dmi_resp_valid),
    .resp_code_i (dmi_resp.resp)
);
