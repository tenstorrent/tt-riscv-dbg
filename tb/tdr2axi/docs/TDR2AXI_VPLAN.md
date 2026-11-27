<!--
SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
SPDX-License-Identifier: CC-BY-4.0
-->

# tdr2axi Verification Plan

Every behavior `doc/tdr2axi.md` and its cited standards require of `tdr2axi` is a feature
in the trace store, and every feature is proven by checks that items declare. The
features, checks and items below are generated from `trace/features.toml` and
`trace/vplan.toml`; the scenario descriptions say what each item drives and what each of
its checks compares.

## Overview

| Field | Value |
|---|---|
| Design under test | `tdr2axi` (`src/tdr2axi/`) |
| Design specification | `doc/tdr2axi.md`, cited by atom ID, not restated |
| Cited standards | RISC-V External Debug Support 0.13.2 (`R-RISCV_DEBUG`), ARM IHI 0022H.c AXI4 and AXI4-Lite (`R-AXI4`, `R-AXI4_LITE`), IEEE Std 1149.1-2013 (`R-IEEE1149_1`); their clauses are the store's X-atoms |
| Register specification | The Debug Module registers of `R-RISCV_DEBUG`; `tdr2axi` adds no registers of its own |
| Trace store | `tb/tdr2axi/trace/`: features, checks and items cite its IDs |
| Testbench | `TDR2AXI_TB_ARCH.md` |
| Realizations | cocotb, and SymbiYosys formal jobs |
| Plan closure | `trace plan --covers` passes: every verify atom, X-atom and assumption is proven by a check an item declares, and every feature, item and configuration dimension has a cover point |

## How the Plan Is Run

Every simulated item runs on each regular build target of `trace/configs.toml` with the
seeds its Seeds column gives, 20 for an item serving a P0 feature and 5 otherwise, so a
parameter appears in every scenario. The four bring-up-only builds,
`axi4_a2_d32_cachef_sync4`, `axi4_a3_d32_cachef`, `axil_a2_d32_prot5` and
`axil_a3_d64_prot5_sync4`, are at address width 2 or 3, too small an address space for
most scenarios' regions. They run bring-up, `tdr2axi_smoke_test`, `tdr2axi_axi_attributes_test`,
`tdr2axi_sba_address_width_test`, `tdr2axi_dm_write_ones_test` and the legal cases of
`tdr2axi_param_checks_test`. The TDR items leave them out: those features vary with
`SYNC_STAGES` and the clock ratio, and the regular builds hold both synchronizer depths.

Verilator runs every seed. The same items run under VCS for the assertion checks bound
in `tb_top`, X-propagation at reset and code coverage. Formal items run in SymbiYosys at
the reduced widths their descriptions name.

The runner is `python3 tb/tdr2axi/scripts/run_sim.py`. `--group smoke` runs the smoke
group on the default build, and `--group signoff` runs every item on every build target
with its testlist reseed count. `--target` picks builds (repeatable, or `all`), and
`--cov` compiles code coverage into a build of its own.

The clock ratio runs in two modes. `--ratio random` draws the `i_clk` period
log-uniform from 1000 to 160000 ps against TCK at 20000 ps, so `i_clk` runs from 20
times faster than TCK to 8 times slower, with a random phase between the clocks.
`--ratio A:B` (repeatable, or `all`) runs the named `CFG-CLK-RATIO` points 20:1, 20:19,
20:21, 1:2 and 1:20 at their exact periods, again with a random phase. The signoff
spread covers the five named points and random draws.

A check that can fire only in part of the configuration space states where in its
Applies column, and a run must show its evidence only when the run meets every condition
there. The per-depth checks hold at one `SYNC_STAGES` value, and the per-ratio checks,
such as `CHK-TDR-ROUND-TRIP-SS4-1TO20`, at one named ratio as well; a random-ratio run
meets no named ratio. The AXI4-only checks hold at `USE_AXI_LITE = 0`, since the
AXI4-Lite channels carry none of the burst, size, cache, last-beat or ID fields they
check, and `CHK-AXI-LITE-FULL-WIDTH` holds at `USE_AXI_LITE = 1`. A check whose Applies
is empty is required in every run, random ratios included.

The `AxiSub` and `TdrHost` rules, the assumption checks and the drain guard run in every
test; an item declares the ones its scenario aims at, and the evidence of those is what
the item must show.

## Features Under Verification

| Feature | Title | Priority | Method | Atoms | X-atoms | Items | Checks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `F-AXI-axi4-size` | On AXI4, AxSIZE follows sbcs.sbaccess and WSTRB marks only the accessed bytes | P0 | sim, vip | `S-TDR2AXI-0059` | none | `tdr2axi_axi_attr_formal`, `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CHK-AXI-AXI4-SIZE`, `CHK-AXI-AXI4-SIZE-SVA` |
| `F-AXI-lite-full-width` | On AXI4-Lite every transfer is one full-width beat: a narrow write marks its bytes with WSTRB and a narrow read takes its bytes from the whole word | P1 | sim, vip | `S-TDR2AXI-0013`, `S-TDR2AXI-0015` | `X-AXI4_LITE-full-width` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CHK-AXI-LITE-FULL-WIDTH` |
| `F-AXI-manager-protocol` | o_axi_req obeys the AXI4 or AXI4-Lite manager handshake rules on every channel | P0 | vip, formal | `S-TDR2AXI-0001`, `S-TDR2AXI-0013`, `S-TDR2AXI-0034`, `S-TDR2AXI-0035` | none | `tdr2axi_axi_mgr_formal`, `tdr2axi_axi_protocol_test` | `CHK-AXI-HANDSHAKE-SVA` |
| `F-AXI-no-comb-path` | No combinational path runs from i_axi_rsp to o_axi_req | P1 | sim, formal | `S-TDR2AXI-0034`, `S-TDR2AXI-0035` | `X-AXI4-no-comb-path`, `X-AXI4_LITE-no-comb-path` | `tdr2axi_axi_comb_path_formal`, `tdr2axi_axi_protocol_test` | `CHK-AXI-NO-COMB-PATH`, `CHK-AXI-NO-COMB-PATH-SVA` |
| `F-AXI-one-outstanding` | The manager issues one single-beat transaction at a time, the next only after the previous response | P0 | sim, formal, vip | `S-TDR2AXI-0012`, `S-TDR2AXI-0058` | none | `tdr2axi_axi_mgr_formal`, `tdr2axi_axi_protocol_test` | `CHK-AXI-ONE-OUTSTANDING`, `CHK-AXI-ONE-OUTSTANDING-SVA` |
| `F-AXI-request-attributes` | Every transaction carries AxPROT = AXI_PROT, AxCACHE = AXI_CACHE on AXI4, ID 0 and user 0 | P0 | sim, formal | `S-TDR2AXI-0016`, `S-TDR2AXI-0017`, `S-TDR2AXI-0058` | `X-AXI4-prot-encoding`, `X-AXI4_LITE-prot-encoding`, `X-AXI4-id-user-values` | `tdr2axi_axi_attr_formal`, `tdr2axi_axi_attributes_test` | `CHK-AXI-REQ-ATTRS`, `CHK-AXI-REQ-ATTRS-SVA` |
| `F-CFG-bender-target` | The Bender target tdr2axi yields a file list that builds tdr2axi from src/tdr2axi | P3 | sim | `S-TDR2AXI-0004` | none | `tdr2axi_smoke_test` | `CHK-CFG-BENDER-TARGET` |
| `F-CFG-param-checks` | A build with an illegal AXI_DATA_WIDTH, AXI_ADDR_WIDTH or SYNC_STAGES, or with AXI structs not overridden to AXI_DATA_WIDTH data, stops before simulation starts | P2 | sim | `S-TDR2AXI-0014`, `S-TDR2AXI-0015`, `S-TDR2AXI-0018`, `S-TDR2AXI-0019`, `S-TDR2AXI-0020`, `S-TDR2AXI-0021`, `S-TDR2AXI-0024` | none | `tdr2axi_param_checks_test` | `CHK-CFG-ILLEGAL-PARAMS` |
| `F-DM-abstract-cmd-unsupported` | Every abstract command sets abstractcs.cmderr = 2 and runs nothing | P2 | sim | `S-TDR2AXI-0010` | `X-RISCV_DEBUG-cmderr` | `tdr2axi_dm_registers_test`, `tdr2axi_dm_write_ones_test` | `CHK-DM-CMDERR` |
| `F-DM-dmactive-clear` | dmactive = 0 holds the DM registers at their reset values, sbcs, sbaddress and sbdata once no system-bus access is in flight, and writing dmactive = 1 enables the DM, while op 3 clears only the bridge status | P0 | sim | `S-TDR2AXI-0056`, `S-TDR2AXI-0064` | `X-RISCV_DEBUG-dmactive` | `tdr2axi_dmactive_clear_test`, `tdr2axi_dmactive_inflight_test`, `tdr2axi_dmactive_reset_test`, `tdr2axi_sba_busy_test` | `CHK-DM-DMACTIVE-ALONE`, `CHK-DM-DMACTIVE-CLEAR`, `CHK-DM-OP3-KEEPS-DM` |
| `F-DM-dmactive-inflight` | An SBA access in flight when dmactive drops completes with the address, data, strobes and size it started with; sbcs, sbaddress and sbdata hold, sbbusy reading 1, until it completes and then take their reset values, unless dmactive is set again first, when its result lands in them; neither dmactive nor op 3 aborts it, only i_arst_n | P0 | sim, vip, formal | `S-TDR2AXI-0056`, `S-TDR2AXI-0090`, `S-TDR2AXI-0091`, `S-TDR2AXI-0092` | none | `tdr2axi_dmactive_inflight_formal`, `tdr2axi_dmactive_inflight_test` | `CHK-DM-DMACTIVE-INFLIGHT`, `CHK-DM-DMACTIVE-INFLIGHT-HOLD`, `CHK-DM-DMACTIVE-INFLIGHT-SVA`, `CHK-DM-DMACTIVE-NO-ABORT`, `CHK-DM-DMACTIVE-REARM` |
| `F-DM-dmactive-no-bus-access` | No bus access starts while dmactive = 0 | P0 | sim, formal | `S-TDR2AXI-0056`, `S-TDR2AXI-0064`, `S-TDR2AXI-0089` | `X-RISCV_DEBUG-dmactive` | `tdr2axi_dmactive_no_bus_formal`, `tdr2axi_dmactive_no_bus_test` | `CHK-DM-DMACTIVE-NO-BUS`, `CHK-DM-DMACTIVE-NO-BUS-SVA` |
| `F-DM-hart-unavailable` | dmstatus reports the one hart as unavailable | P2 | sim | `S-TDR2AXI-0009` | `X-RISCV_DEBUG-hart-unavail` | `tdr2axi_dm_registers_test` | `CHK-DM-HART-UNAVAIL` |
| `F-DM-ndmreset-unconnected` | Setting dmcontrol.ndmreset resets nothing inside or outside tdr2axi | P2 | sim | `S-TDR2AXI-0011` | `X-RISCV_DEBUG-ndmreset` | `tdr2axi_dm_registers_test` | `CHK-DM-NDMRESET` |
| `F-DM-register-map` | The DM answers dmcontrol, sbcs, sbaddress0, sbaddress1, sbdata0 and sbdata1 at the debug specification's DMI addresses | P0 | sim | `S-TDR2AXI-0062` | `X-RISCV_DEBUG-dm-addresses` | `tdr2axi_dm_registers_test` | `CHK-DM-REGISTER-MAP` |
| `F-DM-version` | dmstatus.version reads 2 and sbcs.sbversion reads 1, the values of debug specification 0.13 | P1 | sim | `S-TDR2AXI-0002` | `X-RISCV_DEBUG-dm-version`, `X-RISCV_DEBUG-authenticated`, `X-RISCV_DEBUG-authbusy` | `tdr2axi_smoke_test` | `CHK-DM-VERSION` |
| `F-PORT-bus-and-reset` | The clock, reset and AXI ports are i_clk, i_arst_n, i_test_mode, i_arst_scan_n, o_axi_req and i_axi_rsp, with the table's directions and the AXI channels on i_clk | P1 | sim | `S-TDR2AXI-0025`, `S-TDR2AXI-0026`, `S-TDR2AXI-0027`, `S-TDR2AXI-0028`, `S-TDR2AXI-0034`, `S-TDR2AXI-0035` | none | `tdr2axi_smoke_test` | `CHK-PORT-BUS-LIST` |
| `F-PORT-tdr-interface` | The TDR ports are i_tck, i_dmi_tdr[40:0], i_dmi_tdr_capture, i_dmi_tdr_update and o_dmi_tdr[40:0] on i_tck, with no TAP, shift or dtmcs port | P1 | sim | `S-TDR2AXI-0008`, `S-TDR2AXI-0029`, `S-TDR2AXI-0030`, `S-TDR2AXI-0031`, `S-TDR2AXI-0032`, `S-TDR2AXI-0033` | none | `tdr2axi_smoke_test` | `CHK-PORT-TDR-LIST` |
| `F-RESET-async-any-time` | i_arst_n at any time, an operation in flight included, returns both sides to reset with no AXI transaction left, and the bridge completes operations after release | P0 | sim, vip | `S-TDR2AXI-0026`, `S-TDR2AXI-0051`, `S-TDR2AXI-0052` | none | `tdr2axi_reset_during_clear_test`, `tdr2axi_reset_traffic_test` | `CHK-RESET-ASYNC-ANY-TIME`, `CHK-RESET-NO-STALE-AXI` |
| `F-RESET-sync-release` | i_arst_n resets the i_clk and TCK sides at once and releases each SYNC_STAGES edges of its own clock later | P0 | sim, formal | `S-TDR2AXI-0018`, `S-TDR2AXI-0026`, `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | none | `tdr2axi_reset_release_test`, `tdr2axi_reset_sync_formal` | `CHK-RESET-CDC-CLEAR-1TO2`, `CHK-RESET-CDC-CLEAR-1TO20`, `CHK-RESET-CDC-CLEAR-20TO1`, `CHK-RESET-CDC-CLEAR-20TO19`, `CHK-RESET-CDC-CLEAR-20TO21`, `CHK-RESET-SYNC-RELEASE`, `CHK-RESET-SYNC-RELEASE-SS4`, `CHK-RESET-SYNC-RELEASE-SVA` |
| `F-RESET-test-mode-bypass` | With i_test_mode = 1, i_arst_scan_n drives both internal resets directly, bypassing the synchronizers | P1 | sim, formal | `S-TDR2AXI-0027`, `S-TDR2AXI-0028`, `S-TDR2AXI-0052` | none | `tdr2axi_reset_sync_formal`, `tdr2axi_reset_test_mode_test` | `CHK-RESET-TEST-MODE`, `CHK-RESET-TEST-MODE-SVA` |
| `F-RESET-trst-independent` | A TRST of the host TAP, asserted and released with TCK stopped, resets nothing in tdr2axi, and an operation in flight completes normally | P1 | sim | `S-TDR2AXI-0054`, `S-TDR2AXI-0055` | `X-IEEE1149_1-trst` | `tdr2axi_trst_test` | `CHK-RESET-TRST-INDEPENDENT` |
| `F-SBA-access-errors` | A misaligned access sets sberror = 3 and one wider than the bus sets sberror = 4, with no bus transaction | P2 | sim | `S-TDR2AXI-0060` | `X-RISCV_DEBUG-sberror` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | `CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE` |
| `F-SBA-address-width` | sbaddress0 and, above 32 bits, sbaddress1 reach every AxADDR bit up to AXI_ADDR_WIDTH whatever the data width; at 32 bits or less sbaddress1 is absent: it reads 0, and a write of it with sbbusy and sbbusyerror clear has no effect | P1 | sim | `S-TDR2AXI-0014`, `S-TDR2AXI-0022` | `X-RISCV_DEBUG-sbaddress-regs` | `tdr2axi_dm_write_ones_test`, `tdr2axi_sba_address_width_test` | `CHK-SBA-ADDRESS-WIDTH`, `CHK-SBA-SBADDRESS1-ABSENT` |
| `F-SBA-block-read` | Each read of sbdata0 returns one element; with sbreadondata set it starts the next read, after which, if it succeeded, sbautoincrement advances sbaddress, and with sbreadondata clear it issues no AR and leaves sbaddress unchanged | P1 | sim | `S-TDR2AXI-0081` | `X-RISCV_DEBUG-sbdata0-read`, `X-RISCV_DEBUG-sbautoincrement` | `tdr2axi_sba_block_test`, `tdr2axi_sba_stress_test`, `tdr2axi_tdr_pipelined_test` | `CHK-SBA-BLOCK-READ`, `CHK-SBA-READ-NO-READONDATA` |
| `F-SBA-block-write` | With sbautoincrement set, each write of sbdata0 performs one bus write and, if it succeeded, advances sbaddress by the access size | P1 | sim | `S-TDR2AXI-0079` | `X-RISCV_DEBUG-sbdata0-write`, `X-RISCV_DEBUG-sbautoincrement` | `tdr2axi_sba_block_test`, `tdr2axi_sba_error_address_test`, `tdr2axi_sba_stress_test` | `CHK-SBA-BLOCK-WRITE` |
| `F-SBA-bus-error` | An SLVERR or DECERR response to a read or write sets sberror = 2, which blocks further accesses until cleared | P2 | sim | `S-TDR2AXI-0061` | `X-AXI4-response-codes`, `X-AXI4_LITE-response-codes`, `X-RISCV_DEBUG-sberror` | `tdr2axi_sba_errors_test`, `tdr2axi_sba_stress_test` | `CHK-SBA-BUS-ERROR`, `CHK-SBA-ERROR-BLOCKS` |
| `F-SBA-busy-status` | sbbusy is set from the request until the access completes; an sbaddress or sbdata access during it sets sbbusyerror, is answered busy and does nothing else, and an sbcs write during it with sbbusyerror already set is answered busy and changes no field | P1 | sim | `S-TDR2AXI-0002`, `S-TDR2AXI-0043` | `X-RISCV_DEBUG-sbbusy`, `X-RISCV_DEBUG-sbbusyerror` | `tdr2axi_sba_busy_test` | `CHK-SBA-SBBUSY`, `CHK-SBA-SBBUSYERROR` |
| `F-SBA-data-lanes` | The access size selects the sbdata bits, a 64-bit access spans sbdata1:sbdata0, and reading or writing sbdata1 issues no AR or AW | P0 | sim | `S-TDR2AXI-0015`, `S-TDR2AXI-0077` | `X-RISCV_DEBUG-sbdata-lanes` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test` | `CHK-SBA-DATA-LANES`, `CHK-SBA-SBDATA1-NO-BUS` |
| `F-SBA-no-increment-after-error` | With sbautoincrement set, an access that ends in SLVERR or DECERR leaves sbaddress at the failing address, whether a write of sbaddress0, a write of sbdata0 or a read of sbdata0 started it | P2 | sim | `S-TDR2AXI-0061`, `S-TDR2AXI-0076`, `S-TDR2AXI-0079`, `S-TDR2AXI-0093`, `S-TDR2AXI-0094` | `X-RISCV_DEBUG-sbdata0-write`, `X-RISCV_DEBUG-sbaddress0-read`, `X-RISCV_DEBUG-sbdata0-read` | `tdr2axi_sba_error_address_test` | `CHK-SBA-NO-INC-AFTER-ERROR` |
| `F-SBA-sbcs-fields` | sbcs reports sbversion 1, sbasize = AXI_ADDR_WIDTH and an sbaccess bit per supported size, sbaccess64 only on a 64-bit bus | P1 | sim | `S-TDR2AXI-0014`, `S-TDR2AXI-0015`, `S-TDR2AXI-0023` | `X-RISCV_DEBUG-sbcs-fields` | `tdr2axi_smoke_test` | `CHK-SBA-SBCS-FIELDS` |
| `F-SBA-single-read` | With sbreadonaddr set, writing sbaddress0 starts one bus read whose data reaches sbdata; with it clear, the write issues no AR or AW | P0 | sim | `S-TDR2AXI-0001`, `S-TDR2AXI-0076` | `X-RISCV_DEBUG-sbaddress0-read` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test`, `tdr2axi_smoke_test` | `CHK-SBA-NO-READ-ON-ADDR`, `CHK-SBA-SINGLE-READ` |
| `F-SBA-single-write` | Writing sbdata0 starts one bus write of sbdata to sbaddress at the sbaccess size, landing only on the addressed bytes | P0 | sim | `S-TDR2AXI-0001`, `S-TDR2AXI-0071` | `X-RISCV_DEBUG-sbdata0-write` | `tdr2axi_sba_single_test`, `tdr2axi_sba_stress_test`, `tdr2axi_smoke_test` | `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY` |
| `F-TDR-any-clock-ratio` | Every operation completes with the same result at any i_clk to TCK ratio | P1 | sim | `S-TDR2AXI-0048` | none | `tdr2axi_clock_ratio_test` | `CHK-TDR-RATIO-INVARIANT` |
| `F-TDR-busy-recovery` | After a busy status, op 3 and a resend with more Run-Test/Idle cycles complete the operation; a resent read may reach the bus twice | P1 | sim | `S-TDR2AXI-0045`, `S-TDR2AXI-0046` | `X-RISCV_DEBUG-busy-retry` | `tdr2axi_sba_stress_test`, `tdr2axi_tdr_busy_recovery_test` | `CHK-TDR-BUSY-RECOVERY`, `CHK-TDR-RESEND-READ` |
| `F-TDR-capture-image` | A capture shows the address and data of the last operation accepted in IDLE with a clear status: a read's result, a write's data, a NOP's or op 3's own fields | P0 | sim | `S-TDR2AXI-0033`, `S-TDR2AXI-0037`, `S-TDR2AXI-0038`, `S-TDR2AXI-0063` | `X-RISCV_DEBUG-dmi-update-capture`, `X-RISCV_DEBUG-nop-capture` | `tdr2axi_tdr_ops_test`, `tdr2axi_tdr_pipelined_test` | `CHK-TDR-CAPTURE-IMAGE`, `CHK-TDR-PIPELINED-READ` |
| `F-TDR-op-decode` | An update in IDLE with a clear status decodes the dmi layout: op 0 sends nothing and sets no busy, 1 reads and 2 writes the addressed DM register, 3 clears the sticky status | P0 | sim, formal | `S-TDR2AXI-0030`, `S-TDR2AXI-0036`, `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `S-TDR2AXI-0063` | `X-RISCV_DEBUG-dmi-format`, `X-RISCV_DEBUG-dmi-op` | `tdr2axi_bridge_formal`, `tdr2axi_tdr_ops_test` | `CHK-TDR-OP-DECODE`, `CHK-TDR-OP-DECODE-SVA`, `CHK-TDR-OP3-CLEAR` |
| `F-TDR-result-latency` | A DMI response reaches o_dmi_tdr SYNC_STAGES + 2 to SYNC_STAGES + 3 TCK edges after the DM answers, and a capture before that reads busy | P2 | sim | `S-TDR2AXI-0018`, `S-TDR2AXI-0049` | `X-RISCV_DEBUG-busy-retry` | `tdr2axi_clock_ratio_test`, `tdr2axi_tdr_result_latency_test` | `CHK-TDR-RESULT-LATENCY`, `CHK-TDR-RESULT-LATENCY-SS4`, `CHK-TDR-ROUND-TRIP-SS3-1TO2`, `CHK-TDR-ROUND-TRIP-SS3-1TO20`, `CHK-TDR-ROUND-TRIP-SS3-20TO1`, `CHK-TDR-ROUND-TRIP-SS3-20TO19`, `CHK-TDR-ROUND-TRIP-SS3-20TO21`, `CHK-TDR-ROUND-TRIP-SS4-1TO2`, `CHK-TDR-ROUND-TRIP-SS4-1TO20`, `CHK-TDR-ROUND-TRIP-SS4-20TO1`, `CHK-TDR-ROUND-TRIP-SS4-20TO19`, `CHK-TDR-ROUND-TRIP-SS4-20TO21` |
| `F-TDR-sticky-busy` | Busy sets and sticks on an update of any op, NOP included, while an operation is pending, a capture while a read is pending, or a DM busy answer (read data 0xB051B051); while set, updates are ignored until op 3, and the captured status never reads 2 | P0 | sim, formal | `S-TDR2AXI-0007`, `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `S-TDR2AXI-0042`, `S-TDR2AXI-0043`, `S-TDR2AXI-0045`, `S-TDR2AXI-0056` | `X-RISCV_DEBUG-dmi-status` | `tdr2axi_bridge_formal`, `tdr2axi_dm_resp_formal`, `tdr2axi_sba_busy_test`, `tdr2axi_tdr_busy_capture_test`, `tdr2axi_tdr_busy_update_test` | `CHK-TDR-BUSY-CAPTURE`, `CHK-TDR-BUSY-DM`, `CHK-TDR-BUSY-UPDATE`, `CHK-TDR-DM-RESP-CODES-SVA`, `CHK-TDR-NEVER-FAILED`, `CHK-TDR-STATUS-SVA`, `CHK-TDR-STICKY-IGNORE` |
| `F-TDR-strobes` | Each TCK rising edge with i_dmi_tdr_update high is one update of the parallel TDR value, and o_dmi_tdr is valid at each edge with i_dmi_tdr_capture high | P1 | sim, vip | `S-TDR2AXI-0008`, `S-TDR2AXI-0031`, `S-TDR2AXI-0032` | none | `tdr2axi_tdr_strobes_test` | `CHK-TDR-CAPTURE-EDGE`, `CHK-TDR-UPDATE-EDGE` |
| `F-TDR-tck-stop-hold` | With TCK stopped between scans the TCK side holds its state, and a pending result waits in the response CDC until TCK edges arrive | P0 | sim | `S-TDR2AXI-0029`, `S-TDR2AXI-0049` | `X-IEEE1149_1-tck-static` | `tdr2axi_tdr_tck_stop_test` | `CHK-TDR-TCK-STOP-HOLD`, `CHK-TDR-TCK-STOP-HOLD-SS4` |

