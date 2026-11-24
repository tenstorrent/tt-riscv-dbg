# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of ``TdrHost``'s arithmetic and its own checks; plain Python, no simulator.

They cover the Run-Test/Idle counts the host derives from the clock periods, the update
word and sbcs packing, the busy backoff, the Run-Test/Idle draw, the edges a scan makes
before its update, the scan-shape check over the strobes the host samples, a held update
strobe included, the Run-Test/Idle check before a side-effecting sbdata0 read, and
``resolve`` on a bit vector and on the plain int a simulator may give for an SV int.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

from cocotb.binary import BinaryValue

ENV_DIR = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ENV_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {ENV_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


host_mod = _load("tdr_host")

PREV, DIN = 0x155_5555_5555, 0x0AA_AAAA_AAAA
CAPTURE_EXIT = 2


def cfg(clk_ps: int, tck_ps: int, sync_stages: int = 3, **kwargs: int) -> object:
    return host_mod.TdrHostCfg(tck_ps=tck_ps, clk_ps=clk_ps, sync_stages=sync_stages, **kwargs)


class Recorder:
    """A ``Reporter`` that keeps every verdict."""

    def __init__(self) -> None:
        self.verdicts: list[tuple[str, bool, str]] = []

    def __call__(self, check_id: str, *, passed: bool, detail: str) -> None:
        self.verdicts.append((check_id, passed, detail))

    def last(self, check_id: str) -> bool:
        return [passed for cid, passed, _ in self.verdicts if cid == check_id][-1]


def host(config: object | None = None, rng: random.Random | None = None) -> tuple:
    report = Recorder()
    return host_mod.TdrHost(None, config or cfg(1000, 20000), report, rng=rng), report


def scan_edges(
    *, pad: int = 0, pause: int = 0, idle: int = 2, update_edges: int = 1
) -> list[tuple[int, int, int]]:
    """The (capture, update, update word) samples of one scan as the host makes it."""
    pre_update = 2 + 1 + (host_mod.TDR_LEN + pad - 1) + 1 + (pause + 1 if pause else 0) + 1
    edges = [(int(i == CAPTURE_EXIT), 0, PREV) for i in range(pre_update)]
    edges += [(0, int(i < update_edges), DIN) for i in range(1 + idle)]
    return edges


class CfgTest(unittest.TestCase):
    """The Run-Test/Idle counts the host derives from the periods and SYNC_STAGES."""

    def test_readondata_idle(self) -> None:
        """N = SYNC_STAGES + 3 + floor((SYNC_STAGES + 3) x T_clk / T_tck)."""
        self.assertEqual(cfg(1000, 20000).readondata_idle, 6)
        self.assertEqual(cfg(1000, 20000, sync_stages=4).readondata_idle, 7)
        self.assertEqual(cfg(2000, 1000).readondata_idle, 18)
        self.assertEqual(cfg(20000, 1000).readondata_idle, 126)
        self.assertEqual(cfg(19000, 20000).readondata_idle, 11)

    def test_scan_len_and_pipe_idle(self) -> None:
        self.assertEqual(cfg(1000, 20000, pad_bits=0).scan_len, 41)
        self.assertEqual(cfg(1000, 20000, pad_bits=7).scan_len, 48)
        self.assertEqual(cfg(1000, 20000).pipe_idle, 7)
        self.assertEqual(cfg(20000, 1000).pipe_idle, 6 + 320)

    def test_reset_ready_idle(self) -> None:
        """ceil((SYNC_STAGES + 1) x max(1, r)) + ceil(27 x (1 + r) + max(1, r)), r the
        period ratio T_clk / T_tck."""
        self.assertEqual(cfg(1000, 20000).reset_ready_idle, 4 + 30)
        self.assertEqual(cfg(20000, 1000).reset_ready_idle, 80 + 587)
        self.assertEqual(cfg(2000, 1000, sync_stages=4).reset_ready_idle, 10 + 83)


class PackingTest(unittest.TestCase):
    def test_tdr_word(self) -> None:
        """Address 40:34, data 33:2, op 1:0, data cut to 32 bits."""
        word = host_mod.tdr_word(host_mod.DmiOp.WRITE, 0x7F, 0x1_DEAD_BEEF)
        self.assertEqual(word, (0x7F << 34) | (0xDEAD_BEEF << 2) | 2)
        self.assertEqual(host_mod.tdr_word(3, 0, 0), 3)

    def test_sbcs_value(self) -> None:
        self.assertEqual(host_mod.sbcs_value(2), 2 << 17)
        self.assertEqual(
            host_mod.sbcs_value(3, readonaddr=True, readondata=True, autoinc=True),
            (1 << 20) | (3 << 17) | (1 << 16) | (1 << 15),
        )


class BackoffTest(unittest.TestCase):
    def test_raised_idle(self) -> None:
        """One more plus a tenth, as OpenOCD raises its DMI busy delay."""
        self.assertEqual([host_mod.raised_idle(i) for i in (0, 9, 10, 100)], [1, 10, 12, 111])

    def test_busy_backoffs(self) -> None:
        self.assertEqual(host_mod.busy_backoffs(0, 6), 6)
        self.assertEqual(host_mod.busy_backoffs(6, 6), 0)
        self.assertEqual(host_mod.busy_backoffs(10, 12), 1)
        self.assertEqual(host_mod.busy_backoffs(1, 126), 31)


class DrawIdleTest(unittest.TestCase):
    def test_below_n_is_fixed(self) -> None:
        h, _ = host(cfg(1000, 20000, idle_cycles=2, idle_extra=6), random.Random(1))
        self.assertEqual({h.draw_idle() for _ in range(50)}, {2})

    def test_draw_from_n_to_n_plus_extra(self) -> None:
        h, _ = host(cfg(1000, 20000, idle_cycles=6, idle_extra=6), random.Random(1))
        drawn = {h.draw_idle() for _ in range(400)}
        self.assertEqual(drawn, set(range(6, 13)))

    def test_no_rng_is_fixed(self) -> None:
        h, _ = host(cfg(1000, 20000, idle_cycles=6, idle_extra=6))
        self.assertEqual(h.draw_idle(), 6)


class ScanShapeTest(unittest.TestCase):
    """CHK-TDRHOST-CAPTURE-DR and CHK-TDRHOST-UPDATE-DR over the sampled strobes."""

    def check(self, edges: list, idle: int, update_edges: int = 1) -> Recorder:
        h, report = host()
        h._edges = edges
        h._check_scan_shape(PREV, DIN, idle, update_edges)
        return report

    def test_paths_pass(self) -> None:
        for pad in (0, 7):
            for pause in (0, 1, 5):
                with self.subTest(pad=pad, pause=pause):
                    report = self.check(scan_edges(pad=pad, pause=pause, idle=3), 3)
                    self.assertTrue(report.last("CHK-TDRHOST-CAPTURE-DR"))
                    self.assertTrue(report.last("CHK-TDRHOST-UPDATE-DR"))

    def test_held_update_strobe(self) -> None:
        edges = scan_edges(idle=3, update_edges=2)
        self.assertTrue(self.check(edges, 3, update_edges=2).last("CHK-TDRHOST-UPDATE-DR"))
        self.assertFalse(self.check(edges, 3, update_edges=1).last("CHK-TDRHOST-UPDATE-DR"))
        single = scan_edges(idle=3)
        self.assertFalse(self.check(single, 3, update_edges=2).last("CHK-TDRHOST-UPDATE-DR"))

    def test_capture_at_another_edge_fails(self) -> None:
        edges = scan_edges()
        edges[CAPTURE_EXIT] = (0, 0, PREV)
        edges[CAPTURE_EXIT + 1] = (1, 0, PREV)
        self.assertFalse(self.check(edges, 2).last("CHK-TDRHOST-CAPTURE-DR"))

    def test_update_word_early_or_late_fails(self) -> None:
        early = scan_edges()
        early[-4] = (0, 0, DIN)
        self.assertFalse(self.check(early, 2).last("CHK-TDRHOST-UPDATE-DR"))
        late = scan_edges()
        late[-1] = (0, 0, PREV)
        self.assertFalse(self.check(late, 2).last("CHK-TDRHOST-UPDATE-DR"))

    def test_edges_to_update_follows_live_path(self) -> None:
        """Counted from the host's own pad_bits and pause_cycles, which a test may change
        between scans, and equal to the scan this module models."""
        h, _ = host(cfg(1000, 20000, pad_bits=0))
        for pad in (0, 7):
            for pause in (0, 1, 5):
                with self.subTest(pad=pad, pause=pause):
                    h.pad_bits, h.pause_cycles = pad, pause
                    edges = scan_edges(pad=pad, pause=pause, idle=0)
                    self.assertEqual(h.edges_to_update, len(edges) - 1)

    def test_update_edges_below_one_is_refused(self) -> None:
        h, _ = host()
        coro = h.scan(0, idle=1, update_edges=0)
        with self.assertRaises(ValueError):
            coro.send(None)
        coro.close()


class ReadondataIdleTest(unittest.TestCase):
    """CHK-ENV-READONDATA-IDLE: Run-Test/Idle of at least N before the read."""

    def test_counts_and_grades(self) -> None:
        h, report = host()
        need = h.cfg.readondata_idle
        h._check_readondata_idle(need)
        self.assertTrue(report.last("CHK-ENV-READONDATA-IDLE"))
        h._check_readondata_idle(need - 1)
        self.assertFalse(report.last("CHK-ENV-READONDATA-IDLE"))
        self.assertEqual(h.readondata_idle_checks, 2)


class ResolveTest(unittest.TestCase):
    """``resolve`` grades the bits of a vector and passes a simulator's plain int through."""

    def test_bit_vector(self) -> None:
        sig = SimpleNamespace(_name="vec", value=BinaryValue("101"))
        self.assertEqual(host_mod.resolve(sig), 5)

    def test_unknown_bit_raises(self) -> None:
        sig = SimpleNamespace(_name="vec", value=BinaryValue("1x0"))
        with self.assertRaisesRegex(host_mod.UnknownValueError, "vec is 1x0"):
            host_mod.resolve(sig)

    def test_plain_int(self) -> None:
        sig = SimpleNamespace(_name="count", value=7)
        self.assertEqual(host_mod.resolve(sig), 7)


if __name__ == "__main__":
    unittest.main()
