<!--
SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
SPDX-License-Identifier: CC-BY-4.0
-->

# tdr2axi Testbench Architecture

This document describes how the bench in `tb/tdr2axi/` drives and observes `tdr2axi`.
The behaviors it checks are the features, checks and timing items of the trace store in
`tb/tdr2axi/trace/`, cited here by ID; the design itself is specified in
`doc/tdr2axi.md`. The plan built on this bench is `TDR2AXI_VPLAN.md`, and its functional
coverage `TDR2AXI_FCOV.md`.

## Overview

| Field | Value |
|---|---|
| DUT | `tdr2axi` (`src/tdr2axi/tdr2axi.sv`), instantiated in `tb/tb_top.sv` with the AXI4 or AXI4-Lite request and response types the build selects |
| Bench shape | IP pin-level bench: every DUT connection is a net of the bench top, driven and sampled by one cocotb helper class and two DUT-local models |
| Realizations | cocotb; formal jobs in SymbiYosys share the AXI manager properties with simulation |
| Simulators | Verilator for every seed; VCS for the bound assertions, X-propagation at reset and code coverage; SymbiYosys for formal at reduced widths |
| Framework library | None: the repository has no shared DV library or VIP, so the models live in `cocotb/env/` |
| Trace store | `tb/tdr2axi/trace/` |
| How to run | `python3 tb/tdr2axi/scripts/run_sim.py --group smoke`; `make -C tb/tdr2axi` for one interactive run |

The bench has no environment, agent or sequencer classes. One helper, `Tdr2axiTb`,
starts the clocks, walks the reset ladder, owns the TDR host and the AXI subordinate,
holds the reference models and records the evidence of every check. A test is a cocotb
coroutine that calls the helper's operations and compares what the models predict with
what the DUT did.

## VIP Selection

| Interface | Reference | Model | Side | Configuration from the profile | Binding |
|---|---|---|---|---|---|
| `o_axi_req`, `i_axi_rsp` with `USE_AXI_LITE` = 0 | `R-AXI4` | `AxiSub` (`cocotb/env/axi_sub.py`, DUT-local) | subordinate, memory-backed, with a passive monitor | one transaction outstanding, single beat, narrow transfers, no exclusives; OKAY, SLVERR and DECERR responses; ID and user widths from the bench's struct types | flattened AXI nets of `tb_top` |
| `o_axi_req`, `i_axi_rsp` with `USE_AXI_LITE` = 1 | `R-AXI4_LITE` | `AxiSub` in AXI4-Lite mode | subordinate, memory-backed, with a passive monitor | one transaction outstanding, full-width beats with strobes; OKAY, SLVERR and DECERR responses | the same nets; AXI4-only fields read 0 |
| `i_tck`, `i_dmi_tdr`, `i_dmi_tdr_capture`, `i_dmi_tdr_update`, `o_dmi_tdr` | `R-IEEE1149_1` | `TdrHost` (`cocotb/env/tdr_host.py`, DUT-local) | the external TAP and its TDR cell | 41-bit TDR, no TAP inside the DUT; TCK runs only during scans; TRST modeled as a TDR deselect with TCK stopped | TDR nets of `tb_top` |
| DMI over the TDR | `R-RISCV_DEBUG` | `TdrHost` DMI layer | the debugger | `abits` 7; ops 0 to 3 with op 3 clearing the sticky status; busy recovery by op 3 and a larger Run-Test/Idle count | through the TDR host |

Both models are DUT-local because no second block in this repository uses them. The DMI
layer has no protocol checker of its own: the DM and the DMI end of the DTM are inside
the DUT, so `R-RISCV_DEBUG` behavior is checked by the reference models.

## DUT-Local Models

| Model | Scope | Why it stays local | What would make it shareable |
|---|---|---|---|
| `AxiSub` | AXI4 and AXI4-Lite subordinate with sparse byte memory, per-address error injection, seeded READY delays and response latencies, W before AW, mid-cycle response changes, and a monitor reporting the `vip` checks below | No shared AXI VIP exists in the repository | A shared AXI VIP in the integrating project, which takes its place |
| `TdrHost` | TAP-state walk of the TDR scan (Capture-DR, Shift-DR, Pause-DR, Update-DR, Run-Test/Idle), strobe and update-stage timing, the DMI flow with busy recovery, and a DMI-sequence checker for the assumption checks | Only the TDR bridge uses a 41-bit DMI TDR without a TAP | A second TDR-attached block, or a TAP model that drives a real TAP |
| `SbaRefModel` | DM register file (map, reset values, `dmactive`, `cmderr`) and SBA engine: transactions, `sbcs` flags, auto-increment and memory image, predicted from the spec atoms | DUT-specific | None |
| `TdrStatusModel` | Bridge status rules: capture image, sticky busy, op 3, and the result window of `T-TDR2AXI-resp-to-tdr` | DUT-specific | None |
| `Tdr2axiPredictor` | Feeds both models in DUT event order: each update and capture edge `TdrHost` generates, each DMI request handshake, each R and B handshake, `sbbusy` falling after a response, the end of the CDC clear sequence, resets, TRST and memory fills; publishes the predicted bus accesses and DM answers | The event order is this DUT's | None |

## Testbench Hierarchy

```
cocotb/tests/  test_tdr2axi*.py: one coroutine per plan item, named tdr2axi_<scenario>_test
                 -> tdr2axi_base_test.py: Tdr2axiTb
                      start(): clocks, reset ladder, dmactive
                      TdrHost      scans, DMI operations, busy recovery, DMI-sequence checker
                      AxiSub       subordinate, memory, monitor, vip checks, drain guard
                      Tdr2axiPredictor   feeds SbaRefModel and TdrStatusModel in event order
                      Tdr2axiScoreboard  the scoreboard checks over four in-order streams
                      Tdr2axiTimingMonitor  round-trip and CDC-clear edge counts
                      check(id, expected, observed)  CHK evidence records, CHK-NONVAC
tb/tb_top.sv   DUT, flattened AXI nets, TDR nets, observables, assertion binds, coverage
sva/           tdr2axi_axi_mgr_sva.sv, bound in tb_top and used by the formal jobs
formal/sby/    one job per formal item, with its harness and property files
```

### Realization

