# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""AXI4 / AXI4-Lite subordinate and monitor of the tdr2axi bench.

``AxiSub`` answers the DUT's manager port on the flattened AXI nets of tb_top. Memory is a
sparse byte map reading zero where unwritten. Every handshake is recorded for the test to
pop, and every request is checked at the port: the AXI rules a subordinate relies on
(the AXISUB checks), the tdr2axi manager rules (CHK-AXI-ONE-OUTSTANDING, CHK-AXI-REQ-ATTRS,
CHK-AXI-NO-COMB-PATH, CHK-DM-DMACTIVE-INFLIGHT) and the bench rules of a fabric reset and a
side-effecting read (CHK-ENV-FABRIC-RESET, CHK-ENV-READONDATA-IDLE).

Timing comes from ``AxiSubTiming``: READY held low for a seeded number of cycles once VALID
is seen, or on a seeded share of accesses raised before VALID; response latency drawn per
transaction, a seeded share of responses late; W accepted before AW; and response inputs
changed between i_clk edges. The default is READY high on a free channel and a response two
edges after the handshake. AXI4 reads return seeded random bytes on lanes outside the
access, as a real subordinate may; AXI4-Lite reads return the whole bus word.

Monitor checks count passes and report each check once at ``finalize``; a failure is
reported when it happens. ``add_transaction_callback`` publishes every request handshake to
an observer such as a scoreboard, independent of the queues a test pops.