## Checks

| Check | Kind | Compare | Feature | Atoms | Timing item | Applies | Intent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CHK-AXI-AXI4-SIZE` | scoreboard | in-order | `F-AXI-axi4-size` | `S-TDR2AXI-0059` |  | USE_AXI_LITE = 0 | On AXI4, each AR and AW carries AxSIZE equal to the access's sbaccess and its address, and a write's WSTRB marks only the accessed bytes |
| `CHK-AXI-AXI4-SIZE-SVA` | assertion |  | `F-AXI-axi4-size` | `S-TDR2AXI-0059` |  | USE_AXI_LITE = 0 | On AXI4, while ARVALID or AWVALID is high, AxSIZE equals sbcs.sbaccess of the access, whatever AXI_DATA_WIDTH is |
| `CHK-AXI-HANDSHAKE-SVA` | assertion |  | `F-AXI-manager-protocol` | `S-TDR2AXI-0001`, `S-TDR2AXI-0013`, `S-TDR2AXI-0034`, `S-TDR2AXI-0035` |  |  | On o_axi_req, AR, AW and W VALID stay high until READY, every request field holds while VALID is high and READY low, R and B complete only with RREADY and BREADY, and no VALID is high in reset, in the AXI4 or AXI4-Lite form USE_AXI_LITE selects |
| `CHK-AXI-LITE-FULL-WIDTH` | scoreboard | in-order | `F-AXI-lite-full-width` | `S-TDR2AXI-0013`, `S-TDR2AXI-0015`, `X-AXI4_LITE-full-width` |  | USE_AXI_LITE = 1 | On AXI4-Lite, a narrow write is one full-width beat whose WSTRB marks its bytes, and a narrow read takes its bytes from the addressed lanes of the whole word returned |
| `CHK-AXI-NO-COMB-PATH` | checker |  | `F-AXI-no-comb-path` | `S-TDR2AXI-0034`, `S-TDR2AXI-0035`, `X-AXI4-no-comb-path`, `X-AXI4_LITE-no-comb-path` |  |  | With i_axi_rsp changing between i_clk edges, o_axi_req changes only at i_clk rising edges |
| `CHK-AXI-NO-COMB-PATH-SVA` | assertion |  | `F-AXI-no-comb-path` | `S-TDR2AXI-0034`, `S-TDR2AXI-0035`, `X-AXI4-no-comb-path`, `X-AXI4_LITE-no-comb-path` |  |  | In a model where i_axi_rsp may change on any global tick, o_axi_req is stable on every tick that is not an i_clk rising edge |
| `CHK-AXI-ONE-OUTSTANDING` | checker |  | `F-AXI-one-outstanding` | `S-TDR2AXI-0012`, `S-TDR2AXI-0058` |  |  | At most one AR or AW is accepted without its R or B, the next request follows the previous response, and each transaction is one beat: AxLEN 0, WLAST on the W beat, AxBURST legal for one beat |
| `CHK-AXI-ONE-OUTSTANDING-SVA` | assertion |  | `F-AXI-one-outstanding` | `S-TDR2AXI-0012`, `S-TDR2AXI-0058` |  |  | No AR or AW handshake while a read or write response is owed, AxLEN 0 and WLAST high on every W beat |
| `CHK-AXI-REQ-ATTRS` | checker |  | `F-AXI-request-attributes` | `S-TDR2AXI-0016`, `S-TDR2AXI-0017`, `S-TDR2AXI-0058`, `X-AXI4-prot-encoding`, `X-AXI4_LITE-prot-encoding`, `X-AXI4-id-user-values` |  |  | Every AR and AW carries AxPROT = AXI_PROT and, on AXI4, AxCACHE = AXI_CACHE, AxID 0 and AxUSER 0, and every W carries WUSER 0, against the build's parameter values |
| `CHK-AXI-REQ-ATTRS-SVA` | assertion |  | `F-AXI-request-attributes` | `S-TDR2AXI-0016`, `S-TDR2AXI-0017`, `S-TDR2AXI-0058`, `X-AXI4-prot-encoding`, `X-AXI4_LITE-prot-encoding`, `X-AXI4-id-user-values` |  |  | While ARVALID or AWVALID is high, AxPROT equals AXI_PROT, AxCACHE equals AXI_CACHE on AXI4, and AxID, AxUSER and WUSER are 0 |
| `CHK-AXISUB-BOUNDARY-4K` | vip |  |  | `X-AXI4-boundary-4k` |  | USE_AXI_LITE = 0 | No burst crosses a 4KB address boundary |
| `CHK-AXISUB-BURST-LENGTH` | vip |  |  | `X-AXI4-burst-length` |  | USE_AXI_LITE = 0 | AxLEN is legal for AxBURST: INCR 1 to 256 beats, FIXED 1 to 16, WRAP 2, 4, 8 or 16 from an aligned start |
| `CHK-AXISUB-CACHE-LEGAL` | vip |  |  | `X-AXI4-cache-legal` |  | USE_AXI_LITE = 0 | AxCACHE is one of the memory-type encodings, never a reserved value |
| `CHK-AXISUB-ID-ORDER` | vip |  |  | `X-AXI4-id-order` |  | USE_AXI_LITE = 0 | Each response carries its request's ID, and responses to one ID return in issue order |
| `CHK-AXISUB-LAST-BEAT` | vip |  |  | `X-AXI4-last-beat` |  | USE_AXI_LITE = 0 | WLAST is high on the final W beat of each write |
| `CHK-AXISUB-PAYLOAD-STABLE` | vip |  |  | `X-AXI4-payload-stable`, `X-AXI4_LITE-payload-stable` |  |  | Every request field of AR, AW and W holds from VALID until the handshake |
| `CHK-AXISUB-READ-RESP-ORDER` | vip |  |  | `X-AXI4-read-data-after-address`, `X-AXI4_LITE-read-data-after-address` |  |  | RVALID rises only after the AR handshake of the read it answers |
| `CHK-AXISUB-RESET-VALID-LOW` | vip |  |  | `X-AXI4-reset-valid-low`, `X-AXI4_LITE-reset-valid-low` |  |  | No VALID is high in reset or before the first i_clk rising edge after release |
| `CHK-AXISUB-SIZE-WIDTH` | vip |  |  | `X-AXI4-size-width` |  | USE_AXI_LITE = 0 | AxSIZE never exceeds the data bus width |
| `CHK-AXISUB-STROBE-LANES` | vip |  |  | `X-AXI4-strobe-lanes`, `X-AXI4_LITE-strobe-lanes` |  |  | WSTRB is high only on lanes the address and size select |
| `CHK-AXISUB-VALID-HELD` | vip |  |  | `X-AXI4-valid-held`, `X-AXI4_LITE-valid-held` |  |  | ARVALID, AWVALID and WVALID stay high from assertion until the READY handshake |
| `CHK-AXISUB-VALID-INDEPENDENT` | vip |  |  | `X-AXI4-valid-independent`, `X-AXI4_LITE-valid-independent` |  |  | With READY held low until VALID rises, every VALID still rises: no VALID waits for READY |
| `CHK-AXISUB-WRITE-RESP-ORDER` | vip |  |  | `X-AXI4-write-response-after-data`, `X-AXI4_LITE-write-response-after-data` |  |  | BVALID rises only after the AW handshake and the last W handshake of that write |
| `CHK-CFG-BENDER-TARGET` | checker |  | `F-CFG-bender-target` | `S-TDR2AXI-0004` |  |  | The Bender script for target tdr2axi lists the src/tdr2axi sources, and the bench builds tdr2axi from that list alone |
| `CHK-CFG-ILLEGAL-PARAMS` | checker |  | `F-CFG-param-checks` | `S-TDR2AXI-0014`, `S-TDR2AXI-0015`, `S-TDR2AXI-0018`, `S-TDR2AXI-0019`, `S-TDR2AXI-0020`, `S-TDR2AXI-0021`, `S-TDR2AXI-0024` |  |  | A build with AXI_DATA_WIDTH other than 32 or 64, AXI_ADDR_WIDTH below the byte offset or above 64, SYNC_STAGES below 3, AXI struct data widths other than AXI_DATA_WIDTH, or the default logic struct types stops before simulation, and each legal boundary build starts |
| `CHK-DM-CMDERR` | scoreboard | state | `F-DM-abstract-cmd-unsupported` | `S-TDR2AXI-0010`, `X-RISCV_DEBUG-cmderr` |  |  | Every write of command sets abstractcs.cmderr = 2 and starts no bus access, and writing ones to cmderr clears it |
| `CHK-DM-DMACTIVE-ALONE` | checker |  | `F-DM-dmactive-clear` | `S-TDR2AXI-0056`, `S-TDR2AXI-0064` |  |  | dmactive cleared and set again with no other reset, no system-bus access in flight and none started while it is 0 issues no AR or AW, leaves the subordinate memory unchanged and the bridge status 0, and each capture holds the result of the operation it follows: dmcontrol reads dmactive = 0 while cleared, then 1, and sbcs reads its reset value after the set |
| `CHK-DM-DMACTIVE-CLEAR` | scoreboard | state | `F-DM-dmactive-clear` | `S-TDR2AXI-0056`, `S-TDR2AXI-0064`, `X-RISCV_DEBUG-dmactive` |  |  | While dmactive = 0, whether a dmcontrol write or i_arst_n cleared it, abstractcs reads cmderr = 0 whatever was written before, whether or not a system-bus access is in flight; after i_arst_n, or after a dmcontrol write of dmactive = 0 that found no system-bus access in flight, sbaddress0, sbaddress1, sbdata0 and sbdata1 read their reset values while dmactive = 0 whatever was written before, with sbcs compared only in sbbusy and sbbusyerror, which read 0, sbbusyerror also when it was set before dmactive fell, and sbcs reads its reset value once dmactive = 1; after dmactive = 1, cmderr reads 0 when a dmcontrol write cleared dmactive, and when that write found no system-bus access in flight, sbaddress0, sbaddress1, sbdata0 and sbdata1 read their reset values and accesses work |
| `CHK-DM-DMACTIVE-INFLIGHT` | checker |  | `F-DM-dmactive-inflight` | `S-TDR2AXI-0056`, `S-TDR2AXI-0090` |  |  | An access stalled on AR, AW or W when dmactive drops keeps its address, size, data and strobes until its handshake, and completes once |
| `CHK-DM-DMACTIVE-INFLIGHT-HOLD` | scoreboard | state | `F-DM-dmactive-inflight` | `S-TDR2AXI-0090` |  |  | While dmactive = 0 holds an access in flight, sbcs reads the value it had when dmactive fell, with sbbusy = 1 and sbbusyerror as it was then, 1 after a collision before the fall, and sbaddress0 and sbaddress1 the access's address; a write of sbcs, sbaddress0, sbaddress1, sbdata0 or sbdata1 or a read of sbdata0 or sbdata1 during the hold changes none of them and leaves sbbusyerror as it was, an sbcs write of 1 to sbbusyerror included, whatever the DMI answer; once sbcs reads sbbusy = 0, with dmactive still 0, it reads sbbusyerror = 0 with it, and sbaddress0, sbaddress1, sbdata0 and sbdata1 read their reset values; after dmactive is set again sbcs, sbaddress0, sbaddress1, sbdata0 and sbdata1 read their reset values, sbbusyerror = 0 included, with no trace of the access's read data or error response |
| `CHK-DM-DMACTIVE-INFLIGHT-SVA` | assertion |  | `F-DM-dmactive-inflight` | `S-TDR2AXI-0056`, `S-TDR2AXI-0090` |  |  | With DMI writes of dmcontrol free, every o_axi_req request field holds while its VALID is high and READY low |
| `CHK-DM-DMACTIVE-NO-ABORT` | checker |  | `F-DM-dmactive-inflight` | `S-TDR2AXI-0092` |  |  | With the subordinate withholding READY or the response, dmactive = 0 and op 3 leave the access in place for as long as the subordinate withholds it: ARVALID, AWVALID or WVALID stays high for an AR, AW or W hold, RREADY or BREADY stays high for an R or B hold, and sbcs reads sbbusy = 1 after op 3 and after a wait longer than any other stall; i_arst_n ends the access, lowering every VALID, and the DM then reads its reset values, with sbcs compared only in sbbusy and sbbusyerror, both 0 |
| `CHK-DM-DMACTIVE-NO-BUS` | checker |  | `F-DM-dmactive-no-bus-access` | `S-TDR2AXI-0056`, `S-TDR2AXI-0064`, `X-RISCV_DEBUG-dmactive`, `S-TDR2AXI-0089` |  |  | While dmactive = 0, writes of sbdata0, sbaddress0 and sbcs raise no ARVALID or AWVALID |
| `CHK-DM-DMACTIVE-NO-BUS-SVA` | assertion |  | `F-DM-dmactive-no-bus-access` | `S-TDR2AXI-0056`, `S-TDR2AXI-0064`, `X-RISCV_DEBUG-dmactive`, `S-TDR2AXI-0089` |  |  | No ARVALID or AWVALID rises while dmactive is 0 |
| `CHK-DM-DMACTIVE-REARM` | scoreboard | state | `F-DM-dmactive-inflight` | `S-TDR2AXI-0091` |  |  | dmactive set again by a dmcontrol write the DM takes in a cycle before the access's R or B handshake, whether the access waits for READY or for its R or B response, resets none of sbcs, sbaddress and sbdata, and the access's result lands in them: sbbusy falls at the response, sbdata0 reads the read data, sberror reads 2 after SLVERR or DECERR, and with sbautoincrement set sbaddress advances only after a successful access. A write taken in the handshake's own cycle neither resets them nor lands the result, a boundary S-TDR2AXI-0091 leaves open, and the check grades no re-arm in that cycle |
| `CHK-DM-HART-UNAVAIL` | scoreboard | state | `F-DM-hart-unavailable` | `S-TDR2AXI-0009`, `X-RISCV_DEBUG-hart-unavail` |  |  | dmstatus.allunavail and anyunavail read 1, also after writes of haltreq, resumereq and hartsel |
| `CHK-DM-NDMRESET` | scoreboard | state | `F-DM-ndmreset-unconnected` | `S-TDR2AXI-0011`, `X-RISCV_DEBUG-ndmreset` |  |  | Setting and clearing dmcontrol.ndmreset leaves the SBA registers, subordinate memory and the bridge status unchanged and starts no bus access |
| `CHK-DM-OP3-KEEPS-DM` | scoreboard | state | `F-DM-dmactive-clear` | `S-TDR2AXI-0056` |  |  | Op 3 clears the bridge status and leaves every DM register value unchanged |
| `CHK-DM-REGISTER-MAP` | scoreboard | state | `F-DM-register-map` | `S-TDR2AXI-0062`, `X-RISCV_DEBUG-dm-addresses` |  |  | dmcontrol, sbcs, sbaddress0, sbaddress1, sbdata0 and sbdata1 answer at 0x10, 0x38, 0x39, 0x3A, 0x3C and 0x3D, and dmstatus, abstractcs and command at 0x11, 0x16 and 0x17: a distinct value written to each address changes only its own register |
| `CHK-DM-VERSION` | scoreboard | state | `F-DM-version` | `S-TDR2AXI-0002`, `X-RISCV_DEBUG-dm-version`, `X-RISCV_DEBUG-authenticated`, `X-RISCV_DEBUG-authbusy` |  |  | dmstatus.version reads 2 and sbcs.sbversion reads 1, and dmstatus.authenticated reads 1 and authbusy reads 0 on this DM, which implements no authentication |
| `CHK-ENV-FABRIC-RESET` | env |  |  | `S-TDR2AXI-0057` |  |  | The subordinate drops every pending response and lowers its readys with each i_arst_n, so no transaction survives on one side |
| `CHK-ENV-READONDATA-IDLE` | env |  |  | `S-TDR2AXI-0047` |  |  | Before an sbdata0 read with sbreadondata set at a side-effecting location, the host's Run-Test/Idle count covers the full round trip, N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck) TCK edges, of which T-TDR2AXI-resp-to-tdr is the response leg, and the subordinate flags a second read of such a location |
| `CHK-ENV-SBA-BLOCK-READ-STEPS` | env |  |  | `S-TDR2AXI-0080`, `S-TDR2AXI-0082` |  |  | The host's block read writes sbcs with sbreadonaddr, sbreadondata and sbautoincrement, then the address, and clears sbreadondata before reading the last element |
| `CHK-ENV-SBA-POLL` | env |  |  | `S-TDR2AXI-0072`, `S-TDR2AXI-0078` |  |  | After each access the host reads sbcs until sbbusy = 0 and checks sberror and sbbusyerror before the next access or the data read |
| `CHK-ENV-SBA-READ-STEPS` | env |  |  | `S-TDR2AXI-0074`, `S-TDR2AXI-0075` |  |  | The host's single read writes sbcs with sbaccess and sbreadonaddr, then sbaddress1 when needed, then sbaddress0 |
| `CHK-ENV-SBA-WRITE-STEPS` | env |  |  | `S-TDR2AXI-0066`, `S-TDR2AXI-0067`, `S-TDR2AXI-0068`, `S-TDR2AXI-0069`, `S-TDR2AXI-0070` |  |  | The host's single write writes sbcs with sbaccess, then sbaddress1 when AXI_ADDR_WIDTH > 32, then sbaddress0, then sbdata1 for a 64-bit access, then sbdata0 |
| `CHK-ENV-TDR-UPDATE-STABLE` | env |  |  | `S-TDR2AXI-0050` |  |  | i_dmi_tdr holds a known value from before until after each TCK rising edge with i_dmi_tdr_update high; an assume in the bridge's formal job |
| `CHK-PORT-BUS-LIST` | checker |  | `F-PORT-bus-and-reset` | `S-TDR2AXI-0025`, `S-TDR2AXI-0026`, `S-TDR2AXI-0027`, `S-TDR2AXI-0028`, `S-TDR2AXI-0034`, `S-TDR2AXI-0035` |  |  | The elaborated port list has inputs i_clk, i_arst_n, i_test_mode and i_arst_scan_n, output o_axi_req of type axi_req_t and input i_axi_rsp of type axi_rsp_t |
| `CHK-PORT-TDR-LIST` | checker |  | `F-PORT-tdr-interface` | `S-TDR2AXI-0008`, `S-TDR2AXI-0029`, `S-TDR2AXI-0030`, `S-TDR2AXI-0031`, `S-TDR2AXI-0032`, `S-TDR2AXI-0033` |  |  | The elaborated port list has inputs i_tck, i_dmi_tdr[40:0], i_dmi_tdr_capture and i_dmi_tdr_update and output o_dmi_tdr[40:0], and no TAP, shift-register or dtmcs port |
| `CHK-RESET-ASYNC-ANY-TIME` | scoreboard | state | `F-RESET-async-any-time` | `S-TDR2AXI-0026`, `S-TDR2AXI-0051`, `S-TDR2AXI-0052` |  |  | After i_arst_n at a random phase of both clocks with an operation at any stage, the capture reads address 0, data 0 and status 0, the DM registers read their reset values, a read the request CDC takes while the i_clk side isolates it waits valid at the CDC's destination, and the next operations complete with the predicted results, a busy before the bridge is ready being recovered with op 3; every clear-sequence receiver reads IDLE in the step i_arst_n falls |
| `CHK-RESET-CDC-CLEAR-1TO2` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-cdc-clear-1to2` | ratio = 1:2 | At i_clk:TCK = 1:2, after each i_arst_n release the four isolate requests of u_cdc_req and u_cdc_resp are all low within the maximum of T-TDR2AXI-cdc-clear-1to2, counted in TCK rising edges from the later of the two synchronized releases, and an operation whose Update-DR follows that maximum crosses without busy |
| `CHK-RESET-CDC-CLEAR-1TO20` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-cdc-clear-1to20` | ratio = 1:20 | At i_clk:TCK = 1:20, after each i_arst_n release the four isolate requests of u_cdc_req and u_cdc_resp are all low within the maximum of T-TDR2AXI-cdc-clear-1to20, counted in TCK rising edges from the later of the two synchronized releases, and an operation whose Update-DR follows that maximum crosses without busy |
| `CHK-RESET-CDC-CLEAR-20TO1` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-cdc-clear-20to1` | ratio = 20:1 | At i_clk:TCK = 20:1, after each i_arst_n release the four isolate requests of u_cdc_req and u_cdc_resp are all low within the maximum of T-TDR2AXI-cdc-clear-20to1, counted in TCK rising edges from the later of the two synchronized releases, and an operation whose Update-DR follows that maximum crosses without busy |
| `CHK-RESET-CDC-CLEAR-20TO19` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-cdc-clear-20to19` | ratio = 20:19 | At i_clk:TCK = 20:19, after each i_arst_n release the four isolate requests of u_cdc_req and u_cdc_resp are all low within the maximum of T-TDR2AXI-cdc-clear-20to19, counted in TCK rising edges from the later of the two synchronized releases, and an operation whose Update-DR follows that maximum crosses without busy |
| `CHK-RESET-CDC-CLEAR-20TO21` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-cdc-clear-20to21` | ratio = 20:21 | At i_clk:TCK = 20:21, after each i_arst_n release the four isolate requests of u_cdc_req and u_cdc_resp are all low within the maximum of T-TDR2AXI-cdc-clear-20to21, counted in TCK rising edges from the later of the two synchronized releases, and an operation whose Update-DR follows that maximum crosses without busy |
| `CHK-RESET-NO-STALE-AXI` | checker |  | `F-RESET-async-any-time` | `S-TDR2AXI-0026`, `S-TDR2AXI-0051` |  |  | No AXI request of an operation cut off by i_arst_n reaches o_axi_req after release, and no response from before the reset is taken |
| `CHK-RESET-SYNC-RELEASE` | scoreboard | in-order | `F-RESET-sync-release` | `S-TDR2AXI-0018`, `S-TDR2AXI-0026`, `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-tck-reset-release` | SYNC_STAGES = 3 | At SYNC_STAGES = 3, i_arst_n falling resets both sides in the same step; after it rises the TCK side releases SYNC_STAGES to SYNC_STAGES + 1 TCK rising edges later and the i_clk side as many i_clk edges later, counting only edges that arrive |
| `CHK-RESET-SYNC-RELEASE-SS4` | checker |  | `F-RESET-sync-release` | `S-TDR2AXI-0018`, `S-TDR2AXI-0026`, `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-tck-reset-release-ss4` | SYNC_STAGES = 4 | At SYNC_STAGES = 4, i_arst_n falling resets both sides in the same step; after it rises the TCK side releases within the bounds of T-TDR2AXI-tck-reset-release-ss4 and the i_clk side as many i_clk edges later, counting only edges that arrive |
| `CHK-RESET-SYNC-RELEASE-SVA` | assertion |  | `F-RESET-sync-release` | `S-TDR2AXI-0018`, `S-TDR2AXI-0026`, `S-TDR2AXI-0052`, `S-TDR2AXI-0053` | `T-TDR2AXI-tck-reset-release` |  | Both internal resets fall in the step i_arst_n falls; each rises SYNC_STAGES to SYNC_STAGES + 1 rising edges of its own clock after i_arst_n rises |
| `CHK-RESET-TEST-MODE` | checker |  | `F-RESET-test-mode-bypass` | `S-TDR2AXI-0027`, `S-TDR2AXI-0028`, `S-TDR2AXI-0052` |  |  | With i_test_mode = 1, i_arst_scan_n alone resets both sides and releases them with no synchronizer delay, and i_arst_n has no effect |
| `CHK-RESET-TEST-MODE-SVA` | assertion |  | `F-RESET-test-mode-bypass` | `S-TDR2AXI-0027`, `S-TDR2AXI-0028`, `S-TDR2AXI-0052` |  |  | While i_test_mode is 1 both internal resets equal i_arst_scan_n |
| `CHK-RESET-TRST-INDEPENDENT` | scoreboard | state | `F-RESET-trst-independent` | `S-TDR2AXI-0054`, `S-TDR2AXI-0055`, `X-IEEE1149_1-trst` |  |  | A TAP reset asserted and released with TCK stopped, with an operation in flight, leaves tdr2axi untouched: the operation completes on the bus, the sticky status holds until op 3, and the next capture shows the result |
| `CHK-SBA-ADDRESS-WIDTH` | scoreboard | in-order | `F-SBA-address-width` | `S-TDR2AXI-0014`, `S-TDR2AXI-0022`, `X-RISCV_DEBUG-sbaddress-regs` |  |  | Each address bit below AXI_ADDR_WIDTH, written through sbaddress0 and above bit 31 through sbaddress1, reaches the same AxADDR bit at either data width, and both registers read back what was written |
| `CHK-SBA-BLOCK-READ` | scoreboard | in-order | `F-SBA-block-read` | `S-TDR2AXI-0081`, `X-RISCV_DEBUG-sbdata0-read`, `X-RISCV_DEBUG-sbautoincrement` |  |  | Each sbdata0 read returns one element, and with sbreadondata set starts the next read at the address sbautoincrement advanced |
| `CHK-SBA-BLOCK-WRITE` | scoreboard | in-order | `F-SBA-block-write` | `S-TDR2AXI-0079`, `X-RISCV_DEBUG-sbdata0-write`, `X-RISCV_DEBUG-sbautoincrement` |  |  | With sbautoincrement set, each sbdata0 write gives one bus write at the current address, after each successful write sbaddress advances by the access size, and sbaddress0 and sbaddress1 read back the advanced address |
| `CHK-SBA-BUS-ERROR` | scoreboard | state | `F-SBA-bus-error` | `S-TDR2AXI-0061`, `X-AXI4-response-codes`, `X-AXI4_LITE-response-codes`, `X-RISCV_DEBUG-sberror` |  |  | An SLVERR or DECERR response to a read or a write sets sberror = 2 after exactly one transaction |
| `CHK-SBA-DATA-LANES` | scoreboard | in-order | `F-SBA-data-lanes` | `S-TDR2AXI-0015`, `S-TDR2AXI-0077`, `X-RISCV_DEBUG-sbdata-lanes` |  |  | The access size selects the sbdata bits compared, a 64-bit access spans sbdata1:sbdata0, and bits above a narrow read are not compared |
| `CHK-SBA-ERROR-BLOCKS` | checker |  | `F-SBA-bus-error` | `S-TDR2AXI-0061`, `X-RISCV_DEBUG-sberror` |  |  | Once sberror is nonzero, after an error on a single access or on any element of a block, no further bus access starts: not the write an sbdata0 write requests, not the read an sbaddress0 write with sbreadonaddr requests, and not the next read an sbdata0 read with sbreadondata would start; writing ones to sberror clears it so accesses resume |
| `CHK-SBA-MISALIGNED` | scoreboard | state | `F-SBA-access-errors` | `S-TDR2AXI-0060`, `X-RISCV_DEBUG-sberror` |  |  | An access not naturally aligned to its size sets sberror = 3 and issues no bus transaction |
| `CHK-SBA-NO-INC-AFTER-ERROR` | scoreboard | state | `F-SBA-no-increment-after-error` | `S-TDR2AXI-0061`, `S-TDR2AXI-0076`, `S-TDR2AXI-0079`, `S-TDR2AXI-0093`, `S-TDR2AXI-0094`, `X-RISCV_DEBUG-sbdata0-write`, `X-RISCV_DEBUG-sbaddress0-read`, `X-RISCV_DEBUG-sbdata0-read` |  |  | With sbautoincrement set, after SLVERR or DECERR on a read started by an sbaddress0 write, a write started by an sbdata0 write, or a read started by an sbdata0 read with sbreadondata set, sbaddress0 and sbaddress1 read the failing address |
| `CHK-SBA-NO-READ-ON-ADDR` | checker |  | `F-SBA-single-read` | `S-TDR2AXI-0076`, `X-RISCV_DEBUG-sbaddress0-read` |  |  | With sbreadonaddr clear, writing sbaddress0 issues no AR or AW |
| `CHK-SBA-OVERSIZE` | scoreboard | state | `F-SBA-access-errors` | `S-TDR2AXI-0060`, `X-RISCV_DEBUG-sberror` |  |  | An access wider than AXI_DATA_WIDTH sets sberror = 4 and issues no bus transaction |
| `CHK-SBA-READ-NO-READONDATA` | checker |  | `F-SBA-block-read` | `S-TDR2AXI-0081`, `X-RISCV_DEBUG-sbdata0-read` |  |  | With sbreadondata clear, an sbdata0 read issues no AR and leaves sbaddress unchanged |
| `CHK-SBA-SBADDRESS1-ABSENT` | scoreboard | state | `F-SBA-address-width` | `S-TDR2AXI-0014`, `X-RISCV_DEBUG-sbaddress-regs` |  | AXI_ADDR_WIDTH = 2, 3, 32 | Where AXI_ADDR_WIDTH is 32 or less, a write of sbaddress1 made with sbbusy and sbbusyerror clear has no effect whatever its value, all ones included: sbaddress1 reads 0 after it, and sbaddress0 and the AxADDR of the next access are unchanged. A write of it while sbbusy or sbbusyerror is set collides, and CHK-SBA-SBBUSYERROR grades that |
| `CHK-SBA-SBBUSY` | scoreboard | state | `F-SBA-busy-status` | `S-TDR2AXI-0002`, `X-RISCV_DEBUG-sbbusy` |  |  | DV reading where the debug specification leaves the time sbbusy falls open and a debugger only polls until it reads 0: every sbcs read the DM takes from the write that starts an access until that access's bus response reads sbbusy = 1, and after the bus response at most one sbcs read may read sbbusy = 1, every later read reading 0 until the next access starts |
| `CHK-SBA-SBBUSYERROR` | scoreboard | state | `F-SBA-busy-status` | `S-TDR2AXI-0002`, `S-TDR2AXI-0043`, `X-RISCV_DEBUG-sbbusyerror` |  |  | While sbbusy is set, a write of sbaddress0, sbaddress1, sbdata0 or sbdata1, or a read of sbdata0 or sbdata1, sets sbbusyerror, is answered DMI busy and starts no bus access, and a colliding write leaves sbaddress and sbdata as the stalled access leaves them; an sbcs write with sbbusyerror already set is answered DMI busy, starts no bus access and leaves every sbcs field as it was; a read of sbaddress0 or sbaddress1 returns the address and does not set sbbusyerror. With sbbusyerror set and sbbusy clear, an sbcs write that leaves bit 22 clear is taken and leaves sbbusyerror set, and with sbreadonaddr and sbreadondata set by that write, each of six accesses, a write of sbaddress0, sbaddress1, sbdata0 or sbdata1 or a read of sbdata0 or sbdata1, is answered DMI busy and starts no bus access |
| `CHK-SBA-SBCS-FIELDS` | scoreboard | state | `F-SBA-sbcs-fields` | `S-TDR2AXI-0014`, `S-TDR2AXI-0015`, `S-TDR2AXI-0023`, `X-RISCV_DEBUG-sbcs-fields` |  |  | With dmactive = 1, sbcs reads sbversion 1, sbasize = AXI_ADDR_WIDTH, sbaccess8, 16 and 32 set, sbaccess64 set only for 64-bit data, sbaccess128 clear, and sbaccess 2 after reset |
| `CHK-SBA-SBDATA1-NO-BUS` | checker |  | `F-SBA-data-lanes` | `S-TDR2AXI-0077`, `X-RISCV_DEBUG-sbdata-lanes` |  |  | A read or write of sbdata1 issues no AR or AW |
| `CHK-SBA-SINGLE-READ` | scoreboard | in-order | `F-SBA-single-read` | `S-TDR2AXI-0001`, `S-TDR2AXI-0076`, `X-RISCV_DEBUG-sbaddress0-read` |  |  | With sbreadonaddr set, writing sbaddress0 starts exactly one bus read at sbaddress and the sbaccess size, and the read bytes reach sbdata |
| `CHK-SBA-SINGLE-WRITE` | scoreboard | in-order | `F-SBA-single-write` | `S-TDR2AXI-0001`, `S-TDR2AXI-0071`, `X-RISCV_DEBUG-sbdata0-write` |  |  | Writing sbdata0 starts exactly one bus write of sbdata to sbaddress at the sbaccess size, its bytes on the addressed lanes |
| `CHK-SBA-WRITE-MEMORY` | scoreboard | memory | `F-SBA-single-write` | `S-TDR2AXI-0001`, `S-TDR2AXI-0071` |  |  | Subordinate memory after each write equals the model's image, so only the addressed bytes change |
| `CHK-TDR-BUSY-CAPTURE` | scoreboard | state | `F-TDR-sticky-busy` | `S-TDR2AXI-0007`, `S-TDR2AXI-0042`, `S-TDR2AXI-0043`, `X-RISCV_DEBUG-dmi-status` |  |  | A capture while a read is pending reads busy on that same capture edge, and the status stays busy |
| `CHK-TDR-BUSY-DM` | scoreboard | state | `F-TDR-sticky-busy` | `S-TDR2AXI-0007`, `S-TDR2AXI-0042`, `S-TDR2AXI-0043`, `X-RISCV_DEBUG-dmi-status` |  |  | A busy answer from the DM makes the status busy, with read data 0xB051B051 in the capture |
| `CHK-TDR-BUSY-RECOVERY` | scoreboard | in-order | `F-TDR-busy-recovery` | `S-TDR2AXI-0045`, `X-RISCV_DEBUG-busy-retry` |  |  | After a busy status, op 3 and a resend with more Run-Test/Idle cycles complete the operation with the predicted result, and a resent write reaches the bus once |
| `CHK-TDR-BUSY-UPDATE` | scoreboard | state | `F-TDR-sticky-busy` | `S-TDR2AXI-0007`, `S-TDR2AXI-0042`, `S-TDR2AXI-0043`, `X-RISCV_DEBUG-dmi-status` |  |  | An update of any op, NOP included, while an operation is pending makes the status busy: the next capture and every capture after it read 3 |
| `CHK-TDR-CAPTURE-EDGE` | checker |  | `F-TDR-strobes` | `S-TDR2AXI-0008`, `S-TDR2AXI-0031` |  |  | At each TCK rising edge with i_dmi_tdr_capture high, o_dmi_tdr holds no X or Z and equals the predicted image, whatever TAP path the scan takes |
| `CHK-TDR-CAPTURE-IMAGE` | scoreboard | in-order | `F-TDR-capture-image` | `S-TDR2AXI-0033`, `S-TDR2AXI-0037`, `S-TDR2AXI-0038`, `X-RISCV_DEBUG-dmi-update-capture`, `X-RISCV_DEBUG-nop-capture` |  |  | At each capture edge o_dmi_tdr[40:34] and [33:2] equal the address and data of the last operation accepted in IDLE with a clear status: a read's result, a write's write data, a NOP's or op 3's own fields |
| `CHK-TDR-DM-RESP-CODES-SVA` | assertion |  | `F-TDR-sticky-busy` | `S-TDR2AXI-0039`, `S-TDR2AXI-0042`, `X-RISCV_DEBUG-dmi-status` |  |  | The Debug Module answers every DMI request with success or busy, never the failed or reserved code, so no failed status can reach o_dmi_tdr |
| `CHK-TDR-NEVER-FAILED` | checker |  | `F-TDR-sticky-busy` | `S-TDR2AXI-0039`, `S-TDR2AXI-0042` |  |  | The status at every capture edge is 0 or 3, never 1 or 2 |
| `CHK-TDR-OP-DECODE` | scoreboard | in-order | `F-TDR-op-decode` | `S-TDR2AXI-0030`, `S-TDR2AXI-0036`, `S-TDR2AXI-0039`, `S-TDR2AXI-0063`, `X-RISCV_DEBUG-dmi-format`, `X-RISCV_DEBUG-dmi-op` |  |  | Each update edge in IDLE with a clear status sends one DMI request with i_dmi_tdr[40:34] as address and [33:2] as data, a read for op 1 and a write for op 2, and op 0 sends none |
| `CHK-TDR-OP-DECODE-SVA` | assertion |  | `F-TDR-op-decode` | `S-TDR2AXI-0030`, `S-TDR2AXI-0036`, `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `X-RISCV_DEBUG-dmi-format`, `X-RISCV_DEBUG-dmi-op` |  |  | In the bridge, an update edge in IDLE with the status clear offers a request carrying the address, data and op of i_dmi_tdr for op 1 and op 2; op 0 and op 3 never offer one |
| `CHK-TDR-OP3-CLEAR` | scoreboard | state | `F-TDR-op-decode` | `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `X-RISCV_DEBUG-dmi-op` |  |  | An update with op 3 sends no DMI request and clears the sticky status, so the next capture reads status 0 |
| `CHK-TDR-PIPELINED-READ` | scoreboard | in-order | `F-TDR-capture-image` | `S-TDR2AXI-0038`, `S-TDR2AXI-0063`, `X-RISCV_DEBUG-dmi-update-capture` |  |  | A read returns its data in the capture of the next scan, whether that scan carries a NOP or the next operation |
| `CHK-TDR-RATIO-INVARIANT` | scoreboard | in-order | `F-TDR-any-clock-ratio` | `S-TDR2AXI-0048` |  |  | The same seeded DMI and SBA sequence gives the same o_dmi_tdr results and the same AXI transactions at every clock ratio of the sweep |
| `CHK-TDR-RESEND-READ` | checker |  | `F-TDR-busy-recovery` | `S-TDR2AXI-0046` |  |  | A resent read reaches the bus once or twice at its address, never more, and the data the debugger receives is the last read's |
| `CHK-TDR-RESULT-LATENCY` | scoreboard | in-order | `F-TDR-result-latency` | `S-TDR2AXI-0018`, `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-resp-to-tdr` | SYNC_STAGES = 3 | At SYNC_STAGES = 3, o_dmi_tdr holds the DM's response, with the bridge back in IDLE, SYNC_STAGES + 2 to SYNC_STAGES + 3 TCK rising edges after the DM's response handshake, and a capture of a read before that window reads busy |
| `CHK-TDR-RESULT-LATENCY-SS4` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0018`, `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-resp-to-tdr-ss4` | SYNC_STAGES = 4 | At SYNC_STAGES = 4, o_dmi_tdr holds the DM's response, with the bridge back in IDLE, within the bounds of T-TDR2AXI-resp-to-tdr-ss4 counted in TCK rising edges after the DM's response handshake, and a capture of a read before that window reads busy |
| `CHK-TDR-ROUND-TRIP-SS3-1TO2` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss3-1to2` | SYNC_STAGES = 3; ratio = 1:2 | At SYNC_STAGES = 3 and i_clk:TCK = 1:2, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss3-1to2, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS3-1TO20` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss3-1to20` | SYNC_STAGES = 3; ratio = 1:20 | At SYNC_STAGES = 3 and i_clk:TCK = 1:20, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss3-1to20, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS3-20TO1` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss3-20to1` | SYNC_STAGES = 3; ratio = 20:1 | At SYNC_STAGES = 3 and i_clk:TCK = 20:1, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss3-20to1, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS3-20TO19` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss3-20to19` | SYNC_STAGES = 3; ratio = 20:19 | At SYNC_STAGES = 3 and i_clk:TCK = 20:19, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss3-20to19, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS3-20TO21` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss3-20to21` | SYNC_STAGES = 3; ratio = 20:21 | At SYNC_STAGES = 3 and i_clk:TCK = 20:21, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss3-20to21, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS4-1TO2` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss4-1to2` | SYNC_STAGES = 4; ratio = 1:2 | At SYNC_STAGES = 4 and i_clk:TCK = 1:2, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss4-1to2, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS4-1TO20` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss4-1to20` | SYNC_STAGES = 4; ratio = 1:20 | At SYNC_STAGES = 4 and i_clk:TCK = 1:20, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss4-1to20, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS4-20TO1` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss4-20to1` | SYNC_STAGES = 4; ratio = 20:1 | At SYNC_STAGES = 4 and i_clk:TCK = 20:1, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss4-20to1, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS4-20TO19` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss4-20to19` | SYNC_STAGES = 4; ratio = 20:19 | At SYNC_STAGES = 4 and i_clk:TCK = 20:19, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss4-20to19, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-ROUND-TRIP-SS4-20TO21` | checker |  | `F-TDR-result-latency` | `S-TDR2AXI-0049`, `X-RISCV_DEBUG-busy-retry` | `T-TDR2AXI-round-trip-ss4-20to21` | SYNC_STAGES = 4; ratio = 20:21 | At SYNC_STAGES = 4 and i_clk:TCK = 20:21, each operation's round trip, counted in TCK rising edges from the Update-DR that starts it with the bridge in IDLE and the sticky status clear to the bridge back in IDLE with o_dmi_tdr holding the DM's response, lies within the bounds of T-TDR2AXI-round-trip-ss4-20to21, and a Capture-DR after its maximum never reads busy |
| `CHK-TDR-STATUS-SVA` | assertion |  | `F-TDR-sticky-busy` | `S-TDR2AXI-0007`, `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `S-TDR2AXI-0042`, `S-TDR2AXI-0043`, `S-TDR2AXI-0045`, `X-RISCV_DEBUG-dmi-status` |  |  | The status turns busy when an update arrives while an operation is pending, when a capture arrives while a read's result is pending, or when the Debug Module answers busy; it stays busy until an update with op 3, and a capture reads status 0 only when the held status is clear and the capture itself raises no busy |
| `CHK-TDR-STICKY-IGNORE` | scoreboard | state | `F-TDR-sticky-busy` | `S-TDR2AXI-0039`, `S-TDR2AXI-0041`, `S-TDR2AXI-0045`, `S-TDR2AXI-0056`, `X-RISCV_DEBUG-dmi-status` |  |  | While the status is busy, updates send no DMI request and start no bus access until op 3 clears it |
| `CHK-TDR-TCK-STOP-HOLD` | scoreboard | state | `F-TDR-tck-stop-hold` | `S-TDR2AXI-0029`, `S-TDR2AXI-0049`, `X-IEEE1149_1-tck-static` | `T-TDR2AXI-resp-to-tdr` | SYNC_STAGES = 3 | At SYNC_STAGES = 3, with TCK stopped, o_dmi_tdr and the bridge's state, status, address and data hold while i_clk runs and the bus access completes; the result reaches o_dmi_tdr only as TCK edges resume, within the bounds of T-TDR2AXI-resp-to-tdr counted in TCK edges |
| `CHK-TDR-TCK-STOP-HOLD-SS4` | scoreboard | state | `F-TDR-tck-stop-hold` | `S-TDR2AXI-0029`, `S-TDR2AXI-0049`, `X-IEEE1149_1-tck-static` | `T-TDR2AXI-resp-to-tdr-ss4` | SYNC_STAGES = 4 | At SYNC_STAGES = 4, with TCK stopped, o_dmi_tdr and the bridge's state, status, address and data hold while i_clk runs and the bus access completes; the result reaches o_dmi_tdr only as TCK edges resume, within the bounds of T-TDR2AXI-resp-to-tdr-ss4 counted in TCK edges |
| `CHK-TDR-UPDATE-EDGE` | scoreboard | in-order | `F-TDR-strobes` | `S-TDR2AXI-0008`, `S-TDR2AXI-0032` |  |  | Each TCK rising edge with i_dmi_tdr_update high is one update of the parallel value: a strobe held for two edges is two updates, and the second reads busy at the next capture while the first is pending |
| `CHK-TDRHOST-CAPTURE-DR` | vip |  |  | `X-IEEE1149_1-capture-dr` |  |  | The capture strobe is high for exactly the one TCK rising edge that ends Capture-DR of this TDR |
| `CHK-TDRHOST-UPDATE-DR` | vip |  |  | `X-IEEE1149_1-update-dr` |  |  | The update-stage value changes only on the falling TCK edge in Update-DR, and the update strobe covers the next rising edge |

