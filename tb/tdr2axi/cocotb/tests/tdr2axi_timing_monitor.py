# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Round-trip and CDC-clear timing of tdr2axi, counted on the TCK rising edges the host makes.

``Tdr2axiTimingMonitor`` takes every TCK rising edge from the host's edge callback, with the
bridge FSM, its sticky status, both synchronized resets and the four isolate requests of the
CDCs as they stand before that edge. It owns two checks, each only at a named clock ratio
(CFG-CLK-RATIO) and against the stored bounds of its timing entry in clockreset.toml:

- CHK-TDR-ROUND-TRIP-SS<n>-<ratio>: from the Update-DR of op 1 or 2 with the bridge in IDLE,
  the status clear and both CDCs past their clear sequence, to the edge that returns the
  bridge to IDLE, every operation's count lies within T-TDR2AXI-round-trip-ss<n>-<ratio>.
  The bounds hold for free-running clocks: a TCK pause between host operations or an i_clk
  stop drops the operation in flight, and none starts while i_clk is stopped.
- CHK-RESET-CDC-CLEAR-<ratio>: from the later of the two synchronized releases after
  i_arst_n to the edge after which all four isolate requests are low, the count is within
  the maximum of T-TDR2AXI-cdc-clear-<ratio>.

A run at another ratio records neither. With ``watch_results`` running, it also measures, at
any ratio and grading nothing, each DMI result's way back: the DM's response handshake on
i_clk, kept in ``responses``, the TCK rising edges after it up to the edge that returns the
bridge FSM to IDLE, and o_dmi_tdr then, in ``arrivals``; and it keeps the word of every
capture edge in ``captures``. Edges are numbered from 1 in the order the host makes them, and a handshake
is placed after the last edge at or before it, so a TCK edge that coincides with it is not
counted. The result-latency and TCK-stop tests grade these against T-TDR2AXI-resp-to-tdr
and T-TDR2AXI-resp-to-tdr-ss4, each of which holds at one SYNC_STAGES.

``exercised_at`` says which of these IDs a run's SYNC_STAGES and ratio can show. There is no
SV twin: the bench has one cocotb realization.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import RisingEdge
from cocotb.utils import get_sim_time

import env.tdr2axi_predictor
from env.tdr_host import DmiOp, resolve

__all__ = [
    "RESULT_LATENCY_IDS",
    "RESULT_LATENCY_TIMING",
    "CaptureEdge",
    "ResponseHandshake",
    "ResultArrival",
    "Tdr2axiTimingMonitor",
    "exercised_at",
    "ratio_name",
]

BRIDGE_IDLE = 0
# dmi_tdr_bridge states: WAIT_READ_VALID, and every state from READ to WAIT_WRITE_VALID
BRIDGE_WAIT_READ = 2
BRIDGE_WAITING = frozenset({1, 2, 3, 4})
ROUND_TRIP_PREFIX = "CHK-TDR-ROUND-TRIP-SS"
CDC_CLEAR_PREFIX = "CHK-RESET-CDC-CLEAR-"

# CFG-CLK-RATIO values, i_clk:TCK in frequency, by (T_tck, T_clk) reduced
RATIO_NAMES = {
    (20, 1): "20TO1",
    (20, 19): "20TO19",
    (20, 21): "20TO21",
    (1, 2): "1TO2",
    (1, 20): "1TO20",
}
# (min, max) TCK rising edges of T-TDR2AXI-round-trip-ss<n>-<ratio>
ROUND_TRIP_EDGES = {
    (3, "20TO1"): (6, 6),
    (3, "20TO19"): (10, 11),
    (3, "20TO21"): (11, 12),
    (3, "1TO2"): (16, 18),
    (3, "1TO20"): (106, 126),
    (4, "20TO1"): (7, 7),
    (4, "20TO19"): (12, 13),
    (4, "20TO21"): (13, 14),
    (4, "1TO2"): (19, 21),
    (4, "1TO20"): (127, 147),
}
# (min, max) TCK rising edges of T-TDR2AXI-cdc-clear-<ratio> at any i_clk phase and release
# time
CDC_CLEAR_EDGES = {
    "20TO1": (28, 28),
    "20TO19": (46, 48),
    "20TO21": (48, 51),
    "1TO2": (73, 79),
    "1TO20": (563, 563),
}
# The result-latency check of each SYNC_STAGES with a timing item: T-TDR2AXI-resp-to-tdr
# holds the bounds at 3, T-TDR2AXI-resp-to-tdr-ss4 at 4
RESULT_LATENCY_IDS = {3: "CHK-TDR-RESULT-LATENCY", 4: "CHK-TDR-RESULT-LATENCY-SS4"}
RESULT_LATENCY_TIMING = {3: "T-TDR2AXI-resp-to-tdr", 4: "T-TDR2AXI-resp-to-tdr-ss4"}

Reporter = Callable[..., None]


