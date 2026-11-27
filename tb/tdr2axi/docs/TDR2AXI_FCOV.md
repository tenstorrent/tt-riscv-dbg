<!--
SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
SPDX-License-Identifier: CC-BY-4.0
-->

# tdr2axi Functional Coverage Plan

Functional coverage for `tdr2axi` is collected by one module, `cov/sv/tdr2axi_fcov.sv`,
instantiated in `tb/tb_top.sv` so Verilator and VCS collect the same points. Its cover
properties are counted per run by both simulators; its covergroups, one per area, sit
under `ifndef VERILATOR` and are closed over the merged VCS database. Every point names
the feature whose behavior it measures, so each hole maps to a feature.

| Field | Value |
|---|---|
| Plan | `TDR2AXI_VPLAN.md` |
| Testbench | `TDR2AXI_TB_ARCH.md` |
| Trace store | `tb/tdr2axi/trace/covers.toml`: every point cites a feature |
| Coverage closure | `trace plan --covers` passes: every feature, simulated item, functional clock and reset, and configuration dimension has a point |
| Hole dispositions | `cov/config/vcs/coverage_policy.toml` |

## Coverage Summary

| Area | Covergroups | Cover properties | Covergroup points | Features |
| --- | --- | --- | --- | --- |
| AXI manager | `axi_attr_cg`, `axi_cfg_cg`, `axi_size_cg` | 14 | 5 | `F-AXI-axi4-size`, `F-AXI-lite-full-width`, `F-AXI-manager-protocol`, `F-AXI-no-comb-path`, `F-AXI-one-outstanding`, `F-AXI-request-attributes` |
| Ports and build | none | 18 | 0 | `F-AXI-manager-protocol`, `F-AXI-request-attributes`, `F-CFG-bender-target`, `F-CFG-param-checks`, `F-PORT-bus-and-reset`, `F-PORT-tdr-interface`, `F-SBA-address-width`, `F-SBA-data-lanes` |
| Debug Module | `dm_hold_cg`, `dm_reg_cg` | 25 | 2 | `F-DM-abstract-cmd-unsupported`, `F-DM-dmactive-clear`, `F-DM-dmactive-inflight`, `F-DM-dmactive-no-bus-access`, `F-DM-hart-unavailable`, `F-DM-ndmreset-unconnected`, `F-DM-register-map`, `F-DM-version` |
| Reset | `rst_cfg_cg` | 30 | 1 | `F-DM-dmactive-clear`, `F-DM-dmactive-inflight`, `F-RESET-async-any-time`, `F-RESET-sync-release`, `F-RESET-test-mode-bypass`, `F-RESET-trst-independent` |
| System bus access | `sba_block_cg`, `sba_busy_cg`, `sba_cfg_cg`, `sba_err_cg` | 29 | 5 | `F-SBA-access-errors`, `F-SBA-address-width`, `F-SBA-block-read`, `F-SBA-block-write`, `F-SBA-bus-error`, `F-SBA-busy-status`, `F-SBA-data-lanes`, `F-SBA-no-increment-after-error`, `F-SBA-sbcs-fields`, `F-SBA-single-read`, `F-SBA-single-write` |
| TDR and DMI | `tdr_latency_cg`, `tdr_op_cg`, `tdr_ratio_cg` | 40 | 4 | `F-TDR-any-clock-ratio`, `F-TDR-busy-recovery`, `F-TDR-capture-image`, `F-TDR-op-decode`, `F-TDR-result-latency`, `F-TDR-sticky-busy`, `F-TDR-strobes`, `F-TDR-tck-stop-hold` |

## Coverage Goals

| Metric | Target |
|---|---|
| Cover properties | Every listed point hit by the items it names |
| Covergroups | Every bin hit, or dispositioned in the coverage policy with a reason |
| Code coverage | Line, branch and FSM 100% and toggle at least 95% for `src/tdr2axi/`; the SBA and DMI paths of the reused modules in full, the rest excluded only behind a formal unreachability proof |
| Assertion coverage | Every property below attempted and passing |

## Feeds

The module reads only DUT nets and the bench top's observables, never test variables:
the flattened AXI channels; the TDR nets and strobes; the DMI request and response
handshakes between the bridge and `u_dm_csrs`; the bridge's `state_q` and `error_q`;
the internal resets; `dmactive` and the SBA controls `sbaccess`, `sbautoincrement`,
`sbreadonaddr`, `sbreadondata` and `sbbusy`; dm_sba's one-cycle `sberror` report and the
sticky `sbcs_sberror` and `sbcs_sbbusyerror` fields and the `cmderr` field `u_dm_csrs`
holds; the CDCs' clear-pending outputs and the state of the `cdc_4phase_dst` receiver in
each half of their reset controllers; `i_test_mode` and `i_arst_scan_n`; and the build
parameters.
Clock ratios come from the module's own measurement of the `i_clk` and `i_tck` periods
between edges, and a TCK stop from the time since the last TCK edge.

The `c_sba_blocked_` points require `sbbusyerror` clear. `u_dm_csrs` tests `sbbusy` and
`sbbusyerror` before `sberror` on every `sbaddress0` and `sbdata0` access
(`src/dm_csrs.sv:393-397`, `:525-530`, `:544-549`), so while `sbbusyerror` is set the
access is refused for that reason, and only with it clear is `sberror` what blocks it.

A point that names an `i_arst_n` fall counts only a fall taken with `i_test_mode` = 0.
With `i_test_mode` = 1 both internal resets follow `i_arst_scan_n`, and an `i_arst_n`
pulse resets nothing.

The `dmactive` edges the points count are the ones a DMI write of `dmcontrol` makes. The
module keeps the history of `dmactive`, and of the R or B the AXI manager waits for, on
`arst_clk_n`, the reset of `u_dm_csrs` and of the manager. A reset of the DM, by
`i_arst_n` or `i_arst_scan_n`, clears `dmactive` and that history together and is no
fall, and an `i_arst_n` pulse in test mode leaves both as they were.

A point that names an AXI handshake or a DMI request counts only on that event, so no
point is hit by reset values or idle cycles alone.

