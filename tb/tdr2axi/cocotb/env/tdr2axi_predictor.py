# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Feeds the tdr2axi reference models and publishes what they predict.

``Tdr2axiPredictor`` owns one ``TdrStatusModel`` and one ``SbaRefModel`` and drives them in
event order from stimulus and from the resynchronization points their contracts name:

* every TCK rising edge the ``TdrHost`` generates: ``response`` with the DM's predicted
  answer once ``bridge_state`` is back in IDLE, then ``predict_update`` for an edge with the
  update strobe high or ``predict_capture`` for one with the capture strobe high;
* every i_clk rising edge: ``predict_dmi_read`` or ``predict_dmi_write`` with the request
  the TDR model predicted when a DMI request handshake takes place, then ``bus_response``
  with the R or B the subordinate drives. A DM response handshake gives an answer whose
  response code the SBA model leaves open (``DmAnswer.open_status``) the code the DM
  sent, busy or success, before ``response`` delivers it;
* ``access_retired``, by the DV reading of CHK-SBA-SBBUSY: every sbcs read reads
  sbbusy = 1 from the request that starts an access until its bus response; after the
  response at most one sbcs read reads 1, and every later one reads 0 until the next access
  starts. The first sbcs read the DM takes after the response retires the access. Any
  other DMI request retires it first once the ``sbbusy`` observable has read 0 in
  RESPONDED, the point the debug specification leaves open, and the next sbcs read before
  another access starts may read sbbusy = 1. An sbcs read's answer carries that
  sbbusy window in ``dm_answers`` and reaches the TDR model with sbbusy free, so only the
  scoreboard's CHK-SBA-SBBUSY comparison grades sbbusy. An access held across a
  dmactive = 0 write retires after the request the DM takes at the first edge where
  ``sbbusy`` reads 0, which the model predicts with the held or the reset values, or at
  the next edge. A dmactive = 1 write the DM takes at the edge of a held access's R or B
  handshake raises ``SbaStimulusError``: S-TDR2AXI-0091 leaves that order open;
* the end of the post-reset CDC clear sequence, once every ``cdc_*_clear_pending``
  observable has risen and fallen again after the release: ``settled``;
* each fall of the reset input in control, i_arst_n with i_test_mode = 0 and i_arst_scan_n
  with i_test_mode = 1, and each change of i_test_mode that hands control to a low reset
  input: ``reset`` of both models; a TAP reset of the host: ``trst``; each memory fill:
  ``load_memory``. While i_test_mode = 1, i_arst_n changes neither model, and an i_clk edge
  counts as out of reset by i_arst_scan_n alone (src/tdr2axi/tdr2axi.sv, rstgen_bypass).

The DUT supplies timing, never content: the request a DMI handshake carries is the TDR
model's prediction, and the DM's answer is the SBA model's. While the TDR model holds several
outcomes after a reset, a request one outcome does not send is optional; the handshake's
fields only select which predicted request it is. A DMI handshake with no request predicted
raises ``PredictorSyncError`` at the next i_clk rising edge, so the scoreboard, fed under
``ReadOnly`` of the handshake's edge, first files it under the check whose context forbids
it; a bridge answer with none pending raises at once. The predicted bus accesses and DM
answers are published in ``bus_expectations`` and ``dm_answers`` for the scoreboard, and
``dmi_requests`` counts the DMI request handshakes seen. There is no SV twin.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, replace

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import Edge, FallingEdge, First, RisingEdge

from env.sba_ref_model import (
    DMCONTROL,
    DmAnswer,
    SbaBusAccess,
    SbaPhase,
    SbaRefModel,
    SbaStimulusError,
)
from env.tdr_host import SBCS, resolve
from env.tdr_status_model import DmiRequest, TdrCaptureExpect, TdrOp, TdrStatusModel

__all__ = [
    "PredictorSyncError",
    "Tdr2axiPredictor",
    "entered_by_mode_change",
    "in_reset",
    "reset_input",
]