| Element | cocotb |
|---|---|
| Component bases | plain Python classes, no framework library |
| Interface handoff | the `tb_top` handle cocotb passes to the test |
| Clocks | `i_clk` and TCK started by `Tdr2axiTb`; periods from the test configuration; TCK toggled only during scans |
| Knobs and seed | test configuration from the runner's environment variables; stimulus from the cocotb seed |
| Verdict | the evidence records, `CHK-NONVAC`, the drain guard, and the results file |
| Tests | one coroutine per plan item in an area module |

## Directory Structure

```
tb/tdr2axi/
  Makefile                 one build and one interactive run per CONFIG
  tb/tb_top.sv             bench top, module tb_top
  sva/                     tdr2axi_axi_mgr_sva.sv
  cocotb/env/              tdr_host.py, axi_sub.py, sba_ref_model.py, tdr_status_model.py,
                           tdr2axi_predictor.py, tdr2axi_scoreboard.py; tests/ with the
                           models' unit tests and the scoreboard self-test
  cocotb/tests/            tdr2axi_base_test.py, tdr2axi_timing_monitor.py, test_tdr2axi.py,
                           test_tdr2axi_<area>.py
  cov/sv/                  tdr2axi_fcov.sv
  cov/config/<tool>/       coverage scope, hierarchy and policy files
  formal/sby/              formal jobs
  testlists/all.toml       tests, seeds and groups
  scripts/run_sim.py       runner over the Makefile: tests x seeds x build targets
  trace/                   the trace store
  docs/                    TDR2AXI_TB_ARCH.md, TDR2AXI_VPLAN.md, TDR2AXI_FCOV.md
```

## Bench Top

| DUT interface | Handling in the top | Nets | Driven or read by |
|---|---|---|---|
| `o_axi_req`, `i_axi_rsp` | Repacked combinationally to and from the build's AXI4 or AXI4-Lite structs; every field flattened, AXI4-only fields read 0 on AXI4-Lite | `ar_*`, `aw_*`, `w_*`, `r_*`, `b_*`, including `*_prot`, `*_cache`, `*_id`, `*_user`, `*_burst`, `*_lock`, `*_qos`, `*_region`, `aw_atop`, `w_last`, `r_id`, `r_last`, `b_id` | `AxiSub` |
| TDR | Exposed directly | `tck`, `tdr_upd_q`, `tdr_upd`, `tdr_cap`, `tdr_in` | `TdrHost` |
| `i_clk`, `i_arst_n` | Exposed directly | `clk`, `rst_n` | `Tdr2axiTb` |
| `i_test_mode`, `i_arst_scan_n` | Exposed directly; held at 0 and 1 outside the test-mode item | `test_mode`, `rst_scan_n` | `Tdr2axiTb` |
| Build parameters | Read-only nets | `cfg_axi_lite`, `cfg_addr_width`, `cfg_data_width`, `cfg_axi_prot`, `cfg_axi_cache`, `cfg_sync_stages` | `Tdr2axiTb`, coverage |
| Observables | Continuous assigns from DUT-internal nets | DMI request and response handshakes between the bridge and `u_dm_csrs`; the bridge's `state_q`, `error_q`, `address_q`, `data_q`; `arst_clk_n`, `arst_tck_n`; `dmactive`, `sbaccess`, `sbautoincrement`, `sbreadonaddr`, `sbreadondata`, `sbbusy`, dm_sba's `sberror` report, the sticky `sbcs_sberror` from `u_dm_csrs`; the CDCs' clear-pending outputs | checks and coverage |

**Binds and assertions:** `tdr2axi_axi_mgr_sva` on the flattened AXI nets, enabled
after the first `i_clk` edge of reset, and the `CHK-ENV-TDR-UPDATE-STABLE` assertion on
the TDR nets. **Coverage instances:** `tdr2axi_fcov`. **Image loads:** none; `AxiSub`
memory is filled from the test. **Public scope:** the observables above, for
Verilator. **Stimulus in SV:** none.

## Environment Components

### Configuration

| Field | Level | Randomized | Range or source |
|---|---|---|---|
| `clk_period_ps`, `tck_period_ps` | test | in the clock-ratio, latency and reset items | `CLK-BUS`, `CLK-TCK`; `CFG-CLK-RATIO` from 20:1 to 1:8, drawn log-uniform under `--ratio random`, or a named point, 20:1, 20:19, 20:21, 1:2 or 1:20, under `--ratio A:B`; phase offset between the clocks in both modes |
| `rti_cycles` | test | yes | Run-Test/Idle count after each scan; at least the DMI round trip, N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck) TCK edges, unless the item sweeps it; `T-TDR2AXI-resp-to-tdr` is the response leg of N |
| `ready_delay`, `r_latency`, `b_latency`, `w_before_aw`, `rsp_glitch` | test | yes | `AxiSub` timing, per channel |
| `test_mode` | test | in `tdr2axi_reset_test_mode_test` | `CFG-TEST-MODE` |
| `lite`, `addr_width`, `data_width`, `axi_prot`, `axi_cache`, `sync_stages` | env | no | build parameters read from `tb_top` (`CFG-USE-AXI-LITE`, `CFG-AXI-ADDR-WIDTH`, `CFG-AXI-DATA-WIDTH`, `CFG-AXI-PROT`, `CFG-AXI-CACHE`, `CFG-SYNC-STAGES`) |

### Handles

| Handle | Type | Drives or observes |
|---|---|---|
| `tb.host` | `TdrHost` | TDR nets and the DMI flow |
| `tb.sub` | `AxiSub` | AXI nets |
| `tb.predictor` | `Tdr2axiPredictor` | observables, for event order only |
| `tb.predictor.sba`, `tb.predictor.tdr` | `SbaRefModel`, `TdrStatusModel` | predictions only |

### Scoreboard and Checkers

Each check reports `CHK-<ID> PASS` or `CHK-<ID> FAIL` at the point it is made; a test
whose evidence holds no comparison fails through `CHK-NONVAC`, and one that ends with a
transaction outstanding or unchecked in `AxiSub` fails through `CHK-AXISUB-DRAIN`.
Assertion checks report only on failure, under VCS in simulation and as proofs in the
formal jobs.