def ratio_name(clk_ps: int, tck_ps: int) -> str | None:
    """The CFG-CLK-RATIO value the two periods realize, or None for any other ratio."""
    step = math.gcd(clk_ps, tck_ps)
    return RATIO_NAMES.get((tck_ps // step, clk_ps // step))


def exercised_at(check_id: str, *, sync_stages: int, ratio: str | None) -> bool:
    """Whether a run at this SYNC_STAGES and ratio can show ``check_id``: a round-trip or
    CDC-clear check needs its own ratio, and a round-trip check its own SYNC_STAGES.
    ``Tdr2axiBuild.exercises`` holds each per-depth check to its SYNC_STAGES."""
    if check_id.startswith(ROUND_TRIP_PREFIX):
        return check_id == f"{ROUND_TRIP_PREFIX}{sync_stages}-{ratio}"
    if check_id.startswith(CDC_CLEAR_PREFIX):
        return check_id == f"{CDC_CLEAR_PREFIX}{ratio}"
    return True


@dataclass(frozen=True)
class ResultArrival:
    """One DMI result's way back from the DM's response handshake to o_dmi_tdr.

    ``handshake_edge`` is the last TCK edge at or before the handshake and ``arrival_edge``
    the edge after which the bridge FSM is in IDLE; ``update_edge`` is the Update-DR edge of
    the operation. ``word`` is o_dmi_tdr just after the arrival edge, its status field as the
    strobes of the next edge leave it; ``resp_data`` and ``resp`` are the DM's response, None
    when no handshake preceded the arrival.
    """

    update_edge: int | None
    handshake_edge: int | None
    arrival_edge: int
    read: bool
    word: int
    resp_data: int | None
    resp: int | None

    @property
    def edges(self) -> int | None:
        """TCK rising edges from the handshake to the arrival, the arrival edge included."""
        if self.handshake_edge is None:
            return None
        return self.arrival_edge - self.handshake_edge


@dataclass(frozen=True)
class CaptureEdge:
    """o_dmi_tdr at one capture edge, as the TDR cell loads it."""

    edge: int
    word: int


@dataclass
class ResponseHandshake:
    """One DMI response handshake of the DM: its time, the last TCK edge at or before it,
    and the response."""

    time_ps: int
    edge: int
    data: int | None
    resp: int


class Tdr2axiTimingMonitor:
    """Passive: counts TCK rising edges and grades round trips and CDC clears, and while
    ``watch_results`` runs records result arrivals and capture words. It drives nothing, and
    at a ratio with no stored bounds it grades nothing."""

    def __init__(
        self,
        dut: SimHandleBase,
        *,
        sync_stages: int,
        clk_ps: int,
        tck_ps: int,
        report: Reporter,
    ) -> None:
        self._dut = dut
        self._report = report
        ratio = ratio_name(clk_ps, tck_ps)
        self.ratio = ratio
        self._round_trip = ROUND_TRIP_EDGES.get((sync_stages, ratio or ""))
        self._round_trip_id = f"{ROUND_TRIP_PREFIX}{sync_stages}-{ratio}"
        self._clear_max = CDC_CLEAR_EDGES[ratio][1] if ratio in CDC_CLEAR_EDGES else None
        self._clear_id = f"{CDC_CLEAR_PREFIX}{ratio}"
        self._tck_ps = tck_ps
        self._edge = 0
        self._edge_ps: int | None = None
        self._clk_running = True
        self._trip_start: int | None = None
        self._tck_release: int | None = None
        self._clk_release: int | None = None
        self._clear_open = True
        self._task: Task | None = None
        self._handshakes: list[ResponseHandshake] = []
        self._op_edge: int | None = None
        self._state_prev = BRIDGE_IDLE
        self.arrivals: list[ResultArrival] = []
        self.captures: list[CaptureEdge] = []
        self.responses: list[ResponseHandshake] = []
        self._arrival_callbacks: list[Callable[[ResultArrival], None]] = []

    def add_arrival_callback(self, callback: Callable[[ResultArrival], None]) -> None:
        """Call ``callback`` with each result arrival ``watch_results`` records."""
        self._arrival_callbacks.append(callback)

    @property
    def edge_count(self) -> int:
        """TCK rising edges the host has made since the bench started."""
        return self._edge

    def watch_results(self) -> None:
        """Start recording ``arrivals``, ``captures`` and ``responses``."""
        if self._task is None:
            self._task = cocotb.start_soon(self._watch_dm_responses())

    def stop(self) -> None:
        """Stop recording ``arrivals``, ``captures`` and ``responses``."""
        if self._task is not None:
            self._task.kill()
            self._task = None

    def edge(self, *, capture: bool, update_word: int | None) -> None:
        """One TCK rising edge, with the DUT state sampled just before it."""
        self._edge += 1
        now = round(get_sim_time("ps"))
        if self._edge_ps is not None and now - self._edge_ps > self._tck_ps:
            self._trip_start = None
        self._edge_ps = now
        d = self._dut
        if not resolve(d.arst_tck_n):
            self._trip_start = None
            self._handshakes.clear()
            self._op_edge = None
            self._state_prev = BRIDGE_IDLE
            self._arm_clear()
            return
        self._watch_clear()
        state = resolve(d.bridge_state)
        if self._task is not None:
            self._watch_result(now, state, capture=capture, update_word=update_word)
        if self._trip_start is not None and state == BRIDGE_IDLE:
            self._grade_round_trip(self._edge - 1 - self._trip_start)
            self._trip_start = None
        if (
            update_word is not None
            and update_word & 3 in (DmiOp.READ, DmiOp.WRITE)
            and state == BRIDGE_IDLE
            and resolve(d.bridge_error) == 0
            and not self._clear_open
            and self._clk_running
        ):
            self._trip_start = self._edge

    async def _watch_dm_responses(self) -> None:
        """The DM's DMI response handshakes, sampled at each i_clk rising edge out of reset.
        The reset test re-points the predictor module's ``in_reset`` for test-mode resets, so
        it is looked up there at each edge."""
        d = self._dut
        while True:
            await RisingEdge(d.clk)
            if env.tdr2axi_predictor.in_reset(d):
                self._handshakes.clear()
                continue
            if resolve(d.dmi_resp_valid) and resolve(d.dmi_resp_ready):
                raw = d.dmi_resp_data.value
                data = int(raw) if raw.is_resolvable else None
                now = round(get_sim_time("ps"))
                shake = ResponseHandshake(now, self._edge, data, resolve(d.dmi_resp_resp))
                self._handshakes.append(shake)
                self.responses.append(shake)

    def _watch_result(
        self, now: int, state: int, *, capture: bool, update_word: int | None
    ) -> None:
        """Record an arrival on the edge before this one, then this edge's capture and the
        Update-DR of an operation the bridge takes."""
        d = self._dut
        for handshake in self._handshakes:
            if handshake.time_ps >= now:
                handshake.edge = self._edge
        if self._state_prev in BRIDGE_WAITING and state == BRIDGE_IDLE:
            done = [h for h in self._handshakes if h.time_ps < now]
            handshake = done[0] if done else None
            if handshake is not None:
                self._handshakes.remove(handshake)
            arrival = ResultArrival(
                update_edge=self._op_edge,
                handshake_edge=None if handshake is None else handshake.edge,
                arrival_edge=self._edge - 1,
                read=self._state_prev == BRIDGE_WAIT_READ,
                word=resolve(d.tdr_in),
                resp_data=None if handshake is None else handshake.data,
                resp=None if handshake is None else handshake.resp,
            )
            self.arrivals.append(arrival)
            for callback in self._arrival_callbacks:
                callback(arrival)
        self._state_prev = state
        if capture:
            self.captures.append(CaptureEdge(self._edge, resolve(d.tdr_in)))
        if (
            update_word is not None
            and update_word & 3 in (DmiOp.READ, DmiOp.WRITE)
            and state == BRIDGE_IDLE
            and resolve(d.bridge_error) == 0
        ):
            self._op_edge = self._edge

    def clk_stopped(self) -> None:
        """i_clk is held: the operation in flight is not graded."""
        self._clk_running = False
        self._trip_start = None

    def clk_started(self) -> None:
        """i_clk runs again."""
        self._clk_running = True

    def _arm_clear(self) -> None:
        self._tck_release = None
        self._clk_release = None
        self._clear_open = True

    def _watch_clear(self) -> None:
        """The release edges are the ones before the first edge each reset reads high."""
        if not self._clear_open:
            return
        d = self._dut
        if self._tck_release is None:
            self._tck_release = self._edge - 1
        if self._clk_release is None and resolve(d.arst_clk_n):
            self._clk_release = self._edge - 1
        if self._clk_release is None:
            return
        pending = (
            d.cdc_req_src_clear_pending,
            d.cdc_req_dst_clear_pending,
            d.cdc_resp_src_clear_pending,
            d.cdc_resp_dst_clear_pending,
        )
        if any(resolve(sig) for sig in pending):
            return
        self._clear_open = False
        edges = self._edge - 1 - max(self._tck_release, self._clk_release)
        if self._clear_max is not None:
            self._report(
                self._clear_id,
                passed=edges <= self._clear_max,
                detail=f"CDC clear sequence ended {edges} TCK edges after the later release, "
                f"at most {self._clear_max}",
            )

    def _grade_round_trip(self, edges: int) -> None:
        if self._round_trip is None:
            return
        low, high = self._round_trip
        self._report(
            self._round_trip_id,
            passed=low <= edges <= high,
            detail=f"round trip of {edges} TCK edges from Update-DR to the bridge in IDLE, "
            f"bounds {low} to {high}",
        )