## TDR and DMI

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `c_tdr_busy_on_capture` | `error_q` turns busy on a capture in READ or WAIT_READ_VALID | bridge `state_q`, `error_q`, capture strobe | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_capture_test` | none |
| `c_tdr_busy_on_dm_answer` | A DMI response with the busy code reaches the bridge | DMI response observables | `F-TDR-sticky-busy` | `tdr2axi_sba_busy_test` | none |
| `c_tdr_busy_on_nop_update` | `error_q` turns busy on an update with op 0 outside IDLE | bridge `state_q`, `error_q`, update strobe | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test` | none |
| `c_tdr_busy_on_update` | `error_q` turns busy on an update with op 1 or 2 outside IDLE | bridge `state_q`, `error_q`, update strobe | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test` | none |
| `c_tdr_capture_after_nop` | A capture with status 0 after a NOP | capture strobe, `o_dmi_tdr`, last accepted op | `F-TDR-capture-image` | `tdr2axi_tdr_ops_test` | none |
| `c_tdr_capture_at_bound` | A capture on the TCK edge the response reaches `o_dmi_tdr` | DMI response observables, capture strobe | `F-TDR-result-latency` | `tdr2axi_tdr_result_latency_test` | `T-TDR2AXI-resp-to-tdr` |
| `c_tdr_capture_at_bound_ss4` | On a `SYNC_STAGES` = 4 build, a capture on the TCK edge the response reaches `o_dmi_tdr` | DMI response observables, capture strobe, build parameter | `F-TDR-result-latency` | `tdr2axi_tdr_result_latency_test` | `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES` |
| `c_tdr_capture_next_op` | A capture whose scan updates a read or write, following a read | capture strobe, `o_dmi_tdr`, last accepted op | `F-TDR-capture-image` | `tdr2axi_tdr_pipelined_test` | none |
| `c_tdr_capture_read_data` | A capture with status 0 after a completed read | capture strobe, `o_dmi_tdr`, last accepted op | `F-TDR-capture-image` | `tdr2axi_tdr_ops_test`, `tdr2axi_tdr_pipelined_test` | none |
| `c_tdr_capture_write_data` | A capture with status 0 after a write | capture strobe, `o_dmi_tdr`, last accepted op | `F-TDR-capture-image` | `tdr2axi_tdr_ops_test` | none |
| `c_tdr_clk_stopped_op_pending` | An update while a request waits and `i_clk` has not toggled for 4 TCK periods | bridge `state_q`, `i_clk` activity | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test`, `tdr2axi_tdr_busy_capture_test` | `CLK-BUS` |
| `c_tdr_nop_in_idle` | An update with op 0 in IDLE with a clear status | update strobe, `i_dmi_tdr[1:0]`, bridge `state_q`, `error_q` | `F-TDR-op-decode` | `tdr2axi_tdr_ops_test` | none |
| `c_tdr_op3_clears_busy` | An update with op 3 while `error_q` is busy | bridge `error_q`, update strobe | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test`, `tdr2axi_tdr_busy_capture_test`, `tdr2axi_sba_busy_test`, `tdr2axi_tdr_busy_recovery_test` | none |
| `c_tdr_op3_in_idle` | An update with op 3 in IDLE with a clear status | update strobe, `i_dmi_tdr[1:0]`, bridge `state_q`, `error_q` | `F-TDR-op-decode` | `tdr2axi_tdr_ops_test` | none |
| `c_tdr_ratio_clk_much_faster` | A read completes with `i_clk` at least 10 times faster than TCK | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_ratio_clk_slightly_faster` | A read completes with `i_clk` faster than TCK by at most 10% | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_ratio_near_one` | A read completes with the periods within 10% of each other | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_ratio_tck_faster` | A read completes with TCK 1.5 to 4 times faster than `i_clk` | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_ratio_tck_much_faster` | A read completes with TCK at least 6 times faster than `i_clk` | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_ratio_tck_slightly_faster` | A read completes with TCK faster than `i_clk` by at most 10% | measured periods of `i_clk` and `i_tck`, capture | `F-TDR-any-clock-ratio` | `tdr2axi_clock_ratio_test` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` |
| `c_tdr_read_in_idle` | An update with op 1 in IDLE with a clear status | update strobe, `i_dmi_tdr[1:0]`, bridge `state_q`, `error_q` | `F-TDR-op-decode` | `tdr2axi_tdr_ops_test`, `tdr2axi_smoke_test` | none |
| `c_tdr_read_resent_twice` | Two AR handshakes at one address with no other AR between them | AXI AR channel | `F-TDR-busy-recovery` | `tdr2axi_tdr_busy_recovery_test` | none |
| `c_tdr_resend_completes` | Op 3, then an update in IDLE, then a capture with status 0 | update and capture strobes, `o_dmi_tdr` | `F-TDR-busy-recovery` | `tdr2axi_tdr_busy_recovery_test`, `tdr2axi_sba_stress_test` | none |
| `c_tdr_round_trip_ss3_1to2` | On a `SYNC_STAGES` = 3 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK 1.5 to 4 times faster than `i_clk` | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss3-1to2`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss3_1to20` | On a `SYNC_STAGES` = 3 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK at least 6 times faster than `i_clk` | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss3-1to20`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss3_20to1` | On a `SYNC_STAGES` = 3 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with `i_clk` at least 10 times faster than TCK | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss3-20to1`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss3_20to19` | On a `SYNC_STAGES` = 3 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with `i_clk` faster than TCK by at most 10% | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss3-20to19`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss3_20to21` | On a `SYNC_STAGES` = 3 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK faster than `i_clk` by at most 10% | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss3-20to21`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss4_1to2` | On a `SYNC_STAGES` = 4 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK 1.5 to 4 times faster than `i_clk` | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss4-1to2`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss4_1to20` | On a `SYNC_STAGES` = 4 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK at least 6 times faster than `i_clk` | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss4-1to20`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss4_20to1` | On a `SYNC_STAGES` = 4 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with `i_clk` at least 10 times faster than TCK | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss4-20to1`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss4_20to19` | On a `SYNC_STAGES` = 4 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with `i_clk` faster than TCK by at most 10% | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss4-20to19`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_round_trip_ss4_20to21` | On a `SYNC_STAGES` = 4 build, the bridge returns to IDLE from WAIT_READ_VALID or WAIT_WRITE_VALID with TCK faster than `i_clk` by at most 10% | bridge `state_q`, measured periods, build parameter | `F-TDR-result-latency` | `tdr2axi_clock_ratio_test` | `T-TDR2AXI-round-trip-ss4-20to21`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `c_tdr_scan_via_pause` | A capture and an update more than 50 TCK edges apart | capture and update strobes | `F-TDR-strobes` | `tdr2axi_tdr_strobes_test` | `CLK-TCK` |
| `c_tdr_tck_stop_bus_pending` | An AXI handshake with TCK stopped and the bridge outside IDLE | AXI channels, bridge `state_q`, TCK activity | `F-TDR-tck-stop-hold` | `tdr2axi_tdr_tck_stop_test` | `CLK-TCK` |
| `c_tdr_tck_stop_result_pending` | A response handshake on `i_clk` with TCK stopped for 100 `i_clk` cycles | DMI response observables, TCK activity | `F-TDR-tck-stop-hold` | `tdr2axi_tdr_tck_stop_test` | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr` |
| `c_tdr_tck_stop_result_pending_ss4` | On a `SYNC_STAGES` = 4 build, a response handshake on `i_clk` with TCK stopped for 100 `i_clk` cycles | DMI response observables, TCK activity, build parameter | `F-TDR-tck-stop-hold` | `tdr2axi_tdr_tck_stop_test` | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES` |
| `c_tdr_update_two_edges` | The update strobe high on two consecutive TCK rising edges | `i_dmi_tdr_update` | `F-TDR-strobes` | `tdr2axi_tdr_strobes_test` | none |
| `c_tdr_update_while_sticky` | An update with op 1 or 2 while `error_q` is busy | bridge `error_q`, update strobe | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test`, `tdr2axi_tdr_busy_capture_test`, `tdr2axi_sba_busy_test` | none |
| `c_tdr_write_in_idle` | An update with op 2 in IDLE with a clear status | update strobe, `i_dmi_tdr[1:0]`, bridge `state_q`, `error_q` | `F-TDR-op-decode` | `tdr2axi_tdr_ops_test`, `tdr2axi_smoke_test` | none |
| `tdr_latency_cg.cp_edges` | TCK edges from a DMI response handshake to `o_dmi_tdr`: SYNC_STAGES + 2, SYNC_STAGES + 3 | DMI response observables, TCK | `F-TDR-result-latency` | none | `CFG-SYNC-STAGES` |
| `tdr_op_cg.cx_op_status` | Op of each update crossed with the status of the next capture | update and capture strobes, `i_dmi_tdr[1:0]`, `o_dmi_tdr[1:0]` | `F-TDR-op-decode` | none | none |
| `tdr_ratio_cg.cp_ratio` | Ratio bin of every completed read: `i_clk` much faster, `i_clk` slightly faster, TCK slightly faster, TCK faster, TCK much faster, other | measured periods | `F-TDR-any-clock-ratio` | none | `CFG-CLK-RATIO` |
| `tdr_ratio_cg.cx_ratio_sync` | Ratio bin crossed with `SYNC_STAGES` | measured periods, build parameter | `F-TDR-any-clock-ratio` | none | `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |

```systemverilog
covergroup tdr_op_cg @(posedge i_tck iff arst_tck_n);
  cp_op:     coverpoint upd_op iff (tdr_upd) { bins nop = {0}; bins rd = {1}; bins wr = {2}; bins clr = {3}; }
  cp_status: coverpoint cap_status iff (tdr_cap) { bins ok = {0}; bins busy = {3}; }
  cx_op_status: cross cp_op, cp_status;
endgroup
```

## AXI Manager

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `axi_attr_cg.cp_cache` | AxCACHE at each AR and AW handshake on AXI4 | AXI AR and AW channels | `F-AXI-request-attributes` | none | `CFG-AXI-CACHE` |
| `axi_attr_cg.cp_prot` | AxPROT at each AR and AW handshake | AXI AR and AW channels | `F-AXI-request-attributes` | none | `CFG-AXI-PROT` |
| `axi_cfg_cg.cp_lite` | `USE_AXI_LITE` of the build at each AXI handshake | build parameter, AXI handshakes | `F-AXI-manager-protocol` | none | `CFG-USE-AXI-LITE` |
| `axi_size_cg.cx_size_lane_dir_axi4` | On AXI4 builds, access size by byte lane by direction at each handshake | AXI AR, AW and W channels, `cfg_axi_lite` | `F-AXI-axi4-size` | none | `CFG-AXI-DATA-WIDTH`, `CFG-USE-AXI-LITE` |
| `axi_size_cg.cx_size_lane_dir_lite` | On AXI4-Lite builds, access size by byte lane by direction at each handshake | AXI AR, AW and W channels, `cfg_axi_lite` | `F-AXI-lite-full-width` | none | `CFG-AXI-DATA-WIDTH`, `CFG-USE-AXI-LITE` |
| `c_axi4_narrow_read` | An AR handshake with ARSIZE below the bus width | AXI AR channel | `F-AXI-axi4-size` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | none |
| `c_axi4_narrow_write` | An AW handshake with AWSIZE below the bus width | AXI AW channel | `F-AXI-axi4-size` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | none |
| `c_axi_ar_wait` | ARVALID high with ARREADY low for at least 2 cycles, then the handshake | AXI AR channel | `F-AXI-manager-protocol` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_attr_read` | An AR handshake | AXI AR channel with ARPROT and ARCACHE | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-PROT` |
| `c_axi_attr_write` | An AW handshake | AXI AW channel with AWPROT and AWCACHE | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-PROT`, `CFG-AXI-CACHE` |
| `c_axi_aw_wait` | AWVALID high with AWREADY low for at least 2 cycles, then the handshake | AXI AW channel | `F-AXI-manager-protocol` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_b_late` | BVALID at least 8 cycles after the W handshake | AXI W and B channels | `F-AXI-one-outstanding` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_r_late` | RVALID at least 8 cycles after the AR handshake | AXI AR and R channels | `F-AXI-one-outstanding` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_ready_before_valid` | ARREADY or AWREADY high before VALID rises | AXI AR and AW channels | `F-AXI-manager-protocol` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_rsp_change_mid_cycle` | An `i_axi_rsp` READY or VALID change between `i_clk` edges while a request VALID is high | `i_axi_rsp`, `i_clk` | `F-AXI-no-comb-path` | `tdr2axi_axi_protocol_test` | `CLK-BUS` |
| `c_axi_w_before_aw` | A W handshake before the AW handshake of the same write | AXI AW and W channels | `F-AXI-manager-protocol` | `tdr2axi_axi_protocol_test` | none |
| `c_axi_w_wait` | WVALID high with WREADY low for at least 2 cycles, then the handshake | AXI W channel | `F-AXI-manager-protocol` | `tdr2axi_axi_protocol_test` | none |
| `c_axil_narrow_read` | An AR handshake on AXI4-Lite while `sbaccess` is below the bus width | AXI AR channel, `sbaccess` | `F-AXI-lite-full-width` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CFG-USE-AXI-LITE` |
| `c_axil_narrow_write` | A W handshake on AXI4-Lite with WSTRB neither all ones nor zero | AXI W channel | `F-AXI-lite-full-width` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CFG-USE-AXI-LITE` |

