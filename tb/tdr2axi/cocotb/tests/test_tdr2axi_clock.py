# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi clock tests: the same operations give the same results at every clock ratio.

``tdr2axi_clock_ratio_test`` runs one seeded mix of single and block SBA reads and writes
and DMI register accesses on the bench of ``Tdr2axiTb``. The mix, its sizes, addresses and
data come from the stimulus stream of the seed alone, so a seed replays the same mix at
every i_clk to TCK ratio (``--ratio``) on one build; the host's Run-Test/Idle count, drawn
from N to 2N per scan, follows the ratio. Every read and every AR, AW and W is compared with
the test's image of the subordinate memory, and the memory with the image at the end
(CHK-TDR-RATIO-INVARIANT, S-TDR2AXI-0048); the scoreboard grades the same check on the DM
answers. ``Tdr2axiTimingMonitor`` counts each operation's round trip from its Update-DR and
grades it against the round-trip item of the build's SYNC_STAGES and the run's named ratio
(CHK-TDR-ROUND-TRIP-SS<n>-<ratio>, S-TDR2AXI-0049); a drawn ratio requires none of them.
At a named ratio the same check also requires that no capture of the mix read busy: the
host resent no operation. ``TDR2AXI_RATIO_DIGEST`` logs a digest of every read result and
the final memory, equal across the ratios of one seed and build.

``NEGATIVE_KNOB`` names one fault per run and defaults off, and an armed fault logs a
warning: ``drain`` ends the test with one read whose AR is left unchecked in the
subordinate's monitor (CHK-AXISUB-DRAIN); ``flip`` inverts bit 0 of every byte of the
region in the subordinate's memory after the fill, so the test's comparisons with its image
fail (CHK-TDR-RATIO-INVARIANT).
"""

from __future__ import annotations

import hashlib
import os

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.utils import get_sim_time

from env.tdr_host import DMCONTROL, SBADDRESS0, SBCS, sbcs_value
from tests.tdr2axi_base_test import TIMEOUT_MS, Tdr2axiTb
from tests.tdr2axi_timing_monitor import ROUND_TRIP_EDGES, ROUND_TRIP_PREFIX

NAME = "tdr2axi_clock_ratio_test"
RATIO_INVARIANT = "CHK-TDR-RATIO-INVARIANT"

NEGATIVE_KNOB = "TDR2AXI_CLOCK_RATIO_NEGATIVE"
FAULTS = frozenset({"drain", "flip"})
NEGATIVE = os.environ.get(NEGATIVE_KNOB, "")
LOOPS_KNOB = "TDR2AXI_CLOCK_RATIO_TEST_LOOPS"
DEFAULT_LOOPS = 7

REGION_OFFSET = 0x3000
REGION_BYTES = 0x100
BLOCK_MIN = 2
BLOCK_MAX = 4
WORD_SIZE = 2
DMACTIVE = 1
KINDS = ("single_write", "single_read", "block_write", "block_read", "registers")


def _fault(tb: Tdr2axiTb, name: str) -> bool:
    """Whether this run arms fault ``name`` of ``NEGATIVE_KNOB``; an armed fault logs a
    warning when it acts."""
    if NEGATIVE and NEGATIVE not in FAULTS:
        raise ValueError(f"{NEGATIVE_KNOB}={NEGATIVE}: use one of {sorted(FAULTS)}")
    if NEGATIVE != name:
        return False
    tb.log.warning("%s: %s=%s armed at %.0f ns", NAME, NEGATIVE_KNOB, name, get_sim_time("ns"))
    return True


def _loops() -> int:
    return int(os.environ.get(LOOPS_KNOB, str(DEFAULT_LOOPS)))


class _RatioMix:
    """The seeded mix on one region of the subordinate, and the digest of its results."""

    def __init__(self, tb: Tdr2axiTb) -> None:
        self.tb = tb
        self.base = tb.region + REGION_OFFSET
        self._digest = hashlib.sha256()
        tb.mem_fill(self.base, REGION_BYTES)
        if _fault(tb, "flip"):
            for addr in range(self.base, self.base + REGION_BYTES):
                tb.sub.mem[addr] ^= 1

    async def run(self, kind: str) -> None:
        """One step of the mix, a member of ``KINDS``."""
        await getattr(self, f"_{kind}")()

    async def unchecked_read(self) -> None:
        """A single read whose AR stays in the subordinate's monitor."""
        size = self._size()
        await self.tb.sba_read(self._addr(size, 1), size)

    def finish(self) -> str:
        """Compare the subordinate memory with the image; returns the digest."""
        tb = self.tb
        tb.mem_check(self.base, REGION_BYTES, check_id=RATIO_INVARIANT)
        self._note(*(tb.sub.mem[self.base + i] for i in range(REGION_BYTES)))
        return self._digest.hexdigest()

    # --- Private: one step each -----------------------------------------------------------
    async def _single_write(self) -> None:
        tb = self.tb
        size = self._size()
        addr = self._addr(size, 1)
        data = tb.rand_data(size)
        self._log("single write", size, addr, 1)
        await tb.sba_write(addr, size, data)
        tb.ref_write(addr, size, data)
        tb.expect_write(addr, size, data, check_id=RATIO_INVARIANT)

    async def _single_read(self) -> None:
        tb = self.tb
        size = self._size()
        addr = self._addr(size, 1)
        self._log("single read", size, addr, 1)
        got = await tb.sba_read(addr, size)
        tb.expect_read(addr, size, check_id=RATIO_INVARIANT)
        self._expect_data(got, addr, size, f"single read of 0x{addr:x}")

    async def _block_write(self) -> None:
        tb = self.tb
        size = self._size()
        count = tb.rng.randint(BLOCK_MIN, BLOCK_MAX)
        start = self._addr(size, count)
        data = [tb.rand_data(size) for _ in range(count)]
        self._log("block write", size, start, count)
        await tb.sba_block_write(start, size, data)
        for index, value in enumerate(data):
            addr = start + (index << size)
            tb.ref_write(addr, size, value)
            tb.expect_write(addr, size, value, check_id=RATIO_INVARIANT)

    async def _block_read(self) -> None:
        tb = self.tb
        size = self._size()
        count = tb.rng.randint(BLOCK_MIN, BLOCK_MAX)
        start = self._addr(size, count)
        self._log("block read", size, start, count)
        got = await tb.sba_block_read(start, size, count)
        for index, value in enumerate(got):
            addr = start + (index << size)
            tb.expect_read(addr, size, check_id=RATIO_INVARIANT)
            self._expect_data(value, addr, size, f"block read element {index} at 0x{addr:x}")

    async def _registers(self) -> None:
        """sbaddress0 written with sbreadonaddr clear reads back and starts no access, and
        dmcontrol keeps dmactive."""
        tb = self.tb
        await tb.dmi_write(SBCS, sbcs_value(WORD_SIZE))
        value = tb.rng.getrandbits(32)
        tb.log.info("%s: sbaddress0 write of 0x%08x and read back, dmcontrol read", NAME, value)
        mark = tb.bus_mark()
        await tb.dmi_write(SBADDRESS0, value)
        got = await tb.dmi_read(SBADDRESS0)
        tb.expect_no_bus_since(mark, RATIO_INVARIANT, "sbaddress0 write with sbreadonaddr clear")
        tb.checker.expect_equal(RATIO_INVARIANT, got, value, "sbaddress0 read back")
        dmactive = await tb.dmi_read(DMCONTROL) & DMACTIVE
        tb.checker.expect_equal(RATIO_INVARIANT, dmactive, DMACTIVE, "dmcontrol.dmactive")
        self._note(SBADDRESS0, got, dmactive)

    # --- Private: helpers -----------------------------------------------------------------
    def _addr(self, size: int, count: int) -> int:
        """A start address of ``count`` accesses of 2**size bytes inside the region."""
        slots = REGION_BYTES >> size
        return self.base + (self.tb.rng.randrange(slots - count + 1) << size)

    def _size(self) -> int:
        return self.tb.rng.randint(0, self.tb.max_size)

    def _log(self, what: str, size: int, addr: int, count: int) -> None:
        self.tb.log.info("%s: %s, %d x %d-bit from 0x%x", NAME, what, count, 8 << size, addr)

    def _note(self, *values: int) -> None:
        self._digest.update(repr(values).encode())

    def _expect_data(self, got: int, addr: int, size: int, what: str) -> None:
        self.tb.checker.expect_equal(RATIO_INVARIANT, got, self.tb.ref_read(addr, size), what)
        self._note(addr, size, got)