## Guards

| Guard | Intent |
| --- | --- |
| `CHK-AXISUB-DRAIN` | At the end of a test the subordinate holds no outstanding or unchecked transaction |
| `CHK-NONVAC` | A test that compared nothing fails |

## Items

| Item | Features | Checks | Seeds | Realizations | Vehicle | Target | Model entries |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `tdr2axi_axi_attr_formal` | `F-AXI-request-attributes`, `F-AXI-axi4-size` | `CHK-AXI-REQ-ATTRS-SVA`, `CHK-AXI-AXI4-SIZE-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-PROT`, `CFG-AXI-CACHE` |
| `tdr2axi_axi_attributes_test` | `F-AXI-request-attributes` | `CHK-AXI-REQ-ATTRS`, `CHK-AXI-REQ-ATTRS-SVA`, `CHK-AXISUB-CACHE-LEGAL`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CFG-AXI-PROT`, `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` |
| `tdr2axi_axi_comb_path_formal` | `F-AXI-no-comb-path` | `CHK-AXI-NO-COMB-PATH-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `CFG-USE-AXI-LITE` |
| `tdr2axi_axi_mgr_formal` | `F-AXI-manager-protocol`, `F-AXI-one-outstanding` | `CHK-AXI-HANDSHAKE-SVA`, `CHK-AXI-ONE-OUTSTANDING-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_axi_protocol_test` | `F-AXI-manager-protocol`, `F-AXI-one-outstanding`, `F-AXI-no-comb-path` | `CHK-AXI-HANDSHAKE-SVA`, `CHK-AXI-ONE-OUTSTANDING`, `CHK-AXI-ONE-OUTSTANDING-SVA`, `CHK-AXI-NO-COMB-PATH`, `CHK-AXISUB-VALID-HELD`, `CHK-AXISUB-PAYLOAD-STABLE`, `CHK-AXISUB-VALID-INDEPENDENT`, `CHK-AXISUB-WRITE-RESP-ORDER`, `CHK-AXISUB-READ-RESP-ORDER`, `CHK-AXISUB-LAST-BEAT`, `CHK-AXISUB-BURST-LENGTH`, `CHK-AXISUB-BOUNDARY-4K`, `CHK-AXISUB-ID-ORDER`, `CHK-AXISUB-CACHE-LEGAL`, `CHK-AXISUB-SIZE-WIDTH`, `CHK-AXISUB-STROBE-LANES`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CLK-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| `tdr2axi_bridge_formal` | `F-TDR-op-decode`, `F-TDR-sticky-busy` | `CHK-TDR-OP-DECODE-SVA`, `CHK-TDR-STATUS-SVA`, `CHK-ENV-TDR-UPDATE-STABLE` | 1 | formal | ports | every build | `CLK-TCK`, `RST-ARST-TCK` |
| `tdr2axi_clock_ratio_test` | `F-TDR-any-clock-ratio`, `F-TDR-result-latency` | `CHK-TDR-RATIO-INVARIANT`, `CHK-TDR-ROUND-TRIP-SS3-20TO1`, `CHK-TDR-ROUND-TRIP-SS3-20TO19`, `CHK-TDR-ROUND-TRIP-SS3-20TO21`, `CHK-TDR-ROUND-TRIP-SS3-1TO2`, `CHK-TDR-ROUND-TRIP-SS3-1TO20`, `CHK-TDR-ROUND-TRIP-SS4-20TO1`, `CHK-TDR-ROUND-TRIP-SS4-20TO19`, `CHK-TDR-ROUND-TRIP-SS4-20TO21`, `CHK-TDR-ROUND-TRIP-SS4-1TO2`, `CHK-TDR-ROUND-TRIP-SS4-1TO20`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 5 | cocotb | ports | every build | `CLK-BUS`, `CLK-TCK`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES`, `T-TDR2AXI-round-trip-ss3-20to1`, `T-TDR2AXI-round-trip-ss3-20to19`, `T-TDR2AXI-round-trip-ss3-20to21`, `T-TDR2AXI-round-trip-ss3-1to2`, `T-TDR2AXI-round-trip-ss3-1to20`, `T-TDR2AXI-round-trip-ss4-20to1`, `T-TDR2AXI-round-trip-ss4-20to19`, `T-TDR2AXI-round-trip-ss4-20to21`, `T-TDR2AXI-round-trip-ss4-1to2`, `T-TDR2AXI-round-trip-ss4-1to20` |
| `tdr2axi_dm_registers_test` | `F-DM-register-map`, `F-DM-hart-unavailable`, `F-DM-abstract-cmd-unsupported`, `F-DM-ndmreset-unconnected` | `CHK-DM-REGISTER-MAP`, `CHK-DM-HART-UNAVAIL`, `CHK-DM-CMDERR`, `CHK-DM-NDMRESET`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dm_resp_formal` | `F-TDR-sticky-busy` | `CHK-TDR-DM-RESP-CODES-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE` |
| `tdr2axi_dm_write_ones_test` | `F-DM-abstract-cmd-unsupported`, `F-SBA-address-width` | `CHK-DM-CMDERR`, `CHK-SBA-SBADDRESS1-ABSENT`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 5 | cocotb | ports | every build | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dmactive_clear_test` | `F-DM-dmactive-clear` | `CHK-DM-DMACTIVE-CLEAR`, `CHK-DM-OP3-KEEPS-DM`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `RST-DMACTIVE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dmactive_inflight_formal` | `F-DM-dmactive-inflight` | `CHK-DM-DMACTIVE-INFLIGHT-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dmactive_inflight_test` | `F-DM-dmactive-inflight`, `F-DM-dmactive-clear` | `CHK-DM-DMACTIVE-INFLIGHT`, `CHK-AXISUB-PAYLOAD-STABLE`, `CHK-NONVAC`, `CHK-DM-DMACTIVE-INFLIGHT-HOLD`, `CHK-DM-DMACTIVE-REARM`, `CHK-DM-DMACTIVE-NO-ABORT`, `CHK-DM-DMACTIVE-CLEAR` | 20 | cocotb | ports | every build | `RST-DMACTIVE`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `RST-ARST-BUS`, `RST-ARST-TCK` |
| `tdr2axi_dmactive_no_bus_formal` | `F-DM-dmactive-no-bus-access` | `CHK-DM-DMACTIVE-NO-BUS-SVA` | 1 | formal | ports | every build | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dmactive_no_bus_test` | `F-DM-dmactive-no-bus-access` | `CHK-DM-DMACTIVE-NO-BUS`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `RST-DMACTIVE`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_dmactive_reset_test` | `F-DM-dmactive-clear` | `CHK-DM-DMACTIVE-CLEAR`, `CHK-DM-DMACTIVE-ALONE`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `RST-DMACTIVE`, `CLK-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_param_checks_test` | `F-CFG-param-checks` | `CHK-CFG-ILLEGAL-PARAMS` | 5 | cocotb | ports | every build | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH`, `CFG-SYNC-STAGES`, `CFG-USE-AXI-LITE` |
| `tdr2axi_reset_during_clear_test` | `F-RESET-async-any-time` | `CHK-RESET-ASYNC-ANY-TIME`, `CHK-RESET-NO-STALE-AXI`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-ENV-FABRIC-RESET`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `tdr2axi_reset_release_test` | `F-RESET-sync-release` | `CHK-RESET-SYNC-RELEASE`, `CHK-RESET-SYNC-RELEASE-SS4`, `CHK-RESET-CDC-CLEAR-20TO1`, `CHK-RESET-CDC-CLEAR-20TO19`, `CHK-RESET-CDC-CLEAR-20TO21`, `CHK-RESET-CDC-CLEAR-1TO2`, `CHK-RESET-CDC-CLEAR-1TO20`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `RST-ARST-BUS`, `RST-ARST-TCK`, `T-TDR2AXI-tck-reset-release`, `T-TDR2AXI-tck-reset-release-ss4`, `T-TDR2AXI-cdc-clear-20to1`, `T-TDR2AXI-cdc-clear-20to19`, `T-TDR2AXI-cdc-clear-20to21`, `T-TDR2AXI-cdc-clear-1to2`, `T-TDR2AXI-cdc-clear-1to20`, `RDC-ARST`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| `tdr2axi_reset_sync_formal` | `F-RESET-sync-release`, `F-RESET-test-mode-bypass` | `CHK-RESET-SYNC-RELEASE-SVA`, `CHK-RESET-TEST-MODE-SVA` | 1 | formal | ports | every build | `RST-ARST-BUS`, `RST-ARST-TCK`, `RST-SCAN`, `T-TDR2AXI-tck-reset-release`, `CFG-SYNC-STAGES` |
| `tdr2axi_reset_test_mode_test` | `F-RESET-test-mode-bypass` | `CHK-RESET-TEST-MODE`, `CHK-NONVAC` | 5 | cocotb | ports | every build | `RST-SCAN`, `CFG-TEST-MODE` |
| `tdr2axi_reset_traffic_test` | `F-RESET-async-any-time` | `CHK-RESET-ASYNC-ANY-TIME`, `CHK-RESET-NO-STALE-AXI`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-ENV-FABRIC-RESET`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `tdr2axi_sba_address_width_test` | `F-SBA-address-width` | `CHK-SBA-ADDRESS-WIDTH`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 5 | cocotb | ports | every build | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_sba_block_test` | `F-SBA-block-write`, `F-SBA-block-read` | `CHK-SBA-BLOCK-WRITE`, `CHK-SBA-BLOCK-READ`, `CHK-SBA-READ-NO-READONDATA`, `CHK-ENV-SBA-BLOCK-READ-STEPS`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 5 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| `tdr2axi_sba_busy_test` | `F-TDR-sticky-busy`, `F-SBA-busy-status`, `F-DM-dmactive-clear` | `CHK-TDR-BUSY-DM`, `CHK-TDR-STICKY-IGNORE`, `CHK-SBA-SBBUSY`, `CHK-SBA-SBBUSYERROR`, `CHK-NONVAC`, `CHK-DM-OP3-KEEPS-DM` | 20 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-CLK-RATIO` |
| `tdr2axi_sba_error_address_test` | `F-SBA-no-increment-after-error`, `F-SBA-block-write` | `CHK-SBA-NO-INC-AFTER-ERROR`, `CHK-NONVAC`, `CHK-SBA-BLOCK-WRITE` | 5 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH` |
| `tdr2axi_sba_errors_test` | `F-SBA-access-errors`, `F-SBA-bus-error` | `CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE`, `CHK-SBA-BUS-ERROR`, `CHK-SBA-ERROR-BLOCKS`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 5 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_sba_single_test` | `F-SBA-single-write`, `F-SBA-single-read`, `F-SBA-data-lanes`, `F-AXI-axi4-size`, `F-AXI-lite-full-width` | `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY`, `CHK-SBA-SINGLE-READ`, `CHK-SBA-NO-READ-ON-ADDR`, `CHK-SBA-DATA-LANES`, `CHK-SBA-SBDATA1-NO-BUS`, `CHK-AXI-AXI4-SIZE`, `CHK-AXI-LITE-FULL-WIDTH`, `CHK-ENV-SBA-WRITE-STEPS`, `CHK-ENV-SBA-READ-STEPS`, `CHK-ENV-SBA-POLL`, `CHK-AXISUB-SIZE-WIDTH`, `CHK-AXISUB-STROBE-LANES`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| `tdr2axi_sba_stress_test` | `F-SBA-single-write`, `F-SBA-single-read`, `F-SBA-data-lanes`, `F-SBA-block-write`, `F-SBA-block-read`, `F-SBA-access-errors`, `F-SBA-bus-error`, `F-AXI-axi4-size`, `F-AXI-lite-full-width`, `F-TDR-busy-recovery` | `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY`, `CHK-SBA-SINGLE-READ`, `CHK-SBA-DATA-LANES`, `CHK-SBA-BLOCK-WRITE`, `CHK-SBA-BLOCK-READ`, `CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE`, `CHK-SBA-BUS-ERROR`, `CHK-SBA-ERROR-BLOCKS`, `CHK-AXI-AXI4-SIZE`, `CHK-AXI-LITE-FULL-WIDTH`, `CHK-TDR-BUSY-RECOVERY`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH`, `CFG-CLK-RATIO` |
| `tdr2axi_smoke_test` | `F-PORT-tdr-interface`, `F-PORT-bus-and-reset`, `F-CFG-bender-target`, `F-DM-version`, `F-SBA-sbcs-fields`, `F-SBA-single-write`, `F-SBA-single-read` | `CHK-PORT-TDR-LIST`, `CHK-PORT-BUS-LIST`, `CHK-CFG-BENDER-TARGET`, `CHK-DM-VERSION`, `CHK-SBA-SBCS-FIELDS`, `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-SINGLE-READ`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CLK-BUS`, `CLK-TCK`, `RST-ARST-BUS`, `RST-ARST-TCK`, `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| `tdr2axi_tdr_busy_capture_test` | `F-TDR-sticky-busy` | `CHK-TDR-BUSY-CAPTURE`, `CHK-TDR-STICKY-IGNORE`, `CHK-TDR-NEVER-FAILED`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `CLK-BUS`, `CLK-TCK`, `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `tdr2axi_tdr_busy_recovery_test` | `F-TDR-busy-recovery` | `CHK-TDR-BUSY-RECOVERY`, `CHK-TDR-RESEND-READ`, `CHK-ENV-READONDATA-IDLE`, `CHK-NONVAC` | 5 | cocotb | ports | every build | `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO` |
| `tdr2axi_tdr_busy_update_test` | `F-TDR-sticky-busy` | `CHK-TDR-BUSY-UPDATE`, `CHK-TDR-STICKY-IGNORE`, `CHK-TDR-NEVER-FAILED`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `CLK-BUS`, `CFG-CLK-RATIO` |
| `tdr2axi_tdr_ops_test` | `F-TDR-op-decode`, `F-TDR-capture-image` | `CHK-TDR-OP-DECODE`, `CHK-TDR-OP3-CLEAR`, `CHK-TDR-CAPTURE-IMAGE`, `CHK-ENV-TDR-UPDATE-STABLE`, `CHK-TDRHOST-CAPTURE-DR`, `CHK-TDRHOST-UPDATE-DR`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `CLK-TCK`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| `tdr2axi_tdr_pipelined_test` | `F-TDR-capture-image`, `F-SBA-block-read` | `CHK-TDR-PIPELINED-READ`, `CHK-TDR-CAPTURE-IMAGE`, `CHK-SBA-BLOCK-READ`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | 20 | cocotb | ports | every build | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO` |
| `tdr2axi_tdr_result_latency_test` | `F-TDR-result-latency` | `CHK-TDR-RESULT-LATENCY`, `CHK-TDR-RESULT-LATENCY-SS4`, `CHK-NONVAC` | 5 | cocotb | ports | every build | `T-TDR2AXI-resp-to-tdr`, `T-TDR2AXI-resp-to-tdr-ss4`, `CDC-RESP`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| `tdr2axi_tdr_strobes_test` | `F-TDR-strobes` | `CHK-TDR-UPDATE-EDGE`, `CHK-TDR-CAPTURE-EDGE`, `CHK-TDRHOST-CAPTURE-DR`, `CHK-TDRHOST-UPDATE-DR`, `CHK-ENV-TDR-UPDATE-STABLE`, `CHK-NONVAC` | 5 | cocotb | ports | every build | `CLK-TCK`, `CFG-CLK-RATIO` |
| `tdr2axi_tdr_tck_stop_test` | `F-TDR-tck-stop-hold` | `CHK-TDR-TCK-STOP-HOLD`, `CHK-TDR-TCK-STOP-HOLD-SS4`, `CHK-NONVAC` | 20 | cocotb | ports | every build | `CLK-TCK`, `CDC-RESP`, `T-TDR2AXI-resp-to-tdr`, `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| `tdr2axi_trst_test` | `F-RESET-trst-independent` | `CHK-RESET-TRST-INDEPENDENT`, `CHK-NONVAC` | 5 | cocotb | ports | every build | `RST-TRST`, `CLK-TCK` |

