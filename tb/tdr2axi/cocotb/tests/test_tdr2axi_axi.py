# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi AXI manager tests: request attributes and channel protocol on the bus port.

``tdr2axi_axi_attributes_test`` makes reads and writes of every size the bus carries, in a
seeded order at seeded addresses. ``AxiSub`` compares every AR, AW and W against the
build's AXI_PROT and AXI_CACHE and against ID and user 0 (CHK-AXI-REQ-ATTRS) and checks
AxCACHE is a legal encoding (CHK-AXISUB-CACHE-LEGAL); tb_top's assertions grade
CHK-AXI-REQ-ATTRS-SVA. A build whose AXI_PROT is 3'b010 and AXI_CACHE 4'b0000 cannot tell
a parameter from a constant, so the evidence for the feature comes from the PROT and CACHE
builds.

``tdr2axi_axi_protocol_test`` runs a seeded mix of single reads and writes against a
subordinate whose timing comes from the seed: READY waits of 1 to 4 cycles after VALID,
except on a quarter of the accesses where READY rises before VALID; R and B latencies,
with a quarter of the responses 8 to 12 cycles late; and AW and W order; with every
response input changed between i_clk edges. ``AxiSub`` grades the manager rules: VALID
held and payload stable until READY, VALID independent of READY, one single-beat
transaction outstanding, burst, size, strobe and 4 KiB rules on AXI4, and no combinational
path from i_axi_rsp to o_axi_req; and its own response order and IDs. The test draws this
timing whatever TDR2AXI_RANDOMIZE says. TDR2AXI_AXISUB_NEGATIVE set to ``early_r``,
``early_b``, ``bad_id`` or ``ready_now`` arms that subordinate fault in this test only.

