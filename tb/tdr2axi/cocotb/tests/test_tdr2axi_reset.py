# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi resets: i_arst_n, the test-mode reset and the TAP reset, on the bench of
``Tdr2axiTb``.

* ``tdr2axi_reset_traffic_test``: seeded loops of i_arst_n asserted at a drawn phase with
  one operation held at a drawn stage: offered by the bridge FSM to the request CDC with
  TCK stopped after its update (TCK FSM), taken by the request CDC with i_clk stopped and
  the sticky busy set by a refused update (request CDC), answered by the DM with its
  response held in the response CDC by a TCK stop (DM), or on the bus with the
  subordinate holding it: ARVALID waiting for ARREADY (AR), AWVALID waiting after W was
  taken (AW), WVALID waiting after AW was taken or WVALID and AWVALID both waiting, in
  seeded shares (W), R withheld after the AR (R), B withheld after AW and W (B). The first
  loops take every stage once in seeded order. In a seeded share of the loops, and in at
  least one per run, a dmcontrol write of dmactive and ackhavereset before the operation
  clears havereset, which the assertion sets again. The subordinate resets alongside,
  dropping what it holds (CHK-ENV-FABRIC-RESET) and requiring every VALID low while i_arst_n
  is low (CHK-AXISUB-RESET-VALID-LOW). In a seeded share of the loops, and in at least one per
  run, a second i_arst_n cuts into the CDC clear sequence of the first release, in one of
  two forms. With both clocks running, it comes a drawn count of TCK edges after the
  later synchronized release, short of the run's T-TDR2AXI-cdc-clear window, while both
  synchronized resets are high and a clear is pending. With i_clk held, the i_clk side
  stays in reset, an operation is scanned into the clear sequence, where the request CDC
  refuses it, and the second assertion cuts it off. Each run has the first form, and the
  second as well when it has two second assertions or more. The final release comes either
  with both clocks running, or, in a seeded share and at least one loop per run, with
  i_clk held while a dmcontrol read and a NOP capture busy, cleared with op 3 once i_clk
  runs. At a ratio with ten TCK periods or more per i_clk period (1:20), a seeded share of
  those busy loops, at least one per run, releases with TCK stopped instead, and a run with
  two busy loops or more keeps at least one with i_clk held. In a release with TCK stopped,
  the i_clk side leaves reset first, and TCK runs three edges past the TCK side's release
  and stops while i_clk runs, which puts the TCK side's CDC clear sequences three handshake
  steps ahead. The dmcontrol read and the NOP then capture busy, and TCK stops on the edge
  the request CDC takes the read, which reaches an i_clk side that isolates it from the DM
  until TCK runs again. The first capture after each release comes before the CDC clear
  sequence ends.
  The scoreboard grades every capture and DM answer against the models, the first capture
  after a reset at address 0, data 0 and status 0 (CHK-RESET-ASYNC-ANY-TIME), and any
  transaction, DMI request or capture after it that only a cut-off operation explains
  (CHK-RESET-NO-STALE-AXI). The test checks that the bus stays quiet from the first
  assertion, no VALID raised and no handshake, until the next access; that sbcs reads its
  register reset value and dmcontrol.dmactive reads 0 while dmactive = 0
  (CHK-RESET-ASYNC-ANY-TIME); and that the first AR after dmactive is set again is the read
  of the cut-off operation's address (CHK-RESET-NO-STALE-AXI), returning the memory the
  subordinate holds, which a cut-off write changed only when the subordinate had taken its
  AW and W (CHK-SBA-SINGLE-READ). S-TDR2AXI-0026, -0051, -0052.
* ``tdr2axi_reset_release_test``: seeded loops of i_arst_n asserted and released at a
  drawn phase, with both clocks running, with TCK stopped or with i_clk stopped at the
  release. Both internal resets fall in the step i_arst_n falls. After it rises the TCK
  side releases SYNC_STAGES TCK edges later and the i_clk side SYNC_STAGES i_clk edges
  later, each counting only the edges of its own clock that arrive: the earliest count of
  each window of ``TdrStatusModel.predict_reset_release`` (T-TDR2AXI-tck-reset-release or
  T-TDR2AXI-tck-reset-release-ss4, and RST-ARST-BUS), whose extra edge needs a release
  inside the first synchronizer flop's sampling window. A side whose clock is stopped
  stays in reset (the scoreboard's CHK-RESET-SYNC-RELEASE at SYNC_STAGES 3, the test's
  CHK-RESET-SYNC-RELEASE-SS4 at 4). With i_clk stopped, in a seeded share of the loops
  and always in the first such loop, an operation scanned after the TCK side's release
  meets the CDC clear sequence, which cannot end before the i_clk side runs, and the
  scoreboard grades the busy capture that follows (CHK-TDR-BUSY-CAPTURE). The host's op 3
  retry then completes a dmcontrol read. In the first such loop and a seeded share of the
  others the retry starts at once, while the scanned read is pending in the clear
  sequence; in the rest it starts once that read has its answer and the bridge is back in
  IDLE. At a named ratio ``Tdr2axiTimingMonitor`` grades the CDC clear sequence against
  T-TDR2AXI-cdc-clear-<ratio>, and in a loop with no busy capture a DMI read whose
  Update-DR comes within two TCK edges past that item's maximum crosses without busy
  (CHK-RESET-CDC-CLEAR-<ratio>). Atoms S-TDR2AXI-0018, -0026, -0052, -0053. Each
  assertion and release lands on no edge of either clock, since zero-delay simulation
  resolves a coincident edge by event order rather than by the synchronizer.
* ``tdr2axi_reset_test_mode_test``: with i_test_mode = 1, i_arst_scan_n pulses, with i_clk
  running or with both clocks stopped and a write pending, take both internal resets down
  and up in the step it changes, reset the bridge and the DM, and drop the pending write.
  An i_arst_n pulse leaves both internal resets, dmactive, the sbcs controls and the
  bridge's address and data as they were: the first capture after it holds the address and
  data of the update before it, dmcontrol and sbcs read back what was written, and seeded
  SBA reads and writes then run on the bus with i_test_mode = 1. A second i_arst_n pulse
  starts in the step the subordinate takes an SBA read's AR and ends between edges after a
  drawn number of i_clk edges; the read completes and returns the image's data (CHK-RESET-TEST-MODE,
  S-TDR2AXI-0027, -0028, -0052). The models, the scoreboard and the subordinate keep their
  state across each pulse, and the scoreboard grades every capture, DM answer and bus
  access. The test returns to i_test_mode = 0 under i_arst_n.
* ``tdr2axi_trst_test``: an operation in flight (an sbaddress0 write that starts a bus
  read, an sbdata0 write, or an sbaddress0 read), in half the loops behind a refused update
  that set the sticky busy, meets a TAP reset of the host asserted and released with TCK
  stopped for 32 to 160 i_clk periods, and for at least 104 in the first case with no
  refused update. A bus operation's AXI request, AR or AW and W, is taken by the end of the
  TAP reset: from the operation's scan when alone, and from the TCK stop behind a refused
  update, where i_clk is stopped until the TAP reset. The sticky busy holds until op 3, the
  first capture after it shows the operation's result, and the read data or the memory
  image shows the access; the scoreboard grades each against the models
  (CHK-RESET-TRST-INDEPENDENT, S-TDR2AXI-0054, -0055, X-IEEE1149_1-trst). The TAP reset
  deselects the TDR and changes no tdr2axi pin, so only the models and the scoreboard see
  it.

The release, test-mode and TAP-reset tests require the scoreboard's CHK-TDR-OP-DECODE on
their scans. The release test also requires CHK-TDR-BUSY-CAPTURE, the test-mode test
CHK-SBA-SINGLE-READ and -WRITE, and the TAP-reset test CHK-SBA-SINGLE-READ or -WRITE for
each kind of bus access it runs and CHK-TDR-BUSY-CAPTURE and CHK-DM-OP3-KEEPS-DM when a
case runs behind a refused update.

``tdr2axi_reset_traffic_test`` logs each loop's draws: the ackhavereset write, the stage, the
operation, its size, address and data, the edges it is held at its stage, the second
assertion and its form, the release and its form, the phase of every assertion and release,
the edges each assertion is held, the data of every update the bridge refuses and the TCK
edges before each second assertion.