## Registers Under Verification

`tdr2axi` adds no registers; the Debug Module registers it exposes are those of
`R-RISCV_DEBUG`, at the addresses S-TDR2AXI-0062 gives. Registers whose behavior
`SbaRefModel` models beyond read-back:

| Register | Behavior | Feature |
|---|---|---|
| `dmcontrol` | `dmactive` = 0 holds every other DM register at reset and starts no bus access, while `sbcs`, `sbaddress` and `sbdata` keep their values until an access in flight completes; `ndmreset` has no effect; `haltreq` and `resumereq` change nothing | `F-DM-dmactive-clear`, `F-DM-dmactive-no-bus-access`, `F-DM-dmactive-inflight`, `F-DM-ndmreset-unconnected`, `F-DM-hart-unavailable` |
| `dmstatus` | `version` 2; `authenticated` 1 and `authbusy` 0, with no authentication; hart unavailable | `F-DM-version`, `F-DM-hart-unavailable` |
| `abstractcs`, `command` | every command sets `cmderr` = 2; `cmderr` is write-1-to-clear | `F-DM-abstract-cmd-unsupported` |
| `sbcs` | `sbversion`, `sbasize` and `sbaccess` bits from the build; `sbbusy`, write-1-to-clear `sbbusyerror` and `sberror`; `sberror` blocks accesses | `F-SBA-sbcs-fields`, `F-SBA-busy-status`, `F-SBA-access-errors`, `F-SBA-bus-error` |
| `sbaddress0`, `sbaddress1` | a write of `sbaddress0` reads when `sbreadonaddr` is set; auto-increment after each successful access; `sbaddress1` absent at `AXI_ADDR_WIDTH` 32 or less, reading 0 and ignoring a write made with `sbbusy` and `sbbusyerror` clear; a write made while `sbbusy` or `sbbusyerror` is set collides | `F-SBA-single-read`, `F-SBA-block-write`, `F-SBA-block-read`, `F-SBA-no-increment-after-error`, `F-SBA-address-width` |
| `sbdata0`, `sbdata1` | a write of `sbdata0` writes; a read of `sbdata0` reads when `sbreadondata` is set; `sbdata1` has no side effect, and at `AXI_DATA_WIDTH` 32, where the debug specification has no `sbdata1`, it keeps what is written until a bus read, which the model does not compare (`R-RISCV_DEBUG` profile entry `departure_absent`) | `F-SBA-single-write`, `F-SBA-block-read`, `F-SBA-data-lanes` |

