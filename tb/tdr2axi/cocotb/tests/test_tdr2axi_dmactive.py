# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi dmactive: dmcontrol.dmactive cleared and set, idle and with bus traffic.

Each test runs seeded loops on the bench of ``Tdr2axiTb``; sizes, addresses, data, the
register order and the idle time while dmactive = 0 come from the seed. Atoms
S-TDR2AXI-0056, S-TDR2AXI-0064 and X-RISCV_DEBUG-dmactive, with the clock-reset entry
RST-DMACTIVE.

* ``tdr2axi_dmactive_clear_test``: op 3 with the bridge's sticky busy set clears it and
  leaves every DM register value it was given (CHK-DM-OP3-KEEPS-DM); while dmactive = 0,
  sbaddress0, sbaddress1, sbdata0, sbdata1 and abstractcs.cmderr read their reset values
  whatever was written before or meanwhile, and after dmactive = 1 sbcs and those
  registers read their reset values and accesses work (CHK-DM-DMACTIVE-CLEAR). The
  scoreboard grades both checks on every read as well.
* ``tdr2axi_dmactive_reset_test``: dmactive cleared and set with no other reset and no
  access in flight issues no AR or AW, leaves the subordinate memory unchanged, every
  capture holds the status OK of the operation it follows, dmcontrol reads dmactive = 0
  then 1, and sbcs reads its reset value (CHK-DM-DMACTIVE-ALONE); the reads while cleared
  grade CHK-DM-DMACTIVE-CLEAR, one seeded loop accesses no SBA register while dmactive =
  0, and every transaction is checked (CHK-AXISUB-DRAIN).
* ``tdr2axi_dmactive_no_bus_test``: while dmactive = 0, a write of sbcs with sbreadonaddr
  and sbreadondata, of sbaddress0 and of sbdata0 raises no ARVALID or AWVALID
  (CHK-DM-DMACTIVE-NO-BUS), sampled at every i_clk edge from the write until
  ``SETTLE_CYCLES`` after its result, with every READY low. A VALID fails the write at its
  edge and ends the test before the access reaches a handshake. The same watch must see
  VALID with dmactive set.
* ``tdr2axi_dmactive_inflight_test``: a read stalled on AR, or a write stalled on AW and W,
  when dmactive falls keeps its payload until the handshake (CHK-AXISUB-PAYLOAD-STABLE and
  CHK-DM-DMACTIVE-INFLIGHT in ``AxiSub``) and completes once with the address, size, data
  and strobes it started with (CHK-DM-DMACTIVE-INFLIGHT here). READY is released with
  dmactive = 0 and dmactive set once the DM is idle: ``SbaRefModel`` refuses dmactive set
  while an access from before the fall is in flight. A payload change with dmactive = 0
  fails CHK-DM-DMACTIVE-INFLIGHT before the release, so the test ends before the changed
  access completes.

The last two hold DV's reading of S-TDR2AXI-0056: an access in flight completes with the
payload it started with, and no bus access starts while dmactive = 0.

