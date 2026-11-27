# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Base test of the tdr2axi bench: bring-up, evidence, the SBA flows and the scoreboard feed.

``Tdr2axiTb`` is the bench helper every tdr2axi test builds through ``Tdr2axiTb.setup``. It
reads the seed, the build from the ``cfg_*`` nets of tb_top, the run-time configuration from
``Tdr2axiTestCfg`` and the test's planned checks from ``planned_checks``, logs the seed, the
drawn knobs, the TDR2AXI_CLOCKS line, the required IDs and the planned checks it drops or
defers once, and owns the ``TdrHost``, the ``AxiSub``, the ``Tdr2axiPredictor`` that feeds
the reference models, the ``Tdr2axiTimingMonitor`` on the host's TCK edges, the
``Tdr2axiScoreboard`` and the ``Tdr2axiChecker`` every verdict goes to. The scoreboard takes
the TDR model's predictions through ``Tdr2axiTdrModelTap``, the DM answers from the
predictor, the capture word at each capture edge, every AR, AW and W handshake, and, sampled
at each i_clk rising edge out of reset, the DM's DMI request and response handshakes and the
R and B response codes; each i_arst_n fall with i_test_mode = 0, i_arst_scan_n fall with
i_test_mode = 1, change of i_test_mode that hands control to a low reset input, and TAP
reset of the host resets it. Out of reset means out of i_arst_scan_n while i_test_mode = 1
and out of i_arst_n while it is 0. Bring-up drives i_test_mode = 0 and i_arst_scan_n = 1, holds
i_arst_n for the reset length, releases it, runs TCK until both resets are released and both
CDCs have ended their clear sequence, and sets dmactive.

A test's own comparison fails at once; a monitor's failure is logged when it happens and
fails the test at ``finish``. ``finish`` also grades each assertion check from the tb_top
counters: a failure since setup fails the test, and with none a pass is logged once the
check's antecedent has held. It drains the scoreboard, grades CHK-AXISUB-DRAIN, logs how
many sbcs reads returned sbbusy = 1, and has the checker grade CHK-NONVAC over the required
IDs.

The required IDs are the test's planned checks less those the build, its SYNC_STAGES or the
run's clock ratio cannot exercise, and a drawn i_clk period (CLK_PERIOD_PS=random)
exercises no ratio-specific check. A planned check in ``DEFERRED_CHECKS`` waits for its
owner; ``attach_scoreboard`` requires the scoreboard's planned checks. CHK-ENV-READONDATA-IDLE,
CHK-DM-DMACTIVE-INFLIGHT and CHK-AXI-NO-COMB-PATH count no pass unless the test plans them
or its configuration arms them, and then they are required. There is no SV twin: the bench
has one cocotb realization.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

import cocotb
from cocotb.handle import SimHandleBase
from cocotb.task import Task
from cocotb.triggers import ClockCycles, Edge, FallingEdge, First, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from env.axi_sub import AxiSub, AxiSubCfg
from env.sba_ref_model import DmAnswer, SbaModelCfg, SbaRefModel
from env.tdr2axi_predictor import (
    Tdr2axiPredictor,
    entered_by_mode_change,
    in_reset,
    reset_input,
)
from env.tdr2axi_scoreboard import (
    NDMRESET,
    SCOREBOARD_CHECKS,
    TRST_INDEPENDENT,
    WRITE_MEMORY,
    Tdr2axiScoreboard,
    Tdr2axiScoreboardCfg,
    Tdr2axiTdrModelTap,
)
from env.tdr_host import (
    DMCONTROL,
    MAX_POLLS,
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBDATA0,
    SBDATA1,
    DmiAccess,
    DmiOp,
    DmiResult,
    DmiStatus,
    TdrHost,
    TdrHostCfg,
    resolve,
    sbcs_value,
    tdr_word,
)
from tests.tdr2axi_build import Tdr2axiBuild
from tests.tdr2axi_checker import Tdr2axiChecker
from tests.tdr2axi_plan import DEFERRED_CHECKS, planned_checks
from tests.tdr2axi_test_cfg import Tdr2axiTestCfg, rng_for
from tests.tdr2axi_timing_monitor import (
    ResultArrival,
    Tdr2axiTimingMonitor,
    exercised_at,
    ratio_name,
)

# sbcs fields (RISC-V External Debug Support 0.13.2, sbcs)
SBCS_SBBUSYERROR = 22
SBCS_SBBUSY = 21
SBCS_SBREADONADDR = 20
SBCS_SBACCESS_LSB = 17
SBCS_SBAUTOINCREMENT = 16
SBCS_SBREADONDATA = 15
SBCS_SBERROR_LSB = 12
SBCS_CLEAR_ERRORS = 0x0040_7000
# sbcs while dmcontrol.dmactive = 0: the register reset value, sbaccess = 2 and all else 0
SBCS_INACTIVE = 0x0004_0000

WORD_MASK = 0xFFFF_FFFF
SIZE_64 = 3
TEST_NAME_CHARS = 32
TIMEOUT_MS = 2

# Checks whose monitors count no pass unless the test plans them or its configuration arms
# them
UNARMED_IDS = frozenset(
    {"CHK-ENV-READONDATA-IDLE", "CHK-DM-DMACTIVE-INFLIGHT", "CHK-AXI-NO-COMB-PATH"}
)

# Assertion checks and the tb_top counters of each one's failures and antecedent hits
SVA_NETS = {
    "CHK-AXI-HANDSHAKE-SVA": ("sva_fail_handshake", "sva_hit_handshake"),
    "CHK-AXI-ONE-OUTSTANDING-SVA": ("sva_fail_one_outstanding", "sva_hit_one_outstanding"),
    "CHK-AXI-REQ-ATTRS-SVA": ("sva_fail_req_attrs", "sva_hit_req_attrs"),
    "CHK-AXI-AXI4-SIZE-SVA": ("sva_fail_axi4_size", "sva_hit_axi4_size"),
    "CHK-ENV-TDR-UPDATE-STABLE": ("sva_fail_tdr_update_stable", "sva_hit_tdr_update_stable"),
}