The Model column names each model a comparison takes its expected value from, with the
predict functions that serve the check. `Tdr2axiPredictor` calls them; the DUT supplies
only the timing of each call, never the content of a prediction.

The `scoreboard` checks are made in `Tdr2axiScoreboard`, which compares four in-order
streams and reads no DUT signal. DMI requests pair each update prediction of
`TdrStatusModel`, through `Tdr2axiTdrModelTap`, with the DM's DMI request handshakes. DM
answers pair each answer of `SbaRefModel`, as `Tdr2axiPredictor` publishes it with the
`sbbusy` window, with the DM's DMI response handshakes. Bus transactions pair each
predicted AR, or AW with its W, with the `AxiSub` handshakes and the R and B response
codes. Capture status pairs each capture image of `TdrStatusModel` with `o_dmi_tdr` at the
capture edge. The `checker` checks are made where the table says: `AxiSub` and `TdrHost`
on every handshake and scan, `Tdr2axiTimingMonitor` on the TCK edge counts of the
round-trip and CDC-clear timing items at their named clock ratio, and the test coroutines
and `Tdr2axiTb` helpers on the directed comparisons of their scenarios.

| Check | Kind | Feature | Made in | Feed | Timing item | Model | Comparison |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `CHK-AXI-AXI4-SIZE` | scoreboard | `F-AXI-axi4-size` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`) | in-order |
| `CHK-AXI-AXI4-SIZE-SVA` | assertion | `F-AXI-axi4-size` | formal harness `tdr2axi_axi_attr_formal` | AR and AW size of `o_axi_req`, `sbaccess` |  |  |  |
| `CHK-AXI-HANDSHAKE-SVA` | assertion | `F-AXI-manager-protocol` | `tdr2axi_axi_mgr_sva`, bound in `tb_top` | flattened AXI nets |  |  |  |
| `CHK-AXI-LITE-FULL-WIDTH` | scoreboard | `F-AXI-lite-full-width` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_sbdata`) | in-order |
| `CHK-AXI-NO-COMB-PATH` | checker | `F-AXI-no-comb-path` | `AxiSub` | `o_axi_req` sampled between `i_clk` edges |  |  |  |
| `CHK-AXI-NO-COMB-PATH-SVA` | assertion | `F-AXI-no-comb-path` | formal harness `tdr2axi_axi_comb_path_formal` | `o_axi_req`, `i_axi_rsp` |  |  |  |
| `CHK-AXI-ONE-OUTSTANDING` | checker | `F-AXI-one-outstanding` | `AxiSub` | every AXI handshake |  |  |  |
| `CHK-AXI-ONE-OUTSTANDING-SVA` | assertion | `F-AXI-one-outstanding` | `tdr2axi_axi_mgr_sva`, bound in `tb_top` | flattened AXI nets |  |  |  |
| `CHK-AXI-REQ-ATTRS` | checker | `F-AXI-request-attributes` | `AxiSub` | AR and AW fields |  | build parameters from `tb_top` |  |
| `CHK-AXI-REQ-ATTRS-SVA` | assertion | `F-AXI-request-attributes` | `tdr2axi_axi_mgr_sva`, bound in `tb_top`; formal harness `tdr2axi_axi_attr_formal` | flattened AXI nets |  |  |  |
| `CHK-CFG-BENDER-TARGET` | checker | `F-CFG-bender-target` | `scripts/run_sim.py` | Bender file list; build log |  |  |  |
| `CHK-CFG-ILLEGAL-PARAMS` | checker | `F-CFG-param-checks` | `scripts/run_sim.py`, build-only mode | elaboration log per build |  |  |  |
| `CHK-DM-CMDERR` | scoreboard | `F-DM-abstract-cmd-unsupported` | `tdr2axi_dm_registers_test` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_write`, `predict_abstractcs`) |  |
| `CHK-DM-DMACTIVE-ALONE` | checker | `F-DM-dmactive-clear` | `tdr2axi_dmactive_reset_test` | `TdrHost` DMI results and scan records; `AxiSub` transaction stream; `AxiSub` memory |  | `SbaRefModel` (`predict_dmi_write`, `predict_dmcontrol`, `predict_sbcs`, `predict_memory`), `TdrStatusModel` (`predict_capture`) |  |
| `CHK-DM-DMACTIVE-CLEAR` | scoreboard | `F-DM-dmactive-clear` | `Tdr2axiScoreboard` | DM answers |  | `SbaRefModel` (`predict_dmi_write`, `predict_sbcs`, `predict_sbaddress`, `predict_sbdata`, `predict_dmcontrol`, `predict_abstractcs`) | state |
| `CHK-DM-DMACTIVE-INFLIGHT` | checker | `F-DM-dmactive-inflight` | `AxiSub` | AR, AW and W fields across the dmcontrol write |  |  |  |
| `CHK-DM-DMACTIVE-INFLIGHT-SVA` | assertion | `F-DM-dmactive-inflight` | formal harness `tdr2axi_dmactive_inflight_formal` | `o_axi_req` |  |  |  |
| `CHK-DM-DMACTIVE-NO-BUS` | checker | `F-DM-dmactive-no-bus-access` | `Tdr2axiTb` | `AxiSub` transaction stream; `dmactive` observable |  | `SbaRefModel` (`predict_dmi_write`) |  |
| `CHK-DM-DMACTIVE-NO-BUS-SVA` | assertion | `F-DM-dmactive-no-bus-access` | formal harness `tdr2axi_dmactive_no_bus_formal` | `o_axi_req`, `dmactive` |  |  |  |
| `CHK-DM-HART-UNAVAIL` | scoreboard | `F-DM-hart-unavailable` | `tdr2axi_dm_registers_test` | `TdrHost` DMI results |  | `SbaRefModel` (`predict_dmi_write`, `predict_dmstatus`) |  |
| `CHK-DM-NDMRESET` | scoreboard | `F-DM-ndmreset-unconnected` | `tdr2axi_dm_registers_test` | `TdrHost` DMI results; `AxiSub` transaction stream; `AxiSub` memory |  | `SbaRefModel` (`predict_dmi_write`, `predict_memory`, `predict_sbaddress`, `predict_dmcontrol`) |  |
| `CHK-DM-OP3-KEEPS-DM` | scoreboard | `F-DM-dmactive-clear` | `Tdr2axiScoreboard` | DM answers; capture status |  | `SbaRefModel`, `TdrStatusModel` (`predict_update`, `predict_capture`) | state |
| `CHK-DM-REGISTER-MAP` | scoreboard | `F-DM-register-map` | `tdr2axi_bringup_test`, `tdr2axi_dm_registers_test` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_read`, `predict_dmi_write`, `predict_dmcontrol`) |  |
| `CHK-DM-VERSION` | scoreboard | `F-DM-version` | `tdr2axi_dm_registers_test` | `TdrHost` DMI results |  | `SbaRefModel` (`predict_sbcs`, `predict_dmstatus`) |  |
| `CHK-PORT-BUS-LIST` | checker | `F-PORT-bus-and-reset` | `Tdr2axiTb` | elaborated port list |  | port table of `doc/tdr2axi.md` |  |
| `CHK-PORT-TDR-LIST` | checker | `F-PORT-tdr-interface` | `Tdr2axiTb` | elaborated port list |  | port table of `doc/tdr2axi.md` |  |
| `CHK-RESET-ASYNC-ANY-TIME` | scoreboard | `F-RESET-async-any-time` | `tdr2axi_bringup_test`, `tdr2axi_reset_traffic_test` | `TdrHost` scan records; `TdrHost` DMI results |  | `TdrStatusModel` (`predict_update`, `predict_capture`), `SbaRefModel` (`predict_dmi_read`) |  |
| `CHK-RESET-CDC-CLEAR-1TO2` | checker | `F-RESET-sync-release` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges; synchronized reset and CDC clear-pending observables before each edge | `T-TDR2AXI-cdc-clear-1to2` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-RESET-CDC-CLEAR-1TO20` | checker | `F-RESET-sync-release` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges; synchronized reset and CDC clear-pending observables before each edge | `T-TDR2AXI-cdc-clear-1to20` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-RESET-CDC-CLEAR-20TO1` | checker | `F-RESET-sync-release` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges; synchronized reset and CDC clear-pending observables before each edge | `T-TDR2AXI-cdc-clear-20to1` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-RESET-CDC-CLEAR-20TO19` | checker | `F-RESET-sync-release` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges; synchronized reset and CDC clear-pending observables before each edge | `T-TDR2AXI-cdc-clear-20to19` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-RESET-CDC-CLEAR-20TO21` | checker | `F-RESET-sync-release` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges; synchronized reset and CDC clear-pending observables before each edge | `T-TDR2AXI-cdc-clear-20to21` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-RESET-NO-STALE-AXI` | checker | `F-RESET-async-any-time` | `tdr2axi_reset_traffic_test` | AXI handshakes across `i_arst_n` |  |  |  |
| `CHK-RESET-SYNC-RELEASE` | scoreboard | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | internal reset observables; clock edge counts | `T-TDR2AXI-tck-reset-release` | `TdrStatusModel` (`predict_reset_release`) |  |
| `CHK-RESET-SYNC-RELEASE-SS4` | checker | `F-RESET-sync-release` | `tdr2axi_reset_release_test` | internal reset observables; clock edge counts | `T-TDR2AXI-tck-reset-release-ss4` | `TdrStatusModel` (`predict_reset_release`) |  |
| `CHK-RESET-SYNC-RELEASE-SVA` | assertion | `F-RESET-sync-release` | formal harness `tdr2axi_reset_sync_formal` | both `rstgen_bypass` outputs | `T-TDR2AXI-tck-reset-release` |  |  |
| `CHK-RESET-TEST-MODE` | checker | `F-RESET-test-mode-bypass` | `Tdr2axiTb` | internal reset observables; `TdrHost` DMI results |  | `TdrStatusModel`, `SbaRefModel` |  |
| `CHK-RESET-TEST-MODE-SVA` | assertion | `F-RESET-test-mode-bypass` | formal harness `tdr2axi_reset_sync_formal` | both `rstgen_bypass` outputs |  |  |  |
| `CHK-RESET-TRST-INDEPENDENT` | scoreboard | `F-RESET-trst-independent` | `tdr2axi_trst_test` | `TdrHost` scan records; `AxiSub` transaction stream |  | `TdrStatusModel` (`predict_capture`), `SbaRefModel` |  |
| `CHK-SBA-ADDRESS-WIDTH` | scoreboard | `F-SBA-address-width` | `Tdr2axiScoreboard` | DM answers; bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_sbaddress`) | in-order |
| `CHK-SBA-BLOCK-READ` | scoreboard | `F-SBA-block-read` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_dmi_read`, `predict_sbaddress`, `predict_sbdata`) | in-order |
| `CHK-SBA-BLOCK-WRITE` | scoreboard | `F-SBA-block-write` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_dmi_write`, `predict_sbaddress`) | in-order |
| `CHK-SBA-BUS-ERROR` | scoreboard | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `Tdr2axiTb.sba_idle` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_sbcs`) |  |
| `CHK-SBA-DATA-LANES` | scoreboard | `F-SBA-data-lanes` | `Tdr2axiScoreboard` | DM answers; bus transactions |  | `SbaRefModel` (`predict_sbdata`) | in-order |
| `CHK-SBA-ERROR-BLOCKS` | checker | `F-SBA-bus-error` | `tdr2axi_sba_errors_test`, `Tdr2axiTb.sba_idle` | `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_read`, `predict_dmi_write`) |  |
| `CHK-SBA-MISALIGNED` | scoreboard | `F-SBA-access-errors` | `tdr2axi_sba_errors_test`, `Tdr2axiTb.sba_idle` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_write`, `predict_sbcs`) |  |
| `CHK-SBA-NO-INC-AFTER-ERROR` | scoreboard | `F-SBA-no-increment-after-error` | `Tdr2axiScoreboard` | DM answers |  | `SbaRefModel` (`predict_sbaddress`) | state |
| `CHK-SBA-NO-READ-ON-ADDR` | checker | `F-SBA-single-read` | `Tdr2axiTb` | `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_write`) |  |
| `CHK-SBA-OVERSIZE` | scoreboard | `F-SBA-access-errors` | `tdr2axi_sba_errors_test`, `Tdr2axiTb.sba_idle` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_write`, `predict_sbcs`) |  |
| `CHK-SBA-READ-NO-READONDATA` | checker | `F-SBA-block-read` | `Tdr2axiTb` | `TdrHost` DMI results; `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_read`, `predict_sbaddress`) |  |
| `CHK-SBA-SBBUSY` | scoreboard | `F-SBA-busy-status` | `Tdr2axiScoreboard` | DM answers |  | `SbaRefModel` (`predict_sbcs`) | state |
| `CHK-SBA-SBBUSYERROR` | scoreboard | `F-SBA-busy-status` | `Tdr2axiScoreboard` | DM answers |  | `SbaRefModel` (`predict_dmi_read`, `predict_dmi_write`, `predict_sbcs`), `TdrStatusModel` (`predict_capture`) | state |
| `CHK-SBA-SBCS-FIELDS` | scoreboard | `F-SBA-sbcs-fields` | `tdr2axi_bringup_test`, `tdr2axi_dm_registers_test`, `tdr2axi_reset_traffic_test` | `TdrHost` DMI results |  | `SbaRefModel` (`predict_sbcs`) |  |
| `CHK-SBA-SBDATA1-NO-BUS` | checker | `F-SBA-data-lanes` | `Tdr2axiTb` | `AxiSub` transaction stream |  | `SbaRefModel` (`predict_dmi_write`) |  |
| `CHK-SBA-SINGLE-READ` | scoreboard | `F-SBA-single-read` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_dmi_read`, `predict_dmi_write`, `predict_sbdata`) | in-order |
| `CHK-SBA-SINGLE-WRITE` | scoreboard | `F-SBA-single-write` | `Tdr2axiScoreboard` | bus transactions |  | `SbaRefModel` (`predict_bus_access`, `predict_dmi_write`) | in-order |
| `CHK-SBA-WRITE-MEMORY` | scoreboard | `F-SBA-single-write` | `Tdr2axiTb.mem_check` | `AxiSub` memory |  | `SbaRefModel` (`predict_memory`) |  |
| `CHK-TDR-BUSY-CAPTURE` | scoreboard | `F-TDR-sticky-busy` | `Tdr2axiScoreboard` | capture status |  | `TdrStatusModel` (`predict_capture`) | state |
| `CHK-TDR-BUSY-DM` | scoreboard | `F-TDR-sticky-busy` | `tdr2axi_sba_busy_test` | `TdrHost` scan records; DMI response observables |  | `TdrStatusModel` (`predict_capture`), `SbaRefModel` (`predict_dmi_read`) |  |
| `CHK-TDR-BUSY-RECOVERY` | scoreboard | `F-TDR-busy-recovery` | `tdr2axi_tdr_busy_recovery_test`, `Tdr2axiTb.expect_recovered` | `TdrHost` DMI results; `AxiSub` transaction stream; `AxiSub` memory |  | `TdrStatusModel` (`predict_update`, `predict_capture`), `SbaRefModel` (`predict_dmi_read`, `predict_memory`) |  |
| `CHK-TDR-BUSY-UPDATE` | scoreboard | `F-TDR-sticky-busy` | `tdr2axi_tdr_busy_update_test`, `tdr2axi_reset_traffic_test` | `TdrHost` scan records; DMI request observables |  | `TdrStatusModel` (`predict_update`, `predict_capture`) |  |
| `CHK-TDR-CAPTURE-EDGE` | checker | `F-TDR-strobes` | `TdrHost` | `o_dmi_tdr` at each capture edge |  | `TdrStatusModel` (`predict_capture`) |  |
| `CHK-TDR-CAPTURE-IMAGE` | scoreboard | `F-TDR-capture-image` | `TdrHost`, `tdr2axi_tdr_pipelined_test` | `TdrHost` scan records |  | `TdrStatusModel` (`predict_capture`) |  |
| `CHK-TDR-DM-RESP-CODES-SVA` | assertion | `F-TDR-sticky-busy` | formal harness `tdr2axi_dm_resp_formal` | response code of `u_dm_csrs` at each DMI response |  |  |  |
| `CHK-TDR-NEVER-FAILED` | checker | `F-TDR-sticky-busy` | `TdrHost` | `o_dmi_tdr[1:0]` at every capture edge |  |  |  |
| `CHK-TDR-OP-DECODE` | scoreboard | `F-TDR-op-decode` | `Tdr2axiScoreboard` | DMI requests |  | `TdrStatusModel` (`predict_update`) | in-order |
| `CHK-TDR-OP-DECODE-SVA` | assertion | `F-TDR-op-decode` | formal harness `tdr2axi_bridge_formal` | request offered to `u_cdc_req` |  |  |  |
| `CHK-TDR-OP3-CLEAR` | scoreboard | `F-TDR-op-decode` | `Tdr2axiTb.dmi_clear` | `TdrHost` scan records; DMI request observables |  | `TdrStatusModel` (`predict_update`, `predict_capture`) |  |
| `CHK-TDR-PIPELINED-READ` | scoreboard | `F-TDR-capture-image` | `tdr2axi_tdr_pipelined_test` | `TdrHost` scan records |  | `TdrStatusModel` (`predict_capture`), `SbaRefModel` (`predict_dmi_read`, `predict_sbdata`) |  |
| `CHK-TDR-RATIO-INVARIANT` | scoreboard | `F-TDR-any-clock-ratio` | `Tdr2axiScoreboard` | DM answers |  | `TdrStatusModel` (`predict_capture`), `SbaRefModel` (`predict_bus_access`, `predict_dmi_read`) | in-order |
| `CHK-TDR-RESEND-READ` | checker | `F-TDR-busy-recovery` | `tdr2axi_tdr_busy_recovery_test` | AR handshakes |  |  |  |
| `CHK-TDR-RESULT-LATENCY` | scoreboard | `F-TDR-result-latency` | `tdr2axi_tdr_result_latency_test` | DMI response observables; TCK edge count to `o_dmi_tdr` | `T-TDR2AXI-resp-to-tdr` | `TdrStatusModel` (`predict_result_window`) |  |
| `CHK-TDR-RESULT-LATENCY-SS4` | checker | `F-TDR-result-latency` | `tdr2axi_tdr_result_latency_test` | DMI response observables; TCK edge count to `o_dmi_tdr` | `T-TDR2AXI-resp-to-tdr-ss4` | `TdrStatusModel` (`predict_result_window`) |  |
| `CHK-TDR-ROUND-TRIP-SS3-1TO2` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss3-1to2` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS3-1TO20` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss3-1to20` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS3-20TO1` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss3-20to1` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS3-20TO19` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss3-20to19` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS3-20TO21` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss3-20to21` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS4-1TO2` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss4-1to2` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS4-1TO20` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss4-1to20` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS4-20TO1` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss4-20to1` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS4-20TO19` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss4-20to19` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-ROUND-TRIP-SS4-20TO21` | checker | `F-TDR-result-latency` | `Tdr2axiTimingMonitor` | `TdrHost` TCK rising edges and update words; bridge state and status before each edge; synchronized reset and CDC clear-pending observables | `T-TDR2AXI-round-trip-ss4-20to21` | the timing item's bounds, stored in `Tdr2axiTimingMonitor` |  |
| `CHK-TDR-STATUS-SVA` | assertion | `F-TDR-sticky-busy` | formal harness `tdr2axi_bridge_formal` | TDR update and capture strobes, `o_dmi_tdr`, DMI request and response handshakes |  |  |  |
| `CHK-TDR-STICKY-IGNORE` | scoreboard | `F-TDR-sticky-busy` | `Tdr2axiTb.expect_sticky_ignore` | `TdrHost` scan records; DMI request observables; `AxiSub` transaction stream |  | `TdrStatusModel` (`predict_update`, `predict_capture`) |  |
| `CHK-TDR-TCK-STOP-HOLD` | scoreboard | `F-TDR-tck-stop-hold` | `tdr2axi_tdr_tck_stop_test` | bridge observables; `o_dmi_tdr` | `T-TDR2AXI-resp-to-tdr` | `TdrStatusModel` (`predict_capture`, `predict_result_window`) |  |
| `CHK-TDR-UPDATE-EDGE` | scoreboard | `F-TDR-strobes` | `Tdr2axiScoreboard` | DMI requests; capture status |  | `TdrStatusModel` (`predict_update`) | in-order |

