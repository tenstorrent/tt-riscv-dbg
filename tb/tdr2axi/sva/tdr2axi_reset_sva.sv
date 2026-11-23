// SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Description:
//   Reset synchronizer properties of tdr2axi for a model in which i_clk, TCK and the reset
//   inputs change independently. FUNC_MODE = 1 checks functional mode (i_test_mode = 0):
//   both internal resets assert with i_arst_n and each releases SYNC_STAGES to
//   SYNC_STAGES + 1 rising edges of its own clock after i_arst_n rises. FUNC_MODE = 0
//   checks the test-mode bypass. The assertions are immediate, so they hold at every step
//   of the model, not only at clock edges. Each release counter counts its clock's rising
//   edges since i_arst_n was last low and saturates at SYNC_STAGES + 1; a counter means
//   nothing until i_arst_n has been low once. Each failure message starts with its check ID.

module tdr2axi_reset_sva #(
    parameter int unsigned SYNC_STAGES = 3,
    parameter bit          FUNC_MODE   = 1'b1
) (
    input logic clk_i,
    input logic tck_i,
    input logic arst_ni,
    input logic test_mode_i,
    input logic arst_scan_ni,
    // Internal resets of tdr2axi
    input logic arst_clk_ni,
    input logic arst_tck_ni
);

  localparam int unsigned CountMax = SYNC_STAGES + 1;
  localparam int unsigned CountBits = $clog2(CountMax + 1);

  if (FUNC_MODE) begin : g_func
    // Each armed flag starts at 0, is set while i_arst_n is low and holds otherwise. The
    // clocked branch assigns it too: the slang frontend builds a flop only for a variable
    // that branch assigns, and ties any other to its initializer.
    logic                 armed_clk_q = 1'b0;
    logic                 armed_tck_q = 1'b0;
    logic [CountBits-1:0] count_clk_q;
    logic [CountBits-1:0] count_tck_q;

    always_ff @(posedge clk_i or negedge arst_ni) begin
      if (!arst_ni) begin
        armed_clk_q <= 1'b1;
        count_clk_q <= '0;
      end else begin
        armed_clk_q <= armed_clk_q;
        if (count_clk_q != CountBits'(CountMax)) count_clk_q <= count_clk_q + 1'b1;
      end
    end

    always_ff @(posedge tck_i or negedge arst_ni) begin
      if (!arst_ni) begin
        armed_tck_q <= 1'b1;
        count_tck_q <= '0;
      end else begin
        armed_tck_q <= armed_tck_q;
        if (count_tck_q != CountBits'(CountMax)) count_tck_q <= count_tck_q + 1'b1;
      end
    end

    always_comb begin
      // CHK-RESET-SYNC-RELEASE-SVA: S-TDR2AXI-0026, S-TDR2AXI-0052
      ast_reset_sync_release_assert :
      assert (arst_ni || (!arst_clk_ni && !arst_tck_ni))
      else $error("CHK-RESET-SYNC-RELEASE-SVA FAIL: an internal reset is high with i_arst_n low");

      // CHK-RESET-SYNC-RELEASE-SVA: S-TDR2AXI-0018, S-TDR2AXI-0052
      ast_reset_sync_release_clk_held :
      assert (!armed_clk_q || (count_clk_q >= CountBits'(SYNC_STAGES)) || !arst_clk_ni)
      else $error("CHK-RESET-SYNC-RELEASE-SVA FAIL: i_clk reset released after %0d edges",
                  count_clk_q);

      // CHK-RESET-SYNC-RELEASE-SVA: S-TDR2AXI-0018, S-TDR2AXI-0052
      ast_reset_sync_release_clk_done :
      assert (!armed_clk_q || (count_clk_q < CountBits'(CountMax)) || arst_clk_ni)
      else $error("CHK-RESET-SYNC-RELEASE-SVA FAIL: i_clk reset held after %0d edges",
                  count_clk_q);

      // CHK-RESET-SYNC-RELEASE-SVA: S-TDR2AXI-0018, S-TDR2AXI-0052, S-TDR2AXI-0053
      ast_reset_sync_release_tck_held :
      assert (!armed_tck_q || (count_tck_q >= CountBits'(SYNC_STAGES)) || !arst_tck_ni)
      else $error("CHK-RESET-SYNC-RELEASE-SVA FAIL: TCK reset released after %0d edges",
                  count_tck_q);

      // CHK-RESET-SYNC-RELEASE-SVA: S-TDR2AXI-0018, S-TDR2AXI-0052, S-TDR2AXI-0053
      ast_reset_sync_release_tck_done :
      assert (!armed_tck_q || (count_tck_q < CountBits'(CountMax)) || arst_tck_ni)
      else $error("CHK-RESET-SYNC-RELEASE-SVA FAIL: TCK reset held after %0d edges",
                  count_tck_q);

      cov_reset_sync_release_assert : cover (!arst_ni && armed_clk_q && armed_tck_q);

      cov_reset_sync_release_clk :
      cover (armed_clk_q && arst_clk_ni && (count_clk_q == CountBits'(SYNC_STAGES)));

      cov_reset_sync_release_tck :
      cover (armed_tck_q && arst_tck_ni && (count_tck_q == CountBits'(SYNC_STAGES)));

      // One side released while the other side's reset is held by its synchronizer
      cov_reset_sync_release_clk_only : cover (armed_tck_q && arst_clk_ni && !arst_tck_ni);

      cov_reset_sync_release_tck_only : cover (armed_clk_q && arst_tck_ni && !arst_clk_ni);
    end
  end else begin : g_test_mode
    always_comb begin
      // CHK-RESET-TEST-MODE-SVA: S-TDR2AXI-0027, S-TDR2AXI-0028, S-TDR2AXI-0052
      ast_reset_test_mode_bypass :
      assert (!test_mode_i || ((arst_clk_ni == arst_scan_ni) && (arst_tck_ni == arst_scan_ni)))
      else $error("CHK-RESET-TEST-MODE-SVA FAIL: internal resets %b %b, i_arst_scan_n %b",
                  arst_clk_ni, arst_tck_ni, arst_scan_ni);

      cov_reset_test_mode_scan_low : cover (test_mode_i && !arst_scan_ni && arst_ni);

      cov_reset_test_mode_scan_high : cover (test_mode_i && arst_scan_ni && !arst_ni);
    end
  end

endmodule