# dmi_tdr_bridge state encodings, as the bridge_state observable carries them
BRIDGE_IDLE = 0
BRIDGE_BUSY_STATES = frozenset({1, 2, 3, 4})

RESP_ERROR_MIN = 2  # SLVERR and DECERR
DMI_RESP_BUSY = 3
DMCONTROL_DMACTIVE = 1 << 0

# sbcs.sbbusy (RISC-V External Debug Support 0.13.2, sbcs)
SBCS_SBBUSY = 1 << 21


class PredictorSyncError(RuntimeError):
    """The DUT's event order left the models without a prediction to apply."""


def reset_input(dut: SimHandleBase) -> str:
    """The tb_top net of the reset input that drives both internal resets: ``rst_scan_n``
    (i_arst_scan_n) while i_test_mode = 1, ``rst_n`` (i_arst_n) while it is 0 or when the
    handle has no ``test_mode`` net."""
    test_mode = getattr(dut, "test_mode", None)
    return "rst_scan_n" if test_mode is not None and resolve(test_mode) else "rst_n"


def in_reset(dut: SimHandleBase) -> bool:
    """Whether the reset input in control holds both internal resets now."""
    return resolve(getattr(dut, reset_input(dut))) == 0


def entered_by_mode_change(dut: SimHandleBase) -> bool:
    """After i_test_mode changes, whether the change put both sides in reset: the input now
    in control is low and the one before it was high."""
    now = reset_input(dut)
    before = "rst_n" if now == "rst_scan_n" else "rst_scan_n"
    return resolve(getattr(dut, now)) == 0 and resolve(getattr(dut, before)) == 1


@dataclass(frozen=True)
class _Predicted:
    request: DmiRequest
    optional: bool


