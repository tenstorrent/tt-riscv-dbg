# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi system bus access: single and block accesses, errors, a busy bus, address width,
a seeded mix of every operation, and the address after a failed access.

Each SBA access must give exactly one AXI transaction with the expected address, AxSIZE
(AXI4), strobes and byte lanes, and the subordinate memory must change only where written.
The scoreboard grades sberror after misaligned, oversize and failed accesses, the
transaction of a failed access, a transaction after any of them, the memory image, the
busy capture and the sticky busy against the models. The build (USE_AXI_LITE, widths)
comes from the Makefile.

``tdr2axi_sba_busy_test`` runs its sticky stall on the seed's subordinate timing, then
switches the subordinate to READY high on a free channel and each response on the edge after
its request handshake for the response sweep, so ending a stall at a chosen i_clk edge puts
the R or B handshake at a known edge. It times each sweep stall from the polling scan's
Capture-DR edge, logs where the response handshake and the DM's sbcs read fell, and is
inconclusive when no stall puts the read on the edge after the handshake.

``tdr2axi_sba_error_address_test`` checks RISC-V External Debug Support 0.13.2 sections
3.12.19 and 3.12.23: sbaddress advances after a read an sbaddress0 write started or a
write an sbdata0 write started only when that access succeeded, so after SLVERR or DECERR
sbaddress reads the failing address. A read an sbdata0 read started has no such condition
and is not made here. Its FAIL line names an advance by the access size apart from any other
wrong address, and the run logs its scenario order and one line per scenario.

``tdr2axi_sba_stress_test`` makes one operation of each kind and TDR2AXI_SBA_STRESS_OPS
more, 24 unless set, drawn from the seed; its block reads draw per element whether
sbreadondata starts the next read. TDR2AXI_AXISUB_NEGATIVE=lost_write arms that
subordinate fault in this test only.
"""

from __future__ import annotations

import functools
import os
import random
from dataclasses import dataclass, replace

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.triggers import Event, ReadOnly
from cocotb.utils import get_sim_time

from env.axi_sub import RESP_DECERR, RESP_OKAY, RESP_SLVERR, AxiSubTiming, negative_fault
from env.tdr_host import (
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBDATA0,
    SBDATA1,
    DmiOp,
    busy_backoffs,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_base_test import (
    SBCS_CLEAR_ERRORS,
    SBCS_SBACCESS_LSB,
    SBCS_SBBUSY,
    SBCS_SBBUSYERROR,
    SIZE_64,
    TIMEOUT_MS,
    WORD_MASK,
    Tdr2axiTb,
    size_mask,
)

BLOCK_LEN = 6
ERROR_PATTERN = 0x0123_4567_89AB_CDEF
DECERR_PATTERN = 0x5A5A_A5A5
WORD = 2
# Widest sbaccess the debug specification defines: 128 bits
SIZE_MAX = 4
# Largest value of the 3-bit sbaccess field; 5 to 7 name no size
SBACCESS_MAX = 7
# The block error cases' range, above the single accesses' and aligned to the widest size
ERROR_BLOCK_OFFSET = 0x100
ERROR_BLOCK_BYTES = 2 * BLOCK_LEN * 8
ERROR_RESPONSES = (RESP_SLVERR, RESP_DECERR)
RESPONSE_NAMES = {RESP_SLVERR: "SLVERR", RESP_DECERR: "DECERR"}
SBERROR_BUS = 2
SBERROR_ALIGNMENT = 3
SBERROR_SIZE = 4

ADDRESS_WIDTH = "CHK-SBA-ADDRESS-WIDTH"
NO_INC_AFTER_ERROR = "CHK-SBA-NO-INC-AFTER-ERROR"
SINGLE_WRITE = "CHK-SBA-SINGLE-WRITE"
SINGLE_READ = "CHK-SBA-SINGLE-READ"
BLOCK_WRITE = "CHK-SBA-BLOCK-WRITE"
BLOCK_READ = "CHK-SBA-BLOCK-READ"
ERROR_BLOCKS = "CHK-SBA-ERROR-BLOCKS"
SBDATA1_NO_BUS = "CHK-SBA-SBDATA1-NO-BUS"
SBBUSYERROR = "CHK-SBA-SBBUSYERROR"
REGISTER_NAMES = {
    SBCS: "sbcs",
    SBADDRESS0: "sbaddress0",
    SBADDRESS1: "sbaddress1",
    SBDATA0: "sbdata0",
    SBDATA1: "sbdata1",
}

# Simulated-time bounds at the slowest i_clk the clock ratios name, 20 times slower than TCK
SINGLE_TIMEOUT_MS = 2 * TIMEOUT_MS
ERRORS_TIMEOUT_MS = 2 * TIMEOUT_MS
BUSY_TIMEOUT_MS = 2 * TIMEOUT_MS

BUSY_OFFSET = 0x4000
BUSY_WORDS = 16
# d - SYNC_STAGES of the stalled accesses of the response sweep, a read and a write at each
SWEEP_STEPS = range(-1, 4)
# i_clk rising edges from the one at which the subordinate first sees its stall ended to the
# R or B handshake, with the sweep timing: READY, the request handshake, the response
RELEASE_TO_RESPONSE_EDGES = 3
# i_clk edges the sweep watches past the aimed response handshake for it and the DM's read
SWEEP_WATCH_EDGES = 16

# Seeded backgrounds, each with one bit set and then cleared, beside the walking patterns
ADDRESS_BACKGROUNDS = 4
# Simulated-time bound of the 136 accesses of a 64-bit address at the slowest i_clk the
# knobs draw, 8 times slower than TCK
ADDRESS_WIDTH_TIMEOUT_MS = 30 * TIMEOUT_MS

ERROR_ADDRESS_ROUNDS = 3
ERROR_ADDRESS_OFFSET = 0xA000
ERROR_ADDRESS_SPAN = 0x200
ERROR_BLOCK_MAX = 6
# Simulated-time bound of the six scenarios at the slowest i_clk the knobs draw
ERROR_ADDRESS_TIMEOUT_MS = 20 * TIMEOUT_MS

STRESS_OFFSET = 0xC000
STRESS_SPAN = 0x400
STRESS_BLOCK_MAX = 6
STRESS_OPS_KNOB = "TDR2AXI_SBA_STRESS_OPS"
STRESS_OPS_DEFAULT = 24
STRESS_ERROR_SHARE = 0.25
STRESS_BUSY_SHARE = 0.3
# Block-read elements, all but the last, whose sbdata0 read starts the next element
STRESS_READONDATA_SHARE = 0.5
# Simulated-time bound of one stress operation at the slowest i_clk the knobs draw
STRESS_OP_TIMEOUT_US = 500
STRESS_FAULTS = frozenset({"lost_write"})
STRESS_KINDS = ("write", "read", "block_write", "block_read", "misaligned", "oversize")
# (kind, error, busy): every run makes each of these once
STRESS_EACH_KIND = (
    ("write", False, False),
    ("write", True, False),
    ("write", False, True),
    ("read", False, False),
    ("read", True, False),
    ("read", False, True),
    ("block_write", False, False),
    ("block_write", True, False),
    ("block_read", False, False),
    ("block_read", True, False),
    ("misaligned", False, False),
    ("oversize", False, False),
)


def _stress_extra_ops() -> int:
    """Seeded operations beyond the one of each kind, from STRESS_OPS_KNOB."""
    text = os.environ.get(STRESS_OPS_KNOB, "")
    if not text:
        return STRESS_OPS_DEFAULT
    if not text.isdigit():
        raise ValueError(f"{STRESS_OPS_KNOB}={text!r}: a count of operations")
    return int(text)


STRESS_EXTRA_OPS = _stress_extra_ops()
STRESS_TIMEOUT_US = (
    1000 * TIMEOUT_MS + (len(STRESS_EACH_KIND) + STRESS_EXTRA_OPS) * STRESS_OP_TIMEOUT_US
)


@cocotb.test(timeout_time=SINGLE_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_single_test(dut: SimHandleBase) -> None:
    """Every size at every lane: write, check the bus and memory, read back. After each size,
    an sbdata1 write and an sbdata1 read with sbreadondata set start no bus access, on every
    data width."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_single_test")
    base = tb.region + 0x1000
    tb.mem_fill(base, 4 * tb.nbytes)
    for size in range(tb.max_size + 1):
        for lane in range(0, tb.nbytes, 1 << size):
            addr = base + size * tb.nbytes + lane
            data = tb.rand_data(size)
            tb.clear_monitor()
            await tb.sba_write(addr, size, data)
            tb.ref_write(addr, size, data)
            tb.mem_check(base, 4 * tb.nbytes)
            tb.expect_write(addr, size, data, check_id="CHK-SBA-SINGLE-WRITE")
            tb.expect_no_bus("CHK-SBA-SINGLE-WRITE", f"write size {size} @0x{addr:x}")
            got = await tb.sba_read(addr, size)
            tb.expect_read(addr, size, check_id="CHK-SBA-SINGLE-READ")
            tb.expect_no_bus("CHK-SBA-SINGLE-READ", f"read size {size} @0x{addr:x}")
            tb.checker.expect_equal(
                "CHK-SBA-SINGLE-READ", got, tb.ref_read(addr, size), f"read size {size} @0x{addr:x}"
            )
        await _sbdata1_accesses(tb, size)
    tb.finish()