## Scenario Descriptions

### `tdr2axi_axi_attr_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-AXI-request-attributes` (P0), `F-AXI-axi4-size` (P0) |
| Job | `formal/sby/tdr2axi_axi_attr_formal.sby`, SymbiYosys |
| Checks | `CHK-AXI-REQ-ATTRS-SVA`, `CHK-AXI-AXI4-SIZE-SVA` |
| Model entries | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-PROT`, `CFG-AXI-CACHE` |

**Harness cut.** `tdr2axi` cut at the outputs of `u_dm_sba` (`mem_req`, `mem_addr`, `mem_we`, `mem_wdata`, `mem_be`) and at `sbaccess` from `u_dm_csrs`, which become free inputs; a request is assumed to hold its fields until `mem_gnt`, the adapter's input rule. The AXI adapter and the assignments of `tdr2axi.sv` that set `AxPROT` from `AXI_PROT`, `AxCACHE` from `AXI_CACHE` and the AXI4 `AxSIZE` from `sbaccess` are in the proof as built, so a wiring fault in `tdr2axi.sv` fails it. `i_axi_rsp` is driven by an AXI subordinate assume.

**Reduced width.** `AXI_ADDR_WIDTH` = 16 with `AXI_DATA_WIDTH` = 32 and 64 and `USE_AXI_LITE` = 0 and 1, each run at `AXI_PROT` = 3'b010 and 3'b101 and at `AXI_CACHE` = 4'b0000 and 4'b1111.

**Properties.** `CHK-AXI-REQ-ATTRS-SVA` (`AxPROT` = `AXI_PROT`, `AxCACHE` = `AXI_CACHE` on AXI4, ID and user 0) and `CHK-AXI-AXI4-SIZE-SVA` (`AxSIZE` = `sbaccess` on AXI4), from `sva/tdr2axi_axi_mgr_sva.sv`. Each parameter value is its own run, so wiring that matches only the default value fails at the other.

### `tdr2axi_axi_attributes_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-AXI-request-attributes` (P0) |
| Test | `cocotb/tests/test_tdr2axi_axi.py`, built on `Tdr2axiTb` |
| Randomized | Access size and direction |
| Checks | `CHK-AXI-REQ-ATTRS`, `CHK-AXI-REQ-ATTRS-SVA`, `CHK-AXISUB-CACHE-LEGAL`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-AXI-PROT`, `CFG-AXI-CACHE`, `CFG-USE-AXI-LITE` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Reads and writes of every size on each build.

**Expected results.** Every request carries `AXI_PROT`, `AXI_CACHE` on AXI4, ID 0 and user 0, compared with the build's parameters (`CHK-AXI-REQ-ATTRS`, S-TDR2AXI-0016, S-TDR2AXI-0017, S-TDR2AXI-0058), and `AxCACHE` is a legal encoding. The comparison tells a parameter from a constant only on a build whose `AXI_PROT` differs from 3'b010 or whose `AXI_CACHE` differs from 4'b0000, so the evidence for `F-AXI-request-attributes` counts from those builds; `axi_attr_cg.cp_prot` and `axi_attr_cg.cp_cache` record which values ran. `tdr2axi_axi_attr_formal` proves the same wiring at both values of each parameter.

### `tdr2axi_axi_comb_path_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-AXI-no-comb-path` (P1) |
| Job | `formal/sby/tdr2axi_axi_comb_path_formal.sby`, SymbiYosys |
| Checks | `CHK-AXI-NO-COMB-PATH-SVA` |
| Model entries | `CLK-BUS`, `CFG-USE-AXI-LITE` |

**Harness cut.** The `tdr2axi_axi_mgr_formal` harness in a multiclock model: `i_clk` is a free clock enable on the global clock, and `i_axi_rsp` may change on any global tick.

**Reduced width.** `AXI_ADDR_WIDTH` = 16, `AXI_DATA_WIDTH` = 32, `USE_AXI_LITE` = 0 and 1.

**Properties.** `CHK-AXI-NO-COMB-PATH-SVA`: `o_axi_req` is stable on every tick that is not an `i_clk` rising edge.

### `tdr2axi_axi_mgr_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-AXI-manager-protocol` (P0), `F-AXI-one-outstanding` (P0) |
| Job | `formal/sby/tdr2axi_axi_mgr_formal.sby`, SymbiYosys |
| Checks | `CHK-AXI-HANDSHAKE-SVA`, `CHK-AXI-ONE-OUTSTANDING-SVA` |
| Model entries | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |

**Harness cut.** `tdr2axi` with `u_dmi_tdr_bridge` removed: the `i_clk`-side DMI request is driven by free inputs under a valid/ready assume, so `dm_csrs`, `dm_sba`, the AXI adapter and every assignment of `tdr2axi.sv` are in the proof as built. `i_axi_rsp` is driven by an AXI subordinate assume, the internal reset is low on the first cycle only, and `dmactive` is assumed to stay 1 once set, so this job proves the handshake rules apart from the `dmactive` behaviors.

**Reduced width.** `AXI_ADDR_WIDTH` = 16 with `AXI_DATA_WIDTH` = 32 and 64, each with `USE_AXI_LITE` = 0 and 1.

**Properties.** `CHK-AXI-HANDSHAKE-SVA` and `CHK-AXI-ONE-OUTSTANDING-SVA`, from `sva/tdr2axi_axi_mgr_sva.sv`.

### `tdr2axi_axi_protocol_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-AXI-manager-protocol` (P0), `F-AXI-one-outstanding` (P0), `F-AXI-no-comb-path` (P1) |
| Test | `cocotb/tests/test_tdr2axi_axi.py`, built on `Tdr2axiTb` |
| Randomized | Per-channel READY delay, R and B latency, W before AW, mid-cycle changes of `i_axi_rsp`, access mix |
| Checks | `CHK-AXI-HANDSHAKE-SVA`, `CHK-AXI-ONE-OUTSTANDING`, `CHK-AXI-ONE-OUTSTANDING-SVA`, `CHK-AXI-NO-COMB-PATH`, `CHK-AXISUB-VALID-HELD`, `CHK-AXISUB-PAYLOAD-STABLE`, `CHK-AXISUB-VALID-INDEPENDENT`, `CHK-AXISUB-WRITE-RESP-ORDER`, `CHK-AXISUB-READ-RESP-ORDER`, `CHK-AXISUB-LAST-BEAT`, `CHK-AXISUB-BURST-LENGTH`, `CHK-AXISUB-BOUNDARY-4K`, `CHK-AXISUB-ID-ORDER`, `CHK-AXISUB-CACHE-LEGAL`, `CHK-AXISUB-SIZE-WIDTH`, `CHK-AXISUB-STROBE-LANES`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CLK-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** A seeded SBA access mix against a subordinate whose readys, response latencies and AW/W order are drawn from the seed, with READY held low until VALID on some accesses and `i_axi_rsp` fields toggled between `i_clk` edges.

**Expected results.** The AXI rules hold on every channel (`CHK-AXI-HANDSHAKE-SVA` under VCS and the `AxiSub` rules); one transaction is outstanding and each is one beat (`CHK-AXI-ONE-OUTSTANDING`, S-TDR2AXI-0012, S-TDR2AXI-0058); `o_axi_req` changes only on `i_clk` edges (`CHK-AXI-NO-COMB-PATH`).

### `tdr2axi_bridge_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-op-decode` (P0), `F-TDR-sticky-busy` (P0) |
| Job | `formal/sby/tdr2axi_bridge_formal.sby`, SymbiYosys |
| Checks | `CHK-TDR-OP-DECODE-SVA`, `CHK-TDR-STATUS-SVA`, `CHK-ENV-TDR-UPDATE-STABLE` |
| Model entries | `CLK-TCK`, `RST-ARST-TCK` |

**Harness cut.** `dmi_tdr_bridge` alone, on TCK only. Both `cdc_2phase_clearable` instances are cut: the request side's ready and the response side's valid, data and code become free inputs, constrained so a response follows an accepted request at most once and its code is success or busy. The code constraint is the property `tdr2axi_dm_resp_formal` proves of `dm_csrs`, whose response the CDC carries unchanged; the other constraints abstract the CDC clear sequence and every `i_clk` timing.

**Reduced width.** Full: the TDR is 41 bits and the bridge has no width parameter.

**Properties.** `CHK-TDR-OP-DECODE-SVA` and `CHK-TDR-STATUS-SVA`, with `CHK-ENV-TDR-UPDATE-STABLE` as an assume. The never-failed status follows from the status rules and the response-code constraint, which `CHK-TDR-DM-RESP-CODES-SVA` discharges.

### `tdr2axi_clock_ratio_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-any-clock-ratio` (P1), `F-TDR-result-latency` (P2) |
| Test | `cocotb/tests/test_tdr2axi_clock.py`, built on `Tdr2axiTb` |
| Randomized | `i_clk` and TCK periods with `i_clk` from 20 times faster than TCK to 8 times slower (`--ratio random`), or each named `CFG-CLK-RATIO` point, 20:1, 20:19, 20:21, 1:2 and 1:20 at TCK = 20000 ps (`CLK_PERIOD_PS` 1000, 19000, 21000, 40000 and 400000); the phase between them in both modes |
| Checks | `CHK-TDR-RATIO-INVARIANT`, `CHK-TDR-ROUND-TRIP-SS3-20TO1`, `CHK-TDR-ROUND-TRIP-SS3-20TO19`, `CHK-TDR-ROUND-TRIP-SS3-20TO21`, `CHK-TDR-ROUND-TRIP-SS3-1TO2`, `CHK-TDR-ROUND-TRIP-SS3-1TO20`, `CHK-TDR-ROUND-TRIP-SS4-20TO1`, `CHK-TDR-ROUND-TRIP-SS4-20TO19`, `CHK-TDR-ROUND-TRIP-SS4-20TO21`, `CHK-TDR-ROUND-TRIP-SS4-1TO2`, `CHK-TDR-ROUND-TRIP-SS4-1TO20`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CLK-BUS`, `CLK-TCK`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES`, `T-TDR2AXI-round-trip-ss3-20to1`, `T-TDR2AXI-round-trip-ss3-20to19`, `T-TDR2AXI-round-trip-ss3-20to21`, `T-TDR2AXI-round-trip-ss3-1to2`, `T-TDR2AXI-round-trip-ss3-1to20`, `T-TDR2AXI-round-trip-ss4-20to1`, `T-TDR2AXI-round-trip-ss4-20to19`, `T-TDR2AXI-round-trip-ss4-20to21`, `T-TDR2AXI-round-trip-ss4-1to2`, `T-TDR2AXI-round-trip-ss4-1to20` |
| Seeds and target | 5 per build and ratio; every build target, each at all five named ratios and at random ratios |

**Stimulus.** One seeded mix of single and block SBA accesses and DMI register accesses, replayed at each ratio of the sweep, random or named, with the Run-Test/Idle count the ratio needs. Each operation starts with the bridge in IDLE and a clear status, and the bench counts TCK rising edges from its Update-DR to the bridge's return to IDLE. `run_sim.py --test tdr2axi_clock_ratio_test --target all --reseed` runs it on every build at the five named ratios and at random ratios, five seeds each, as the signoff group does.

