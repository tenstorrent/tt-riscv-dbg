# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Drivers for the tdr2axi cocotb tests: a TAP-state-accurate TDR/DMI host, an AXI subordinate and monitor."""

import hashlib
import os

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, Timer

# DMI register addresses
DMCONTROL, DMSTATUS, ABSTRACTCS, COMMAND = 0x10, 0x11, 0x16, 0x17
SBCS, SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1 = 0x38, 0x39, 0x3A, 0x3C, 0x3D

# TDR op / status
OP_NOP, OP_READ, OP_WRITE, OP_CLEAR = 0, 1, 2, 3
ST_OK, ST_BUSY = 0, 3

# sbcs fields
SBBUSYERROR, SBBUSY, SBREADONADDR = 22, 21, 20
SBACCESS_LSB, SBAUTOINCREMENT, SBREADONDATA, SBERROR_LSB = 17, 16, 15, 12
SBCS_CLEAR_ERRORS = 0x0040_7000
# sbcs register reset value, read while dmcontrol.dmactive = 0: sbaccess = 2, all else 0
SBCS_INACTIVE = 0x0004_0000

RESP_SLVERR, RESP_DECERR = 2, 3

TDR_LEN = 41
MAX_POLLS = 20


def tdr_word(op, addr, data):
    """Pack a 41-bit TDR scan word: addr[40:34], data[33:2], op[1:0]."""
    return (addr << 34) | (data << 2) | op


def sbcs_val(size, readonaddr=False, readondata=False, autoinc=False):
    """sbcs write value with the given access size and read/auto-increment controls."""
    return (int(readonaddr) << SBREADONADDR) | (size << SBACCESS_LSB) | (int(autoinc) << SBAUTOINCREMENT) | (
        int(readondata) << SBREADONDATA
    )


def size_mask(size):
    """Mask of the bits an access of 2**size bytes carries."""
    return (1 << (8 << size)) - 1


_stim_count = 0


def randbits(nbits):
    """Pseudo-random test stimulus: SHA-256 in counter mode, seeded from cocotb.RANDOM_SEED.

    Deterministic on purpose, so a failing run replays with `make RANDOM_SEED=<seed>`
    (cocotb prints the seed at start-up). Stimulus data only, never a secret.
    """
    global _stim_count
    nbytes = (nbits + 7) // 8
    buf = b""
    while len(buf) < nbytes:
        key = f"{getattr(cocotb, 'RANDOM_SEED', 0)}:{_stim_count}".encode()
        buf += hashlib.sha256(key).digest()
        _stim_count += 1
    return int.from_bytes(buf[:nbytes], "little") & ((1 << nbits) - 1)


def rand64():
    """Random 64-bit value."""
    return randbits(64)


def known_int(sig):
    """int(sig), failing with the bit string when it holds X or Z."""
    raw = sig.value
    assert raw.is_resolvable, f"{sig._name} is {raw.binstr}"
    return int(raw)