### Protocol Checks of the Models

| Check | Model | X-atoms | Rule |
| --- | --- | --- | --- |
| `CHK-AXISUB-BOUNDARY-4K` | `AxiSub` | `X-AXI4-boundary-4k` | No burst crosses a 4KB address boundary |
| `CHK-AXISUB-BURST-LENGTH` | `AxiSub` | `X-AXI4-burst-length` | AxLEN is legal for AxBURST: INCR 1 to 256 beats, FIXED 1 to 16, WRAP 2, 4, 8 or 16 from an aligned start |
| `CHK-AXISUB-CACHE-LEGAL` | `AxiSub` | `X-AXI4-cache-legal` | AxCACHE is one of the memory-type encodings, never a reserved value |
| `CHK-AXISUB-ID-ORDER` | `AxiSub` | `X-AXI4-id-order` | Each response carries its request's ID, and responses to one ID return in issue order |
| `CHK-AXISUB-LAST-BEAT` | `AxiSub` | `X-AXI4-last-beat` | WLAST is high on the final W beat of each write |
| `CHK-AXISUB-PAYLOAD-STABLE` | `AxiSub` | `X-AXI4-payload-stable`, `X-AXI4_LITE-payload-stable` | Every request field of AR, AW and W holds from VALID until the handshake |
| `CHK-AXISUB-READ-RESP-ORDER` | `AxiSub` | `X-AXI4-read-data-after-address`, `X-AXI4_LITE-read-data-after-address` | RVALID rises only after the AR handshake of the read it answers |
| `CHK-AXISUB-RESET-VALID-LOW` | `AxiSub` | `X-AXI4-reset-valid-low`, `X-AXI4_LITE-reset-valid-low` | No VALID is high in reset or before the first i_clk rising edge after release |
| `CHK-AXISUB-SIZE-WIDTH` | `AxiSub` | `X-AXI4-size-width` | AxSIZE never exceeds the data bus width |
| `CHK-AXISUB-STROBE-LANES` | `AxiSub` | `X-AXI4-strobe-lanes`, `X-AXI4_LITE-strobe-lanes` | WSTRB is high only on lanes the address and size select |
| `CHK-AXISUB-VALID-HELD` | `AxiSub` | `X-AXI4-valid-held`, `X-AXI4_LITE-valid-held` | ARVALID, AWVALID and WVALID stay high from assertion until the READY handshake |
| `CHK-AXISUB-VALID-INDEPENDENT` | `AxiSub` | `X-AXI4-valid-independent`, `X-AXI4_LITE-valid-independent` | With READY held low until VALID rises, every VALID still rises: no VALID waits for READY |
| `CHK-AXISUB-WRITE-RESP-ORDER` | `AxiSub` | `X-AXI4-write-response-after-data`, `X-AXI4_LITE-write-response-after-data` | BVALID rises only after the AW handshake and the last W handshake of that write |
| `CHK-TDRHOST-CAPTURE-DR` | `TdrHost` | `X-IEEE1149_1-capture-dr` | The capture strobe is high for exactly the one TCK rising edge that ends Capture-DR of this TDR |
| `CHK-TDRHOST-UPDATE-DR` | `TdrHost` | `X-IEEE1149_1-update-dr` | The update-stage value changes only on the falling TCK edge in Update-DR, and the update strobe covers the next rising edge |

