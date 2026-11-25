# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi DMI TDR: the op decode and capture image, the update and capture strobes, the busy
status and its recovery by op 3 and resend, pipelined reads, the result latency, and a TCK
stopped while an operation completes.

A capture comes Run-Test/Idle + 3 TCK edges after the update before it, and one DMI round
trip takes at most N = ``readondata_idle`` Run-Test/Idle edges, so a scan with at least N
lets its result cross back before the next capture.
"""

from __future__ import annotations

import random
from collections import Counter

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import ReadOnly

from env.axi_sub import AxiAddr, AxiW
from env.tdr2axi_scoreboard import SCOREBOARD_CHECKS
from env.tdr_host import (
    ABSTRACTCS,
    COMMAND,
    DMCONTROL,
    DMSTATUS,
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBDATA0,
    SBDATA1,
    TDR_ADDR_LSB,
    TDR_DATA_LSB,
    DmiOp,
    DmiResult,
    DmiStatus,
    busy_backoffs,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_base_test import (
    SBCS_CLEAR_ERRORS,
    SBCS_SBBUSYERROR,
    TIMEOUT_MS,
    WORD_MASK,
    Tdr2axiTb,
)
from tests.tdr2axi_test_cfg import PAD_BITS_MAX, PAUSE_CYCLES_MAX
from tests.tdr2axi_timing_monitor import (
    RESULT_LATENCY_IDS,
    RESULT_LATENCY_TIMING,
    CaptureEdge,
    ResponseHandshake,
    ResultArrival,
)

WORD = 2
PIPELINED_WORDS = 8
RECOVERY_ROUNDS = 4
RETRY_PATTERN = 0xC0DE_0000
RESEND_READ_OFFSET = 0x100
STALLED_RESEND_OFFSET = 0x180
SIDE_EFFECT_OFFSET = 0x200
TCK_STOP_OFFSET = 0x300
# A resent read runs once, or twice when its first run was taken (S-TDR2AXI-0046)
RESEND_RUNS = (1, 2)
# Op scans of the resend: up to two busy captures while the first run is pending, the run
# that completes, and one spare
RESEND_POLLS = 4
RESEND_READ = "CHK-TDR-RESEND-READ"
READONDATA_IDLE = "CHK-ENV-READONDATA-IDLE"
OP_DECODE = "CHK-TDR-OP-DECODE"
UPDATE_EDGE = "CHK-TDR-UPDATE-EDGE"
BUSY_CAPTURE = "CHK-TDR-BUSY-CAPTURE"
CLK_STOP_PERIODS = 4
CLK_RESUME_PERIODS = 50
STICKY_POLLS = 3
# Ops of an update scanned while an operation is pending, each refused (S-TDR2AXI-0043)
REFUSED_OPS = (DmiOp.NOP, DmiOp.READ, DmiOp.WRITE)
BUSY_SCANS_MAX = 2
# Longest seeded wait, in nominal i_clk periods, with i_clk held stopped after the refused
# updates
CLK_HOLD_PERIODS = 100
BUSY_UPDATE_OFFSET = 0x5000
BUSY_UPDATE_STRIDE = 0x100
# dmi_resp_resp of a busy DM answer (X-RISCV_DEBUG-dmi-op)
DMI_RESP_BUSY = 3
# TCK rising edges from the start of a scan through its capture edge
CAPTURE_EDGE = 3
# data0 and progbuf0: DM registers whose write starts nothing with abstractauto clear
DATA0 = 0x04
PROGBUF0 = 0x20
# DMI addresses with no DM register (X-RISCV_DEBUG-dm-addresses)
UNMAPPED_LOW = (0x00, 0x03)
UNMAPPED_HIGH = (0x41, 0x7F)
MAPPED_READS = (
    DATA0,
    DMCONTROL,
    DMSTATUS,
    ABSTRACTCS,
    COMMAND,
    PROGBUF0,
    SBCS,
    SBADDRESS0,
    SBADDRESS1,
    SBDATA0,
    SBDATA1,
)
OPS_PER_KIND = 12
OPS_BUSY_EVERY = 8
UNMAPPED_SHARE = 0.25
STROBE_SCANS = 16
HELD_OPS = (DmiOp.READ, DmiOp.WRITE, DmiOp.NOP, DmiOp.CLEAR)
BUSY_CAPTURE_ROUNDS = 6
LATENCY_MARGIN = 2
LATENCY_ROUNDS = 2
TCK_STOP_ROUNDS = 6
# Longest TCK stop, in i_clk periods; the first round's stop is at least TCK_STOP_LONG, so a
# DM answer given in the stop waits more than 100 i_clk periods for TCK
TCK_STOP_PERIODS = 160
TCK_STOP_LONG = 110


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_busy_update_test(dut: SimHandleBase) -> None:
    """i_clk stopped: a write is taken and waits, and an update of any op, NOP included,
    while it is pending is refused with sticky busy, which ignores every update until op 3.

    One round per op of ``REFUSED_OPS``, in a seeded order, each with its own memory range.
    A round stops i_clk for a seeded number of periods, scans write A, then the refused
    update and one to ``BUSY_SCANS_MAX`` more seeded updates of op 0, 1 or 2, so every
    round scans an update under the sticky busy while write A is pending, holds i_clk
    stopped for a seeded number of periods and restarts it. It then polls with NOPs, scans
    an update of each op 0, 1 and 2 under the sticky busy, and clears it with op 3. A
    capture while a write is pending is not an error; the refusal shows at the next capture.
    The scoreboard grades each capture's status (CHK-TDR-BUSY-UPDATE, CHK-TDR-STICKY-IGNORE),
    the one write that reaches the bus and the memory against the models. Each round logs
    the op, address and data of every update it scans while write A is pending and under
    the sticky busy.
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_busy_update_test")
    await tb.dmi_write(SBCS, sbcs_value(WORD, autoinc=True))
    refused_ops = list(REFUSED_OPS)
    tb.rng.shuffle(refused_ops)
    for index, refused in enumerate(refused_ops):
        base = tb.region + BUSY_UPDATE_OFFSET + index * BUSY_UPDATE_STRIDE
        await _busy_update_round(tb, f"round {index}, {refused.name} refused", refused, base)
    tb.finish()