``NEGATIVE_KNOB`` names one fault per run and defaults off, and an armed fault logs a
warning: ``trst-arst`` pulses i_arst_n with each TAP reset, as a TRST wired to it would
(CHK-RESET-TRST-INDEPENDENT); ``test-mode-arst`` makes the models, the scoreboard and the
subordinate take i_arst_n as the reset input in control while i_test_mode = 1, so they reset
on the test-mode test's i_arst_n pulses and mispredict what follows; ``test-mode-arst-sub``
does the same to the subordinate alone, which then drops the read in flight at the second
pulse. Each loop knob sets its test's loop count; 0 runs none, so the test's own checks log
no pass (CHK-NONVAC).
"""

from __future__ import annotations

import math
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import Event, ReadOnly, Timer
from cocotb.utils import get_sim_time

import env.axi_sub
import env.tdr2axi_predictor
import tests.tdr2axi_base_test
from env.axi_sub import AxiAddr, AxiSubTiming, AxiW
from env.tdr2axi_scoreboard import SCOREBOARD_CHECKS
from env.tdr_host import (
    DMCONTROL,
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBCS_SBACCESS_LSB,
    SBCS_SBAUTOINCREMENT,
    SBCS_SBREADONADDR,
    SBCS_SBREADONDATA,
    SBDATA0,
    SBDATA1,
    TDR_ADDR_LSB,
    TDR_DATA_LSB,
    TDR_LEN,
    DmiOp,
    DmiStatus,
    resolve,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_base_test import SBCS_INACTIVE, SIZE_64, TIMEOUT_MS, WORD_MASK, Tdr2axiTb
from tests.tdr2axi_test_cfg import rng_for
from tests.tdr2axi_timing_monitor import BRIDGE_IDLE, CDC_CLEAR_EDGES, CDC_CLEAR_PREFIX

WORD = 2

NEGATIVE_KNOB = "TDR2AXI_RESET_NEGATIVE"
FAULTS = frozenset({"trst-arst", "test-mode-arst", "test-mode-arst-sub"})
NEGATIVE = os.environ.get(NEGATIVE_KNOB, "")

ASYNC_ANY_TIME = "CHK-RESET-ASYNC-ANY-TIME"
NO_STALE_AXI = "CHK-RESET-NO-STALE-AXI"
SYNC_RELEASE = "CHK-RESET-SYNC-RELEASE"
SYNC_RELEASE_SS4 = "CHK-RESET-SYNC-RELEASE-SS4"
TEST_MODE = "CHK-RESET-TEST-MODE"
TRST_INDEPENDENT = "CHK-RESET-TRST-INDEPENDENT"

# Scoreboard checks a test requires: every scan's update, the release test's busy capture,
# the test-mode test's bus accesses, and per TAP-reset case the bus access it starts and,
# behind a refused update, the busy capture and the op 3 clear
OP_DECODE = "CHK-TDR-OP-DECODE"
BUSY_CAPTURE = "CHK-TDR-BUSY-CAPTURE"
SINGLE_READ = "CHK-SBA-SINGLE-READ"
SINGLE_WRITE = "CHK-SBA-SINGLE-WRITE"
TRST_CASE_IDS = {
    "bus_read": (SINGLE_READ,),
    "bus_write": (SINGLE_WRITE,),
    "reg_read": (),
    "sticky": (BUSY_CAPTURE, "CHK-DM-OP3-KEEPS-DM"),
}

TRAFFIC_LOOPS_KNOB = "TDR2AXI_RESET_TRAFFIC_TEST_LOOPS"
TRAFFIC_LOOPS = 10
RELEASE_LOOPS_KNOB = "TDR2AXI_RESET_RELEASE_TEST_LOOPS"
RELEASE_LOOPS = 5
TEST_MODE_LOOPS_KNOB = "TDR2AXI_RESET_TEST_MODE_TEST_LOOPS"
TEST_MODE_LOOPS = 3
TRST_LOOPS_KNOB = "TDR2AXI_TRST_TEST_LOOPS"
TRST_LOOPS = 6

RELEASE_VARIANTS = ("both_running", "tck_stopped", "clk_stopped")
# i_clk edges and TCK cycles i_arst_n or i_arst_scan_n is held for
HOLD_CLK_MIN = 2
HOLD_CLK_MAX = 8
HOLD_TCK_MAX = 3
# i_clk edges the i_clk side runs out of reset while TCK is stopped
TCK_STOPPED_CLK_MAX = 8
# i_clk periods between stop_clk and i_clk held low
CLK_STOP_SETTLE = 2
# Share of the clk_stopped release loops after the first that scan an operation into the
# CDC clear sequence
BUSY_SCAN_SHARE = 0.5
# Share of those loops after the first whose op 3 retry starts while the scanned read is
# pending
RETRY_IN_CLEAR_SHARE = 0.5

TDR_ADDR_MASK = 0x7F
DMACTIVE = 1
DMCONTROL_ACKHAVERESET = 1 << 28
REGION_OFFSET = 0x5000
REGION_BYTES = 0x80
# sbcs fields an sbcs write sets: sbreadonaddr, sbaccess, sbautoincrement, sbreadondata
SBCS_CONTROLS = (
    (1 << SBCS_SBREADONADDR)
    | (0b111 << SBCS_SBACCESS_LSB)
    | (1 << SBCS_SBAUTOINCREMENT)
    | (1 << SBCS_SBREADONDATA)
)
# SBA accesses after each i_arst_n pulse in test mode, beyond one read and one write
TEST_MODE_EXTRA_ACCESSES = 2
TRST_KINDS = ("bus_read", "bus_write", "reg_read")
# i_clk periods TCK stays stopped around the TAP reset: the DMI request crosses in
# SYNC_STAGES + 3 i_clk cycles and the subordinate answers within its READY wait and
# latency of at most 10 cycles. A long stop keeps TCK stopped past the 100th i_clk edge
# after the first one that follows the last TCK edge, the count at which
# c_rst_trst_op_in_flight sees the bridge outside IDLE.
TRST_STOP_MIN = 32
TRST_STOP_MAX = 160
TRST_LONG_STOP_MIN = 104
# Run-Test/Idle cycles after the restart beyond SYNC_STAGES + 1, the count that brings a
# waiting response to o_dmi_tdr before the next capture (T-TDR2AXI-resp-to-tdr)
TRST_RTI_EXTRA = 8

# The operations each stage of the traffic test can hold: an sbaddress0 write with
# sbreadonaddr or an sbdata0 read with sbreadondata starts a bus read, an sbdata0 write a
# bus write, and an sbaddress0 write with sbreadonaddr clear or an sbaddress0 read no bus
# access
STAGE_KINDS = {
    "tck_fsm": ("addr_read", "data_read", "data_write"),
    "cdc_req": ("addr_read", "data_read", "data_write"),
    "dm": ("reg_write", "reg_read"),
    "ar": ("addr_read", "data_read"),
    "aw": ("data_write",),
    "w": ("data_write",),
    "r": ("addr_read", "data_read"),
    "b": ("data_write",),
}
STAGES = tuple(STAGE_KINDS)
# Shares of the traffic loops with a second assertion in the CDC clear sequence and with a
# busy capture before the bridge is ready; each also comes in at least one loop per run
SECOND_SHARE = 0.3
BUSY_RELEASE_SHARE = 0.3
# Share of the second assertions whose first release has both clocks running; at least one
# per run, and with two or more second assertions at least one with i_clk held
LIVE_CLEAR_SHARE = 0.5
# Share of the W-stage loops whose W waits after AW was taken; the rest hold both waiting
W_ALONE_SHARE = 0.5
# Share of the traffic loops that write ackhavereset before the operation, at least one per
# run
ACK_HAVERESET_SHARE = 0.5
# Share of the busy releases whose read crosses into the isolated i_clk side, at least one per
# run at a ratio that allows it, and with two busy releases or more at least one with i_clk
# held
CROSS_SHARE = 0.5
# TCK edges after the TCK side leaves reset in which, in the first phase of the clear
# sequence, it withdraws its isolate request, which the i_clk side has acknowledged, while
# its own acknowledge of the i_clk side's request needs a fourth
CROSS_TCK_EDGES = 3
# TCK periods per i_clk period below which the scans of the crossing read can outlast the
# clear sequence: of CFG-CLK-RATIO, 1:20 alone
CROSS_MIN_RATIO = 10
# i_clk edges the read waits to cross, beyond the clear sequence at any ratio that allows it
CROSS_CLK_MAX = 64
# i_clk edges with TCK stopped in which the i_clk side completes a step of the clear sequence
# that needs no TCK edge: past the synchronizer's as the i_clk side leaves reset, its
# acknowledge of the TCK side's isolate request, and after the TCK side's release, its
# acknowledge of that request's withdrawal
CLK_FIRST_EDGES = 6
TRAFFIC_OFFSET = 0x7000
TRAFFIC_SPAN = 0x100
# i_clk edges, or i_clk periods with i_clk stopped, an operation stays at its stage before
# the drawn assertion phase
DWELL_MAX = 8
# i_clk edges after its scan by which the DM answers an operation or its bus request
# reaches the subordinate
STAGE_WAIT_CLK = 64
# i_clk edges the subordinate withholds R or B for in the R and B stages, beyond any
# assertion at every clock ratio
RESPONSE_HOLD = 1 << 20
# dmi_tdr_bridge states as bridge_state carries them: READ and WRITE offer the request to
# the request CDC, WAIT_READ_VALID and WAIT_WRITE_VALID wait for its answer
BRIDGE_OFFERING = frozenset({1, 3})
BRIDGE_AWAITING = frozenset({2, 4})
CDC_CLEAR_NETS = (
    "cdc_req_src_clear_pending",
    "cdc_req_dst_clear_pending",
    "cdc_resp_src_clear_pending",
    "cdc_resp_dst_clear_pending",
)
# Simulated-time limit of the traffic test: TRAFFIC_LOOPS loops at the slowest i_clk of
# CFG-CLK-RATIO
TRAFFIC_TIMEOUT_MS = 5 * TIMEOUT_MS


def _fault(tb: Tdr2axiTb, name: str) -> bool:
    """Whether this run arms fault ``name`` of ``NEGATIVE_KNOB``; an armed fault logs a
    warning each time it acts."""
    if NEGATIVE and NEGATIVE not in FAULTS:
        raise ValueError(f"{NEGATIVE_KNOB}={NEGATIVE}: use one of {sorted(FAULTS)}")
    if NEGATIVE != name:
        return False
    tb.log.warning("%s: %s=%s armed at %.0f ns", tb.name, NEGATIVE_KNOB, name, get_sim_time("ns"))
    return True


def _arst_input(dut: SimHandleBase) -> str:
    del dut
    return "rst_n"


def _arst_low(dut: SimHandleBase) -> bool:
    return resolve(dut.rst_n) == 0


def _arst_entered(dut: SimHandleBase) -> bool:
    return resolve(dut.rst_n) == 0 and resolve(dut.rst_scan_n) == 1


def _arm_test_mode_arst(tb: Tdr2axiTb) -> None:
    """With ``test-mode-arst`` armed, the predictor, the base test's scoreboard watchers and
    the subordinate take i_arst_n as the reset input in control whatever i_test_mode is;
    with ``test-mode-arst-sub`` armed, the subordinate alone does."""
    if _fault(tb, "test-mode-arst"):
        modules = (env.tdr2axi_predictor, tests.tdr2axi_base_test, env.axi_sub)
    elif _fault(tb, "test-mode-arst-sub"):
        modules = (env.axi_sub,)
    else:
        return
    for module in modules:
        module.reset_input = _arst_input
        module.in_reset = _arst_low
        module.entered_by_mode_change = _arst_entered


def _loops(knob: str, default: int) -> int:
    return int(os.environ.get(knob, str(default)))


def _off_edge_delay(tb: Tdr2axiTb) -> int:
    """Picoseconds from now to a drawn time within one period of the slower clock that is
    no rising or falling edge of i_clk or TCK. i_clk changes at ``tb.clk_edge_ps`` plus a
    whole number of half periods, and TCK at whole half periods from time 0."""
    cfg = tb.cfg
    now = round(get_sim_time("ps"))
    delay = tb.rng.randrange(1, max(cfg.clk_ps, cfg.tck_ps))
    while (now + delay - tb.clk_edge_ps) % (cfg.clk_ps // 2) == 0 or (now + delay) % (
        cfg.tck_ps // 2
    ) == 0:
        delay += 1
    return delay


def _edges_to_update(tb: Tdr2axiTb) -> int:
    """TCK rising edges one host scan makes before its update edge: Run-Test/Idle through
    Capture-DR (2), into Shift-DR (1), the shifts after the first (scan_len - 1), into
    Exit1-DR (1), through Pause-DR when the host pauses (pause_cycles + 1) and into
    Update-DR (1)."""
    cfg = tb.host.cfg
    pause = cfg.pause_cycles + 1 if cfg.pause_cycles else 0
    return cfg.scan_len + 4 + pause


def _internal_resets(tb: Tdr2axiTb) -> tuple[int, int]:
    """arst_clk_n and arst_tck_n."""
    return tb.observe("arst_clk_n"), tb.observe("arst_tck_n")


def _write_order(timing: AxiSubTiming) -> str:
    """Which write channel the subordinate's READY waits on, if either."""
    if timing.w_before_aw:
        return "AWREADY waits for a W"
    if timing.aw_before_w:
        return "WREADY waits for an AW"
    return "neither READY waits"