## Clocks and Resets

| Clock | Ports | Range | Relation | Gating | Source |
|---|---|---|---|---|---|
| `CLK-BUS` | `i_clk` | none stated; the bench runs 20 times faster than TCK down to 8 times slower, drawn at random, and at the named `CFG-CLK-RATIO` points 20:1, 20:19, 20:21, 1:2 and 1:20 | asynchronous to `CLK-TCK` | stopped by the test to hold an operation pending | `Tdr2axiTb`, free-running with a phase offset from TCK |
| `CLK-TCK` | `i_tck` | none stated; 50 MHz in the default builds | asynchronous to `CLK-BUS` | runs only during scans and Run-Test/Idle; stopped between scans and whenever TRST changes | `TdrHost` |

| Reset | Ports | Polarity | Assert, release | Releases on | Scope |
|---|---|---|---|---|---|
| `RST-ARST-BUS` | `i_arst_n` | low | async, sync | `CLK-BUS` | the DM, the SBA engine, the AXI adapter and the `i_clk` halves of both CDCs |
| `RST-ARST-TCK` | `i_arst_n` | low | async, sync | `CLK-TCK` | the bridge state machine, its registers and sticky status, and the TCK halves of both CDCs |
| `RST-SCAN` | `i_arst_scan_n` | low | async, async | none | both internal resets while `i_test_mode` = 1 |
| `RST-TRST` | none: the host TAP's | low | async, async | none | nothing in `tdr2axi`; the bench models it as a TDR deselect with TCK stopped |
| `RST-DMACTIVE` | none: `dmcontrol.dmactive`, written over DMI | low | sync, sync | `CLK-BUS` | `dm_csrs` only: the SBA and abstract-command registers and `dmcontrol` except `dmactive`; `dm_sba`, the AXI adapter, both CDCs and the TCK side keep running, so an access in flight completes |