Both run on any build; with an address space smaller than the test region they use the
whole space. Each access logs its direction, size, address, data and response. The
scoreboard compares every transaction with the SBA model, and each test pops each
transaction it makes, so CHK-AXISUB-DRAIN sees none left over. There is no SV twin: the
bench has one cocotb realization.
"""

from __future__ import annotations

from dataclasses import replace

import cocotb
from cocotb.handle import SimHandleBase

from env.axi_sub import AxiSubTiming, negative_fault
from tests.tdr2axi_base_test import TIMEOUT_MS, Tdr2axiTb, size_mask
from tests.tdr2axi_test_cfg import Tdr2axiTestCfg, rng_for

ATTRIBUTES_TEST = "tdr2axi_axi_attributes_test"
PROTOCOL_TEST = "tdr2axi_axi_protocol_test"

# Each direction at each size, this many times
ATTRIBUTE_ROUNDS = 2
ATTRIBUTE_OFFSET = 0x6000
ATTRIBUTE_SPAN = 0x100

PROTOCOL_ACCESSES = 32
PROTOCOL_OFFSET = 0x10000
# Four 4 KiB pages
PROTOCOL_SPAN = 0x4000
# Simulated-time bound of either test at the slowest i_clk the knobs draw, 8 times slower
# than TCK
AXI_TIMEOUT_MS = 4 * TIMEOUT_MS
READY_WAIT = (1, 4)
LATENCY_MAX = 6
READY_EARLY_SHARE = 0.25
LATE_SHARE = 0.25
# c_axi_r_late and c_axi_b_late need a response at least 8 cycles after its handshake
LATE_LATENCY = (8, 12)
PROTOCOL_FAULTS = frozenset({"early_r", "early_b", "bad_id", "ready_now"})


def _aligned(tb: Tdr2axiTb, base: int, span: int, size: int) -> int:
    """A seeded address in [base, base + span) aligned to 2**size bytes."""
    return base + tb.rng.randrange(0, span, 1 << size)


async def _write(tb: Tdr2axiTb, addr: int, size: int) -> None:
    """One single write of random data, its transaction popped and checked."""
    data = tb.rand_data(size)
    tb.clear_monitor()
    await tb.sba_write(addr, size, data)
    tb.log.info("write size %d @0x%x data 0x%x answered OKAY", size, addr, data)
    tb.expect_write(addr, size, data, check_id="CHK-SBA-SINGLE-WRITE")
    tb.expect_no_bus("CHK-SBA-SINGLE-WRITE", f"write size {size} @0x{addr:x}")
    tb.ref_write(addr, size, data)


async def _read(tb: Tdr2axiTb, addr: int, size: int) -> None:
    """One single read, its transaction popped and its data checked against the image."""
    nbytes = 1 << size
    if any(addr + i not in tb.ref_mem for i in range(nbytes)):
        tb.mem_fill(addr, nbytes)
    tb.clear_monitor()
    got = await tb.sba_read(addr, size)
    tb.log.info("read size %d @0x%x data 0x%x answered OKAY", size, addr, got)
    tb.expect_read(addr, size, check_id="CHK-SBA-SINGLE-READ")
    tb.expect_no_bus("CHK-SBA-SINGLE-READ", f"read size {size} @0x{addr:x}")
    tb.checker.expect_equal(
        "CHK-SBA-SINGLE-READ",
        got,
        tb.ref_read(addr, size) & size_mask(size),
        f"read size {size} @0x{addr:x}",
    )


def _require_both_directions(tb: Tdr2axiTb, mark: dict[str, int]) -> None:
    seen = {ch: tb.sub.handshakes[ch] - mark.get(ch, 0) for ch in ("ar", "aw", "w")}
    tb.checker.require_stimulus(
        created=seen["ar"] > 0 and seen["aw"] > 0 and seen["w"] > 0,
        reason=f"the mix made AR {seen['ar']} AW {seen['aw']} W {seen['w']} handshakes",
    )


@cocotb.test(timeout_time=AXI_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_axi_attributes_test(dut: SimHandleBase) -> None:
    """Reads and writes of every size: every request carries AXI_PROT, AXI_CACHE on AXI4,
    ID 0 and user 0, and a legal AxCACHE."""
    tb = await Tdr2axiTb.setup(dut, ATTRIBUTES_TEST)
    b = tb.build
    tb.log.info(
        "%s: AXI_PROT %s, AXI_CACHE %s, %s",
        ATTRIBUTES_TEST,
        f"{b.prot:03b}",
        f"{b.cache:04b}",
        "AXI4-Lite" if b.lite else "AXI4",
    )
    base, span = tb.address_range(ATTRIBUTE_OFFSET, ATTRIBUTE_SPAN)
    accesses = [
        (write, size)
        for _ in range(ATTRIBUTE_ROUNDS)
        for size in range(tb.max_size + 1)
        for write in (False, True)
    ]
    tb.rng.shuffle(accesses)
    mark = dict(tb.sub.handshakes)
    for write, size in accesses:
        addr = _aligned(tb, base, span, size)
        if write:
            await _write(tb, addr, size)
        else:
            await _read(tb, addr, size)
    _require_both_directions(tb, mark)
    tb.finish()


class _ProtocolTb(Tdr2axiTb):
    """The bench with a subordinate whose READY waits at least one cycle after VALID rises
    or, on a quarter of the accesses, rises before VALID, whose responses are late on a
    quarter of them, and whose response inputs change between i_clk edges; waits, latencies
    and AW and W order come from the seed.

    The timing replaces the knob timing whatever TDR2AXI_RANDOMIZE says, and the base
    test's seed line shows the knob timing; the line after it shows the timing that runs.
    """

    def __init__(self, dut: SimHandleBase, cfg: Tdr2axiTestCfg, name: str) -> None:
        rng = rng_for(cfg.seed, f"{name}:protocol_timing")
        timing = AxiSubTiming(
            ready_wait_max=rng.randint(*READY_WAIT),
            r_latency=(0, rng.randint(0, LATENCY_MAX)),
            b_latency=(0, rng.randint(0, LATENCY_MAX)),
            w_before_aw=rng.random() < 0.5,
            rsp_glitch=True,
            ready_early_share=READY_EARLY_SHARE,
            late_share=LATE_SHARE,
            late_latency=LATE_LATENCY,
        )
        super().__init__(dut, replace(cfg, axi=timing), name)
        self.log.info(
            "%s: subordinate timing %s, used instead of the knob timing in the seed line",
            name,
            timing,
        )
        self.sub.arm_fault(negative_fault(PROTOCOL_FAULTS))


@cocotb.test(timeout_time=AXI_TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_axi_protocol_test(dut: SimHandleBase) -> None:
    """A seeded mix of reads and writes against a stalling, reordering subordinate whose
    response inputs change between i_clk edges: the manager keeps every AXI channel rule,
    one single-beat transaction at a time, and o_axi_req changes only at i_clk edges."""
    tb = await _ProtocolTb.setup(dut, PROTOCOL_TEST)
    base, span = tb.address_range(PROTOCOL_OFFSET, PROTOCOL_SPAN)
    mark = dict(tb.sub.handshakes)
    for _ in range(PROTOCOL_ACCESSES):
        size = tb.rng.randint(0, tb.max_size)
        addr = _aligned(tb, base, span, size)
        if tb.rng.random() < 0.5:
            await _write(tb, addr, size)
        else:
            await _read(tb, addr, size)
    _require_both_directions(tb, mark)
    tb.finish()