async def _sbdata1_accesses(tb: Tdr2axiTb, size: int) -> None:
    """With sbaccess ``size`` and sbreadondata set, an sbdata1 write and then an sbdata1 read
    each start no AXI request. The subordinate holds READY low over both, so a request one of
    them starts waits on its channel with VALID high when that access has completed."""
    await tb.dmi_write(SBCS, sbcs_value(size, readondata=True))
    what = f"sbaccess {size}, {tb.build.data_width}-bit data"
    mark = tb.bus_mark()
    tb.sub.stall = True
    await tb.dmi_write(SBDATA1, tb.rand_data(WORD))
    tb.expect_no_request(mark, SBDATA1_NO_BUS, f"sbdata1 write, {what}, READY held low")
    await tb.dmi_read(SBDATA1)
    tb.expect_no_request(
        mark, SBDATA1_NO_BUS, f"sbdata1 read with sbreadondata, {what}, READY held low"
    )
    tb.sub.stall = False
    await tb.sba_idle()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_block_test(dut: SimHandleBase) -> None:
    """Block write then block read at every size, as in the programming examples of
    doc/tdr2axi.md."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_block_test")
    for size in range(tb.max_size + 1):
        step = 1 << size
        # Start one access in, so 8- and 16-bit blocks cross a bus word
        base = tb.region + 0x2000 + size * 0x100 + step
        tb.mem_fill(base - step, (BLOCK_LEN + 2) * step)
        data = [tb.rand_data(size) for _ in range(BLOCK_LEN)]

        tb.clear_monitor()
        await tb.sba_block_write(base, size, data)
        for i, value in enumerate(data):
            tb.expect_write(base + i * step, size, value, check_id="CHK-SBA-BLOCK-WRITE")
            tb.ref_write(base + i * step, size, value)
        tb.expect_no_bus("CHK-SBA-BLOCK-WRITE", f"block write size {size}")
        tb.mem_check(base - step, (BLOCK_LEN + 2) * step)
        tb.checker.expect_equal(
            "CHK-SBA-BLOCK-WRITE",
            await tb.dmi_read(SBADDRESS0),
            (base + BLOCK_LEN * step) & WORD_MASK,
            "sbaddress0 after the block write",
        )

        tb.clear_monitor()
        got = await tb.sba_block_read(base, size, BLOCK_LEN)
        for i, value in enumerate(data):
            tb.checker.expect_equal(
                "CHK-SBA-BLOCK-READ", got[i], value, f"block read size {size} [{i}]"
            )
        for i in range(BLOCK_LEN):
            tb.expect_read(base + i * step, size, check_id="CHK-SBA-BLOCK-READ")
        tb.expect_no_bus("CHK-SBA-BLOCK-READ", f"block read size {size}")
    tb.finish()


@cocotb.test(timeout_time=ERRORS_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_errors_test(dut: SimHandleBase) -> None:
    """Misaligned accesses and accesses of every sbaccess wider than the bus, up to 7, never
    reach the bus; bus errors set sberror = 2, on a single access and, with sbautoincrement
    set, on a seeded element after the first of a block write and of a block read whose
    element the sbdata0 read before it starts. After every error, with sbbusyerror clear, an
    sbdata0 write, an sbaddress write with sbreadonaddr and an sbdata0 read with sbreadondata
    each start no transfer until sberror is cleared; after a misaligned access they use its
    size."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_errors_test")
    base = tb.region + 0x3000
    tb.mem_fill(base, 4 * tb.nbytes)
    tb.mem_fill(base + ERROR_BLOCK_OFFSET, ERROR_BLOCK_BYTES)
    await _misaligned_accesses(tb, base)
    await _oversize_accesses(tb, base)
    await _bus_errors(tb, base)
    await _block_write_error(tb, base + ERROR_BLOCK_OFFSET)
    await _block_read_error(tb, base + ERROR_BLOCK_OFFSET)

    # Accesses resume once sberror is cleared
    tb.clear_monitor()
    got = await tb.sba_read(base, WORD)
    tb.expect_read(base, WORD, check_id="CHK-SBA-ERROR-BLOCKS")
    tb.checker.expect_equal(
        "CHK-SBA-SINGLE-READ", got, tb.ref_read(base, WORD), "read after the errors"
    )
    tb.finish()