The bridge raises ARVALID or AWVALID no sooner than the second `i_clk` edge after an R
or B handshake, so the shortest gap from a response to the next request is two edges.

## System Bus Access

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `c_sba_access_64bit` | A handshake with `sbaccess` = 3 | `sbaccess`, AXI handshakes | `F-SBA-data-lanes` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CFG-AXI-DATA-WIDTH` |
| `c_sba_addr_above_32` | An AR or AW handshake with an address bit above 31 set | AXI AR and AW channels | `F-SBA-address-width` | `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_sba_addr_msb` | An AR or AW handshake with address bit `AXI_ADDR_WIDTH` - 1 set | AXI AR and AW channels | `F-SBA-address-width` | `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_sba_block_read` | A DMI read of `sbdata0` with `sbreadondata` set followed by an AR handshake | DMI request observables, AXI AR channel | `F-SBA-block-read` | `tdr2axi_sba_block_test`, `tdr2axi_tdr_pipelined_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_block_write` | Four AW handshakes at consecutive addresses with `sbautoincrement` set | AXI AW channel, `sbautoincrement` | `F-SBA-block-write` | `tdr2axi_sba_block_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_block_write_error_mid` | A B handshake with SLVERR or DECERR for an `sbdata0` write that follows a completed write with no `sbaddress0` write between, while `sbautoincrement` is set | AXI B channel, DMI request observables, `sbautoincrement` | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_blocked_by_sberror` | A DMI write of `sbdata0` while `sberror` is nonzero and `sbbusyerror` is clear | DMI request observables, `sbcs_sberror`, `sbcs_sbbusyerror` | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_blocked_read_on_addr` | A DMI write of `sbaddress0` with `sbreadonaddr` set while `sberror` is nonzero and `sbbusyerror` is clear | DMI request observables, `sbreadonaddr`, `sbcs_sberror`, `sbcs_sbbusyerror` | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_blocked_read_on_data` | A DMI read of `sbdata0` with `sbreadondata` set while `sberror` is nonzero and `sbbusyerror` is clear | DMI request observables, `sbreadondata`, `sbcs_sberror`, `sbcs_sbbusyerror` | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_read_error_autoinc_addr` | An R handshake with SLVERR or DECERR for a read an `sbaddress0` write started, while `sbautoincrement` is set | AXI R channel, DMI request observables, `sbautoincrement` | `F-SBA-no-increment-after-error` | `tdr2axi_sba_error_address_test` | none |
| `c_sba_read_error_autoinc_data` | An R handshake with SLVERR or DECERR for a read an `sbdata0` read started, while `sbreadondata` and `sbautoincrement` are set | AXI R channel, DMI request observables, `sbreadondata`, `sbautoincrement` | `F-SBA-no-increment-after-error` | `tdr2axi_sba_error_address_test`, `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_read_on_sbaddress0` | A DMI write of `sbaddress0` followed by an AR handshake | DMI request observables, AXI AR channel | `F-SBA-single-read` | `tdr2axi_smoke_test`, `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_sbaddress0_no_read` | A DMI write of `sbaddress0` with `sbreadonaddr` clear | DMI request observables, `sbreadonaddr` | `F-SBA-single-read` | `tdr2axi_sba_single_test` | none |
| `c_sba_sbaddress1_write_absent` | A DMI write of `sbaddress1` with every bit set on a build whose `AXI_ADDR_WIDTH` is 32 or less | DMI request observables, build parameter | `F-SBA-address-width` | `tdr2axi_dm_write_ones_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_sba_sbaddress_read_while_busy` | A DMI read of `sbaddress0` or `sbaddress1` while `sbbusy` is high, which is no collision | DMI request observables, `sbbusy` | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | none |
| `c_sba_sbbusy_held` | `sbbusy` high for at least 16 `i_clk` cycles | `sbbusy` | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | none |
| `c_sba_sbbusy_read_after_response` | A DMI read of `sbcs` the DM takes on the `i_clk` edge after the access's R or B handshake, the one cycle in which `u_dm_csrs` answers `sbbusy` = 1 from its registered copy after `sbbusy` itself has fallen. Observed behavior, not a requirement: the debug specification leaves the time `sbbusy` falls open, and `CHK-SBA-SBBUSY` allows one such read | DMI request observables, `sbbusy`, AXI R and B channels | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | `CFG-CLK-RATIO` |
| `c_sba_sbbusyerror_set` | While `sbbusy` is high, a DMI write of `sbaddress0`, `sbaddress1`, `sbdata0` or `sbdata1`, or a DMI read of `sbdata0` or `sbdata1` | DMI request observables, `sbbusy` | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | none |
| `c_sba_sbcs_active` | A DMI read of `sbcs` while `dmactive` is 1 | DMI request observables, `dmactive` | `F-SBA-sbcs-fields` | `tdr2axi_smoke_test` | none |
| `c_sba_sbcs_write_busy_same_size` | A DMI write of `sbcs` while `sbbusy` is high whose `sbaccess` equals the size of the access in flight | DMI request observables, `sbbusy`, `sbaccess` | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | none |
| `c_sba_sbcs_write_while_busy` | A DMI write of `sbcs` while `sbbusy` is high. The debug specification leaves the result undefined; the bench makes the write only with `sbbusyerror` already set, and `CHK-SBA-SBBUSYERROR` grades the DUT's answer, DMI busy with no field changed (`src/dm_csrs.sv:512-514`) | DMI request observables, `sbbusy` | `F-SBA-busy-status` | `tdr2axi_sba_busy_test` | none |
| `c_sba_sbdata0_read_no_readondata` | A DMI read of `sbdata0` with `sbreadondata` clear | DMI request observables, `sbreadondata` | `F-SBA-block-read` | `tdr2axi_sba_block_test` | none |
| `c_sba_sbdata1_no_bus` | A DMI read or write of `sbdata1` with the bus idle | DMI request observables | `F-SBA-data-lanes` | `tdr2axi_sba_single_test` | none |
| `c_sba_sberror_align` | `sberror` becomes 3 | `sberror` | `F-SBA-access-errors` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_sberror_bus_read` | `sberror` becomes 2 after an R handshake | `sberror`, AXI R channel | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_sberror_bus_write` | `sberror` becomes 2 after a B handshake | `sberror`, AXI B channel | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_sberror_size` | `sberror` becomes 4 | `sberror` | `F-SBA-access-errors` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | none |
| `c_sba_write_error_autoinc` | A B handshake with SLVERR or DECERR while `sbautoincrement` is set | AXI B channel, `sbautoincrement` | `F-SBA-no-increment-after-error` | `tdr2axi_sba_error_address_test` | none |
| `c_sba_write_on_sbdata0` | A DMI write of `sbdata0` followed by an AW handshake | DMI request observables, AXI AW channel | `F-SBA-single-write` | `tdr2axi_smoke_test`, `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | none |
| `sba_block_cg.cx_len_readondata` | Block length (1, 2 to 4, 5 to 16, above 16) by `sbreadondata` | AXI handshakes, `sbautoincrement`, `sbreadondata` | `F-SBA-block-read` | none | none |
| `sba_busy_cg.cp_cause` | Colliding access while `sbbusy` is high: `sbaddress0` write, `sbaddress1` write, `sbdata0` read, `sbdata0` write, `sbdata1` read or write | DMI request observables, `sbbusy` | `F-SBA-busy-status` | none | none |
| `sba_cfg_cg.cp_addr_width` | `AXI_ADDR_WIDTH` of the build at each SBA handshake | build parameter, AXI handshakes | `F-SBA-address-width` | none | `CFG-AXI-ADDR-WIDTH` |
| `sba_cfg_cg.cp_data_width` | `AXI_DATA_WIDTH` of the build at each SBA handshake | build parameter, AXI handshakes | `F-SBA-data-lanes` | none | `CFG-AXI-DATA-WIDTH` |
| `sba_err_cg.cx_resp_dir` | SLVERR or DECERR by read or write | AXI R and B channels | `F-SBA-bus-error` | none | none |

## Debug Module

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `c_dm_command_ones` | A DMI write of `command` with every bit set | DMI request observables | `F-DM-abstract-cmd-unsupported` | `tdr2axi_dm_write_ones_test` | none |
| `c_dm_command_write` | A DMI write of `command` | DMI request observables | `F-DM-abstract-cmd-unsupported` | `tdr2axi_dm_registers_test`, `tdr2axi_dm_write_ones_test` | none |
| `c_dm_command_zero_after_ones` | A DMI write of `command` = 0 after a write of `command` with every bit set | DMI request observables | `F-DM-abstract-cmd-unsupported` | `tdr2axi_dm_write_ones_test` | none |
| `c_dm_dmactive_fall` | A `dmcontrol` write takes `dmactive` to 0 | `dmactive` | `F-DM-dmactive-clear` | `tdr2axi_dmactive_clear_test`, `tdr2axi_dmactive_inflight_test`, `tdr2axi_dmactive_no_bus_test` | none |
| `c_dm_dmactive_fall_ar_wait` | A `dmcontrol` write takes `dmactive` to 0 while ARVALID is high and ARREADY low | `dmactive`, AXI AR channel | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | none |
| `c_dm_dmactive_fall_aw_wait` | A `dmcontrol` write takes `dmactive` to 0 while AWVALID is high and AWREADY low | `dmactive`, AXI AW channel | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | none |
| `c_dm_dmactive_fall_b_wait` | A `dmcontrol` write takes `dmactive` to 0 between a W handshake and its B handshake | `dmactive`, AXI W and B channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | none |
| `c_dm_dmactive_fall_cmderr` | A `dmcontrol` write takes `dmactive` to 0 with `cmderr` nonzero and `sbbusy` high | `dmactive`, `cmderr`, `sbbusy` | `F-DM-dmactive-clear` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_dmactive_fall_r_wait` | A `dmcontrol` write takes `dmactive` to 0 between an AR handshake and its R handshake | `dmactive`, AXI AR and R channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | none |
| `c_dm_dmactive_fall_sbbusyerror` | A `dmcontrol` write takes `dmactive` to 0 with `sbbusyerror` and `sbbusy` high | `dmactive`, `sbcs_sbbusyerror`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_dmactive_fall_w_wait` | A `dmcontrol` write takes `dmactive` to 0 while WVALID is high and WREADY low | `dmactive`, AXI W channel | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | none |
| `c_dm_dmactive_rise_b_wait` | A `dmcontrol` write takes `dmactive` to 1 between a W handshake and its B handshake | `dmactive`, AXI W and B channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_dmactive_rise_inflight` | A `dmcontrol` write takes `dmactive` to 1 while `sbbusy` is high, so the access in flight when it fell lands its result | `dmactive`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_dmactive_rise_r_wait` | A `dmcontrol` write takes `dmactive` to 1 between an AR handshake and its R handshake | `dmactive`, AXI AR and R channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_dmstatus_read` | A DMI read of `dmstatus` | DMI request observables | `F-DM-version` | `tdr2axi_smoke_test`, `tdr2axi_dm_registers_test` | none |
| `c_dm_hart_request_write` | A DMI write of `dmcontrol` with `haltreq` or `resumereq` set | DMI request observables | `F-DM-hart-unavailable` | `tdr2axi_dm_registers_test` | none |
| `c_dm_ndmreset_set` | A DMI write of `dmcontrol` with `ndmreset` set | DMI request observables | `F-DM-ndmreset-unconnected` | `tdr2axi_dm_registers_test` | none |
| `c_dm_op3_dm_written` | An update with op 3 after a DMI write of an SBA register | update strobe, DMI request observables | `F-DM-dmactive-clear` | `tdr2axi_dmactive_clear_test`, `tdr2axi_sba_busy_test` | none |
| `c_dm_op3_while_held` | An update with op 3 while `dmactive` is 0 and `sbbusy` is high | update strobe, `dmactive`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_response_while_inactive` | An R or B handshake while `dmactive` is 0 | `dmactive`, AXI R and B channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_sba_access_held` | While `dmactive` is 0 and `sbbusy` is high, a DMI write of `sbcs`, `sbaddress0`, `sbaddress1`, `sbdata0` or `sbdata1`, or a DMI read of `sbdata0` or `sbdata1` | DMI request observables, `dmactive`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_sbaddress0_write_inactive` | A DMI write of `sbaddress0` while `dmactive` is 0 | DMI request observables, `dmactive` | `F-DM-dmactive-no-bus-access` | `tdr2axi_dmactive_no_bus_test` | none |
| `c_dm_sbcs_read_held` | A DMI read of `sbcs` while `dmactive` is 0 and `sbbusy` is high | DMI request observables, `dmactive`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_dm_sbdata0_write_inactive` | A DMI write of `sbdata0` while `dmactive` is 0 | DMI request observables, `dmactive` | `F-DM-dmactive-no-bus-access` | `tdr2axi_dmactive_no_bus_test` | none |
| `c_dm_unmapped_access` | A DMI access to an address outside the register map | DMI request observables | `F-DM-register-map` | `tdr2axi_dm_registers_test` | none |
| `dm_hold_cg.cx_resp_dmactive` | R or B handshake of an access in flight when a `dmcontrol` write took `dmactive` to 0, by `dmactive` at the handshake (0, held; 1, set again), response (OKAY, SLVERR, DECERR) and `sbautoincrement` | `dmactive`, `sbbusy`, AXI R and B channels, `sbautoincrement` | `F-DM-dmactive-inflight` | none | `RST-DMACTIVE` |
| `dm_reg_cg.cx_addr_op` | DMI address by op | DMI request observables | `F-DM-register-map` | none | none |