class Tdr2axiPredictor:
    """Drives the TDR status model and the SBA model in DUT event order."""

    def __init__(self, dut: SimHandleBase, tdr: TdrStatusModel, sba: SbaRefModel) -> None:
        self._dut = dut
        self.tdr = tdr
        self.sba = sba
        self._log = logging.getLogger("cocotb.tdr2axi.predictor")
        self._requests: deque[_Predicted] = deque()
        self._answers: deque[DmAnswer] = deque()
        # Answers at the head of ``_answers`` whose DM response handshake has been seen
        self._responded = 0
        self._tasks: list[Task] = []
        self._bridge_prev = BRIDGE_IDLE
        self._clear_seen = False
        self._sbbusy_fell = False
        self._late_busy = False
        self._lost: PredictorSyncError | None = None
        self.bus_expectations: list[SbaBusAccess] = []
        self.dm_answers: list[DmAnswer] = []
        self.dmi_requests = 0

    def start(self) -> None:
        """Apply the reset the bench holds at start, and run the i_clk and reset watchers."""
        self._reset()
        self._tasks.append(cocotb.start_soon(self._watch_clk()))
        self._tasks.append(cocotb.start_soon(self._watch_reset()))
        self._tasks.append(cocotb.start_soon(self._watch_scan_reset()))

    def stop(self) -> None:
        """Stop the watchers."""
        for task in self._tasks:
            task.kill()
        self._tasks.clear()

    def load_memory(self, addr: int, data: bytes) -> None:
        """Record bytes the test placed in the subordinate's memory."""
        self.sba.load_memory(addr, data)

    def trst(self) -> None:
        """Apply a TAP reset of the host, asserted and released with TCK stopped."""
        self.tdr.trst()
        self.sba.reset("RST-TRST")

    def tck_edge(self, *, capture: bool, update_word: int | None) -> TdrCaptureExpect | None:
        """Apply one TCK rising edge the host is about to generate; return the capture image.

        Called before the edge with the strobes the host drives. ``bridge_state`` read here is
        its value after the previous edge, so an answer that reached the bridge on that edge
        is applied before this edge's capture or update.
        """
        self._watch_clear()
        state = resolve(self._dut.bridge_state)
        if self._bridge_prev in BRIDGE_BUSY_STATES and state == BRIDGE_IDLE:
            self._deliver_answer()
        self._bridge_prev = state
        if update_word is not None:
            expect = self.tdr.predict_update(update_word)
            if expect.request is not None:
                self._requests.append(_Predicted(expect.request, expect.optional))
        if capture:
            return self.tdr.predict_capture()
        return None

    # --- Private --------------------------------------------------------------------------
    def _deliver_answer(self) -> None:
        if not self._answers:
            raise PredictorSyncError("the bridge returned to IDLE with no DM answer predicted")
        answer = self._answers.popleft()
        if self._responded:
            self._responded -= 1
        elif answer.open_status:
            raise PredictorSyncError("the bridge returned an open DM answer the DM never sent")
        self.tdr.response(busy=answer.busy, allowed=answer.data.allowed, mask=answer.data.mask)

    def _dm_responded(self, resp: int) -> None:
        """A DM response handshake answers the oldest request without one; an open answer
        takes its response code."""
        index = self._responded
        if index >= len(self._answers):
            self._lost = PredictorSyncError("DM response handshake with no request awaiting one")
            return
        answer = self._answers[index]
        if answer.open_status:
            busy = resp == DMI_RESP_BUSY
            self._answers[index] = replace(answer, busy=busy, open_status=False)
        self._responded += 1

    def _watch_clear(self) -> None:
        """The CDC clear sequence after a reset ends when its pending flags have all fallen."""
        if not self.tdr.settling or in_reset(self._dut):
            return
        d = self._dut
        pending = any(
            resolve(sig)
            for sig in (
                d.cdc_req_src_clear_pending,
                d.cdc_req_dst_clear_pending,
                d.cdc_resp_src_clear_pending,
                d.cdc_resp_dst_clear_pending,
            )
        )
        if pending:
            self._clear_seen = True
        elif self._clear_seen:
            self._clear_seen = False
            self.tdr.settled()

    async def _watch_clk(self) -> None:
        """Apply every i_clk rising edge."""
        while True:
            await RisingEdge(self._dut.clk)
            self._clk_edge()

    def _clk_edge(self) -> None:
        """One i_clk rising edge: the loss of a DMI handshake the edge before, then out of
        reset, in DM order, the sbbusy fall, a DMI request, then a bus response."""
        if self._lost is not None:
            raise self._lost
        d = self._dut
        if in_reset(d):
            return
        self._watch_clear()
        if self.sba.phase is SbaPhase.RESPONDED and not resolve(d.sbbusy):
            if self.sba.held and self._sbbusy_fell:
                self._retire()
            else:
                self._sbbusy_fell = True
        if resolve(d.dmi_resp_valid) and resolve(d.dmi_resp_ready):
            self._dm_responded(resolve(d.dmi_resp_resp))
        r_done = resolve(d.r_valid) and resolve(d.r_ready)
        b_done = resolve(d.b_valid) and resolve(d.b_ready)
        if resolve(d.dmi_req_valid) and resolve(d.dmi_req_ready):
            self._take_dmi_request(responding=bool(r_done or b_done))
        if r_done:
            self.sba.bus_response(
                error=resolve(d.r_resp) >= RESP_ERROR_MIN, rdata=resolve(d.r_data)
            )
        if b_done:
            self.sba.bus_response(error=resolve(d.b_resp) >= RESP_ERROR_MIN)

    def _take_dmi_request(self, *, responding: bool) -> None:
        """One DMI request the DM takes; ``responding`` is an R or B handshake at its edge."""
        self.dmi_requests += 1
        if not self._requests:
            self._lost = PredictorSyncError("DMI request handshake with no request predicted")
            return
        request = self._select_request()
        sbcs_read = request.op is TdrOp.READ and request.addr == SBCS
        held = self.sba.held
        rearm = (
            request.op is TdrOp.WRITE
            and request.addr == DMCONTROL
            and bool(request.data & DMCONTROL_DMACTIVE)
        )
        if held and rearm and responding and self.sba.phase is SbaPhase.PENDING:
            raise SbaStimulusError(
                "dmactive set at the edge of the held access's R or B handshake: "
                "S-TDR2AXI-0091 leaves open whether the result lands"
            )
        if self._sbbusy_fell and not sbcs_read and not held:
            if self.sba.phase is SbaPhase.RESPONDED:
                self._late_busy = True
            self._retire()
        if request.op is TdrOp.READ:
            answer = self.sba.predict_dmi_read(request.addr)
        else:
            answer = self.sba.predict_dmi_write(request.addr, request.data)
        if sbcs_read:
            answer = self._sbbusy_window(answer)
            self._retire()
            self._late_busy = False
        elif self.sba.phase is not SbaPhase.IDLE:
            self._late_busy = False
        if held and self._sbbusy_fell:
            self._retire()
        self._answers.append(_sbbusy_free(answer) if sbcs_read else answer)
        self.dm_answers.append(answer)
        if answer.access is not None:
            self.bus_expectations.append(answer.access)
        self._log.debug(
            "DM %s 0x%02x: busy %s access %s",
            request.op.name,
            request.addr,
            answer.busy,
            answer.access,
        )

    def _sbbusy_window(self, answer: DmAnswer) -> DmAnswer:
        """The first sbcs read after an access another request retired may read sbbusy = 1."""
        data = answer.data
        if not self._late_busy or answer.busy or not data.mask & SBCS_SBBUSY:
            return answer
        allowed = tuple(dict.fromkeys((*data.allowed, *(v | SBCS_SBBUSY for v in data.allowed))))
        return replace(answer, data=replace(data, allowed=allowed))

    def _retire(self) -> None:
        if self.sba.phase is SbaPhase.RESPONDED:
            self.sba.access_retired()
        self._sbbusy_fell = False

    def _select_request(self) -> DmiRequest:
        """The predicted request this handshake carries: optional ones it skips were not sent."""
        d = self._dut
        seen = (resolve(d.dmi_req_op), resolve(d.dmi_req_addr), resolve(d.dmi_req_data))
        for index, predicted in enumerate(self._requests):
            req = predicted.request
            if (int(req.op), req.addr, req.data) == seen:
                skipped = list(self._requests)[:index]
                if all(p.optional for p in skipped):
                    for _ in range(index + 1):
                        self._requests.popleft()
                    return req
                break
        return self._requests.popleft().request

    async def _watch_reset(self) -> None:
        """i_arst_n resets the DM side and the TCK side together while i_test_mode = 0, and
        a change of i_test_mode that hands control to a low reset input resets them in that
        input's scope; ``_watch_scan_reset`` applies i_arst_scan_n falls."""
        d = self._dut
        while True:
            arst_fell = FallingEdge(d.rst_n)
            fired = await First(arst_fell, Edge(d.test_mode))
            if fired is arst_fell:
                if reset_input(d) == "rst_n":
                    self._reset()
            elif entered_by_mode_change(d):
                self._reset(scope="RST-SCAN" if reset_input(d) == "rst_scan_n" else None)

    async def _watch_scan_reset(self) -> None:
        """With i_test_mode = 1, i_arst_scan_n resets both sides in place of i_arst_n."""
        while True:
            await FallingEdge(self._dut.rst_scan_n)
            if resolve(self._dut.test_mode):
                self._reset(scope="RST-SCAN")

    def _reset(self, *, scope: str | None = None) -> None:
        self.sba.reset(scope or "RST-ARST-BUS")
        self.tdr.reset(scope or "RST-ARST-TCK")
        self._requests.clear()
        self._answers.clear()
        self._responded = 0
        self._bridge_prev = BRIDGE_IDLE
        self._clear_seen = False
        self._sbbusy_fell = False
        self._late_busy = False


def _sbbusy_free(answer: DmAnswer) -> DmAnswer:
    """An sbcs read's answer with sbbusy left out of the compared bits."""
    return replace(answer, data=replace(answer.data, mask=answer.data.mask & ~SBCS_SBBUSY))