async def _misaligned_accesses(tb: Tdr2axiTb, base: int) -> None:
    """Misaligned: sberror = 3 and no bus access, and none while sberror stays set."""
    for size in range(1, tb.max_size + 1):
        tb.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
        await tb.sba_set_addr(base + 1)
        await tb.sba_idle(SBERROR_ALIGNMENT)
        what = f"misaligned read of size {size}"
        await _blocked_accesses(tb, base, SBERROR_ALIGNMENT, size=size, what=what)
        tb.expect_no_bus(ERROR_BLOCKS, f"{what} and the accesses after it")
        await _clear_sberror(tb, size, check_id=ERROR_BLOCKS)
        await tb.sba_set_addr(base + (1 << size) // 2)
        await tb.sba_write_data(size, ERROR_PATTERN)
        await tb.sba_idle(SBERROR_ALIGNMENT)
        what = f"misaligned write of size {size}"
        await _blocked_accesses(tb, base, SBERROR_ALIGNMENT, size=size, what=what)
        tb.expect_no_bus(ERROR_BLOCKS, f"{what} and the accesses after it")
        await _clear_sberror(tb, size, check_id=ERROR_BLOCKS)


async def _oversize_accesses(tb: Tdr2axiTb, base: int) -> None:
    """Unsupported size, each sbaccess from one above the bus width to 7 at an address
    aligned to 128 bytes: sberror = 4 and no bus access, and none while sberror stays set."""
    for size in range(tb.max_size + 1, SBACCESS_MAX + 1):
        tb.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
        await tb.sba_set_addr(base)
        await tb.sba_idle(SBERROR_SIZE)
        what = f"read of size {size}"
        await _blocked_accesses(tb, base, SBERROR_SIZE, what=what)
        tb.expect_no_bus(ERROR_BLOCKS, f"{what} and the accesses after it")
        await _clear_sberror(tb, WORD, check_id=ERROR_BLOCKS)


async def _bus_errors(tb: Tdr2axiTb, base: int) -> None:
    """SLVERR on a read and DECERR on a write: sberror = 2 after exactly one transaction, and
    no transaction while sberror stays set."""
    tb.clear_monitor()
    tb.sub.set_read_response(base, RESP_SLVERR)
    await tb.dmi_write(SBCS, sbcs_value(WORD, readonaddr=True))
    await tb.sba_set_addr(base)
    await tb.sba_idle(SBERROR_BUS)
    tb.clear_monitor()
    tb.sub.set_read_response(base, RESP_OKAY)
    await _blocked_accesses(tb, base, SBERROR_BUS, what="read answered SLVERR")
    await _clear_sberror(tb, WORD, check_id=ERROR_BLOCKS)

    tb.sub.set_write_response(base + 4, RESP_DECERR)
    await tb.dmi_write(SBCS, sbcs_value(WORD))
    await tb.sba_set_addr(base + 4)
    await tb.sba_write_data(WORD, DECERR_PATTERN)
    await tb.sba_idle(SBERROR_BUS)
    tb.clear_monitor()
    tb.sub.set_write_response(base + 4, RESP_OKAY)
    # The subordinate stores the data of a write it answers with DECERR
    tb.ref_write(base + 4, WORD, DECERR_PATTERN)
    await _blocked_accesses(tb, base, SBERROR_BUS, what="write answered DECERR")
    await _clear_sberror(tb, WORD, check_id=ERROR_BLOCKS)


def _error_block(tb: Tdr2axiTb, base: int, kind: str) -> tuple[int, int, int]:
    """A seeded size and the seeded element of a block from ``base``, after the first and
    below BLOCK_LEN, that the subordinate answers with a seeded error, logged; returns the
    size, the failing element and the response."""
    size = tb.rng.randint(0, tb.max_size)
    fail_at = tb.rng.randrange(1, BLOCK_LEN)
    resp = tb.rng.choice(ERROR_RESPONSES)
    tb.log.info(
        "%s size %d from 0x%x, element %d answered %s",
        kind,
        size,
        base,
        fail_at,
        RESPONSE_NAMES[resp],
    )
    return size, fail_at, resp


async def _block_write_error(tb: Tdr2axiTb, base: int) -> None:
    """A block write with sbautoincrement whose seeded element after the first is answered
    with an error: each element is polled to sbbusy = 0, the failing one ends with
    sberror = 2, and the block stops there."""
    size, fail_at, resp = _error_block(tb, base, "block write")
    step = 1 << size
    fail_addr = base + fail_at * step
    what = f"element {fail_at} of a size {size} block write answered {RESPONSE_NAMES[resp]}"
    tb.sub.set_write_response(fail_addr, resp)
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, autoinc=True))
    await tb.sba_set_addr(base)
    for i in range(fail_at + 1):
        data = tb.rand_data(size)
        failed = i == fail_at
        await tb.sba_write_data(size, data)
        await tb.sba_idle(SBERROR_BUS if failed else 0)
        if failed:
            tb.skip_write()
        else:
            tb.expect_write(base + i * step, size, data, check_id=BLOCK_WRITE)
        # The subordinate stores the data of a write it answers with an error
        tb.ref_write(base + i * step, size, data)
    tb.sub.set_write_response(fail_addr, RESP_OKAY)
    tb.expect_no_bus(ERROR_BLOCKS, what)
    await _blocked_accesses(tb, base, SBERROR_BUS, what=what)
    await _clear_sberror(tb, WORD, check_id=ERROR_BLOCKS)


async def _block_read_error(tb: Tdr2axiTb, base: int) -> None:
    """A block read with sbreadonaddr, sbreadondata and sbautoincrement whose seeded element
    after the first, started by the sbdata0 read of the element before it, is answered with
    an error: each element is polled to sbbusy = 0 and the failing one ends with
    sberror = 2. The scoreboard grades the data of the elements read."""
    size, fail_at, resp = _error_block(tb, base, "block read")
    step = 1 << size
    fail_addr = base + fail_at * step
    what = f"element {fail_at} of a size {size} block read answered {RESPONSE_NAMES[resp]}"
    tb.sub.set_read_response(fail_addr, resp)
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True, readondata=True, autoinc=True))
    await tb.sba_set_addr(base)
    for i in range(fail_at):
        await tb.sba_idle()
        tb.expect_read(base + i * step, size, check_id=BLOCK_READ)
        await tb.sba_read_data(size)
    await tb.sba_idle(SBERROR_BUS)
    tb.skip_read()
    tb.sub.set_read_response(fail_addr, RESP_OKAY)
    tb.expect_no_bus(ERROR_BLOCKS, what)
    await _blocked_accesses(tb, base, SBERROR_BUS, what=what)
    await _clear_sberror(tb, WORD, check_id=ERROR_BLOCKS)