## Reset

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `c_rst_ar_wait` | With `i_test_mode` = 0, `i_arst_n` falls with ARVALID high and ARREADY low | `i_arst_n`, `i_test_mode`, AXI AR channel | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-BUS` |
| `c_rst_b_pending` | With `i_test_mode` = 0, `i_arst_n` falls between a W handshake and its B handshake | `i_arst_n`, `i_test_mode`, AXI W and B channels | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-BUS` |
| `c_rst_bridge_busy` | With `i_test_mode` = 0, `i_arst_n` falls with the bridge outside IDLE | `i_arst_n`, `i_test_mode`, bridge `state_q` | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-TCK` |
| `c_rst_busy_after_release` | A capture reads busy before the first request after a release is accepted | capture strobe, `o_dmi_tdr[1:0]`, request CDC ready | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test`, `tdr2axi_reset_release_test` | `RDC-ARST` |
| `c_rst_cdc_clear_1to2` | The last isolate request of both CDCs falls after a release, with TCK 1.5 to 4 times faster than `i_clk` | the CDCs' clear-pending outputs, TCK-side reset, measured periods | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-cdc-clear-1to2`, `RDC-ARST`, `CFG-CLK-RATIO` |
| `c_rst_cdc_clear_1to20` | The last isolate request of both CDCs falls after a release, with TCK at least 6 times faster than `i_clk` | the CDCs' clear-pending outputs, TCK-side reset, measured periods | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-cdc-clear-1to20`, `RDC-ARST`, `CFG-CLK-RATIO` |
| `c_rst_cdc_clear_20to1` | The last isolate request of both CDCs falls after a release, with `i_clk` at least 10 times faster than TCK | the CDCs' clear-pending outputs, TCK-side reset, measured periods | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-cdc-clear-20to1`, `RDC-ARST`, `CFG-CLK-RATIO` |
| `c_rst_cdc_clear_20to19` | The last isolate request of both CDCs falls after a release, with `i_clk` faster than TCK by at most 10% | the CDCs' clear-pending outputs, TCK-side reset, measured periods | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-cdc-clear-20to19`, `RDC-ARST`, `CFG-CLK-RATIO` |
| `c_rst_cdc_clear_20to21` | The last isolate request of both CDCs falls after a release, with TCK faster than `i_clk` by at most 10% | the CDCs' clear-pending outputs, TCK-side reset, measured periods | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-cdc-clear-20to21`, `RDC-ARST`, `CFG-CLK-RATIO` |
| `c_rst_cdc_dst_wait_ack` | With `i_test_mode` = 0, `i_arst_n` falls while the `cdc_4phase_dst` receiver of a reset-controller half of either CDC is in `WAIT_DOWNSTREAM_ACK` | `i_arst_n`, `i_test_mode`, the clear-sequence receivers' states | `F-RESET-async-any-time` | `tdr2axi_reset_during_clear_test` | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST` |
| `c_rst_cdc_req_inflight` | With `i_test_mode` = 0, `i_arst_n` falls with a request in the request CDC | `i_arst_n`, `i_test_mode`, request CDC valid and ready | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-TCK`, `CDC-REQ` |
| `c_rst_dmactive_alone` | A `dmcontrol` write takes `dmactive` to 1 after another took it to 0 with the bus idle, with no SBA register access, no AXI request and no reset of the DM between the two writes | `dmactive`, `sbbusy`, `arst_clk_n`, DMI request observables, AXI channels | `F-DM-dmactive-clear` | `tdr2axi_dmactive_reset_test` | `RST-DMACTIVE` |
| `c_rst_dmactive_held` | With `i_test_mode` = 0, `i_arst_n` falls while `dmactive` is 0 and `sbbusy` is high | `i_arst_n`, `i_test_mode`, `dmactive`, `sbbusy` | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-ARST-BUS`, `RST-DMACTIVE` |
| `c_rst_dmactive_idle` | A `dmcontrol` write takes `dmactive` to 0 with no AXI request valid, no response owed and `sbbusy` low | `dmactive`, `sbbusy`, AXI channels | `F-DM-dmactive-clear` | `tdr2axi_dmactive_reset_test`, `tdr2axi_dmactive_clear_test` | `RST-DMACTIVE` |
| `c_rst_dmactive_sba_access` | A `dmcontrol` write takes `dmactive` to 0 with `sbbusy` high, an AXI request valid or a response owed | `dmactive`, `sbbusy`, AXI channels | `F-DM-dmactive-inflight` | `tdr2axi_dmactive_inflight_test` | `RST-DMACTIVE` |
| `c_rst_during_cdc_clear` | With `i_test_mode` = 0, `i_arst_n` falls while a CDC's clear sequence is pending | `i_arst_n`, `i_test_mode`, the CDCs' clear-pending outputs | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RDC-ARST` |
| `c_rst_during_live_cdc_clear` | With `i_test_mode` = 0, `i_arst_n` falls while a CDC's clear sequence is pending and both internal resets are high, so neither side holds the sequence in reset | `i_arst_n`, `i_test_mode`, the CDCs' clear-pending outputs, both internal resets | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RDC-ARST`, `RST-ARST-BUS`, `RST-ARST-TCK` |
| `c_rst_idle` | With `i_test_mode` = 0, `i_arst_n` falls with the bridge in IDLE and no AXI VALID high | `i_arst_n`, `i_test_mode`, bridge `state_q`, AXI channels | `F-RESET-async-any-time` | `tdr2axi_reset_release_test`, `tdr2axi_reset_traffic_test` | `RST-ARST-BUS`, `RST-ARST-TCK` |
| `c_rst_r_pending` | With `i_test_mode` = 0, `i_arst_n` falls between an AR handshake and its R handshake | `i_arst_n`, `i_test_mode`, AXI AR and R channels | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-BUS` |
| `c_rst_release_clk_first` | The `i_clk`-side reset releases before the TCK-side reset | both internal resets | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `RST-ARST-BUS`, `RST-ARST-TCK` |
| `c_rst_release_tck_first` | The TCK-side reset releases before the `i_clk`-side reset | both internal resets | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `RST-ARST-BUS`, `RST-ARST-TCK` |
| `c_rst_release_tck_stopped` | `i_arst_n` rises with TCK stopped, and the TCK-side reset releases after TCK resumes | `i_arst_n`, TCK-side reset, TCK activity | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `RST-ARST-TCK` |
| `c_rst_release_test_mode_off` | The TCK-side reset releases with `i_test_mode` = 0, through the synchronizer | TCK-side reset, `i_test_mode` | `F-RESET-test-mode-bypass` | `tdr2axi_reset_test_mode_test` | `CFG-TEST-MODE`, `RST-ARST-TCK` |
| `c_rst_scan_reset` | `i_arst_scan_n` falls and rises with `i_test_mode` = 1 | `i_test_mode`, `i_arst_scan_n` | `F-RESET-test-mode-bypass` | `tdr2axi_reset_test_mode_test` | `CFG-TEST-MODE`, `RST-SCAN` |
| `c_rst_tck_release_ss3` | The TCK-side reset releases on a `SYNC_STAGES` = 3 build | TCK-side reset, build parameter | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-tck-reset-release`, `RST-ARST-TCK`, `CFG-SYNC-STAGES` |
| `c_rst_tck_release_ss4` | The TCK-side reset releases on a `SYNC_STAGES` = 4 build | TCK-side reset, build parameter | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | `T-TDR2AXI-tck-reset-release-ss4`, `RST-ARST-TCK`, `CFG-SYNC-STAGES` |
| `c_rst_test_mode_access` | An AXI handshake while `i_test_mode` = 1 | `i_test_mode`, AXI handshakes | `F-RESET-test-mode-bypass` | `tdr2axi_reset_test_mode_test` | `CFG-TEST-MODE` |
| `c_rst_trst_op_in_flight` | Both TDR strobes low for a stop of at least 100 `i_clk` cycles with the bridge outside IDLE, followed by a capture with status 0 | TCK activity, strobes, bridge `state_q` | `F-RESET-trst-independent` | `tdr2axi_trst_test` | `RST-TRST` |
| `c_rst_w_alone` | With `i_test_mode` = 0, `i_arst_n` falls with WVALID high, WREADY low and AWVALID low, after the write's AW handshake | `i_arst_n`, `i_test_mode`, AXI AW and W channels | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-BUS` |
| `c_rst_w_wait` | With `i_test_mode` = 0, `i_arst_n` falls with AWVALID or WVALID high and its READY low | `i_arst_n`, `i_test_mode`, AXI AW and W channels | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | `RST-ARST-BUS` |
| `rst_cfg_cg.cp_sync_stages` | `SYNC_STAGES` of the build at each reset release | build parameter, internal resets | `F-RESET-sync-release` | none | `CFG-SYNC-STAGES` |