async def _busy_update_round(tb: Tdr2axiTb, what: str, refused: DmiOp, base: int) -> None:
    """Write A to ``base`` taken with i_clk stopped, an update of op ``refused`` and the
    seeded updates after it refused, the sticky busy kept until op 3, and the memory at
    ``base`` holding write A alone."""
    rng = tb.rng
    stop = rng.randint(1, CLK_STOP_PERIODS)
    hold = rng.randint(0, CLK_HOLD_PERIODS)
    write_a = rng.getrandbits(32)
    busy_words = [
        _busy_word(rng, op)
        for op in (refused, *rng.choices(REFUSED_OPS, k=rng.randint(1, BUSY_SCANS_MAX)))
    ]
    tb.log.info(
        "%s: i_clk stopped %d periods before write A 0x%08x and %d after %d updates while "
        "it is pending: %s",
        what,
        stop,
        write_a,
        hold,
        len(busy_words),
        ", ".join(_word_text(word) for word in busy_words),
    )
    tb.mem_fill(base, 2 * tb.nbytes)
    await tb.sba_set_addr(base)
    tb.clear_monitor()
    tb.stop_clk()
    await tb.wait_clk_periods(stop)
    await tb.scan(tdr_word(DmiOp.WRITE, SBDATA0, write_a))  # A: taken, waits for i_clk
    for word in busy_words:
        await tb.scan(word)
    await tb.wait_clk_periods(hold)
    tb.start_clk()
    await tb.wait_clk_periods(CLK_RESUME_PERIODS)
    for _ in range(STICKY_POLLS):
        await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    # With write A complete and nothing pending, the sticky busy ignores every op but 3
    sticky = _sticky_words(rng)
    tb.log.info("%s: under the sticky busy: %s", what, ", ".join(_word_text(w) for w in sticky))
    await tb.scan_sticky(sticky, f"{what}: sticky busy")
    await tb.dmi_clear()
    await tb.sba_idle()
    tb.clear_monitor()
    tb.ref_write(base, WORD, write_a)
    tb.mem_check(base, 2 * tb.nbytes)


def _busy_word(rng: random.Random, op: DmiOp) -> int:
    """An update of op ``op`` to sbaddress0, sbdata0 or sbcs with seeded data."""
    return tdr_word(op, rng.choice((SBADDRESS0, SBDATA0, SBCS)), rng.getrandbits(32))


def _word_text(word: int) -> str:
    """An update word's op, address and data, for a log line to replay it from."""
    op = DmiOp(word & 3).name
    return f"{op} 0x{word >> TDR_ADDR_LSB:02x} 0x{(word >> TDR_DATA_LSB) & WORD_MASK:08x}"


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_pipelined_test(dut: SimHandleBase) -> None:
    """The next op in place of the NOP poll, with enough Run-Test/Idle for each result to
    cross back: one scan per 32-bit word. The scoreboard grades each capture, its status and
    the word of the read before it, against the models."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_pipelined_test")
    n = PIPELINED_WORDS
    base = tb.region + 0x6000
    saved_idle = tb.host.idle_cycles
    tb.mem_fill(base, n * 4)
    await tb.dmi_write(SBCS, sbcs_value(WORD, readonaddr=True, readondata=True, autoinc=True))
    tb.clear_monitor()
    await tb.sba_set_addr(base)  # starts word 0
    tb.host.idle_cycles = tb.host.cfg.pipe_idle
    # Ops: n-1 sbdata0 reads, clear sbreadondata, the last sbdata0 read, NOP. The capture of
    # each scan returns the previous op's result.
    tb.log.info("pipelined reads at Run-Test/Idle %d", tb.host.idle_cycles)
    await tb.scan(tdr_word(DmiOp.READ, SBDATA0, 0))
    for i in range(1, n + 1):
        if i == n - 1:
            await tb.scan(tdr_word(DmiOp.WRITE, SBCS, sbcs_value(WORD, autoinc=True)))
        else:
            await tb.scan(tdr_word(DmiOp.READ, SBDATA0, 0))
    await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    tb.host.idle_cycles = saved_idle
    await tb.sba_idle()
    for i in range(n):
        tb.expect_read(base + 4 * i, WORD, check_id="CHK-SBA-BLOCK-READ")
    tb.expect_no_bus("CHK-SBA-BLOCK-READ", "pipelined read")
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_busy_recovery_test(dut: SimHandleBase) -> None:
    """Captures while a read is pending read busy: a read polled too early, and a read and a
    write each scanned while an earlier read is pending. Op 3 and a resend with more
    Run-Test/Idle complete each with its result, and each resent write reaches the bus
    once. An sbdata0 read with sbreadondata polled too early is resent the same way, twice:
    once with the bus free, where its bus read runs once or twice, and once with the bus
    stalled until the DM answers the resend busy, so the resend is refused with sbbusyerror
    and the bus read runs once. Each bus read of that location returns new data, and the
    debugger reads the first run's. Last, an sbdata0 read with sbreadondata at a
    side-effecting location, with Run-Test/Idle of at least N, reads it once, and the host
    checks that count before the read. The scoreboard grades each resend, its result and
    the resent writes on the bus against the models.

    The capture of the next scan comes Run-Test/Idle + 3 TCK edges after an update, and a
    read's answer reaches the bridge no sooner than SYNC_STAGES + 2 edges after it
    (T-TDR2AXI-resp-to-tdr), so a count below SYNC_STAGES - 1 makes that capture busy at
    any clock ratio.
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_busy_recovery_test")
    base = tb.region + 0x8000
    saved_idle = tb.host.idle_cycles
    need = tb.host.cfg.readondata_idle
    short_idle = tb.rng.randint(0, tb.build.sync_stages - 2)
    tb.log.info(
        "Run-Test/Idle %d after a busy-making scan; one round trip needs %d", short_idle, need
    )
    tb.mem_fill(base, 2 * RECOVERY_ROUNDS * 4)
    tb.mem_fill(base + RESEND_READ_OFFSET, 4)
    tb.mem_fill(base + STALLED_RESEND_OFFSET, 4)
    tb.mem_fill(base + SIDE_EFFECT_OFFSET, 4)
    await tb.dmi_write(SBCS, sbcs_value(WORD, autoinc=True))
    await tb.sba_set_addr(base)
    tb.clear_monitor()

    busy_captures, resent_writes = await _recovery_rounds(tb, short_idle, base)
    tb.host.idle_cycles = saved_idle
    tb.log.info("%d busy captures, each recovered by op 3 and a resend", busy_captures)
    tb.checker.require_stimulus(
        created=busy_captures > 0,
        reason=f"no busy capture at Run-Test/Idle {short_idle}, so no recovery ran",
    )
    await tb.sba_idle()
    for addr, data, retries in resent_writes:
        if retries:
            tb.skip_write()
        else:
            tb.expect_write(addr, WORD, data, check_id="CHK-SBA-BLOCK-WRITE")
        tb.ref_write(addr, WORD, data)
    tb.mem_check(base, 2 * RECOVERY_ROUNDS * 4)
    await _resend_read(tb, short_idle, base + RESEND_READ_OFFSET, stall=False)
    tb.host.idle_cycles = saved_idle
    refused = await _resend_read(tb, short_idle, base + STALLED_RESEND_OFFSET, stall=True)
    tb.host.idle_cycles = saved_idle
    tb.checker.require_stimulus(
        created=refused,
        reason="the resend with the bus stalled met no sbbusyerror, so no refused resend ran",
    )
    await _side_effect_read(tb, base + SIDE_EFFECT_OFFSET)
    tb.finish()