@cocotb.test(timeout_time=BUSY_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_busy_test(dut: SimHandleBase) -> None:
    """An SBA access stalled on the bus, on the seed's subordinate timing: DM busy, sticky
    TDR busy that ignores updates until op 3, and sbbusyerror. While the read is held, the
    DM answers busy to a write of sbaddress0, sbaddress1, sbdata0 and sbdata1 and an sbdata1
    read, answers sbaddress reads, and answers busy to an sbcs write that would change every
    field, which the sbcs and sbaddress reads after it show unchanged. Then a write held the
    same way sets sbbusyerror, and once it completes the DM answers busy to those writes and
    the sbdata1 read with sbbusyerror alone set, and an sbcs write with bit 22 clear leaves
    sbbusyerror set. The scoreboard grades each busy answer (CHK-SBA-SBBUSYERROR) and the
    captures from it to op 3 (CHK-TDR-BUSY-DM). Then the response sweep, on fixed timing:
    stalled reads and writes, each polled with one sbcs read whose stall ends so the R or B
    handshake falls d i_clk edges after the read is handed to the CDC, d from
    SYNC_STAGES - 1 to SYNC_STAGES + 3. The DM takes the read SYNC_STAGES + 2 edges after
    the hand-over, so the read and the write at d = SYNC_STAGES + 1 put it on the edge after
    the handshake. The scoreboard grades the sbbusy of every sbcs read (CHK-SBA-SBBUSY): 1
    for a read taken before the response, 0 or 1 for the first read after it. Each stalled
    access is graded as a single read or write."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_busy_test")
    base = tb.region + BUSY_OFFSET
    tb.mem_fill(base, BUSY_WORDS * tb.nbytes)
    await _sticky_stall(tb, base)
    await _sbbusyerror_alone(tb, base, base + (BUSY_WORDS - 1) * tb.nbytes)
    _use_sweep_timing(tb)
    await _response_sweep(tb, base + tb.nbytes)
    tb.finish()


def _use_sweep_timing(tb: Tdr2axiTb) -> None:
    """Switch the subordinate to READY high on a free channel and each R or B on the edge after
    its request handshake, keeping the seed's response glitch setting; RELEASE_TO_RESPONSE_EDGES
    holds with this timing. The subordinate draws its timing per request, and nothing may be in
    flight at the switch."""
    knob = tb.sub.cfg.timing
    timing = AxiSubTiming(r_latency=(0, 0), b_latency=(0, 0), rsp_glitch=knob.rsp_glitch)
    tb.sub.cfg = replace(tb.sub.cfg, timing=timing)
    tb.log.info("response sweep: subordinate timing %s, in place of %s", timing, knob)


@dataclass(frozen=True)
class _Landing:
    """Where a released stall's response handshake and the DM's taking of the sbcs read fell,
    in i_clk rising edges after the TCK edge that hands the read to the CDC; None when the
    watch ended without it."""

    response: int | None
    request: int | None

    @property
    def landed(self) -> bool:
        """The DM took the read on the edge after the response handshake."""
        return self.response is not None and self.request == self.response + 1


class _StallRelease:
    """Ends the subordinate's stall so the response handshake falls ``d`` i_clk rising edges
    after the TCK edge that hands the next scan's DMI request to the CDC, the first
    Run-Test/Idle edge after Update-DR, and watches where that handshake and the DM's taking
    of the request fall. The TCK edges of a scan follow the host's grid from its Capture-DR
    edge, so that edge fixes when the request is handed over."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self._tb = tb
        self._armed = False
        self._capture_ps = 0
        self._captured = Event()
        tb.host.add_edge_callback(self._edge)

    def arm(self) -> None:
        """Take the next Capture-DR edge as the polling scan's."""
        self._captured.clear()
        self._armed = True

    async def release(self, d: int) -> _Landing:
        """After the armed Capture-DR edge, end the stall at the i_clk edge that puts the
        response handshake ``d`` edges after the hand-over, and return where it and the DM's
        sbcs read fell. Each edge is sampled once it has settled, so a handshake seen there
        takes place at the next edge."""
        await self._captured.wait()
        await ReadOnly()
        tb = self._tb
        handover_ps = self._capture_ps + (tb.host.edges_to_update - 1) * tb.cfg.tck_ps
        handover = _clk_rises(tb, self._capture_ps, handover_ps)
        release = max(1, handover + d - RELEASE_TO_RESPONSE_EDGES)
        response = request = None
        for edge in range(1, handover + d + SWEEP_WATCH_EDGES):
            await tb.clk_cycles(1)
            await ReadOnly()
            if edge == release:
                tb.sub.stall = False
            if response is None and _response_handshake(tb):
                response = edge + 1 - handover
            if request is None and _sbcs_request(tb):
                request = edge + 1 - handover
            if response is not None and request is not None:
                break
        return _Landing(response, request)

    def _edge(self, *, capture: bool, update_word: int | None) -> None:
        if self._armed and capture:
            self._armed = False
            self._capture_ps = round(get_sim_time("ps"))
            self._captured.set()


def _clk_rises(tb: Tdr2axiTb, start_ps: int, end_ps: int) -> int:
    """i_clk rising edges in (start_ps, end_ps]: i_clk starts low at ``clk_edge_ps``, so it
    rises half a period after it and every period after that."""
    period = tb.cfg.clk_ps
    first = tb.clk_edge_ps + period // 2

    def rises_by(time_ps: int) -> int:
        return (time_ps - first) // period + 1 if time_ps >= first else 0

    return rises_by(end_ps) - rises_by(start_ps)


def _response_handshake(tb: Tdr2axiTb) -> bool:
    return bool(
        (tb.observe("r_valid") and tb.observe("r_ready"))
        or (tb.observe("b_valid") and tb.observe("b_ready"))
    )


def _sbcs_request(tb: Tdr2axiTb) -> bool:
    return bool(
        tb.observe("dmi_req_valid")
        and tb.observe("dmi_req_ready")
        and tb.observe("dmi_req_addr") == SBCS
    )


async def _sticky_stall(tb: Tdr2axiTb, base: int) -> None:
    """A read held on AR: an sbdata0 read collides and the DM answers busy, the sticky busy
    ignores the updates after it until op 3, and sbbusyerror stays set until cleared. The
    sbcs read straight after op 3, ahead of every other collision, shows the sbbusyerror the
    sbdata0 read set. With the read held and sbbusyerror set, the DM answers busy to each
    access of ``_collisions`` in a seeded order, answers the sbaddress reads after them, and
    answers busy to the sbcs write of ``_sbcs_write_in_flight``; the sbcs and sbaddress reads
    after it hold every field they held before."""
    held = sbcs_value(WORD, readonaddr=True)
    tb.clear_monitor()
    tb.sub.stall = True
    await tb.dmi_write(SBCS, held)
    await tb.sba_set_addr(base)  # starts the read, which the stall holds on AR
    # The scoreboard grades the capture that follows the DM's busy answer
    await tb.dmi(DmiOp.READ, SBDATA0, 0, max_polls=5, clear_busy=False)
    # An sbcs write that would clear sbbusyerror, and a read: both ignored while busy
    await tb.scan_sticky(
        [
            tdr_word(DmiOp.WRITE, SBCS, sbcs_value(WORD) | SBCS_CLEAR_ERRORS),
            tdr_word(DmiOp.READ, SBCS, 0),
        ],
        "updates after the DM's busy answer",
    )
    await tb.dmi_clear()
    await _expect_sbbusyerror(tb, "the sbdata0 read during the stall")
    await _busy_answers(tb, _collisions(tb, base), "the read held on AR")
    await _read_sbaddress(tb)
    await _sbcs_write_in_flight(tb, held)
    await _expect_sbbusyerror(tb, "the access during the stall")
    await _read_sbaddress(tb)
    tb.sub.stall = False
    await tb.sba_idle(0, exp_busyerr=True)
    await tb.dmi_write(SBCS, sbcs_value(WORD) | SBCS_CLEAR_ERRORS)
    await tb.sba_idle()
    tb.checker.expect_equal(
        "CHK-SBA-SINGLE-READ",
        await tb.dmi_read(SBDATA0),
        tb.ref_read(base, WORD),
        "data of the stalled read",
    )
    tb.expect_read(base, WORD, check_id="CHK-SBA-SINGLE-READ")
    tb.expect_no_bus(SBBUSYERROR, "stalled read")


async def _expect_sbbusyerror(tb: Tdr2axiTb, what: str) -> None:
    """An sbcs read, which sets no field, with sbbusyerror = 1 (CHK-SBA-SBBUSYERROR); the
    scoreboard compares the whole value with the SBA model."""
    value = await tb.dmi_read(SBCS)
    tb.checker.expect_equal(
        SBBUSYERROR, (value >> SBCS_SBBUSYERROR) & 1, 1, f"sbbusyerror after {what}"
    )


def _collisions(tb: Tdr2axiTb, base: int) -> list[tuple[DmiOp, int, int]]:
    """A write of sbaddress0, sbaddress1, sbdata0 and sbdata1 and an sbdata1 read, each of
    which the DM answers busy while sbbusy or sbbusyerror is set. While sbbusy is set the
    model keeps every register, and the sbaddress reads after them grade sbaddress; with
    sbbusyerror alone set it forgets the register a write names. The sbaddress values name a
    seeded bus word of the busy range other than its first and last, which the held accesses
    use, with sbaddress1 seeded below AXI_ADDR_WIDTH."""
    word = tb.rng.randrange(1, BUSY_WORDS - 1)
    high = tb.rng.getrandbits(max(tb.build.addr_width - 32, 0))
    return [
        (DmiOp.WRITE, SBADDRESS0, (base + word * tb.nbytes) & WORD_MASK),
        (DmiOp.WRITE, SBADDRESS1, high),
        (DmiOp.WRITE, SBDATA0, tb.rand_data(WORD)),
        (DmiOp.WRITE, SBDATA1, tb.rand_data(WORD)),
        (DmiOp.READ, SBDATA1, 0),
    ]


async def _sbcs_write_in_flight(tb: Tdr2axiTb, held: int) -> None:
    """An sbcs write with the read held on AR and sbbusyerror set, after every collision so
    none sets sbbusyerror again. It sets bit 22 and complements every rw field of ``held``,
    the sbcs value the DM holds: each flag inverted and a seeded sbaccess other than held's,
    so the sbcs read straight after it shows any field the DM changed. The scoreboard takes
    the sbcs fields from every sbcs write it sees, answered busy or not, so no access may
    start before the next sbcs write."""
    flags = sbcs_value(0, readonaddr=True, readondata=True, autoinc=True)
    held_size = (held >> SBCS_SBACCESS_LSB) & SBACCESS_MAX
    size = tb.rng.choice([s for s in range(tb.max_size + 1) if s != held_size])
    value = (~held & flags) | sbcs_value(size) | SBCS_CLEAR_ERRORS
    await _busy_answers(tb, [(DmiOp.WRITE, SBCS, value)], "the read held on AR")


async def _busy_answers(tb: Tdr2axiTb, accesses: list[tuple[DmiOp, int, int]], when: str) -> None:
    """Each access in a seeded order, each one scan whose Run-Test/Idle covers the DMI round
    trip, so the DM's busy answer is back by the next capture, then op 3 and a NOP. The
    scoreboard grades the answer (CHK-SBA-SBBUSYERROR), the op 3 scan's capture
    (CHK-TDR-BUSY-DM) and the capture after op 3."""
    order = list(accesses)
    tb.rng.shuffle(order)
    names = [f"{REGISTER_NAMES[addr]} {op.name.lower()}" for op, addr, _ in order]
    tb.log.info("busy answers while %s: %s", when, ", ".join(names))
    for (op, addr, data), name in zip(order, names, strict=True):
        tb.log.info("%s 0x%08x while %s, busy answer expected", name, data, when)
        await tb.scan(tdr_word(op, addr, data), idle=tb.host.cfg.readondata_idle)
        await tb.dmi_clear()


async def _sbbusyerror_alone(tb: Tdr2axiTb, base: int, addr: int) -> None:
    """A write at ``addr`` held on AW and W and a second sbdata0 write the DM answers busy,
    which sets sbbusyerror. Once the write completes, with only sbbusyerror set, the DM
    answers busy to each access of ``_collisions`` and the subordinate holds READY low over
    them, so a request one starts waits on its channel with VALID high when it is checked.
    An sbcs write with bit 22 clear and a seeded sbaccess is taken and leaves sbbusyerror
    set; writing 1 to bit 22 clears it."""
    data = tb.rand_data(WORD)
    what = f"write @0x{addr:x} held on AW and W"
    tb.clear_monitor()
    tb.sub.stall = True
    await tb.dmi_write(SBCS, sbcs_value(WORD))
    await tb.sba_set_addr(addr)
    await tb.dmi_write(SBDATA0, data)  # starts the write, which the stall holds
    await _busy_answers(tb, [(DmiOp.WRITE, SBDATA0, tb.rand_data(WORD))], f"the {what}")
    tb.sub.stall = False
    await tb.sba_idle(0, exp_busyerr=True)
    tb.expect_write(addr, WORD, data, check_id=SINGLE_WRITE)
    tb.ref_write(addr, WORD, data)
    mark = tb.bus_mark()
    tb.sub.stall = True
    try:
        await _busy_answers(tb, _collisions(tb, base), "sbbusyerror alone is set")
        tb.expect_no_request(
            mark, SBBUSYERROR, "accesses with sbbusyerror alone set, READY held low"
        )
    finally:
        tb.sub.stall = False
    size = tb.rng.randint(0, tb.max_size)
    tb.log.info("sbcs write of sbaccess %d with bit 22 clear: sbbusyerror holds", size)
    await tb.dmi_write(SBCS, sbcs_value(size))
    await tb.sba_idle(0, exp_busyerr=True)
    await _clear_sberror(tb, WORD)
    tb.expect_no_bus(SINGLE_WRITE, f"{what}: one transaction")


async def _response_sweep(tb: Tdr2axiTb, base: int) -> None:
    """A stalled read and a stalled write at each d of the sweep, in a seeded order, from
    ``base`` one bus word apart; at least one puts the DM's sbcs read on the edge after its
    response handshake."""
    release = _StallRelease(tb)
    sync = tb.build.sync_stages
    stalls = [(sync + step, write) for step in SWEEP_STEPS for write in (False, True)]
    tb.rng.shuffle(stalls)
    tb.log.info(
        "response sweep, response handshake d i_clk edges after the hand-over: %s",
        ", ".join(f"{'write' if write else 'read'} d={d}" for d, write in stalls),
    )
    landed = 0
    for i, (d, write) in enumerate(stalls):
        landed += await _stalled_poll(tb, release, base + i * tb.nbytes, d, write=write)
    tb.log.info(
        "%d of %d stalled accesses put the DM's sbcs read on the edge after the response",
        landed,
        len(stalls),
    )
    tb.checker.require_stimulus(
        created=landed > 0,
        reason="no stalled access put the DM's sbcs read on the i_clk edge after its R or B "
        "handshake",
    )


async def _stalled_poll(
    tb: Tdr2axiTb, release: _StallRelease, addr: int, d: int, *, write: bool
) -> bool:
    """A read or write at ``addr`` held by the stall, one sbcs read whose stall ends so the
    response handshake falls ``d`` edges after the read is handed over, then the access polled
    to sbbusy = 0 and checked. Returns whether the DM took the sbcs read on the edge after
    the response handshake."""
    what = f"stalled {'write' if write else 'read'} @0x{addr:x}, d={d}"
    data = tb.rand_data(WORD)
    tb.clear_monitor()
    tb.sub.stall = True
    if write:
        await tb.dmi_write(SBCS, sbcs_value(WORD))
        await tb.sba_set_addr(addr)
        await tb.dmi_write(SBDATA0, data)  # starts the write, which the stall holds
    else:
        await tb.dmi_write(SBCS, sbcs_value(WORD, readonaddr=True))
        await tb.sba_set_addr(addr)  # starts the read, which the stall holds
    release.arm()
    released = cocotb.start_soon(release.release(d))
    sbcs = await tb.dmi_read(SBCS)
    landing = await released
    tb.log.info(
        "%s: sbcs 0x%08x, sbbusy %d; response handshake at edge %s and the DM's sbcs read at "
        "edge %s after the hand-over%s",
        what,
        sbcs,
        (sbcs >> SBCS_SBBUSY) & 1,
        landing.response,
        landing.request,
        ": read on the edge after the response" if landing.landed else "",
    )
    await tb.sba_idle()
    if write:
        tb.expect_write(addr, WORD, data, check_id=SINGLE_WRITE)
        tb.ref_write(addr, WORD, data)
    else:
        tb.expect_read(addr, WORD, check_id=SINGLE_READ)
        tb.checker.expect_equal(
            SINGLE_READ, await tb.dmi_read(SBDATA0), tb.ref_read(addr, WORD), what
        )
    tb.expect_no_bus(SINGLE_WRITE if write else SINGLE_READ, f"{what}: one transaction")
    return landing.landed


# --- Shared helpers --------------------------------------------------------------------
def _trailing_zeros(value: int, limit: int) -> int:
    """Trailing zero bits of ``value``, at most ``limit``; ``limit`` for zero."""
    if value == 0:
        return limit
    return min(limit, (value & -value).bit_length() - 1)


async def _read_sbaddress(tb: Tdr2axiTb) -> int:
    """sbaddress1:sbaddress0, sbaddress1 only where the address is wider than 32 bits."""
    value = await tb.dmi_read(SBADDRESS0)
    if tb.build.addr_width > 32:
        value |= await tb.dmi_read(SBADDRESS1) << 32
    return value


async def _clear_sberror(tb: Tdr2axiTb, size: int, *, check_id: str | None = None) -> None:
    """Clear sberror and sbbusyerror and poll to sbbusy = 0; with ``check_id``, sberror reads
    0 under it."""
    await tb.dmi_write(SBCS, sbcs_value(size) | SBCS_CLEAR_ERRORS)
    await tb.sba_idle(check_id=check_id)


async def _blocked_accesses(
    tb: Tdr2axiTb, addr: int, sberror: int, *, size: int = WORD, what: str
) -> None:
    """With sberror set and sbbusyerror clear, at sbaccess ``size``: an sbdata0 write, an
    sbaddress write of ``addr`` with sbreadonaddr and an sbdata0 read with sbreadondata each
    start no transfer, and sberror holds and sbbusyerror stays clear. ``what`` names the
    access that set sberror. The subordinate holds READY low over the three, so a request one
    of them starts waits on its channel with VALID high when that access is checked."""
    context = f"after the {what}, sberror {sberror}, sbaccess {size}"
    first = tb.bus_mark()
    tb.sub.stall = True
    try:
        await tb.dmi_write(SBCS, sbcs_value(size))
        mark = tb.bus_mark()
        await tb.dmi_write(SBDATA0, tb.rand_data(WORD))
        tb.expect_no_request(mark, ERROR_BLOCKS, f"{context}: sbdata0 write, READY held low")
        await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
        mark = tb.bus_mark()
        await tb.sba_set_addr(addr)
        tb.expect_no_request(
            mark,
            ERROR_BLOCKS,
            f"{context}: sbaddress write of 0x{addr:x} with sbreadonaddr, READY held low",
        )
        await tb.dmi_write(SBCS, sbcs_value(size, readondata=True))
        mark = tb.bus_mark()
        await tb.dmi_read(SBDATA0)
        tb.expect_no_request(
            mark, ERROR_BLOCKS, f"{context}: sbdata0 read with sbreadondata, READY held low"
        )
    finally:
        tb.sub.stall = False
    await tb.sba_idle(sberror)
    tb.expect_no_bus_since(first, ERROR_BLOCKS, f"{context}: the three accesses, polled to idle")


# --- Address width ---------------------------------------------------------------------
@dataclass
class _Programmed:
    """sbcs and sbaddress1 as the test last wrote them."""

    sbcs: int | None = None
    high: int | None = None


@cocotb.test(timeout_time=ADDRESS_WIDTH_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_address_width_test(dut: SimHandleBase) -> None:
    """Walking-one and walking-zero addresses over every bit below AXI_ADDR_WIDTH, and seeded
    backgrounds with one bit set and then cleared, in a seeded order, one read or write
    each: AxADDR carries every bit, and sbaddress0 and, above 32 bits, sbaddress1 read back
    what was written. No bit at or above AXI_ADDR_WIDTH is written."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_address_width_test")
    width = tb.build.addr_width
    mask = (1 << width) - 1
    addrs = [1 << bit for bit in range(width)] + [mask ^ (1 << bit) for bit in range(width)]
    for bit in tb.rng.sample(range(width), min(ADDRESS_BACKGROUNDS, width)):
        background = tb.rng.getrandbits(width)
        addrs += [background | (1 << bit), background & ~(1 << bit)]
    tb.rng.shuffle(addrs)
    tb.log.info("%d addresses over %d address bits", len(addrs), width)
    programmed = _Programmed()
    for addr in addrs:
        await _address_access(tb, addr, programmed)
    tb.finish()


async def _address_access(tb: Tdr2axiTb, addr: int, programmed: _Programmed) -> None:
    """One access at ``addr``, of a size its alignment allows, then the address read back.
    sbcs and sbaddress1 are written only when they change."""
    size = tb.rng.randint(0, _trailing_zeros(addr, tb.max_size))
    write = tb.rng.random() < 0.5
    what = f"{'write' if write else 'read'} size {size} @0x{addr:x}"
    sbcs = sbcs_value(size, readonaddr=not write)
    if programmed.sbcs != sbcs:
        await tb.dmi_write(SBCS, sbcs)
        programmed.sbcs = sbcs
    high = addr >> 32
    high_written = tb.build.addr_width > 32 and programmed.high != high
    if high_written:
        await tb.dmi_write(SBADDRESS1, high)
        programmed.high = high
    tb.clear_monitor()
    await tb.dmi_write(SBADDRESS0, addr & WORD_MASK)
    if write:
        data = tb.rand_data(size)
        await tb.sba_write_data(size, data)
        await tb.sba_idle()
        tb.log.info("%s data 0x%x answered OKAY", what, data)
        tb.expect_write(addr, size, data, check_id=ADDRESS_WIDTH)
    else:
        await tb.sba_idle()
        tb.log.info("%s answered OKAY", what)
        tb.expect_read(addr, size, check_id=ADDRESS_WIDTH)
    tb.expect_no_bus(ADDRESS_WIDTH, what)
    tb.checker.expect_equal(
        ADDRESS_WIDTH, await tb.dmi_read(SBADDRESS0), addr & WORD_MASK, f"sbaddress0 after {what}"
    )
    if high_written:
        tb.checker.expect_equal(
            ADDRESS_WIDTH, await tb.dmi_read(SBADDRESS1), high, f"sbaddress1 after {what}"
        )


# --- Address after a failed access -----------------------------------------------------
@cocotb.test(timeout_time=ERROR_ADDRESS_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_sba_error_address_test(dut: SimHandleBase) -> None:
    """With sbautoincrement set, reads an sbaddress0 write starts and block writes whose
    seeded element fails, each answered SLVERR or DECERR at a seeded size: after the error
    sbaddress reads the failing address (0.13.2 3.12.19, 3.12.23). One block write fails
    after a successful element, so a run that reaches it also sees sbaddress advance; the
    first wrong address stops the test."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_error_address_test")
    base, span = tb.address_range(ERROR_ADDRESS_OFFSET, ERROR_ADDRESS_SPAN)
    tb.mem_fill(base, span)
    scenarios = [("read", _failed_read)] * ERROR_ADDRESS_ROUNDS + [
        (
            "block write failing after a success" if i == 0 else "block write",
            functools.partial(_failed_block_write, after_success=i == 0),
        )
        for i in range(ERROR_ADDRESS_ROUNDS)
    ]
    tb.rng.shuffle(scenarios)
    tb.log.info("scenario order: %s", ", ".join(name for name, _ in scenarios))
    for _, scenario in scenarios:
        await scenario(tb, base, span)
    tb.finish()


async def _expect_failing_address(tb: Tdr2axiTb, addr: int, size: int, what: str) -> None:
    """sbaddress reads ``addr``, the address of the access that failed. The FAIL detail
    names an advance by the access size apart from any other wrong value."""
    observed = await _read_sbaddress(tb)
    advanced = (addr + (1 << size)) & ((1 << tb.build.addr_width) - 1)
    if observed == advanced:
        detail = (
            f"sbaddress advanced after a failed access: 0x{observed:x}, one access past the "
            f"failing address 0x{addr:x}, {what}"
        )
    else:
        detail = f"sbaddress 0x{observed:x} {what}, not the failing address 0x{addr:x}"
    tb.checker.expect_true(NO_INC_AFTER_ERROR, passed=observed == addr, detail=detail)


async def _failed_read(tb: Tdr2axiTb, base: int, span: int) -> None:
    """A read an sbaddress0 write starts with sbautoincrement set, answered with an error."""
    size = tb.rng.choice([s for s in range(tb.max_size + 1) if span >> s >= 2])
    step = 1 << size
    addr = base + tb.rng.randrange(0, span - 2 * step + 1, step)
    resp = tb.rng.choice(ERROR_RESPONSES)
    what = f"after the read of size {size} at 0x{addr:x} answered {RESPONSE_NAMES[resp]}"
    tb.log.info("read size %d @0x%x, answered %s", size, addr, RESPONSE_NAMES[resp])
    tb.sub.set_read_response(addr, resp)
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True, autoinc=True))
    await tb.sba_set_addr(addr)
    await tb.sba_idle(SBERROR_BUS)
    tb.clear_monitor()
    tb.sub.set_read_response(addr, RESP_OKAY)
    await _expect_failing_address(tb, addr, size, what)
    await _clear_sberror(tb, size)