**Reset ladder.** `Tdr2axiTb.start()` holds `i_arst_n` low with `i_test_mode` = 0 and
`i_arst_scan_n` = 1 for at least ten `i_clk` cycles with TCK idle, releases it, and runs
at least `SYNC_STAGES` + 1 TCK edges so the TCK side leaves reset
(`T-TDR2AXI-tck-reset-release`). Both CDCs then run their clear sequence, which needs
edges of both clocks; the first operation scan recovers a busy capture with op 3 and a
retry. The helper ends the ladder by writing `dmcontrol` = 1. The subordinate resets
with every `i_arst_n` (`CHK-ENV-FABRIC-RESET`).

**Crossings:** `CDC-REQ`, `CDC-RESP` and `RDC-ARST`. Their structural sign-off belongs
to the integrating design; the bench checks their functional effect through
`F-TDR-any-clock-ratio` and `F-RESET-async-any-time`.

## Stimulus

Tests call the helper's operations; each operation is one DUT-level action.

| Operation | Owner | Action |
|---|---|---|
| `scan(value)` | `TdrHost` | one DR scan along a seeded TAP path; returns the captured word |
| `dmi(op, addr, data)` | `TdrHost` | one DMI operation and its NOP poll, with or without busy recovery |
| `sba_write(addr, size, data)`, `sba_read(addr, size)` | `Tdr2axiTb` | the single-access programming steps |
| `sba_block_write`, `sba_block_read` | `Tdr2axiTb` | the block-access programming steps |
| `sba_idle(err, busyerr)` | `Tdr2axiTb` | poll `sbcs` until `sbbusy` = 0, then return the flags |
| `stall(channels)`, `inject(addr, resp)` | `AxiSub` | hold READY low on channels; answer an address with SLVERR or DECERR |
| `stop_clk()`, `stop_tck(cycles)`, `reset(phase)`, `trst()` | `Tdr2axiTb`, `TdrHost` | clock stops, `i_arst_n` at a seeded phase, a TAP reset with TCK stopped |

