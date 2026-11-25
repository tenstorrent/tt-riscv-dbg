# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi dmactive: dmcontrol.dmactive cleared and set, idle and with bus traffic.

Each test runs seeded loops on the bench of ``Tdr2axiTb``; sizes, addresses, data, the
register order and the idle time while dmactive = 0 come from the seed. Atoms
S-TDR2AXI-0056, S-TDR2AXI-0064, S-TDR2AXI-0089 to S-TDR2AXI-0092 and
X-RISCV_DEBUG-dmactive, with the clock-reset entry RST-DMACTIVE.

* ``tdr2axi_dmactive_clear_test``: two loops; the seed picks the one whose sbcs has
  sbautoincrement set and the one that raises sbbusyerror. Op 3 with the bridge's sticky
  busy set clears it and leaves every DM register value it was given
  (CHK-DM-OP3-KEEPS-DM). In the loop that raises sbbusyerror, a seeded single read or write
  then waits at READY while a seeded access of the six that collide while sbbusy is set is
  answered busy, op 3 recovers, once the access completes an sbcs read must show
  sbbusyerror = 1, and the programmed sbcs is written again, so dmactive falls with every
  sbcs field it was given and sbbusyerror set; the other loop's fall has sbbusyerror 0.
  While dmactive = 0, sbcs reads sbbusy and sbbusyerror 0, first before any other access
  and again beside sbaddress0, sbaddress1, sbdata0, sbdata1 and abstractcs.cmderr, which
  read their reset values whatever was written before or meanwhile; after dmactive = 1
  sbcs and those registers read their reset values and accesses work
  (CHK-DM-DMACTIVE-CLEAR). The scoreboard grades both checks on every read as well.
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
* ``tdr2axi_dmactive_inflight_test``: seven loops in seeded order. Each writes an
  abstract command and reads abstractcs, so cmderr reads 2 before dmactive falls, then
  starts one access at a nonzero address with a non-reset sbcs rw field, which the
  subordinate holds at READY (``stall``) or at its R or B response (``hold_responses``)
  while dmactive falls. A request held at READY keeps its payload until the handshake:
  ``AxiSub`` grades it at every edge (CHK-AXISUB-PAYLOAD-STABLE, and
  CHK-DM-DMACTIVE-INFLIGHT at the handshake or a withdrawal), and the test fails
  CHK-DM-DMACTIVE-INFLIGHT on a payload change or a withdrawal in the edges between the
  fall and the scenario. A released access completes once with the address, size, data
  and strobes it started with (CHK-DM-DMACTIVE-INFLIGHT here).

  Hold, a read and a write held at READY: in the one the seed picks, a seeded access of
  the six that collide while sbbusy is set (a write of sbaddress0, sbaddress1, sbdata0 or
  sbdata1, a read of sbdata0 or sbdata1) is answered busy before the fall, op 3 recovers,
  and an sbcs read must show sbbusyerror = 1; the other reaches the fall with it 0. sbcs,
  sbaddress0, sbaddress1 and abstractcs are read, seeded writes of sbaddress0, sbdata0 and
  sbcs, the sbcs write with 1 in sbbusyerror and the sberror bits, and a read of sbdata0
  follow, each followed by op 3, and sbcs, sbaddress0 and sbaddress1 are read again; after
  the release sbcs is polled to sbbusy = 0, which must read sbbusyerror = 0 with it, and
  the SBA registers are read before dmactive is set and again after
  (CHK-DM-DMACTIVE-INFLIGHT-HOLD, CHK-DM-DMACTIVE-CLEAR). Re-arm, a read and a write held
  at READY and one held at its response, a read the seed picks answered OKAY and one of
  the other two answered SLVERR or DECERR: abstractcs, sbcs, sbaddress0 and sbaddress1 are
  read, dmactive is set while the access waits, and the subordinate releases it at least
  ``REARM_SETTLE_EDGES`` i_clk edges after the edge the DM takes that write, so the result
  lands in sbcs, sbdata0 and sbaddress (CHK-DM-DMACTIVE-REARM); RREADY or BREADY of the
  response-held one must stay high from before the fall until then
  (CHK-DM-DMACTIVE-NO-ABORT). No-abort, one access held at
  READY and one at its response: op 3 and a wait longer than any other stall leave sbbusy
  reading 1, sbaddress the access's address and the held VALIDs, RREADY or BREADY high,
  and i_arst_n then ends the access: every request VALID, RREADY and BREADY reads low in
  reset, a request held at READY never reaches a handshake, and sbbusy and sbbusyerror read
  0 after it (CHK-DM-DMACTIVE-NO-ABORT). The scoreboard grades every register read.