**Expected results.** Results and AXI transactions are the same at every ratio, random or named (`CHK-TDR-RATIO-INVARIANT`, S-TDR2AXI-0048). At a named ratio each round trip lies within the bounds of the round-trip item for the run's `SYNC_STAGES` and ratio, such as `T-TDR2AXI-round-trip-ss3-1to2`, and a capture after that item's maximum never reads busy (`CHK-TDR-ROUND-TRIP-SS3-20TO1` to `CHK-TDR-ROUND-TRIP-SS4-1TO20`, S-TDR2AXI-0049); a round-trip check is required only at the `SYNC_STAGES` value and named ratio its Applies column names, and a random-ratio run requires none.

### `tdr2axi_dm_registers_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-register-map` (P0), `F-DM-hart-unavailable` (P2), `F-DM-abstract-cmd-unsupported` (P2), `F-DM-ndmreset-unconnected` (P2) |
| Test | `cocotb/tests/test_tdr2axi_dm.py`, built on `Tdr2axiTb` |
| Randomized | Register order, written values, DMI addresses outside the map |
| Checks | `CHK-DM-REGISTER-MAP`, `CHK-DM-HART-UNAVAIL`, `CHK-DM-CMDERR`, `CHK-DM-NDMRESET`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Write a distinct value to each register of the map and read every register back; write `command` with random abstract commands and clear `cmderr`; write `haltreq`, `resumereq` and `hartsel` in `dmcontrol`; set and clear `ndmreset` with SBA registers and subordinate memory holding known values; read and write DMI addresses outside the map.

**Expected results.** Each address of S-TDR2AXI-0062 behaves as its register, and a write changes only its own register (`CHK-DM-REGISTER-MAP`). `dmstatus` reports the hart unavailable throughout (`CHK-DM-HART-UNAVAIL`, S-TDR2AXI-0009). Every command sets `cmderr` = 2 and starts nothing (`CHK-DM-CMDERR`, S-TDR2AXI-0010). `ndmreset` changes nothing (`CHK-DM-NDMRESET`, S-TDR2AXI-0011). No access in this item reaches the bus.

### `tdr2axi_dm_resp_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-sticky-busy` (P0) |
| Job | `formal/sby/tdr2axi_dm_resp_formal.sby`, SymbiYosys |
| Checks | `CHK-TDR-DM-RESP-CODES-SVA` |
| Model entries | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE` |

**Harness cut.** `tdr2axi` with `u_dmi_tdr_bridge` removed, so the `i_clk`-side DMI request and its response ready are free inputs under a valid/ready assume. `dm_csrs`, `dm_sba` and the AXI adapter are as built, DMI writes of `dmcontrol` are free, `i_axi_rsp` is driven by an AXI subordinate assume with any response code, and the internal reset is low on the first cycle only.

**Reduced width.** `AXI_ADDR_WIDTH` = 16, `AXI_DATA_WIDTH` = 32, `USE_AXI_LITE` = 0 and 1.

**Properties.** `CHK-TDR-DM-RESP-CODES-SVA`: every DMI response of `dm_csrs` carries code 0 or 3, whatever the request and the bus answer. It discharges the response-code constraint of `tdr2axi_bridge_formal`.

### `tdr2axi_dm_write_ones_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-abstract-cmd-unsupported` (P2), `F-SBA-address-width` (P1) |
| Test | `cocotb/tests/test_tdr2axi_dm.py`, built on `Tdr2axiTb` |
| Randomized | Order of the `command` and `sbaddress1` sequences, the address in `sbaddress0`, the size of the read after the `sbaddress1` write |
| Checks | `CHK-DM-CMDERR`, `CHK-SBA-SBADDRESS1-ABSENT`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 5 per build; every build target and the four bring-up builds |

**Stimulus.** Write `command` = 0xFFFFFFFF, read `abstractcs`, clear `cmderr` by writing 0x700 to `abstractcs`, write `command` = 0, read `abstractcs` and clear `cmderr` again, so every `command` field rises and falls. On a build whose `AXI_ADDR_WIDTH` is 32 or less, write `sbaddress0` with an address in range, write `sbaddress1` = 0xFFFFFFFF with `sbbusy` and `sbbusyerror` clear, read `sbaddress1` and `sbaddress0`, and run one single read at the address `sbaddress0` holds. On the wider builds the test writes no `sbaddress1` bit at or above `AXI_ADDR_WIDTH`, which S-TDR2AXI-0014 leaves open from 33 to 63 bits.

**Expected results.** Each `command` write, all ones and zero alike, sets `cmderr` = 2 and starts nothing, and writing ones to `cmderr` clears it (`CHK-DM-CMDERR`, S-TDR2AXI-0010). At 32 bits or less, with `sbbusy` and `sbbusyerror` clear, `sbaddress1` reads 0 after the write, `sbaddress0` reads what was written, and the read's `ARADDR` is that address (`CHK-SBA-SBADDRESS1-ABSENT`, S-TDR2AXI-0014, X-RISCV_DEBUG-sbaddress-regs: 0.13.2 3.12.20 makes `sbaddress1` absent below `sbasize` 33, and 3.12 has an absent register read 0 and a write of it have no effect). The DM meets the write half only with both clear: a write of the absent `sbaddress1` during an access collides like any other (`R-RISCV_DEBUG` profile entry `departure_absent`), which `CHK-SBA-SBBUSYERROR` grades in `tdr2axi_sba_busy_test`. No other access in this item reaches the bus.

### `tdr2axi_dmactive_clear_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-clear` (P0) |
| Test | `cocotb/tests/test_tdr2axi_dmactive.py`, built on `Tdr2axiTb` |
| Randomized | Register values written before the clear, which loop sets `sbautoincrement` and which raises `sbbusyerror`, the access held for the collision and the colliding access, the number of op 3 scans that clear the sticky busy, the writes while `dmactive` = 0 and whether op 3 follows them, the order of the register reads, the access after `dmactive` = 1 |
| Checks | `CHK-DM-DMACTIVE-CLEAR`, `CHK-DM-OP3-KEEPS-DM`, `CHK-NONVAC` |
| Model entries | `RST-DMACTIVE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** In each of two loops, write known values to the SBA registers, with `sbautoincrement` set in one loop, and a `command` that sets `cmderr` = 2, raise the bridge's sticky busy status, clear it with op 3 and read the registers back. In one loop, start a seeded single read or write that the subordinate holds at READY, collide with it by a seeded access of those X-RISCV_DEBUG-sbbusyerror names, recover the busy answer with op 3, release the access, poll `sbcs` until `sbbusy` reads 0, confirming `sbbusyerror` = 1, and write the known `sbcs` value again. Then, in each loop, write `dmcontrol` = 0 and read `sbcs` before any other access, make a seeded subset of register writes that start no access, followed by op 3 in a seeded share of the loops, read the registers and `sbcs`, write `dmcontrol` = 1, read them again and run an access.

**Expected results.** While `dmactive` = 0 the registers read their reset values, `sbcs` compared only in `sbbusy` and `sbbusyerror`, which read 0 also after a fall with `sbbusyerror` = 1, and after `dmactive` = 1 `sbcs` reads its reset value, `sbautoincrement` included, and the DM works (`CHK-DM-DMACTIVE-CLEAR`, S-TDR2AXI-0056, S-TDR2AXI-0064); op 3 changes no DM register (`CHK-DM-OP3-KEEPS-DM`).

### `tdr2axi_dmactive_inflight_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-inflight` (P0) |
| Job | `formal/sby/tdr2axi_dmactive_inflight_formal.sby`, SymbiYosys |
| Checks | `CHK-DM-DMACTIVE-INFLIGHT-SVA` |
| Model entries | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |

**Harness cut.** The `tdr2axi_axi_mgr_formal` cut without its `dmactive` assume: DMI writes of `dmcontrol` are free, so `dmactive` may fall and rise on any `i_clk` cycle with an access in flight. TCK and `u_dmi_tdr_bridge`, with both `cdc_2phase_clearable` instances inside it, lie outside the cut, so the proof is on `i_clk` alone: the free DMI port reaches every request order the bridge can deliver. The internal reset is low on the first cycle only, so the CDC clear sequence after a reset plays no part; reset with an access in flight is `tdr2axi_reset_traffic_test`'s.

**Reduced width.** `AXI_ADDR_WIDTH` = 16 with `AXI_DATA_WIDTH` = 32 and 64, each with `USE_AXI_LITE` = 0 and 1.

**Properties.** `CHK-DM-DMACTIVE-INFLIGHT-SVA`: the request fields hold while VALID is high and READY low, across any `dmactive` change. The job proves this property alone, so a shallower counterexample to another property cannot end the run before it; a counterexample becomes a directed seed of `tdr2axi_dmactive_inflight_test`.

### `tdr2axi_dmactive_inflight_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-inflight` (P0), `F-DM-dmactive-clear` (P0) |
| Test | `cocotb/tests/test_tdr2axi_dmactive.py`, built on `Tdr2axiTb` |
| Randomized | Channel held (AR, AW, W, R, B), size, address, bus type, response (OKAY, SLVERR, DECERR), `sbautoincrement` and the other `sbcs` read-write fields, the order of the seven loops, the direction of the response-held Re-arm loop and of both No-abort loops, which Re-arm read is answered OKAY and which other Re-arm loop SLVERR or DECERR, which Hold loop collides before the fall and with which access, the order and values of the Hold scenario's DMI accesses, the No-abort wait |
| Checks | `CHK-DM-DMACTIVE-INFLIGHT`, `CHK-AXISUB-PAYLOAD-STABLE`, `CHK-NONVAC`, `CHK-DM-DMACTIVE-INFLIGHT-HOLD`, `CHK-DM-DMACTIVE-REARM`, `CHK-DM-DMACTIVE-NO-ABORT`, `CHK-DM-DMACTIVE-CLEAR` |
| Model entries | `RST-DMACTIVE`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `RST-ARST-BUS`, `RST-ARST-TCK` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** The `RST-DMACTIVE` scenarios with a system-bus access in flight, in seven loops of a seeded order: Hold with a read and with a write, each held at READY; Re-arm with a read and a write held at READY and one access of seeded direction held at its R or B response; No-abort with one access held at READY and one held at its response, each of seeded direction. Each loop writes `command` first, so `cmderr` is 2 when `dmactive` falls, then starts an SBA access at a nonzero address with at least one `sbcs` read-write field away from its reset value (an `sbaccess` other than 2, or `sbautoincrement`, `sbreadonaddr` or `sbreadondata` set), holds it with READY or the response withheld and writes `dmcontrol` = 0; a read's data differs from every register's reset value. In one of the two Hold loops, chosen by the seed, a seeded access of those X-RISCV_DEBUG-sbbusyerror names collides with the held access before the fall, op 3 recovers the busy answer, and an `sbcs` read before `dmcontrol` = 0 confirms `sbbusyerror` = 1; the other Hold loop reaches the fall with `sbbusyerror` = 0. Hold: read `sbcs`, `sbaddress0`, `sbaddress1` and `abstractcs`; in a seeded order write `sbaddress0`, `sbdata0` and `sbcs` with values other than the held ones, the `sbcs` write also writing 1 to `sbbusyerror` and the `sberror` bits, and read `sbdata0`, recovering the busy status each may raise with op 3; read `sbcs`, `sbaddress0` and `sbaddress1` again; release the access with a seeded response, poll `sbcs` until `sbbusy` reads 0, read `sbaddress0`, `sbaddress1`, `sbdata0` and `sbdata1` with `dmactive` still 0, then write `dmcontrol` = 1 and read `sbcs`, `sbaddress0`, `sbaddress1`, `sbdata0`, `sbdata1` and `abstractcs`. Re-arm: read `abstractcs`, `sbcs`, `sbaddress0` and `sbaddress1`, write `dmcontrol` = 1 while the access is held, at READY or at its R or B response, wait until the DMI request observables show the DM has taken that write and at least two more `i_clk` edges have passed, so the response never arrives in the cycle the DM takes it, release the access with its seeded response and `sbautoincrement` set or clear, and read the same registers once `sbbusy` reads 0. The seed picks one Re-arm read to answer OKAY and one other Re-arm loop to answer SLVERR or DECERR. No abort: hold the access, write `dmcontrol` = 0, scan op 3, read `sbcs`, keep the access held for a seeded wait longer than any stall the subordinate otherwise applies, read `sbcs` again, then assert `i_arst_n` with the subordinate reset alongside, release it, recover a busy before the bridge is ready with op 3, and read the DM registers. Outside Hold's seeded accesses and its pre-fall collision the test reads only `sbcs`, `sbaddress0`, `sbaddress1` and `abstractcs` while an access is held; the DM never answers those busy.

**Expected results.** The held request keeps its address, size, data and strobes until the handshake and completes once (`CHK-DM-DMACTIVE-INFLIGHT`, `CHK-AXISUB-PAYLOAD-STABLE`, S-TDR2AXI-0090). `abstractcs` reads `cmderr` = 0 while `dmactive` = 0 and after it is set again, although an access is in flight (`CHK-DM-DMACTIVE-CLEAR`, S-TDR2AXI-0056, X-RISCV_DEBUG-dmactive). While `dmactive` = 0 holds the access, `sbcs` reads the value it had before the clear, with `sbbusy` = 1 and `sbbusyerror` as it was then, and `sbaddress0` and `sbaddress1` the access's address, before and after Hold's seeded accesses: none of those changes a held value or `sbbusyerror`, and their DMI answers are not compared, since Debug 1.0 lets any access fail while `dmactive` = 0 (the RTL answers busy, `R-RISCV_DEBUG` profile entry `dm_reset`). In the Hold loop that collided, `sbbusyerror` reads 1 throughout the hold, after the `sbcs` write that writes 1 to it as well: the DM answers that write busy, which skips the W1C update (`src/dm_csrs.sv:512-519`), and with `dmactive` = 0 it keeps `sbcs` whatever the write would set (`:688`, `:712-716`). Once the access completes `sbbusy` and `sbbusyerror` read 0 and, with `dmactive` still 0, `sbaddress0`, `sbaddress1`, `sbdata0` and `sbdata1` read their reset values: an `sbcs` read returns the register itself (`:382-384`), which takes its reset value at the first edge with `sbbusy_i` low, so both fields leave the held value together (`:713`). After `dmactive` = 1 every SBA register reads its reset value, `sbbusyerror` = 0 included, the read data and error response never landing (`CHK-DM-DMACTIVE-INFLIGHT-HOLD`, S-TDR2AXI-0090). Set again before the response, whether the access waits for READY or for its response, no register resets and the result lands: `sbdata0` holds the read data, `sberror` reads 2 after SLVERR or DECERR, and `sbaddress` advances only after a successful access with `sbautoincrement` set (`CHK-DM-DMACTIVE-REARM`, S-TDR2AXI-0091). A `dmcontrol` write the DM takes in the cycle of the response neither resets the registers nor lands the result; S-TDR2AXI-0091 leaves that cycle open, so the test stays out of it and the check grades no re-arm there. In the Re-arm loop held at its response, RREADY or BREADY stays high from before `dmcontrol` = 0 until the release (`CHK-DM-DMACTIVE-NO-ABORT`, S-TDR2AXI-0092). Neither `dmactive` = 0 nor op 3 ends a held access: ARVALID, AWVALID or WVALID stays high for an AR, AW or W hold, RREADY or BREADY for an R or B hold, and `sbcs` reads `sbbusy` = 1 after op 3 and after the wait, until `i_arst_n`, after which no VALID is high and the DM reads its reset values, `sbcs` compared only in `sbbusy` and `sbbusyerror`, both 0 (`CHK-DM-DMACTIVE-NO-ABORT`, `CHK-DM-DMACTIVE-CLEAR`, S-TDR2AXI-0092, S-TDR2AXI-0064). Each simulator reports a payload change at a different place: under VCS the adapter's own assertion in `axi_lite_from_mem` stops the run with `$fatal` ("mem_addr_i has to be stable") before the bench's checks report; under Verilator, which does not compile that assertion, `CHK-AXISUB-PAYLOAD-STABLE` reports it.

### `tdr2axi_dmactive_no_bus_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-no-bus-access` (P0) |
| Job | `formal/sby/tdr2axi_dmactive_no_bus_formal.sby`, SymbiYosys |
| Checks | `CHK-DM-DMACTIVE-NO-BUS-SVA` |
| Model entries | `CLK-BUS`, `RST-ARST-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |

**Harness cut.** The `tdr2axi_dmactive_inflight_formal` cut: `tdr2axi` without `u_dmi_tdr_bridge`, with DMI writes of `dmcontrol` free, on `i_clk` alone, and the internal reset low on the first cycle only.

**Reduced width.** `AXI_ADDR_WIDTH` = 16 with `AXI_DATA_WIDTH` = 32 and 64, each with `USE_AXI_LITE` = 0 and 1.

**Properties.** `CHK-DM-DMACTIVE-NO-BUS-SVA`: no ARVALID or AWVALID rises while `dmactive` is 0. A counterexample becomes a directed seed of `tdr2axi_dmactive_no_bus_test`.

### `tdr2axi_dmactive_no_bus_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-no-bus-access` (P0) |
| Test | `cocotb/tests/test_tdr2axi_dmactive.py`, built on `Tdr2axiTb` |
| Randomized | Order of the writes, data, the further registers written, bus type |
| Checks | `CHK-DM-DMACTIVE-NO-BUS`, `CHK-NONVAC` |
| Model entries | `RST-DMACTIVE`, `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Write `dmcontrol` = 0. Every seed then writes `sbdata0`, and adds, in a seeded order, writes of `sbaddress0` and of `sbcs` with `sbreadonaddr` set.

**Expected results.** No ARVALID or AWVALID rises while `dmactive` = 0 (`CHK-DM-DMACTIVE-NO-BUS`, S-TDR2AXI-0056, S-TDR2AXI-0064, S-TDR2AXI-0089).

### `tdr2axi_dmactive_reset_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-DM-dmactive-clear` (P0) |
| Test | `cocotb/tests/test_tdr2axi_dmactive.py`, built on `Tdr2axiTb` |
| Randomized | Register values written before the clear, idle time before and after it, which scenario runs first, bus type |
| Checks | `CHK-DM-DMACTIVE-CLEAR`, `CHK-DM-DMACTIVE-ALONE`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `RST-DMACTIVE`, `CLK-BUS`, `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Two of the `RST-DMACTIVE` scenarios; the third, with a system-bus access in flight, is `tdr2axi_dmactive_inflight_test`'s. Idle: write the SBA registers, wait until `sbbusy` reads 0, write `dmcontrol` = 0, read the registers, write `dmcontrol` = 1 and run an access. Alone: with no access in flight, write `dmcontrol` = 0, read `dmcontrol`, write `dmcontrol` = 1 and read `dmcontrol` and `sbcs`, with no SBA register access or bus access between the two writes and no other reset.

**Expected results.** In the idle scenario the registers read their reset values while `dmactive` = 0 and the DM works after it is set again (`CHK-DM-DMACTIVE-CLEAR`, S-TDR2AXI-0056, S-TDR2AXI-0064). In the alone scenario no AR or AW is issued, memory is unchanged, the bridge status stays 0, `dmcontrol` reads `dmactive` = 0 and then 1, and `sbcs` reads its reset value (`CHK-DM-DMACTIVE-ALONE`).

### `tdr2axi_param_checks_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-CFG-param-checks` (P2) |
| Test | `scripts/run_sim.py`, from the entry's `build_cases` in `testlists/all.toml`; no cocotb module |
| Randomized | Illegal parameter value and dimension per seed |
| Checks | `CHK-CFG-ILLEGAL-PARAMS` |
| Model entries | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH`, `CFG-SYNC-STAGES`, `CFG-USE-AXI-LITE` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** The runner's build-only mode builds `tdr2axi` with a seeded illegal value of `AXI_DATA_WIDTH`, `AXI_ADDR_WIDTH` or `SYNC_STAGES`, or with mismatched or default struct types; a bring-up runs on the legal boundary builds.