async def _recovery_rounds(
    tb: Tdr2axiTb, short_idle: int, base: int
) -> tuple[int, list[tuple[int, int, int]]]:
    """The sbaddress0 read polled early, then each round's read and write behind a pending
    read; returns the busy captures and each resent write's address, data and retries."""
    # The host raises its count from short_idle one backoff at a time until the poll finds
    # the result
    tb.host.idle_cycles = short_idle
    need = tb.host.cfg.readondata_idle
    result = await tb.dmi(DmiOp.READ, SBADDRESS0, max_polls=busy_backoffs(short_idle, need) + 1)
    busy_captures = result.retries
    resent_writes: list[tuple[int, int, int]] = []
    for i in range(RECOVERY_ROUNDS):
        addr = base + 4 * i
        result = await _behind_pending_read(tb, short_idle, DmiOp.READ, SBADDRESS0)
        busy_captures += result.retries
        result = await _behind_pending_read(tb, short_idle, DmiOp.WRITE, SBDATA0, RETRY_PATTERN + i)
        busy_captures += result.retries
        resent_writes.append((addr, RETRY_PATTERN + i, result.retries))
    return busy_captures, resent_writes


class _FreshReads:
    """Subordinate observer: new random data at ``addr`` at each AR handshake there, so each
    bus read of it returns a value no earlier read returned; ``values`` keeps them in
    order."""

    def __init__(self, tb: Tdr2axiTb, addr: int) -> None:
        self._tb = tb
        self.addr = addr
        self.values: list[int] = []

    def __call__(self, channel: str, item: AxiAddr | AxiW) -> None:
        if channel != "ar" or not isinstance(item, AxiAddr) or item.addr != self.addr:
            return
        tb = self._tb
        tb.mem_fill(self.addr, 1 << WORD)
        while tb.ref_read(self.addr, WORD) in self.values:
            tb.mem_fill(self.addr, 1 << WORD)
        self.values.append(tb.ref_read(self.addr, WORD))


async def _resend_read(tb: Tdr2axiTb, short_idle: int, addr: int, *, stall: bool) -> bool:
    """The debugger's resend of an sbdata0 read with sbreadondata: the read runs, its poll
    comes ``short_idle`` cycles after it and reads busy, and op 3 and one resend at the
    host's Run-Test/Idle of at least N run it again. Each run returns sbdata0 and reads
    ``addr`` into it again; returns whether the resend was refused.

    A resend that reaches the DM while the first run's bus read is in flight is refused with
    sbbusyerror and never runs; sbdata0 then holds the first run's data, which the debugger
    reads after clearing sbbusyerror. With ``stall`` the subordinate holds every request
    until the DM answers busy, so the resend meets the first run's bus read.
    """
    fresh = _FreshReads(tb, addr)
    tb.sub.add_transaction_callback(fresh)
    await tb.dmi_write(SBCS, sbcs_value(WORD, readonaddr=True, readondata=True))
    await tb.sba_set_addr(addr)
    await tb.sba_idle()
    tb.expect_read(addr, WORD, check_id=RESEND_READ)
    reads_before = tb.sub.read_count(addr)
    release: Task | None = None
    if stall:
        tb.sub.stall = True
        release = cocotb.start_soon(_release_stall_on_busy_answer(tb))
    raised = tb.host.idle_cycles
    tb.host.idle_cycles = short_idle
    await tb.scan(tdr_word(DmiOp.READ, SBDATA0, 0))
    cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    tb.host.idle_cycles = raised
    tb.checker.require_stimulus(
        created=cap & 3 == DmiStatus.BUSY,
        reason=f"the sbdata0 read polled at Run-Test/Idle {short_idle} did not read busy",
    )
    await tb.scan(tdr_word(DmiOp.CLEAR, 0, 0))
    result = await tb.dmi(DmiOp.READ, SBDATA0, max_polls=RESEND_POLLS)
    if release is not None:
        if not release.done():
            release.kill()
        tb.sub.stall = False
    sbcs = await tb.sba_poll()
    data = result.rdata
    refused = bool((sbcs >> SBCS_SBBUSYERROR) & 1)
    if refused:
        tb.log.info("the resent sbdata0 read met the first run's bus read and was refused")
        await tb.dmi_write(SBCS, SBCS_CLEAR_ERRORS | sbcs_value(WORD))
        data = await tb.dmi_read(SBDATA0)
    runs = tb.sub.read_count(addr) - reads_before
    tb.log.info(
        "resent sbdata0 read, bus %s: %d op 3 retries, %d bus reads returning %s",
        "stalled" if stall else "free",
        result.retries,
        runs,
        [f"0x{v:08x}" for v in fresh.values],
    )
    allowed = RESEND_RUNS[:1] if refused else RESEND_RUNS
    tb.checker.expect_true(
        RESEND_READ,
        passed=runs in allowed,
        detail=f"a resent sbdata0 read with sbreadondata read 0x{addr:x} {runs} times,"
        f" {'refused' if refused else 'run'}: expected {list(allowed)}",
    )
    for _ in range(runs):
        tb.expect_read(addr, WORD, check_id=RESEND_READ)
    tb.checker.expect_equal(
        RESEND_READ,
        data,
        fresh.values[1],
        f"resent read data, against the bus reads' {[hex(v) for v in fresh.values]}",
    )
    return refused