``NEGATIVE_KNOB`` names one fault per run and defaults off: ``skip`` leaves out each
test's dmactive step, so its required checks log no pass (CHK-NONVAC); ``memory`` changes
one subordinate byte while dmactive = 0, as a write no AXI handshake shows would
(CHK-DM-DMACTIVE-ALONE); ``drain`` leaves the reset test's last read unchecked in the
monitor (CHK-AXISUB-DRAIN); ``op3`` scans a NOP in place of each op 3 of the clear test,
so the sticky busy stays set (CHK-DM-OP3-KEEPS-DM).
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import Event, First, Timer
from cocotb.utils import get_sim_time

from env.axi_sub import RESP_DECERR, RESP_OKAY, RESP_SLVERR
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
    SBCS_CLEAR_ERRORS,
    SBCS_SBACCESS_LSB,
    SBCS_SBAUTOINCREMENT,
    SBCS_SBBUSY,
    SBCS_SBBUSYERROR,
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
NO_ABORT = "CHK-DM-DMACTIVE-NO-ABORT"
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
# i_clk cycles the held access waits before and after dmactive falls
STALL_BEFORE_MAX = 6
STALL_AFTER_MIN = 2
STALL_AFTER_MAX = 12
VALID_WAIT_CYCLES = 64

# The inflight test's scenarios
HOLD_RUN = "Hold"
REARM_RUN = "Re-arm"
NO_ABORT_RUN = "No-abort"
DRAW_TRIES = 64
# The subordinate's answers to a held access, OKAY on half of them
RESPONSES = (RESP_OKAY, RESP_OKAY, RESP_SLVERR, RESP_DECERR)
# The answers of the Re-arm loop the seed picks to land a bus error
ERROR_RESPONSES = (RESP_SLVERR, RESP_DECERR)
# The Re-arm loops by index: a read and a write held at READY, one held at its response.
# The seed picks one that reads to answer OKAY and one of the other two to answer SLVERR or
# DECERR
REARM_LOOPS = 3
REARM_READ = 0
REARM_WRITE = 1
REARM_AT_RESPONSE = 2
# Fewest i_clk edges between the DM taking dmactive = 1 and the release of a Re-arm
# access, so the release is clear of the cycle of that write: S-TDR2AXI-0091 leaves a
# dmactive = 1 write in the response's own cycle open
REARM_SETTLE_EDGES = 2
NO_ABORT_WAIT_MAX = 64
# i_clk edges with i_arst_n low before the request VALIDs, RREADY and BREADY are read
RESET_CLK_EDGES = 4
REQUEST_VALIDS = ("ar_valid", "aw_valid", "w_valid")
# The seven inflight loops run past TIMEOUT_MS at a 1:20 clock ratio
INFLIGHT_TIMEOUT_MS = 3 * TIMEOUT_MS
SBACCESS_RESET = 2
SBCS_RW_FLAGS = sbcs_value(0, readonaddr=True, readondata=True, autoinc=True)
SBCS_BUSY_PAIR = (1 << SBCS_SBBUSY) | (1 << SBCS_SBBUSYERROR)


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
    tb: Tdr2axiTb, *, sba: bool = True, sbcs: bool = False, when: str = "with dmactive = 0"
) -> None:
    """The registers dmactive = 0 clears read their reset values, in seeded order; with
    ``sba`` false only abstractcs.cmderr, so no SBA register is accessed, and with ``sbcs``
    sbcs too, on sbbusy and sbbusyerror. ``when`` names the dmactive state in each
    failure."""
    regs = [ABSTRACTCS]
    if sba:
        regs += [SBADDRESS0, SBDATA0, SBDATA1]
    if sba and tb.build.addr_width > 32:
        regs.append(SBADDRESS1)
    if sbcs:
        regs.append(SBCS)
    tb.rng.shuffle(regs)
    for reg in regs:
        value = await tb.dmi_read(reg)
        if reg == ABSTRACTCS:
            value = (value >> ABSTRACTCS_CMDERR_LSB) & CMDERR_FIELD
        elif reg == SBCS:
            value &= SBCS_BUSY_PAIR
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
async def _program_dm(tb: Tdr2axiTb, base: int, *, autoinc: bool) -> dict[int, int]:
    """Give sbcs sbautoincrement ``autoinc`` and a seeded sbaccess, and every other register
    dmactive = 0 clears a value other than its reset value, with no access in flight,
    sbdata0 keeping the nonzero data of the read before; returns the value each reads,
    cmderr for abstractcs."""
    size = tb.rng.randint(0, tb.max_size)
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