async def _failed_block_write(
    tb: Tdr2axiTb, base: int, span: int, *, after_success: bool = False
) -> None:
    """A block write with sbautoincrement set whose seeded element is answered with an
    error; each element is polled to sbbusy = 0, and the block stops at the error. With
    ``after_success`` the failing element is not the first, so sbaddress has advanced
    before the error."""
    size = tb.rng.choice([s for s in range(tb.max_size + 1) if span >> s >= 3])
    step = 1 << size
    count = tb.rng.randint(2, min(ERROR_BLOCK_MAX, (span >> size) - 1))
    fail_at = tb.rng.randrange(1 if after_success else 0, count)
    start = base + tb.rng.randrange(0, span - (count + 1) * step + 1, step)
    fail_addr = start + fail_at * step
    resp = tb.rng.choice(ERROR_RESPONSES)
    what = (
        f"after element {fail_at} of a size {size} block from 0x{start:x} answered "
        f"{RESPONSE_NAMES[resp]}"
    )
    tb.log.info(
        "block write size %d @0x%x, element %d @0x%x answered %s",
        size,
        start,
        fail_at,
        fail_addr,
        RESPONSE_NAMES[resp],
    )
    tb.sub.set_write_response(fail_addr, resp)
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, autoinc=True))
    await tb.sba_set_addr(start)
    for i in range(fail_at + 1):
        data = tb.rand_data(size)
        failed = i == fail_at
        await tb.sba_write_data(size, data)
        await tb.sba_idle(SBERROR_BUS if failed else 0)
        if failed:
            tb.skip_write()
        else:
            tb.expect_write(start + i * step, size, data, check_id=BLOCK_WRITE)
        # The subordinate stores the data of a write it answers with an error
        tb.ref_write(start + i * step, size, data)
    tb.sub.set_write_response(fail_addr, RESP_OKAY)
    await _expect_failing_address(tb, fail_addr, size, what)
    await _clear_sberror(tb, size)