async def _release_stall_on_busy_answer(tb: Tdr2axiTb) -> None:
    """Let the subordinate take requests again from the DM's first busy DMI answer."""
    while not (
        tb.observe("dmi_resp_valid")
        and tb.observe("dmi_resp_ready")
        and tb.observe("dmi_resp_resp") == DMI_RESP_BUSY
    ):
        await tb.clk_cycles(1)
    tb.sub.stall = False


async def _side_effect_read(tb: Tdr2axiTb, addr: int) -> None:
    """An sbdata0 read with sbreadondata that reads the side-effecting location ``addr``,
    at the host's Run-Test/Idle of at least N: the host checks that count before the read,
    the read runs once, so the location is read once, and sbdata0 then holds its value."""
    tb.arm_side_effect(addr)
    await tb.dmi_write(SBCS, sbcs_value(WORD, readondata=True))
    await tb.sba_set_addr(addr)
    host_checks = tb.host.readondata_idle_checks
    await tb.dmi_read(SBDATA0)
    await tb.sba_idle()
    tb.disarm_side_effects()
    tb.checker.require_stimulus(
        created=tb.host.readondata_idle_checks > host_checks,
        reason="the host checked no Run-Test/Idle count before the side-effecting read",
    )
    tb.expect_read(addr, WORD, check_id=READONDATA_IDLE)
    tb.checker.expect_equal(
        READONDATA_IDLE, tb.sub.read_count(addr), 1, "reads of the side-effecting location"
    )
    await tb.dmi_write(SBCS, sbcs_value(WORD))
    tb.checker.expect_equal(
        "CHK-SBA-SINGLE-READ",
        await tb.dmi_read(SBDATA0),
        tb.ref_read(addr, WORD),
        "sbdata0 after the side-effecting read",
    )


