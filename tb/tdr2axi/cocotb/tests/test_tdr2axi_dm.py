# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi Debug Module registers: the DMI register map, dmstatus, sbcs, the abstract command
error, ndmreset and the absent sbaddress1.

``tdr2axi_dm_registers_test`` runs on the bench of ``Tdr2axiTb``. It reads dmstatus and the
sbcs reset value, writes an abstract command and clears cmderr, sets and clears ndmreset,
writes dmcontrol with ackhavereset and hartsel all ones, then zero, and clears abstractauto.
It then writes each word of data0 to data11 and progbuf0 to progbuf15 its seeded value, the
inverse and the seeded value again, in seeded order, reading the word back after its first
write, and reads every mapped register. Last it sweeps the register map of S-TDR2AXI-0062
and X-RISCV_DEBUG-dm-addresses in ``MAP_PASSES`` seeded passes. Each pass writes, in
seeded order, a seeded value to each of the nine mapped addresses, to each register of
``OTHER_DM_REGS``, and to three addresses outside the map from ``OUTSIDE_MAP``: one next to
the map, one mapped address with DMI address bit 6 set, and one the debug specification
leaves unassigned. It reads back each address outside the map it writes, and after every
write it reads all nine mapped registers in seeded order. Each value differs, on the bits
the SBA model predicts, from what every other mapped register reads, so a write that lands
on another register changes a value the reads after it compare. A dmcontrol value sets
dmactive and haltreq, resumereq or both, and draws every other bit, hartsel and ndmreset
among them; a command value is a random abstract command.

The scoreboard grades every read against the SBA model: dmstatus.version, authenticated and
authbusy and sbcs.sbversion (CHK-DM-VERSION), allunavail and anyunavail
(CHK-DM-HART-UNAVAIL), the sbcs fields (CHK-SBA-SBCS-FIELDS), cmderr on every abstractcs
read (CHK-DM-CMDERR), and, after ndmreset is set, the bridge status, sbaddress, sbcs and
the memory image (CHK-DM-NDMRESET), and it fails a bus access nothing predicted under the
context it falls in. Under CHK-DM-REGISTER-MAP it grades each mapped read on the bits the
model predicts, sbcs.sbbusy aside, except command, which the model leaves open, and the
first sbcs read after dmactive is set, which CHK-DM-DMACTIVE-CLEAR grades. So in the sweep
a write that changes another register, or a read that returns another register's value,
fails that check by name. The model predicts no value for an address outside the map, the
``OTHER_DM_REGS`` included, so those reads are graded on their response code alone; it
predicts dmcontrol on dmactive and ndmreset only, not hartsel. The dmstatus read after each
hartsel write grades allunavail and anyunavail under CHK-DM-HART-UNAVAIL, which the model
predicts set whatever hartsel holds.

The model predicts no value for data0 to data11 or progbuf0 to progbuf15 either, so their
reads are graded on their response code alone. With abstractauto clear no access to them
starts an abstract command, so the mapped reads after them grade cmderr 0 under
CHK-DM-CMDERR and each mapped register on the bits the model predicts under
CHK-DM-REGISTER-MAP.

The sweep starts no bus access: sbcs is written with sbreadonaddr and sbreadondata clear
and sbaccess from 1 to the bus width, and sbaddress0 always holds an odd value, so an sbdata0
write with sberror clear is refused for alignment and sets sberror = 3. Any AXI transaction
is left unchecked in the subordinate and fails CHK-AXISUB-DRAIN.