**Expected results.** Each illegal build stops before simulation and each boundary build starts (`CHK-CFG-ILLEGAL-PARAMS`, S-TDR2AXI-0014, S-TDR2AXI-0015, S-TDR2AXI-0018 to S-TDR2AXI-0021, S-TDR2AXI-0024).

### `tdr2axi_reset_during_clear_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-async-any-time` (P0) |
| Test | `cocotb/tests/test_tdr2axi_reset.py`, built on `Tdr2axiTb` |
| Randomized | The CDC (`u_cdc_req`, `u_cdc_resp`) and the half of its reset controller the second assertion is timed on, the clock ratio, the phase of the first release |
| Checks | `CHK-RESET-ASYNC-ANY-TIME`, `CHK-RESET-NO-STALE-AXI`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-ENV-FABRIC-RESET`, `CHK-NONVAC` |
| Model entries | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Assert and release `i_arst_n` with the subordinate reset alongside, then, during the clear sequence both CDCs run after the release, assert `i_arst_n` again on a cycle where the `cdc_4phase_dst` receiver of a `cdc_reset_ctrlr` half waits in `WAIT_DOWNSTREAM_ACK` for its local acknowledge, a state it holds for single cycles. The test times the assertion on the receiver's state, `i_state_transition_cdc_dst.state_q` under `i_cdc_reset_ctrlr_half_a` or `_b` of `u_cdc_req` or `u_cdc_resp`, which the bench exposes as an observable. Release, let the clear sequence finish, recover a busy before the bridge is ready with op 3, and run operations.

**Expected results.** The capture reads address 0, data 0, status 0, the DM registers read their reset values, and the operations after release complete (`CHK-RESET-ASYNC-ANY-TIME`, S-TDR2AXI-0026, S-TDR2AXI-0051, S-TDR2AXI-0052); nothing from before the reset reaches the bus (`CHK-RESET-NO-STALE-AXI`), and no VALID is high in reset (`CHK-AXISUB-RESET-VALID-LOW`). `c_rst_cdc_dst_wait_ack` shows that the assertion landed in `WAIT_DOWNSTREAM_ACK`.

### `tdr2axi_reset_release_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-sync-release` (P0) |
| Test | `cocotb/tests/test_tdr2axi_reset.py`, built on `Tdr2axiTb` |
| Randomized | Phase of the `i_arst_n` release against each clock, clock ratio, TCK running or stopped at release; the ratio is drawn from `i_clk` 20 times faster than TCK to 8 times slower (`--ratio random`) or is a named `CFG-CLK-RATIO` point, 20:1, 20:19, 20:21, 1:2 and 1:20 (`CLK_PERIOD_PS` 1000, 19000, 21000, 40000 and 400000 at TCK = 20000 ps) |
| Checks | `CHK-RESET-SYNC-RELEASE`, `CHK-RESET-SYNC-RELEASE-SS4`, `CHK-RESET-CDC-CLEAR-20TO1`, `CHK-RESET-CDC-CLEAR-20TO19`, `CHK-RESET-CDC-CLEAR-20TO21`, `CHK-RESET-CDC-CLEAR-1TO2`, `CHK-RESET-CDC-CLEAR-1TO20`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-NONVAC` |
| Model entries | `RST-ARST-BUS`, `RST-ARST-TCK`, `T-TDR2AXI-tck-reset-release`, `T-TDR2AXI-tck-reset-release-ss4`, `T-TDR2AXI-cdc-clear-20to1`, `T-TDR2AXI-cdc-clear-20to19`, `T-TDR2AXI-cdc-clear-20to21`, `T-TDR2AXI-cdc-clear-1to2`, `T-TDR2AXI-cdc-clear-1to20`, `RDC-ARST`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target, and at least one SYNC_STAGES 3 and one SYNC_STAGES 4 build at all five named ratios and at random ratios |

**Stimulus.** Assert `i_arst_n` while idle, release it at a seeded phase, in one order with TCK stopped and resumed later and in another with `i_clk` slow, and count the edges of each clock until its internal reset releases. With both clocks running, count TCK rising edges from the later of the two synchronized releases until all four isolate requests of `u_cdc_req` and `u_cdc_resp` are low; then run a DMI read whose Update-DR follows the clear item's maximum.

**Expected results.** Both sides reset at once and release SYNC_STAGES to SYNC_STAGES + 1 edges of their own clock later (`CHK-RESET-SYNC-RELEASE` with `T-TDR2AXI-tck-reset-release` at SYNC_STAGES 3, `CHK-RESET-SYNC-RELEASE-SS4` with `T-TDR2AXI-tck-reset-release-ss4` at 4, S-TDR2AXI-0052, S-TDR2AXI-0053); no AXI VALID is high in reset. At a named ratio the clear sequence ends within the maximum of the clear item for that ratio, such as 79 TCK edges at 1:2 (`T-TDR2AXI-cdc-clear-1to2`), and the read after it crosses without busy (`CHK-RESET-CDC-CLEAR-20TO1` to `CHK-RESET-CDC-CLEAR-1TO20`); a random-ratio run requires none of the clear checks, and the release checks hold in both modes.

### `tdr2axi_reset_sync_formal`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-sync-release` (P0), `F-RESET-test-mode-bypass` (P1) |
| Job | `formal/sby/tdr2axi_reset_sync_formal.sby`, SymbiYosys |
| Checks | `CHK-RESET-SYNC-RELEASE-SVA`, `CHK-RESET-TEST-MODE-SVA` |
| Model entries | `RST-ARST-BUS`, `RST-ARST-TCK`, `RST-SCAN`, `T-TDR2AXI-tck-reset-release`, `CFG-SYNC-STAGES` |

**Harness cut.** The two `rstgen_bypass` instances of `tdr2axi` in a multiclock model, with `i_clk` and TCK as independent clock enables on the global clock, either of which may stop, and `i_arst_n`, `i_test_mode` and `i_arst_scan_n` free.

**Reduced width.** `SYNC_STAGES` = 3 and 4.

**Properties.** `CHK-RESET-SYNC-RELEASE-SVA` (assertion with `i_arst_n`, release after SYNC_STAGES to SYNC_STAGES + 1 edges of each clock, `T-TDR2AXI-tck-reset-release`) and `CHK-RESET-TEST-MODE-SVA`.

### `tdr2axi_reset_test_mode_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-test-mode-bypass` (P1) |
| Test | `cocotb/tests/test_tdr2axi_reset.py`, built on `Tdr2axiTb` |
| Randomized | `i_arst_scan_n` pulse timing, `i_arst_n` activity while `i_test_mode` = 1 |
| Checks | `CHK-RESET-TEST-MODE`, `CHK-NONVAC` |
| Model entries | `RST-SCAN`, `CFG-TEST-MODE` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Drive `i_test_mode` = 1, pulse `i_arst_scan_n` with an operation pending, toggle `i_arst_n`, then run operations; return to `i_test_mode` = 0 through a functional reset.

**Expected results.** `i_arst_scan_n` alone resets and releases both sides with no synchronizer delay (`CHK-RESET-TEST-MODE`, S-TDR2AXI-0027, S-TDR2AXI-0028, S-TDR2AXI-0052).

### `tdr2axi_reset_traffic_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-async-any-time` (P0) |
| Test | `cocotb/tests/test_tdr2axi_reset.py`, built on `Tdr2axiTb` |
| Randomized | Order of the stages the operation is held at (TCK FSM, request CDC, DM, AR, AW, W, R, B) and the stage of each later loop, W waiting alone or with AW, assertion phase, second assertion during the post-reset clear sequence, busy capture before the bridge is ready and its release with i_clk held or TCK stopped, clock ratio |
| Checks | `CHK-RESET-ASYNC-ANY-TIME`, `CHK-RESET-NO-STALE-AXI`, `CHK-AXISUB-RESET-VALID-LOW`, `CHK-ENV-FABRIC-RESET`, `CHK-NONVAC` |
| Model entries | `RST-ARST-BUS`, `RST-ARST-TCK`, `RDC-ARST`, `CDC-REQ`, `CDC-RESP`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** In each loop, assert `i_arst_n` asynchronously with an operation held at a stage, with the subordinate reset alongside, release, and run operations. The first loops take every stage once and the W stage once more, in a seeded order, and later loops a seeded stage; among the W loops, WVALID waits after AW was taken in at least one and WVALID and AWVALID both wait in at least one other. A seeded share of the loops, and at least one per run, asserts a second reset before the first clear sequence finishes. In a seeded share of the loops, again at least one per run, a `dmcontrol` read and a NOP capture busy before the bridge is ready, and op 3 recovers it once the read has its answer; at 1:20 a seeded share of these, and at least one per run, releases with TCK stopped, so the request CDC takes the read while the i_clk side isolates it from the DM.

**Expected results.** The capture reads address 0, data 0, status 0, the DM registers read their reset values, and operations after release complete (`CHK-RESET-ASYNC-ANY-TIME`, S-TDR2AXI-0026, S-TDR2AXI-0051, S-TDR2AXI-0052); nothing from before the reset reaches the bus (`CHK-RESET-NO-STALE-AXI`). While `dmactive` = 0 after reset, `sbcs` reads its register reset value, `sbaccess` = 2 and every other field 0, and `dmactive` reads 0; a read the request CDC takes while the i_clk side isolates it from the DM, with the DM's request valid low, waits valid at the request CDC's destination (`CHK-RESET-ASYNC-ANY-TIME`).

### `tdr2axi_sba_address_width_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-address-width` (P1) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Bit position and the other address bits |
| Checks | `CHK-SBA-ADDRESS-WIDTH`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Walking-one and walking-zero addresses over every bit below `AXI_ADDR_WIDTH`, through `sbaddress1` above bit 31, with one access each, on every build.

**Expected results.** Each bit reaches the same `AxADDR` bit at either data width, and both address registers read back what was written (`CHK-SBA-ADDRESS-WIDTH`, S-TDR2AXI-0014, S-TDR2AXI-0022). No address bit above `AXI_ADDR_WIDTH` is written; the absent `sbaddress1` at 32 bits or less is `tdr2axi_dm_write_ones_test`'s.

### `tdr2axi_sba_block_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-block-write` (P1), `F-SBA-block-read` (P1) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Size, block length, start offset within a bus word, `sbreadondata` per read |
| Checks | `CHK-SBA-BLOCK-WRITE`, `CHK-SBA-BLOCK-READ`, `CHK-SBA-READ-NO-READONDATA`, `CHK-ENV-SBA-BLOCK-READ-STEPS`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Block writes and block reads at every size, starting one element into a bus word so narrow blocks cross words, with `sbreadondata` cleared before the last element, plus `sbdata0` reads with `sbreadondata` clear.

**Expected results.** Each element is one transaction at the advancing address and `sbaddress` reads back the advanced value (`CHK-SBA-BLOCK-WRITE`, `CHK-SBA-BLOCK-READ`, S-TDR2AXI-0079, S-TDR2AXI-0081); an `sbdata0` read without `sbreadondata` issues no AR and leaves the address (`CHK-SBA-READ-NO-READONDATA`).

### `tdr2axi_sba_busy_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-sticky-busy` (P0), `F-SBA-busy-status` (P1), `F-DM-dmactive-clear` (P0) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Order of the colliding registers in each phase and of the stalls of their own, each such stall's length and its read or write, bus type, the size of the second busy `sbcs` write and of the `sbcs` write with bit 22 clear |
| Checks | `CHK-TDR-BUSY-DM`, `CHK-TDR-STICKY-IGNORE`, `CHK-SBA-SBBUSY`, `CHK-SBA-SBBUSYERROR`, `CHK-NONVAC`, `CHK-DM-OP3-KEEPS-DM` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Four phases run in order. The sticky stall holds a read on AR and reads `sbdata0`, which collides; it scans an `sbcs` write and an `sbcs` read that the sticky busy ignores, recovers with op 3 and reads `sbcs`. With the read still held and `sbbusyerror` set, it makes in a seeded order a write of `sbaddress0`, `sbaddress1`, `sbdata0` and `sbdata1` and a read of `sbdata1`, each followed by op 3, reads `sbaddress0` and, above 32 address bits, `sbaddress1`, and writes `sbcs` twice, which the debug specification leaves undefined while `sbbusy` is set. Both `sbcs` writes set bit 22 and complement every other read-write field of the held value; the first keeps the held read's `sbaccess`, so a DM that took it would change no byte enable of the held request, and the second carries a seeded `sbaccess` other than the held read's. An `sbcs` read follows each, and the `sbaddress` reads follow the second. This phase grades the busy answers, the sticky busy up to op 3, and that while `sbbusy` is set no collision or `sbcs` write changes `sbaddress` or an `sbcs` field. The `sbbusyerror`-alone phase holds a write on AW and W, sets `sbbusyerror` with a colliding `sbdata0` write, and releases the write. With only `sbbusyerror` set, it writes `sbcs` with bit 22 clear, a seeded `sbaccess`, and `sbreadonaddr` and `sbreadondata` set; then, with the subordinate's readys low, it makes the six accesses, a write of `sbaddress0`, `sbaddress1`, `sbdata0` and `sbdata1` and a read of `sbdata0` and `sbdata1`, in a seeded order, each followed by op 3, and clears `sbbusyerror` by writing 1 to it. This phase grades that the `sbcs` write is taken and leaves `sbbusyerror` set, and that each of the six accesses is answered busy and starts no bus access, where a DM that ignored `sbbusyerror` would start one on the `sbaddress0` write, the `sbdata0` write and the `sbdata0` read. Then come the stalls of their own. Each collision X-RISCV_DEBUG-sbbusyerror names, a write of `sbaddress0`, `sbaddress1`, `sbdata0` or `sbdata1` and a read of `sbdata0` or `sbdata1`, gets one, and one more stall reads `sbaddress0` and, above 32 address bits, `sbaddress1`, which are no collision and need no op 3. In a seeded order, each stall starts a read or a write of seeded direction, holds it with the subordinate's readys low for a seeded number of `i_clk` cycles, makes its access, then recovers with op 3 after a collision, the release, a poll of `sbcs` until `sbbusy` is 0 and a write clearing `sbbusyerror`. Each of these stalls starts with `sbbusyerror` and the busy status clear, so each register's error is its own. The last phase, the response sweep on fixed subordinate timing, stalls reads and writes that each poll `sbcs` once and release the response d `i_clk` edges after that poll's Update-DR, d running from SYNC_STAGES - 1 to SYNC_STAGES + 3, so at any ratio one of them lands the R or B handshake on the edge just before the DM takes the read.

**Expected results.** The DM's busy answer makes the status busy with read data 0xB051B051 (`CHK-TDR-BUSY-DM`); every `sbcs` read from the write that starts an access until its bus response reads `sbbusy` = 1, and after the response at most one read may read 1, every later read reading 0 until the next access (`CHK-SBA-SBBUSY`, a DV reading: the debug specification leaves the time `sbbusy` falls open, and a debugger only polls until 0; the check accepts the one late read without requiring it, and `c_sba_sbbusy_read_after_response` shows the stimulus reached it); each colliding access, on its own, sets `sbbusyerror` and starts nothing, a colliding write leaves `sbaddress` and `sbdata` as the stalled access leaves them, and a read of `sbaddress0` or `sbaddress1` returns the address and leaves `sbbusyerror` clear (`CHK-SBA-SBBUSYERROR`, S-TDR2AXI-0043, X-RISCV_DEBUG-sbbusyerror), the `sbaddress1` write included at every address width, the absent register at 32 bits or less as well (`R-RISCV_DEBUG` profile entry `departure_absent`); each stalled access completes once. Each busy `sbcs` write is answered DMI busy, leaves `sbbusyerror` set and changes no other field, so the `sbcs` read after it reads the held value with `sbbusyerror` = 1 (`CHK-SBA-SBBUSYERROR`, the `R-RISCV_DEBUG` profile entry `departure_busy`: `src/dm_csrs.sv:512-514`, where 0.13.2 3.12.18 says nothing); `c_sba_sbcs_write_while_busy` and `c_sba_sbcs_write_busy_same_size` record that both cases ran. With `sbbusyerror` alone set, the `sbcs` write with bit 22 clear is taken and leaves `sbbusyerror` set, and each of the six accesses after it, with `sbreadonaddr` and `sbreadondata` set, is answered DMI busy and starts no bus access (`CHK-SBA-SBBUSYERROR`; 0.13.2 3.12.18 lets no access start while `sbbusyerror` is set, and the busy answer is the `R-RISCV_DEBUG` profile entry `departure_busy`). Reads after each op 3 return the DM registers as they were before it (`CHK-DM-OP3-KEEPS-DM`).

### `tdr2axi_sba_error_address_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-no-increment-after-error` (P2), `F-SBA-block-write` (P1) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Access that fails (read on `sbaddress0`, write on `sbdata0`, read on `sbdata0`), element of the block that fails, error type, size |
| Checks | `CHK-SBA-NO-INC-AFTER-ERROR`, `CHK-NONVAC`, `CHK-SBA-BLOCK-WRITE` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** With `sbautoincrement` set: reads started by an `sbaddress0` write, answered SLVERR or DECERR; block writes started by `sbdata0` writes with the error on a seeded element; and block reads with `sbreadondata` set whose error falls on a read an `sbdata0` read started, at a seeded element after the first.

**Expected results.** After the error `sbaddress` reads the failing address, whichever access failed (`CHK-SBA-NO-INC-AFTER-ERROR`, S-TDR2AXI-0061, S-TDR2AXI-0076, S-TDR2AXI-0079, S-TDR2AXI-0093, S-TDR2AXI-0094). Each element of a block write is one write at the advancing address whose lanes carry the element's data, the failing element's lanes included (`CHK-SBA-BLOCK-WRITE`, S-TDR2AXI-0079).

### `tdr2axi_sba_errors_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-access-errors` (P2), `F-SBA-bus-error` (P2) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Size, misalignment offset, error response type and direction, block length, the block element that fails |
| Checks | `CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE`, `CHK-SBA-BUS-ERROR`, `CHK-SBA-ERROR-BLOCKS`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Misaligned accesses at every size above 8 bits, accesses wider than the bus, single reads and writes answered SLVERR or DECERR, and blocks whose error falls on an element after the first: block writes with `sbautoincrement` set, and block reads with `sbreadondata` and `sbautoincrement` set. After each error the test makes every access that would start a transfer (an `sbdata0` write, an `sbaddress0` write with `sbreadonaddr`, an `sbdata0` read with `sbreadondata`), then clears `sberror`, writes `sbaddress0` and makes an aligned access. Misaligned and oversized accesses are never combined. The address after a failed access is not compared here; `tdr2axi_sba_error_address_test` grades it.

**Expected results.** `sberror` reads 3, 4 or 2 with the bus untouched for 3 and 4 and exactly one transaction for 2 (`CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE`, `CHK-SBA-BUS-ERROR`, S-TDR2AXI-0060, S-TDR2AXI-0061). From the failing transaction until `sberror` is cleared no AR or AW starts, including the read an `sbdata0` read with `sbreadondata` would start in a block (`CHK-SBA-ERROR-BLOCKS`); after clearing, the aligned access completes.