class AxiSub:
    """AXI4 / AXI4-Lite subordinate and monitor on the flat tb signals.

    Memory is a sparse byte dict reading zero when uninitialised. Every handshake at the DUT port is
    recorded; valid and payload must stay stable until ready. stall holds all three request readys low.
    AXI4 reads return random data on lanes outside the access, as a real subordinate may: the bridge
    must not depend on them. AXI4-Lite reads return the whole bus word.
    """

    def __init__(self, dut, lite, data_width, latency=1):
        """Bind to a tb top; call start() to run it."""
        self.dut = dut
        self.lite = lite
        self.nbytes = data_width // 8
        self.latency = latency
        self.mem = {}
        self.rerr = {}
        self.werr = {}
        self.stall = False
        self.errors = []
        self.ar_q, self.aw_q, self.w_q = [], [], []
        self._reset_state()

    def _reset_state(self):
        self.ar_rdy = self.aw_rdy = self.w_rdy = False
        self.r_vld = self.b_vld = False
        self.rd_pend = None
        self.wr_pend = None
        self.aw_hold, self.w_hold = [], []
        self.hold = {"ar": None, "aw": None, "w": None}

    def drive_idle(self):
        """Idle every subordinate output."""
        d = self.dut
        for sig in (d.ar_ready, d.aw_ready, d.w_ready, d.r_valid, d.b_valid):
            sig.value = 0
        d.r_data.value = 0
        d.r_resp.value = 0
        d.b_resp.value = 0

    def start(self):
        """Run the subordinate in the background."""
        self.drive_idle()
        return cocotb.start_soon(self._run())

    def error(self, msg):
        """Record a protocol or check failure; the test fails at finish()."""
        self.errors.append(msg)
        self.dut._log.error(msg)

    def _read_lanes(self, addr, size):
        base = addr & ~(self.nbytes - 1)
        off = addr % self.nbytes
        data = 0
        for i in range(self.nbytes):
            if self.lite or off <= i < off + (1 << size):
                byte = self.mem.get(base + i, 0)
            else:
                byte = randbits(8)
            data |= byte << (8 * i)
        return data

    def _write(self, addr, strb, data):
        base = addr & ~(self.nbytes - 1)
        for i in range(self.nbytes):
            if (strb >> i) & 1:
                self.mem[base + i] = (data >> (8 * i)) & 0xFF

    async def _run(self):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            if int(d.rst_n.value) == 0:
                self._reset_state()
                self.drive_idle()
                continue
            ar_v, aw_v, w_v = (known_int(s) for s in (d.ar_valid, d.aw_valid, d.w_valid))
            r_rdy, b_rdy = known_int(d.r_ready), known_int(d.b_ready)
            ar = (known_int(d.ar_addr), known_int(d.ar_size), known_int(d.ar_len)) if ar_v else None
            aw = (known_int(d.aw_addr), known_int(d.aw_size), known_int(d.aw_len)) if aw_v else None
            w = (known_int(d.w_data), known_int(d.w_strb)) if w_v else None

            # valid and payload hold until ready
            for name, cur, prev in (("AR", ar, self.hold["ar"]), ("AW", aw, self.hold["aw"]), ("W", w, self.hold["w"])):
                if prev is not None and cur != prev:
                    self.error(f"{name} changed before ready: {prev} -> {cur}")
            self.hold["ar"] = ar if ar_v and not self.ar_rdy else None
            self.hold["aw"] = aw if aw_v and not self.aw_rdy else None
            self.hold["w"] = w if w_v and not self.w_rdy else None

            # Handshakes at this edge
            if self.r_vld and r_rdy:
                self.r_vld = False
            if self.b_vld and b_rdy:
                self.b_vld = False
            if ar_v and self.ar_rdy:
                self.ar_q.append(ar)
                self.rd_pend = [ar, self.latency]
            if aw_v and self.aw_rdy:
                self.aw_q.append(aw)
                self.aw_hold.append(aw)
            if w_v and self.w_rdy:
                self.w_q.append(w)
                self.w_hold.append(w)
            if self.aw_hold and self.w_hold and self.wr_pend is None:
                (addr, _, _), (data, strb) = self.aw_hold.pop(0), self.w_hold.pop(0)
                self._write(addr, strb, data)
                resp = self.werr.get(addr, 0)
                self.wr_pend = [resp, self.latency]

            # Responses
            if self.rd_pend is not None and not self.r_vld:
                if self.rd_pend[1] <= 0:
                    addr, size, _ = self.rd_pend[0]
                    d.r_data.value = self._read_lanes(addr, size)
                    d.r_resp.value = self.rerr.get(addr, 0)
                    self.r_vld = True
                    self.rd_pend = None
                else:
                    self.rd_pend[1] -= 1
            if self.wr_pend is not None and not self.b_vld:
                if self.wr_pend[1] <= 0:
                    d.b_resp.value = self.wr_pend[0]
                    self.b_vld = True
                    self.wr_pend = None
                else:
                    self.wr_pend[1] -= 1

            # Readys for the next cycle: one request of each kind in flight
            free = not self.stall
            self.ar_rdy = free and self.rd_pend is None and not self.r_vld
            self.aw_rdy = free and not self.aw_hold and self.wr_pend is None and not self.b_vld
            self.w_rdy = free and not self.w_hold and self.wr_pend is None and not self.b_vld
            d.ar_ready.value = int(self.ar_rdy)
            d.aw_ready.value = int(self.aw_rdy)
            d.w_ready.value = int(self.w_rdy)
            d.r_valid.value = int(self.r_vld)
            d.b_valid.value = int(self.b_vld)

    # --- Checks -------------------------------------------------------------------------
    def clear_monitor(self):
        """Forget the recorded transactions."""
        self.ar_q.clear()
        self.aw_q.clear()
        self.w_q.clear()

    def expect_no_bus(self, what):
        """No transaction is left unchecked in the monitor."""
        if self.ar_q or self.aw_q or self.w_q:
            self.error(
                f"{what}: expected no AXI transaction, saw AR {len(self.ar_q)} AW {len(self.aw_q)} W {len(self.w_q)}"
            )

    def expect_ar(self, addr, size):
        """Pop the next AR: exact address, AxSIZE = size and a single beat in AXI4."""
        if not self.ar_q:
            self.error(f"read {addr:#x}: no AR")
            return
        a, s, ln = self.ar_q.pop(0)
        if a != addr:
            self.error(f"AR addr {a:#x}, expected {addr:#x}")
        if not self.lite:
            if s != size:
                self.error(f"AR size {s}, expected {size}")
            if ln != 0:
                self.error(f"AR len {ln}, expected 0")

    def expect_aw_w(self, addr, size, data):
        """Pop the next AW and W: address, AxSIZE, strobes of exactly the accessed lanes and their bytes."""
        if not self.aw_q or not self.w_q:
            self.error(f"write {addr:#x}: AW {len(self.aw_q)} W {len(self.w_q)}")
            return
        a, s, ln = self.aw_q.pop(0)
        wdata, strb = self.w_q.pop(0)
        off = addr % self.nbytes
        exp_strb = ((1 << (1 << size)) - 1) << off
        if a != addr:
            self.error(f"AW addr {a:#x}, expected {addr:#x}")
        if not self.lite:
            if s != size:
                self.error(f"AW size {s}, expected {size}")
            if ln != 0:
                self.error(f"AW len {ln}, expected 0")
        if strb != exp_strb:
            self.error(f"WSTRB {strb:#x}, expected {exp_strb:#x}")
        for i in range(1 << size):
            got, exp = (wdata >> (8 * (off + i))) & 0xFF, (data >> (8 * i)) & 0xFF
            if got != exp:
                self.error(f"W lane {off + i} = {got:#04x}, expected {exp:#04x}")


