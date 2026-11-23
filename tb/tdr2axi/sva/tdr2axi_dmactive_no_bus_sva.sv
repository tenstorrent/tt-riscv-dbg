// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   No bus access starts while the Debug Module is inactive, bound by
//   tdr2axi_dmactive_no_bus_sva_bind.sv. Each failure message starts with its check ID.

module tdr2axi_dmactive_no_bus_sva (
    input logic clk_i,
    // Internal i_clk reset of tdr2axi
    input logic rst_ni,
    input logic dmactive_i,
    input logic ar_valid_i,
    input logic aw_valid_i
);

  // CHK-DM-DMACTIVE-NO-BUS-SVA: S-TDR2AXI-0056, S-TDR2AXI-0064, X-RISCV_DEBUG-dmactive
  ast_dm_dmactive_no_bus_ar :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !(ar_valid_i && !$past(ar_valid_i) && $past(rst_ni) && !dmactive_i))
  else $error("CHK-DM-DMACTIVE-NO-BUS-SVA FAIL: ARVALID rose while dmactive is 0");

  // CHK-DM-DMACTIVE-NO-BUS-SVA: S-TDR2AXI-0056, S-TDR2AXI-0064, X-RISCV_DEBUG-dmactive
  ast_dm_dmactive_no_bus_aw :
  assert property (@(posedge clk_i) disable iff (!rst_ni)
      !(aw_valid_i && !$past(aw_valid_i) && $past(rst_ni) && !dmactive_i))
  else $error("CHK-DM-DMACTIVE-NO-BUS-SVA FAIL: AWVALID rose while dmactive is 0");

  // A request rises, and dmactive returns to 0 after it was set
  cov_dm_dmactive_no_bus_ar_rise :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      ar_valid_i && !$past(ar_valid_i) && $past(rst_ni));

  cov_dm_dmactive_no_bus_aw_rise :
  cover property (@(posedge clk_i) disable iff (!rst_ni)
      aw_valid_i && !$past(aw_valid_i) && $past(rst_ni));

  cov_dm_dmactive_no_bus_inactive :
  cover property (@(posedge clk_i) disable iff (!rst_ni) !dmactive_i && $past(dmactive_i));

endmodule