## Ports and Build

| Point | Event | Feed | Feature | Items | Model entries |
| --- | --- | --- | --- | --- | --- |
| `c_cfg_addr_width_2` | An AR or AW handshake on a build with `AXI_ADDR_WIDTH` = 2 | build parameter, AXI handshakes | `F-SBA-address-width` | `tdr2axi_smoke_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_cfg_addr_width_3` | An AR or AW handshake on a build with `AXI_ADDR_WIDTH` = 3 | build parameter, AXI handshakes | `F-SBA-address-width` | `tdr2axi_smoke_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_cfg_addr_width_32` | An AR or AW handshake on a build with `AXI_ADDR_WIDTH` = 32 | build parameter, AXI handshakes | `F-SBA-address-width` | `tdr2axi_smoke_test`, `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_cfg_addr_width_48` | An AR or AW handshake on a build with `AXI_ADDR_WIDTH` = 48 | build parameter, AXI handshakes | `F-SBA-address-width` | `tdr2axi_smoke_test`, `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_cfg_addr_width_64` | An AR or AW handshake on a build with `AXI_ADDR_WIDTH` = 64 | build parameter, AXI handshakes | `F-SBA-address-width` | `tdr2axi_smoke_test`, `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH` |
| `c_cfg_axi4` | An AR or AW handshake on an AXI4 build | build parameter, AXI handshakes | `F-AXI-manager-protocol` | `tdr2axi_smoke_test`, `tdr2axi_axi_protocol_test` | `CFG-USE-AXI-LITE` |
| `c_cfg_axi4_lite` | An AR or AW handshake on an AXI4-Lite build | build parameter, AXI handshakes | `F-AXI-manager-protocol` | `tdr2axi_smoke_test`, `tdr2axi_axi_protocol_test` | `CFG-USE-AXI-LITE` |
| `c_cfg_cache_0000` | An AR or AW handshake on AXI4 with AxCACHE = 4'b0000 | AXI AR and AW channels | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` |
| `c_cfg_cache_1111` | An AR or AW handshake on AXI4 with AxCACHE = 4'b1111 | AXI AR and AW channels | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` |
| `c_cfg_data_width_32` | An AR or AW handshake on a build with `AXI_DATA_WIDTH` = 32 | build parameter, AXI handshakes | `F-SBA-data-lanes` | `tdr2axi_smoke_test`, `tdr2axi_sba_single_test` | `CFG-AXI-DATA-WIDTH` |
| `c_cfg_data_width_64` | An AR or AW handshake on a build with `AXI_DATA_WIDTH` = 64 | build parameter, AXI handshakes | `F-SBA-data-lanes` | `tdr2axi_smoke_test`, `tdr2axi_sba_single_test` | `CFG-AXI-DATA-WIDTH` |
| `c_cfg_first_access` | The first AR or AW handshake after a `dmcontrol` write takes `dmactive` to 1 | `dmactive`, AXI handshakes | `F-CFG-bender-target` | `tdr2axi_smoke_test` | none |
| `c_cfg_min_addr_width` | An AXI handshake on a build whose `AXI_ADDR_WIDTH` is the minimum for its data width | build parameters, AXI handshakes | `F-CFG-param-checks` | `tdr2axi_axi_attributes_test`, `tdr2axi_sba_address_width_test` | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `c_cfg_min_addr_width_sbcs` | A DMI read of `sbcs` while `dmactive` is 1 on a build whose `AXI_ADDR_WIDTH` is the minimum for its data width | build parameters, DMI request observables, `dmactive` | `F-CFG-param-checks` | `tdr2axi_param_checks_test` | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `c_cfg_prot_010` | An AR or AW handshake with AxPROT = 3'b010 | AXI AR and AW channels | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-PROT` |
| `c_cfg_prot_101` | An AR or AW handshake with AxPROT = 3'b101 | AXI AR and AW channels | `F-AXI-request-attributes` | `tdr2axi_axi_attributes_test` | `CFG-AXI-PROT` |
| `c_port_axi_read_write` | An AR handshake and an AW handshake in one test | AXI AR and AW channels | `F-PORT-bus-and-reset` | `tdr2axi_smoke_test` | `CFG-USE-AXI-LITE` |
| `c_port_tdr_scan` | A capture and an update in one scan, each on its own TCK edge | TDR strobes | `F-PORT-tdr-interface` | `tdr2axi_smoke_test` | none |

## Clock, Reset and Configuration Entries