async def _raise_sbbusyerror(tb: Tdr2axiTb, base: int) -> None:
    """sbbusyerror set with no access in flight: a seeded single read or write the
    subordinate holds at READY, a seeded access of ``_collisions`` answered busy while it
    waits, op 3, then the release; once sbbusy reads 0, sbcs must show sbbusyerror = 1.
    The access leaves sbdata0 nonzero, and sbdata1 too for a 64-bit access."""
    write = bool(tb.rng.getrandbits(1))
    for _ in range(DRAW_TRIES):
        size = tb.rng.randint(0, tb.max_size)
        addr = _random_addr(tb, base, size)
        data = tb.rand_data(size) if write else tb.ref_read(addr, size)
        nonzero = bool(data & WORD_MASK and (size != SIZE_64 or data >> 32))
        if nonzero:
            break
    tb.checker.require_stimulus(
        created=nonzero, reason=f"no access drawn with nonzero data in {DRAW_TRIES}"
    )
    op, reg, value = tb.rng.choice(_collisions(tb, base))
    kind = "write" if write else "read"
    tb.log.info(
        "tdr2axi_dmactive_clear_test: %s of size %d @0x%x held at READY, %s of 0x%02x "
        "(0x%08x) collides",
        kind,
        size,
        addr,
        op.name,
        reg,
        value,
    )
    tb.clear_monitor()
    tb.sub.stall = True
    await tb.dmi_write(SBCS, sbcs_value(size, readonaddr=not write))
    await tb.sba_set_addr(addr)
    if write:
        await tb.sba_write_data(size, data)
    result = await tb.dmi(op, reg, value, clear_busy=False)
    tb.log.info("collision status %s", result.status.name)
    await tb.dmi_clear()
    tb.sub.stall = False
    sbcs = await tb.sba_poll()
    tb.checker.require_stimulus(
        created=bool((sbcs >> SBCS_SBBUSYERROR) & 1),
        reason=f"sbcs 0x{sbcs:08x} after the {op.name} of 0x{reg:02x} with the {kind} held "
        "shows sbbusyerror clear",
    )
    if write:
        tb.expect_write(addr, size, data, check_id=SINGLE_WRITE)
        tb.ref_write(addr, size, data)
    else:
        tb.expect_read(addr, size, check_id=SINGLE_READ)


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
    """Op 3 with busy set keeps the DM registers; dmactive = 0 clears them and sbcs, with
    sbautoincrement set in one loop and sbbusyerror, set by a collision, in one loop,
    whatever is written; after dmactive = 1 they and sbcs read their reset values and
    accesses work."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_clear_test")
    base = tb.region + CLEAR_OFFSET
    tb.mem_fill(base, REGION_WORDS * tb.nbytes)
    autoinc_loop = tb.rng.randrange(CLEAR_LOOPS)
    collide_loop = tb.rng.randrange(CLEAR_LOOPS)
    for loop in range(CLEAR_LOOPS):
        autoinc = loop == autoinc_loop
        collide = loop == collide_loop
        tb.log.info(
            "tdr2axi_dmactive_clear_test: loop %d of %d, sbautoincrement %d, sbbusyerror %d "
            "at the fall",
            loop + 1,
            CLEAR_LOOPS,
            autoinc,
            collide,
        )
        await _sba_round_trip(tb, base, SINGLE_WRITE, nonzero=True)
        expected = await _program_dm(tb, base, autoinc=autoinc)
        await _expect_kept_after_op3(tb, expected)
        if _fault(tb, "skip", "the loop leaves out dmactive = 0 and its checks"):
            continue
        if collide:
            await _raise_sbbusyerror(tb, base)
            # 0 in sbbusyerror, so the write leaves it set
            await tb.dmi_write(SBCS, expected[SBCS])
        await tb.dmi_write(DMCONTROL, 0)
        # Before any SBA register write, which the DM answers busy while sbbusyerror is set
        sbcs = await tb.dmi_read(SBCS)
        tb.checker.expect_equal(
            CLEAR, sbcs & SBCS_BUSY_PAIR, 0, "sbcs sbbusy and sbbusyerror with dmactive = 0"
        )
        await _write_while_cleared(tb)
        if tb.rng.getrandbits(1):
            await tb.dmi_clear()
        await _expect_cleared(tb, sbcs=True)
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
@dataclass(frozen=True)
class _Loop:
    """One inflight loop: the scenario, the access's direction, whether the subordinate
    withholds the R or B response rather than READY, whether an access collides with the
    held one before dmactive falls, and whether the response is SLVERR or DECERR only, or
    OKAY only."""

    scenario: str
    write: bool
    at_response: bool = False
    collide: bool = False
    error: bool = False
    okay: bool = False


@dataclass(frozen=True)
class _Held:
    """One access the subordinate holds across dmactive = 0: its payload, the sbcs value
    that programs it, the response it gets once released, the scenario, whether the
    subordinate withholds the R or B response rather than READY, and the nets that stay
    high while it waits."""

    write: bool
    size: int
    addr: int
    data: int
    sbcs: int
    resp: int
    scenario: str
    at_response: bool
    nets: tuple[str, ...]

    @property
    def kind(self) -> str:
        """``read`` or ``write``."""
        return "write" if self.write else "read"

    @property
    def channels(self) -> tuple[str, ...]:
        """The request channels the access uses."""
        return ("aw", "w") if self.write else ("ar",)

    @property
    def where(self) -> str:
        """Where the subordinate holds the access."""
        return "its response" if self.at_response else "READY"

    @property
    def single(self) -> str:
        """The check of a single access of this kind."""
        return SINGLE_WRITE if self.write else SINGLE_READ


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


class _LevelWatch:
    """Nets sampled at every i_clk rising edge from ``start`` until ``stop``: ``low`` names
    the first one seen low and when, and ``edges`` counts the edges sampled."""

    def __init__(self, tb: Tdr2axiTb, nets: tuple[str, ...]) -> None:
        self._tb = tb
        self._nets = nets
        self._task: Task | None = None
        self.low = ""
        self.edges = 0

    def start(self) -> None:
        """Sample from the next i_clk rising edge."""
        self._task = cocotb.start_soon(self.run())

    def stop(self) -> None:
        """Stop sampling; a second call does nothing."""
        if self._task is not None:
            self._task.kill()
            self._task = None

    async def run(self) -> None:
        """Sample until a net reads low, or until killed."""
        tb = self._tb
        while not self.low:
            await tb.clk_cycles(1)
            self.edges += 1
            dropped = [net for net in self._nets if not tb.observe(net)]
            if dropped:
                self.low = f"{', '.join(dropped)} low at {get_sim_time('ns')} ns"


class _DmcontrolWatch:
    """The DMI request observables at every i_clk rising edge: ``taken`` is set at the edge
    the DM takes a dmcontrol write with dmactive = 1, and ``after`` counts the edges since."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self._tb = tb
        self.taken = False
        self.after = 0

    def _rearm_taken(self) -> bool:
        tb = self._tb
        return (
            bool(tb.observe("dmi_req_valid") and tb.observe("dmi_req_ready"))
            and tb.observe("dmi_req_op") == DmiOp.WRITE
            and tb.observe("dmi_req_addr") == DMCONTROL
            and bool(tb.observe("dmi_req_data") & DMCONTROL_DMACTIVE)
        )

    async def run(self) -> None:
        """Sample until killed."""
        while True:
            await self._tb.clk_cycles(1)
            if self.taken:
                self.after += 1
            else:
                self.taken = self._rearm_taken()