async def _behind_pending_read(
    tb: Tdr2axiTb, short_idle: int, op: DmiOp, addr: int, wdata: int = 0
) -> DmiResult:
    """A raw sbaddress0 read with Run-Test/Idle ``short_idle``, then the op flow at the host's
    raised count: the op scan's capture lands while that read is pending and reads busy."""
    raised = tb.host.idle_cycles
    tb.host.idle_cycles = short_idle
    await tb.scan(tdr_word(DmiOp.READ, SBADDRESS0, 0))
    tb.host.idle_cycles = raised
    return await tb.dmi(op, addr, wdata)


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_ops_test(dut: SimHandleBase) -> None:
    """A seeded stream of op 0, 1, 2 and 3 scans at seeded DMI addresses, mapped and
    unmapped, with seeded data fields, 0 to 7 bypass bits and Pause-DR or none, each updated
    in IDLE with a clear status and each with Run-Test/Idle from N to 2N.

    The scoreboard compares each capture with the models' image: the last op's address and
    its data, a read's answer as the SBA model predicts it, a write's data, or a NOP's or
    op 3's own data field, with status 0 (CHK-TDR-CAPTURE-IMAGE). Op 1 and op 2 each send
    one DMI request, whose op, address and data the scoreboard compares with the TDR
    model's, and op 0 sends none (CHK-TDR-OP-DECODE); the scoreboard fails any request op 3
    sends (CHK-TDR-OP3-CLEAR). Every OPS_BUSY_EVERY scans a read polled SYNC_STAGES - 2 or
    fewer Run-Test/Idle cycles after its update reads busy, and the scoreboard checks that
    op 3 clears the sticky status (CHK-TDR-OP3-CLEAR). The host checks its own strobes in
    every scan (CHK-TDRHOST-CAPTURE-DR, CHK-TDRHOST-UPDATE-DR) and tb_top the update word
    around every update edge (CHK-ENV-TDR-UPDATE-STABLE). Writes go only to registers whose
    write starts no bus access and leaves dmcontrol and sbcs as they are.
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_ops_test")
    rng = tb.rng
    need = tb.host.cfg.readondata_idle
    writes = _ops_write_targets(tb)
    ops = [op for op in DmiOp for _ in range(OPS_PER_KIND)]
    rng.shuffle(ops)
    unmapped = 0
    for i, op in enumerate(ops):
        addr = _ops_addr(rng, op, writes)
        unmapped += addr not in MAPPED_READS
        data = _ops_data(tb, addr) if op is DmiOp.WRITE else rng.getrandbits(32)
        _draw_tap_path(tb)
        idle = rng.randint(need, 2 * need)
        tb.log.info(
            "scan %d: %s 0x%02x data 0x%08x, %d bypass bits, Pause-DR %d, Run-Test/Idle %d",
            i,
            op.name,
            addr,
            data,
            tb.host.pad_bits,
            tb.host.pause_cycles,
            idle,
        )
        await _op_scan(tb, op, addr, data, idle=idle, what=f"scan {i}")
        if i % OPS_BUSY_EVERY == OPS_BUSY_EVERY - 1:
            await _busy_then_clear(tb, f"scan {i} busy episode")
    # The capture of the last op
    await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    tb.log.info(
        "%d op scans, %d per op, %d at unmapped addresses, %d busy statuses cleared by op 3",
        len(ops),
        OPS_PER_KIND,
        unmapped,
        len(ops) // OPS_BUSY_EVERY,
    )
    tb.finish()


def _ops_write_targets(tb: Tdr2axiTb) -> tuple[int, ...]:
    mapped = [SBADDRESS0, DATA0, PROGBUF0, ABSTRACTCS, COMMAND]
    if tb.build.addr_width > 32:
        mapped.append(SBADDRESS1)
    return tuple(mapped)


def _ops_addr(rng: random.Random, op: DmiOp, writes: tuple[int, ...]) -> int:
    if rng.random() < UNMAPPED_SHARE:
        return rng.randint(*rng.choice((UNMAPPED_LOW, UNMAPPED_HIGH)))
    if op is DmiOp.WRITE:
        return rng.choice(writes)
    if op is DmiOp.READ:
        return rng.choice(MAPPED_READS)
    return rng.randint(UNMAPPED_LOW[0], UNMAPPED_HIGH[1])


def _ops_data(tb: Tdr2axiTb, addr: int) -> int:
    """Write data; an sbaddress1 write sets no bit at or above AXI_ADDR_WIDTH."""
    data = tb.rng.getrandbits(32)
    if addr == SBADDRESS1:
        data &= (1 << (tb.build.addr_width - 32)) - 1
    return data


def _draw_tap_path(tb: Tdr2axiTb) -> None:
    tb.host.pad_bits = tb.rng.randint(0, PAD_BITS_MAX)
    tb.host.pause_cycles = tb.rng.choice((0, tb.rng.randint(1, PAUSE_CYCLES_MAX)))


async def _op_scan(tb: Tdr2axiTb, op: DmiOp, addr: int, data: int, *, idle: int, what: str) -> None:
    """Scan ``op`` with Run-Test/Idle ``idle`` of at least N, and check the DMI requests op 0,
    1 or 2 sends and the DM answers to a read."""
    requests, answers = tb.predictor.dmi_requests, len(tb.predictor.dm_answers)
    await tb.scan(tdr_word(op, addr, data), idle=idle)
    if op is DmiOp.CLEAR:
        return
    tb.checker.expect_equal(
        OP_DECODE,
        tb.predictor.dmi_requests - requests,
        int(op in (DmiOp.READ, DmiOp.WRITE)),
        f"{what}: DMI requests sent by {op.name} 0x{addr:02x}",
    )
    if op is DmiOp.READ:
        new = tb.predictor.dm_answers[answers:]
        tb.checker.expect_equal(OP_DECODE, len(new), 1, f"{what}: DM answers to READ 0x{addr:02x}")


async def _busy_then_clear(tb: Tdr2axiTb, what: str) -> None:
    """A read polled SYNC_STAGES - 2 or fewer Run-Test/Idle cycles after its update reads
    busy; a poll with Run-Test/Idle N lets it finish, and op 3 clears the sticky status."""
    rng = tb.rng
    idle = rng.randint(0, tb.build.sync_stages - 2)
    addr = rng.choice(MAPPED_READS)
    await tb.scan(tdr_word(DmiOp.READ, addr, rng.getrandbits(32)), idle=idle)
    cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=tb.host.cfg.readondata_idle)
    tb.checker.require_stimulus(
        created=cap & 3 == DmiStatus.BUSY,
        reason=f"{what}: the capture {idle + CAPTURE_EDGE} TCK edges after a read's update "
        f"read status {cap & 3}, not busy",
    )
    await tb.dmi_clear()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_strobes_test(dut: SimHandleBase) -> None:
    """Scans along every TAP path the host makes, 0 or 7 bypass bits and Pause-DR or none,
    then seeded paths, each an op and a NOP poll; every other op holds the update strobe for
    two TCK rising edges, cycling through op 1, op 2, op 0 and op 3.

    Each edge with the update strobe high is one update (CHK-TDR-UPDATE-EDGE): a held op 1
    or 2 sends one DMI request and its second edge, with the op pending, sets busy, which
    the next capture reads; a held op 0 or op 3 sends none and the next capture reads its
    own fields with status 0. The scoreboard pairs each request and that capture with the
    TDR model (CHK-TDR-UPDATE-EDGE). The host compares o_dmi_tdr at every capture edge with
    the model (CHK-TDR-CAPTURE-EDGE) and checks its own strobes in every scan
    (CHK-TDRHOST-CAPTURE-DR, CHK-TDRHOST-UPDATE-DR), and tb_top the update word around
    every update edge (CHK-ENV-TDR-UPDATE-STABLE).
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_strobes_test")
    rng = tb.rng
    need = tb.host.cfg.readondata_idle
    paths = [(pad, pause) for pad in (0, PAD_BITS_MAX) for pause in (False, True)]
    rng.shuffle(paths)
    held = Counter[DmiOp]()
    for i in range(STROBE_SCANS):
        if i < len(paths):
            pad, pause = paths[i]
        else:
            pad, pause = rng.randint(0, PAD_BITS_MAX), rng.random() < 0.5
        tb.host.pad_bits = pad
        tb.host.pause_cycles = rng.randint(1, PAUSE_CYCLES_MAX) if pause else 0
        width = 1 + i % 2
        if width == 2:
            op = HELD_OPS[(i // 2) % len(HELD_OPS)]
        else:
            op = rng.choice((DmiOp.NOP, DmiOp.READ, DmiOp.WRITE))
        addr = rng.choice((SBADDRESS0, DATA0)) if op is DmiOp.WRITE else rng.choice(MAPPED_READS)
        data = rng.getrandbits(32)
        what = f"{op.name} 0x{addr:02x} with the update strobe over {width} edges"
        tb.log.info(
            "scan %d: %s, data 0x%08x, %d bypass bits, Pause-DR %d",
            i,
            what,
            data,
            pad,
            tb.host.pause_cycles,
        )
        requests = tb.predictor.dmi_requests
        await tb.scan(tdr_word(op, addr, data), idle=need, update_edges=width)
        sent = tb.predictor.dmi_requests - requests
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=need)
        if width == 1:
            continue
        held[op] += 1
        pending = op in (DmiOp.READ, DmiOp.WRITE)
        tb.checker.expect_equal(UPDATE_EDGE, sent, int(pending), f"{what}: DMI requests")
        if pending:
            tb.checker.expect_equal(UPDATE_EDGE, cap & 3, DmiStatus.BUSY, f"{what}: capture")
            await tb.dmi_clear()
        else:
            tb.checker.expect_equal(
                UPDATE_EDGE, cap, tdr_word(DmiStatus.OK, addr, data), f"{what}: capture"
            )
    tb.log.info(
        "%d op scans, %s with the update strobe over two edges",
        STROBE_SCANS,
        ", ".join(f"{count} {op.name}" for op, count in sorted(held.items())),
    )
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_busy_capture_test(dut: SimHandleBase) -> None:
    """A capture while a read is pending reads busy on that capture edge, and the status
    stays busy until op 3. Each round reads sbaddress0 and polls it early: with i_clk
    stopped, 3 to 2N + 3 TCK edges after the read's update, so the read cannot finish, or
    with i_clk running, 3 to SYNC_STAGES + 3 edges after it, no later than the shortest
    round trip at any clock ratio.

    That capture and one to three NOP captures after it read busy, the later ones after the
    read has finished, and the scoreboard compares each with the TDR model
    (CHK-TDR-BUSY-CAPTURE); the scoreboard checks that updates while busy send nothing and
    start no bus access (CHK-TDR-STICKY-IGNORE); after op 3, the resent read returns sbaddress0
    (CHK-TDR-BUSY-CAPTURE). No capture reads status 1 or 2 (CHK-TDR-NEVER-FAILED). Both
    variants run, in a seeded order.
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_busy_capture_test")
    rng = tb.rng
    need = tb.host.cfg.readondata_idle
    modes = [True, False] + [rng.random() < 0.5 for _ in range(BUSY_CAPTURE_ROUNDS - 2)]
    rng.shuffle(modes)
    for i, clk_stopped in enumerate(modes):
        value = rng.getrandbits(32)
        await tb.dmi_write(SBADDRESS0, value)
        if clk_stopped:
            tb.stop_clk()
            await tb.wait_clk_periods(rng.randint(1, CLK_STOP_PERIODS))
            idle = rng.randint(0, 2 * need)
        else:
            idle = rng.randint(0, tb.build.sync_stages)
        what = (
            f"round {i}, i_clk {'stopped' if clk_stopped else 'running'}: capture "
            f"{idle + CAPTURE_EDGE} TCK edges after a read's update"
        )
        tb.log.info("%s, sbaddress0 0x%08x", what, value)
        await tb.scan(tdr_word(DmiOp.READ, SBADDRESS0, ~value & WORD_MASK), idle=idle)
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=need)
        tb.checker.expect_equal(BUSY_CAPTURE, cap & 3, DmiStatus.BUSY, what)
        if clk_stopped:
            await tb.wait_clk_periods(rng.randint(0, CLK_STOP_PERIODS))
            tb.start_clk()
            await tb.wait_clk_periods(CLK_RESUME_PERIODS)
        for poll in range(rng.randint(1, STICKY_POLLS)):
            cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=need)
            tb.checker.expect_equal(
                BUSY_CAPTURE, cap & 3, DmiStatus.BUSY, f"{what}: NOP capture {poll + 1} after it"
            )
        await tb.scan_sticky(_sticky_words(rng), f"{what}: sticky busy")
        await tb.dmi_clear()
        tb.checker.expect_equal(
            BUSY_CAPTURE, await tb.dmi_read(SBADDRESS0), value, f"{what}: resend after op 3"
        )
    tb.finish()


def _sticky_words(rng: random.Random) -> list[int]:
    """Updates of op 0, 1 and 2 in a seeded order, each to sbaddress0, sbdata0 or sbcs."""
    return [
        tdr_word(op, rng.choice((SBADDRESS0, SBDATA0, SBCS)), rng.getrandbits(32))
        for op in rng.sample((DmiOp.NOP, DmiOp.READ, DmiOp.WRITE), k=3)
    ]


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_result_latency_test(dut: SimHandleBase) -> None:
    """Reads of sbaddress0, each polled with its capture a seeded count of TCK edges after
    the DM's response handshake, from two before the window of T-TDR2AXI-resp-to-tdr (or
    -ss4 at SYNC_STAGES 4) to two after it, every count twice.

    After the read's update, Run-Test/Idle runs one edge at a time until the timing monitor
    has seen the response handshake, then as many more as place the poll's capture at the
    seeded count; a count the handshake's own edge has passed lands one edge later. The
    monitor counts the edges from the handshake to the edge that returns the bridge to
    IDLE: the count lies in the window the TDR model predicts, SYNC_STAGES + 2 to
    SYNC_STAGES + 3, and o_dmi_tdr then holds sbaddress0's address and the DM's response
    data; a capture at or before that edge reads busy, and one after it the response with
    status 0. The scoreboard grades the arrival and the poll's capture against the models at
    SYNC_STAGES 3 (CHK-TDR-RESULT-LATENCY), and the test at 4 (CHK-TDR-RESULT-LATENCY-SS4).
    Captures land both before and after the window.
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_result_latency_test")
    rng = tb.rng
    ss = tb.build.sync_stages
    check_id = RESULT_LATENCY_IDS[ss]
    window = tb.predictor.tdr.predict_result_window()
    lo, hi = window.earliest, window.latest
    tb.log.info(
        "%s: %d to %d TCK edges after the response handshake, %s at SYNC_STAGES %d",
        check_id,
        lo,
        hi,
        RESULT_LATENCY_TIMING[ss],
        ss,
    )
    value = rng.getrandbits(32)
    await tb.dmi_write(SBADDRESS0, value)
    graded_by_test = check_id not in SCOREBOARD_CHECKS
    if graded_by_test:
        tb.timing.watch_results()
    else:
        tb.watch_results(check_id)
    targets = list(range(lo - LATENCY_MARGIN, hi + LATENCY_MARGIN + 1)) * LATENCY_ROUNDS
    rng.shuffle(targets)
    offsets = Counter[int]()
    for target in targets:
        offset = await _latency_read(
            tb, check_id, (lo, hi), value, target, graded_by_test=graded_by_test
        )
        offsets[offset] += 1
    if graded_by_test:
        tb.timing.stop()
    else:
        tb.stop_results()
    tb.log.info("poll captures by TCK edges after the response handshake: %s", dict(offsets))
    tb.checker.require_stimulus(
        created=min(offsets) < lo and max(offsets) > hi,
        reason=f"poll captures at {sorted(offsets)} edges after the handshake do not reach "
        f"both sides of {lo} to {hi}",
    )
    tb.finish()