# Memory checks the scoreboard grades; mem_check grades any other check itself
SCOREBOARD_MEMORY_CHECKS = frozenset({WRITE_MEMORY, NDMRESET, TRST_INDEPENDENT})
# Bridge observables and o_dmi_tdr, which hold while TCK is stopped
TCK_STOP_NETS = ("bridge_state", "bridge_error", "bridge_address", "bridge_data", "tdr_in")

RESP_ERROR_MIN = 2  # SLVERR and DECERR


def size_mask(size: int) -> int:
    """Mask of the bits an access of 2**size bytes carries."""
    return (1 << (8 << size)) - 1


def _seed() -> int:
    return int(cocotb.RANDOM_SEED)


def _read_build(dut: SimHandleBase) -> Tdr2axiBuild:
    """The parameters tb_top was elaborated with."""
    return Tdr2axiBuild(
        lite=bool(resolve(dut.cfg_axi_lite)),
        addr_width=resolve(dut.cfg_addr_width),
        data_width=resolve(dut.cfg_data_width),
        prot=resolve(dut.cfg_axi_prot),
        cache=resolve(dut.cfg_axi_cache),
        sync_stages=resolve(dut.cfg_sync_stages),
    )


def _host_cfg(cfg: Tdr2axiTestCfg, build: Tdr2axiBuild) -> TdrHostCfg:
    """The host's clocks and scan shape, with Run-Test/Idle of at least one DMI round trip,
    N, so the poll after each op captures its result; randomized runs draw each scan's
    count from N to 2N."""
    host_cfg = TdrHostCfg(
        tck_ps=cfg.tck_ps,
        clk_ps=cfg.clk_ps,
        sync_stages=build.sync_stages,
        pad_bits=cfg.pad_bits,
        pause_cycles=cfg.pause_cycles,
    )
    need = host_cfg.readondata_idle
    return replace(host_cfg, idle_cycles=need, idle_extra=need if cfg.randomized else 0)


def _describe_deferred(deferred: dict[str, str]) -> str:
    return "; ".join(f"{i} ({owner})" for i, owner in sorted(deferred.items())) or "none"


def _sub_cfg(cfg: Tdr2axiTestCfg, build: Tdr2axiBuild) -> AxiSubCfg:
    """The subordinate's port shape from the build and its timing from the test cfg."""
    return AxiSubCfg(
        lite=build.lite,
        addr_width=build.addr_width,
        data_width=build.data_width,
        prot=build.prot,
        cache=build.cache,
        clk_ps=cfg.clk_ps,
        timing=cfg.axi,
    )


def _scoreboard_cfg(build: Tdr2axiBuild) -> Tdr2axiScoreboardCfg:
    """The scoreboard's view of the build."""
    return Tdr2axiScoreboardCfg(
        lite=build.lite,
        data_width=build.data_width,
        sync_stages=build.sync_stages,
        addr_width=build.addr_width,
    )


def _predictor(
    dut: SimHandleBase, cfg: Tdr2axiTestCfg, build: Tdr2axiBuild, scoreboard: Tdr2axiScoreboard
) -> Tdr2axiPredictor:
    """The predictor over both reference models, the TDR model writing to ``scoreboard``."""
    tdr = Tdr2axiTdrModelTap(scoreboard, sync_stages=build.sync_stages, negative=cfg.tdr_negative)
    sba_cfg = SbaModelCfg(build.lite, build.addr_width, build.data_width)
    return Tdr2axiPredictor(dut, tdr, SbaRefModel(sba_cfg, negative=cfg.sba_negative))