def _draw_held(
    tb: Tdr2axiTb,
    base: int,
    scenario: str,
    *,
    write: bool,
    at_response: bool,
    error: bool,
    okay: bool,
) -> _Held:
    """An access at a nonzero address with a non-reset sbcs rw field, read data or write
    data other than 0, and a seeded response, SLVERR or DECERR with ``error``, OKAY with
    ``okay``. sbreadondata stays clear for Re-arm, whose sbdata0 read after the access would
    start another."""
    for _ in range(DRAW_TRIES):
        size = tb.rng.randint(0, tb.max_size)
        addr = _random_addr(tb, base, size)
        autoinc = bool(tb.rng.getrandbits(1))
        readondata = scenario != REARM_RUN and bool(tb.rng.getrandbits(1))
        data = tb.rand_data(size) if write else tb.ref_read(addr, size)
        # A read's sbreadonaddr is already a non-reset field
        rw_set = not write or size != SBACCESS_RESET or autoinc or readondata
        if addr and data & WORD_MASK and rw_set:
            break
    tb.checker.require_stimulus(
        created=bool(addr and data & WORD_MASK and rw_set),
        reason=f"no {scenario} access drawn with a nonzero address and data in {DRAW_TRIES}",
    )
    sbcs = sbcs_value(size, readonaddr=not write, readondata=readondata, autoinc=autoinc)
    if at_response:
        nets = ("b_ready",) if write else ("r_ready",)
    else:
        nets = ("aw_valid", "w_valid") if write else ("ar_valid",)
    if okay:
        answers: tuple[int, ...] = (RESP_OKAY,)
    elif error:
        answers = ERROR_RESPONSES
    else:
        answers = RESPONSES
    return _Held(
        write=write,
        size=size,
        addr=addr,
        data=data,
        sbcs=sbcs | SBCS_CLEAR_ERRORS,
        resp=tb.rng.choice(answers),
        scenario=scenario,
        at_response=at_response,
        nets=nets,
    )