``tdr2axi_dm_write_ones_test`` runs two sequences in seeded order. The command sequence
writes command with every bit set, reads abstractcs, clears cmderr with 0x700, reads
abstractcs, then does the same with command = 0, so every command field rises and falls;
the scoreboard grades cmderr 2 after each write and 0 after each clear, and any bus access
after a command write, under CHK-DM-CMDERR, and the test checks that no AXI transaction
follows. On a build whose AXI_ADDR_WIDTH is 32 or less, where sbaddress1 is absent
(S-TDR2AXI-0014, X-RISCV_DEBUG-sbaddress-regs), the sbaddress1 sequence writes sbcs with
sbreadondata and a seeded sbaccess, sbaddress0 with a seeded address in range aligned to
that size, reads sbcs to confirm sbbusy and sbbusyerror clear, writes sbaddress1 with
every bit set, and reads sbaddress1, sbaddress0 and sbcs. An sbdata0 read then starts one
bus read at the address sbaddress0 holds; once sbcs reads idle, sbreadondata is cleared
and sbdata is read. The scoreboard grades under CHK-SBA-SBADDRESS1-ABSENT the write's
success answer, sbaddress1 reading 0, sbaddress0 reading what was written, sbbusyerror
clear, the read's AR and any bus access before it; the test checks that the write and the
reads after it start no AXI transaction and that the one AR carries the address
sbaddress0 holds. On a wider build sbaddress1 is present, and the test writes none of its
bits, since S-TDR2AXI-0014 leaves those at or above AXI_ADDR_WIDTH open. Every AXI
transaction the subordinate records is checked, and one left over fails
CHK-AXISUB-DRAIN.
"""

from __future__ import annotations

from collections.abc import Callable

import cocotb
from cocotb.handle import SimHandleBase

from env.sba_ref_model import (
    SBCS_SBACCESS_FIELD,
    SBCS_SBACCESS_LSB,
    SBCS_SBERROR_FIELD,
    SBCS_SBERROR_LSB,
    SBCS_SBREADONADDR,
    SBCS_SBREADONDATA,
    Expected,
)
from env.tdr2axi_scoreboard import CMDERR, SBADDRESS1_ABSENT
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
    DmiOp,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_base_test import (
    SBCS_SBBUSY,
    SBCS_SBBUSYERROR,
    TIMEOUT_MS,
    WORD_MASK,
    Tdr2axiTb,
)

# Access Register command for x0 (RISC-V External Debug Support 0.13.2, 3.6.1.1)
COMMAND_ACCESS_REG = 0x0022_1000
ABSTRACTCS_CLEAR_CMDERR = 0x0000_0700
# dmcontrol fields (RISC-V External Debug Support 0.13.2, dmcontrol)
DMCONTROL_DMACTIVE = 1 << 0
DMCONTROL_NDMRESET = 1 << 1
DMCONTROL_HARTSELHI_LSB = 6
DMCONTROL_HARTSELLO_LSB = 16
DMCONTROL_ACKHAVERESET = 1 << 28
DMCONTROL_RESUMEREQ = 1 << 30
DMCONTROL_HALTREQ = 1 << 31
DMCONTROL_HART_REQUESTS = (DMCONTROL_HALTREQ, DMCONTROL_RESUMEREQ)
# hartsel is 20 bits, hartsello its low 10 and hartselhi its high 10
HARTSEL_HALF_BITS = 10
HARTSEL_HALF_MASK = (1 << HARTSEL_HALF_BITS) - 1
# hartsel is WARL: an all-ones write keeps only the bits the DM implements (0.13.2, dmcontrol)
HARTSEL_WRITES = ((1 << 2 * HARTSEL_HALF_BITS) - 1, 0)
NDMRESET_OFFSET = 0x9000
NDMRESET = "CHK-DM-NDMRESET"
# abstractauto (RISC-V External Debug Support 0.13.2, 3.12)
ABSTRACTAUTO = 0x18
# data0 to data11 and progbuf0 to progbuf15 (RISC-V External Debug Support 0.13.2, 3.12)
DATA_REGS = {0x04 + index: f"data{index}" for index in range(12)}
PROGBUF_REGS = {0x20 + index: f"progbuf{index}" for index in range(16)}
# XOR masks of a word's three writes: its seeded value, the inverse, the seeded value again,
# so every bit of the word rises and falls whatever it held before
WORD_XORS = (0, WORD_MASK, 0)

# The DMI addresses of S-TDR2AXI-0062 and X-RISCV_DEBUG-dm-addresses
MAPPED = {
    DMCONTROL: "dmcontrol",
    DMSTATUS: "dmstatus",
    ABSTRACTCS: "abstractcs",
    COMMAND: "command",
    SBCS: "sbcs",
    SBADDRESS0: "sbaddress0",
    SBADDRESS1: "sbaddress1",
    SBDATA0: "sbdata0",
    SBDATA1: "sbdata1",
}
# DM registers of RISC-V External Debug Support 0.13.2, 3.12, outside the map. Once the sweep
# writes abstractauto, its autoexec bits make an access to data0 to data11 or progbuf0 to
# progbuf15 start an abstract command
OTHER_DM_REGS = {
    ABSTRACTAUTO: "abstractauto",
    0x1D: "nextdm",
    0x34: "haltsum2",
    0x35: "haltsum3",
    0x40: "haltsum0",
}
# Addresses outside the map, a write to any of which changes no mapped register: those next
# to the map at which the debug specification places hartinfo, haltsum1, hawindowsel,
# hawindow, sbaddress3, sbaddress2, sbdata2 and sbdata3; the mapped addresses with DMI
# address bit 6 set; and those at which the debug specification places no register
NEXT_TO_MAP = (0x12, 0x13, 0x14, 0x15, 0x37, 0x3B, 0x3E, 0x3F)
ALIASES = tuple(addr | 0x40 for addr in MAPPED)
UNASSIGNED = (*range(0x04), 0x1E, 0x1F, *range(0x31, 0x34), 0x36, *range(0x41, 0x80))
OUTSIDE_MAP = (NEXT_TO_MAP, ALIASES, UNASSIGNED)
REG_NAMES = {**MAPPED, **OTHER_DM_REGS, **DATA_REGS, **PROGBUF_REGS}
MAP_PASSES = 2
# Draws per write in search of a value no other mapped register reads
DRAW_TRIES = 32
# Steps 5 and 6 outlast TIMEOUT_MS at 1:20 with SYNC_STAGES 4 and the longest Pause-DR and
# bypass draws
DM_TIMEOUT_MS = 6 * TIMEOUT_MS
# sbcs as the sweep writes it: no read trigger, so a write of sbaddress0 or a read of
# sbdata0 starts no access, and sberror written as 0 or all ones, the writes SbaRefModel
# predicts
SBCS_READ_TRIGGERS = SBCS_SBREADONADDR | SBCS_SBREADONDATA
SBCS_SBACCESS = SBCS_SBACCESS_FIELD << SBCS_SBACCESS_LSB
SBCS_SBERROR = SBCS_SBERROR_FIELD << SBCS_SBERROR_LSB
SBCS_DRAWN = WORD_MASK & ~(SBCS_READ_TRIGGERS | SBCS_SBACCESS | SBCS_SBERROR)
# The command values of the write-ones test, in order: every bit set, then every bit clear
COMMAND_WRITES = (WORD_MASK, 0)
# The widest AXI_ADDR_WIDTH at which sbaddress1 is absent (S-TDR2AXI-0014)
SBADDRESS1_ABSENT_WIDTH = 32
# Where in the test region the write-ones test reads, and the bytes it may read from
WRITE_ONES_OFFSET = 0xA000
WRITE_ONES_SPAN = 0x100


@cocotb.test(timeout_time=DM_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dm_registers_test(dut: SimHandleBase) -> None:
    """dmstatus, the sbcs reset value, every abstract command failing as unsupported,
    ndmreset changing nothing tdr2axi holds, the hart unavailable whatever hartsel holds, the
    data and progbuf words written and read with abstractauto clear starting no command, and
    a write to each address of the register map changing only its own register."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dm_registers_test")
    tb.log.info("Step 1: read dmstatus and sbcs / the hart unavailable, the sbcs reset value")
    await tb.dmi_read(DMSTATUS)
    await tb.dmi_read(SBCS)
    tb.log.info("Step 2: write an abstract command, clear cmderr / cmderr 2, then 0")
    await tb.dmi_write(COMMAND, COMMAND_ACCESS_REG)
    await tb.dmi_read(ABSTRACTCS)
    await tb.dmi_write(ABSTRACTCS, ABSTRACTCS_CLEAR_CMDERR)
    await tb.dmi_read(ABSTRACTCS)
    tb.log.info("Step 3: set and clear ndmreset / SBA registers, status and memory unchanged")
    await _ndmreset(tb)
    tb.log.info("Step 4: write hartsel all ones, then zero / dmactive kept, the hart unavailable")
    await _hartsel(tb)
    tb.log.info("Step 5: clear abstractauto, write each data and progbuf word / cmderr 0")
    await _data_progbuf(tb)
    tb.log.info("Step 6: sweep the register map / each write changes only its own register")
    await _register_map(tb)
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_dm_write_ones_test(dut: SimHandleBase) -> None:
    """command written with every bit set and then zero fails as unsupported each time and
    starts nothing, and where AXI_ADDR_WIDTH is 32 or less an sbaddress1 write of every bit
    set has no effect: it reads 0, and sbaddress0 and the next bus read keep the address
    written before it."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_dm_write_ones_test")
    steps = [_command_ones_zero, _sbaddress1_ones]
    tb.rng.shuffle(steps)
    tb.log.info("sequence order: %s", " then ".join(step.__name__ for step in steps))
    for index, step in enumerate(steps, start=1):
        tb.log.info("Step %d: %s", index, (step.__doc__ or "").splitlines()[0])
        await step(tb)
    tb.finish()


async def _command_ones_zero(tb: Tdr2axiTb) -> None:
    """command all ones, then zero / cmderr 2 after each, 0 after each clear, no bus access.

    Each write of ``COMMAND_WRITES`` is followed by an abstractcs read, a write of 0x700 that
    clears cmderr and another abstractcs read, which the scoreboard grades."""
    for value in COMMAND_WRITES:
        tb.log.info("command write 0x%08x, then cmderr cleared", value)
        await tb.dmi_write(COMMAND, value)
        await tb.dmi_read(ABSTRACTCS)
        await tb.dmi_write(ABSTRACTCS, ABSTRACTCS_CLEAR_CMDERR)
        await tb.dmi_read(ABSTRACTCS)
    tb.expect_no_bus(CMDERR, "command written all ones and zero")


async def _sbaddress1_ones(tb: Tdr2axiTb) -> None:
    """sbaddress1 all ones where it is absent / no effect, then one read at sbaddress0.

    sbcs with sbreadondata and a seeded size, sbaddress0 with a seeded address, sbbusy and
    sbbusyerror confirmed clear, sbaddress1 written with every bit set and read back with
    sbaddress0 and sbcs, then an sbdata0 read that starts one bus read at the address
    sbaddress0 holds. On a build wider than 32 bits nothing is written."""
    width = tb.build.addr_width
    if width > SBADDRESS1_ABSENT_WIDTH:
        tb.log.info(
            "AXI_ADDR_WIDTH = %d: sbaddress1 is present, no sbaddress1 bit at or above the "
            "width is written",
            width,
        )
        return
    size = tb.rng.randint(0, tb.max_size)
    base, span = tb.address_range(WRITE_ONES_OFFSET, WRITE_ONES_SPAN)
    addr = base + tb.rng.randrange(0, span, 1 << size)
    tb.mem_fill(addr, 1 << size)
    tb.log.info(
        "AXI_ADDR_WIDTH = %d: sbaddress0 0x%x, then sbaddress1 0x%08x, then a read of %d "
        "bytes at sbaddress0",
        width,
        addr,
        WORD_MASK,
        1 << size,
    )
    await tb.dmi_write(SBCS, sbcs_value(size, readondata=True))
    await tb.dmi_write(SBADDRESS0, addr)
    sbcs = await tb.dmi_read(SBCS)
    tb.checker.require_stimulus(
        created=not (sbcs >> SBCS_SBBUSY) & 1 and not (sbcs >> SBCS_SBBUSYERROR) & 1,
        reason=f"sbcs 0x{sbcs:08x} has sbbusy or sbbusyerror set before the sbaddress1 write",
    )
    mark = tb.bus_mark()
    await tb.dmi_write(SBADDRESS1, WORD_MASK)
    await tb.dmi_read(SBADDRESS1)
    await tb.dmi_read(SBADDRESS0)
    await tb.dmi_read(SBCS)
    tb.expect_no_bus_since(mark, SBADDRESS1_ABSENT, "sbaddress1 written all ones, then read")
    await tb.sba_read_data(size)
    await tb.sba_idle()
    tb.expect_read(addr, size, check_id=SBADDRESS1_ABSENT)
    await tb.dmi_write(SBCS, sbcs_value(size))
    data = await tb.sba_read_data(size)
    tb.log.info("read of %d bytes at 0x%x returned 0x%x", 1 << size, addr, data)
    tb.expect_no_bus(SBADDRESS1_ABSENT, "sbdata read with sbreadondata clear")


async def _ndmreset(tb: Tdr2axiTb) -> None:
    """ndmreset set and cleared, then the bridge status, sbaddress and sbcs read back and the
    memory image compared, all by the scoreboard."""
    addr = tb.region + NDMRESET_OFFSET
    tb.mem_fill(addr, tb.nbytes)
    # sbreadonaddr is clear after reset, so the address write starts no access
    await tb.sba_set_addr(addr)
    await tb.dmi_read(SBCS)
    await tb.dmi_write(DMCONTROL, DMCONTROL_NDMRESET | DMCONTROL_DMACTIVE)
    await tb.dmi_write(DMCONTROL, DMCONTROL_DMACTIVE)
    await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    await tb.dmi_read(SBADDRESS0)
    if tb.build.addr_width > 32:
        await tb.dmi_read(SBADDRESS1)
    await tb.dmi_read(SBCS)
    tb.mem_check(addr, tb.nbytes, check_id=NDMRESET)


async def _hartsel(tb: Tdr2axiTb) -> None:
    """dmcontrol written with dmactive, ackhavereset and each of ``HARTSEL_WRITES``, each
    write followed by a read of dmcontrol and dmstatus, which the scoreboard grades."""
    for hartsel in HARTSEL_WRITES:
        value = (
            DMCONTROL_DMACTIVE
            | DMCONTROL_ACKHAVERESET
            | (hartsel & HARTSEL_HALF_MASK) << DMCONTROL_HARTSELLO_LSB
            | (hartsel >> HARTSEL_HALF_BITS) << DMCONTROL_HARTSELHI_LSB
        )
        tb.log.info("dmcontrol write 0x%08x: hartsel 0x%05x with ackhavereset", value, hartsel)
        await tb.dmi_write(DMCONTROL, value)
        await tb.dmi_read(DMCONTROL)
        await tb.dmi_read(DMSTATUS)


async def _data_progbuf(tb: Tdr2axiTb) -> None:
    """abstractauto cleared, then each word of ``DATA_REGS`` and ``PROGBUF_REGS`` written its
    seeded value XOR each of ``WORD_XORS`` in turn, each round in seeded order and each write
    of the first round followed by a read of the word, then every mapped register read."""
    await tb.dmi_write(ABSTRACTAUTO, 0)
    await tb.dmi_read(ABSTRACTAUTO)
    words = [*DATA_REGS, *PROGBUF_REGS]
    seeded = {reg: tb.rng.getrandbits(32) for reg in words}
    tb.log.info(
        "data and progbuf seeded values: %s",
        " ".join(f"{_name(reg)}=0x{value:08x}" for reg, value in seeded.items()),
    )
    for round_index, xor in enumerate(WORD_XORS, start=1):
        tb.rng.shuffle(words)
        tb.log.info(
            "data and progbuf round %d of %d, seeded value xor 0x%08x: %s",
            round_index,
            len(WORD_XORS),
            xor,
            " ".join(_name(reg) for reg in words),
        )
        for reg in words:
            await tb.dmi_write(reg, seeded[reg] ^ xor)
            if round_index == 1:
                await tb.dmi_read(reg)
    await _read_map(tb, "after the data and progbuf words")


async def _register_map(tb: Tdr2axiTb) -> None:
    """sbaddress0 made odd, every mapped register read, then ``MAP_PASSES`` passes of a
    distinct value written to every mapped address, to every register of ``OTHER_DM_REGS``
    and to one address of each group of ``OUTSIDE_MAP``, in seeded order, each write followed
    by a read of every mapped register."""
    await tb.dmi_write(SBADDRESS0, _draw(tb, SBADDRESS0))
    await _read_map(tb, "before the sweep")
    for pass_index in range(1, MAP_PASSES + 1):
        writes = [*MAPPED, *OTHER_DM_REGS, *(tb.rng.choice(group) for group in OUTSIDE_MAP)]
        tb.rng.shuffle(writes)
        tb.log.info(
            "register map pass %d of %d: writes to %s",
            pass_index,
            MAP_PASSES,
            " ".join(_name(reg) for reg in writes),
        )
        for index, reg in enumerate(writes, start=1):
            value, held = _distinct_value(tb, reg)
            context = f"pass {pass_index} write {index}: 0x{value:08x} to {_name(reg)}"
            tb.log.info(
                "%s of %d; value also read by %s",
                context,
                len(writes),
                " ".join(_name(r) for r in held) or "no other mapped register",
            )
            await tb.dmi_write(reg, value)
            if reg not in MAPPED:
                await tb.dmi_read(reg)
            await _read_map(tb, f"after {context}")


async def _read_map(tb: Tdr2axiTb, context: str) -> None:
    """Every mapped register read in seeded order; the scoreboard grades each but command
    under CHK-DM-REGISTER-MAP."""
    regs = list(MAPPED)
    tb.rng.shuffle(regs)
    tb.log.info("read map %s: %s", context, " ".join(_name(reg) for reg in regs))
    for reg in regs:
        await tb.dmi_read(reg)


def _draw(tb: Tdr2axiTb, reg: int) -> int:
    """A seeded value for a write of ``reg`` that starts no bus access and keeps dmactive
    set: dmcontrol with haltreq, resumereq or both, sbaddress0 odd and within the address
    width, sbaddress1 within it, and sbcs with no read trigger, sbaccess from 1 to the bus
    width and sberror kept or cleared whole."""
    value = tb.rng.getrandbits(32)
    addr_mask = (1 << tb.build.addr_width) - 1
    if reg == DMCONTROL:
        if not value & (DMCONTROL_HALTREQ | DMCONTROL_RESUMEREQ):
            value |= tb.rng.choice(DMCONTROL_HART_REQUESTS)
        return value | DMCONTROL_DMACTIVE
    if reg == SBADDRESS0:
        return (value & addr_mask & WORD_MASK) | 1
    if reg == SBADDRESS1:
        return value & (addr_mask >> 32)
    if reg == SBCS:
        size = tb.rng.randint(1, tb.max_size)
        sberror = tb.rng.choice((0, SBCS_SBERROR))
        return (value & SBCS_DRAWN) | (size << SBCS_SBACCESS_LSB) | sberror
    return value


def _distinct_value(tb: Tdr2axiTb, reg: int) -> tuple[int, list[int]]:
    """A ``_draw`` value for ``reg`` that differs, on the bits the SBA model predicts, from
    what every other mapped register reads, with the registers that read it anyway when
    none of ``DRAW_TRIES`` draws differs from all of them."""
    held = {
        other: expected
        for other in MAPPED
        if other != reg and (expected := _prediction(tb, other)) is not None and expected.mask
    }

    def readers(value: int) -> list[int]:
        return [other for other, expected in held.items() if expected.matches(value)]

    value = _draw(tb, reg)
    best = (value, readers(value))
    for _ in range(DRAW_TRIES - 1):
        if not best[1]:
            break
        value = _draw(tb, reg)
        same = readers(value)
        if len(same) < len(best[1]):
            best = (value, same)
    return best


def _prediction(tb: Tdr2axiTb, reg: int) -> Expected | None:
    """The SBA model's prediction of a read of mapped register ``reg``; None for command,
    which the model leaves open."""
    sba = tb.predictor.sba
    readers: dict[int, Callable[[], Expected]] = {
        DMCONTROL: sba.predict_dmcontrol,
        DMSTATUS: sba.predict_dmstatus,
        ABSTRACTCS: sba.predict_abstractcs,
        SBCS: sba.predict_sbcs,
        SBADDRESS0: lambda: sba.predict_sbaddress(0),
        SBADDRESS1: lambda: sba.predict_sbaddress(1),
        SBDATA0: lambda: sba.predict_sbdata(0),
        SBDATA1: lambda: sba.predict_sbdata(1),
    }
    reader = readers.get(reg)
    return None if reader is None else reader()


def _name(reg: int) -> str:
    return REG_NAMES.get(reg) or f"0x{reg:02x}"