class Tdr2axiTb:
    """Clocks, resets, TDR host, AXI subordinate, model feed and evidence for one test."""

    def __init__(self, dut: SimHandleBase, cfg: Tdr2axiTestCfg, name: str) -> None:
        self._dut = dut
        self.cfg = cfg
        self.name = name
        self.log = logging.getLogger("cocotb.tdr2axi.tb")
        self.build = _read_build(dut)
        b = self.build
        self.nbytes = b.nbytes
        self.max_size = b.max_size
        self.sbcs_reset = b.sbcs_reset
        ratio = None if cfg.clk_drawn else ratio_name(cfg.clk_ps, cfg.tck_ps)
        planned = planned_checks(name)
        self.exercised = frozenset(
            i
            for i in planned
            if b.exercises(i) and exercised_at(i, sync_stages=b.sync_stages, ratio=ratio)
        )
        self.dropped = planned - self.exercised
        self.deferred = {i: DEFERRED_CHECKS[i] for i in self.exercised if i in DEFERRED_CHECKS}
        required = (self.exercised - self.deferred.keys()) | cfg.armed_ids
        self.checker = Tdr2axiChecker(
            name=name, required_ids=required, unarmed_ids=UNARMED_IDS - required
        )
        # Test regions sit above 4 GiB when the address is wide enough to need sbaddress1
        self.region = 0x1_0000_0000 if b.addr_width > 32 else 0
        self.rng = rng_for(cfg.seed, f"{name}:stimulus")
        self._tasks: list[Task] = []
        self._clk_en = True
        # i_clk changes only at clk_edge_ps plus a whole number of half periods; ``start``
        # sets it as it starts the clock, and it has no value before then
        self.clk_edge_ps: int
        self._unpolled_access = False
        self.sbcs_reads = 0
        self.sbbusy_reads = 0
        self._answers_fed = 0
        self._resets = 0
        self._sva_at_setup = self._sva_counts()
        self.ref_mem: dict[int, int] = {}
        self.scoreboard = Tdr2axiScoreboard(_scoreboard_cfg(b), report=self.checker.record)
        self.predictor = _predictor(dut, cfg, b, self.scoreboard)
        self.host = TdrHost(
            dut,
            _host_cfg(cfg, b),
            self.checker.record,
            self.predictor,
            rng=rng_for(cfg.seed, f"{name}:tdr_host"),
        )
        self.timing = Tdr2axiTimingMonitor(
            dut,
            sync_stages=b.sync_stages,
            clk_ps=cfg.clk_ps,
            tck_ps=cfg.tck_ps,
            report=self.checker.record,
        )
        self.host.add_edge_callback(self.timing.edge)
        self.timing.add_arrival_callback(self._scoreboard_arrival)
        self._results_watched = False
        self.sub = AxiSub(
            dut, _sub_cfg(cfg, b), self.checker.record, rng_for(cfg.seed, f"{name}:axi_sub")
        )
        self.attach_scoreboard(self.scoreboard)

    @classmethod
    async def setup(
        cls,
        dut: SimHandleBase,
        name: str,
        *,
        dmactive: bool = True,
    ) -> Tdr2axiTb:
        """Build the bench for test ``name`` with the check IDs its plan items require, log
        the seed, the drawn knobs and the checks, bring the DUT up and, by default, set
        dmactive."""
        # Shown as ASCII in the waveform, to find where each test starts
        dut.test_name.value = int.from_bytes(
            name[:TEST_NAME_CHARS].rjust(TEST_NAME_CHARS).encode(), "big"
        )
        # The cfg_* nets are continuous assigns, X at time 0 when the first test starts
        await Timer(1, "ps")
        cfg = Tdr2axiTestCfg.from_knobs(name, seed=_seed())
        tb = cls(dut, cfg, name)
        tb.log.info("%s: seed %d, %s", name, cfg.seed, cfg.describe())
        tb.log.info(
            "TDR2AXI_CLOCKS clk_ps=%d tck_ps=%d phase_ps=%d",
            cfg.clk_ps,
            cfg.tck_ps,
            cfg.clk_phase_ps,
        )
        tb.log.info(
            "%s: build %s, required %s",
            name,
            tb.build,
            " ".join(sorted(tb.checker.required_ids)),
        )
        tb.log.info(
            "%s: planned checks dropped by the build or clock ratio: %s; deferred: %s",
            name,
            " ".join(sorted(tb.dropped)) or "none",
            _describe_deferred(tb.deferred),
        )
        await tb.start(dmactive=dmactive)
        return tb

    async def start(self, *, dmactive: bool = True) -> None:
        """Idle the inputs, start clocks, subordinate and models, reset, and set dmactive."""
        d = self._dut
        d.test_mode.value = 0
        d.rst_scan_n.value = 1
        d.sva_en.value = 0
        d.clk.value = 0
        d.rst_n.value = 0
        self.host.drive_idle()
        self.sub.start()
        self.predictor.start()
        self._tasks.append(cocotb.start_soon(self._feed_scoreboard()))
        self._tasks.append(cocotb.start_soon(self._watch_reset()))
        self._tasks.append(cocotb.start_soon(self._watch_scan_reset()))
        self.clk_edge_ps = round(get_sim_time("ps")) + self.cfg.clk_phase_ps
        self._tasks.append(cocotb.start_soon(self._clock()))
        await RisingEdge(d.clk)
        d.sva_en.value = 1
        await ClockCycles(d.clk, self.cfg.reset_cycles - 1)
        d.rst_n.value = 1
        await self.wait_reset_ready()
        if dmactive:
            await self.dmi_write(DMCONTROL, 1)

    async def _clock(self) -> None:
        """i_clk, paused low while the clock is stopped. Its edges fall at ``clk_edge_ps``
        plus whole half periods, and ``start`` sets ``clk_edge_ps`` to ``clk_phase_ps``
        past the time it starts this task. The host keeps TCK on a grid from time 0, so
        ``clk_edge_ps`` sets where the edges of the two clocks fall."""
        clk = self._dut.clk
        level = 0
        half = self.cfg.clk_ps // 2
        delay = self.clk_edge_ps - round(get_sim_time("ps"))
        if delay:
            await Timer(delay, "ps")
        while True:
            await Timer(half, "ps")
            if self._clk_en or level:
                level ^= 1
                clk.value = level

    # --- Evidence -------------------------------------------------------------------------
    def attach_scoreboard(self, scoreboard: Tdr2axiScoreboard) -> None:
        """Attach ``scoreboard``: it takes every AR, AW and W handshake of the subordinate
        and the capture word at every capture edge of the host as they happen, and ``start``
        feeds it the DM side. It reports through ``checker.record``; those of its checks this
        test plans and exercises become required, and leave the deferred list."""
        self.sub.add_transaction_callback(scoreboard.write_bus)
        self.host.add_edge_callback(self._scoreboard_edge)
        taken = self.exercised & SCOREBOARD_CHECKS
        self.checker.add_required(taken)
        for check_id in taken:
            self.deferred.pop(check_id, None)
        self.log.info(
            "%s: the scoreboard requires %s; deferred: %s",
            self.name,
            " ".join(sorted(taken)) or "nothing",
            _describe_deferred(self.deferred),
        )

    def finish(self) -> None:
        """Finalize the monitors and the scoreboard, grade the assertions and the drain
        guard, stop the bench, and have the checker grade the test."""
        self.sub.finalize()
        self.scoreboard.drain()
        self._grade_assertions()
        self.log.info(
            "%s: %d of %d sbcs reads returned sbbusy = 1",
            self.name,
            self.sbbusy_reads,
            self.sbcs_reads,
        )
        drain = self.sub.drain_problem()
        self.checker.record("CHK-AXISUB-DRAIN", passed=not drain, detail=drain)
        self.stop()
        self.checker.finalize()

    def stop(self) -> None:
        """Stop the clock, the subordinate and the model feed."""
        self.sub.stop()
        self.predictor.stop()
        for task in self._tasks:
            task.kill()
        self._tasks.clear()

    # --- Clocks and resets ----------------------------------------------------------------
    def stop_clk(self) -> None:
        """Hold i_clk low from its next falling edge."""
        self._clk_en = False
        self.timing.clk_stopped()

    def start_clk(self) -> None:
        """Let i_clk run again."""
        self._clk_en = True
        self.timing.clk_started()

    async def wait_clk_periods(self, count: int) -> None:
        """Wait ``count`` nominal i_clk periods, whether or not i_clk runs."""
        await Timer(count * self.cfg.clk_ps, "ps")

    async def clk_cycles(self, count: int) -> None:
        """Wait ``count`` i_clk rising edges."""
        await ClockCycles(self._dut.clk, count)

    async def tck_cycles(self, count: int) -> None:
        """Run ``count`` TCK periods in Run-Test/Idle."""
        await self.host.tck_cycles(count)

    @property
    def elaborated_dut(self) -> SimHandleBase:
        """The tdr2axi instance tb_top elaborates, for reading its ports and module name.

        VCS gives the g_bus generate block no handle of its own and lists its members under
        tb_top by their dotted names, so the instance is looked up as ``g_bus.u_dut`` there.
        """
        try:
            return self._dut.g_bus.u_dut
        except AttributeError:
            return self._dut._id("g_bus.u_dut", extended=False)

    def observe(self, net: str) -> int:
        """The value of the tb_top observable ``net`` now; X or Z raises
        ``UnknownValueError``. Reads only: a test drives the DUT through the host and the
        subordinate."""
        return resolve(getattr(self._dut, net))

    def observe_bits(self, net: str) -> str:
        """The value of the tb_top observable ``net`` now as a bit string, X and Z included,
        for a failure's detail."""
        value = getattr(self._dut, net).value
        return str(value) if isinstance(value, int) else value.binstr

    def off_edge_delay(self, within: int | None = None) -> int:
        """Picoseconds from now to a drawn time that is no rising or falling edge of i_clk or
        TCK: within one period of the slower clock, or under ``within`` picoseconds when
        given. i_clk changes at ``clk_edge_ps`` plus a whole number of half periods, and TCK
        at whole half periods from time 0."""
        now = round(get_sim_time("ps"))
        span = within or max(self.cfg.clk_ps, self.cfg.tck_ps)
        delay = self.rng.randrange(1, span)
        while self._on_edge(now + delay):
            delay = delay + 1 if within is None or delay + 1 < within else 1
        return delay

    def _on_edge(self, time_ps: int) -> bool:
        """Whether ``time_ps`` is a rising or falling edge of i_clk or TCK."""
        cfg = self.cfg
        clk = (time_ps - self.clk_edge_ps) % (cfg.clk_ps // 2) == 0
        return clk or time_ps % (cfg.tck_ps // 2) == 0

    def assert_reset(self) -> None:
        """Assert i_arst_n."""
        self._dut.rst_n.value = 0

    def release_reset(self) -> None:
        """Release i_arst_n."""
        self._dut.rst_n.value = 1

    def set_test_mode(self, *, on: bool) -> None:
        """Drive i_test_mode; while it is 1, i_arst_scan_n alone drives both internal
        resets."""
        self._dut.test_mode.value = int(on)

    def assert_scan_reset(self) -> None:
        """Assert i_arst_scan_n."""
        self._dut.rst_scan_n.value = 0

    def release_scan_reset(self) -> None:
        """Release i_arst_scan_n."""
        self._dut.rst_scan_n.value = 1

    async def wait_reset_ready(self) -> None:
        """Run-Test/Idle after the release of i_arst_n until the first operation crosses
        without busy: the CDCs clear only on TCK edges."""
        await self.host.tck_cycles(self.host.cfg.reset_ready_idle)

    def arm_side_effect(self, addr: int) -> None:
        """Make ``addr`` a location a read changes: the subordinate fails a second read of
        it, and the host checks its Run-Test/Idle before each sbdata0 read with
        sbreadondata (CHK-ENV-READONDATA-IDLE) until ``disarm_side_effects``."""
        self.sub.arm_side_effect(addr)
        self.host.side_effect_reads = True

    def disarm_side_effects(self) -> None:
        """Stop treating the host's sbdata0 reads with sbreadondata as reads of a
        side-effecting location."""
        self.host.side_effect_reads = False

    def trst(self) -> None:
        """TAP reset of the host between scans: TCK is stopped and the TDR deselected, so
        no DUT pin changes and only the models and the scoreboard apply it."""
        self.predictor.trst()
        self.scoreboard.reset("RST-TRST")

    def trst_end(self) -> None:
        """The TAP reset ends: every bus access it found queued or that started during it
        must have been taken."""
        self.scoreboard.end_trst()

    def grade_event(
        self, check_id: str, label: str, value: int | None, *, window: tuple[int, int]
    ) -> None:
        """The scoreboard grades ``value``, a measured ``label`` count of ``check_id``, against
        ``window``, the earliest and latest count the model allows."""
        self.scoreboard.write_event_expect(check_id, label, *window)
        self.scoreboard.write_event(check_id, label, value)

    def watch_results(self, check_id: str) -> None:
        """Have the timing monitor record each DMI result's way back and the scoreboard
        grade it under ``check_id`` against the TDR model's window."""
        self.timing.watch_results()
        self.scoreboard.watch_results(check_id, self.predictor.tdr.predict_result_window())
        self._results_watched = True

    def stop_results(self) -> None:
        """Stop what ``watch_results`` started."""
        self.timing.stop()
        self.scoreboard.stop_results()
        self._results_watched = False

    @property
    def tck_stop_check(self) -> str:
        """The TCK-stop check that applies at the build's SYNC_STAGES."""
        return self.scoreboard.cfg.tck_stop_check

    def tck_stop_nets(self) -> tuple[int, ...]:
        """The bridge observables and o_dmi_tdr, as they stand now."""
        return tuple(self.observe(net) for net in TCK_STOP_NETS)

    def begin_tck_stop(self) -> None:
        """TCK stops: the scoreboard takes the bridge and o_dmi_tdr as the state every
        i_clk edge of the stop must show."""
        self.scoreboard.write_hold_start(self.tck_stop_nets())

    def sample_tck_stop(self, edge: int) -> None:
        """The bridge and o_dmi_tdr at i_clk rising edge ``edge`` of the stop."""
        self.scoreboard.write_hold_sample(self.tck_stop_nets(), edge)

    def end_tck_stop(self) -> None:
        """TCK is about to run again."""
        self.scoreboard.write_hold_end()

    # --- DMI ------------------------------------------------------------------------------
    async def scan(self, din: int, *, idle: int | None = None, update_edges: int = 1) -> int:
        """One DR scan with ``idle`` Run-Test/Idle cycles, or the host's drawn count, and the
        update strobe held over ``update_edges`` TCK rising edges; returns the captured
        word."""
        return await self.host.scan(din, idle=idle, update_edges=update_edges)

    async def dmi(
        self,
        op: DmiOp,
        addr: int,
        wdata: int = 0,
        *,
        max_polls: int = MAX_POLLS,
        clear_busy: bool = True,
    ) -> DmiResult:
        """One DMI op flow, see ``TdrHost.dmi``."""
        return await self.host.dmi(op, addr, wdata, max_polls=max_polls, clear_busy=clear_busy)

    async def dmi_write(self, addr: int, data: int) -> None:
        """DMI write that must complete with status OK."""
        result = await self.dmi(DmiOp.WRITE, addr, data)
        self._check_dmi_status(result, f"DMI write 0x{addr:02x}")

    async def dmi_read(self, addr: int) -> int:
        """DMI read that must complete with status OK; returns the data."""
        result = await self.dmi(DmiOp.READ, addr)
        self._check_dmi_status(result, f"DMI read 0x{addr:02x}")
        if addr == SBCS:
            self.sbcs_reads += 1
            self.sbbusy_reads += (result.rdata >> SBCS_SBBUSY) & 1
        return result.rdata

    async def dmi_clear(self) -> None:
        """Op 3 and a NOP; the scoreboard grades the capture after op 3 with the TDR model."""
        await self.scan(tdr_word(DmiOp.CLEAR, 0, 0))
        await self.scan(tdr_word(DmiOp.NOP, 0, 0))

    async def scan_sticky(self, words: Sequence[int], what: str) -> None:
        """With the sticky busy status set, scan each update word and then a NOP; the
        scoreboard grades each capture, and any DMI request or bus access, with the TDR
        model (CHK-TDR-STICKY-IGNORE)."""
        self.log.info("%s: %d updates with the sticky busy set", what, len(words))
        for word in [*words, tdr_word(DmiOp.NOP, 0, 0)]:
            await self.scan(word)

    def _check_dmi_status(self, result: DmiResult, what: str) -> None:
        if result.retries:
            return
        self.checker.expect_true(
            "CHK-TDR-NEVER-FAILED",
            passed=result.status is DmiStatus.OK,
            detail=f"{what}: status {result.status.name}",
        )

    # --- Memory: the bytes the test expects in the subordinate ----------------------------
    def address_range(self, offset: int, span: int) -> tuple[int, int]:
        """Base and length of a test's range ``span`` bytes long at ``offset`` into the test
        region, or the whole space when the build's address space does not reach past it."""
        space = 1 << self.build.addr_width
        if self.region + offset + span <= space:
            return self.region + offset, span
        return 0, space

    def mem_fill(self, base: int, nbytes: int) -> None:
        """Random bytes in the subordinate memory, the test's image and the SBA model."""
        data = bytes(self.rng.getrandbits(8) for _ in range(nbytes))
        for offset, byte in enumerate(data):
            self.ref_mem[base + offset] = byte
            self.sub.mem[base + offset] = byte
        self.predictor.load_memory(base, data)

    def rand_data(self, size: int) -> int:
        """Random data of 2**size bytes."""
        return self.rng.getrandbits(8 << size)

    def ref_read(self, addr: int, size: int) -> int:
        """Expected value of 2**size bytes at ``addr``."""
        return sum(self.ref_mem[addr + i] << (8 * i) for i in range(1 << size))

    def ref_write(self, addr: int, size: int, data: int) -> None:
        """Apply a write of 2**size bytes to the expected image."""
        for i in range(1 << size):
            self.ref_mem[addr + i] = (data >> (8 * i)) & 0xFF

    def mem_check(self, base: int, nbytes: int, *, check_id: str = WRITE_MEMORY) -> None:
        """Every byte in [base, base + nbytes) of the subordinate matches the expected image.

        The scoreboard grades a check in SCOREBOARD_MEMORY_CHECKS against the SBA model's
        image, which takes the expected image's byte where the model leaves one unknown:
        the subordinate stores the data of a write it answers with an error. The test
        grades any other check against the expected image.
        """
        observed = [self.sub.mem.get(base + i) for i in range(nbytes)]
        if check_id in SCOREBOARD_MEMORY_CHECKS:
            predicted = self.predictor.sba.predict_memory(base, nbytes)
            expected = [
                self.ref_mem[base + i] if exp is None else exp for i, exp in enumerate(predicted)
            ]
            self.scoreboard.write_memory(check_id, base, observed, expected)
            return
        for i, got in enumerate(observed):
            exp = self.ref_mem[base + i]
            self.checker.expect_true(
                check_id,
                passed=got == exp,
                detail=f"mem[0x{base + i:x}] = {got}, expected 0x{exp:02x}",
            )

    # --- AXI transactions -----------------------------------------------------------------
    def clear_monitor(self) -> None:
        """Forget the recorded AXI transactions."""
        self.sub.clear_monitor()

    def skip_read(self) -> None:
        """Forget the oldest recorded AR, a read the scoreboard grades."""
        if self.sub.ar_q:
            self.sub.ar_q.pop(0)

    def skip_write(self) -> None:
        """Forget the oldest recorded AW and W, a write the scoreboard grades."""
        if self.sub.aw_q:
            self.sub.aw_q.pop(0)
        if self.sub.w_q:
            self.sub.w_q.pop(0)

    def bus_mark(self) -> Counter[str]:
        """The subordinate's handshake counts per channel, for ``expect_no_bus_since``."""
        return Counter(self.sub.handshakes)

    def expect_no_bus_since(self, mark: Counter[str], check_id: str, what: str) -> None:
        """No AR, AW or W handshake since ``mark`` was taken."""
        seen = Counter(self.sub.handshakes)
        seen.subtract(mark)
        self.checker.expect_true(
            check_id,
            passed=not any(seen.values()),
            detail=f"{what}: expected no AXI transaction, saw AR {seen['ar']} AW {seen['aw']} "
            f"W {seen['w']}",
        )

    def expect_no_request(self, mark: Counter[str], check_id: str, what: str) -> None:
        """ARVALID, AWVALID and WVALID are low and no request handshake has happened since
        ``mark``. With the subordinate's READY held low, a request the DUT started before the
        call waits on its channel with VALID high."""
        valid = {ch: self.observe(f"{ch}_valid") for ch in ("ar", "aw", "w")}
        seen = Counter(self.sub.handshakes)
        seen.subtract(mark)
        self.checker.expect_true(
            check_id,
            passed=not any(valid.values()) and not any(seen.values()),
            detail=f"{what}: expected no AXI request, saw ARVALID {valid['ar']} AWVALID "
            f"{valid['aw']} WVALID {valid['w']}, handshakes AR {seen['ar']} AW {seen['aw']} W "
            f"{seen['w']}",
        )

    def expect_no_bus(self, check_id: str, what: str) -> None:
        """No AXI transaction is left unchecked in the monitor."""
        sub = self.sub
        self.checker.expect_true(
            check_id,
            passed=not (sub.ar_q or sub.aw_q or sub.w_q),
            detail=f"{what}: expected no AXI transaction, saw AR {len(sub.ar_q)} AW "
            f"{len(sub.aw_q)} W {len(sub.w_q)}",
        )

    def expect_read(self, addr: int, size: int, *, check_id: str) -> None:
        """Pop the next AR: exactly one at ``addr``, AxSIZE = size and one beat on AXI4."""
        self.checker.expect_true(
            check_id, passed=bool(self.sub.ar_q), detail=f"read 0x{addr:x}: no AR"
        )
        ar = self.sub.ar_q.pop(0)
        self.checker.expect_equal(check_id, ar.addr, addr, "ARADDR")
        if not self.build.lite:
            self.checker.expect_equal("CHK-AXI-AXI4-SIZE", ar.size, size, "ARSIZE")
            self.checker.expect_equal("CHK-AXI-AXI4-SIZE", ar.len, 0, "ARLEN")

    def expect_write(
        self, addr: int, size: int, data: int, *, check_id: str, addr_check_id: str | None = None
    ) -> None:
        """Pop the next AW and W: address, AxSIZE, strobes of exactly the accessed lanes, and
        the accessed bytes on those lanes. The AW and W's presence and AWADDR grade under
        ``addr_check_id`` when it is given, and the bytes under ``check_id``."""
        sub = self.sub
        addr_id = addr_check_id or check_id
        self.checker.expect_true(
            addr_id,
            passed=bool(sub.aw_q and sub.w_q),
            detail=f"write 0x{addr:x}: AW {len(sub.aw_q)} W {len(sub.w_q)}",
        )
        aw, w = sub.aw_q.pop(0), sub.w_q.pop(0)
        off = addr % self.nbytes
        lanes_check = "CHK-AXI-LITE-FULL-WIDTH" if self.build.lite else "CHK-AXI-AXI4-SIZE"
        self.checker.expect_equal(addr_id, aw.addr, addr, "AWADDR")
        if not self.build.lite:
            self.checker.expect_equal("CHK-AXI-AXI4-SIZE", aw.size, size, "AWSIZE")
            self.checker.expect_equal("CHK-AXI-AXI4-SIZE", aw.len, 0, "AWLEN")
        self.checker.expect_equal(lanes_check, w.strb, ((1 << (1 << size)) - 1) << off, "WSTRB")
        for i in range(1 << size):
            got = (w.data >> (8 * (off + i))) & 0xFF
            self.checker.expect_equal(
                check_id, got, (data >> (8 * i)) & 0xFF, f"write 0x{addr:x}: W lane {off + i}"
            )

    # --- System bus access ----------------------------------------------------------------
    async def sba_set_addr(self, addr: int) -> None:
        """sbaddress1 when the address is wider than 32 bits, then sbaddress0."""
        if self.build.addr_width > 32:
            await self.dmi_write(SBADDRESS1, addr >> 32)
        await self.dmi_write(SBADDRESS0, addr & WORD_MASK)

    async def sba_poll(self) -> int:
        """Poll sbcs until sbbusy = 0; returns that sbcs value. Reaching sbbusy = 0 within
        MAX_POLLS is the host's own CHK-ENV-SBA-POLL obligation; the scoreboard grades the
        sbbusy of every sbcs read."""
        value = 0
        for _ in range(MAX_POLLS):
            value = await self.dmi_read(SBCS)
            if not (value >> SBCS_SBBUSY) & 1:
                break
        self._unpolled_access = False
        self.checker.expect_true(
            "CHK-ENV-SBA-POLL",
            passed=not (value >> SBCS_SBBUSY) & 1,
            detail=f"sbbusy = 1 after {MAX_POLLS} polls, sbcs 0x{value:08x}",
        )
        return value

    async def sba_idle(
        self, exp_err: int = 0, *, exp_busyerr: bool = False, check_id: str | None = None
    ) -> None:
        """Poll sbcs until sbbusy = 0, then check sbbusyerror and, with ``check_id`` and no
        ``exp_err``, sberror = 0 under ``check_id``. The scoreboard compares every sbcs read,
        sberror included, with the SBA model, and grades the sberror ``exp_err`` names under
        its error's check (CHK-SBA-BUS-ERROR, CHK-SBA-MISALIGNED, CHK-SBA-OVERSIZE)."""
        value = await self.sba_poll()
        err = (value >> SBCS_SBERROR_LSB) & 7
        if check_id is not None and not exp_err:
            self.checker.expect_equal(check_id, err, 0, "sberror")
        self.checker.expect_equal(
            "CHK-SBA-SBBUSYERROR", (value >> SBCS_SBBUSYERROR) & 1, int(exp_busyerr), "sbbusyerror"
        )

    async def sba_write_data(self, size: int, data: int) -> None:
        """sbdata1 for a 64-bit access, then sbdata0, which starts the write."""
        if size == SIZE_64:
            await self.dmi_write(SBDATA1, data >> 32)
        await self.dmi_write(SBDATA0, data & WORD_MASK)

    async def sba_read_data(self, size: int) -> int:
        """sbdata1 first, which starts nothing, then sbdata0."""
        hi = await self.dmi_read(SBDATA1) if size == SIZE_64 else 0
        lo = await self.dmi_read(SBDATA0)
        return ((hi << 32) | lo) & size_mask(size)

    async def sba_write(self, addr: int, size: int, data: int) -> None:
        """Single write in the order of the programming examples of doc/tdr2axi.md. The
        address write with sbreadonaddr clear and the sbdata1 write start no bus access; the
        subordinate holds READY low over both, so a request either starts waits on its channel
        with VALID high when it is checked."""
        start = self._begin_access()
        self.sub.stall = True
        try:
            mark = self.bus_mark()
            await self.dmi_write(SBCS, sbcs_value(size))
            await self.sba_set_addr(addr)
            self.expect_no_request(
                mark,
                "CHK-SBA-NO-READ-ON-ADDR",
                f"sbaddress write 0x{addr:x} with sbreadonaddr clear, READY held low",
            )
            if size == SIZE_64:
                mark = self.bus_mark()
                await self.dmi_write(SBDATA1, data >> 32)
                self.expect_no_request(
                    mark,
                    "CHK-SBA-SBDATA1-NO-BUS",
                    "sbdata1 write of a 64-bit access, READY held low",
                )
        finally:
            self.sub.stall = False
        await self.dmi_write(SBDATA0, data & WORD_MASK)
        self._unpolled_access = True
        self._check_steps("CHK-ENV-SBA-WRITE-STEPS", start, self._write_steps(size))
        await self.sba_idle()

    async def sba_read(self, addr: int, size: int) -> int:
        """Single read in the order of the programming examples of doc/tdr2axi.md."""
        start = self._begin_access()
        await self.dmi_write(SBCS, sbcs_value(size, readonaddr=True))
        await self.sba_set_addr(addr)
        self._unpolled_access = True
        self._check_steps("CHK-ENV-SBA-READ-STEPS", start, self._read_steps())
        data = await self.sba_read_data(size)
        await self.sba_idle()
        return data

    async def sba_block_write(self, base: int, size: int, data: list[int]) -> None:
        """Block write: sbautoincrement, the start address, then one sbdata write per element."""
        await self.dmi_write(SBCS, sbcs_value(size, autoinc=True))
        await self.sba_set_addr(base)
        for value in data:
            await self.sba_write_data(size, value)
        await self.sba_idle()

    async def sba_block_read(self, base: int, size: int, count: int) -> list[int]:
        """Block read: sbreadonaddr, sbreadondata and sbautoincrement, the start address,
        then one sbdata read per element, with sbreadondata cleared before the last so
        nothing past the end is read: that read starts no bus access and leaves sbaddress0
        where it was. The subordinate holds READY low over the last read, so a request it
        starts waits on its channel with VALID high when it is checked."""
        start = len(self.host.accesses)
        await self.dmi_write(SBCS, sbcs_value(size, readonaddr=True, readondata=True, autoinc=True))
        await self.sba_set_addr(base)
        data = [await self.sba_read_data(size) for _ in range(count - 1)]
        await self.sba_idle()
        await self.dmi_write(SBCS, sbcs_value(size, autoinc=True))
        addr_before = await self.dmi_read(SBADDRESS0)
        mark = self.bus_mark()
        self.sub.stall = True
        try:
            data.append(await self.sba_read_data(size))
            self.expect_no_request(
                mark,
                "CHK-SBA-READ-NO-READONDATA",
                "sbdata0 read with sbreadondata clear, READY held low",
            )
        finally:
            self.sub.stall = False
        await self.sba_idle()
        self.expect_no_bus_since(
            mark, "CHK-SBA-READ-NO-READONDATA", "sbdata0 read with sbreadondata clear"
        )
        self.checker.expect_equal(
            "CHK-SBA-READ-NO-READONDATA",
            await self.dmi_read(SBADDRESS0),
            addr_before,
            "sbaddress0 after an sbdata0 read with sbreadondata clear",
        )
        self._check_block_read_steps(start)
        return data

    # --- Private: scoreboard feed ---------------------------------------------------------
    def _scoreboard_arrival(self, arrival: ResultArrival) -> None:
        """A result arrival for the scoreboard, while ``watch_results`` runs."""
        if self._results_watched:
            self.scoreboard.write_result_arrival(
                edges=arrival.edges, read=arrival.read, word=arrival.word, resp=arrival.resp
            )

    def _scoreboard_edge(self, *, capture: bool, update_word: int | None) -> None:
        """One TCK rising edge for the scoreboard; an X or Z capture word is passed as None."""
        captured = None
        if capture:
            raw = self._dut.tdr_in.value
            captured = int(raw) if raw.is_resolvable else None
        self.scoreboard.write_tck_edge(update_word=update_word, capture=capture, captured=captured)

    async def _feed_scoreboard(self) -> None:
        """The DM's DMI handshakes and the R and B response codes at each i_clk rising edge
        out of reset, with the answers the predictor published at that edge, read under
        ``ReadOnly`` once it has run. A reset within the time step drops the edge's events."""
        d = self._dut
        while True:
            await RisingEdge(d.clk)
            if in_reset(d):
                continue
            resets = self._resets
            request = None
            if resolve(d.dmi_req_valid) and resolve(d.dmi_req_ready):
                request = (resolve(d.dmi_req_op), resolve(d.dmi_req_addr), resolve(d.dmi_req_data))
            response = None
            if resolve(d.dmi_resp_valid) and resolve(d.dmi_resp_ready):
                raw = d.dmi_resp_data.value
                response = (int(raw) if raw.is_resolvable else None, resolve(d.dmi_resp_resp))
            bus_error = None
            if resolve(d.r_valid) and resolve(d.r_ready):
                bus_error = resolve(d.r_resp) >= RESP_ERROR_MIN
            elif resolve(d.b_valid) and resolve(d.b_ready):
                bus_error = resolve(d.b_resp) >= RESP_ERROR_MIN
            await ReadOnly()
            answers = self.predictor.dm_answers[self._answers_fed :]
            self._answers_fed = len(self.predictor.dm_answers)
            if self._resets != resets:
                continue
            self._feed_edge(request, answers, response, bus_error)

    def _feed_edge(
        self,
        request: tuple[int, int, int] | None,
        answers: Sequence[DmAnswer],
        response: tuple[int | None, int] | None,
        bus_error: bool | None,
    ) -> None:
        sb = self.scoreboard
        if request is not None:
            sb.write_dmi_request(*request)
        for answer in answers:
            sb.write_dm_answer(answer)
        if response is not None:
            sb.write_dmi_response(*response)
        if bus_error is not None:
            sb.write_bus_response(error=bus_error)

    async def _watch_reset(self) -> None:
        """i_arst_n resets the DM side and the TCK side of the scoreboard while i_test_mode =
        0, and a change of i_test_mode that hands control to a low reset input resets it in
        that input's scope; ``_watch_scan_reset`` applies i_arst_scan_n falls."""
        d = self._dut
        while True:
            arst_fell = FallingEdge(d.rst_n)
            fired = await First(arst_fell, Edge(d.test_mode))
            if fired is arst_fell:
                if reset_input(d) != "rst_n":
                    continue
            elif not entered_by_mode_change(d):
                continue
            self._resets += 1
            if reset_input(d) == "rst_scan_n":
                self.scoreboard.reset("RST-SCAN")
            else:
                self.scoreboard.reset("RST-ARST-BUS")
                self.scoreboard.reset("RST-ARST-TCK")

    async def _watch_scan_reset(self) -> None:
        """With i_test_mode = 1, i_arst_scan_n resets both sides in place of i_arst_n."""
        while True:
            await FallingEdge(self._dut.rst_scan_n)
            if resolve(self._dut.test_mode):
                self._resets += 1
                self.scoreboard.reset("RST-SCAN")

    # --- Private: assertions --------------------------------------------------------------
    def _sva_counts(self) -> dict[str, tuple[int, int]]:
        d = self._dut
        return {
            check_id: (resolve(getattr(d, fail)), resolve(getattr(d, hit)))
            for check_id, (fail, hit) in SVA_NETS.items()
        }

    def _grade_assertions(self) -> None:
        """Fail each assertion check whose failure counter rose during this test, and pass
        one with no failure whose antecedent held."""
        now = self._sva_counts()
        for check_id, (fails, hits) in now.items():
            fails_at_setup, hits_at_setup = self._sva_at_setup[check_id]
            failures = fails - fails_at_setup
            if failures:
                self.checker.record(
                    check_id,
                    passed=False,
                    detail=f"{failures} assertion failures in this test; the simulator log "
                    "names each",
                )
            elif hits > hits_at_setup:
                self.checker.record(check_id, passed=True, detail="")

    # --- Private: SBA step checks ---------------------------------------------------------
    def _begin_access(self) -> int:
        self.checker.record(
            "CHK-ENV-SBA-POLL",
            passed=not self._unpolled_access,
            detail="a single access starts before the previous one was polled to sbbusy = 0",
        )
        return len(self.host.accesses)

    def _write_steps(self, size: int) -> list[tuple[DmiOp, int]]:
        steps = self._read_steps()
        if size == SIZE_64:
            steps.append((DmiOp.WRITE, SBDATA1))
        steps.append((DmiOp.WRITE, SBDATA0))
        return steps

    def _read_steps(self) -> list[tuple[DmiOp, int]]:
        steps = [(DmiOp.WRITE, SBCS)]
        if self.build.addr_width > 32:
            steps.append((DmiOp.WRITE, SBADDRESS1))
        steps.append((DmiOp.WRITE, SBADDRESS0))
        return steps

    def _check_steps(self, check_id: str, start: int, steps: list[tuple[DmiOp, int]]) -> None:
        seen = [(a.op, a.addr) for a in self.host.accesses[start:]]
        self.checker.record(
            check_id, passed=seen == steps, detail=f"DMI steps {seen}, expected {steps}"
        )

    def _check_block_read_steps(self, start: int) -> None:
        ran: list[DmiAccess] = self.host.accesses[start:]
        setup = sbcs_value(0, readonaddr=True, readondata=True, autoinc=True)
        first = ran[0] if ran else None
        reads = [i for i, a in enumerate(ran) if (a.op, a.addr) == (DmiOp.READ, SBDATA0)]
        clears = [
            i
            for i, a in enumerate(ran)
            if (a.op, a.addr) == (DmiOp.WRITE, SBCS) and not (a.data >> SBCS_SBREADONDATA) & 1
        ]
        problems = []
        if (
            first is None
            or (first.op, first.addr) != (DmiOp.WRITE, SBCS)
            or (first.data & setup != setup)
        ):
            problems.append(
                "sbcs is not first written with sbreadonaddr, sbreadondata and sbautoincrement"
            )
        if (
            not reads
            or not clears
            or not clears[-1] < reads[-1]
            or (len(reads) > 1 and clears[-1] < reads[-2])
        ):
            problems.append("sbreadondata is not cleared between the last two sbdata0 reads")
        self.checker.record(
            "CHK-ENV-SBA-BLOCK-READ-STEPS", passed=not problems, detail="; ".join(problems)
        )