async def _start_held(tb: Tdr2axiTb, acc: _Held) -> bool:
    """Program the access with the subordinate holding it at READY or at its response;
    returns whether every net of the hold reads high."""
    if acc.at_response:
        tb.sub.hold_responses = True
    else:
        tb.sub.stall = True
    await tb.dmi_write(SBCS, acc.sbcs)
    await tb.sba_set_addr(acc.addr)
    if acc.write:
        await tb.sba_write_data(acc.size, acc.data)
    for net in acc.nets:
        if not await _wait_net(tb, net, 1, VALID_WAIT_CYCLES):
            return False
    return True


async def _read_shuffled(tb: Tdr2axiTb, regs: list[int]) -> dict[int, int]:
    """Read each register once in seeded order; the scoreboard grades every value."""
    tb.rng.shuffle(regs)
    return {reg: await tb.dmi_read(reg) for reg in regs}


def _collisions(tb: Tdr2axiTb, base: int) -> list[tuple[DmiOp, int, int]]:
    """The accesses the DM answers busy while sbbusy is set, each setting sbbusyerror
    (X-RISCV_DEBUG-sbbusyerror): writes of sbaddress0, sbaddress1, sbdata0 and sbdata1 and
    reads of sbdata0 and sbdata1. The sbaddress values stay below AXI_ADDR_WIDTH."""
    return [
        (DmiOp.WRITE, SBADDRESS0, _random_addr(tb, base, 0) & WORD_MASK),
        (DmiOp.WRITE, SBADDRESS1, tb.rng.getrandbits(max(tb.build.addr_width - 32, 0))),
        (DmiOp.WRITE, SBDATA0, tb.rng.getrandbits(32)),
        (DmiOp.WRITE, SBDATA1, tb.rng.getrandbits(32)),
        (DmiOp.READ, SBDATA0, 0),
        (DmiOp.READ, SBDATA1, 0),
    ]


async def _collide_before_fall(tb: Tdr2axiTb, base: int, acc: _Held) -> None:
    """One seeded access of ``_collisions`` while the access is held with dmactive set, op
    3 to recover the busy answer, then an sbcs read that must show sbbusyerror = 1."""
    op, reg, value = tb.rng.choice(_collisions(tb, base))
    tb.log.info(
        "%s %s collides before the fall: %s of 0x%02x (0x%08x), busy answer expected",
        acc.scenario,
        acc.kind,
        op.name,
        reg,
        value,
    )
    result = await tb.dmi(op, reg, value, clear_busy=False)
    tb.log.info("collision status %s", result.status.name)
    await tb.dmi_clear()
    sbcs = await tb.dmi_read(SBCS)
    tb.checker.require_stimulus(
        created=bool((sbcs >> SBCS_SBBUSYERROR) & 1),
        reason=f"sbcs 0x{sbcs:08x} after the {op.name} of 0x{reg:02x} with the {acc.kind} "
        "held shows sbbusyerror clear",
    )


async def _hold_time_accesses(tb: Tdr2axiTb, base: int, acc: _Held) -> None:
    """In seeded order, writes of sbaddress0, sbdata0 and sbcs with values other than the
    held ones and an sbdata0 read, each recovered with op 3 whatever the DM answered. The
    sbcs write also writes 1 to sbbusyerror and the sberror bits."""
    other = acc.addr
    while other & WORD_MASK == acc.addr & WORD_MASK:
        other = _random_addr(tb, base, acc.size)
    size = tb.rng.choice([s for s in range(tb.max_size + 1) if s != acc.size])
    flags = (acc.sbcs ^ SBCS_RW_FLAGS) & SBCS_RW_FLAGS
    sbcs = sbcs_value(size) | flags | SBCS_CLEAR_ERRORS
    accesses = [
        (DmiOp.WRITE, SBADDRESS0, other & WORD_MASK),
        (DmiOp.WRITE, SBDATA0, tb.rng.getrandbits(32)),
        (DmiOp.WRITE, SBCS, sbcs),
        (DmiOp.READ, SBDATA0, 0),
    ]
    tb.rng.shuffle(accesses)
    for op, reg, value in accesses:
        result = await tb.dmi(op, reg, value, clear_busy=False)
        tb.log.info(
            "hold-time %s of 0x%02x (0x%08x): status %s",
            op.name,
            reg,
            value,
            result.status.name,
        )
        await tb.dmi_clear()


async def _release(tb: Tdr2axiTb) -> None:
    """The subordinate takes the request or gives the response, with the drawn response
    code; sbcs is polled until sbbusy = 0."""
    tb.sub.stall = False
    tb.sub.hold_responses = False
    await tb.sba_poll()


