# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of ``AxiSub``'s seeded timing and its negative knob; no simulator runs.

They cover READY before VALID on the early share of accesses and never without it, the
late share of response latencies, a share of 0 drawing nothing from the seed, the write
channel orders ``w_before_aw`` and ``aw_before_w``, and the knob: ``negative_fault``
returns only a fault the caller grades, the subordinate never reads the variable, and
``arm_fault`` logs ``NEGATIVE VALIDATION:``.

``axi_sub`` imports cocotb, so these tests run in the bench's Python environment and are
skipped where cocotb is missing.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import random
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

COCOTB_DIR = Path(__file__).resolve().parents[2]
ACCESSES = 400
# Cycles a channel stays free with VALID low before VALID rises
IDLE_CYCLES = 5
# VALID of each channel with a write's AW and W both offered
WRITE_VALID = {"ar": 0, "aw": 1, "w": 1}


def _has_cocotb() -> bool:
    """Whether the cocotb package is installed; the bench's own cocotb directory, a namespace
    package from its parent, is not it."""
    try:
        return importlib.util.find_spec("cocotb.triggers") is not None
    except ModuleNotFoundError:
        return False


HAS_COCOTB = _has_cocotb()


def _load_package(name: str, directory: Path) -> ModuleType:
    """The package in ``directory`` under ``name``, so its modules import each other."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load package {name} from {directory}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _axi_sub() -> ModuleType:
    _load_package("env", COCOTB_DIR / "env")
    return importlib.import_module("env.axi_sub")


def _sub(mod: ModuleType, seed: int = 1, **timing: object) -> Any:
    """A subordinate on a 32-bit AXI4 port with ``timing``, its seed ``seed``; no signals."""
    cfg = mod.AxiSubCfg(
        lite=False,
        addr_width=32,
        data_width=32,
        prot=2,
        cache=0,
        clk_ps=1000,
        timing=mod.AxiSubTiming(**timing),
    )
    return mod.AxiSub(None, cfg, lambda *_a, **_k: None, random.Random(seed))


@unittest.skipUnless(HAS_COCOTB, "cocotb is not installed")
class AxiSubTimingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _axi_sub()

    def _sub(self, **timing: object) -> Any:
        return _sub(self.mod, **timing)

    def _early_accesses(self, sub: Any) -> int:
        """Accesses of ACCESSES whose READY is high in a cycle before VALID rises; each
        access ends at the handshake, which resets the channel's wait."""
        early = 0
        for _ in range(ACCESSES):
            before = [sub._ready_after_wait("ar", free=True, valid=0) for _ in range(IDLE_CYCLES)]
            self.assertEqual(len(set(before)), 1, "READY changed while VALID was low")
            early += before[0]
            ready = before[0]
            while not ready:
                ready = sub._ready_after_wait("ar", free=True, valid=1)
            sub._wait_left["ar"] = None
        return early

    def test_ready_waits_for_valid_without_early_share(self) -> None:
        self.assertEqual(self._early_accesses(self._sub(ready_wait_max=4)), 0)

    def test_ready_before_valid_on_early_share(self) -> None:
        early = self._early_accesses(self._sub(ready_wait_max=4, ready_early_share=0.25))
        self.assertGreater(early, ACCESSES * 0.15)
        self.assertLess(early, ACCESSES * 0.35)

    def test_ready_held_after_valid_rises_on_early_access(self) -> None:
        sub = self._sub(ready_wait_max=4, ready_early_share=1.0)
        self.assertTrue(sub._ready_after_wait("aw", free=True, valid=0))
        self.assertTrue(sub._ready_after_wait("aw", free=True, valid=1))

    def test_zero_shares_draw_nothing_while_valid_is_low(self) -> None:
        sub = self._sub(ready_wait_max=4)
        state = sub._rng.getstate()
        for _ in range(IDLE_CYCLES):
            self.assertFalse(sub._ready_after_wait("w", free=True, valid=0))
        self.assertEqual(sub._rng.getstate(), state)

    def test_zero_late_share_draws_as_the_span_alone(self) -> None:
        sub = self._sub(r_latency=(0, 6))
        twin = random.Random(1)
        for _ in range(50):
            self.assertEqual(sub._latency((0, 6)), twin.randint(0, 6))

    def test_late_share_gives_late_latencies(self) -> None:
        sub = self._sub(late_share=0.25, late_latency=(8, 12))
        latencies = [sub._latency((0, 6)) for _ in range(ACCESSES)]
        late = [x for x in latencies if x >= 8]
        self.assertTrue(all(8 <= x <= 12 for x in late))
        self.assertTrue(all(0 <= x <= 6 for x in latencies if x < 8))
        self.assertGreater(len(late), ACCESSES * 0.15)
        self.assertLess(len(late), ACCESSES * 0.35)