The negative knob TDR2AXI_AXISUB_NEGATIVE is off by default, and no testlist entry may arm
it. The subordinate never reads it: a test that grades a fault's check passes the faults it
grades to ``negative_fault`` and the result to ``arm_fault``, so in every other test the
variable arms nothing. An armed fault makes the subordinate misbehave in one way and logs
``NEGATIVE VALIDATION:``. ``early_r`` raises RVALID once while the first AR waits for
ARREADY (CHK-AXISUB-READ-RESP-ORDER). ``early_b`` raises BVALID once while the first AW
waits for AWREADY (CHK-AXISUB-WRITE-RESP-ORDER). ``bad_id`` answers with RID and BID one off
the request's ID (CHK-AXISUB-ID-ORDER). ``ready_now`` drives READY high on every free
channel whatever the timing, so no VALID waits (CHK-NONVAC in a test that requires the
held-VALID rules). ``lost_write`` answers OKAY to the first write that would get OKAY
without storing its data (CHK-SBA-WRITE-MEMORY). There is no SV twin: the bench has one
cocotb realization.
"""

from __future__ import annotations

import logging
import os
import random
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from env.tdr2axi_predictor import entered_by_mode_change, in_reset, reset_input
from env.tdr_host import Reporter, resolve

__all__ = [
    "AXI4_CACHE_LEGAL",
    "FAULTS",
    "NEGATIVE_KNOB",
    "RESP_DECERR",
    "RESP_OKAY",
    "RESP_SLVERR",
    "AxiAddr",
    "AxiSub",
    "AxiSubCfg",
    "AxiSubTiming",
    "AxiW",
    "Observer",
    "negative_fault",
]

RESP_OKAY = 0
RESP_SLVERR = 2
RESP_DECERR = 3

BURST_FIXED = 0
BURST_INCR = 1
BURST_WRAP = 2

# AxCACHE memory-type encodings of AMBA AXI (IHI 0022, Table A4-5); the rest are reserved
AXI4_CACHE_LEGAL = frozenset(
    {0b0000, 0b0001, 0b0010, 0b0011, 0b0110, 0b0111, 0b1010, 0b1011, 0b1110, 0b1111}
)

PAGE_4K = 4096

# i_clk cycles sbbusy may stay high with every request VALID low and no response owed
VALID_WAIT_LIMIT = 256

CHANNELS = ("ar", "aw", "w")

NEGATIVE_KNOB = "TDR2AXI_AXISUB_NEGATIVE"
FAULTS = frozenset({"early_r", "early_b", "bad_id", "ready_now", "lost_write"})

# _wait_left value of an access whose READY waits for VALID before its wait is drawn
_AWAIT_VALID = -1


def negative_fault(graded: Collection[str]) -> str:
    """The fault NEGATIVE_KNOB names when it is one of ``graded``, the faults whose checks
    the calling test grades; else an empty string. A name outside FAULTS raises."""
    fault = os.environ.get(NEGATIVE_KNOB, "")
    if fault and fault not in FAULTS:
        raise ValueError(f"{NEGATIVE_KNOB}={fault!r}; one of {sorted(FAULTS)}")
    return fault if fault in graded else ""


@dataclass(frozen=True)
class AxiSubTiming:
    """Subordinate timing; the defaults give READY high on a free channel.

    With ``ready_wait_max`` above 0 a channel's READY waits for VALID and then 0 to
    ``ready_wait_max`` cycles, except on the ``ready_early_share`` of accesses where it rises
    as soon as the channel is free, before VALID. The ``late_share`` of R and B responses
    take a latency from ``late_latency`` in place of ``r_latency`` or ``b_latency``. A share
    of 0 draws nothing from the seed. ``w_before_aw`` holds AWREADY low until W is taken, and
    ``aw_before_w`` holds WREADY low until AW is taken; at most one of them is set.
    """

    ready_wait_max: int = 0
    r_latency: tuple[int, int] = (1, 1)
    b_latency: tuple[int, int] = (1, 1)
    w_before_aw: bool = False
    aw_before_w: bool = False
    rsp_glitch: bool = False
    ready_early_share: float = 0.0
    late_share: float = 0.0
    late_latency: tuple[int, int] = (8, 12)

    def __post_init__(self) -> None:
        if self.w_before_aw and self.aw_before_w:
            raise ValueError("w_before_aw and aw_before_w each wait for the other channel")


@dataclass(frozen=True)
class AxiSubCfg:
    """Bus form and build attributes the subordinate checks against."""

    lite: bool
    addr_width: int
    data_width: int
    prot: int
    cache: int
    clk_ps: int
    timing: AxiSubTiming = field(default_factory=AxiSubTiming)


@dataclass(frozen=True)
class AxiAddr:
    """One AR or AW request as it stood at its handshake."""

    addr: int
    size: int
    len: int
    burst: int
    lock: int
    cache: int
    prot: int
    qos: int
    region: int
    id: int
    user: int
    atop: int = 0


@dataclass(frozen=True)
class AxiW:
    """One W beat as it stood at its handshake."""

    data: int
    strb: int
    last: int
    user: int


Observer = Callable[[str, AxiAddr | AxiW], None]


class AxiSub:
    """AXI subordinate, memory and port monitor on the flattened nets of tb_top.

    One read and one write are in flight at most: a channel's READY stays low while its
    transaction waits for a response. ``stall`` holds every request READY low, and
    ``hold_responses`` holds RVALID and BVALID low: a response due while it is set waits,
    its remaining latency included, until it is cleared. Error responses are set per
    address. An address armed as side-effecting may be read once.
    ``handshakes`` counts the request handshakes of each channel since the start.
    """

    def __init__(
        self, dut: SimHandleBase, cfg: AxiSubCfg, report: Reporter, rng: random.Random
    ) -> None:
        self._dut = dut
        self.cfg = cfg
        self._report = report
        self._rng = rng
        self._log = logging.getLogger("cocotb.tdr2axi.axi_sub")
        self._nbytes = cfg.data_width // 8
        self._max_size = self._nbytes.bit_length() - 1
        self.mem: dict[int, int] = {}
        self.stall = False
        self.hold_responses = False
        self.ar_q: list[AxiAddr] = []
        self.aw_q: list[AxiAddr] = []
        self.w_q: list[AxiW] = []
        self._rerr: dict[int, int] = {}
        self._werr: dict[int, int] = {}
        self._side_effect: set[int] = set()
        self._ar_counts: Counter[int] = Counter()
        self._passes: Counter[str] = Counter()
        self._tasks: list[Task] = []
        self._in_reset = True
        self._valid_wait = 0
        self._observers: list[Observer] = []
        self.handshakes: Counter[str] = Counter()
        self.fault = ""
        self._fault_fired = False
        self._reset_state()

    # --- Lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        """Idle the subordinate outputs and run the subordinate and its watchers."""
        self._drive_idle()
        self._tasks.append(cocotb.start_soon(self._run()))
        self._tasks.append(cocotb.start_soon(self._watch_reset()))
        if self.cfg.timing.rsp_glitch:
            self._tasks.append(cocotb.start_soon(self._glitch()))

    def stop(self) -> None:
        """Stop the subordinate's coroutines."""
        for task in self._tasks:
            task.kill()
        self._tasks.clear()

    def finalize(self) -> None:
        """Report every monitor check exercised in this test with its pass count."""
        for check_id, count in sorted(self._passes.items()):
            for _ in range(count):
                self._report(check_id, passed=True, detail="")
        self._passes.clear()

    def arm_fault(self, fault: str) -> None:
        """Arm ``fault``, one of FAULTS, for the rest of the test; an empty name arms none."""
        if not fault:
            return
        if fault not in FAULTS:
            raise ValueError(f"subordinate fault {fault!r}; one of {sorted(FAULTS)}")
        self.fault = fault
        self._fault_fired = False
        self._log.warning(
            "NEGATIVE VALIDATION: %s=%s: the subordinate breaks its own rule", NEGATIVE_KNOB, fault
        )

    def add_transaction_callback(self, observer: Observer) -> None:
        """Call ``observer`` with the channel name and the item at every AR, AW and W
        handshake, in handshake order; it must not await."""
        self._observers.append(observer)

    def drain_problem(self) -> str:
        """Why the subordinate is not drained, or an empty string when it is."""
        problems = []
        if self._rd_pend is not None or self._r_vld:
            problems.append("a read response is outstanding")
        if self._wr_pend is not None or self._b_vld or self._aw_hold or self._w_hold:
            problems.append("a write is outstanding")
        if self.ar_q or self.aw_q or self.w_q:
            problems.append(f"unchecked AR {len(self.ar_q)} AW {len(self.aw_q)} W {len(self.w_q)}")
        return "; ".join(problems)

    # --- Memory and responses -------------------------------------------------------------
    def set_read_response(self, addr: int, resp: int) -> None:
        """Answer reads of ``addr`` with ``resp``; RESP_OKAY restores the default."""
        self._set_response(self._rerr, addr, resp)

    def set_write_response(self, addr: int, resp: int) -> None:
        """Answer writes to ``addr`` with ``resp``; RESP_OKAY restores the default."""
        self._set_response(self._werr, addr, resp)

    def arm_side_effect(self, addr: int) -> None:
        """Treat ``addr`` as a location a read changes: a second AR there fails."""
        self._side_effect.add(addr)
        self._ar_counts[addr] = 0

    def read_count(self, addr: int) -> int:
        """AR handshakes at ``addr`` since the start, or since it was armed."""
        return self._ar_counts[addr]

    def clear_monitor(self) -> None:
        """Forget the recorded transactions."""
        self.ar_q.clear()
        self.aw_q.clear()
        self.w_q.clear()

    # --- Private: state -------------------------------------------------------------------
    @staticmethod
    def _set_response(table: dict[int, int], addr: int, resp: int) -> None:
        if resp == RESP_OKAY:
            table.pop(addr, None)
        else:
            table[addr] = resp

    def _reset_state(self) -> None:
        self._ar_rdy = self._aw_rdy = self._w_rdy = False
        self._r_vld = self._b_vld = False
        self._rd_pend: list[Any] | None = None  # [AxiAddr, cycles left]
        self._wr_pend: list[Any] | None = None  # [resp, cycles left, AxiAddr]
        self._r_owner: AxiAddr | None = None
        self._b_owner: AxiAddr | None = None
        self._aw_hold: list[AxiAddr] = []
        self._w_hold: list[AxiW] = []
        self._hold: dict[str, AxiAddr | AxiW | None] = dict.fromkeys(CHANNELS)
        self._valid_prev: dict[str, int] = dict.fromkeys(CHANNELS, 0)
        self._wait_left: dict[str, int | None] = dict.fromkeys(CHANNELS)
        self._rd_owed = self._wr_owed = False
        self._first_after_reset = True
        # A request ended by reset is not one withdrawn after the dmactive fall
        self._dmactive_prev = 0
        self._inflight_at_dmactive_fall: dict[str, AxiAddr | AxiW] = {}

    def _drive_idle(self) -> None:
        d = self._dut
        for sig in (d.ar_ready, d.aw_ready, d.w_ready, d.r_valid, d.b_valid, d.r_last):
            sig.value = 0
        for sig in (d.r_data, d.r_resp, d.r_id, d.r_user, d.b_resp, d.b_id, d.b_user):
            sig.value = 0

    def _drive_outputs(self) -> None:
        d = self._dut
        d.ar_ready.value = int(self._ar_rdy)
        d.aw_ready.value = int(self._aw_rdy)
        d.w_ready.value = int(self._w_rdy)
        d.r_valid.value = int(self._r_vld)
        d.r_last.value = int(self._r_vld)
        d.b_valid.value = int(self._b_vld)

    def _pass(self, check_id: str) -> None:
        self._passes[check_id] += 1

    def _expect(self, check_id: str, *, passed: bool, detail: str) -> None:
        if passed:
            self._passes[check_id] += 1
        else:
            self._report(check_id, passed=False, detail=detail)

    # --- Private: sampling ----------------------------------------------------------------
    def _sample_ar(self) -> AxiAddr:
        d = self._dut
        return AxiAddr(
            addr=resolve(d.ar_addr),
            size=resolve(d.ar_size),
            len=resolve(d.ar_len),
            burst=resolve(d.ar_burst),
            lock=resolve(d.ar_lock),
            cache=resolve(d.ar_cache),
            prot=resolve(d.ar_prot),
            qos=resolve(d.ar_qos),
            region=resolve(d.ar_region),
            id=resolve(d.ar_id),
            user=resolve(d.ar_user),
        )

    def _sample_aw(self) -> AxiAddr:
        d = self._dut
        return AxiAddr(
            addr=resolve(d.aw_addr),
            size=resolve(d.aw_size),
            len=resolve(d.aw_len),
            burst=resolve(d.aw_burst),
            lock=resolve(d.aw_lock),
            cache=resolve(d.aw_cache),
            prot=resolve(d.aw_prot),
            qos=resolve(d.aw_qos),
            region=resolve(d.aw_region),
            id=resolve(d.aw_id),
            user=resolve(d.aw_user),
            atop=resolve(d.aw_atop),
        )

    def _sample_w(self) -> AxiW:
        d = self._dut
        return AxiW(
            data=resolve(d.w_data),
            strb=resolve(d.w_strb),
            last=resolve(d.w_last),
            user=resolve(d.w_user),
        )

    def _request_snapshot(self) -> tuple[int, ...]:
        d = self._dut
        nets = (
            d.ar_valid, d.ar_addr, d.ar_size, d.ar_len, d.ar_burst, d.ar_lock, d.ar_cache,
            d.ar_prot, d.ar_qos, d.ar_region, d.ar_id, d.ar_user,
            d.aw_valid, d.aw_addr, d.aw_size, d.aw_len, d.aw_burst, d.aw_lock, d.aw_cache,
            d.aw_prot, d.aw_qos, d.aw_region, d.aw_id, d.aw_user, d.aw_atop,
            d.w_valid, d.w_data, d.w_strb, d.w_last, d.w_user, d.r_ready, d.b_ready,
        )  # fmt: skip
        return tuple(resolve(sig) for sig in nets)

    # --- Private: main loop ---------------------------------------------------------------
    async def _run(self) -> None:
        d = self._dut
        while True:
            await RisingEdge(d.clk)
            if in_reset(d):
                self._at_reset_edge()
                continue
            self._in_reset = False
            valid = {"ar": resolve(d.ar_valid), "aw": resolve(d.aw_valid), "w": resolve(d.w_valid)}
            if self._first_after_reset:
                self._first_after_reset = False
                self._expect(
                    "CHK-AXISUB-RESET-VALID-LOW",
                    passed=not any(valid.values()),
                    detail=f"VALID at the first i_clk edge after reset release: {valid}",
                )
            r_rdy, b_rdy = resolve(d.r_ready), resolve(d.b_ready)
            req: dict[str, AxiAddr | AxiW | None] = {
                "ar": self._sample_ar() if valid["ar"] else None,
                "aw": self._sample_aw() if valid["aw"] else None,
                "w": self._sample_w() if valid["w"] else None,
            }
            ready = {"ar": self._ar_rdy, "aw": self._aw_rdy, "w": self._w_rdy}
            self._check_held(valid, req)
            self._check_valid_rise(valid, ready)
            self._check_dmactive_inflight(valid, ready, req)
            self._check_response_owners()
            self._take_responses(r_rdy, b_rdy)
            self._take_requests(valid, ready, req)
            self._issue_responses()
            if self.fault:
                self._inject_fault(valid, ready, req)
            self._watch_valid_wait(valid)
            self._next_readys(valid)
            self._drive_outputs()
            self._valid_prev = valid

    def _at_reset_edge(self) -> None:
        d = self._dut
        valids = (resolve(d.ar_valid), resolve(d.aw_valid), resolve(d.w_valid))
        self._expect(
            "CHK-AXISUB-RESET-VALID-LOW", passed=not any(valids), detail=f"VALID in reset: {valids}"
        )
        outputs = tuple(
            resolve(sig) for sig in (d.ar_ready, d.aw_ready, d.w_ready, d.r_valid, d.b_valid)
        )
        pending = bool(
            self._rd_pend is not None or self._wr_pend is not None or self._aw_hold or self._w_hold
        )
        self._expect(
            "CHK-ENV-FABRIC-RESET",
            passed=not (any(outputs) or pending),
            detail=f"subordinate in reset with outputs {outputs}, a transaction pending {pending}",
        )
        self._in_reset = True
        self._reset_state()
        self._drive_idle()

    async def _watch_reset(self) -> None:
        """Each fall of the reset input in control, i_arst_n while i_test_mode = 0 and
        i_arst_scan_n while it is 1, and each change of i_test_mode that hands control to a
        low reset input, drops every pending response and lowers every output."""
        d = self._dut
        triggers = cocotb.triggers
        while True:
            arst_fell, scan_fell = FallingEdge(d.rst_n), FallingEdge(d.rst_scan_n)
            fired = await triggers.First(arst_fell, scan_fell, triggers.Edge(d.test_mode))
            if fired is arst_fell:
                entered = reset_input(d) == "rst_n"
            elif fired is scan_fell:
                entered = reset_input(d) == "rst_scan_n"
            else:
                entered = entered_by_mode_change(d)
            if not entered:
                continue
            self._in_reset = True
            self._reset_state()
            self._drive_idle()

    # --- Private: request checks ----------------------------------------------------------
    def _check_held(self, valid: dict[str, int], req: dict[str, AxiAddr | AxiW | None]) -> None:
        """VALID and payload hold from VALID until the handshake."""
        for ch in CHANNELS:
            prev = self._hold[ch]
            if prev is None:
                continue
            if not valid[ch]:
                self._report(
                    "CHK-AXISUB-VALID-HELD",
                    passed=False,
                    detail=f"{ch.upper()}VALID dropped before READY: {prev}",
                )
                continue
            self._pass("CHK-AXISUB-VALID-HELD")
            self._expect(
                "CHK-AXISUB-PAYLOAD-STABLE",
                passed=req[ch] == prev,
                detail=f"{ch.upper()} changed before READY: {prev} -> {req[ch]}",
            )

    def _check_valid_rise(self, valid: dict[str, int], ready: dict[str, bool]) -> None:
        """A request VALID rises whatever READY is, and never while a response is owed."""
        for ch in CHANNELS:
            if not valid[ch] or self._valid_prev[ch]:
                continue
            if not ready[ch]:
                self._pass("CHK-AXISUB-VALID-INDEPENDENT")
            if ch == "w" and self._aw_hold:
                continue
            self._expect(
                "CHK-AXI-ONE-OUTSTANDING",
                passed=not (self._rd_owed or self._wr_owed),
                detail=f"{ch.upper()}VALID rose with a response owed (read {self._rd_owed}, "
                f"write {self._wr_owed})",
            )

    def _check_dmactive_inflight(
        self,
        valid: dict[str, int],
        ready: dict[str, bool],
        req: dict[str, AxiAddr | AxiW | None],
    ) -> None:
        """A request stalled when dmactive falls keeps its payload and completes once."""
        dmactive = resolve(self._dut.dmactive)
        if self._dmactive_prev and not dmactive:
            for ch in CHANNELS:
                held = req[ch]
                if valid[ch] and not ready[ch] and held is not None:
                    self._inflight_at_dmactive_fall[ch] = held
        self._dmactive_prev = dmactive
        for ch, held in list(self._inflight_at_dmactive_fall.items()):
            if valid[ch] and ready[ch]:
                self._expect(
                    "CHK-DM-DMACTIVE-INFLIGHT",
                    passed=req[ch] == held,
                    detail=f"{ch.upper()} stalled at the dmactive fall completed as {req[ch]}, "
                    f"held {held}",
                )
                del self._inflight_at_dmactive_fall[ch]
            elif not valid[ch]:
                self._report(
                    "CHK-DM-DMACTIVE-INFLIGHT",
                    passed=False,
                    detail=f"{ch.upper()} stalled at the dmactive fall was withdrawn: {held}",
                )
                del self._inflight_at_dmactive_fall[ch]

    def _check_addr(self, ch: str, a: AxiAddr) -> None:
        cfg = self.cfg
        name = ch.upper()
        self._expect(
            "CHK-AXI-REQ-ATTRS",
            passed=a.prot == cfg.prot,
            detail=f"{name}PROT {a.prot:03b}, AXI_PROT {cfg.prot:03b}",
        )
        if cfg.lite:
            return
        self._expect(
            "CHK-AXI-REQ-ATTRS",
            passed=a.cache == cfg.cache and a.id == 0 and a.user == 0,
            detail=f"{name}CACHE {a.cache:04b} {name}ID {a.id} {name}USER {a.user}, "
            f"AXI_CACHE {cfg.cache:04b}",
        )
        self._expect(
            "CHK-AXI-ONE-OUTSTANDING",
            passed=a.len == 0 and a.burst in (BURST_FIXED, BURST_INCR),
            detail=f"{name}LEN {a.len} {name}BURST {a.burst}: not a legal single beat",
        )
        self._expect(
            "CHK-AXISUB-SIZE-WIDTH",
            passed=a.size <= self._max_size,
            detail=f"{name}SIZE {a.size} wider than the {cfg.data_width}-bit bus",
        )
        self._expect(
            "CHK-AXISUB-CACHE-LEGAL",
            passed=a.cache in AXI4_CACHE_LEGAL,
            detail=f"{name}CACHE {a.cache:04b} is reserved",
        )
        self._expect(
            "CHK-AXISUB-BURST-LENGTH",
            passed=self._burst_legal(a),
            detail=f"{name}LEN {a.len} illegal for {name}BURST {a.burst} at 0x{a.addr:x}",
        )
        if a.burst != BURST_WRAP:
            last = a.addr if a.burst == BURST_FIXED else a.addr + ((a.len + 1) << a.size) - 1
            self._expect(
                "CHK-AXISUB-BOUNDARY-4K",
                passed=a.addr // PAGE_4K == last // PAGE_4K,
                detail=f"{name} from 0x{a.addr:x} to 0x{last:x} crosses a 4KB boundary",
            )

    @staticmethod
    def _burst_legal(a: AxiAddr) -> bool:
        if a.burst == BURST_INCR:
            return a.len <= 255
        if a.burst == BURST_FIXED:
            return a.len <= 15
        if a.burst == BURST_WRAP:
            return a.len in (1, 3, 7, 15) and a.addr % (1 << a.size) == 0
        return False

    def _check_write(self, aw: AxiAddr, w: AxiW) -> None:
        off = aw.addr % self._nbytes
        if self.cfg.lite:
            # A contiguous, naturally aligned group of 2**k lanes starting at the address
            group = w.strb >> off
            lanes = group.bit_length()
            ok = (
                w.strb != 0
                and w.strb & ((1 << off) - 1) == 0
                and group & (group + 1) == 0
                and lanes & (lanes - 1) == 0
                and off % lanes == 0
            )
            what = f"WSTRB 0x{w.strb:x} for a write at 0x{aw.addr:x}"
        else:
            nbytes = 1 << aw.size
            low = (aw.addr - aw.addr % nbytes) % self._nbytes
            lanes = ((1 << nbytes) - 1) << low
            ok = w.strb & ~lanes == 0 and (w.strb >> off) << off == w.strb
            self._expect(
                "CHK-AXISUB-LAST-BEAT",
                passed=w.last == 1,
                detail=f"WLAST {w.last} on the only beat of the write to 0x{aw.addr:x}",
            )
            self._expect(
                "CHK-AXI-ONE-OUTSTANDING",
                passed=w.last == 1,
                detail=f"WLAST {w.last} on a single beat",
            )
            self._expect("CHK-AXI-REQ-ATTRS", passed=w.user == 0, detail=f"WUSER {w.user}")
            what = f"WSTRB 0x{w.strb:x} for a write of size {aw.size} at 0x{aw.addr:x}"
        self._expect("CHK-AXISUB-STROBE-LANES", passed=ok, detail=what)

    # --- Private: handshakes --------------------------------------------------------------
    def _take_responses(self, r_rdy: int, b_rdy: int) -> None:
        if self._r_vld and r_rdy:
            self._r_vld = False
            self._r_owner = None
            self._rd_owed = False
        if self._b_vld and b_rdy:
            self._b_vld = False
            self._b_owner = None
            self._wr_owed = False

    def _take_requests(
        self,
        valid: dict[str, int],
        ready: dict[str, bool],
        req: dict[str, AxiAddr | AxiW | None],
    ) -> None:
        for ch in CHANNELS:
            self._hold[ch] = req[ch] if valid[ch] and not ready[ch] else None
        ar, aw, w = req["ar"], req["aw"], req["w"]
        if ready["ar"] and isinstance(ar, AxiAddr):
            self._expect(
                "CHK-AXI-ONE-OUTSTANDING",
                passed=not (self._rd_owed or self._wr_owed),
                detail=f"AR handshake at 0x{ar.addr:x} with a response owed",
            )
            self._check_addr("ar", ar)
            self._count_read(ar.addr)
            self.ar_q.append(ar)
            self._notify("ar", ar)
            self._rd_pend = [ar, self._latency(self.cfg.timing.r_latency)]
            self._rd_owed = True
            self._wait_left["ar"] = None
        if ready["aw"] and isinstance(aw, AxiAddr):
            self._expect(
                "CHK-AXI-ONE-OUTSTANDING",
                passed=not (self._rd_owed or self._wr_owed),
                detail=f"AW handshake at 0x{aw.addr:x} with a response owed",
            )
            self._check_addr("aw", aw)
            self.aw_q.append(aw)
            self._notify("aw", aw)
            self._aw_hold.append(aw)
            self._wr_owed = True
            self._wait_left["aw"] = None
        if ready["w"] and isinstance(w, AxiW):
            self.w_q.append(w)
            self._notify("w", w)
            self._w_hold.append(w)
            self._wait_left["w"] = None
        if self._aw_hold and self._w_hold and self._wr_pend is None:
            aw_a, w_b = self._aw_hold.pop(0), self._w_hold.pop(0)
            self._check_write(aw_a, w_b)
            resp = self._werr.get(aw_a.addr, RESP_OKAY)
            if self.fault == "lost_write" and resp == RESP_OKAY and not self._fault_fired:
                self._fault_fired = True
            else:
                self._write(aw_a.addr, w_b.strb, w_b.data)
            self._wr_pend = [resp, self._latency(self.cfg.timing.b_latency), aw_a]

    def _notify(self, channel: str, item: AxiAddr | AxiW) -> None:
        self.handshakes[channel] += 1
        for observer in self._observers:
            observer(channel, item)

    def _count_read(self, addr: int) -> None:
        self._ar_counts[addr] += 1
        if addr not in self._side_effect:
            return
        if self._ar_counts[addr] == 1:
            self._passes["CHK-ENV-READONDATA-IDLE"] += 1
            return
        self._report(
            "CHK-ENV-READONDATA-IDLE",
            passed=False,
            detail=f"side-effecting location 0x{addr:x} read {self._ar_counts[addr]} times",
        )

    def _issue_responses(self) -> None:
        if self.hold_responses:
            return
        d = self._dut
        if self._rd_pend is not None and not self._r_vld:
            if self._rd_pend[1] <= 0:
                ar = self._rd_pend[0]
                d.r_data.value = self._read_lanes(ar.addr, ar.size)
                d.r_resp.value = self._rerr.get(ar.addr, RESP_OKAY)
                d.r_id.value = self._response_id(ar.id)
                self._r_vld = True
                self._r_owner = ar
                self._rd_pend = None
            else:
                self._rd_pend[1] -= 1
        if self._wr_pend is not None and not self._b_vld:
            if self._wr_pend[1] <= 0:
                d.b_resp.value = self._wr_pend[0]
                d.b_id.value = self._response_id(self._wr_pend[2].id)
                self._b_vld = True
                self._b_owner = self._wr_pend[2]
                self._wr_pend = None
            else:
                self._wr_pend[1] -= 1

    def _check_response_owners(self) -> None:
        """The subordinate's own R and B follow their request and carry its ID."""
        d = self._dut
        if resolve(d.r_valid):
            owner = self._r_owner
            self._expect(
                "CHK-AXISUB-READ-RESP-ORDER",
                passed=owner is not None,
                detail="RVALID high with no accepted AR waiting for it",
            )
            if owner is not None:
                self._expect(
                    "CHK-AXISUB-ID-ORDER",
                    passed=resolve(d.r_id) == owner.id,
                    detail=f"RID {resolve(d.r_id)} for ARID {owner.id}",
                )
        if resolve(d.b_valid):
            owner = self._b_owner
            self._expect(
                "CHK-AXISUB-WRITE-RESP-ORDER",
                passed=owner is not None,
                detail="BVALID high with no accepted AW and W waiting for it",
            )
            if owner is not None:
                self._expect(
                    "CHK-AXISUB-ID-ORDER",
                    passed=resolve(d.b_id) == owner.id,
                    detail=f"BID {resolve(d.b_id)} for AWID {owner.id}",
                )

    def _response_id(self, request_id: int) -> int:
        return request_id ^ 1 if self.fault == "bad_id" else request_id

    def _inject_fault(
        self,
        valid: dict[str, int],
        ready: dict[str, bool],
        req: dict[str, AxiAddr | AxiW | None],
    ) -> None:
        """Raise RVALID or BVALID once, owned by no request, while the first AR or AW of the
        armed fault's kind waits for READY."""
        if self._fault_fired:
            return
        d = self._dut
        ar = req["ar"]
        if self.fault == "early_r" and valid["ar"] and not ready["ar"] and not self._r_vld:
            if isinstance(ar, AxiAddr):
                d.r_data.value = self._read_lanes(ar.addr, ar.size)
                d.r_resp.value = RESP_OKAY
                d.r_id.value = ar.id
            self._r_vld = True
            self._r_owner = None
            self._fault_fired = True
        elif self.fault == "early_b" and valid["aw"] and not ready["aw"] and not self._b_vld:
            d.b_resp.value = RESP_OKAY
            d.b_id.value = 0
            self._b_vld = True
            self._b_owner = None
            self._fault_fired = True

    def _watch_valid_wait(self, valid: dict[str, int]) -> None:
        """sbbusy high with every VALID low and nothing owed means the DUT waits for READY."""
        busy = resolve(self._dut.sbbusy)
        idle = not any(valid.values()) and not (self._rd_owed or self._wr_owed)
        self._valid_wait = self._valid_wait + 1 if busy and idle and not self.stall else 0
        if self._valid_wait == VALID_WAIT_LIMIT:
            self._report(
                "CHK-AXISUB-VALID-INDEPENDENT",
                passed=False,
                detail=f"sbbusy high for {VALID_WAIT_LIMIT} cycles with every request VALID low",
            )

    def _next_readys(self, valid: dict[str, int]) -> None:
        """READY for the next cycle: one request of each kind in flight."""
        free = not self.stall
        read_free = free and self._rd_pend is None and not self._r_vld
        write_free = free and self._wr_pend is None and not self._b_vld
        aw_free = write_free and not self._aw_hold
        if self.cfg.timing.w_before_aw and self.fault != "ready_now":
            aw_free = aw_free and bool(self._w_hold)
        self._ar_rdy = self._ready_after_wait("ar", free=read_free, valid=valid["ar"])
        w_free = write_free and not self._w_hold
        if self.cfg.timing.aw_before_w and self.fault != "ready_now":
            w_free = w_free and bool(self._aw_hold)
        self._aw_rdy = self._ready_after_wait("aw", free=aw_free, valid=valid["aw"])
        self._w_rdy = self._ready_after_wait("w", free=w_free, valid=valid["w"])

    def _ready_after_wait(self, ch: str, *, free: bool, valid: int) -> bool:
        """READY of a free channel: on the early share high from its first free cycle until
        the handshake, otherwise low until VALID and then for the drawn wait."""
        timing = self.cfg.timing
        wait_max = 0 if self.fault == "ready_now" else timing.ready_wait_max
        if wait_max == 0 or not free:
            return free
        left = self._wait_left[ch]
        if left is None:
            early = timing.ready_early_share > 0 and self._rng.random() < timing.ready_early_share
            left = 0 if early else _AWAIT_VALID
        if left == _AWAIT_VALID:
            if not valid:
                self._wait_left[ch] = left
                return False
            left = self._rng.randint(0, wait_max)
        self._wait_left[ch] = max(0, left - 1)
        return left == 0

    def _latency(self, span: tuple[int, int]) -> int:
        """A response latency from ``span``, or on the late share from ``late_latency``."""
        timing = self.cfg.timing
        if timing.late_share > 0 and self._rng.random() < timing.late_share:
            return self._draw(timing.late_latency)
        return self._draw(span)

    def _draw(self, span: tuple[int, int]) -> int:
        return self._rng.randint(span[0], span[1])

    # --- Private: memory ------------------------------------------------------------------
    def _read_lanes(self, addr: int, size: int) -> int:
        base = addr - addr % self._nbytes
        off = addr % self._nbytes
        data = 0
        for lane in range(self._nbytes):
            if self.cfg.lite or off <= lane < off + (1 << size):
                byte = self.mem.get(base + lane, 0)
            else:
                byte = self._rng.getrandbits(8)
            data |= byte << (8 * lane)
        return data

    def _write(self, addr: int, strb: int, data: int) -> None:
        base = addr - addr % self._nbytes
        for lane in range(self._nbytes):
            if (strb >> lane) & 1:
                self.mem[base + lane] = (data >> (8 * lane)) & 0xFF

    # --- Private: response changes between i_clk edges ------------------------------------
    async def _glitch(self) -> None:
        """Change every response input between i_clk edges; o_axi_req must not follow."""
        d = self._dut
        quarter = max(1, self.cfg.clk_ps // 4)
        drives: tuple[tuple[SimHandleBase, Callable[[], int]], ...] = (
            (d.ar_ready, lambda: int(self._ar_rdy)),
            (d.aw_ready, lambda: int(self._aw_rdy)),
            (d.w_ready, lambda: int(self._w_rdy)),
            (d.r_valid, lambda: int(self._r_vld)),
            (d.b_valid, lambda: int(self._b_vld)),
        )
        payload = (d.r_data, d.r_resp, d.r_id, d.r_last, d.r_user, d.b_resp, d.b_id, d.b_user)
        while True:
            await FallingEdge(d.clk)
            if self._in_reset or in_reset(d):
                continue
            before = self._request_snapshot()
            held = [sig.value for sig in payload]
            for sig, _ in drives:
                sig.value = self._rng.getrandbits(1)
            for sig in payload:
                sig.value = self._rng.getrandbits(len(sig))
            await Timer(quarter, "ps")
            after = self._request_snapshot()
            for sig, intended in drives:
                sig.value = intended()
            for sig, value in zip(payload, held, strict=True):
                sig.value = value
            self._expect(
                "CHK-AXI-NO-COMB-PATH",
                passed=before == after,
                detail=f"o_axi_req changed between i_clk edges: {before} -> {after}",
            )