async def _read_after_completion(tb: Tdr2axiTb, *, rearmed: bool) -> None:
    """The SBA registers once the access has completed, and with ``rearmed`` false the
    same after dmactive is set again, with sbcs and abstractcs."""
    regs = [SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1]
    if rearmed:
        await _read_shuffled(tb, [*regs, ABSTRACTCS])
        return
    await _read_shuffled(tb, list(regs))
    await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)
    await _read_shuffled(tb, [SBCS, *regs, ABSTRACTCS])


async def _hold(tb: Tdr2axiTb, base: int, acc: _Held) -> None:
    """Hold: reads of the held registers, the hold-time accesses, the reads again; then
    the release, and the registers before and after dmactive is set again."""
    await _read_shuffled(tb, [SBCS, SBADDRESS0, SBADDRESS1, ABSTRACTCS])
    await _hold_time_accesses(tb, base, acc)
    await _read_shuffled(tb, [SBCS, SBADDRESS0, SBADDRESS1])
    await _release(tb)
    await _read_after_completion(tb, rearmed=False)


async def _rearm(tb: Tdr2axiTb, acc: _Held, levels: _LevelWatch) -> None:
    """Re-arm: the held registers read, dmactive set while the access is held, the release
    once the DM has taken that write and at least REARM_SETTLE_EDGES i_clk edges have
    passed, then the registers. ``levels`` samples the RREADY or BREADY of a response hold
    from before dmactive fell until the release."""
    await _read_shuffled(tb, [ABSTRACTCS, SBCS, SBADDRESS0, SBADDRESS1])
    watch = _DmcontrolWatch(tb)
    watching: Task = cocotb.start_soon(watch.run())
    await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)
    while watch.taken and watch.after < REARM_SETTLE_EDGES:
        await tb.clk_cycles(1)
    watching.kill()
    tb.checker.require_stimulus(
        created=watch.taken, reason="no DM handshake seen for the write of dmactive = 1"
    )
    if not acc.at_response:
        tb.checker.expect_true(
            INFLIGHT,
            passed=all(tb.observe(net) for net in acc.nets),
            detail=f"the {acc.kind} withdrawn before its handshake once dmactive was set again",
        )
    else:
        levels.stop()
        tb.checker.expect_true(
            NO_ABORT,
            passed=not levels.low,
            detail=f"{'/'.join(acc.nets)} of the {acc.kind} held at its response, before the "
            f"re-arm: {levels.low}",
        )
    await _release(tb)
    await _read_after_completion(tb, rearmed=True)


async def _no_abort(tb: Tdr2axiTb, acc: _Held, watch: _LevelWatch) -> None:
    """No-abort: op 3 and a wait longer than any other stall leave the access in place,
    sbbusy reading 1 and sbaddress the access's address; ``watch`` has sampled the hold's
    nets since before dmactive fell."""
    await tb.dmi_clear()
    after_op3 = await tb.dmi_read(SBCS)
    wait = tb.rng.randint(_no_abort_wait_min(tb), NO_ABORT_WAIT_MAX)
    tb.log.info("No-abort: %d i_clk edges between the sbcs reads", wait)
    await tb.clk_cycles(wait)
    after_wait = await tb.dmi_read(SBCS)
    await _read_shuffled(tb, [SBADDRESS0, SBADDRESS1, ABSTRACTCS])
    for value, when in ((after_op3, "after op 3"), (after_wait, f"after {wait} i_clk edges")):
        tb.checker.expect_true(
            NO_ABORT,
            passed=bool((value >> SBCS_SBBUSY) & 1),
            detail=f"sbcs 0x{value:08x} {when} with the {acc.kind} held",
        )
    tb.checker.expect_true(
        NO_ABORT, passed=not watch.low, detail=f"{'/'.join(acc.nets)} not held: {watch.low}"
    )


def _no_abort_wait_min(tb: Tdr2axiTb) -> int:
    """One i_clk edge more than the longest READY wait, response latency and stall the
    subordinate and the other scenarios draw."""
    timing = tb.sub.cfg.timing
    longest = max(
        timing.ready_wait_max,
        timing.r_latency[1],
        timing.b_latency[1],
        timing.late_latency[1],
        STALL_AFTER_MAX,
    )
    return min(longest + 1, NO_ABORT_WAIT_MAX)