Each functional clock and reset, and each configuration dimension with more than one
value, is measured by the points below. A build dimension is hit by the build targets
that build each value, and each value has a cover property of its own, so a value no
run reached is a hole in the per-run report under either simulator: a `c_cfg_` point
for the address width, data width, bus type, AxPROT and AxCACHE values, and
`c_rst_tck_release_ss3` and `c_rst_tck_release_ss4` for the two `SYNC_STAGES` values.
`CFG-CLK-RATIO` and `CFG-TEST-MODE` are hit by the items that sweep them at run time.
The five named ratios fall in separate bands of the measured periods: 20:1 in
`c_tdr_ratio_clk_much_faster`, 20:19 in `c_tdr_ratio_clk_slightly_faster`, 20:21 in
`c_tdr_ratio_tck_slightly_faster`, 1:2 in `c_tdr_ratio_tck_faster` and 1:20 in
`c_tdr_ratio_tck_much_faster`; the round-trip and CDC clear points use the same bands.
Under `--ratio random` the `i_clk` period is drawn log-uniform from 1000 to 160000 ps
against TCK at 20000 ps, and about 58% of the draws fall between the bands: `i_clk`
1.1 to 10 times faster than TCK, 1.1 to 1.5 times slower, or 4 to 6 times slower. Only
those draws reach the `other` bin of `tdr_ratio_cg.cp_ratio` and its two
`cx_ratio_sync` crosses. `tdr2axi_clock_ratio_test` is expected to hit them in its
random-ratio runs on every build, at both `SYNC_STAGES` values, and every other item
with `CFG-CLK-RATIO` in its model adds samples when the signoff spread draws a random
ratio. Every bin and cross of `tdr_ratio_cg` is reachable, so none is ignored.
`CFG-TEST-MODE` = 0 is `c_rst_release_test_mode_off` and 1 is `c_rst_test_mode_access`.
`RST-DMACTIVE` is cleared while idle (`c_rst_dmactive_idle`), during an SBA access
(`c_rst_dmactive_sba_access`) and alone (`c_rst_dmactive_alone`), and during an access
with `cmderr` set (`c_dm_dmactive_fall_cmderr`) or `sbbusyerror` set
(`c_dm_dmactive_fall_sbbusyerror`). While an access in flight holds the SBA registers with
`dmactive` at 0, `sbcs` is read (`c_dm_sbcs_read_held`), another SBA register is accessed
(`c_dm_sba_access_held`), op 3 is scanned (`c_dm_op3_while_held`), the response arrives
(`c_dm_response_while_inactive`), `dmactive` is set again (`c_dm_dmactive_rise_inflight`),
also while the access waits for its R or B response (`c_dm_dmactive_rise_r_wait`,
`c_dm_dmactive_rise_b_wait`), and `i_arst_n` falls with `i_test_mode` = 0
(`c_rst_dmactive_held`). `dm_hold_cg.cx_resp_dmactive` crosses that response with
`dmactive`, the response type and `sbautoincrement`.

| Point | Entries | Feature |
| --- | --- | --- |
| `axi_attr_cg.cp_cache` | `CFG-AXI-CACHE` | `F-AXI-request-attributes` |
| `axi_attr_cg.cp_prot` | `CFG-AXI-PROT` | `F-AXI-request-attributes` |
| `axi_cfg_cg.cp_lite` | `CFG-USE-AXI-LITE` | `F-AXI-manager-protocol` |
| `axi_size_cg.cx_size_lane_dir_axi4` | `CFG-AXI-DATA-WIDTH`, `CFG-USE-AXI-LITE` | `F-AXI-axi4-size` |
| `axi_size_cg.cx_size_lane_dir_lite` | `CFG-AXI-DATA-WIDTH`, `CFG-USE-AXI-LITE` | `F-AXI-lite-full-width` |
| `c_axi_attr_read` | `CFG-AXI-PROT` | `F-AXI-request-attributes` |
| `c_axi_attr_write` | `CFG-AXI-PROT`, `CFG-AXI-CACHE` | `F-AXI-request-attributes` |
| `c_axi_rsp_change_mid_cycle` | `CLK-BUS` | `F-AXI-no-comb-path` |
| `c_axil_narrow_read` | `CFG-USE-AXI-LITE` | `F-AXI-lite-full-width` |
| `c_axil_narrow_write` | `CFG-USE-AXI-LITE` | `F-AXI-lite-full-width` |
| `c_cfg_addr_width_2` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_cfg_addr_width_3` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_cfg_addr_width_32` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_cfg_addr_width_48` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_cfg_addr_width_64` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_cfg_axi4` | `CFG-USE-AXI-LITE` | `F-AXI-manager-protocol` |
| `c_cfg_axi4_lite` | `CFG-USE-AXI-LITE` | `F-AXI-manager-protocol` |
| `c_cfg_cache_0000` | `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` | `F-AXI-request-attributes` |
| `c_cfg_cache_1111` | `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` | `F-AXI-request-attributes` |
| `c_cfg_data_width_32` | `CFG-AXI-DATA-WIDTH` | `F-SBA-data-lanes` |
| `c_cfg_data_width_64` | `CFG-AXI-DATA-WIDTH` | `F-SBA-data-lanes` |
| `c_cfg_min_addr_width` | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` | `F-CFG-param-checks` |
| `c_cfg_min_addr_width_sbcs` | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` | `F-CFG-param-checks` |
| `c_cfg_prot_010` | `CFG-AXI-PROT` | `F-AXI-request-attributes` |
| `c_cfg_prot_101` | `CFG-AXI-PROT` | `F-AXI-request-attributes` |
| `c_dm_dmactive_fall_cmderr` | `RST-DMACTIVE` | `F-DM-dmactive-clear` |
| `c_dm_dmactive_fall_sbbusyerror` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_dmactive_rise_b_wait` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_dmactive_rise_inflight` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_dmactive_rise_r_wait` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_op3_while_held` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_response_while_inactive` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_sba_access_held` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_dm_sbcs_read_held` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_port_axi_read_write` | `CFG-USE-AXI-LITE` | `F-PORT-bus-and-reset` |
| `c_rst_ar_wait` | `RST-ARST-BUS` | `F-RESET-async-any-time` |
| `c_rst_b_pending` | `RST-ARST-BUS` | `F-RESET-async-any-time` |
| `c_rst_bridge_busy` | `RST-ARST-TCK` | `F-RESET-async-any-time` |
| `c_rst_busy_after_release` | `RDC-ARST` | `F-RESET-async-any-time` |
| `c_rst_cdc_clear_1to2` | `T-TDR2AXI-cdc-clear-1to2`, `RDC-ARST`, `CFG-CLK-RATIO` | `F-RESET-sync-release` |
| `c_rst_cdc_clear_1to20` | `T-TDR2AXI-cdc-clear-1to20`, `RDC-ARST`, `CFG-CLK-RATIO` | `F-RESET-sync-release` |
| `c_rst_cdc_clear_20to1` | `T-TDR2AXI-cdc-clear-20to1`, `RDC-ARST`, `CFG-CLK-RATIO` | `F-RESET-sync-release` |
| `c_rst_cdc_clear_20to19` | `T-TDR2AXI-cdc-clear-20to19`, `RDC-ARST`, `CFG-CLK-RATIO` | `F-RESET-sync-release` |
| `c_rst_cdc_clear_20to21` | `T-TDR2AXI-cdc-clear-20to21`, `RDC-ARST`, `CFG-CLK-RATIO` | `F-RESET-sync-release` |
| `c_rst_cdc_dst_wait_ack` | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST` | `F-RESET-async-any-time` |
| `c_rst_cdc_req_inflight` | `RST-ARST-TCK`, `CDC-REQ` | `F-RESET-async-any-time` |
| `c_rst_dmactive_alone` | `RST-DMACTIVE` | `F-DM-dmactive-clear` |
| `c_rst_dmactive_held` | `RST-ARST-BUS`, `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_rst_dmactive_idle` | `RST-DMACTIVE` | `F-DM-dmactive-clear` |
| `c_rst_dmactive_sba_access` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `c_rst_during_cdc_clear` | `RDC-ARST` | `F-RESET-async-any-time` |
| `c_rst_during_live_cdc_clear` | `RDC-ARST`, `RST-ARST-BUS`, `RST-ARST-TCK` | `F-RESET-async-any-time` |
| `c_rst_idle` | `RST-ARST-BUS`, `RST-ARST-TCK` | `F-RESET-async-any-time` |
| `c_rst_r_pending` | `RST-ARST-BUS` | `F-RESET-async-any-time` |
| `c_rst_release_clk_first` | `RST-ARST-BUS`, `RST-ARST-TCK` | `F-RESET-sync-release` |
| `c_rst_release_tck_first` | `RST-ARST-BUS`, `RST-ARST-TCK` | `F-RESET-sync-release` |
| `c_rst_release_tck_stopped` | `RST-ARST-TCK` | `F-RESET-sync-release` |
| `c_rst_release_test_mode_off` | `CFG-TEST-MODE`, `RST-ARST-TCK` | `F-RESET-test-mode-bypass` |
| `c_rst_scan_reset` | `CFG-TEST-MODE`, `RST-SCAN` | `F-RESET-test-mode-bypass` |
| `c_rst_tck_release_ss3` | `T-TDR2AXI-tck-reset-release`, `RST-ARST-TCK`, `CFG-SYNC-STAGES` | `F-RESET-sync-release` |
| `c_rst_tck_release_ss4` | `T-TDR2AXI-tck-reset-release-ss4`, `RST-ARST-TCK`, `CFG-SYNC-STAGES` | `F-RESET-sync-release` |
| `c_rst_test_mode_access` | `CFG-TEST-MODE` | `F-RESET-test-mode-bypass` |
| `c_rst_trst_op_in_flight` | `RST-TRST` | `F-RESET-trst-independent` |
| `c_rst_w_alone` | `RST-ARST-BUS` | `F-RESET-async-any-time` |
| `c_rst_w_wait` | `RST-ARST-BUS` | `F-RESET-async-any-time` |
| `c_sba_access_64bit` | `CFG-AXI-DATA-WIDTH` | `F-SBA-data-lanes` |
| `c_sba_addr_above_32` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_sba_addr_msb` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_sba_sbaddress1_write_absent` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `c_sba_sbbusy_read_after_response` | `CFG-CLK-RATIO` | `F-SBA-busy-status` |
| `c_tdr_capture_at_bound` | `T-TDR2AXI-resp-to-tdr` | `F-TDR-result-latency` |
| `c_tdr_capture_at_bound_ss4` | `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_clk_stopped_op_pending` | `CLK-BUS` | `F-TDR-sticky-busy` |
| `c_tdr_ratio_clk_much_faster` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_ratio_clk_slightly_faster` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_ratio_near_one` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_ratio_tck_faster` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_ratio_tck_much_faster` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_ratio_tck_slightly_faster` | `CFG-CLK-RATIO`, `CLK-BUS`, `CLK-TCK` | `F-TDR-any-clock-ratio` |
| `c_tdr_round_trip_ss3_1to2` | `T-TDR2AXI-round-trip-ss3-1to2`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss3_1to20` | `T-TDR2AXI-round-trip-ss3-1to20`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss3_20to1` | `T-TDR2AXI-round-trip-ss3-20to1`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss3_20to19` | `T-TDR2AXI-round-trip-ss3-20to19`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss3_20to21` | `T-TDR2AXI-round-trip-ss3-20to21`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss4_1to2` | `T-TDR2AXI-round-trip-ss4-1to2`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss4_1to20` | `T-TDR2AXI-round-trip-ss4-1to20`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss4_20to1` | `T-TDR2AXI-round-trip-ss4-20to1`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss4_20to19` | `T-TDR2AXI-round-trip-ss4-20to19`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_round_trip_ss4_20to21` | `T-TDR2AXI-round-trip-ss4-20to21`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `c_tdr_scan_via_pause` | `CLK-TCK` | `F-TDR-strobes` |
| `c_tdr_tck_stop_bus_pending` | `CLK-TCK` | `F-TDR-tck-stop-hold` |
| `c_tdr_tck_stop_result_pending` | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr` | `F-TDR-tck-stop-hold` |
| `c_tdr_tck_stop_result_pending_ss4` | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES` | `F-TDR-tck-stop-hold` |
| `dm_hold_cg.cx_resp_dmactive` | `RST-DMACTIVE` | `F-DM-dmactive-inflight` |
| `rst_cfg_cg.cp_sync_stages` | `CFG-SYNC-STAGES` | `F-RESET-sync-release` |
| `sba_cfg_cg.cp_addr_width` | `CFG-AXI-ADDR-WIDTH` | `F-SBA-address-width` |
| `sba_cfg_cg.cp_data_width` | `CFG-AXI-DATA-WIDTH` | `F-SBA-data-lanes` |
| `tdr_latency_cg.cp_edges` | `CFG-SYNC-STAGES` | `F-TDR-result-latency` |
| `tdr_ratio_cg.cp_ratio` | `CFG-CLK-RATIO` | `F-TDR-any-clock-ratio` |
| `tdr_ratio_cg.cx_ratio_sync` | `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` | `F-TDR-any-clock-ratio` |

