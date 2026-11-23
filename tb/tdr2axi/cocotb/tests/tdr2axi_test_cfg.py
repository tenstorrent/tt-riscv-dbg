# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Run-time configuration of one tdr2axi test, drawn from the seed by default.

``Tdr2axiTestCfg.from_knobs`` reads the knobs from the environment and, unless
TDR2AXI_RANDOMIZE is 0, draws the i_clk phase, the reset length, the scan shape and the
subordinate timing from the seed, salted by the test name so each test draws its own: the
i_clk phase over one i_clk period, in a quarter of the runs one where i_clk and TCK rising
edges coincide, a reset of 10 to 20 i_clk cycles, 0 to 7 bypass bits, Pause-DR in a third
of the runs, READY waits of 0 to 4 cycles, R and B latencies of 0 to 6 cycles and W before
AW in half the runs. Both clock periods are the knob values exactly, so a named clock ratio
holds; TDR2AXI_CLOCK_JITTER=1 draws each knob period within 20 % of its value instead.
CLK_PERIOD_PS=random draws the i_clk period from the seed, log-uniform from 20 times faster
than TCK to 8 times slower (1000 to 160000 ps at TCK = 20000 ps), with or without
TDR2AXI_RANDOMIZE. With TDR2AXI_RANDOMIZE=0 every other knob value holds and the
subordinate answers with READY high and a fixed latency.

Knobs: TDR2AXI_RANDOMIZE (1 unless set to 0), CLK_PERIOD_PS (an i_clk period or
``random``) and TCK_PERIOD_PS (the clock ratio is a run-time setting),
TDR2AXI_CLOCK_JITTER (0 unless set to 1), TDR2AXI_AXI_GLITCH (change the AXI response
inputs between i_clk edges), TDR2AXI_SBA_REF_MODEL_NEGATIVE and
TDR2AXI_TDR_STATUS_MODEL_NEGATIVE (invert the models' predictions). The seed comes from the
base test. There is no SV twin: the bench has one cocotb realization.
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field

from env.axi_sub import AxiSubTiming
from env.sba_ref_model import NEGATIVE_KNOB as SBA_NEGATIVE_KNOB
from env.tdr_status_model import NEGATIVE_KNOB as TDR_NEGATIVE_KNOB

__all__ = ["CLOCK_JITTER_KNOB", "GLITCH_KNOB", "RANDOMIZE_KNOB", "Tdr2axiTestCfg", "rng_for"]

RANDOMIZE_KNOB = "TDR2AXI_RANDOMIZE"
CLOCK_JITTER_KNOB = "TDR2AXI_CLOCK_JITTER"
GLITCH_KNOB = "TDR2AXI_AXI_GLITCH"

CLK_PERIOD_PS = 1000
TCK_PERIOD_PS = 20000
RANDOM_PERIOD = "random"
# CLK_PERIOD_PS=random spans i_clk from this many times faster than TCK to this many slower
RANDOM_CLK_FASTER = 20
RANDOM_CLK_SLOWER = 8
CLK_PHASE_PS = 137
RESET_CYCLES = 10
PAD_BITS = 7

PERIOD_SPREAD = 0.2
COINCIDENT_PHASE_SHARE = 0.25
PAD_BITS_MAX = 7
PAUSE_CYCLES_MAX = 80
READY_WAIT_MAX = 4
LATENCY_MAX = 6


def rng_for(seed: int, label: str) -> random.Random:
    """A random stream salted by ``label``, so adding a draw elsewhere leaves it unchanged."""
    return random.Random(f"{seed}:{label}")


def _knob_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _knob_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name, "")
    if not value:
        return default
    return value != "0"


def _even(value: float) -> int:
    return 2 * round(value / 2)


def _draw_clk_period(rng: random.Random, tck_ps: int) -> int:
    """An i_clk period log-uniform from tck_ps / RANDOM_CLK_FASTER to tck_ps x
    RANDOM_CLK_SLOWER, even so both half periods are whole picoseconds."""
    low = math.log(tck_ps / RANDOM_CLK_FASTER)
    high = math.log(tck_ps * RANDOM_CLK_SLOWER)
    return _even(math.exp(rng.uniform(low, high)))