async def _reset_held(tb: Tdr2axiTb, acc: _Held, mark: Counter[str]) -> None:
    """i_arst_n ends the held access: every request VALID and RREADY and BREADY read low in
    reset, a request held at READY never reaches a handshake, and after the release the DM
    reads its reset values, sbbusy and sbbusyerror 0; the subordinate keeps the data of a
    write it took."""
    await Timer(tb.off_edge_delay(), "ps")
    tb.log.info(
        "i_arst_n falls at %.0f ns with the %s held at %s",
        get_sim_time("ns"),
        acc.kind,
        acc.where,
    )
    tb.assert_reset()
    tb.sub.stall = False
    tb.sub.hold_responses = False
    await tb.clk_cycles(RESET_CLK_EDGES)
    raised = [net for net in (*REQUEST_VALIDS, "r_ready", "b_ready") if tb.observe(net)]
    tb.checker.expect_true(
        NO_ABORT, passed=not raised, detail=f"{', '.join(raised)} high with i_arst_n low"
    )
    _expect_bus(tb, acc, mark, NO_ABORT, taken=acc.at_response)
    if acc.write and acc.at_response:
        tb.ref_write(acc.addr, acc.size, acc.data)
    await Timer(tb.off_edge_delay(), "ps")
    tb.release_reset()
    await tb.tck_cycles(tb.build.sync_stages + 2)
    await tb.wait_reset_ready()
    await tb.dmi_clear()
    regs = await _read_shuffled(
        tb, [DMCONTROL, SBCS, SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1, ABSTRACTCS]
    )
    tb.checker.expect_true(
        NO_ABORT,
        passed=not regs[SBCS] & SBCS_BUSY_PAIR,
        detail=f"sbcs 0x{regs[SBCS]:08x} after i_arst_n: sbbusy and sbbusyerror must read 0",
    )
    await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)


def _expect_bus(
    tb: Tdr2axiTb, acc: _Held, mark: Counter[str], check_id: str, *, taken: bool = True
) -> None:
    """The access reached the bus once with the payload it started with, or with ``taken``
    false never reached a handshake."""
    seen = tb.bus_mark()
    seen.subtract(mark)
    once = {"ar": 0, "aw": 1, "w": 1} if acc.write else {"ar": 1, "aw": 0, "w": 0}
    want = once if taken else dict.fromkeys(once, 0)
    tb.checker.expect_true(
        check_id,
        passed=all(seen[ch] == count for ch, count in want.items()),
        detail=f"the {acc.kind} reached the bus as AR {seen['ar']} AW {seen['aw']} W {seen['w']}",
    )
    if not taken:
        return
    if acc.write:
        tb.expect_write(acc.addr, acc.size, acc.data, check_id=check_id)
    else:
        tb.expect_read(acc.addr, acc.size, check_id=check_id)


def _set_response(tb: Tdr2axiTb, acc: _Held, resp: int) -> None:
    if acc.write:
        tb.sub.set_write_response(acc.addr, resp)
    else:
        tb.sub.set_read_response(acc.addr, resp)


async def _drop_dmactive(tb: Tdr2axiTb, acc: _Held, levels: _LevelWatch) -> None:
    """dmactive cleared a drawn number of i_clk edges into the hold, with the payload
    watched from before the write until the scenario's checks; No-abort runs its checks
    under that watch and ``levels``, which samples the hold's nets."""
    payload = _PayloadWatch(tb, () if acc.at_response else acc.channels)
    watching: Task = cocotb.start_soon(payload.run())
    before = tb.rng.randint(0, STALL_BEFORE_MAX)
    after = tb.rng.randint(STALL_AFTER_MIN, STALL_AFTER_MAX)
    tb.log.info("dmactive = 0 written %d i_clk edges into the hold, %d edges waited", before, after)
    await tb.clk_cycles(before)
    await tb.dmi_write(DMCONTROL, 0)
    await tb.clk_cycles(after)
    tb.checker.require_stimulus(
        created=not tb.observe("dmactive"),
        reason=f"dmactive reads 1 after the write of 0 with the {acc.kind} held at {acc.where}",
    )
    if acc.scenario == NO_ABORT_RUN:
        await _no_abort(tb, acc, levels)
    watching.kill()
    if not acc.at_response:
        tb.checker.expect_true(INFLIGHT, passed=not payload.change, detail=payload.change)


async def _held_scenario(tb: Tdr2axiTb, base: int, acc: _Held, mark: Counter[str]) -> str:
    """dmactive cleared with the access held, then the scenario; returns the check that
    grades the bus and the memory."""
    levels = _LevelWatch(tb, acc.nets)
    levels.start()
    await _drop_dmactive(tb, acc, levels)
    if acc.scenario == NO_ABORT_RUN:
        levels.stop()
        await _reset_held(tb, acc, mark)
        return NO_ABORT
    if not acc.at_response:
        levels.stop()
        tb.checker.expect_true(
            INFLIGHT,
            passed=not levels.low,
            detail=f"the {acc.kind} withdrawn before its handshake: {levels.low}",
        )
    if acc.scenario == REARM_RUN:
        await _rearm(tb, acc, levels)
    else:
        await _hold(tb, base, acc)
    check_id = acc.single if acc.at_response else INFLIGHT
    _expect_bus(tb, acc, mark, check_id)
    if acc.write:
        tb.ref_write(acc.addr, acc.size, acc.data)
    return check_id