``NEGATIVE_KNOB`` names one fault per run and defaults off: ``skip`` leaves out each
test's dmactive step, so its required checks log no pass (CHK-NONVAC); ``memory`` changes
one subordinate byte while dmactive = 0, as a write no AXI handshake shows would
(CHK-DM-DMACTIVE-ALONE); ``drain`` leaves the reset test's last read unchecked in the
monitor (CHK-AXISUB-DRAIN); ``op3`` scans a NOP in place of each op 3 of the clear test,
so the sticky busy stays set (CHK-DM-OP3-KEEPS-DM).
"""

from __future__ import annotations

import os

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import Event, First
from cocotb.utils import get_sim_time

from env.sba_ref_model import (
    ABSTRACTCS_CMDERR_LSB,
    CMDERR_FIELD,
    CMDERR_NOT_SUPPORTED,
    DMCONTROL_DMACTIVE,
)
from env.tdr_host import (
    ABSTRACTCS,
    COMMAND,
    DMCONTROL,
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBDATA0,
    SBDATA1,
    DmiOp,
    DmiStatus,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_base_test import (
    SBCS_SBACCESS_LSB,
    SBCS_SBAUTOINCREMENT,
    SIZE_64,
    TIMEOUT_MS,
    WORD_MASK,
    Tdr2axiTb,
)

NEGATIVE_KNOB = "TDR2AXI_DMACTIVE_NEGATIVE"
FAULTS = frozenset({"skip", "memory", "drain", "op3"})
NEGATIVE = os.environ.get(NEGATIVE_KNOB, "")

CLEAR = "CHK-DM-DMACTIVE-CLEAR"
OP3_KEEPS = "CHK-DM-OP3-KEEPS-DM"
ALONE = "CHK-DM-DMACTIVE-ALONE"
NO_BUS = "CHK-DM-DMACTIVE-NO-BUS"
INFLIGHT = "CHK-DM-DMACTIVE-INFLIGHT"
SINGLE_READ = "CHK-SBA-SINGLE-READ"
SINGLE_WRITE = "CHK-SBA-SINGLE-WRITE"

# Access Register command for x0, which tdr2axi answers with cmderr = 2
COMMAND_ACCESS_REG = 0x0022_1000

CLEAR_OFFSET = 0x8000
RESET_OFFSET = 0x8800
NO_BUS_OFFSET = 0x9000
INFLIGHT_OFFSET = 0x9800
REGION_WORDS = 4

CLEAR_LOOPS = 2
RESET_LOOPS = 3
NO_BUS_LOOPS = 2
INFLIGHT_LOOPS = 4
OP3_REPEAT_MAX = 2
# Nominal i_clk periods of the stop before the scans that raise the sticky busy, and of
# the run after it in which the taken read completes
CLK_STOP_PERIODS = 4
CLK_RESUME_PERIODS = 50
# i_clk cycles spent with dmactive = 0 and no operation, drawn per loop
INACTIVE_IDLE_MAX = 40
# i_clk edges after a write's result in which the DM would have raised VALID: dm_sba
# leaves Idle on the edge after the DM takes the write and requests from the next
SETTLE_CYCLES = 8
# i_clk cycles the stalled request is held before and after dmactive falls
STALL_BEFORE_MAX = 6
STALL_AFTER_MIN = 2
STALL_AFTER_MAX = 12
VALID_WAIT_CYCLES = 64
IDLE_WAIT_CYCLES = 256


def _fault(tb: Tdr2axiTb, name: str, what: str) -> bool:
    """Whether this run arms fault ``name`` of ``NEGATIVE_KNOB``; an armed fault logs a
    warning naming ``what`` it does and the time."""
    if NEGATIVE and NEGATIVE not in FAULTS:
        raise ValueError(f"{NEGATIVE_KNOB}={NEGATIVE}: use one of {sorted(FAULTS)}")
    if NEGATIVE != name:
        return False
    tb.log.warning("%s=%s at %s ns: %s", NEGATIVE_KNOB, name, get_sim_time("ns"), what)
    return True


def _random_addr(tb: Tdr2axiTb, base: int, size: int) -> int:
    """An address in the test region aligned to an access of 2**size bytes."""
    return base + tb.rng.randrange(0, REGION_WORDS * tb.nbytes, 1 << size)


def _sbcs_after_write(tb: Tdr2axiTb, size: int, *, autoinc: bool) -> int:
    """sbcs with dmactive = 1 after an sbcs write of ``size`` and ``autoinc``, idle."""
    keep = tb.sbcs_reset & ~((7 << SBCS_SBACCESS_LSB) | (1 << SBCS_SBAUTOINCREMENT))
    return keep | sbcs_value(size, autoinc=autoinc)


async def _set_dmactive(tb: Tdr2axiTb, value: int, check_id: str) -> None:
    """Write dmcontrol.dmactive; the capture after the write holds its status OK."""
    result = await tb.dmi(DmiOp.WRITE, DMCONTROL, value)
    tb.checker.expect_equal(
        check_id, result.status, DmiStatus.OK, f"status of the dmactive = {value} write"
    )


async def _expect_cleared(
    tb: Tdr2axiTb, *, sba: bool = True, when: str = "with dmactive = 0"
) -> None:
    """The registers dmactive = 0 clears read their reset values, in seeded order; with
    ``sba`` false only abstractcs.cmderr, so no SBA register is accessed. ``when`` names
    the dmactive state in each failure."""
    regs = [ABSTRACTCS]
    if sba:
        regs += [SBADDRESS0, SBDATA0, SBDATA1]
    if sba and tb.build.addr_width > 32:
        regs.append(SBADDRESS1)
    tb.rng.shuffle(regs)
    for reg in regs:
        value = await tb.dmi_read(reg)
        if reg == ABSTRACTCS:
            value = (value >> ABSTRACTCS_CMDERR_LSB) & CMDERR_FIELD
        tb.checker.expect_equal(CLEAR, value, 0, f"register 0x{reg:02x} {when}")


async def _sba_round_trip(
    tb: Tdr2axiTb, base: int, check_id: str, *, nonzero: bool = False
) -> None:
    """One write and one read back, each one AXI transaction with the expected payload.
    With ``nonzero`` the data's low 32 bits, which sbdata0 holds after the read, are
    redrawn until nonzero."""
    size = tb.rng.randint(0, tb.max_size)
    addr = _random_addr(tb, base, size)
    data = tb.rand_data(size)
    while nonzero and not data & WORD_MASK:
        data = tb.rand_data(size)
    tb.clear_monitor()
    await tb.sba_write(addr, size, data)
    tb.expect_write(addr, size, data, check_id=check_id)
    tb.ref_write(addr, size, data)
    got = await tb.sba_read(addr, size)
    tb.expect_read(addr, size, check_id=check_id)
    tb.expect_no_bus(check_id, f"write and read of size {size} @0x{addr:x}")
    tb.checker.expect_equal(check_id, got, tb.ref_read(addr, size), f"read @0x{addr:x}")


# --- tdr2axi_dmactive_clear_test --------------------------------------------------------
async def _program_dm(tb: Tdr2axiTb, base: int) -> dict[int, int]:
    """Give sbcs and every register dmactive = 0 clears a value other than its reset value,
    with no access in flight, sbdata0 keeping the nonzero data of the read before; returns
    the value each reads, cmderr for abstractcs."""
    size = tb.rng.randint(0, tb.max_size)
    autoinc = bool(tb.rng.getrandbits(1))
    addr = _random_addr(tb, base, size)
    await tb.dmi_write(SBCS, sbcs_value(size, autoinc=autoinc))
    await tb.sba_set_addr(addr)
    expected = {SBCS: _sbcs_after_write(tb, size, autoinc=autoinc), SBADDRESS0: addr & WORD_MASK}
    # sbreadondata is clear, so this read starts no access
    expected[SBDATA0] = await tb.dmi_read(SBDATA0)
    tb.checker.require_stimulus(
        created=expected[SBDATA0] != 0, reason="sbdata0 holds its reset value before op 3"
    )
    if tb.build.addr_width > 32:
        expected[SBADDRESS1] = addr >> 32
    if tb.max_size == SIZE_64:
        high = tb.rng.getrandbits(32) | 1
        await tb.dmi_write(SBDATA1, high)
        expected[SBDATA1] = high
    await tb.dmi_write(COMMAND, COMMAND_ACCESS_REG)
    expected[ABSTRACTCS] = CMDERR_NOT_SUPPORTED
    return expected


async def _raise_busy(tb: Tdr2axiTb) -> None:
    """Set the bridge's sticky busy: with i_clk stopped a dmcontrol read is taken and waits,
    the next read is refused, and the NOP after it captures busy; i_clk then runs until the
    taken read completes."""
    tb.stop_clk()
    await tb.wait_clk_periods(CLK_STOP_PERIODS)
    await tb.scan(tdr_word(DmiOp.READ, DMCONTROL, 0))
    await tb.scan(tdr_word(DmiOp.READ, DMCONTROL, 0))
    cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    tb.start_clk()
    await tb.wait_clk_periods(CLK_RESUME_PERIODS)
    tb.checker.require_stimulus(
        created=cap & 3 == DmiStatus.BUSY,
        reason=f"the NOP after a refused read captured status {cap & 3}, not busy",
    )


async def _expect_kept_after_op3(tb: Tdr2axiTb, expected: dict[int, int]) -> None:
    """The sticky busy raised, op 3, repeated, each clearing the status, then every
    programmed register reads its value, in seeded order."""
    await _raise_busy(tb)
    noclear = _fault(tb, "op3", "each op 3 scans as a NOP, so the sticky busy stays set")
    for _ in range(tb.rng.randint(1, OP3_REPEAT_MAX)):
        await tb.scan(tdr_word(DmiOp.NOP if noclear else DmiOp.CLEAR, 0, 0))
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        tb.checker.expect_equal(OP3_KEEPS, cap & 3, DmiStatus.OK, "status after op 3")
    regs = list(expected)
    tb.rng.shuffle(regs)
    for reg in regs:
        value = await tb.dmi_read(reg)
        if reg == ABSTRACTCS:
            value = (value >> ABSTRACTCS_CMDERR_LSB) & CMDERR_FIELD
        tb.checker.expect_equal(OP3_KEEPS, value, expected[reg], f"register 0x{reg:02x} after op 3")


async def _write_while_cleared(tb: Tdr2axiTb) -> None:
    """A seeded subset of writes that start no bus access, in seeded order, while
    dmactive = 0."""
    writes = [
        (SBADDRESS0, tb.rng.getrandbits(32) | 1),
        (SBDATA1, tb.rng.getrandbits(32) | 1),
        (ABSTRACTCS, 0),
        (COMMAND, COMMAND_ACCESS_REG),
    ]
    if tb.build.addr_width > 32:
        writes.append((SBADDRESS1, 1))
    tb.rng.shuffle(writes)
    for reg, value in writes[: tb.rng.randint(1, len(writes))]:
        await tb.dmi_write(reg, value)


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dmactive_clear_test(dut: SimHandleBase) -> None:
    """Op 3 with busy set keeps the DM registers; dmactive = 0 clears them whatever is
    written; after dmactive = 1 they and sbcs read their reset values and accesses work."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_clear_test")
    base = tb.region + CLEAR_OFFSET
    tb.mem_fill(base, REGION_WORDS * tb.nbytes)
    for loop in range(CLEAR_LOOPS):
        tb.log.info("tdr2axi_dmactive_clear_test: loop %d of %d", loop + 1, CLEAR_LOOPS)
        await _sba_round_trip(tb, base, SINGLE_WRITE, nonzero=True)
        expected = await _program_dm(tb, base)
        await _expect_kept_after_op3(tb, expected)
        if _fault(tb, "skip", "the loop leaves out dmactive = 0 and its checks"):
            continue
        await tb.dmi_write(DMCONTROL, 0)
        await _write_while_cleared(tb)
        if tb.rng.getrandbits(1):
            await tb.dmi_clear()
        await _expect_cleared(tb)
        await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)
        tb.checker.expect_equal(
            CLEAR, await tb.dmi_read(SBCS), tb.sbcs_reset, "sbcs after dmactive = 1"
        )
        await _expect_cleared(tb, when="after dmactive = 1")
        await _sba_round_trip(tb, base, CLEAR)
    tb.finish()