async def _latency_read(
    tb: Tdr2axiTb,
    check_id: str,
    window: tuple[int, int],
    value: int,
    target: int,
    *,
    graded_by_test: bool,
) -> int:
    """One read of sbaddress0 polled ``target`` TCK edges after the DM's response handshake,
    or the first edge after that the handshake allows; with ``graded_by_test``, grades the
    result's arrival and the poll's capture. Returns the capture's edges after the
    handshake."""
    mon = tb.timing
    need = tb.host.cfg.readondata_idle
    arrivals, captures, responses = len(mon.arrivals), len(mon.captures), len(mon.responses)
    await tb.scan(tdr_word(DmiOp.READ, SBADDRESS0, ~value & WORD_MASK), idle=0)
    for _ in range(2 * need):
        if len(mon.responses) > responses:
            break
        await tb.tck_cycles(1)
    if len(mon.responses) == responses:
        tb.checker.expect_true(
            check_id,
            passed=False,
            detail=f"no DM response within {2 * need} TCK edges of a read's update",
        )
    handshake = mon.responses[responses]
    await tb.tck_cycles(max(0, handshake.edge + target - CAPTURE_EDGE - mon.edge_count))
    cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=need)
    new = mon.arrivals[arrivals:]
    poll = mon.captures[captures + 1]
    if graded_by_test:
        _grade_latency(
            tb,
            check_id,
            window=window,
            value=value,
            handshake=handshake,
            new=new,
            poll=poll,
            cap=cap,
        )
    offset = poll.edge - handshake.edge
    tb.log.info(
        "poll at %d edges after the handshake (target %d), result at %s, capture 0x%011x",
        offset,
        target,
        new[0].edges if new else None,
        cap,
    )
    if cap & 3 == DmiStatus.BUSY:
        await tb.dmi_clear()
    return offset