def _drawn(loop: _Loop) -> str:
    """The log tag of a loop whose response kind the seed fixed."""
    if loop.error:
        return " (SLVERR or DECERR drawn)"
    if loop.okay:
        return " (OKAY drawn)"
    return ""


async def _inflight_loop(tb: Tdr2axiTb, base: int, loop: _Loop) -> None:
    """One access held by the subordinate when dmactive falls, after a command write that
    sets cmderr = 2 and a read of it, then the scenario, with the collision before the fall
    when ``loop`` names one."""
    skip = _fault(tb, "skip", "the access completes with dmactive = 1")
    acc = _draw_held(
        tb,
        base,
        loop.scenario,
        write=loop.write,
        at_response=loop.at_response and not skip,
        error=loop.error,
        okay=loop.okay,
    )
    tb.log.info(
        "tdr2axi_dmactive_inflight_test: %s, %s of size %d @0x%x, sbcs 0x%08x, response %d%s, "
        "held at %s%s",
        loop.scenario,
        acc.kind,
        acc.size,
        acc.addr,
        acc.sbcs,
        acc.resp,
        _drawn(loop),
        acc.where,
        ", collides before the fall" if loop.collide and not skip else "",
    )
    _set_response(tb, acc, acc.resp)
    tb.clear_monitor()
    mark = tb.bus_mark()
    await tb.dmi_write(COMMAND, COMMAND_ACCESS_REG)
    await tb.dmi_read(ABSTRACTCS)
    held = await _start_held(tb, acc)
    tb.checker.require_stimulus(created=held, reason=f"the {acc.kind} was not held at {acc.where}")
    if loop.collide and not skip:
        await _collide_before_fall(tb, base, acc)
    if skip:
        await _release(tb)
        _expect_bus(tb, acc, mark, acc.single)
        if acc.write:
            tb.ref_write(acc.addr, acc.size, acc.data)
        check_id = acc.single
    else:
        check_id = await _held_scenario(tb, base, acc, mark)
    tb.mem_check(base, REGION_WORDS * tb.nbytes, check_id=check_id)
    _set_response(tb, acc, RESP_OKAY)


@cocotb.test(timeout_time=INFLIGHT_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dmactive_inflight_test(dut: SimHandleBase) -> None:
    """An access the subordinate holds when dmactive falls keeps its payload and is held:
    Hold on a read and a write, one of them after a collision, Re-arm on a read and a write
    held at READY and on one held at its response, one read answered OKAY and one other
    loop with an error, and No-abort on one held at READY and one at its response."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dmactive_inflight_test")
    base = tb.region + INFLIGHT_OFFSET
    tb.mem_fill(base, REGION_WORDS * tb.nbytes)
    collide_write = bool(tb.rng.getrandbits(1))
    response_write = bool(tb.rng.getrandbits(1))
    reads = [REARM_READ] if response_write else [REARM_READ, REARM_AT_RESPONSE]
    okay_rearm = tb.rng.choice(reads)
    error_rearm = tb.rng.choice([i for i in range(REARM_LOOPS) if i != okay_rearm])
    loops = [
        _Loop(HOLD_RUN, write=False, collide=not collide_write),
        _Loop(HOLD_RUN, write=True, collide=collide_write),
        _Loop(
            REARM_RUN,
            write=False,
            error=error_rearm == REARM_READ,
            okay=okay_rearm == REARM_READ,
        ),
        _Loop(REARM_RUN, write=True, error=error_rearm == REARM_WRITE),
        _Loop(
            REARM_RUN,
            response_write,
            at_response=True,
            error=error_rearm == REARM_AT_RESPONSE,
            okay=okay_rearm == REARM_AT_RESPONSE,
        ),
        _Loop(NO_ABORT_RUN, bool(tb.rng.getrandbits(1))),
        _Loop(NO_ABORT_RUN, bool(tb.rng.getrandbits(1)), at_response=True),
    ]
    tb.rng.shuffle(loops)
    for index, loop in enumerate(loops):
        tb.log.info("tdr2axi_dmactive_inflight_test: loop %d of %d", index + 1, len(loops))
        await _inflight_loop(tb, base, loop)
    tb.finish()