@unittest.skipUnless(HAS_COCOTB, "cocotb is not installed")
class AxiSubWriteOrderTest(unittest.TestCase):
    """READY of AW and W with both VALID high, before and after the first channel is taken."""

    def setUp(self) -> None:
        self.mod = _axi_sub()

    def _readys(self, sub: Any) -> tuple[bool, bool]:
        sub._next_readys(WRITE_VALID)
        return sub._aw_rdy, sub._w_rdy

    def test_default_takes_aw_and_w_together(self) -> None:
        self.assertEqual(self._readys(_sub(self.mod)), (True, True))

    def test_aw_before_w_holds_wready_until_aw_is_taken(self) -> None:
        sub = _sub(self.mod, aw_before_w=True)
        self.assertEqual(self._readys(sub), (True, False))
        sub._aw_hold.append(mock.sentinel.aw)
        self.assertEqual(self._readys(sub), (False, True))

    def test_w_before_aw_holds_awready_until_w_is_taken(self) -> None:
        sub = _sub(self.mod, w_before_aw=True)
        self.assertEqual(self._readys(sub), (False, True))
        sub._w_hold.append(mock.sentinel.w)
        self.assertEqual(self._readys(sub), (True, False))

    def test_both_orders_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.AxiSubTiming(w_before_aw=True, aw_before_w=True)


@unittest.skipUnless(HAS_COCOTB, "cocotb is not installed")
class AxiSubNegativeKnobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _axi_sub()

    def _environ(self, value: str | None) -> Any:
        env = {k: v for k, v in os.environ.items() if k != self.mod.NEGATIVE_KNOB}
        if value is not None:
            env[self.mod.NEGATIVE_KNOB] = value
        return mock.patch.dict(os.environ, env, clear=True)

    def test_knob_name(self) -> None:
        self.assertEqual(self.mod.NEGATIVE_KNOB, "TDR2AXI_AXISUB_NEGATIVE")

    def test_unset_knob_names_no_fault(self) -> None:
        with self._environ(None):
            self.assertEqual(self.mod.negative_fault(self.mod.FAULTS), "")

    def test_knob_names_a_graded_fault(self) -> None:
        with self._environ("bad_id"):
            self.assertEqual(self.mod.negative_fault({"bad_id", "early_r"}), "bad_id")

    def test_knob_ignored_by_a_test_that_does_not_grade_it(self) -> None:
        with self._environ("lost_write"):
            self.assertEqual(self.mod.negative_fault({"bad_id"}), "")

    def test_unknown_fault_raises(self) -> None:
        with self._environ("late_r"), self.assertRaises(ValueError):
            self.mod.negative_fault(self.mod.FAULTS)

    def test_subordinate_does_not_read_the_knob(self) -> None:
        with self._environ("bad_id"):
            sub = _sub(self.mod)
        self.assertEqual(sub.fault, "")

    def test_arm_fault_logs_negative_validation(self) -> None:
        sub = _sub(self.mod)
        with self.assertLogs("cocotb.tdr2axi.axi_sub", level="WARNING") as logs:
            sub.arm_fault("early_b")
        self.assertEqual(sub.fault, "early_b")
        self.assertIn("NEGATIVE VALIDATION: TDR2AXI_AXISUB_NEGATIVE=early_b", logs.output[0])

    def test_arm_fault_empty_arms_none_and_unknown_raises(self) -> None:
        sub = _sub(self.mod)
        sub.arm_fault("")
        self.assertEqual(sub.fault, "")
        with self.assertRaises(ValueError):
            sub.arm_fault("late_r")


if __name__ == "__main__":
    unittest.main()