## Checking

| Place | Component | Owns | Fails when |
|---|---|---|---|
| 1 | `Tdr2axiScoreboard`, fed by `SbaRefModel` and `TdrStatusModel` | the `scoreboard` checks | a mismatch, a lost, repeated or unpredicted item, or a declared check with no comparison |
| 2 | `AxiSub` and `TdrHost` | the `vip` checks and their `checker` checks on every handshake and scan | an illegal step |
| 3 | `Tdr2axiTimingMonitor` | the round-trip and CDC-clear `checker` checks | a TCK edge count outside its timing item's bounds |
| 4 | The test coroutines and `Tdr2axiTb` helpers | the other `checker` checks, against the models or the scenario's expected value | a mismatch |
| 5 | `tdr2axi_axi_mgr_sva` and the `tb_top` assertion | the `assertion` checks bound in simulation, and the `env` assertion on the TDR nets | a property fails under VCS |
| 6 | `Tdr2axiTb` evidence | `CHK-NONVAC`, `CHK-AXISUB-DRAIN` | no comparison, or a transaction left over |
| 7 | Formal jobs | the `assertion` checks that need a proof | a counterexample |

Observed values come only from the DUT's nets, the observables and the models'
monitors; expected values come only from the models.

The assumption checks hold the bench to the rules the specification places on the
debugger and the integration:

| Check | Made in | Assumption atoms | Rule |
| --- | --- | --- | --- |
| `CHK-ENV-FABRIC-RESET` | `AxiSub` | `S-TDR2AXI-0057` | The subordinate drops every pending response and lowers its readys with each i_arst_n, so no transaction survives on one side |
| `CHK-ENV-READONDATA-IDLE` | `TdrHost` DMI layer and `AxiSub` | `S-TDR2AXI-0047` | Before an sbdata0 read with sbreadondata set at a side-effecting location, the host's Run-Test/Idle count covers the full round trip, N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck) TCK edges, of which T-TDR2AXI-resp-to-tdr is the response leg, and the subordinate flags a second read of such a location |
| `CHK-ENV-SBA-BLOCK-READ-STEPS` | `TdrHost` DMI layer | `S-TDR2AXI-0080`, `S-TDR2AXI-0082` | The host's block read writes sbcs with sbreadonaddr, sbreadondata and sbautoincrement, then the address, and clears sbreadondata before reading the last element |
| `CHK-ENV-SBA-POLL` | `TdrHost` DMI layer | `S-TDR2AXI-0072`, `S-TDR2AXI-0078` | After each access the host reads sbcs until sbbusy = 0 and checks sberror and sbbusyerror before the next access or the data read |
| `CHK-ENV-SBA-READ-STEPS` | `TdrHost` DMI layer | `S-TDR2AXI-0074`, `S-TDR2AXI-0075` | The host's single read writes sbcs with sbaccess and sbreadonaddr, then sbaddress1 when needed, then sbaddress0 |
| `CHK-ENV-SBA-WRITE-STEPS` | `TdrHost` DMI layer | `S-TDR2AXI-0066`, `S-TDR2AXI-0067`, `S-TDR2AXI-0068`, `S-TDR2AXI-0069`, `S-TDR2AXI-0070` | The host's single write writes sbcs with sbaccess, then sbaddress1 when AXI_ADDR_WIDTH > 32, then sbaddress0, then sbdata1 for a 64-bit access, then sbdata0 |
| `CHK-ENV-TDR-UPDATE-STABLE` | `tb_top` assertion; assume in `tdr2axi_bridge_formal` | `S-TDR2AXI-0050` | i_dmi_tdr holds a known value from before until after each TCK rising edge with i_dmi_tdr_update high; an assume in the bridge's formal job |