### `tdr2axi_sba_single_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-single-write` (P0), `F-SBA-single-read` (P0), `F-SBA-data-lanes` (P0), `F-AXI-axi4-size` (P0), `F-AXI-lite-full-width` (P1) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Data, address region (above 4 GiB when `AXI_ADDR_WIDTH` > 32), access order |
| Checks | `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY`, `CHK-SBA-SINGLE-READ`, `CHK-SBA-NO-READ-ON-ADDR`, `CHK-SBA-DATA-LANES`, `CHK-SBA-SBDATA1-NO-BUS`, `CHK-AXI-AXI4-SIZE`, `CHK-AXI-LITE-FULL-WIDTH`, `CHK-ENV-SBA-WRITE-STEPS`, `CHK-ENV-SBA-READ-STEPS`, `CHK-ENV-SBA-POLL`, `CHK-AXISUB-SIZE-WIDTH`, `CHK-AXISUB-STROBE-LANES`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Every access size at every byte lane: a single write through the programming steps, then a single read of the same address; `sbdata1` read and written alone on every build, 32-bit data included, since `u_dm_csrs` decodes it at both widths; an `sbaddress0` write with `sbreadonaddr` clear.

**Expected results.** Each write and read gives exactly one transaction at the right address, size, strobes and lanes, and memory changes only on the addressed bytes (`CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY`, `CHK-SBA-SINGLE-READ`, `CHK-AXI-AXI4-SIZE`, `CHK-AXI-LITE-FULL-WIDTH`); `sbdata` lanes follow the size (`CHK-SBA-DATA-LANES`); `sbdata1` read or written on its own and a plain `sbaddress0` write reach no bus at either data width (`CHK-SBA-SBDATA1-NO-BUS`, `CHK-SBA-NO-READ-ON-ADDR`).

### `tdr2axi_sba_stress_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-SBA-single-write` (P0), `F-SBA-single-read` (P0), `F-SBA-data-lanes` (P0), `F-SBA-block-write` (P1), `F-SBA-block-read` (P1), `F-SBA-access-errors` (P2), `F-SBA-bus-error` (P2), `F-AXI-axi4-size` (P0), `F-AXI-lite-full-width` (P1), `F-TDR-busy-recovery` (P1) |
| Test | `cocotb/tests/test_tdr2axi_sba.py`, built on `Tdr2axiTb` |
| Randomized | Size, address, single or block, block length, `sbreadondata`, an error on any access or block element, Run-Test/Idle count, subordinate timing, clock ratio |
| Checks | `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-WRITE-MEMORY`, `CHK-SBA-SINGLE-READ`, `CHK-SBA-DATA-LANES`, `CHK-SBA-BLOCK-WRITE`, `CHK-SBA-BLOCK-READ`, `CHK-SBA-MISALIGNED`, `CHK-SBA-OVERSIZE`, `CHK-SBA-BUS-ERROR`, `CHK-SBA-ERROR-BLOCKS`, `CHK-AXI-AXI4-SIZE`, `CHK-AXI-LITE-FULL-WIDTH`, `CHK-TDR-BUSY-RECOVERY`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CFG-USE-AXI-LITE`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-ADDR-WIDTH`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** A long seeded mix of every SBA operation, with the host's busy recovery active. After an error the mix clears `sberror` and writes `sbaddress0` before its next access, so `sbaddress` is not compared between a failed access and that write.

**Expected results.** `SbaRefModel` predicts every DMI result, transaction and memory byte, and the declared checks compare them; no transfer starts while `sberror` is set (`CHK-SBA-ERROR-BLOCKS`).

### `tdr2axi_smoke_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-PORT-tdr-interface` (P1), `F-PORT-bus-and-reset` (P1), `F-CFG-bender-target` (P3), `F-DM-version` (P1), `F-SBA-sbcs-fields` (P1), `F-SBA-single-write` (P0), `F-SBA-single-read` (P0) |
| Test | `cocotb/tests/test_tdr2axi.py`, built on `Tdr2axiTb` |
| Randomized | Build target; the address in each access |
| Checks | `CHK-PORT-TDR-LIST`, `CHK-PORT-BUS-LIST`, `CHK-CFG-BENDER-TARGET`, `CHK-DM-VERSION`, `CHK-SBA-SBCS-FIELDS`, `CHK-SBA-SINGLE-WRITE`, `CHK-SBA-SINGLE-READ`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CLK-BUS`, `CLK-TCK`, `RST-ARST-BUS`, `RST-ARST-TCK`, `CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Reset with `i_test_mode` = 0, release, recover any busy capture before the bridge is ready with op 3, and enable the DM with `dmcontrol` = 1. Read `dmstatus` and `sbcs`, then one 32-bit SBA write and one 32-bit SBA read of the same address through the single-access sequences. Before the first scan, the elaborated port list of `tdr2axi` is compared with the port tables, and the build log with the Bender file list.

**Expected results.** `CHK-PORT-TDR-LIST` and `CHK-PORT-BUS-LIST` compare the port list with S-TDR2AXI-0025 to S-TDR2AXI-0035; `CHK-CFG-BENDER-TARGET` checks S-TDR2AXI-0004. `CHK-DM-VERSION` and `CHK-SBA-SBCS-FIELDS` compare `dmstatus` and `sbcs` with S-TDR2AXI-0002, S-TDR2AXI-0014, S-TDR2AXI-0015 and -0023; `dmstatus` also reads `authenticated` = 1 and `authbusy` = 0, since this DM implements no authentication (X-RISCV_DEBUG-authenticated, X-RISCV_DEBUG-authbusy). The write and the read each give exactly one transaction (`CHK-SBA-SINGLE-WRITE`, `CHK-SBA-SINGLE-READ`), and the subordinate is drained at the end.

### `tdr2axi_tdr_busy_capture_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-sticky-busy` (P0) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | TCK edge of the capture across the `T-TDR2AXI-resp-to-tdr` window; i_clk stop length; clock ratio |
| Checks | `CHK-TDR-BUSY-CAPTURE`, `CHK-TDR-STICKY-IGNORE`, `CHK-TDR-NEVER-FAILED`, `CHK-NONVAC` |
| Model entries | `CLK-BUS`, `CLK-TCK`, `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Start a DMI read, then scan so that its Capture-DR lands while the read is pending: with `i_clk` stopped, and with `i_clk` running at a Run-Test/Idle count below the window. Then op 3, and a resend with a count above the window.

**Expected results.** The capture on the pending read reads busy on that same edge and the status stays busy (`CHK-TDR-BUSY-CAPTURE`, S-TDR2AXI-0043); nothing is sent while it holds (`CHK-TDR-STICKY-IGNORE`); op 3 clears it and the resend returns the data.

### `tdr2axi_tdr_busy_recovery_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-busy-recovery` (P1) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Starting Run-Test/Idle count from 0, operation mix, clock ratio |
| Checks | `CHK-TDR-BUSY-RECOVERY`, `CHK-TDR-RESEND-READ`, `CHK-ENV-READONDATA-IDLE`, `CHK-NONVAC` |
| Model entries | `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Back-to-back operations with a Run-Test/Idle count below the window, following the debugger's recovery: op 3 after each busy capture, a larger count, and a resend. A subset resends `sbdata0` reads with `sbreadondata` set at locations the subordinate marks side-effecting, once with the count below the window.

**Expected results.** Every operation completes with its predicted result and each write lands once (`CHK-TDR-BUSY-RECOVERY`, S-TDR2AXI-0045). A resent read reaches the bus at most twice (`CHK-TDR-RESEND-READ`, S-TDR2AXI-0046). With the count at or above the DMI round trip N, the subordinate sees no second read of a side-effecting location (`CHK-ENV-READONDATA-IDLE`).

### `tdr2axi_tdr_busy_update_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-sticky-busy` (P0) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Order of the rounds, one per refused op (NOP included), number (one or two) and ops of the further updates while the write waits, register and data of each update, order of the ops scanned under the sticky busy, i_clk stop and hold lengths |
| Checks | `CHK-TDR-BUSY-UPDATE`, `CHK-TDR-STICKY-IGNORE`, `CHK-TDR-NEVER-FAILED`, `CHK-NONVAC` |
| Model entries | `CLK-BUS`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** Three rounds, one per op refused (NOP, read, write), in a seeded order. Each stops `i_clk`, scans a write so it waits in the bridge, then scans the refused update and one or two further updates, each of a seeded op; it holds `i_clk` stopped, restarts it, scans NOPs and an update of each op 0, 1 and 2 while the status is busy, then op 3 and a poll of `sbcs`.

**Expected results.** The refused update makes the next capture and every later one read busy (`CHK-TDR-BUSY-UPDATE`, S-TDR2AXI-0043); updates while busy send nothing and reach no bus (`CHK-TDR-STICKY-IGNORE`, S-TDR2AXI-0045); only the first write lands in memory; no capture reads status 2 (`CHK-TDR-NEVER-FAILED`).

### `tdr2axi_tdr_ops_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-op-decode` (P0), `F-TDR-capture-image` (P0) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Op, address and data of every scan; Run-Test/Idle count of at least the DMI round trip, N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck) TCK edges; clock ratio |
| Checks | `CHK-TDR-OP-DECODE`, `CHK-TDR-OP3-CLEAR`, `CHK-TDR-CAPTURE-IMAGE`, `CHK-ENV-TDR-UPDATE-STABLE`, `CHK-TDRHOST-CAPTURE-DR`, `CHK-TDRHOST-UPDATE-DR`, `CHK-NONVAC` |
| Model entries | `CLK-TCK`, `CFG-CLK-RATIO`, `CFG-SYNC-STAGES` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** A seeded stream of scans with op 0, 1, 2 and 3 at mapped and unmapped DMI addresses, each started in IDLE with a clear status, with the TAP path and shift padding drawn from the seed.

**Expected results.** Every update sends exactly the DMI request `TdrStatusModel` predicts from the update-stage value, and op 0 and op 3 send none (`CHK-TDR-OP-DECODE`, `CHK-TDR-OP3-CLEAR`, S-TDR2AXI-0030, S-TDR2AXI-0036, S-TDR2AXI-0039, S-TDR2AXI-0041). Each capture shows the address and data of the last accepted operation (`CHK-TDR-CAPTURE-IMAGE`, S-TDR2AXI-0037, S-TDR2AXI-0038). `TdrHost` checks its own strobes and the update-edge stability the DUT assumes.

### `tdr2axi_tdr_pipelined_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-capture-image` (P0), `F-SBA-block-read` (P1) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Block length, start address, Run-Test/Idle count from the DMI round trip up, N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck) TCK edges plus the subordinate's read latency; `T-TDR2AXI-resp-to-tdr` is only the response leg of N |
| Checks | `CHK-TDR-PIPELINED-READ`, `CHK-TDR-CAPTURE-IMAGE`, `CHK-SBA-BLOCK-READ`, `CHK-NONVAC`, `CHK-AXISUB-DRAIN` |
| Model entries | `CLK-TCK`, `T-TDR2AXI-resp-to-tdr`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target |

**Stimulus.** A block read with `sbreadondata` and `sbautoincrement` set, where each scan carries the next `sbdata0` read instead of a NOP poll, `sbreadondata` is cleared before the last element, and a final NOP collects the last word.

**Expected results.** The capture of each scan returns the previous read's data (`CHK-TDR-PIPELINED-READ`, S-TDR2AXI-0063), the elements match memory (`CHK-SBA-BLOCK-READ`, S-TDR2AXI-0081), and the AR stream holds one read per element.

### `tdr2axi_tdr_result_latency_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-result-latency` (P2) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Run-Test/Idle count swept across the window, clock ratio, build `SYNC_STAGES` |
| Checks | `CHK-TDR-RESULT-LATENCY`, `CHK-TDR-RESULT-LATENCY-SS4`, `CHK-NONVAC` |
| Model entries | `T-TDR2AXI-resp-to-tdr`, `T-TDR2AXI-resp-to-tdr-ss4`, `CDC-RESP`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| Seeds and target | 5 per build; every build target, and at least one SYNC_STAGES 4 build |

**Stimulus.** DMI reads whose following capture is placed at each TCK edge from before the window's start to after its end.

**Expected results.** The response reaches `o_dmi_tdr` SYNC_STAGES + 2 to SYNC_STAGES + 3 TCK edges after the DM's response handshake, and a capture before then reads busy: 5 to 6 edges at SYNC_STAGES 3 (`CHK-TDR-RESULT-LATENCY`, `T-TDR2AXI-resp-to-tdr`) and 6 to 7 at SYNC_STAGES 4 (`CHK-TDR-RESULT-LATENCY-SS4`, `T-TDR2AXI-resp-to-tdr-ss4`), S-TDR2AXI-0049.

### `tdr2axi_tdr_strobes_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-strobes` (P1) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | TAP path (Pause-DR, shift padding), strobe widths, clock ratio |
| Checks | `CHK-TDR-UPDATE-EDGE`, `CHK-TDR-CAPTURE-EDGE`, `CHK-TDRHOST-CAPTURE-DR`, `CHK-TDRHOST-UPDATE-DR`, `CHK-ENV-TDR-UPDATE-STABLE`, `CHK-NONVAC` |
| Model entries | `CLK-TCK`, `CFG-CLK-RATIO` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** Scans through every TAP path `TdrHost` models, including scans that hold `i_dmi_tdr_update` for two TCK edges while an operation is pending.

**Expected results.** Each rising edge with the update strobe high is one update (`CHK-TDR-UPDATE-EDGE`, S-TDR2AXI-0032), and `o_dmi_tdr` is valid at each capture edge (`CHK-TDR-CAPTURE-EDGE`, S-TDR2AXI-0031). The host's strobe and update-stage rules hold.

### `tdr2axi_tdr_tck_stop_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-TDR-tck-stop-hold` (P0) |
| Test | `cocotb/tests/test_tdr2axi_tdr.py`, built on `Tdr2axiTb` |
| Randomized | Stop length, the point at which TCK stops (op in the DM, on the bus, response in the CDC), clock ratio |
| Checks | `CHK-TDR-TCK-STOP-HOLD`, `CHK-TDR-TCK-STOP-HOLD-SS4`, `CHK-NONVAC` |
| Model entries | `CLK-TCK`, `CDC-RESP`, `T-TDR2AXI-resp-to-tdr`, `T-TDR2AXI-resp-to-tdr-ss4`, `CFG-SYNC-STAGES`, `CFG-CLK-RATIO` |
| Seeds and target | 20 per build; every build target, so both `SYNC_STAGES` values |

**Stimulus.** Stop TCK between scans for a seeded number of `i_clk` cycles while an operation completes on the bus, then resume with Run-Test/Idle edges and capture.

**Expected results.** The bridge state and `o_dmi_tdr` hold over the stop, and the result appears only as TCK edges arrive, within the result-latency window counted in TCK edges: 5 to 6 at `SYNC_STAGES` 3 (`CHK-TDR-TCK-STOP-HOLD`, `T-TDR2AXI-resp-to-tdr`) and 6 to 7 at 4 (`CHK-TDR-TCK-STOP-HOLD-SS4`, `T-TDR2AXI-resp-to-tdr-ss4`), S-TDR2AXI-0029, S-TDR2AXI-0049, X-IEEE1149_1-tck-static.

### `tdr2axi_trst_test`

| Field | Value |
| --- | --- |
| Features, priority | `F-RESET-trst-independent` (P1) |
| Test | `cocotb/tests/test_tdr2axi_reset.py`, built on `Tdr2axiTb` |
| Randomized | Operation in flight, stop length |
| Checks | `CHK-RESET-TRST-INDEPENDENT`, `CHK-NONVAC` |
| Model entries | `RST-TRST`, `CLK-TCK` |
| Seeds and target | 5 per build; every build target |

**Stimulus.** With an operation in flight, stop TCK, model a TAP reset that deselects the TDR (both strobes low, update-stage value unchanged), restart TCK through Run-Test/Idle, and scan a NOP.

**Expected results.** The operation completes on the bus, the sticky status survives until op 3, and the capture shows the result (`CHK-RESET-TRST-INDEPENDENT`, S-TDR2AXI-0054, S-TDR2AXI-0055).


## Testlist Mapping

Each simulated item is one record in `testlists/all.toml`, read through the project's
records adapter, and the groups are `[[groups]]` tables that list test names. The cocotb
module is the one in the Makefile's `MODULE` list that defines the test. The smoke group
also runs `tdr2axi_bringup_test`, the bench's bring-up test, which is no plan item. Formal
items run from `formal/sby/` and are not in the testlist.

```toml
[[tests]]
name = "tdr2axi_sba_single_test"
reseed = 20

[[groups]]
name = "smoke"
tests = ["tdr2axi_bringup_test", "tdr2axi_smoke_test", "tdr2axi_dm_registers_test", "tdr2axi_sba_single_test"]
```

| Test | Module | reseed | Groups |
| --- | --- | --- | --- |
| `tdr2axi_axi_attributes_test` | `test_tdr2axi_axi` | 20 | nightly, signoff |
| `tdr2axi_axi_protocol_test` | `test_tdr2axi_axi` | 20 | nightly, signoff |
| `tdr2axi_clock_ratio_test` | `test_tdr2axi_clock` | 5 | nightly, signoff |
| `tdr2axi_dm_registers_test` | `test_tdr2axi_dm` | 20 | smoke, nightly, signoff |
| `tdr2axi_dm_write_ones_test` | `test_tdr2axi_dm` | 5 | nightly, signoff |
| `tdr2axi_dmactive_clear_test` | `test_tdr2axi_dmactive` | 20 | nightly, signoff |
| `tdr2axi_dmactive_inflight_test` | `test_tdr2axi_dmactive` | 20 | nightly, signoff |
| `tdr2axi_dmactive_no_bus_test` | `test_tdr2axi_dmactive` | 20 | nightly, signoff |
| `tdr2axi_dmactive_reset_test` | `test_tdr2axi_dmactive` | 20 | nightly, signoff |
| `tdr2axi_param_checks_test` | none, a runner build check | 5 | nightly, signoff |
| `tdr2axi_reset_during_clear_test` | `test_tdr2axi_reset` | 20 | nightly, signoff |
| `tdr2axi_reset_release_test` | `test_tdr2axi_reset` | 20 | nightly, signoff |
| `tdr2axi_reset_test_mode_test` | `test_tdr2axi_reset` | 5 | nightly, signoff |
| `tdr2axi_reset_traffic_test` | `test_tdr2axi_reset` | 20 | nightly, signoff |
| `tdr2axi_sba_address_width_test` | `test_tdr2axi_sba` | 5 | nightly, signoff |
| `tdr2axi_sba_block_test` | `test_tdr2axi_sba` | 5 | nightly, signoff |
| `tdr2axi_sba_busy_test` | `test_tdr2axi_sba` | 20 | nightly, signoff |
| `tdr2axi_sba_error_address_test` | `test_tdr2axi_sba` | 5 | nightly, signoff |
| `tdr2axi_sba_errors_test` | `test_tdr2axi_sba` | 5 | nightly, signoff |
| `tdr2axi_sba_single_test` | `test_tdr2axi_sba` | 20 | smoke, nightly, signoff |
| `tdr2axi_sba_stress_test` | `test_tdr2axi_sba` | 20 | nightly, signoff |
| `tdr2axi_smoke_test` | `test_tdr2axi` | 20 | smoke, nightly, signoff |
| `tdr2axi_tdr_busy_capture_test` | `test_tdr2axi_tdr` | 20 | nightly, signoff |
| `tdr2axi_tdr_busy_recovery_test` | `test_tdr2axi_tdr` | 5 | nightly, signoff |
| `tdr2axi_tdr_busy_update_test` | `test_tdr2axi_tdr` | 20 | nightly, signoff |
| `tdr2axi_tdr_ops_test` | `test_tdr2axi_tdr` | 20 | nightly, signoff |
| `tdr2axi_tdr_pipelined_test` | `test_tdr2axi_tdr` | 20 | nightly, signoff |
| `tdr2axi_tdr_result_latency_test` | `test_tdr2axi_tdr` | 5 | nightly, signoff |
| `tdr2axi_tdr_strobes_test` | `test_tdr2axi_tdr` | 5 | nightly, signoff |
| `tdr2axi_tdr_tck_stop_test` | `test_tdr2axi_tdr` | 20 | nightly, signoff |
| `tdr2axi_trst_test` | `test_tdr2axi_reset` | 5 | nightly, signoff |
