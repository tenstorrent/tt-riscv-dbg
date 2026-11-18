# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi: DMI TDR -> RISC-V DM system bus access -> AXI4 / AXI4-Lite manager.

The JTAG side is a TAP-state-accurate TDR host (OpenOCD dmi_jtag flow); the AXI side is a
subordinate that records every handshake, checks valid/payload stability and models memory.
Each SBA access must give exactly one transaction with the expected address, AxSIZE (AXI4),
strobes and byte lanes. The build (AXI_LITE, widths) comes from the Makefile CONFIG.
"""

import cocotb
from cocotb.triggers import ClockCycles, Timer
from tdr2axi_util import (
    ABSTRACTCS,
    COMMAND,
    DMCONTROL,
    DMSTATUS,
    OP_NOP,
    OP_READ,
    OP_WRITE,
    RESP_DECERR,
    RESP_SLVERR,
    SBADDRESS0,
    SBBUSY,
    SBBUSYERROR,
    SBCS,
    SBCS_CLEAR_ERRORS,
    SBCS_INACTIVE,
    SBDATA0,
    ST_BUSY,
    ST_OK,
    OP_CLEAR,
    Tb,
    rand64,
    sbcs_val,
    size_mask,
    tdr_word,
)

TIMEOUT_MS = 2


async def _setup(dut, name):
    """Build the test bench for this config, reset the DUT and set dmactive."""
    # Shown as ASCII in the waveform, to find where each test starts
    dut.test_name.value = int.from_bytes(name[:24].rjust(24).encode(), "big")
    # The cfg_* wires are continuous assigns, still X at time 0 when the first test starts
    await Timer(1, "ps")
    tb = Tb(dut)
    await tb.start()
    return tb


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def dm_basic(dut):
    """dmstatus, sbcs reset value, and every abstract command failing with 'not supported'."""
    tb = await _setup(dut, "dm_basic")
    v = await tb.dmi_read(DMSTATUS)
    dut._log.info(f"dmstatus = {v:#010x}")
    assert v & 0xF == 2, f"dmstatus.version {v & 0xF}, expected 2 (debug spec 0.13)"
    assert (v >> 7) & 1, "dmstatus.authenticated = 0"
    assert (v >> 12) & 1 and (v >> 13) & 1, "hart 0 not reported unavailable"
    v = await tb.dmi_read(SBCS)
    dut._log.info(f"sbcs = {v:#010x}")
    assert v == tb.sbcs_reset, f"sbcs {v:#010x}, expected {tb.sbcs_reset:#010x}"
    await tb.dmi_write(COMMAND, 0x0022_1000)
    v = await tb.dmi_read(ABSTRACTCS)
    assert (v >> 8) & 7 == 2, f"abstractcs.cmderr {(v >> 8) & 7}, expected 2"
    await tb.dmi_write(ABSTRACTCS, 0x0000_0700)
    v = await tb.dmi_read(ABSTRACTCS)
    assert (v >> 8) & 7 == 0, "cmderr not cleared"
    tb.sub.expect_no_bus("dm_basic")
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def single(dut):
    """Every size at every lane: write, check the bus and memory, read back."""
    tb = await _setup(dut, "single")
    base = tb.region + 0x1000
    tb.mem_fill(base, 4 * tb.nbytes)
    for size in range(tb.max_size + 1):
        for lane in range(0, tb.nbytes, 1 << size):
            a = base + size * tb.nbytes + lane
            d = rand64() & size_mask(size)
            tb.sub.clear_monitor()
            await tb.sba_write(a, size, d)
            tb.sub.expect_aw_w(a, size, d)
            tb.sub.expect_no_bus(f"write size {size} @{a:#x}")
            tb.ref_write(a, size, d)
            tb.mem_check(base, 4 * tb.nbytes)
            rd = await tb.sba_read(a, size)
            tb.sub.expect_ar(a, size)
            tb.sub.expect_no_bus(f"read size {size} @{a:#x}")
            assert rd == tb.ref_read(a, size), f"read size {size} @{a:#x} = {rd:#x}, expected {tb.ref_read(a, size):#x}"
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def block(dut):
    """Block write then block read at every size, as in the programming examples of doc/tdr2axi.md."""
    tb = await _setup(dut, "block")
    n = 6
    for size in range(tb.max_size + 1):
        step = 1 << size
        # Start one access in, so 8- and 16-bit blocks cross a bus word
        base = tb.region + 0x2000 + size * 0x100 + step
        tb.mem_fill(base - step, (n + 2) * step)
        d = [rand64() & size_mask(size) for _ in range(n)]

        # Block write
        tb.sub.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_val(size, autoinc=True))
        await tb.sba_set_addr(base)
        for i in range(n):
            await tb.sba_write_data(size, d[i])
        await tb.sba_idle()
        for i in range(n):
            tb.sub.expect_aw_w(base + i * step, size, d[i])
            tb.ref_write(base + i * step, size, d[i])
        tb.sub.expect_no_bus(f"block write size {size}")
        tb.mem_check(base - step, (n + 2) * step)
        v = await tb.dmi_read(SBADDRESS0)
        assert v == (base + n * step) & 0xFFFF_FFFF, f"sbaddress0 after block write {v:#010x}"

        # Block read: clear sbreadondata before the last read so nothing past the end is read
        tb.sub.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_val(size, readonaddr=True, readondata=True, autoinc=True))
        await tb.sba_set_addr(base)
        for i in range(n - 1):
            rd = await tb.sba_read_data(size)
            assert rd == d[i], f"block read size {size} [{i}] = {rd:#x}, expected {d[i]:#x}"
        await tb.sba_idle()
        await tb.dmi_write(SBCS, sbcs_val(size, autoinc=True))
        rd = await tb.sba_read_data(size)
        assert rd == d[n - 1], f"block read size {size} [{n - 1}] = {rd:#x}, expected {d[n - 1]:#x}"
        await tb.sba_idle()
        for i in range(n):
            tb.sub.expect_ar(base + i * step, size)
        tb.sub.expect_no_bus(f"block read size {size}")
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def errors(dut):
    """Misaligned and unsupported accesses never reach the bus; bus errors set sberror = 2."""
    tb = await _setup(dut, "errors")
    base = tb.region + 0x3000
    tb.mem_fill(base, 4 * tb.nbytes)

    # Misaligned: sberror = 3, no bus access
    for size in range(1, tb.max_size + 1):
        tb.sub.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_val(size, readonaddr=True))
        await tb.sba_set_addr(base + 1)
        await tb.sba_idle(3)
        tb.sub.expect_no_bus(f"misaligned read size {size}")
        # While sberror != 0 no access starts, even an aligned one
        await tb.sba_set_addr(base)
        await tb.sba_idle(3)
        tb.sub.expect_no_bus("read with sberror set")
        await tb.dmi_write(SBCS, sbcs_val(size) | SBCS_CLEAR_ERRORS)
        await tb.sba_idle()
        await tb.sba_set_addr(base + (1 << size) // 2)
        await tb.sba_write_data(size, 0x0123_4567_89AB_CDEF)
        await tb.sba_idle(3)
        tb.sub.expect_no_bus(f"misaligned write size {size}")
        await tb.dmi_write(SBCS, sbcs_val(size) | SBCS_CLEAR_ERRORS)
        await tb.sba_idle()

    # Unsupported size: sberror = 4, no bus access
    for size in range(tb.max_size + 1, 5):
        tb.sub.clear_monitor()
        await tb.dmi_write(SBCS, sbcs_val(size, readonaddr=True))
        await tb.sba_set_addr(base)
        await tb.sba_idle(4)
        tb.sub.expect_no_bus(f"unsupported size {size}")
        await tb.dmi_write(SBCS, sbcs_val(2) | SBCS_CLEAR_ERRORS)
        await tb.sba_idle()

    # Bus errors: sberror = 2 after exactly one transaction
    tb.sub.clear_monitor()
    tb.sub.rerr[base] = RESP_SLVERR
    await tb.dmi_write(SBCS, sbcs_val(2, readonaddr=True))
    await tb.sba_set_addr(base)
    await tb.sba_idle(2)
    tb.sub.expect_ar(base, 2)
    tb.sub.expect_no_bus("read SLVERR")
    del tb.sub.rerr[base]
    await tb.dmi_write(SBCS, sbcs_val(2) | SBCS_CLEAR_ERRORS)
    await tb.sba_idle()

    tb.sub.werr[base + 4] = RESP_DECERR
    await tb.dmi_write(SBCS, sbcs_val(2))
    await tb.sba_set_addr(base + 4)
    await tb.sba_write_data(2, 0x5A5A_A5A5)
    await tb.sba_idle(2)
    tb.sub.expect_aw_w(base + 4, 2, 0x5A5A_A5A5)
    tb.sub.expect_no_bus("write DECERR")
    del tb.sub.werr[base + 4]
    tb.ref_write(base + 4, 2, 0x5A5A_A5A5)  # the subordinate stores the data despite the error
    await tb.dmi_write(SBCS, sbcs_val(2) | SBCS_CLEAR_ERRORS)
    await tb.sba_idle()

    # The SBA still works after the errors
    tb.sub.clear_monitor()
    rd = await tb.sba_read(base, 2)
    tb.sub.expect_ar(base, 2)
    assert rd == tb.ref_read(base, 2), "read after errors"
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def sba_busy(dut):
    """An SBA access stalled on the bus: DM BUSY -> sticky TDR busy -> op 3 -> sbbusyerror."""
    tb = await _setup(dut, "sba_busy")
    base = tb.region + 0x4000
    tb.mem_fill(base, tb.nbytes)
    tb.sub.clear_monitor()
    tb.sub.stall = True
    await tb.dmi_write(SBCS, sbcs_val(2, readonaddr=True))
    await tb.sba_set_addr(base)  # starts the read, which the stall holds on AR
    _, st = await tb.dmi(OP_READ, SBDATA0, 0, max_polls=5, clear_busy=False)
    assert st == ST_BUSY, f"sbdata0 read during a stalled access: status {st}"
    await tb.dmi_clear()
    v = await tb.dmi_read(SBCS)
    assert (v >> SBBUSY) & 1 and (v >> SBBUSYERROR) & 1, f"sbcs {v:#010x}: expected sbbusy and sbbusyerror"
    tb.sub.stall = False
    await tb.sba_idle(0, True)
    await tb.dmi_write(SBCS, sbcs_val(2) | SBCS_CLEAR_ERRORS)
    await tb.sba_idle()
    rd = await tb.dmi_read(SBDATA0)
    assert rd == tb.ref_read(base, 2), f"data of the stalled read {rd:#010x}"
    tb.sub.expect_ar(base, 2)
    tb.sub.expect_no_bus("stalled read")
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def refused_op(dut):
    """i_clk stopped: the second op cannot be taken and is refused with sticky busy.

    As in dmi_jtag, a capture while a write is pending is not an error; the refusal shows up at
    the next capture.
    """
    tb = await _setup(dut, "refused_op")
    base = tb.region + 0x5000
    tb.mem_fill(base, 2 * tb.nbytes)
    await tb.dmi_write(SBCS, sbcs_val(2, autoinc=True))
    await tb.sba_set_addr(base)
    tb.sub.clear_monitor()
    tb.clk_en = False
    await Timer(4 * tb.clk_ps, "ps")
    await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0x1111_1111))  # A: accepted, waits for i_clk
    cap = await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0x2222_2222))  # B: refused
    assert cap & 3 == ST_OK, "capture while write A is pending is not ok"
    cap = await tb.scan(tdr_word(OP_NOP, 0, 0))
    assert cap & 3 == ST_BUSY, "refused op did not make status busy"
    tb.clk_en = True
    await Timer(50 * tb.clk_ps, "ps")
    for _ in range(3):
        cap = await tb.scan(tdr_word(OP_NOP, 0, 0))
        assert cap & 3 == ST_BUSY, "busy after a refused op is not sticky"
    await tb.dmi_clear()
    await tb.sba_idle()
    tb.sub.expect_aw_w(base, 2, 0x1111_1111)  # A only
    tb.sub.expect_no_bus("refused op")
    tb.ref_write(base, 2, 0x1111_1111)
    tb.mem_check(base, 2 * tb.nbytes)
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def pipelined(dut):
    """Next op in place of the NOP poll, with enough Run-Test/Idle for each result to cross back.

    One scan per 32-bit word.
    """
    tb = await _setup(dut, "pipelined")
    n = 8
    base = tb.region + 0x6000
    saved_idle = tb.host.idle_cycles
    tb.mem_fill(base, n * 4)
    await tb.dmi_write(SBCS, sbcs_val(2, readonaddr=True, readondata=True, autoinc=True))
    tb.sub.clear_monitor()
    await tb.sba_set_addr(base)  # starts word 0
    tb.host.idle_cycles = tb.host.pipe_idle
    # Ops: n-1 sbdata0 reads, clear sbreadondata, the last sbdata0 read, NOP.
    # The capture of each scan returns the previous op's result.
    await tb.scan(tdr_word(OP_READ, SBDATA0, 0))
    for i in range(1, n + 1):
        if i < n - 1:
            cap = await tb.scan(tdr_word(OP_READ, SBDATA0, 0))
        elif i == n - 1:
            cap = await tb.scan(tdr_word(OP_WRITE, SBCS, sbcs_val(2, autoinc=True)))
        else:
            cap = await tb.scan(tdr_word(OP_READ, SBDATA0, 0))
        assert cap & 3 == ST_OK, f"pipelined capture {i} status {cap & 3} (idle {tb.host.idle_cycles})"
        if i < n - 1:
            got = (cap >> 2) & 0xFFFF_FFFF
            assert got == tb.ref_read(base + 4 * (i - 1), 2), f"pipelined word {i - 1} = {got:#010x}"
    cap = await tb.scan(tdr_word(OP_NOP, 0, 0))
    assert cap & 3 == ST_OK, "pipelined last capture"
    got = (cap >> 2) & 0xFFFF_FFFF
    assert got == tb.ref_read(base + 4 * (n - 1), 2), f"pipelined word {n - 1} = {got:#010x}"
    tb.host.idle_cycles = saved_idle
    await tb.sba_idle()
    for i in range(n):
        tb.sub.expect_ar(base + 4 * i, 2)
    tb.sub.expect_no_bus("pipelined read")
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def resets(dut):
    """i_arst_n resets the bridge and the DM together: an op in flight is dropped and sticky busy clears."""
    tb = await _setup(dut, "resets")
    base = tb.region + 0x7000
    tb.mem_fill(base, 2 * tb.nbytes)
    await tb.dmi_write(SBCS, sbcs_val(2, autoinc=True))
    await tb.sba_set_addr(base)
    tb.sub.clear_monitor()
    # i_clk stopped: op A crosses no further than TCK, op B is refused, status goes busy
    tb.clk_en = False
    await Timer(4 * tb.clk_ps, "ps")
    await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0x1111_1111))
    await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0x2222_2222))
    cap = await tb.scan(tdr_word(OP_NOP, 0, 0))
    assert cap & 3 == ST_BUSY, "refused op did not make status busy"

    # Reset while op A is still waiting for i_clk
    dut.rst_n.value = 0
    await Timer(tb.clk_ps, "ps")
    tb.clk_en = True
    await ClockCycles(dut.clk, 10)
    for _ in range(3):
        await tb.host.tck_cycle()
    dut.rst_n.value = 1
    for _ in range(5):
        await tb.host.tck_cycle()  # TCK-side reset release
    await Timer(50 * tb.clk_ps, "ps")

    # The bridge's sticky status is cleared, and op A never reached the DM
    cap = await tb.scan(tdr_word(OP_NOP, 0, 0))
    assert cap & 3 == ST_OK, f"status {cap & 3} after i_arst_n: the bridge must be reset"
    tb.sub.expect_no_bus("op in flight at i_arst_n")
    # While dmactive = 0, dm_csrs holds sbcs at its register reset value (sbaccess = 2,
    # every other field 0); sbversion, sbasize and the supported sizes appear once
    # dmactive = 1, as after any other DM reset
    v = await tb.dmi_read(SBCS)
    assert v == SBCS_INACTIVE, f"sbcs after i_arst_n {v:#010x}, expected {SBCS_INACTIVE:#010x}"
    await tb.dmi_write(DMCONTROL, 1)
    v = await tb.dmi_read(SBCS)
    assert v == tb.sbcs_reset, f"sbcs after dmactive {v:#010x}, expected {tb.sbcs_reset:#010x}"
    tb.sub.clear_monitor()
    rd = await tb.sba_read(base, 2)
    tb.sub.expect_ar(base, 2)
    assert rd == tb.ref_read(base, 2), "read after i_arst_n"
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def busy_capture_retry(dut):
    """Back-to-back ops with no Run-Test/Idle: most captures still see the previous op pending and read busy.

    A busy capture refuses the op of the same scan, so the spec host flow (op 3, then resend)
    runs every op exactly once.
    """
    tb = await _setup(dut, "busy_capture_retry")
    n = 4
    base = tb.region + 0x8000
    saved_idle = tb.host.idle_cycles
    busy_seen = 0
    tb.mem_fill(base, 2 * n * 4)
    await tb.dmi_write(SBCS, sbcs_val(2, autoinc=True))
    await tb.sba_set_addr(base)
    tb.sub.clear_monitor()
    tb.host.idle_cycles = 0
    await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0xC0DE_0000))
    for i in range(1, n):
        cap = await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0xC0DE_0000 + i))
        while cap & 3 == ST_BUSY:
            busy_seen += 1
            assert busy_seen < 100, "busy capture retry does not converge"
            await tb.scan(tdr_word(OP_CLEAR, 0, 0))
            cap = await tb.scan(tdr_word(OP_WRITE, SBDATA0, 0xC0DE_0000 + i))  # resend
    tb.host.idle_cycles = saved_idle
    await tb.scan(tdr_word(OP_NOP, 0, 0))
    await tb.sba_idle()
    if busy_seen == 0:
        dut._log.warning("busy_capture_retry: no busy capture at idle 0 (inconclusive)")
    dut._log.info(f"busy_capture_retry: {busy_seen} busy captures")
    for i in range(n):
        tb.sub.expect_aw_w(base + 4 * i, 2, 0xC0DE_0000 + i)
        tb.ref_write(base + 4 * i, 2, 0xC0DE_0000 + i)
    tb.sub.expect_no_bus("busy capture retry")
    tb.mem_check(base, 2 * n * 4)
    tb.finish()