async def _clk_release_edges(tb: Tdr2axiTb, limit: int) -> int | None:
    """i_clk rising edges from now to the one after which arst_clk_n is high, or None when
    ``limit`` edges pass without it."""
    for edge in range(1, limit + 1):
        await tb.clk_cycles(1)
        await ReadOnly()
        if tb.observe("arst_clk_n"):
            return edge
    return None


class _TckEdges:
    """Counts the TCK rising edges the host makes, and finds the one after which arst_tck_n
    is high once ``arm`` has run."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self._tb = tb
        self.count = 0
        self._start = 0
        self._released: int | None = None
        self._armed = False
        tb.host.add_edge_callback(self._edge)

    def arm(self) -> None:
        """Start counting from now, the moment i_arst_n rises."""
        self._start = self.count
        self._released = None
        self._armed = True

    @property
    def released(self) -> int | None:
        """The edge index after which arst_tck_n was high, or None."""
        return self._released

    @property
    def to_release(self) -> int | None:
        """TCK rising edges from ``arm`` to the release, or None."""
        return None if self._released is None else self._released - self._start

    def _edge(self, *, capture: bool, update_word: int | None) -> None:
        """Called before each edge, with arst_tck_n as the previous edge left it."""
        del capture, update_word
        if self._armed and self._released is None and self._tb.observe("arst_tck_n"):
            self._released = self.count
            self._armed = False
        self.count += 1


# --- tdr2axi_reset_traffic_test -----------------------------------------------------------
@dataclass(frozen=True)
class _CutOff:
    """One traffic loop's draws: the operation i_arst_n cuts off, the stage it is held at
    and for how long, whether ackhavereset is written first, and the shape of the
    release."""

    stage: str
    kind: str
    size: int
    addr: int
    data: int
    dwell: int
    second: bool
    busy: bool
    live: bool
    w_alone: bool
    ack: bool
    cross: bool

    def describe(self) -> str:
        """Every draw of the loop, for its log line."""
        data = f" data 0x{self.data:x}" if self.kind == "data_write" else ""
        form = ""
        if self.stage == "w":
            form = ", W waiting after AW" if self.w_alone else ", W and AW waiting"
        second = "no"
        if self.second:
            second = "both clocks running" if self.live else "i_clk held"
        busy = "no"
        if self.busy:
            busy = "with i_clk held"
        if self.cross:
            busy = "with TCK held at the release and the read crossing into isolation"
        return (
            f"{'ackhavereset, ' if self.ack else ''}{self.stage} stage{form}, {self.kind} of "
            f"{1 << self.size} bytes at 0x{self.addr:x}{data}, held {self.dwell} edges, second "
            f"assertion in the CDC clear sequence {second}, busy capture before the bridge is "
            f"ready {busy}"
        )


class _ResetTraffic:
    """One i_arst_n assertion per ``cycle``, with an operation held at a drawn stage."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self.tb = tb
        self.base = tb.region + TRAFFIC_OFFSET
        self._timing = tb.sub.cfg.timing
        self._stall_on: str | None = None
        self._clk_held = False
        self._dm_requests = 0
        self._op_mark: Counter[str] = Counter()
        tb.mem_fill(self.base, TRAFFIC_SPAN)
        tb.sub.add_transaction_callback(self._on_handshake)

    def schedule(self, loops: int) -> list[_CutOff]:
        """Every stage once in seeded order, then drawn stages, from a stream of their own;
        the second assertion, the busy release and the ackhavereset write each in a seeded
        share and at least once when there is a loop; among the second assertions, both
        clocks running at the first release in a seeded share, at least once, and i_clk held
        at least once when there are two; among the busy releases at a ratio that allows it,
        the read crossing into isolation in a seeded share, at least once, and i_clk held at
        least once when there are two; and the W stage's form."""
        tb = self.tb
        rng = rng_for(tb.cfg.seed, f"{tb.name}:stages")
        stages = list(STAGES)
        rng.shuffle(stages)
        stages += [rng.choice(STAGES) for _ in range(loops - len(stages))]
        stages = stages[:loops]
        second = [rng.random() < SECOND_SHARE for _ in stages]
        busy = [rng.random() < BUSY_RELEASE_SHARE for _ in stages]
        for flags in (second, busy):
            if flags and not any(flags):
                flags[rng.randrange(loops)] = True
        seconds = [i for i, flag in enumerate(second) if flag]
        live = [flag and rng.random() < LIVE_CLEAR_SHARE for flag in second]
        if seconds and not any(live):
            live[rng.choice(seconds)] = True
        if len(seconds) > 1 and all(live[i] for i in seconds):
            live[rng.choice(seconds)] = False
        w_alone = [rng.random() < W_ALONE_SHARE for _ in stages]
        ack = [rng.random() < ACK_HAVERESET_SHARE for _ in stages]
        if ack and not any(ack):
            ack[rng.randrange(loops)] = True
        busies = [i for i, flag in enumerate(busy) if flag]
        cross = [flag and rng.random() < CROSS_SHARE for flag in busy]
        if busies and not any(cross):
            cross[rng.choice(busies)] = True
        if len(busies) > 1 and all(cross[i] for i in busies):
            cross[rng.choice(busies)] = False
        if tb.cfg.clk_ps < CROSS_MIN_RATIO * tb.cfg.tck_ps:
            cross = [False] * len(cross)
        flags = zip(second, busy, live, w_alone, ack, cross, strict=True)
        return [
            self._draw(stage, second=s, busy=b, live=v, w_alone=w, ack=a, cross=c)
            for stage, (s, b, v, w, a, c) in zip(stages, flags, strict=True)
        ]

    async def cycle(self, loop: int, cut: _CutOff) -> None:
        """Write ackhavereset when drawn, hold the operation at its stage, assert i_arst_n,
        in a second-assertion loop assert it again in the CDC clear sequence, release, and
        check what follows."""
        tb = self.tb
        if cut.ack:
            # Clears dmstatus.allhavereset and anyhavereset, which the assertion sets again
            await tb.dmi_write(DMCONTROL, DMACTIVE | DMCONTROL_ACKHAVERESET)
        word = await self._prepare(cut)
        await self._reach(cut, word)
        mark = await self._assert(f"loop {loop}: the {cut.stage} stage", lambda: self._held(cut))
        if cut.stage == "b":
            tb.ref_write(cut.addr, cut.size, cut.data)
        await self._hold()
        if cut.second and cut.live:
            await self._assert_in_live_clear(loop)
        elif cut.second:
            await self._assert_in_clear(loop)
        if cut.cross:
            await self._release_clk_first()
        else:
            await self._release(clk_held=cut.busy)
        if cut.busy:
            await self._busy_recovery(loop, cross=cut.cross)
        else:
            await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
            await tb.wait_reset_ready()
        await self._check_after(loop, cut, mark)

    def _draw(self, stage: str, **flags: bool) -> _CutOff:
        tb = self.tb
        size = tb.rng.randint(0, tb.max_size)
        return _CutOff(
            stage=stage,
            kind=tb.rng.choice(STAGE_KINDS[stage]),
            size=size,
            addr=self.base + tb.rng.randrange(0, TRAFFIC_SPAN, 1 << size),
            data=tb.rand_data(size),
            dwell=tb.rng.randint(0, DWELL_MAX),
            **flags,
        )

    async def _prepare(self, cut: _CutOff) -> int:
        """Program sbcs and the address registers the operation needs; returns its update
        word."""
        tb = self.tb
        if cut.kind == "addr_read":
            await tb.dmi_write(SBCS, sbcs_value(cut.size, readonaddr=True))
            if tb.build.addr_width > 32:
                await tb.dmi_write(SBADDRESS1, cut.addr >> 32)
            return tdr_word(DmiOp.WRITE, SBADDRESS0, cut.addr & WORD_MASK)
        if cut.kind == "data_read":
            await tb.dmi_write(SBCS, sbcs_value(cut.size, readondata=True))
            await tb.sba_set_addr(cut.addr)
            return tdr_word(DmiOp.READ, SBDATA0, 0)
        await tb.dmi_write(SBCS, sbcs_value(cut.size))
        if cut.kind == "data_write":
            await tb.sba_set_addr(cut.addr)
            if cut.size == SIZE_64:
                await tb.dmi_write(SBDATA1, cut.data >> 32)
            return tdr_word(DmiOp.WRITE, SBDATA0, cut.data & WORD_MASK)
        if cut.kind == "reg_write":
            return tdr_word(DmiOp.WRITE, SBADDRESS0, cut.addr & WORD_MASK)
        return tdr_word(DmiOp.READ, SBADDRESS0, 0)

    async def _reach(self, cut: _CutOff, word: int) -> None:
        """Scan the operation and hold it at its stage for ``cut.dwell`` edges."""
        tb = self.tb
        self._op_mark = tb.bus_mark()
        if cut.stage == "tck_fsm":
            # TCK stops on the update edge, before the request CDC samples the request
            await tb.scan(word, idle=0)
            await tb.clk_cycles(cut.dwell)
        elif cut.stage == "cdc_req":
            await self._stop_clk()
            await tb.scan(word)
            refused = tb.rng.getrandbits(32)
            tb.log.info("%s: sbdata0 write of 0x%08x refused behind it", tb.name, refused)
            await tb.scan(tdr_word(DmiOp.WRITE, SBDATA0, refused))
            await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
            await tb.wait_clk_periods(cut.dwell)
        elif cut.stage == "dm":
            # One TCK edge after the update hands the request to the request CDC; with TCK
            # stopped the answer then waits in the response CDC
            self._dm_requests = tb.predictor.dmi_requests
            await tb.scan(word, idle=1)
            await self._clk_until(lambda: self._held(cut))
            await tb.clk_cycles(cut.dwell)
        else:
            self._arm_subordinate(cut)
            await tb.scan(word, idle=tb.host.cfg.readondata_idle)
            await self._clk_until(lambda: self._held(cut))
            await tb.clk_cycles(cut.dwell)

    def _arm_subordinate(self, cut: _CutOff) -> None:
        """READY held low on every channel for AR and for W with AW; one write channel taken
        first and then every READY held for AW, and for W alone; R or B withheld for R and
        B."""
        sub = self.tb.sub
        stage = cut.stage
        if stage == "ar" or (stage == "w" and not cut.w_alone):
            sub.stall = True
        elif stage == "aw":
            timing = replace(self._timing, w_before_aw=True, aw_before_w=False)
            sub.cfg = replace(sub.cfg, timing=timing)
            self._stall_on = "w"
        elif stage == "w":
            timing = replace(self._timing, w_before_aw=False, aw_before_w=True)
            sub.cfg = replace(sub.cfg, timing=timing)
            self._stall_on = "aw"
        elif stage == "r":
            hold = (RESPONSE_HOLD, RESPONSE_HOLD)
            sub.cfg = replace(sub.cfg, timing=replace(self._timing, r_latency=hold))
        else:
            hold = (RESPONSE_HOLD, RESPONSE_HOLD)
            sub.cfg = replace(sub.cfg, timing=replace(self._timing, b_latency=hold))

    def _release_subordinate(self) -> None:
        sub = self.tb.sub
        sub.cfg = replace(sub.cfg, timing=self._timing)
        sub.stall = False
        self._stall_on = None

    def _on_handshake(self, channel: str, item: AxiAddr | AxiW) -> None:
        del item
        if channel == self._stall_on:
            self.tb.sub.stall = True
            self._stall_on = None

    def _held(self, cut: _CutOff) -> bool:
        """Whether the operation is at its stage now."""
        tb = self.tb
        stage = cut.stage
        seen = Counter(tb.sub.handshakes)
        seen.subtract(self._op_mark)
        if stage == "tck_fsm":
            offered = tb.observe("bridge_state") in BRIDGE_OFFERING
            return offered and tb.observe("cdc_req_src_valid") == 1
        if stage == "cdc_req":
            taken = tb.observe("bridge_state") in BRIDGE_AWAITING
            return taken and not tb.observe("dmi_req_valid")
        if stage == "dm":
            # The DM's response is valid from the edge after its request handshake until
            # the response CDC takes it
            taken = tb.observe("bridge_state") in BRIDGE_AWAITING
            asked = tb.predictor.dmi_requests > self._dm_requests
            return taken and asked and not tb.observe("dmi_resp_valid")
        if stage == "ar":
            return self._waits("ar")
        if stage == "aw":
            return seen["w"] > 0 and not seen["aw"] and self._waits("aw")
        if stage == "w" and cut.w_alone:
            return seen["aw"] > 0 and not seen["w"] and self._waits("w")
        if stage == "w":
            return self._waits("w") and self._waits("aw")
        # sbbusy stays high until the DM takes the response
        busy = bool(tb.observe("sbbusy"))
        if stage == "r":
            return seen["ar"] > 0 and busy and not tb.observe("r_valid")
        return seen["aw"] > 0 and seen["w"] > 0 and busy and not tb.observe("b_valid")

    def _waits(self, channel: str) -> bool:
        """VALID of ``channel`` high with its READY low."""
        tb = self.tb
        return bool(tb.observe(f"{channel}_valid")) and not tb.observe(f"{channel}_ready")

    def _in_clear(self) -> bool:
        """A CDC clear sequence is pending and the bridge offers a request."""
        tb = self.tb
        pending = any(tb.observe(net) for net in CDC_CLEAR_NETS)
        return pending and tb.observe("bridge_state") in BRIDGE_OFFERING

    def _in_live_clear(self) -> bool:
        """A CDC clear sequence is pending with both synchronized resets high."""
        tb = self.tb
        pending = any(tb.observe(net) for net in CDC_CLEAR_NETS)
        return pending and all(_internal_resets(tb))

    async def _clk_until(self, held: Callable[[], bool]) -> bool:
        """i_clk edges until ``held``, at most STAGE_WAIT_CLK; whether it held."""
        if held():
            return True
        for _ in range(STAGE_WAIT_CLK):
            await self.tb.clk_cycles(1)
            await ReadOnly()
            if held():
                return True
        return False

    async def _stop_clk(self) -> None:
        self.tb.stop_clk()
        self._clk_held = True
        await self.tb.wait_clk_periods(CLK_STOP_SETTLE)

    def _start_clk(self) -> None:
        self.tb.start_clk()
        self._clk_held = False

    async def _assert(self, what: str, held: Callable[[], bool]) -> Counter[str]:
        """i_arst_n falls at a drawn phase, on no edge of either clock, with ``what`` held;
        the subordinate's hold ends with it. Returns the handshake counts in that step, with
        the recorded transactions cleared."""
        tb = self.tb
        delay = _off_edge_delay(tb)
        await Timer(delay, "ps")
        tb.checker.require_stimulus(created=held(), reason=f"{what} is not held at i_arst_n")
        tb.log.info(
            "%s: %s, i_arst_n falls at %.0f ns, %d ps into the drawn phase",
            tb.name,
            what,
            get_sim_time("ns"),
            delay,
        )
        mark = tb.bus_mark()
        tb.clear_monitor()
        tb.assert_reset()
        self._release_subordinate()
        return mark

    async def _hold(self) -> None:
        """i_arst_n low for a drawn count of i_clk edges and then of TCK edges, with i_clk
        running."""
        tb = self.tb
        clk_edges = tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX)
        tck_edges = tb.rng.randint(0, HOLD_TCK_MAX)
        tb.log.info(
            "%s: i_arst_n low for %d i_clk edges, then %d TCK edges", tb.name, clk_edges, tck_edges
        )
        if self._clk_held:
            await tb.wait_clk_periods(1)
            self._start_clk()
        await tb.clk_cycles(clk_edges)
        await tb.tck_cycles(tck_edges)

    async def _release(self, *, clk_held: bool) -> None:
        """i_arst_n rises at a drawn phase, with i_clk held when ``clk_held``, and TCK runs
        until the TCK side is out of reset: SYNC_STAGES + 1 TCK edges at the latest
        (T-TDR2AXI-tck-reset-release, T-TDR2AXI-tck-reset-release-ss4)."""
        await self._rise(clk_held=clk_held)
        await self.tb.tck_cycles(self.tb.build.sync_stages + 2)

    async def _release_clk_first(self) -> None:
        """i_arst_n rises at a drawn phase with TCK stopped, and i_clk runs until its side is
        out of reset and has acknowledged the TCK side's isolate request. TCK then runs until
        the TCK side is out of reset and CROSS_TCK_EDGES edges more, and stops for
        CLK_FIRST_EDGES i_clk edges, in which the i_clk side acknowledges the withdrawal of
        that request. Through those edges the i_clk side's own isolate request waits for the
        TCK side's acknowledge, so in each CDC the TCK side's clear sequence leads by three
        of a phase's four handshake steps."""
        tb = self.tb
        await self._rise(clk_held=False)
        sync = tb.build.sync_stages
        await tb.clk_cycles(sync + CLK_FIRST_EDGES)
        edges = 0
        while not _internal_resets(tb)[1] and edges <= sync:
            await tb.tck_cycles(1)
            edges += 1
        await tb.tck_cycles(CROSS_TCK_EDGES)
        await tb.clk_cycles(CLK_FIRST_EDGES)

    async def _rise(self, *, clk_held: bool) -> None:
        """i_arst_n rises at a drawn phase, with i_clk held when ``clk_held``."""
        tb = self.tb
        if clk_held:
            await self._stop_clk()
        delay = _off_edge_delay(tb)
        await Timer(delay, "ps")
        tb.log.info(
            "%s: i_arst_n rises at %.0f ns, %d ps into the drawn phase, i_clk %s",
            tb.name,
            get_sim_time("ns"),
            delay,
            "held" if clk_held else "running",
        )
        tb.release_reset()

    async def _assert_in_clear(self, loop: int) -> None:
        """Release with i_clk held, so the CDC clear sequence cannot end; scan an sbdata0
        write or a dmcontrol read, which the bridge offers and the isolated request CDC
        refuses, and in a seeded share a NOP after it; then assert i_arst_n again."""
        tb = self.tb
        await self._release(clk_held=True)
        if tb.rng.random() < 0.5:
            data = tb.rng.getrandbits(32)
            word, what = tdr_word(DmiOp.WRITE, SBDATA0, data), f"sbdata0 write of 0x{data:08x}"
        else:
            word, what = tdr_word(DmiOp.READ, DMCONTROL, 0), "dmcontrol read"
        nop = tb.rng.random() < 0.5
        edges = tb.rng.randint(0, HOLD_TCK_MAX)
        tb.log.info(
            "%s: loop %d: %s%s into the CDC clear sequence with i_clk held, then %d TCK edges",
            tb.name,
            loop,
            what,
            " and a NOP" if nop else "",
            edges,
        )
        await tb.scan(word)
        if nop:
            await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        await tb.tck_cycles(edges)
        await self._assert(f"loop {loop}: the {what} in the CDC clear sequence", self._in_clear)
        await self._hold()

    async def _assert_in_live_clear(self, loop: int) -> None:
        """Release with both clocks running; once both synchronized resets are high, a drawn
        count of TCK edges short of the fewest the CDC clear sequence takes at the run's
        ratio (T-TDR2AXI-cdc-clear-<ratio>), then assert i_arst_n again. TCK pauses for under
        one period of the slower clock at the assertion's drawn phase; the TCK side of the
        sequence ends only on a TCK edge, so the pause leaves the sequence pending."""
        tb = self.tb
        ratio = None if tb.cfg.clk_drawn else tb.timing.ratio
        # A drawn i_clk is at most as fast as at 20:1, whose window is the shortest
        window = CDC_CLEAR_EDGES.get(ratio or "", CDC_CLEAR_EDGES["20TO1"])[0]
        # The window counts from the edge before the first one that reads both resets high,
        # up to one edge before this loop reads them, so the draw ends two edges short of it
        edges = tb.rng.randint(0, window - 2)
        await self._rise(clk_held=False)
        sync = tb.build.sync_stages + 2
        limit = sync * math.ceil(tb.cfg.clk_ps / tb.cfg.tck_ps) + sync
        waited = 0
        while not all(_internal_resets(tb)) and waited < limit:
            await tb.tck_cycles(1)
            waited += 1
        tb.log.info(
            "%s: loop %d: both synchronized resets high after %d TCK edges, then %d TCK "
            "edges of the CDC clear sequence, at least %d long, both clocks running",
            tb.name,
            loop,
            waited,
            edges,
            window,
        )
        await tb.tck_cycles(edges)
        await self._assert(
            f"loop {loop}: the CDC clear sequence {edges} TCK edges after the later release",
            self._in_live_clear,
        )
        await self._hold()

    async def _busy_recovery(self, loop: int, *, cross: bool) -> None:
        """After a release with i_clk held, or with ``cross`` one with TCK stopped, a dmcontrol
        read the isolated request CDC refuses and a NOP whose capture reads busy; then i_clk
        runs, with ``cross`` the read crosses into the isolated i_clk side, and once the
        read has its answer the host clears the busy with op 3 and reads dmcontrol."""
        tb = self.tb
        # With no Run-Test/Idle cycles the scans end before the clear sequence's last phase
        idle = 0 if cross else None
        await tb.scan(tdr_word(DmiOp.READ, DMCONTROL, 0), idle=idle)
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=idle)
        tb.checker.require_stimulus(
            created=cap & 3 == DmiStatus.BUSY,
            reason=f"loop {loop}: no busy capture before the bridge is ready after i_arst_n",
        )
        self._start_clk()
        if cross:
            await self._cross_into_isolation(loop)
        cfg = tb.host.cfg
        for _ in range(cfg.reset_ready_idle + cfg.readondata_idle):
            if tb.observe("bridge_state") == BRIDGE_IDLE:
                break
            await tb.tck_cycles(1)
        tb.log.info("%s: loop %d: op 3 retry after a busy capture", tb.name, loop)
        await tb.dmi_read(DMCONTROL)

    async def _cross_into_isolation(self, loop: int) -> None:
        """TCK runs until the request CDC takes the dmcontrol read and then stops for
        SYNC_STAGES + 3 i_clk edges. With its clear sequence three steps ahead, the TCK side
        leaves isolation as soon as the i_clk side's last phase reaches it, and the read
        crosses while the i_clk side waits for TCK edges to end that phase, isolating the
        read from the DM."""
        tb = self.tb
        edges = 0
        limit = CROSS_CLK_MAX * math.ceil(tb.cfg.clk_ps / tb.cfg.tck_ps)
        while tb.observe("bridge_state") in BRIDGE_OFFERING and edges < limit:
            await tb.tck_cycles(1)
            edges += 1
        hold = tb.build.sync_stages + 3
        await tb.clk_cycles(hold)
        await ReadOnly()
        taken = tb.observe("bridge_state") in BRIDGE_AWAITING
        isolating = tb.observe("cdc_req_dst_clear_pending") == 1
        requested = tb.observe("dmi_req_valid") == 1
        tb.log.info(
            "%s: loop %d: dmcontrol read taken %d TCK edges after the NOP, then TCK held for "
            "%d i_clk edges: i_clk side %s, DMI request %s",
            tb.name,
            loop,
            edges,
            hold,
            "isolating" if isolating else "open",
            "valid" if requested else "not valid",
        )
        tb.checker.require_stimulus(
            created=taken and isolating and not requested,
            reason=f"loop {loop}: the dmcontrol read does not wait in the isolated i_clk side",
        )

    async def _check_after(self, loop: int, cut: _CutOff, mark: Counter[str]) -> None:
        """The bus quiet since the first assertion, the DM at its reset values, the first
        access after dmactive is set reading the cut-off operation's address, and a write
        after it completing."""
        tb = self.tb
        what = f"loop {loop}: {cut.stage} {cut.kind} cut off by i_arst_n"
        tb.expect_no_request(mark, NO_STALE_AXI, what)
        # While dmactive = 0 sbcs holds its register reset value (sbaccess = 2, every other
        # field 0); sbversion, sbasize and the supported sizes appear once dmactive = 1
        sbcs = await tb.dmi_read(SBCS)
        tb.checker.expect_equal(ASYNC_ANY_TIME, sbcs, SBCS_INACTIVE, f"{what}: sbcs")
        dmcontrol = await tb.dmi_read(DMCONTROL)
        tb.checker.expect_equal(ASYNC_ANY_TIME, dmcontrol & DMACTIVE, 0, f"{what}: dmactive")
        await tb.dmi_write(DMCONTROL, DMACTIVE)
        await tb.dmi_read(SBCS)
        got = await tb.sba_read(cut.addr, cut.size)
        tb.expect_read(cut.addr, cut.size, check_id=NO_STALE_AXI)
        tb.checker.expect_true(
            NO_STALE_AXI,
            passed=not tb.sub.ar_q,
            detail=f"{what}: {len(tb.sub.ar_q)} more AR after the read of its address",
        )
        tb.checker.expect_equal(
            SINGLE_READ, got, tb.ref_read(cut.addr, cut.size), f"{what}: read of its address"
        )
        await self._write_after(what)

    async def _write_after(self, what: str) -> None:
        """A write of drawn size, address and data: one AW and one W within STAGE_WAIT_CLK
        i_clk edges of its sbdata0 write and none more by its OKAY, then the written bytes in
        memory. A write that sends one channel alone gets no B, so the wait for the two
        handshakes is bounded."""
        tb = self.tb
        size = tb.rng.randint(0, tb.max_size)
        addr = self.base + tb.rng.randrange(0, TRAFFIC_SPAN, 1 << size)
        data = tb.rand_data(size)
        what = f"{what}: write of {1 << size} bytes at 0x{addr:x} data 0x{data:x} after it"
        tb.log.info("%s: %s", tb.name, what)
        await tb.dmi_write(SBCS, sbcs_value(size))
        await tb.sba_set_addr(addr)
        if size == SIZE_64:
            await tb.dmi_write(SBDATA1, data >> 32)
        mark = tb.bus_mark()

        def sent() -> Counter[str]:
            seen = Counter(tb.sub.handshakes)
            seen.subtract(mark)
            return seen

        def expect_one(when: str) -> None:
            """One AW, one W and no AR since the mark; a failure names both VALIDs and the
            subordinate's write order."""
            seen = sent()
            tb.checker.expect_true(
                NO_STALE_AXI,
                passed=seen["aw"] == 1 and seen["w"] == 1 and not seen["ar"],
                detail=f"{what}: AW {seen['aw']} W {seen['w']} AR {seen['ar']} {when}, "
                f"AWVALID {tb.observe('aw_valid')} WVALID {tb.observe('w_valid')} there, "
                f"{_write_order(tb.sub.cfg.timing)}; expected one AW and one W",
            )

        await tb.dmi_write(SBDATA0, data & WORD_MASK)
        await self._clk_until(lambda: sent()["aw"] > 0 and sent()["w"] > 0)
        expect_one(f"within {STAGE_WAIT_CLK} i_clk edges of its sbdata0 write")
        await tb.sba_idle(check_id=NO_STALE_AXI)
        expect_one("at its OKAY")
        tb.expect_write(addr, size, data, check_id=NO_STALE_AXI)
        tb.ref_write(addr, size, data)
        tb.mem_check(addr, 1 << size, check_id=NO_STALE_AXI)


