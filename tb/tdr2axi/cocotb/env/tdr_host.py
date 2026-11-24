# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""TDR host of the tdr2axi bench: TAP-state-accurate DMI TDR scans and the DMI op flow.

``TdrHost`` drives ``tck``, ``tdr_upd_q``, ``tdr_upd`` and ``tdr_cap`` of tb_top as a JTAG TAP
whose selected DR is the 41-bit DMI TDR, and samples ``tdr_in`` (o_dmi_tdr) in Capture-DR.
On top of the scans it runs the debugger's DMI flow: the op scan, a NOP poll, and on a busy
capture op 3 and a resend with more Run-Test/Idle cycles. Each scan's Run-Test/Idle count
is ``idle_cycles``, or, when that is at least the DMI round trip N, drawn from
``idle_cycles`` to ``idle_cycles + idle_extra``. The bypass bits ``pad_bits`` and the
Pause-DR length ``pause_cycles`` apply to the next scan, and a scan can hold the update
strobe over more than one TCK rising edge, which a TAP never does. TCK runs only during
scans.

Checks it owns: CHK-TDRHOST-CAPTURE-DR and CHK-TDRHOST-UPDATE-DR on its own strobes in
every scan, CHK-TDR-NEVER-FAILED at every capture, CHK-TDR-CAPTURE-EDGE on o_dmi_tdr at every
capture edge against the image a ``TdrEdgePredictor`` predicts for that edge,
CHK-TDR-CAPTURE-IMAGE on the address of a successful read, and CHK-ENV-READONDATA-IDLE on the
Run-Test/Idle count of each op scan of an sbdata0 read with sbreadondata set while
``side_effect_reads`` is set: a pass when it covers N, a failure when it does not, counted in
``readondata_idle_checks``. Verdicts go to the ``Reporter`` the bench passes in. There is no
SV twin: the bench has one cocotb realization.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from typing import Protocol

from cocotb.handle import SimHandleBase
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time

__all__ = [
    "ABSTRACTCS",
    "COMMAND",
    "DMCONTROL",
    "DMSTATUS",
    "MAX_POLLS",
    "SBADDRESS0",
    "SBADDRESS1",
    "SBCS",
    "SBDATA0",
    "SBDATA1",
    "TDR_LEN",
    "DmiAccess",
    "DmiOp",
    "DmiResult",
    "DmiStatus",
    "EdgeCallback",
    "Reporter",
    "TdrEdgePredictor",
    "TdrHost",
    "TdrHostCfg",
    "UnknownValueError",
    "busy_backoffs",
    "raised_idle",
    "resolve",
    "sbcs_value",
    "tdr_word",
]

# DMI register addresses (RISC-V External Debug Support 0.13.2, Table 3.1)
DMCONTROL = 0x10
DMSTATUS = 0x11
ABSTRACTCS = 0x16
COMMAND = 0x17
SBCS = 0x38
SBADDRESS0 = 0x39
SBADDRESS1 = 0x3A
SBDATA0 = 0x3C
SBDATA1 = 0x3D

# sbcs fields
SBCS_SBREADONADDR = 20
SBCS_SBACCESS_LSB = 17
SBCS_SBAUTOINCREMENT = 16
SBCS_SBREADONDATA = 15

# o_dmi_tdr / i_dmi_tdr layout: addr[40:34], data[33:2], op or status[1:0]
TDR_LEN = 41
TDR_ADDR_LSB = 34
TDR_DATA_LSB = 2
WORD_MASK = 0xFFFF_FFFF
MAX_POLLS = 20

# Scan edge indices from Run-Test/Idle: Select-DR-Scan, Capture-DR, then the edge leaving it
_CAPTURE_EXIT_EDGE = 2


class Reporter(Protocol):
    """Takes one check's verdict: a pass to count, or a failure with its detail."""

    def __call__(self, check_id: str, *, passed: bool, detail: str) -> None:
        """Record ``check_id`` as passed or failed."""
        ...


class CaptureImage(Protocol):
    """A predicted capture image."""

    def matches(self, word: int) -> bool:
        """Whether the 41-bit ``word`` equals the prediction."""
        ...