## Assertion Coverage

| Property | Source | What it asserts |
| --- | --- | --- |
| `CHK-AXI-AXI4-SIZE-SVA` | `sva/tdr2axi_axi_mgr_sva.sv` | On AXI4, while ARVALID or AWVALID is high, AxSIZE equals sbcs.sbaccess of the access, whatever AXI_DATA_WIDTH is |
| `CHK-AXI-HANDSHAKE-SVA` | `sva/tdr2axi_axi_mgr_sva.sv` | On o_axi_req, AR, AW and W VALID stay high until READY, every request field holds while VALID is high and READY low, R and B complete only with RREADY and BREADY, and no VALID is high in reset, in the AXI4 or AXI4-Lite form USE_AXI_LITE selects |
| `CHK-AXI-NO-COMB-PATH-SVA` | `formal/sby/` | In a model where i_axi_rsp may change on any global tick, o_axi_req is stable on every tick that is not an i_clk rising edge |
| `CHK-AXI-ONE-OUTSTANDING-SVA` | `sva/tdr2axi_axi_mgr_sva.sv` | No AR or AW handshake while a read or write response is owed, AxLEN 0 and WLAST high on every W beat |
| `CHK-AXI-REQ-ATTRS-SVA` | `sva/tdr2axi_axi_mgr_sva.sv` | While ARVALID or AWVALID is high, AxPROT equals AXI_PROT, AxCACHE equals AXI_CACHE on AXI4, and AxID, AxUSER and WUSER are 0 |
| `CHK-DM-DMACTIVE-INFLIGHT-SVA` | `formal/sby/` | With DMI writes of dmcontrol free, every o_axi_req request field holds while its VALID is high and READY low |
| `CHK-DM-DMACTIVE-NO-BUS-SVA` | `formal/sby/` | No ARVALID or AWVALID rises while dmactive is 0 |
| `CHK-RESET-SYNC-RELEASE-SVA` | `formal/sby/` | Both internal resets fall in the step i_arst_n falls; each rises SYNC_STAGES to SYNC_STAGES + 1 rising edges of its own clock after i_arst_n rises |
| `CHK-RESET-TEST-MODE-SVA` | `formal/sby/` | While i_test_mode is 1 both internal resets equal i_arst_scan_n |
| `CHK-TDR-DM-RESP-CODES-SVA` | `formal/sby/` | The Debug Module answers every DMI request with success or busy, never the failed or reserved code, so no failed status can reach o_dmi_tdr |
| `CHK-TDR-OP-DECODE-SVA` | `formal/sby/` | In the bridge, an update edge in IDLE with the status clear offers a request carrying the address, data and op of i_dmi_tdr for op 1 and op 2; op 0 and op 3 never offer one |
| `CHK-TDR-STATUS-SVA` | `formal/sby/` | The status turns busy when an update arrives while an operation is pending, when a capture arrives while a read's result is pending, or when the Debug Module answers busy; it stays busy until an update with op 3, and a capture reads status 0 only when the held status is clear and the capture itself raises no busy |

## Traceability