def _draw_phase(rng: random.Random, clk_ps: int, tck_ps: int) -> int:
    """An i_clk phase over one i_clk period. i_clk rises at phase + clk_ps / 2 + k x clk_ps
    and TCK at tck_ps / 2 + n x tck_ps, so the phases that make some rising edges coincide
    are those congruent to (tck_ps - clk_ps) / 2 modulo gcd(clk_ps, tck_ps)."""
    if rng.random() >= COINCIDENT_PHASE_SHARE:
        return rng.randrange(clk_ps)
    step = math.gcd(clk_ps, tck_ps)
    return (tck_ps // 2 - clk_ps // 2) % step + step * rng.randrange(clk_ps // step)


@dataclass(frozen=True)
class Tdr2axiTestCfg:
    """Run-time configuration of one test: seed, clocks, reset, host and subordinate."""

    seed: int
    randomized: bool
    clk_ps: int
    tck_ps: int
    clk_drawn: bool = False
    clk_phase_ps: int = CLK_PHASE_PS
    reset_cycles: int = RESET_CYCLES
    pad_bits: int = PAD_BITS
    pause_cycles: int = 0
    axi: AxiSubTiming = field(default_factory=AxiSubTiming)
    sba_negative: bool = False
    tdr_negative: bool = False

    @classmethod
    def from_knobs(cls, name: str, *, seed: int) -> Tdr2axiTestCfg:
        """The configuration of test ``name``: drawn from ``seed`` around the knobs, or the
        knob values themselves with TDR2AXI_RANDOMIZE=0."""
        tck_ps = _knob_int("TCK_PERIOD_PS", TCK_PERIOD_PS)
        clk_drawn = os.environ.get("CLK_PERIOD_PS") == RANDOM_PERIOD
        if clk_drawn:
            clk_ps = _draw_clk_period(rng_for(seed, f"{name}:clk_period"), tck_ps)
        else:
            clk_ps = _knob_int("CLK_PERIOD_PS", CLK_PERIOD_PS)
        negatives = {
            "sba_negative": _knob_flag(SBA_NEGATIVE_KNOB, default=False),
            "tdr_negative": _knob_flag(TDR_NEGATIVE_KNOB, default=False),
        }
        glitch = _knob_flag(GLITCH_KNOB, default=False)
        if not _knob_flag(RANDOMIZE_KNOB, default=True):
            return cls(
                seed=seed,
                randomized=False,
                clk_ps=clk_ps,
                tck_ps=tck_ps,
                clk_drawn=clk_drawn,
                axi=AxiSubTiming(rsp_glitch=glitch),
                **negatives,
            )
        rng = rng_for(seed, f"{name}:test_cfg")
        clk_scale = rng.uniform(1 - PERIOD_SPREAD, 1 + PERIOD_SPREAD)
        tck_scale = rng.uniform(1 - PERIOD_SPREAD, 1 + PERIOD_SPREAD)
        if _knob_flag(CLOCK_JITTER_KNOB, default=False):
            clk_ps = clk_ps if clk_drawn else _even(clk_ps * clk_scale)
            tck_ps = _even(tck_ps * tck_scale)
        latency_max = rng.randint(0, LATENCY_MAX)
        return cls(
            seed=seed,
            randomized=True,
            clk_ps=clk_ps,
            tck_ps=tck_ps,
            clk_drawn=clk_drawn,
            clk_phase_ps=_draw_phase(rng, clk_ps, tck_ps),
            reset_cycles=rng.randint(RESET_CYCLES, 2 * RESET_CYCLES),
            pad_bits=rng.randint(0, PAD_BITS_MAX),
            pause_cycles=rng.choice((0, 0, rng.randint(1, PAUSE_CYCLES_MAX))),
            axi=AxiSubTiming(
                ready_wait_max=rng.randint(0, READY_WAIT_MAX),
                r_latency=(0, latency_max),
                b_latency=(0, rng.randint(0, LATENCY_MAX)),
                w_before_aw=rng.random() < 0.5,
                rsp_glitch=glitch,
            ),
            **negatives,
        )

    @property
    def armed_ids(self) -> frozenset[str]:
        """Checks the knobs arm: the response glitch arms CHK-AXI-NO-COMB-PATH."""
        return frozenset({"CHK-AXI-NO-COMB-PATH"}) if self.axi.rsp_glitch else frozenset()

    def describe(self) -> str:
        """The drawn knobs, for the one log line each test writes."""
        t = self.axi
        return (
            f"{RANDOMIZE_KNOB}={int(self.randomized)}: i_clk {self.clk_ps} ps"
            f"{' drawn' if self.clk_drawn else ''} at phase "
            f"{self.clk_phase_ps} ps, TCK {self.tck_ps} ps, reset {self.reset_cycles} i_clk "
            f"cycles, {self.pad_bits} bypass bits, Pause-DR {self.pause_cycles} cycles, READY "
            f"wait 0 to {t.ready_wait_max}, R latency {t.r_latency[0]} to {t.r_latency[1]}, "
            f"B latency {t.b_latency[0]} to {t.b_latency[1]}, W before AW {t.w_before_aw}, "
            f"response glitch {t.rsp_glitch}, SBA model negative {self.sba_negative}, TDR "
            f"model negative {self.tdr_negative}"
        )