def _expect_no_busy(tb: Tdr2axiTb, retries: int) -> None:
    """At a named ratio with a round-trip item, no capture of the mix read busy: the host's
    Run-Test/Idle count covers the round trip, so the host resent nothing."""
    ratio = None if tb.cfg.clk_drawn else tb.timing.ratio
    sync_stages = tb.build.sync_stages
    if ratio is None or (sync_stages, ratio) not in ROUND_TRIP_EDGES:
        return
    tb.checker.expect_equal(
        f"{ROUND_TRIP_PREFIX}{sync_stages}-{ratio}",
        retries,
        0,
        f"operations the host resent after a busy capture at {ratio}",
    )


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_clock_ratio_test(dut: SimHandleBase) -> None:
    """The seeded mix gives the image's results at the run's ratio, and each round trip at
    a named ratio lies within its timing item."""
    tb = await Tdr2axiTb.setup(dut, NAME)
    loops = _loops()
    tb.checker.require_stimulus(
        created=loops >= len(KINDS),
        reason=f"{LOOPS_KNOB}={loops} runs fewer steps than the {len(KINDS)} kinds",
    )
    mix = _RatioMix(tb)
    kinds = list(KINDS)
    tb.rng.shuffle(kinds)
    kinds += [tb.rng.choice(KINDS) for _ in range(loops - len(KINDS))]
    retries = tb.host.busy_retries
    for loop, kind in enumerate(kinds, 1):
        tb.log.info("%s: loop %d of %d, %s", NAME, loop, loops, kind)
        await mix.run(kind)
    _expect_no_busy(tb, tb.host.busy_retries - retries)
    if _fault(tb, "drain"):
        await mix.unchecked_read()
    digest = mix.finish()
    tb.log.info(
        "TDR2AXI_RATIO_DIGEST test=%s seed=%d build=%s sha256=%s",
        NAME,
        tb.cfg.seed,
        tb.build,
        digest,
    )
    tb.finish()