| Feature | Cover points | Items expected to hit them |
| --- | --- | --- |
| `F-AXI-axi4-size` | `axi_size_cg.cx_size_lane_dir_axi4`, `c_axi4_narrow_read`, `c_axi4_narrow_write` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` |
| `F-AXI-lite-full-width` | `axi_size_cg.cx_size_lane_dir_lite`, `c_axil_narrow_read`, `c_axil_narrow_write` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` |
| `F-AXI-manager-protocol` | `axi_cfg_cg.cp_lite`, `c_axi_ar_wait`, `c_axi_aw_wait`, `c_axi_ready_before_valid`, `c_axi_w_before_aw`, `c_axi_w_wait`, `c_cfg_axi4`, `c_cfg_axi4_lite` | `tdr2axi_axi_protocol_test`, `tdr2axi_smoke_test` |
| `F-AXI-no-comb-path` | `c_axi_rsp_change_mid_cycle` | `tdr2axi_axi_protocol_test` |
| `F-AXI-one-outstanding` | `c_axi_b_late`, `c_axi_r_late` | `tdr2axi_axi_protocol_test` |
| `F-AXI-request-attributes` | `axi_attr_cg.cp_cache`, `axi_attr_cg.cp_prot`, `c_axi_attr_read`, `c_axi_attr_write`, `c_cfg_cache_0000`, `c_cfg_cache_1111`, `c_cfg_prot_010`, `c_cfg_prot_101` | `tdr2axi_axi_attributes_test` |
| `F-CFG-bender-target` | `c_cfg_first_access` | `tdr2axi_smoke_test` |
| `F-CFG-param-checks` | `c_cfg_min_addr_width`, `c_cfg_min_addr_width_sbcs` | `tdr2axi_axi_attributes_test`, `tdr2axi_param_checks_test`, `tdr2axi_sba_address_width_test` |
| `F-DM-abstract-cmd-unsupported` | `c_dm_command_ones`, `c_dm_command_write`, `c_dm_command_zero_after_ones` | `tdr2axi_dm_registers_test`, `tdr2axi_dm_write_ones_test` |
| `F-DM-dmactive-clear` | `c_dm_dmactive_fall`, `c_dm_dmactive_fall_cmderr`, `c_dm_op3_dm_written`, `c_rst_dmactive_alone`, `c_rst_dmactive_idle` | `tdr2axi_dmactive_clear_test`, `tdr2axi_dmactive_inflight_test`, `tdr2axi_dmactive_no_bus_test`, `tdr2axi_dmactive_reset_test`, `tdr2axi_sba_busy_test` |
| `F-DM-dmactive-inflight` | `c_dm_dmactive_fall_ar_wait`, `c_dm_dmactive_fall_aw_wait`, `c_dm_dmactive_fall_b_wait`, `c_dm_dmactive_fall_r_wait`, `c_dm_dmactive_fall_sbbusyerror`, `c_dm_dmactive_fall_w_wait`, `c_dm_dmactive_rise_b_wait`, `c_dm_dmactive_rise_inflight`, `c_dm_dmactive_rise_r_wait`, `c_dm_op3_while_held`, `c_dm_response_while_inactive`, `c_dm_sba_access_held`, `c_dm_sbcs_read_held`, `c_rst_dmactive_held`, `c_rst_dmactive_sba_access`, `dm_hold_cg.cx_resp_dmactive` | `tdr2axi_dmactive_inflight_test` |
| `F-DM-dmactive-no-bus-access` | `c_dm_sbaddress0_write_inactive`, `c_dm_sbdata0_write_inactive` | `tdr2axi_dmactive_no_bus_test` |
| `F-DM-hart-unavailable` | `c_dm_hart_request_write` | `tdr2axi_dm_registers_test` |
| `F-DM-ndmreset-unconnected` | `c_dm_ndmreset_set` | `tdr2axi_dm_registers_test` |
| `F-DM-register-map` | `c_dm_unmapped_access`, `dm_reg_cg.cx_addr_op` | `tdr2axi_dm_registers_test` |
| `F-DM-version` | `c_dm_dmstatus_read` | `tdr2axi_dm_registers_test`, `tdr2axi_smoke_test` |
| `F-PORT-bus-and-reset` | `c_port_axi_read_write` | `tdr2axi_smoke_test` |
| `F-PORT-tdr-interface` | `c_port_tdr_scan` | `tdr2axi_smoke_test` |
| `F-RESET-async-any-time` | `c_rst_ar_wait`, `c_rst_b_pending`, `c_rst_bridge_busy`, `c_rst_busy_after_release`, `c_rst_cdc_dst_wait_ack`, `c_rst_cdc_req_inflight`, `c_rst_during_cdc_clear`, `c_rst_during_live_cdc_clear`, `c_rst_idle`, `c_rst_r_pending`, `c_rst_w_alone`, `c_rst_w_wait` | `tdr2axi_reset_during_clear_test`, `tdr2axi_reset_release_test`, `tdr2axi_reset_traffic_test` |
| `F-RESET-sync-release` | `c_rst_cdc_clear_1to2`, `c_rst_cdc_clear_1to20`, `c_rst_cdc_clear_20to1`, `c_rst_cdc_clear_20to19`, `c_rst_cdc_clear_20to21`, `c_rst_release_clk_first`, `c_rst_release_tck_first`, `c_rst_release_tck_stopped`, `c_rst_tck_release_ss3`, `c_rst_tck_release_ss4`, `rst_cfg_cg.cp_sync_stages` | `tdr2axi_reset_release_test` |
| `F-RESET-test-mode-bypass` | `c_rst_release_test_mode_off`, `c_rst_scan_reset`, `c_rst_test_mode_access` | `tdr2axi_reset_test_mode_test` |
| `F-RESET-trst-independent` | `c_rst_trst_op_in_flight` | `tdr2axi_trst_test` |
| `F-SBA-access-errors` | `c_sba_sberror_align`, `c_sba_sberror_size` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` |
| `F-SBA-address-width` | `c_cfg_addr_width_2`, `c_cfg_addr_width_3`, `c_cfg_addr_width_32`, `c_cfg_addr_width_48`, `c_cfg_addr_width_64`, `c_sba_addr_above_32`, `c_sba_addr_msb`, `c_sba_sbaddress1_write_absent`, `sba_cfg_cg.cp_addr_width` | `tdr2axi_dm_write_ones_test`, `tdr2axi_sba_address_width_test`, `tdr2axi_smoke_test` |
| `F-SBA-block-read` | `c_sba_block_read`, `c_sba_sbdata0_read_no_readondata`, `sba_block_cg.cx_len_readondata` | `tdr2axi_sba_block_test`, `tdr2axi_sba_stress_test`, `tdr2axi_tdr_pipelined_test` |
| `F-SBA-block-write` | `c_sba_block_write` | `tdr2axi_sba_block_test`, `tdr2axi_sba_stress_test` |
| `F-SBA-bus-error` | `c_sba_block_write_error_mid`, `c_sba_blocked_by_sberror`, `c_sba_blocked_read_on_addr`, `c_sba_blocked_read_on_data`, `c_sba_sberror_bus_read`, `c_sba_sberror_bus_write`, `sba_err_cg.cx_resp_dir` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` |
| `F-SBA-busy-status` | `c_sba_sbaddress_read_while_busy`, `c_sba_sbbusy_held`, `c_sba_sbbusy_read_after_response`, `c_sba_sbbusyerror_set`, `c_sba_sbcs_write_busy_same_size`, `c_sba_sbcs_write_while_busy`, `sba_busy_cg.cp_cause` | `tdr2axi_sba_busy_test` |
| `F-SBA-data-lanes` | `c_cfg_data_width_32`, `c_cfg_data_width_64`, `c_sba_access_64bit`, `c_sba_sbdata1_no_bus`, `sba_cfg_cg.cp_data_width` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test`, `tdr2axi_smoke_test` |
| `F-SBA-no-increment-after-error` | `c_sba_read_error_autoinc_addr`, `c_sba_read_error_autoinc_data`, `c_sba_write_error_autoinc` | `tdr2axi_sba_error_address_test`, `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` |
| `F-SBA-sbcs-fields` | `c_sba_sbcs_active` | `tdr2axi_smoke_test` |
| `F-SBA-single-read` | `c_sba_read_on_sbaddress0`, `c_sba_sbaddress0_no_read` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test`, `tdr2axi_smoke_test` |
| `F-SBA-single-write` | `c_sba_write_on_sbdata0` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test`, `tdr2axi_smoke_test` |
| `F-TDR-any-clock-ratio` | `c_tdr_ratio_clk_much_faster`, `c_tdr_ratio_clk_slightly_faster`, `c_tdr_ratio_near_one`, `c_tdr_ratio_tck_faster`, `c_tdr_ratio_tck_much_faster`, `c_tdr_ratio_tck_slightly_faster`, `tdr_ratio_cg.cp_ratio`, `tdr_ratio_cg.cx_ratio_sync` | `tdr2axi_clock_ratio_test` |
| `F-TDR-busy-recovery` | `c_tdr_read_resent_twice`, `c_tdr_resend_completes` | `tdr2axi_sba_stress_test`, `tdr2axi_tdr_busy_recovery_test` |
| `F-TDR-capture-image` | `c_tdr_capture_after_nop`, `c_tdr_capture_next_op`, `c_tdr_capture_read_data`, `c_tdr_capture_write_data` | `tdr2axi_tdr_ops_test`, `tdr2axi_tdr_pipelined_test` |
| `F-TDR-op-decode` | `c_tdr_nop_in_idle`, `c_tdr_op3_in_idle`, `c_tdr_read_in_idle`, `c_tdr_write_in_idle`, `tdr_op_cg.cx_op_status` | `tdr2axi_smoke_test`, `tdr2axi_tdr_ops_test` |
| `F-TDR-result-latency` | `c_tdr_capture_at_bound`, `c_tdr_capture_at_bound_ss4`, `c_tdr_round_trip_ss3_1to2`, `c_tdr_round_trip_ss3_1to20`, `c_tdr_round_trip_ss3_20to1`, `c_tdr_round_trip_ss3_20to19`, `c_tdr_round_trip_ss3_20to21`, `c_tdr_round_trip_ss4_1to2`, `c_tdr_round_trip_ss4_1to20`, `c_tdr_round_trip_ss4_20to1`, `c_tdr_round_trip_ss4_20to19`, `c_tdr_round_trip_ss4_20to21`, `tdr_latency_cg.cp_edges` | `tdr2axi_clock_ratio_test`, `tdr2axi_tdr_result_latency_test` |
| `F-TDR-sticky-busy` | `c_tdr_busy_on_capture`, `c_tdr_busy_on_dm_answer`, `c_tdr_busy_on_nop_update`, `c_tdr_busy_on_update`, `c_tdr_clk_stopped_op_pending`, `c_tdr_op3_clears_busy`, `c_tdr_update_while_sticky` | `tdr2axi_sba_busy_test`, `tdr2axi_tdr_busy_capture_test`, `tdr2axi_tdr_busy_recovery_test`, `tdr2axi_tdr_busy_update_test` |
| `F-TDR-strobes` | `c_tdr_scan_via_pause`, `c_tdr_update_two_edges` | `tdr2axi_tdr_strobes_test` |
| `F-TDR-tck-stop-hold` | `c_tdr_tck_stop_bus_pending`, `c_tdr_tck_stop_result_pending`, `c_tdr_tck_stop_result_pending_ss4` | `tdr2axi_tdr_tck_stop_test` |

## Implementation Rules

1. `tdr2axi_fcov` is instantiated in `tb_top` beside the DUT, in code both simulators
   compile.
2. Each cover-property body and its disable condition is a single continuous-assign
   wire, so both simulators sample the same expression.
3. Covergroups sit under `ifndef VERILATOR`.
4. Every internal net used as a feed is an observable of `tb_top` and is in the
   Verilator public scope.
5. Both simulators' builds list the same coverage sources.