def _grade_latency(
    tb: Tdr2axiTb,
    check_id: str,
    *,
    window: tuple[int, int],
    value: int,
    handshake: ResponseHandshake,
    new: list[ResultArrival],
    poll: CaptureEdge,
    cap: int,
) -> None:
    """The result's arrival from one read of sbaddress0 against ``window``, and the poll's
    capture: busy at or before the arrival edge, the result with status 0 after it."""
    lo, hi = window
    tb.checker.expect_equal(check_id, len(new), 1, "results back from one read")
    arrival = new[0]
    edges = arrival.edges
    word = arrival.word
    tb.checker.expect_true(
        check_id,
        passed=arrival.handshake_edge == handshake.edge
        and edges is not None
        and lo <= edges <= hi
        and arrival.read
        and word >> TDR_ADDR_LSB == SBADDRESS0
        and (word >> TDR_DATA_LSB) & WORD_MASK == value
        and arrival.resp_data == value
        and arrival.resp == 0,
        detail=f"read result in o_dmi_tdr 0x{word:011x} {edges} TCK edges after the response "
        f"handshake at edge {arrival.handshake_edge} (data {arrival.resp_data}, response "
        f"{arrival.resp}): expected {lo} to {hi} edges, sbaddress0 0x{value:08x}",
    )
    offset = poll.edge - handshake.edge
    early = poll.edge <= arrival.arrival_edge
    expected = "busy" if early else f"0x{tdr_word(DmiStatus.OK, SBADDRESS0, value):011x}"
    tb.checker.expect_true(
        check_id,
        passed=cap == poll.word
        and (
            cap & 3 == DmiStatus.BUSY if early else cap == tdr_word(DmiStatus.OK, SBADDRESS0, value)
        ),
        detail=f"poll capture 0x{cap:011x} (monitor 0x{poll.word:011x}) {offset} TCK edges after "
        f"the response handshake, result {edges} edges after it: expected {expected}",
    )


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_tdr_tck_stop_test(dut: SimHandleBase) -> None:
    """TCK stopped for a seeded 1 to TCK_STOP_PERIODS i_clk periods, a seeded 0 to N
    Run-Test/Idle edges after the update of an sbdata0 write or an sbdata0 read with
    sbreadondata, which alternate: with the request in the bridge, taken by the DM,
    answered, or back in o_dmi_tdr. The first round stops TCK one edge after the update, the
    request launched, for a seeded TCK_STOP_LONG to TCK_STOP_PERIODS i_clk periods, so where
    i_clk is the slower clock the DM answers in the stop and its answer waits there more than
    100 i_clk periods. In that round and in about half the others the subordinate holds every
    request from before the op's update to a seeded i_clk edge of the stop, so the bus access
    ends while TCK is stopped even at a fast i_clk, and the result, which needs TCK edges to
    cross back, follows only once TCK resumes; at a slow i_clk the DM also answers in the
    stop.

    At every i_clk rising edge of the stop, the bridge's state, status, address and data and
    o_dmi_tdr hold their values from the start of the stop. The result reaches o_dmi_tdr only
    on TCK edges, within the TDR model's window of SYNC_STAGES + 2 to SYNC_STAGES + 3 edges
    after the DM's response handshake (T-TDR2AXI-resp-to-tdr, -ss4 at SYNC_STAGES 4), with
    sbdata0's address and the write's data or the DM's read data as the SBA model predicts
    it, and the NOP capture after TCK resumes holds it with status 0. The bus access is the
    op's, once. The scoreboard grades each of these against the models
    (CHK-TDR-TCK-STOP-HOLD at SYNC_STAGES 3, CHK-TDR-TCK-STOP-HOLD-SS4 at 4).
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_tdr_tck_stop_test")
    rng = tb.rng
    need = tb.host.cfg.readondata_idle
    base = tb.region + TCK_STOP_OFFSET
    tb.mem_fill(base, 4)
    await tb.sba_set_addr(base)
    tb.clear_monitor()
    tb.watch_results(tb.tck_stop_check)
    first_write = rng.random() < 0.5
    phases = Counter[str]()
    held_back = 0
    for i in range(TCK_STOP_ROUNDS):
        lead = 1 if i == 0 else rng.randint(0, need)
        stop = rng.randint(TCK_STOP_LONG if i == 0 else 1, TCK_STOP_PERIODS)
        stalled = i == 0 or rng.random() < 0.5
        phase, round_held_back = await _tck_stop_round(
            tb,
            base,
            write=(i % 2 == 0) == first_write,
            lead=lead,
            stop=stop,
            release=rng.randint(1, stop) if stalled else None,
        )
        phases[phase] += 1
        held_back += round_held_back
    tb.stop_results()
    tb.log.info(
        "TCK stops by where the op stood: %s; %d with the bus access ending in the stop and "
        "the result back after it",
        dict(phases),
        held_back,
    )
    tb.checker.require_stimulus(
        created=held_back > 0,
        reason="no TCK stop held the result back while the bus access ended",
    )
    tb.finish()


async def _tck_stop_round(
    tb: Tdr2axiTb,
    base: int,
    *,
    write: bool,
    lead: int,
    stop: int,
    release: int | None,
) -> tuple[str, bool]:
    """One sbdata0 op with TCK stopped ``lead`` Run-Test/Idle edges after its update for
    ``stop`` i_clk periods; with ``release``, the subordinate holds every request from
    before the op until i_clk rising edge ``release`` of the stop. Returns where the op stood
    when TCK stopped, and whether the bus access ended during the stop with the result
    reaching o_dmi_tdr only after it."""
    rng = tb.rng
    need = tb.host.cfg.readondata_idle
    mon = tb.timing
    await tb.dmi_write(SBCS, sbcs_value(WORD, readondata=not write))
    data = rng.getrandbits(32)
    op = DmiOp.WRITE if write else DmiOp.READ
    requests = tb.predictor.dmi_requests
    arrivals, responses = len(mon.arrivals), len(mon.responses)
    tb.sub.stall = release is not None
    await tb.scan(tdr_word(op, SBDATA0, data), idle=lead)
    stop_edge = mon.edge_count
    if len(mon.arrivals) > arrivals:
        phase = "back in o_dmi_tdr"
    elif len(mon.responses) > responses:
        phase = "answered"
    elif tb.predictor.dmi_requests > requests:
        phase = "taken by the DM"
    else:
        phase = "in the bridge"
    responses = len(mon.responses)
    sbbusy_fell = await _hold_while_tck_stopped(tb, stop, release)
    tb.sub.stall = False
    answered = len(mon.responses) > responses
    what = (
        f"{op.name} sbdata0, TCK stopped {lead} edges after the update for {stop} i_clk "
        f"periods with the op {phase}, bus stalled to i_clk edge {release} of the stop"
    )
    await tb.tck_cycles(rng.randint(need, 2 * need))
    # The scoreboard grades this capture, the result's arrival and the bus access
    await tb.scan(tdr_word(DmiOp.NOP, 0, 0), idle=need)
    await tb.sba_idle()
    tb.clear_monitor()
    if write:
        tb.ref_write(base, WORD, data)
    new = mon.arrivals[arrivals:]
    arrival_edge = new[0].arrival_edge if new else None
    held_back = sbbusy_fell and arrival_edge is not None and arrival_edge > stop_edge
    tb.log.info(
        "%s: result %s edges after the handshake, at edge %s; in the stop the DM answered %s "
        "and sbbusy fell %s",
        what,
        new[0].edges if new else None,
        arrival_edge,
        answered,
        sbbusy_fell,
    )
    return phase, held_back


async def _hold_while_tck_stopped(tb: Tdr2axiTb, periods: int, release: int | None) -> bool:
    """Hand the scoreboard the bridge and o_dmi_tdr once the host's last strobe and TCK
    writes have settled and at each of ``periods`` i_clk rising edges while TCK is stopped,
    and end the subordinate's stall at edge ``release``; returns whether sbbusy fell during
    the stop."""
    await ReadOnly()
    tb.begin_tck_stop()
    sbbusy_seen = bool(tb.observe("sbbusy"))
    sbbusy_fell = False
    for edge in range(1, periods + 1):
        await tb.clk_cycles(1)
        if edge == release:
            tb.sub.stall = False
        tb.sample_tck_stop(edge)
        sbbusy = bool(tb.observe("sbbusy"))
        sbbusy_fell |= sbbusy_seen and not sbbusy
        sbbusy_seen |= sbbusy
    tb.end_tck_stop()
    return sbbusy_fell