## Coverage

| Module | Area | Features | Feed |
|---|---|---|---|
| `cov/sv/tdr2axi_fcov.sv` | TDR and DMI, AXI manager, system bus access, Debug Module, reset, ports and build | every feature | AXI and TDR nets, observables, build parameters, measured clock periods |

Detail in `TDR2AXI_FCOV.md`.

## Formal Harnesses

| Job | Harness cut | Clocks | Widths |
|---|---|---|---|
| `tdr2axi_bridge_formal` | `dmi_tdr_bridge` with both CDCs cut to free inputs under handshake assumes; response codes constrained to success or busy, as `tdr2axi_dm_resp_formal` proves | TCK only | full |
| `tdr2axi_dm_resp_formal` | `tdr2axi` without `u_dmi_tdr_bridge`, the `i_clk`-side DMI request free | `i_clk` only | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 |
| `tdr2axi_axi_mgr_formal` | `tdr2axi` without `u_dmi_tdr_bridge`, the `i_clk`-side DMI request free and `dmactive` held once set | `i_clk` only | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 and 64 |
| `tdr2axi_axi_attr_formal` | `tdr2axi` cut at the outputs of `u_dm_sba` and at `sbaccess`, so the adapter and the wiring of `tdr2axi.sv` are proved as built | `i_clk` only | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 and 64; `AXI_PROT` 3'b010 and 3'b101; `AXI_CACHE` 4'b0000 and 4'b1111 |
| `tdr2axi_axi_comb_path_formal` | the `tdr2axi_axi_mgr_formal` cut | multiclock, `i_axi_rsp` free on every tick | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 |
| `tdr2axi_dmactive_inflight_formal` | the `tdr2axi_axi_mgr_formal` cut with `dmcontrol` writes free | `i_clk` only | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 and 64 |
| `tdr2axi_dmactive_no_bus_formal` | the same cut as `tdr2axi_dmactive_inflight_formal` | `i_clk` only | `AXI_ADDR_WIDTH` 16, `AXI_DATA_WIDTH` 32 and 64 |
| `tdr2axi_reset_sync_formal` | the two `rstgen_bypass` instances | multiclock, either clock may stop | `SYNC_STAGES` 3 and 4 |

The cuts and properties are detailed per job in `TDR2AXI_VPLAN.md`.

## Source Selection

| Item | Value |
|---|---|
| Source targets | `bender script flist-plus -t tdr2axi -t tech_cells_generic:tech_cells_generic_exclude_deprecated` |
| Defines | `COMMON_CELLS_ASSERTS_OFF` for Verilator; none for VCS, so the PULP cells' own assertions compile there |
| Build targets | the `[[target]]` entries of `trace/configs.toml`, one build each |

## Build and Run

```bash
python3 tb/tdr2axi/scripts/run_sim.py --group smoke --jobs 8
python3 tb/tdr2axi/scripts/run_sim.py --group signoff --jobs 8
python3 tb/tdr2axi/scripts/run_sim.py --group signoff --sim vcs --cov
python3 tb/tdr2axi/scripts/run_sim.py --test tdr2axi_sba_single_test \
    --target axil_a32_d32 --seed 7 --ratio 1:2 --wave
python3 tb/tdr2axi/scripts/run_sim.py --test tdr2axi_clock_ratio_test \
    --target axi4_a64_d64 --target axi4_a64_d64_prot5_cachef_sync4 --ratio all --reseed
make -C tb/tdr2axi CONFIG=axi4_a64_d64 TESTCASE=tdr2axi_sba_single_test CLK_PERIOD_PS=19000
```

`--group smoke` runs the smoke group on the default build; `--group signoff` runs every
item on every build target with its testlist reseed count. `--target` (repeatable, or
`all`) picks builds, and `--cov` compiles code coverage into a build of its own.

The clock ratio has two modes. `--ratio random` sets `CLK_PERIOD_PS=random`, and the
bench draws the `i_clk` period log-uniform from 1000 to 160000 ps against
`TCK_PERIOD_PS` = 20000, `i_clk` from 20 times faster than TCK to 8 times slower.
`--ratio A:B` (repeatable, or `all`) runs a named `CFG-CLK-RATIO` point at its exact
period: 1000 ps at 20:1, 19000 at 20:19, 21000 at 20:21, 40000 at 1:2 and 400000 at
1:20. Both modes draw the phase between the clocks from the seed, and the signoff spread
covers the five named points and random draws. A check bound to one named ratio is
required only at that ratio; a ratio-invariant check is required in both modes.

## Differences Between the Bench and This Architecture

The bench in `tb/tdr2axi/` differs from the architecture above in these places.

| Component | Architecture | Bench |
|---|---|---|
| Tests | every simulated plan item in `testlists/all.toml` | the testlist holds `tdr2axi_bringup_test`, `tdr2axi_dm_registers_test`, `tdr2axi_sba_single_test`, `tdr2axi_sba_block_test`, `tdr2axi_sba_errors_test`, `tdr2axi_sba_busy_test`, `tdr2axi_tdr_busy_update_test`, `tdr2axi_tdr_pipelined_test`, `tdr2axi_tdr_busy_recovery_test` and `tdr2axi_reset_traffic_test`; the other simulated items have no test |
| Test names | one name per plan item | `tdr2axi_bringup_test` runs the scenario of `tdr2axi_smoke_test` |
| Test-mode pins | `i_test_mode` and `i_arst_scan_n` driven by the test | `Tdr2axiTb` holds them at 0 and 1, and no test changes them |
| Clock ratio | each run sets `CLK_PERIOD_PS` from its `--ratio`, random or a named point | the Makefile also names `axi4_a64_d64_slowclk`, `axi4_a64_d64` at 1:2, for `make` runs without the runner |