class TdrEdgePredictor(Protocol):
    """Predicts the TDR at each TCK rising edge the host generates."""

    def tck_edge(self, *, capture: bool, update_word: int | None) -> CaptureImage | None:
        """Apply one edge with the given strobes; return the image a capture edge predicts."""
        ...


class EdgeCallback(Protocol):
    """Takes each TCK rising edge the host generates, with the strobes it carries."""

    def __call__(self, *, capture: bool, update_word: int | None) -> None: ...


class UnknownValueError(RuntimeError):
    """A checked DUT output holds X or Z."""


class DmiOp(IntEnum):
    """Op field of an update (RISC-V External Debug Support 0.13.2, dmi.op)."""

    NOP = 0
    READ = 1
    WRITE = 2
    CLEAR = 3


class DmiStatus(IntEnum):
    """Status field of a capture; 2 (failed) is never legal for tdr2axi."""

    OK = 0
    RESERVED = 1
    FAILED = 2
    BUSY = 3


@dataclass(frozen=True)
class TdrHostCfg:
    """Clock periods, the synchronizer depth of the build, and the host's scan shape."""

    tck_ps: int
    clk_ps: int
    sync_stages: int
    pad_bits: int = 7
    idle_cycles: int = 4
    idle_extra: int = 0
    pause_cycles: int = 0

    @property
    def scan_len(self) -> int:
        """Shift-DR cycles of one scan: the TDR plus the bypass bits of the other TAPs."""
        return TDR_LEN + self.pad_bits

    @property
    def readondata_idle(self) -> int:
        """Run-Test/Idle TCK edges a side-effecting sbdata0 read with sbreadondata needs.

        N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck), the full round trip
        of one DMI request through the bridge: SYNC_STAGES + 2 TCK edges, plus the TCK edges
        within SYNC_STAGES + 3 i_clk periods. At any phase those are at most one more than
        the whole TCK periods the i_clk periods fill.
        """
        return self.sync_stages + 3 + (self.sync_stages + 3) * self.clk_ps // self.tck_ps

    @property
    def reset_ready_idle(self) -> int:
        """Run-Test/Idle TCK edges after i_arst_n rises by which the first operation crosses
        without busy.

        Both synchronized releases come within SYNC_STAGES + 1 edges of each clock
        (T-TDR2AXI-tck-reset-release), and both CDCs then end their clear sequence within
        ceil(27 x (1 + T_clk / T_tck) + max(1, T_clk / T_tck)) TCK edges
        (T-TDR2AXI-cdc-clear-20to1 to T-TDR2AXI-cdc-clear-1to20).
        """
        ratio = Fraction(self.clk_ps, self.tck_ps)
        release = math.ceil((self.sync_stages + 1) * max(1, ratio))
        return release + math.ceil(27 * (1 + ratio) + max(1, ratio))

    @property
    def pipe_idle(self) -> int:
        """Run-Test/Idle TCK edges after an update that let its result cross back before the
        next Capture-DR, for a host that sends the next op in place of the NOP poll."""
        return 6 + -(-16 * self.clk_ps // self.tck_ps)


@dataclass(frozen=True)
class DmiResult:
    """Outcome of one DMI op flow: the data and status of the final capture."""

    rdata: int
    status: DmiStatus
    addr: int
    retries: int


@dataclass(frozen=True)
class DmiAccess:
    """One DMI op flow the host ran, as the SBA step checks read it."""

    op: DmiOp
    addr: int
    data: int
    status: DmiStatus
    rdata: int


def tdr_word(op: DmiOp | int, addr: int, data: int) -> int:
    """Pack a 41-bit update word: addr[40:34], data[33:2], op[1:0]."""
    return (addr << TDR_ADDR_LSB) | ((data & WORD_MASK) << TDR_DATA_LSB) | int(op)


def sbcs_value(
    size: int, *, readonaddr: bool = False, readondata: bool = False, autoinc: bool = False
) -> int:
    """sbcs write value with sbaccess = size and the read and auto-increment controls."""
    return (
        (int(readonaddr) << SBCS_SBREADONADDR)
        | (size << SBCS_SBACCESS_LSB)
        | (int(autoinc) << SBCS_SBAUTOINCREMENT)
        | (int(readondata) << SBCS_SBREADONDATA)
    )


def raised_idle(idle: int) -> int:
    """Run-Test/Idle count after one busy backoff: one more plus a tenth, the step OpenOCD
    raises its DMI busy delay by."""
    return idle + idle // 10 + 1


def busy_backoffs(start: int, need: int) -> int:
    """Busy backoffs that raise a Run-Test/Idle count from ``start`` to at least ``need``."""
    count = 0
    while start < need:
        start = raised_idle(start)
        count += 1
    return count


def resolve(sig: SimHandleBase) -> int:
    """Return ``sig`` as an int; X or Z raises ``UnknownValueError`` with the bit string.

    A 2-state integer variable such as an SV ``int`` reads as a Python int on a simulator
    that exposes it as an integer object, as VCS does, and as a bit vector on others.
    """
    raw = sig.value
    if isinstance(raw, int):
        return raw
    if not raw.is_resolvable:
        raise UnknownValueError(f"{sig._name} is {raw.binstr}")
    return int(raw)


class TdrHost:
    """JTAG host of the DMI TDR on the pin-level tb_top, and the DMI op flow above it.

    Each scan walks Select-DR-Scan, Capture-DR (capture strobe high for the one edge that
    leaves it), Shift-DR, Exit1-DR, optionally Pause-DR and Exit2-DR, and Update-DR, where the
    update word and strobe change on the falling edge and the strobe covers the next rising
    edge; then the scan's Run-Test/Idle count. The host samples its own strobes at every
    rising edge it generates and checks the scan shape against them.
    """

    def __init__(
        self,
        dut: SimHandleBase,
        cfg: TdrHostCfg,
        report: Reporter,
        predictor: TdrEdgePredictor | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._dut = dut
        self.cfg = cfg
        self._report = report
        self._predictor = predictor
        self._rng = rng
        self._log = logging.getLogger("cocotb.tdr2axi.tdr_host")
        self.idle_cycles = cfg.idle_cycles
        self.idle_extra = cfg.idle_extra
        self.pad_bits = cfg.pad_bits
        self.pause_cycles = cfg.pause_cycles
        self.busy_retries = 0
        self.side_effect_reads = False
        self.readondata_idle_checks = 0
        self.accesses: list[DmiAccess] = []
        self._sbcs = 0
        self._upd_q = 0
        self._edges: list[tuple[int, int, int]] = []
        self._edge_callbacks: list[EdgeCallback] = []

    @property
    def edges_to_update(self) -> int:
        """TCK rising edges the next scan makes before its update edge, from the live
        ``pad_bits`` and ``pause_cycles``: Run-Test/Idle through Capture-DR (2), into
        Shift-DR (1), the shifts after the first, into Exit1-DR (1), through Pause-DR when
        the scan pauses (``pause_cycles`` + 1) and into Update-DR (1)."""
        pause = self.pause_cycles + 1 if self.pause_cycles else 0
        return TDR_LEN + self.pad_bits + 4 + pause

    def add_edge_callback(self, callback: EdgeCallback) -> None:
        """Call ``callback`` at every TCK rising edge, with the DUT as it stands before it."""
        self._edge_callbacks.append(callback)

    def drive_idle(self) -> None:
        """TCK low, both strobes low and a zero update word."""
        d = self._dut
        d.tck.value = 0
        d.tdr_upd_q.value = 0
        d.tdr_upd.value = 0
        d.tdr_cap.value = 0
        self._upd_q = 0

    async def tck_cycles(self, count: int) -> None:
        """``count`` TCK periods with both strobes low, as in Run-Test/Idle."""
        for _ in range(count):
            await self._tck_cycle()

    async def scan(self, din: int, *, idle: int | None = None, update_edges: int = 1) -> int:
        """One DR scan from Run-Test/Idle back to Run-Test/Idle, with ``idle`` Run-Test/Idle
        cycles or a count ``draw_idle`` picks; returns the captured word.

        The update strobe covers ``update_edges`` TCK rising edges: the one leaving Update-DR
        and the first ``update_edges - 1`` Run-Test/Idle edges, of which there are at least
        that many.
        """
        if update_edges < 1:
            raise ValueError(f"update_edges must be 1 or more, got {update_edges}")
        idle = self.draw_idle() if idle is None else idle
        idle = max(idle, update_edges - 1)
        d = self._dut
        self._edges = []
        prev_upd_q = self._upd_q
        await self._tck_cycle()  # Run-Test/Idle -> Select-DR-Scan
        await self._tck_cycle()  # Select-DR-Scan -> Capture-DR
        # o_dmi_tdr depends on the capture strobe (busy raised by this capture shows in it),
        # so the word is sampled once the strobe has settled
        d.tdr_cap.value = 1
        await Timer(1, "ps")
        dout = resolve(d.tdr_in)
        self._check_status(dout)
        await self._tck_cycle()  # Capture-DR -> Shift-DR
        d.tdr_cap.value = 0
        for _ in range(TDR_LEN + self.pad_bits - 1):
            await self._tck_cycle()  # Shift-DR
        await self._tck_cycle()  # last shift, Shift-DR -> Exit1-DR
        if self.pause_cycles:
            await self._tck_cycle()  # Exit1-DR -> Pause-DR
            for _ in range(self.pause_cycles - 1):
                await self._tck_cycle()  # Pause-DR
            await self._tck_cycle()  # Pause-DR -> Exit2-DR
        await self._tck_cycle()  # Exit1-DR or Exit2-DR -> Update-DR
        d.tdr_upd_q.value = din
        d.tdr_upd.value = 1
        self._upd_q = din
        await self._tck_cycle()  # Update-DR -> Run-Test/Idle: the bridge samples the update
        for edge in range(idle):
            if edge == update_edges - 1:
                d.tdr_upd.value = 0
            await self._tck_cycle()
        d.tdr_upd.value = 0
        self._check_scan_shape(prev_upd_q, din, idle, update_edges)
        return dout

    def draw_idle(self) -> int:
        """The next scan's Run-Test/Idle count: ``idle_cycles`` below N, else drawn up to
        ``idle_extra`` above it."""
        floor = self.idle_cycles
        if floor < self.cfg.readondata_idle or not self.idle_extra or self._rng is None:
            return floor
        return floor + self._rng.randint(0, self.idle_extra)

    async def dmi(
        self,
        op: DmiOp,
        addr: int,
        wdata: int = 0,
        *,
        max_polls: int = MAX_POLLS,
        clear_busy: bool = True,
    ) -> DmiResult:
        """DMI op flow: the op scan, then a NOP poll; returns the poll's data and status.

        A busy capture on either scan means the op may not have run: with ``clear_busy`` the
        host clears with op 3, raises its Run-Test/Idle count and resends. OK after a write
        means no error has occurred; the write itself may be in progress.
        """
        side_effect_read = (
            op is DmiOp.READ
            and addr == SBDATA0
            and self.side_effect_reads
            and (self._sbcs >> SBCS_SBREADONDATA) & 1
        )
        retries = self.busy_retries
        cap = 0
        for _ in range(max_polls):
            idle = self.draw_idle()
            if side_effect_read:
                self._check_readondata_idle(idle)
            cap = await self.scan(tdr_word(op, addr, wdata), idle=idle)
            if clear_busy and cap & 3 == DmiStatus.BUSY:
                await self._busy_backoff()
                continue
            cap = await self.scan(tdr_word(DmiOp.NOP, 0, 0))
            if not clear_busy or cap & 3 != DmiStatus.BUSY:
                break
            await self._busy_backoff()
        status = DmiStatus(cap & 3)
        rdata = (cap >> TDR_DATA_LSB) & WORD_MASK
        cap_addr = cap >> TDR_ADDR_LSB
        if status is DmiStatus.OK and op is DmiOp.READ:
            self._report(
                "CHK-TDR-CAPTURE-IMAGE",
                passed=cap_addr == addr,
                detail=f"capture address 0x{cap_addr:02x} after a read of 0x{addr:02x}",
            )
        if status is DmiStatus.OK and op is DmiOp.WRITE and addr == SBCS:
            self._sbcs = wdata
        self.accesses.append(DmiAccess(op, addr, wdata, status, rdata))
        self._log.debug(
            "DMI %s 0x%02x data 0x%08x: status %s rdata 0x%08x",
            op.name,
            addr,
            wdata,
            status.name,
            rdata,
        )
        return DmiResult(rdata, status, cap_addr, self.busy_retries - retries)

    # --- Private helpers ------------------------------------------------------------------
    async def _tck_cycle(self) -> None:
        d = self._dut
        await self._tck_grid(self.cfg.tck_ps // 2)
        cap, upd, upd_q = resolve(d.tdr_cap), resolve(d.tdr_upd), resolve(d.tdr_upd_q)
        self._edges.append((cap, upd, upd_q))
        if self._predictor is not None:
            image = self._predictor.tck_edge(capture=bool(cap), update_word=upd_q if upd else None)
            if image is not None:
                self._check_capture_edge(image)
        for callback in self._edge_callbacks:
            callback(capture=bool(cap), update_word=upd_q if upd else None)
        d.tck.value = 1
        await self._tck_grid(0)
        d.tck.value = 0

    async def _tck_grid(self, offset_ps: int) -> None:
        """Wait for the next time ``offset_ps`` past a multiple of the TCK period. TCK edges
        keep one grid from time 0 across scans and waits, so the i_clk phase alone sets where
        the edges of the two clocks fall."""
        wait = (offset_ps - round(get_sim_time("ps"))) % self.cfg.tck_ps
        await Timer(wait or self.cfg.tck_ps, "ps")

    async def _busy_backoff(self) -> None:
        """Op 3, then a larger Run-Test/Idle count that every later access uses."""
        self.busy_retries += 1
        await self.scan(tdr_word(DmiOp.CLEAR, 0, 0))
        self.idle_cycles = raised_idle(self.idle_cycles)

    def _check_capture_edge(self, image: CaptureImage) -> None:
        raw = self._dut.tdr_in.value
        if not raw.is_resolvable:
            self._report(
                "CHK-TDR-CAPTURE-EDGE", passed=False, detail=f"o_dmi_tdr is {raw.binstr} at capture"
            )
            return
        word = int(raw)
        self._report(
            "CHK-TDR-CAPTURE-EDGE",
            passed=image.matches(word),
            detail=f"o_dmi_tdr 0x{word:011x} at capture, predicted {image}",
        )

    def _check_status(self, word: int) -> None:
        status = word & 3
        self._report(
            "CHK-TDR-NEVER-FAILED",
            passed=status in (DmiStatus.OK, DmiStatus.BUSY),
            detail=f"capture status {status} in 0x{word:011x}",
        )

    def _check_readondata_idle(self, idle: int) -> None:
        need = self.cfg.readondata_idle
        self.readondata_idle_checks += 1
        self._report(
            "CHK-ENV-READONDATA-IDLE",
            passed=idle >= need,
            detail=f"sbdata0 read with sbreadondata at a side-effecting location after "
            f"{idle} Run-Test/Idle cycles, {need} needed",
        )

    def _check_scan_shape(self, prev_upd_q: int, din: int, idle: int, update_edges: int) -> None:
        edges = self._edges
        update_exit = len(edges) - 1 - idle
        cap_edges = [i for i, (cap, _, _) in enumerate(edges) if cap]
        self._report(
            "CHK-TDRHOST-CAPTURE-DR",
            passed=cap_edges == [_CAPTURE_EXIT_EDGE],
            detail=f"capture strobe high at scan edges {cap_edges}, "
            f"expected [{_CAPTURE_EXIT_EDGE}]",
        )
        upd_edges = [i for i, (_, upd, _) in enumerate(edges) if upd]
        expected = list(range(update_exit, update_exit + update_edges))
        early = [i for i, (_, _, q) in enumerate(edges[:update_exit]) if q != prev_upd_q]
        late = [i for i, (_, _, q) in enumerate(edges[update_exit:], update_exit) if q != din]
        self._report(
            "CHK-TDRHOST-UPDATE-DR",
            passed=upd_edges == expected and not early and not late,
            detail=f"update strobe at scan edges {upd_edges}, expected {expected} for the "
            f"{update_edges} update edge(s) the scan asks; update word changed before "
            f"Update-DR at edges {early}, differs after it at edges {late}",
        )