# --- tdr2axi_dmactive_reset_test --------------------------------------------------------
@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dmactive_reset_test(dut: SimHandleBase) -> None:
    """RST-DMACTIVE alone: dmactive cleared and set with i_clk running, no other reset and
    no access in flight changes nothing outside the DM registers."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_reset_test")
    base = tb.region + RESET_OFFSET
    span = REGION_WORDS * tb.nbytes
    tb.mem_fill(base, span)
    loops = RESET_LOOPS + 1
    # The loop at alone_loop has dmactive rise with no SBA register access or bus request
    # since it fell, the RST-DMACTIVE alone case
    alone_loop = tb.rng.randrange(loops)
    for loop in range(loops):
        alone = loop == alone_loop
        tb.log.info(
            "tdr2axi_dmactive_reset_test: loop %d of %d, SBA registers %s while dmactive = 0",
            loop + 1,
            loops,
            "untouched" if alone else "read",
        )
        await _sba_round_trip(tb, base, SINGLE_READ)
        if _fault(tb, "skip", "the loop leaves out dmactive = 0 and its checks"):
            continue
        mark = tb.bus_mark()
        await _set_dmactive(tb, 0, ALONE)
        await tb.clk_cycles(tb.rng.randint(0, INACTIVE_IDLE_MAX))
        # Drawn on every loop, so a fault run draws the stimulus of its normal run
        addr = _random_addr(tb, base, 0)
        if _fault(tb, "memory", f"subordinate byte 0x{addr:x} inverted with no handshake"):
            tb.sub.mem[addr] = tb.sub.mem.get(addr, 0) ^ 0xFF
            tb.log.warning("%s: byte 0x%x reads 0x%02x", NEGATIVE_KNOB, addr, tb.sub.mem[addr])
        dmcontrol = await tb.dmi_read(DMCONTROL)
        tb.checker.expect_equal(
            ALONE, dmcontrol & DMCONTROL_DMACTIVE, 0, "dmcontrol.dmactive cleared"
        )
        await _expect_cleared(tb, sba=not alone)
        cap = await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
        tb.checker.expect_equal(ALONE, cap & 3, DmiStatus.OK, "bridge status with dmactive = 0")
        await _set_dmactive(tb, DMCONTROL_DMACTIVE, ALONE)
        dmcontrol = await tb.dmi_read(DMCONTROL)
        tb.checker.expect_equal(
            ALONE, dmcontrol & DMCONTROL_DMACTIVE, DMCONTROL_DMACTIVE, "dmcontrol.dmactive set"
        )
        tb.checker.expect_equal(
            ALONE, await tb.dmi_read(SBCS), tb.sbcs_reset, "sbcs after dmactive = 1"
        )
        tb.expect_no_bus_since(mark, ALONE, "dmactive cleared and set")
        tb.mem_check(base, span, check_id=ALONE)
    if _fault(tb, "drain", "a last read that the test does not check"):
        await tb.sba_read(_random_addr(tb, base, 2), 2)
    tb.finish()


# --- tdr2axi_dmactive_no_bus_test -------------------------------------------------------
class _InactiveBusWatch:
    """ARVALID and AWVALID at every i_clk rising edge out of reset. A VALID with dmactive =
    0 is an event and sets ``raised`` at its edge, ahead of the response the access draws;
    VALIDs with dmactive = 1 are counted."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self._tb = tb
        self.events: list[str] = []
        self.active_valids = 0
        self.inactive_edges = 0
        self.raised = Event()

    def open_window(self) -> int:
        """Clear ``raised`` for a new window; returns the number of events recorded before
        it opens."""
        self.raised.clear()
        return len(self.events)

    async def run(self) -> None:
        """Sample until killed."""
        tb = self._tb
        while True:
            await tb.clk_cycles(1)
            if not tb.observe("rst_n"):
                continue
            valid = [ch for ch in ("ar", "aw") if tb.observe(f"{ch}_valid")]
            if tb.observe("dmactive"):
                self.active_valids += len(valid)
                continue
            self.inactive_edges += 1
            now = get_sim_time("ns")
            raised = [f"{ch.upper()}VALID at {now} ns" for ch in valid]
            self.events += raised
            if raised:
                self.raised.set()


