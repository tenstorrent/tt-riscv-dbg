// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   One bit delayed by one step of the global clock of a multiclock formal model, for
//   properties that compare a net with its value at the previous step. q is 0 at step 0.
//   Read with the native Yosys frontend (read_verilog -sv -formal), which lowers
//   $global_clock; the slang frontend instantiates it as a module already in the design.

module tdr2axi_fv_gclk_dly (
    input  logic d,
    output logic q
);

  initial q = 1'b0;

  always @($global_clock) q <= d;

endmodule