@cocotb.test(timeout_time=TRAFFIC_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_reset_traffic_test(dut: SimHandleBase) -> None:
    """i_arst_n at any time resets the bridge and the DM together: an operation held at any
    stage is dropped, nothing from before the reset reaches the bus, and operations after
    the release complete."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_reset_traffic_test")
    run = _ResetTraffic(tb)
    loops = _loops(TRAFFIC_LOOPS_KNOB, TRAFFIC_LOOPS)
    for loop, cut in enumerate(run.schedule(loops), 1):
        tb.log.info("%s: loop %d of %d, %s", tb.name, loop, loops, cut.describe())
        await run.cycle(loop, cut)
    tb.finish()


# --- tdr2axi_reset_release_test -----------------------------------------------------------
class _ResetRelease:
    """One i_arst_n assertion and release per ``cycle``, graded against the windows of the
    reset entries and the CDC clear item of the run's ratio."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self.tb = tb
        self.tck = _TckEdges(tb)
        self.window = tb.predictor.tdr.predict_reset_release()
        self.check_id = SYNC_RELEASE_SS4 if tb.build.sync_stages == 4 else SYNC_RELEASE
        ratio = None if tb.cfg.clk_drawn else tb.timing.ratio
        self.clear_id = f"{CDC_CLEAR_PREFIX}{ratio}" if ratio else None
        self.clear_max = CDC_CLEAR_EDGES[ratio][1] if ratio in CDC_CLEAR_EDGES else None
        self.busy_scan = False
        self._busy_scans = 0
        self._retries_in_clear = 0

    async def cycle(self, variant: str) -> None:
        """Assert i_arst_n, release it in ``variant``, grade both releases, then either read
        dmcontrol once the CDC clear sequence has had its maximum, or, after a busy capture
        during the clear, read it through the host's op 3 retry; then set dmactive again."""
        tb = self.tb
        self.busy_scan = variant == "clk_stopped" and (
            not self._busy_scans or tb.rng.random() < BUSY_SCAN_SHARE
        )
        await self._assert()
        release = getattr(self, f"_release_{variant}")
        clk_edges, tck_edges, clk_at = await release()
        self._grade(variant, clk_edges, tck_edges)
        later = max(self.tck.released if self.tck.released is not None else 0, clk_at)
        if self.busy_scan:
            self._busy_scans += 1
            draw = tb.rng.random() < RETRY_IN_CLEAR_SHARE
            if not self._retries_in_clear or draw:
                self._retries_in_clear += 1
                state = tb.observe("bridge_state")
                tb.log.info("%s: op 3 retry at once, bridge state %d", tb.name, state)
                tb.checker.require_stimulus(
                    created=state != BRIDGE_IDLE,
                    reason="the dmcontrol read is not pending at the immediate op 3 retry",
                )
            else:
                await self._await_bridge_idle()
                tb.log.info("%s: op 3 retry with the bridge in IDLE", tb.name)
            await tb.dmi_read(DMCONTROL)
        else:
            await self._read_after_clear(later)
        await tb.dmi_write(DMCONTROL, DMACTIVE)

    async def _scan_into_clear(self) -> None:
        """With the TCK side released and i_clk stopped, a dmcontrol read's update and then
        a NOP: the request cannot cross before the clear sequence ends, so the NOP's
        capture reads busy and sets the sticky status, which the scoreboard grades."""
        tb = self.tb
        tb.log.info("%s: dmcontrol read into the CDC clear sequence", tb.name)
        await tb.scan(tdr_word(DmiOp.READ, DMCONTROL, 0))
        await tb.scan(tdr_word(DmiOp.NOP, 0, 0))

    async def _await_bridge_idle(self) -> None:
        """TCK edges until the read scanned into the clear sequence has its answer and the
        bridge is back in IDLE, at most the clear item's maximum, or the host's reset-ready
        count at a drawn ratio, plus one DMI round trip."""
        tb = self.tb
        cfg = tb.host.cfg
        clear = self.clear_max if self.clear_max is not None else cfg.reset_ready_idle
        for _ in range(clear + cfg.readondata_idle):
            if tb.observe("bridge_state") == BRIDGE_IDLE:
                return
            await tb.tck_cycles(1)

    async def _assert(self) -> None:
        tb = self.tb
        await Timer(_off_edge_delay(tb), "ps")
        before = _internal_resets(tb)
        tb.assert_reset()
        await ReadOnly()
        after = _internal_resets(tb)
        self._report(
            "internal resets out of place as i_arst_n falls",
            2 - sum(before) + sum(after),
            0,
            passed=before == (1, 1) and after == (0, 0),
            detail=f"arst_clk_n, arst_tck_n {before} before i_arst_n fell and {after} in "
            "the step it fell",
        )
        await tb.clk_cycles(tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX))
        await tb.tck_cycles(tb.rng.randint(0, HOLD_TCK_MAX))

    def _release(self) -> None:
        self.tb.release_reset()
        self.tck.arm()

    async def _release_both_running(self) -> tuple[int | None, int | None, int]:
        """Release between edges of both running clocks."""
        tb, w, cfg = self.tb, self.window, self.tb.cfg
        delay = _off_edge_delay(tb)
        tck_count = (
            math.ceil((delay + (w.clk.latest + 2) * cfg.clk_ps) / cfg.tck_ps) + w.tck.latest + 2
        )
        task = cocotb.start_soon(tb.tck_cycles(tck_count))
        await Timer(delay, "ps")
        self._release()
        clk_edges = await _clk_release_edges(tb, w.clk.latest + 2)
        clk_at = self.tck.count
        await task
        return clk_edges, self.tck.to_release, clk_at

    async def _release_tck_stopped(self) -> tuple[int | None, int | None, int]:
        """Release with TCK stopped: the TCK side stays in reset until TCK runs."""
        tb, w = self.tb, self.window
        await Timer(_off_edge_delay(tb), "ps")
        self._release()
        clk_edges = await _clk_release_edges(tb, w.clk.latest + 2)
        clk_at = self.tck.count
        await tb.clk_cycles(tb.rng.randint(1, TCK_STOPPED_CLK_MAX))
        await ReadOnly()
        held = tb.observe("arst_tck_n")
        self._report(
            "arst_tck_n with TCK stopped",
            held,
            0,
            passed=held == 0,
            detail=f"arst_tck_n {held} with no TCK edge since i_arst_n rose",
        )
        await tb.tck_cycles(w.tck.latest + 1)
        return clk_edges, self.tck.to_release, clk_at

    async def _release_clk_stopped(self) -> tuple[int | None, int | None, int]:
        """Release with i_clk stopped: the i_clk side stays in reset until i_clk runs."""
        tb, w, cfg = self.tb, self.window, self.tb.cfg
        tb.stop_clk()
        await tb.wait_clk_periods(CLK_STOP_SETTLE)
        await Timer(_off_edge_delay(tb), "ps")
        self._release()
        await tb.tck_cycles(w.tck.latest + 1 + tb.rng.randint(0, HOLD_TCK_MAX))
        await ReadOnly()
        held = tb.observe("arst_clk_n")
        self._report(
            "arst_clk_n with i_clk stopped",
            held,
            0,
            passed=held == 0,
            detail=f"arst_clk_n {held} with no i_clk edge since i_arst_n rose",
        )
        if self.busy_scan:
            await self._scan_into_clear()
        tb.start_clk()
        tck_count = math.ceil((w.clk.latest + 2) * cfg.clk_ps / cfg.tck_ps) + 2
        task = cocotb.start_soon(tb.tck_cycles(tck_count))
        clk_edges = await _clk_release_edges(tb, w.clk.latest + 2)
        clk_at = self.tck.count
        await task
        return clk_edges, self.tck.to_release, clk_at

    def _grade(self, variant: str, clk_edges: int | None, tck_edges: int | None) -> None:
        """Each side SYNC_STAGES edges of its own clock after the release: RST-ARST-BUS and
        the TCK item each add one edge only for a release inside the first synchronizer
        flop's sampling window, which a release on no edge of either clock never is."""
        tb, w = self.tb, self.window
        tb.log.info(
            "%s: release with %s, TCK side after %s TCK edges, i_clk side after %s i_clk edges",
            tb.name,
            variant,
            tck_edges,
            clk_edges,
        )
        self._report(
            f"{variant}: TCK edges to the TCK side's release",
            tck_edges,
            w.tck.earliest,
            passed=tck_edges == w.tck.earliest,
            detail=f"{variant}: TCK side released {tck_edges} TCK edges after i_arst_n rose, "
            f"{w.tck.earliest} expected off every edge, window {w.tck.earliest} to "
            f"{w.tck.latest} ({w.tck.timing})",
        )
        self._report(
            f"{variant}: i_clk edges to the i_clk side's release",
            clk_edges,
            w.clk.earliest,
            passed=clk_edges == w.clk.earliest,
            detail=f"{variant}: i_clk side released {clk_edges} i_clk edges after i_arst_n "
            f"rose, {w.clk.earliest} expected off every edge, window {w.clk.earliest} to "
            f"{w.clk.latest} ({w.clk.timing})",
        )

    def _report(
        self, label: str, value: int | None, expected: int, *, passed: bool, detail: str
    ) -> None:
        """One count of the release: the scoreboard grades CHK-RESET-SYNC-RELEASE against
        ``expected``, the count the model allows off every clock edge, and the test grades
        CHK-RESET-SYNC-RELEASE-SS4 through ``passed``."""
        if self.check_id in SCOREBOARD_CHECKS:
            self.tb.grade_event(self.check_id, label, value, window=(expected, expected))
        else:
            self.tb.checker.record(self.check_id, passed=passed, detail=detail)

    async def _read_after_clear(self, later: int) -> None:
        """A dmcontrol read whose update edge is the first or second TCK edge past the clear
        item's maximum from the later release must cross: its capture holds status OK and
        the read's address."""
        tb = self.tb
        limit = self.clear_max if self.clear_max is not None else tb.host.cfg.reset_ready_idle
        await tb.tck_cycles(max(0, limit + 1 - (self.tck.count - later) - _edges_to_update(tb)))
        await tb.scan(tdr_word(DmiOp.READ, DMCONTROL, 0))
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        if self.clear_id is None:
            return
        tb.checker.expect_true(
            self.clear_id,
            passed=cap & 3 == DmiStatus.OK and cap >> TDR_ADDR_LSB == DMCONTROL,
            detail=f"capture 0x{cap:0{(TDR_LEN + 3) // 4}x} after a dmcontrol read whose "
            f"Update-DR came more than {limit} TCK edges after the later release",
        )


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_reset_release_test(dut: SimHandleBase) -> None:
    """i_arst_n asserts both sides at once and each releases SYNC_STAGES edges of its own
    clock later; after the CDC clear sequence an operation crosses without busy."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_reset_release_test")
    run = _ResetRelease(tb)
    loops = _loops(RELEASE_LOOPS_KNOB, RELEASE_LOOPS)
    if loops:
        tb.checker.add_required({OP_DECODE})
    variants = list(RELEASE_VARIANTS)
    tb.rng.shuffle(variants)
    variants += [tb.rng.choice(RELEASE_VARIANTS) for _ in range(loops - len(variants))]
    if "clk_stopped" in variants[:loops]:
        tb.checker.add_required({BUSY_CAPTURE})
    for loop, variant in enumerate(variants[:loops], 1):
        tb.log.info("%s: loop %d of %d, %s", tb.name, loop, loops, variant)
        await run.cycle(variant)
    tb.finish()


# --- tdr2axi_reset_test_mode_test ---------------------------------------------------------
class _TestMode:
    """i_arst_scan_n and i_arst_n pulses while i_test_mode = 1."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self.tb = tb
        self.base = tb.region + REGION_OFFSET
        self._pulse_on_ar = False
        self._hold = 0
        self._held: Task | None = None
        self._ar_taken = Event()
        tb.mem_fill(self.base, REGION_BYTES)
        tb.sub.add_transaction_callback(self._on_handshake)

    async def enter(self) -> None:
        """i_test_mode rises with i_arst_scan_n high, between clock edges."""
        tb = self.tb
        await Timer(_off_edge_delay(tb), "ps")
        tb.set_test_mode(on=True)

    async def leave(self) -> None:
        """i_test_mode falls with i_arst_n low, so the functional reset takes over and
        releases through the synchronizers."""
        tb = self.tb
        await Timer(_off_edge_delay(tb), "ps")
        tb.assert_reset()
        await tb.clk_cycles(tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX))
        await Timer(_off_edge_delay(tb), "ps")
        tb.set_test_mode(on=False)
        await tb.clk_cycles(tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX))
        await tb.tck_cycles(tb.rng.randint(0, HOLD_TCK_MAX))
        tb.release_reset()
        await tb.wait_reset_ready()
        await tb.dmi_write(DMCONTROL, DMACTIVE)

    async def scan_reset(self, *, pending: bool) -> None:
        """Pulse i_arst_scan_n; with ``pending``, an sbdata0 write waits in the bridge and
        both clocks are stopped from before the pulse until after it."""
        tb = self.tb
        mark = tb.bus_mark()
        what = "i_arst_scan_n pulse with a write pending" if pending else "i_arst_scan_n pulse"
        tb.log.info("%s: i_test_mode = 1, %s", tb.name, what)
        if pending:
            await self._pend_write()
        await Timer(_off_edge_delay(tb), "ps")
        tb.assert_scan_reset()
        await ReadOnly()
        self._expect_resets((0, 0), f"{what}: in the step i_arst_scan_n fell")
        if not pending:
            clk_hold = tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX)
            tck_hold = tb.rng.randint(0, HOLD_TCK_MAX)
            tb.log.info(
                "%s: i_arst_scan_n low for %d i_clk edges, then %d TCK edges",
                tb.name,
                clk_hold,
                tck_hold,
            )
            await tb.clk_cycles(clk_hold)
            await tb.tck_cycles(tck_hold)
            await ReadOnly()
            self._expect_resets((0, 0), f"{what}: while i_arst_scan_n is low")
        await Timer(_off_edge_delay(tb), "ps")
        tb.release_scan_reset()
        await ReadOnly()
        self._expect_resets((1, 1), f"{what}: in the step i_arst_scan_n rose")
        if pending:
            await Timer(_off_edge_delay(tb), "ps")
            tb.start_clk()
        await tb.wait_reset_ready()
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        tb.checker.expect_equal(TEST_MODE, cap, 0, f"{what}: first capture after it")
        tb.checker.expect_equal(TEST_MODE, tb.observe("dmactive"), 0, f"{what}: dmactive")
        tb.expect_no_bus_since(mark, TEST_MODE, what)
        await tb.dmi_write(DMCONTROL, DMACTIVE)

    async def functional_reset(self) -> None:
        """Pulse i_arst_n with DM and bridge state set: none of it changes, the bridge and
        the DM answer as before the pulse, and seeded SBA accesses run on the bus."""
        tb = self.tb
        size = tb.rng.randint(0, tb.max_size)
        sbcs = sbcs_value(size, autoinc=True)
        addr, data = tb.rng.randrange(TDR_ADDR_MASK + 1), tb.rng.getrandbits(32)
        tb.log.info(
            "%s: i_arst_n pulse after sbcs 0x%08x and a NOP update of address 0x%02x data 0x%08x",
            tb.name,
            sbcs,
            addr,
            data,
        )
        await tb.dmi_write(SBCS, sbcs)
        await tb.scan(tdr_word(DmiOp.NOP, addr, data))
        state = self._state()
        await Timer(_off_edge_delay(tb), "ps")
        tb.assert_reset()
        await ReadOnly()
        self._expect_resets((1, 1), "in the step i_arst_n fell")
        await tb.clk_cycles(tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX))
        await tb.tck_cycles(tb.rng.randint(1, HOLD_TCK_MAX))
        await ReadOnly()
        self._expect_resets((1, 1), "while i_arst_n is low")
        self._expect_state(state, "while i_arst_n is low")
        await Timer(_off_edge_delay(tb), "ps")
        tb.release_reset()
        await ReadOnly()
        self._expect_resets((1, 1), "in the step i_arst_n rose")
        clk_edges = tb.build.sync_stages + 1
        await tb.clk_cycles(clk_edges)
        await tb.tck_cycles(clk_edges)
        await ReadOnly()
        self._expect_resets((1, 1), f"{clk_edges} edges of each clock after i_arst_n rose")
        self._expect_state(state, "after the i_arst_n pulse")
        await self._expect_answers(addr, data, sbcs)
        await self._bus_accesses()

    async def pulse_during_read(self) -> None:
        """An i_arst_n pulse from the step the subordinate takes an SBA read's AR, released
        between edges after a drawn number of i_clk edges past the AR's edge: both internal
        resets stay high, and the read completes with the image's data."""
        tb = self.tb
        size = tb.rng.randint(0, tb.max_size)
        addr = self.base + tb.rng.randrange(0, REGION_BYTES, 1 << size)
        hold = tb.rng.randint(HOLD_CLK_MIN, HOLD_CLK_MAX)
        tb.log.info(
            "%s: i_test_mode = 1, i_arst_n low from the AR of a %d-bit read at 0x%x for %d "
            "i_clk edges past it",
            tb.name,
            8 << size,
            addr,
            hold,
        )
        await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
        self._hold = hold
        self._ar_taken.clear()
        self._pulse_on_ar = True
        await tb.sba_set_addr(addr)
        await self._ar_taken.wait()
        await self._held
        await ReadOnly()
        self._expect_resets((1, 1), "i_arst_n low from the AR of an SBA read")
        await Timer(_off_edge_delay(tb), "ps")
        tb.release_reset()
        tb.expect_read(addr, size, check_id=TEST_MODE)
        got = await tb.sba_read_data(size)
        await tb.sba_idle()
        tb.checker.expect_equal(
            TEST_MODE,
            got,
            tb.ref_read(addr, size),
            "i_test_mode = 1, data of the SBA read in flight at the i_arst_n pulse",
        )

    def _on_handshake(self, channel: str, item: AxiAddr | AxiW) -> None:
        """In the step the subordinate takes the AR ``pulse_during_read`` waits for, assert
        i_arst_n and start counting the i_clk edges it holds for."""
        del item
        if channel != "ar" or not self._pulse_on_ar:
            return
        self._pulse_on_ar = False
        self.tb.assert_reset()
        self._held = cocotb.start_soon(self.tb.clk_cycles(self._hold))
        self._ar_taken.set()

    async def _expect_answers(self, addr: int, data: int, sbcs: int) -> None:
        """After the i_arst_n pulse the first capture holds the address and data of the NOP
        update before it with status OK, sbcs reads back its controls and dmcontrol reads
        back dmactive."""
        tb = self.tb
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        tb.checker.expect_equal(
            TEST_MODE,
            cap,
            (addr << TDR_ADDR_LSB) | (data << TDR_DATA_LSB),
            "i_test_mode = 1, first capture after the i_arst_n pulse",
        )
        got = await tb.dmi_read(SBCS)
        tb.checker.expect_equal(
            TEST_MODE,
            got & SBCS_CONTROLS,
            sbcs & SBCS_CONTROLS,
            "i_test_mode = 1, sbcs controls after the i_arst_n pulse",
        )
        got = await tb.dmi_read(DMCONTROL)
        tb.checker.expect_equal(
            TEST_MODE,
            got & DMACTIVE,
            DMACTIVE,
            "i_test_mode = 1, dmactive after the i_arst_n pulse",
        )

    async def _bus_accesses(self) -> None:
        """One SBA write, one SBA read and up to TEST_MODE_EXTRA_ACCESSES more of either
        kind, in seeded order, sizes and addresses inside the region; the scoreboard grades
        each access and the test checks the bus and the data."""
        tb = self.tb
        kinds = ["write", "read"]
        kinds += [tb.rng.choice(kinds) for _ in range(tb.rng.randint(0, TEST_MODE_EXTRA_ACCESSES))]
        tb.rng.shuffle(kinds)
        for step, kind in enumerate(kinds, 1):
            size = tb.rng.randint(0, tb.max_size)
            addr = self.base + tb.rng.randrange(0, REGION_BYTES, 1 << size)
            tb.log.info(
                "%s: i_test_mode = 1, SBA access %d of %d, %d-bit %s at 0x%x",
                tb.name,
                step,
                len(kinds),
                8 << size,
                kind,
                addr,
            )
            if kind == "write":
                data = tb.rand_data(size)
                await tb.sba_write(addr, size, data)
                tb.ref_write(addr, size, data)
                tb.expect_write(addr, size, data, check_id=TEST_MODE)
                tb.mem_check(addr, 1 << size, check_id=TEST_MODE)
            else:
                got = await tb.sba_read(addr, size)
                tb.expect_read(addr, size, check_id=TEST_MODE)
                tb.checker.expect_equal(
                    TEST_MODE, got, tb.ref_read(addr, size), "i_test_mode = 1, SBA read data"
                )

    async def _pend_write(self) -> None:
        """An sbdata0 write whose update the bridge takes with i_clk stopped."""
        tb = self.tb
        addr = self.base + tb.rng.randrange(0, REGION_BYTES, 1 << WORD)
        await tb.dmi_write(SBCS, sbcs_value(WORD))
        await tb.sba_set_addr(addr)
        tb.stop_clk()
        await tb.wait_clk_periods(CLK_STOP_SETTLE)
        await tb.scan(tdr_word(DmiOp.WRITE, SBDATA0, tb.rng.getrandbits(32)), idle=1)
        tb.checker.require_stimulus(
            created=tb.observe("bridge_state") != BRIDGE_IDLE,
            reason="the sbdata0 write is not pending at the i_arst_scan_n pulse",
        )

    def _state(self) -> dict[str, int]:
        nets = ("dmactive", "sbaccess", "sbautoincrement", "bridge_address", "bridge_data")
        return {net: self.tb.observe(net) for net in nets}

    def _expect_state(self, state: dict[str, int], when: str) -> None:
        now = self._state()
        self.tb.checker.record(
            TEST_MODE,
            passed=now == state,
            detail=f"i_test_mode = 1, {when}: DM and bridge state {now}, before the i_arst_n "
            f"pulse {state}",
        )

    def _expect_resets(self, expected: tuple[int, int], when: str) -> None:
        now = _internal_resets(self.tb)
        self.tb.checker.record(
            TEST_MODE,
            passed=now == expected,
            detail=f"i_test_mode = 1, {when}: arst_clk_n, arst_tck_n {now}, expected {expected}",
        )


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_reset_test_mode_test(dut: SimHandleBase) -> None:
    """With i_test_mode = 1, i_arst_scan_n alone resets and releases both sides with no
    synchronizer delay, and i_arst_n has no effect."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_reset_test_mode_test")
    mode = _TestMode(tb)
    loops = _loops(TEST_MODE_LOOPS_KNOB, TEST_MODE_LOOPS)
    if loops:
        tb.checker.add_required({OP_DECODE, SINGLE_READ, SINGLE_WRITE})
    _arm_test_mode_arst(tb)
    await mode.enter()
    for loop in range(1, loops + 1):
        tb.log.info("%s: loop %d of %d", tb.name, loop, loops)
        await mode.functional_reset()
        await mode.pulse_during_read()
        await mode.scan_reset(pending=False)
        await mode.scan_reset(pending=True)
    await mode.leave()
    tb.finish()


# --- tdr2axi_trst_test ----------------------------------------------------------------------
class _Trst:
    """One operation in flight across a TAP reset per ``case``."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self.tb = tb
        self.base = tb.region + REGION_OFFSET
        self._long_stop = False
        tb.mem_fill(self.base, REGION_BYTES)

    async def case(self, kind: str, *, sticky: bool) -> None:
        """Start operation ``kind``, put the TAP reset in while it is in flight, and check
        the bus, the sticky status and the capture after TCK runs again."""
        tb = self.tb
        size = tb.rng.randint(0, tb.max_size)
        addr = self.base + tb.rng.randrange(0, REGION_BYTES, 1 << size)
        word, data = await self._prepare(kind, size, addr)
        what = f"{kind} {'behind a refused update' if sticky else 'alone'}"
        tb.log.info("%s: %s, %d-bit access at 0x%x", tb.name, what, 8 << size, addr)
        if sticky:
            tb.stop_clk()
            await tb.wait_clk_periods(CLK_STOP_SETTLE)
        await tb.scan(word, idle=tb.rng.randint(1, tb.build.sync_stages + 2))
        tb.checker.require_stimulus(
            created=tb.observe("bridge_state") != BRIDGE_IDLE,
            reason=f"{what}: no operation in flight at the TAP reset",
        )
        if sticky:
            await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=1)
        await self._tap_reset(what, sticky=sticky)
        await tb.tck_cycles(tb.build.sync_stages + 1 + tb.rng.randint(0, TRST_RTI_EXTRA))
        # The scoreboard grades the captures, the busy one behind a refused update and the
        # one with the operation's result
        await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        if sticky:
            await tb.scan(tdr_word(DmiOp.CLEAR, 0, 0))
            await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        await self._finish(kind, size, addr, data)

    async def _prepare(self, kind: str, size: int, addr: int) -> tuple[int, int]:
        """Set sbcs and the address for ``kind``; returns the update word and the data a bus
        write carries."""
        tb = self.tb
        wide = tb.build.addr_width > 32
        if kind == "bus_read":
            await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
            if wide:
                await tb.dmi_write(SBADDRESS1, addr >> 32)
            return tdr_word(DmiOp.WRITE, SBADDRESS0, addr & WORD_MASK), 0
        if kind == "bus_write":
            data = tb.rand_data(size)
            await tb.dmi_write(SBCS, sbcs_value(size))
            await tb.sba_set_addr(addr)
            if size == SIZE_64:
                await tb.dmi_write(SBDATA1, data >> 32)
            return tdr_word(DmiOp.WRITE, SBDATA0, data & WORD_MASK), data
        await tb.dmi_write(SBCS, sbcs_value(WORD))
        await tb.dmi_write(SBADDRESS0, tb.rng.getrandbits(32))
        return tdr_word(DmiOp.READ, SBADDRESS0, 0), 0

    async def _tap_reset(self, what: str, *, sticky: bool) -> None:
        """TRST asserted and released with TCK stopped; i_clk runs meanwhile. The first case
        with no refused update stops TCK for at least TRST_LONG_STOP_MIN i_clk periods. The
        scoreboard grades that a bus operation's AXI request, AR or AW and W, is taken by the
        end of the TAP reset."""
        tb = self.tb
        low = TRST_STOP_MIN
        if not sticky and not self._long_stop:
            low, self._long_stop = TRST_LONG_STOP_MIN, True
        periods = tb.rng.randint(low, TRST_STOP_MAX)
        tb.log.info("%s: %s, TAP reset with TCK stopped for %d i_clk", tb.name, what, periods)
        tb.trst()
        arst = _fault(tb, "trst-arst")
        if arst:
            tb.assert_reset()
        if sticky:
            tb.start_clk()
        await tb.wait_clk_periods(periods)
        if arst:
            tb.release_reset()
        tb.trst_end()

    async def _finish(self, kind: str, size: int, addr: int, data: int) -> None:
        """Read back a bus read's data or check a bus write's memory, which the scoreboard
        grades, and leave the SBA idle."""
        tb = self.tb
        if kind == "bus_read":
            await tb.sba_read_data(size)
        elif kind == "bus_write":
            tb.ref_write(addr, size, data)
            tb.mem_check(addr, 1 << size, check_id=TRST_INDEPENDENT)
        tb.clear_monitor()
        await tb.sba_idle()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_trst_test(dut: SimHandleBase) -> None:
    """A TAP reset with TCK stopped leaves an operation in flight and the sticky status as
    they were: the operation completes and its result shows at the next capture."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_trst_test")
    trst = _Trst(tb)
    loops = _loops(TRST_LOOPS_KNOB, TRST_LOOPS)
    cases = [(kind, sticky) for kind in TRST_KINDS for sticky in (False, True)]
    tb.rng.shuffle(cases)
    cases += [(tb.rng.choice(TRST_KINDS), tb.rng.random() < 0.5) for _ in range(loops)]
    for kind, sticky in cases[:loops]:
        tb.checker.add_required({OP_DECODE, *TRST_CASE_IDS[kind]})
        if sticky:
            tb.checker.add_required(TRST_CASE_IDS["sticky"])
    for loop, (kind, sticky) in enumerate(cases[:loops], 1):
        tb.log.info("%s: loop %d of %d, %s, sticky %s", tb.name, loop, loops, kind, sticky)
        await trst.case(kind, sticky=sticky)
    tb.finish()