async def _write_and_settle(tb: Tdr2axiTb, reg: int, value: int) -> None:
    """Write ``reg`` and wait ``SETTLE_CYCLES`` i_clk edges after the result."""
    await tb.dmi_write(reg, value)
    await tb.clk_cycles(SETTLE_CYCLES)


async def _write_no_bus(
    tb: Tdr2axiTb, watch: _InactiveBusWatch, *, name: str, reg: int, value: int
) -> None:
    """One write with dmactive = 0, graded on the VALIDs the watch sees from before its op
    scan until ``SETTLE_CYCLES`` i_clk edges after its result; the first VALID fails it at
    its edge."""
    what = f"{name} write of 0x{value:08x} while dmactive = 0"
    seen, edges = watch.open_window(), watch.inactive_edges
    write = cocotb.start_soon(_write_and_settle(tb, reg, value))
    await First(write.join(), watch.raised.wait())
    raised = watch.events[seen:]
    if raised:
        write.kill()
    else:
        tb.checker.require_stimulus(
            created=watch.inactive_edges - edges > SETTLE_CYCLES and not tb.observe("dmactive"),
            reason=f"{name} write: dmactive was not 0 throughout its window",
        )
    tb.checker.expect_true(NO_BUS, passed=not raised, detail=f"{what} raised {', '.join(raised)}")


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dmactive_no_bus_test(dut: SimHandleBase) -> None:
    """While dmactive = 0, writes of sbcs, sbaddress0 and sbdata0 raise no ARVALID or
    AWVALID; with dmactive set before and after, accesses raise VALID and complete."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_no_bus_test")
    base = tb.region + NO_BUS_OFFSET
    tb.mem_fill(base, REGION_WORDS * tb.nbytes)
    watch = _InactiveBusWatch(tb)
    task: Task = cocotb.start_soon(watch.run())
    for loop in range(NO_BUS_LOOPS):
        tb.log.info("tdr2axi_dmactive_no_bus_test: loop %d of %d", loop + 1, NO_BUS_LOOPS)
        await _sba_round_trip(tb, base, SINGLE_WRITE)
        if _fault(tb, "skip", "the loop leaves out dmactive = 0 and its writes"):
            continue
        size = tb.rng.randint(0, tb.max_size)
        addr = _random_addr(tb, base, size)
        # Each write would start an access with dmactive = 1: sbaddress0 with sbreadonaddr,
        # a later sbdata0 read with sbreadondata, and sbdata0 itself
        writes = [
            ("sbcs", SBCS, sbcs_value(size, readonaddr=True, readondata=True, autoinc=True)),
            ("sbaddress0", SBADDRESS0, addr & WORD_MASK),
            ("sbdata0", SBDATA0, tb.rng.getrandbits(32)),
        ]
        tb.rng.shuffle(writes)
        await tb.dmi_write(DMCONTROL, 0)
        # Every READY is low while dmactive = 0, so an access the DM starts there waits at
        # VALID, where the watch fails it, and never reaches a handshake
        tb.sub.stall = True
        await tb.clk_cycles(tb.rng.randint(0, INACTIVE_IDLE_MAX))
        for name, reg, value in writes:
            await _write_no_bus(tb, watch, name=name, reg=reg, value=value)
        tb.sub.stall = False
        await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)
        tb.checker.expect_equal(
            CLEAR, await tb.dmi_read(SBCS), tb.sbcs_reset, "sbcs after dmactive = 1"
        )
    await _sba_round_trip(tb, base, SINGLE_WRITE)
    task.kill()
    tb.checker.require_stimulus(
        created=watch.active_valids > 0, reason="the VALID watch saw no VALID with dmactive set"
    )
    tb.finish()


# --- tdr2axi_dmactive_inflight_test -----------------------------------------------------
async def _wait_net(tb: Tdr2axiTb, net: str, value: int, limit: int) -> bool:
    """Wait up to ``limit`` i_clk edges for ``net`` to read ``value``."""
    for _ in range(limit):
        if tb.observe(net) == value:
            return True
        await tb.clk_cycles(1)
    return tb.observe(net) == value


class _PayloadWatch:
    """The payload of each request channel of one access at every i_clk edge its VALID is
    high, until killed. A change with dmactive = 0 stops the watch with the channel, the
    time and both payloads in ``change``; a change with dmactive = 1 becomes the held
    payload, which ``AxiSub`` grades."""

    def __init__(self, tb: Tdr2axiTb, channels: tuple[str, ...]) -> None:
        self._tb = tb
        self._channels = channels
        self.change = ""

    def _payload(self, ch: str) -> str:
        tb = self._tb
        if ch == "w":
            return f"data 0x{tb.observe('w_data'):x} strb 0x{tb.observe('w_strb'):x}"
        return f"addr 0x{tb.observe(f'{ch}_addr'):x} size {tb.observe(f'{ch}_size')}"

    async def run(self) -> None:
        """Sample until a change with dmactive = 0, or until killed."""
        tb = self._tb
        held: dict[str, str] = {}
        while not self.change:
            await tb.clk_cycles(1)
            for ch in self._channels:
                if not tb.observe(f"{ch}_valid"):
                    continue
                now = self._payload(ch)
                if held.setdefault(ch, now) == now:
                    continue
                if tb.observe("dmactive"):
                    held[ch] = now
                    continue
                self.change = (
                    f"{ch.upper()} payload changed after the dmactive fall, before READY, at "
                    f"{get_sim_time('ns')} ns: {held[ch]} -> {now}"
                )
                break


async def _start_stalled(tb: Tdr2axiTb, addr: int, size: int, data: int | None) -> str:
    """Start a read (``data`` None) or a write with every READY low; returns the request
    channel the access waits on."""
    autoinc = bool(tb.rng.getrandbits(1))
    tb.sub.stall = True
    if data is None:
        await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=True, autoinc=autoinc))
        await tb.sba_set_addr(addr)
        return "ar"
    await tb.dmi_write(SBCS, sbcs_value(size, autoinc=autoinc))
    await tb.sba_set_addr(addr)
    await tb.sba_write_data(size, data)
    return "aw"


async def _inflight_access(tb: Tdr2axiTb, base: int, *, write: bool) -> None:
    """One access stalled on the bus, dmactive cleared under it, then released: it completes
    once with its own payload, and dmactive is set again once the DM is idle."""
    dropped = not _fault(tb, "skip", "the access completes with dmactive = 1")
    size = tb.rng.randint(0, tb.max_size)
    addr = _random_addr(tb, base, size)
    data = tb.rand_data(size) if write else None
    kind = "write" if write else "read"
    check_id = INFLIGHT if dropped else (SINGLE_WRITE if write else SINGLE_READ)
    tb.log.info("tdr2axi_dmactive_inflight_test: %s of size %d @0x%x", kind, size, addr)
    tb.clear_monitor()
    mark = tb.bus_mark()
    channel = await _start_stalled(tb, addr, size, data)
    on_bus = await _wait_net(tb, f"{channel}_valid", 1, VALID_WAIT_CYCLES)
    tb.checker.require_stimulus(created=on_bus, reason=f"the {kind} raised no VALID")
    watch = _PayloadWatch(tb, ("aw", "w") if write else ("ar",))
    watching: Task = cocotb.start_soon(watch.run())
    await tb.clk_cycles(tb.rng.randint(0, STALL_BEFORE_MAX))
    if dropped:
        await tb.dmi_write(DMCONTROL, 0)
        await tb.clk_cycles(tb.rng.randint(STALL_AFTER_MIN, STALL_AFTER_MAX))
        tb.checker.require_stimulus(
            created=bool(tb.observe(f"{channel}_valid")) and not tb.observe("dmactive"),
            reason=f"the {kind} was not stalled on the bus with dmactive = 0",
        )
    watching.kill()
    if dropped:
        tb.checker.expect_true(INFLIGHT, passed=not watch.change, detail=watch.change)
    tb.sub.stall = False
    idle = await _wait_net(tb, "sbbusy", 0, IDLE_WAIT_CYCLES)
    tb.checker.expect_true(check_id, passed=idle, detail=f"the {kind} never completed")
    seen = tb.bus_mark()
    seen.subtract(mark)
    once = {"ar": 0, "aw": 1, "w": 1} if write else {"ar": 1, "aw": 0, "w": 0}
    tb.checker.expect_true(
        check_id,
        passed=all(seen[ch] == count for ch, count in once.items()),
        detail=f"the {kind} completed as AR {seen['ar']} AW {seen['aw']} W {seen['w']}",
    )
    if data is None:
        tb.expect_read(addr, size, check_id=check_id)
    else:
        tb.expect_write(addr, size, data, check_id=check_id)
        tb.ref_write(addr, size, data)
    tb.mem_check(base, REGION_WORDS * tb.nbytes, check_id=check_id)
    if dropped:
        await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dmactive_inflight_test(dut: SimHandleBase) -> None:
    """A read stalled on AR, or a write stalled on AW and W, when dmactive falls keeps its
    payload until the handshake and completes once with it."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_inflight_test")
    base = tb.region + INFLIGHT_OFFSET
    tb.mem_fill(base, REGION_WORDS * tb.nbytes)
    writes = [False, True] + [bool(tb.rng.getrandbits(1)) for _ in range(INFLIGHT_LOOPS - 2)]
    tb.rng.shuffle(writes)
    for loop, write in enumerate(writes):
        tb.log.info("tdr2axi_dmactive_inflight_test: loop %d of %d", loop + 1, INFLIGHT_LOOPS)
        await _inflight_access(tb, base, write=write)
    tb.finish()