class TdrHost:
    """TAP-state-accurate model of the DMI TDR plus the OpenOCD-style DMI flow on top of it.

    Each scan walks Select-DR, Capture-DR (TDR_in sampled, capture strobe high), Shift-DR
    (41 + pad cycles), Exit1-DR and Update-DR (update stage and TDR_upd change on the falling
    edge inside it), then idle_cycles of Run-Test/Idle. TCK only runs during scans.
    """

    def __init__(self, dut, clk_ps, tck_ps, pad_bits=7):
        """Bind to a tb top."""
        self.dut = dut
        self.clk_ps = clk_ps
        self.tck_ps = tck_ps
        self.scan_len = TDR_LEN + pad_bits
        self.idle_cycles = 4
        self.busy_retries = 0
        # Run-Test/Idle cycles after each Update-DR that let a result cross back before the
        # next Capture-DR, for hosts that send the next op in place of the NOP poll
        self.pipe_idle = 6 + (16 * clk_ps + tck_ps - 1) // tck_ps

    async def tck_cycle(self):
        """One TCK period."""
        await Timer(self.tck_ps // 2, "ps")
        self.dut.tck.value = 1
        await Timer(self.tck_ps // 2, "ps")
        self.dut.tck.value = 0

    async def scan(self, din):
        """One DR scan from Run-Test/Idle back to Run-Test/Idle; returns the captured word."""
        d = self.dut
        await self.tck_cycle()  # Run-Test/Idle -> Select-DR-Scan
        await self.tck_cycle()  # Select-DR-Scan -> Capture-DR
        # TDR_in depends on the capture strobe (busy raised by this capture is reported in it),
        # so sample after the strobe has settled
        d.tdr_cap.value = 1
        await Timer(1, "ps")
        dout = known_int(d.tdr_in)
        await self.tck_cycle()  # Capture-DR -> Shift-DR
        d.tdr_cap.value = 0
        for _ in range(self.scan_len - 1):
            await self.tck_cycle()  # Shift-DR
        await self.tck_cycle()  # last shift, Shift-DR -> Exit1-DR
        await self.tck_cycle()  # Exit1-DR -> Update-DR
        d.tdr_upd_q.value = din
        d.tdr_upd.value = 1
        await self.tck_cycle()  # Update-DR -> Run-Test/Idle: the bridge samples TDR_upd
        d.tdr_upd.value = 0
        for _ in range(self.idle_cycles):
            await self.tck_cycle()
        return dout

    async def _busy_backoff(self):
        """OpenOCD dmi_busy_delay: clear with op 3 and raise the idle count, kept for later accesses."""
        self.busy_retries += 1
        await self.scan(tdr_word(OP_CLEAR, 0, 0))
        self.idle_cycles += self.idle_cycles // 10 + 1

    async def dmi(self, op, addr, wdata=0, max_polls=MAX_POLLS, clear_busy=True):
        """DMI op flow on dmi_jtag semantics: the op scan, then a NOP poll; returns (rdata, status).

        A busy capture on either means the op may not have run: with clear_busy the host clears with
        op 3 and resends. OK after a write means no error so far, not that the write has completed.
        """
        cap = 0
        for _ in range(max_polls):
            cap = await self.scan(tdr_word(op, addr, wdata))
            if clear_busy and (cap & 3) == ST_BUSY:
                await self._busy_backoff()
                continue
            cap = await self.scan(tdr_word(OP_NOP, 0, 0))
            if not clear_busy or (cap & 3) != ST_BUSY:
                break
            await self._busy_backoff()
        rdata, status = (cap >> 2) & 0xFFFF_FFFF, cap & 3
        if status == ST_OK and op == OP_READ:
            assert cap >> 34 == addr, f"capture addr {cap >> 34:#04x}, expected {addr:#04x}"
        return rdata, status

    async def dmi_write(self, addr, data):
        """DMI write, expecting OK."""
        _, st = await self.dmi(OP_WRITE, addr, data)
        assert st == ST_OK, f"DMI write {addr:#04x} status {st}"

    async def dmi_read(self, addr):
        """DMI read, expecting OK; returns the data."""
        data, st = await self.dmi(OP_READ, addr)
        assert st == ST_OK, f"DMI read {addr:#04x} status {st}"
        return data

    async def dmi_clear(self):
        """Op 3 is handled in TCK: the next capture is OK unless an op is still pending."""
        await self.scan(tdr_word(OP_CLEAR, 0, 0))
        cap = await self.scan(tdr_word(OP_NOP, 0, 0))
        assert (cap & 3) == ST_OK, f"status {cap & 3} after op 3"


class Tb:
    """Clocks, reset, TDR host, AXI subordinate and the SBA helpers for one test."""

    def __init__(self, dut):
        """Read the build configuration from the tb top and the clock periods from the environment."""
        self.dut = dut
        self.lite = int(dut.cfg_axi_lite.value)
        self.addr_width = int(dut.cfg_addr_width.value)
        self.data_width = int(dut.cfg_data_width.value)
        self.nbytes = self.data_width // 8
        self.max_size = self.nbytes.bit_length() - 1
        self.clk_ps = int(os.environ.get("CLK_PERIOD_PS", "1000"))
        self.tck_ps = int(os.environ.get("TCK_PERIOD_PS", "20000"))
        self.clk_en = True
        self.host = TdrHost(dut, self.clk_ps, self.tck_ps)
        self.sub = AxiSub(dut, self.lite, self.data_width)
        self.ref_mem = {}
        self.sbcs_reset = 0x2004_0000 | (self.addr_width << 5) | (0x0F if self.max_size == 3 else 0x07)
        # Test regions above 4 GiB when the address is wide enough, so sbaddress1 is used
        self.region = 0x1_0000_0000 if self.addr_width > 32 else 0

    async def start(self):
        """Idle the inputs, start clocks and the subordinate, reset, and set dmactive."""
        d = self.dut
        d.clk.value = 0
        d.rst_n.value = 0
        d.tck.value = 0
        d.tdr_upd_q.value = 0
        d.tdr_upd.value = 0
        d.tdr_cap.value = 0
        self.sub.start()
        cocotb.start_soon(self._clock())
        await ClockCycles(d.clk, 10)
        d.rst_n.value = 1
        for _ in range(5):
            await self.host.tck_cycle()  # TCK-side reset release
        await self.dmi_write(DMCONTROL, 1)

    async def _clock(self):
        """i_clk, paused low when clk_en drops. The phase offset keeps its edges off the TCK grid."""
        clk = self.dut.clk
        level = 0
        await Timer(137, "ps")
        while True:
            await Timer(self.clk_ps // 2, "ps")
            if self.clk_en or level:
                level ^= 1
                clk.value = level

    def finish(self):
        """Fail the test on anything the monitor flagged."""
        assert not self.sub.errors, "\n".join(self.sub.errors)

    # --- DMI / TDR passthrough ----------------------------------------------------------
    async def scan(self, din):
        """One DR scan."""
        return await self.host.scan(din)

    async def dmi(self, *args, **kwargs):
        """One DMI op, see TdrHost.dmi."""
        return await self.host.dmi(*args, **kwargs)

    async def dmi_write(self, addr, data):
        """DMI write, expecting OK."""
        await self.host.dmi_write(addr, data)

    async def dmi_read(self, addr):
        """DMI read, expecting OK."""
        return await self.host.dmi_read(addr)

    async def dmi_clear(self):
        """Op 3 and check the next capture."""
        await self.host.dmi_clear()

    # --- Memory: byte-wise reference model, preloaded into the subordinate ----------------
    def mem_fill(self, base, nbytes):
        """Random bytes in both the subordinate memory and the reference."""
        for i in range(nbytes):
            b = randbits(8)
            self.ref_mem[base + i] = b
            self.sub.mem[base + i] = b

    def ref_read(self, addr, size):
        """Reference value of 2**size bytes at addr."""
        return sum(self.ref_mem[addr + i] << (8 * i) for i in range(1 << size))

    def ref_write(self, addr, size, data):
        """Update the reference with an access of 2**size bytes."""
        for i in range(1 << size):
            self.ref_mem[addr + i] = (data >> (8 * i)) & 0xFF

    def mem_check(self, base, nbytes):
        """Every byte in [base, base + nbytes) of the subordinate matches the reference."""
        for i in range(nbytes):
            got = self.sub.mem.get(base + i)
            assert got == self.ref_mem[base + i], (
                f"mem[{base + i:#x}] = {got}, expected {self.ref_mem[base + i]:#04x}"
            )

    # --- SBA helpers ----------------------------------------------------------------------
    async def sba_set_addr(self, addr):
        """Program sbaddress1 (when the address is wider than 32 bits) and sbaddress0."""
        if self.addr_width > 32:
            await self.dmi_write(SBADDRESS1, addr >> 32)
        await self.dmi_write(SBADDRESS0, addr & 0xFFFF_FFFF)

    async def sba_idle(self, exp_err=0, exp_busyerr=False):
        """Poll sbcs until sbbusy = 0 and check sberror / sbbusyerror."""
        for _ in range(MAX_POLLS):
            v = await self.dmi_read(SBCS)
            if not (v >> SBBUSY) & 1:
                break
        assert not (v >> SBBUSY) & 1, "sbbusy stuck"
        err = (v >> SBERROR_LSB) & 7
        assert err == exp_err, f"sberror {err}, expected {exp_err}"
        assert bool((v >> SBBUSYERROR) & 1) == exp_busyerr, f"sbbusyerror {(v >> SBBUSYERROR) & 1}, expected {int(exp_busyerr)}"

    async def sba_write_data(self, size, data):
        """sbdata1 (64-bit only) then sbdata0, which starts the write."""
        if size == 3:
            await self.dmi_write(SBDATA1, data >> 32)
        await self.dmi_write(SBDATA0, data & 0xFFFF_FFFF)

    async def sba_read_data(self, size):
        """sbdata1 first (it does not start a read), then sbdata0."""
        hi = await self.dmi_read(SBDATA1) if size == 3 else 0
        lo = await self.dmi_read(SBDATA0)
        return ((hi << 32) | lo) & size_mask(size)

    async def sba_write(self, addr, size, data):
        """Single write, as in the programming examples of doc/tdr2axi.md."""
        await self.dmi_write(SBCS, sbcs_val(size))
        await self.sba_set_addr(addr)
        await self.sba_write_data(size, data)
        await self.sba_idle()

    async def sba_read(self, addr, size):
        """Single read, as in the programming examples of doc/tdr2axi.md."""
        await self.dmi_write(SBCS, sbcs_val(size, readonaddr=True))
        await self.sba_set_addr(addr)
        data = await self.sba_read_data(size)
        await self.sba_idle()
        return data