# --- Seeded mix of every operation -----------------------------------------------------
@dataclass
class _Mix:
    """The stress test's bench, address range and busy-recovery state."""

    tb: Tdr2axiTb
    base: int
    span: int
    short_idle: int
    busy_events: int = 0

    @property
    def rng(self) -> random.Random:
        return self.tb.rng

    def aligned(self, size: int, room: int = 1) -> int:
        """A seeded address aligned to 2**size with ``room`` accesses of that size from it
        inside the range."""
        step = 1 << size
        return self.base + self.rng.randrange(0, self.span - room * step + 1, step)

    def mem_check(self, start: int, nbytes: int) -> None:
        """The bus words holding [start, start + nbytes) match the expected image."""
        word = self.tb.nbytes
        low = start - start % word
        high = -(-(start + nbytes) // word) * word
        self.tb.mem_check(low, high - low)

    async def pending_read(self) -> None:
        """An sbcs read scanned with Run-Test/Idle ``short_idle``: the next op scan captures
        while it is pending, reads busy and is ignored until op 3."""
        tb = self.tb
        raised = tb.host.idle_cycles
        tb.host.idle_cycles = self.short_idle
        await tb.scan(tdr_word(DmiOp.READ, SBCS, 0))
        tb.host.idle_cycles = raised

    def recovered(self, result_retries: int, what: str) -> None:
        self.tb.checker.require_stimulus(
            created=result_retries > 0,
            reason=f"{what} at Run-Test/Idle {self.short_idle} read no busy capture",
        )
        self.busy_events += 1


@cocotb.test(timeout_time=STRESS_TIMEOUT_US, timeout_unit="us")
async def tdr2axi_sba_stress_test(dut: SimHandleBase) -> None:
    """A seeded mix of single and block reads and writes of every size, misaligned and
    oversized accesses, and SLVERR or DECERR on a single access or a block element, some
    accesses made through a busy capture that op 3 and a resend recover. SbaRefModel and the
    scoreboard predict and compare every DMI answer and transaction, and the test compares
    the data, memory, sberror and the bus after each operation. After an error every access
    that would start a transfer starts none, and the mix clears sberror and writes
    sbaddress0 before the next access."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_sba_stress_test")
    tb.sub.arm_fault(negative_fault(STRESS_FAULTS))
    base, span = tb.address_range(STRESS_OFFSET, STRESS_SPAN)
    mix = _Mix(tb, base, span, short_idle=tb.rng.randint(0, tb.build.sync_stages - 2))
    tb.mem_fill(base, span)
    ops = list(STRESS_EACH_KIND)
    for _ in range(STRESS_EXTRA_OPS):
        kind = tb.rng.choice(STRESS_KINDS)
        error = kind in STRESS_KINDS[:4] and tb.rng.random() < STRESS_ERROR_SHARE
        busy = kind in STRESS_KINDS[:2] and not error and tb.rng.random() < STRESS_BUSY_SHARE
        ops.append((kind, error, busy))
    tb.rng.shuffle(ops)
    tb.log.info(
        "%d operations; Run-Test/Idle %d before a busy capture: %s",
        len(ops),
        mix.short_idle,
        ", ".join(f"{k}{' error' if e else ''}{' busy' if b else ''}" for k, e, b in ops),
    )
    for kind, error, busy in ops:
        if kind == "write":
            await _mix_write(mix, error=error, busy=busy)
        elif kind == "read":
            await _mix_read(mix, error=error, busy=busy)
        elif kind == "block_write":
            await _mix_block_write(mix, error=error)
        elif kind == "block_read":
            await _mix_block_read(mix, error=error)
        else:
            await _mix_access_error(mix, oversize=kind == "oversize")
    tb.log.info("%d accesses recovered from a busy capture", mix.busy_events)
    tb.finish()


async def _mix_after_error(mix: _Mix, sberror: int, what: str) -> None:
    """The blocked accesses after the failed access ``what``, with sberror set and
    sbbusyerror clear, then the mix clears sberror, which reads 0."""
    await _blocked_accesses(mix.tb, mix.base, sberror, what=what)
    await _clear_sberror(mix.tb, WORD, check_id=ERROR_BLOCKS)


async def _mix_write(mix: _Mix, *, error: bool, busy: bool) -> None:
    """A single write; with ``busy`` its sbdata0 write's op scan captures behind a pending
    read, so op 3 and a resend make it, and it reaches the bus once."""
    tb = mix.tb
    size = mix.rng.randint(0, tb.max_size)
    addr = mix.aligned(size)
    data = tb.rand_data(size)
    what = f"write size {size} @0x{addr:x}"
    if error:
        tb.sub.set_write_response(addr, mix.rng.choice(ERROR_RESPONSES))
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size))
    await tb.sba_set_addr(addr)
    if size == SIZE_64:
        await tb.dmi_write(SBDATA1, data >> 32)
    if busy:
        raised = tb.host.idle_cycles
        await mix.pending_read()
        result = await tb.dmi(DmiOp.WRITE, SBDATA0, data & WORD_MASK)
        tb.host.idle_cycles = raised
        mix.recovered(result.retries, f"sbdata0 write of the {what}")
    else:
        await tb.dmi_write(SBDATA0, data & WORD_MASK)
    await tb.sba_idle(SBERROR_BUS if error else 0)
    if error:
        tb.skip_write()
    else:
        tb.expect_write(addr, size, data, check_id=SINGLE_WRITE)
    if not error and not busy:
        tb.expect_no_bus(SINGLE_WRITE, f"{what}: one transaction")
    tb.ref_write(addr, size, data)
    mix.mem_check(addr, 1 << size)
    if error:
        tb.sub.set_write_response(addr, RESP_OKAY)
        await _mix_after_error(mix, SBERROR_BUS, what)


async def _mix_read(mix: _Mix, *, error: bool, busy: bool) -> None:
    """A single read; with ``busy`` its sbdata0 read is polled too early, and op 3 and
    resends at a raised Run-Test/Idle return the data."""
    tb = mix.tb
    size = mix.rng.randint(0, tb.max_size)
    addr = mix.aligned(size)
    what = f"read size {size} @0x{addr:x}"
    if error:
        tb.sub.set_read_response(addr, mix.rng.choice(ERROR_RESPONSES))
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
    await tb.sba_set_addr(addr)
    await tb.sba_idle(SBERROR_BUS if error else 0)
    if error:
        tb.skip_read()
    else:
        tb.expect_read(addr, size, check_id=SINGLE_READ)
        tb.expect_no_bus(SINGLE_READ, f"{what}: one transaction")
    if error:
        tb.sub.set_read_response(addr, RESP_OKAY)
        await _mix_after_error(mix, SBERROR_BUS, what)
        return
    high = await tb.dmi_read(SBDATA1) if size == SIZE_64 else 0
    if busy:
        raised = tb.host.idle_cycles
        tb.host.idle_cycles = mix.short_idle
        need = tb.host.cfg.readondata_idle
        result = await tb.dmi(
            DmiOp.READ, SBDATA0, max_polls=busy_backoffs(mix.short_idle, need) + 1
        )
        tb.host.idle_cycles = raised
        mix.recovered(result.retries, f"sbdata0 read of the {what}")
        low = result.rdata
    else:
        low = await tb.dmi_read(SBDATA0)
    tb.checker.expect_equal(
        SINGLE_READ, ((high << 32) | low) & size_mask(size), tb.ref_read(addr, size), what
    )


async def _mix_block_write(mix: _Mix, *, error: bool) -> None:
    """A block write with sbautoincrement, each element polled to sbbusy = 0; with
    ``error`` a seeded element fails and the block stops there."""
    tb = mix.tb
    size = mix.rng.randint(0, tb.max_size)
    step = 1 << size
    count = mix.rng.randint(2, STRESS_BLOCK_MAX)
    # One element of room either side for the memory check
    start = mix.aligned(size, room=count + 2) + step
    fail_at = mix.rng.randrange(count) if error else None
    what = f"block write of {count} size {size} from 0x{start:x}"
    if fail_at is not None:
        tb.sub.set_write_response(start + fail_at * step, mix.rng.choice(ERROR_RESPONSES))
    tb.clear_monitor()
    await tb.dmi_write(SBCS, sbcs_value(size, autoinc=True))
    await tb.sba_set_addr(start)
    for i in range(count):
        data = tb.rand_data(size)
        failed = i == fail_at
        await tb.sba_write_data(size, data)
        await tb.sba_idle(SBERROR_BUS if failed else 0)
        if failed:
            tb.skip_write()
        else:
            tb.expect_write(start + i * step, size, data, check_id=BLOCK_WRITE)
        tb.ref_write(start + i * step, size, data)
        if failed:
            break
    tb.expect_no_bus(BLOCK_WRITE, what)
    mix.mem_check(start - step, (count + 2) * step)
    if fail_at is not None:
        tb.sub.set_write_response(start + fail_at * step, RESP_OKAY)
        await _mix_after_error(mix, SBERROR_BUS, what)
        return
    tb.checker.expect_equal(
        BLOCK_WRITE,
        await tb.dmi_read(SBADDRESS0),
        (start + count * step) & WORD_MASK,
        f"sbaddress0 after the {what}",
    )


async def _mix_block_read(mix: _Mix, *, error: bool) -> None:
    """A block read with sbreadonaddr and sbautoincrement, each element polled to sbbusy = 0.
    Before each element's sbdata0 read but the last's a seeded draw keeps sbreadondata set,
    so the read starts the next element, or clears it, so the read starts nothing and an
    sbaddress0 write starts the next element; sbreadondata is clear before the last. sbcs is
    written only with sbbusy = 0. With ``error`` a seeded element fails, and the block stops
    before reading its sbdata0."""
    tb = mix.tb
    size = mix.rng.randint(0, tb.max_size)
    step = 1 << size
    count = mix.rng.randint(2, STRESS_BLOCK_MAX)
    start = mix.aligned(size, room=count + 1)
    fail_at = mix.rng.randrange(count) if error else None
    what = f"block read of {count} size {size} from 0x{start:x}"
    if fail_at is not None:
        tb.sub.set_read_response(start + fail_at * step, mix.rng.choice(ERROR_RESPONSES))
    tb.clear_monitor()
    readondata = True
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True, readondata=True, autoinc=True))
    await tb.sba_set_addr(start)
    got = []
    started_by = []
    for i in range(count):
        failed = i == fail_at
        await tb.sba_idle(SBERROR_BUS if failed else 0)
        if failed:
            tb.skip_read()
            break
        tb.expect_read(start + i * step, size, check_id=BLOCK_READ)
        more = i < count - 1
        keep = more and mix.rng.random() < STRESS_READONDATA_SHARE
        if keep != readondata:
            sbcs = sbcs_value(size, readonaddr=True, readondata=keep, autoinc=True)
            await tb.dmi_write(SBCS, sbcs)
            readondata = keep
        got.append(await tb.sba_read_data(size))
        if more:
            started_by.append("sbdata0" if keep else "sbaddress0")
            if not keep:
                await tb.sba_set_addr(start + (i + 1) * step)
    tb.log.info("%s: elements after the first started by %s", what, ", ".join(started_by))
    tb.expect_no_bus(BLOCK_READ, what)
    for i, value in enumerate(got):
        tb.checker.expect_equal(
            BLOCK_READ, value, tb.ref_read(start + i * step, size), f"{what} [{i}]"
        )
    if fail_at is not None:
        tb.sub.set_read_response(start + fail_at * step, RESP_OKAY)
        await _mix_after_error(mix, SBERROR_BUS, what)


async def _mix_access_error(mix: _Mix, *, oversize: bool) -> None:
    """A read or write misaligned to its size, or of a size wider than the bus at an aligned
    address: sberror = 3 or 4 and no transaction."""
    tb = mix.tb
    if oversize:
        size = mix.rng.randint(tb.max_size + 1, SIZE_MAX)
        addr = mix.base + mix.rng.randrange(0, mix.span, 1 << size)
        sberror = SBERROR_SIZE
    else:
        size = mix.rng.randint(1, tb.max_size)
        addr = mix.aligned(size) + mix.rng.randrange(1, 1 << size)
        sberror = SBERROR_ALIGNMENT
    write = mix.rng.random() < 0.5
    what = f"{'write' if write else 'read'} size {size} @0x{addr:x}"
    tb.log.info("access error: %s", what)
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=not write))
    await tb.sba_set_addr(addr)
    if write:
        await tb.sba_write_data(min(size, SIZE_64), tb.rand_data(min(size, SIZE_64)))
    await tb.sba_idle(sberror)
    await _mix_after_error(mix, sberror, what)
