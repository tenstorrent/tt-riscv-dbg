# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Spec-derived unit tests of ``TdrStatusModel``; plain Python, no simulator.

Each test cites the atoms it reproduces: the TDR layout and ops, the capture image, the
three busy rules, op 3, the busy marker, resets and TRST, the outcomes allowed before the
post-reset CDC clear sequence ends and the captured words that drop them, and the result
and reset-release windows at their SYNC_STAGES limits. The last test runs one DM busy answer from ``SbaRefModel`` through the
bridge, as the bench pairs the two models.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import unittest
from pathlib import Path
from types import ModuleType

ENV_DIR = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ENV_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {ENV_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tdr = _load("tdr_status_model")
sba = _load("sba_ref_model")

OK, BUSY = tdr.TdrStatus.SUCCESS, tdr.TdrStatus.BUSY
NOP, READ, WRITE, CLEAR = tdr.TdrOp.NOP, tdr.TdrOp.READ, tdr.TdrOp.WRITE, tdr.TdrOp.CLEAR


def word(op: int, addr: int, data: int) -> int:
    """S-TDR2AXI-0037 to S-TDR2AXI-0039: address 40:34, data 33:2, op 1:0."""
    return (addr << 34) | (data << 2) | op


def image(model: object) -> tuple[int, int, int]:
    """The exact capture prediction as (address, data, status)."""
    cap = model.predict_capture()
    return cap.addr, cap.data.allowed[0], int(cap.status)


def images(cap: object) -> set[tuple[int, int, int]]:
    """Every image a capture prediction allows, as (address, data, status), read without
    handing a word to its observer."""
    return {(i.addr, i.data.allowed[0], int(i.status)) for i in (cap, *cap.alternatives)}


def model(**kwargs: object) -> object:
    return tdr.TdrStatusModel(sync_stages=3, **kwargs)


class DecodeTest(unittest.TestCase):
    """S-TDR2AXI-0030, S-TDR2AXI-0036, X-RISCV_DEBUG-dmi-format: the 41-bit layout."""

    def test_field_limits(self) -> None:
        self.assertEqual(tdr.decode_tdr(word(3, 0x7F, 0xFFFF_FFFF)), (3, 0x7F, 0xFFFF_FFFF))
        self.assertEqual(tdr.decode_tdr(0), (0, 0, 0))
        with self.assertRaises(ValueError):
            tdr.decode_tdr(1 << 41)


class OpDecodeTest(unittest.TestCase):
    """CHK-TDR-OP-DECODE, CHK-TDR-OP3-CLEAR, CHK-DM-OP3-KEEPS-DM: S-TDR2AXI-0039,
    S-TDR2AXI-0041, X-RISCV_DEBUG-dmi-op."""

    def test_reads_and_writes_send_their_fields(self) -> None:
        for op in (READ, WRITE):
            bridge = model()
            request = bridge.predict_update(word(op, 0x38, 0x1234_5678)).request
            self.assertEqual((request.op, request.addr, request.data), (op, 0x38, 0x1234_5678))
            self.assertEqual(bridge.pending, op)

    def test_nop_and_op3_send_nothing(self) -> None:
        for op in (NOP, CLEAR):
            bridge = model()
            self.assertIsNone(bridge.predict_update(word(op, 0x10, 0x1)).request)
            self.assertIsNone(bridge.pending)
            self.assertEqual(image(bridge), (0x10, 0x1, OK))


class CaptureImageTest(unittest.TestCase):
    """CHK-TDR-CAPTURE-IMAGE, CHK-TDR-PIPELINED-READ: S-TDR2AXI-0037, S-TDR2AXI-0038,
    S-TDR2AXI-0063, X-RISCV_DEBUG-dmi-update-capture, X-RISCV_DEBUG-nop-capture."""

    def test_after_reset(self) -> None:
        self.assertEqual(image(model()), (0, 0, OK))

    def test_read_returns_in_next_capture(self) -> None:
        for follow in (NOP, READ, WRITE):
            bridge = model()
            bridge.predict_update(word(READ, 0x3C, 0x5))
            bridge.response(busy=False, allowed=(0xAABB_CCDD,), mask=0xFFFF_FFFF)
            self.assertEqual(image(bridge), (0x3C, 0xAABB_CCDD, OK))
            bridge.predict_update(word(follow, 0x3C, 0))

    def test_write_shows_its_data(self) -> None:
        bridge = model()
        bridge.predict_update(word(WRITE, 0x39, 0x0000_1000))
        bridge.response(busy=False)
        self.assertEqual(image(bridge), (0x39, 0x0000_1000, OK))

    def test_read_data_set_and_free_bits(self) -> None:
        """A read capture accepts an allowed value on compared bits and anything on free bits."""
        bridge = model()
        bridge.predict_update(word(READ, 0x38, 0))
        bridge.response(busy=False, allowed=(0x20, 0x00), mask=0xFF)
        cap = bridge.predict_capture()
        self.assertTrue(cap.matches(word(0, 0x38, 0xFF00)))
        self.assertTrue(cap.matches(word(0, 0x38, 0x20)))
        self.assertFalse(cap.matches(word(0, 0x38, 0x21)))
        self.assertFalse(cap.matches(word(3, 0x38, 0x20)))


class BusyCaptureTest(unittest.TestCase):
    """CHK-TDR-BUSY-CAPTURE: S-TDR2AXI-0043, X-RISCV_DEBUG-dmi-status."""

    def test_capture_during_read(self) -> None:
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0x77))
        self.assertEqual(image(bridge), (0x3C, 0x77, BUSY))
        bridge.response(busy=False, allowed=(0x1,), mask=0xFFFF_FFFF)
        self.assertEqual(image(bridge), (0x3C, 0x1, BUSY))

    def test_capture_during_write_is_not_busy(self) -> None:
        bridge = model()
        bridge.predict_update(word(WRITE, 0x3C, 0x77))
        self.assertEqual(image(bridge), (0x3C, 0x77, OK))


class BusyUpdateTest(unittest.TestCase):
    """CHK-TDR-BUSY-UPDATE, CHK-TDR-UPDATE-EDGE: S-TDR2AXI-0032, S-TDR2AXI-0043."""

    def test_non_op3_while_pending(self) -> None:
        """Op 0, 1 or 2 while a read or write is pending sets sticky busy, sends nothing and
        loads nothing, and busy outlasts the answer."""
        for pending in (READ, WRITE):
            for op in (NOP, READ, WRITE):
                with self.subTest(pending=pending.name, op=op.name):
                    bridge = model()
                    bridge.predict_update(word(pending, 0x3C, 0x11))
                    self.assertIsNone(bridge.predict_update(word(op, 0x39, 0x22)).request)
                    self.assertEqual((bridge.status, bridge.pending), (BUSY, pending))
                    self.assertEqual(image(bridge), (0x3C, 0x11, BUSY))
                    bridge.response(busy=False, allowed=(0x11,), mask=0xFFFF_FFFF)
                    self.assertEqual(image(bridge), (0x3C, 0x11, BUSY))

    def test_strobe_held_two_edges(self) -> None:
        bridge = model()
        scan = word(WRITE, 0x3C, 0x11)
        self.assertIsNotNone(bridge.predict_update(scan).request)
        self.assertIsNone(bridge.predict_update(scan).request)
        self.assertEqual(int(bridge.predict_capture().status), 3)


class Op3WhilePendingTest(unittest.TestCase):
    """CHK-TDR-OP3-CLEAR, CHK-TDR-BUSY-RECOVERY: S-TDR2AXI-0041, S-TDR2AXI-0043,
    X-RISCV_DEBUG-dmi-status: op 3 while an operation is pending clears the status and
    leaves the operation to complete, as ``dtmcs.dmireset`` does."""

    def test_read_pending(self) -> None:
        """A read polled early reads busy; op 3 clears it and the read's answer arrives."""
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0x77))
        self.assertEqual(image(bridge), (0x3C, 0x77, BUSY))
        self.assertIsNone(bridge.predict_update(word(CLEAR, 0x11, 0x5)).request)
        self.assertEqual((bridge.status, bridge.pending), (OK, READ))
        bridge.response(busy=False, allowed=(0xAABB_CCDD,), mask=0xFFFF_FFFF)
        self.assertEqual(image(bridge), (0x3C, 0xAABB_CCDD, OK))

    def test_write_pending(self) -> None:
        """Busy from an update behind a write clears by op 3; the write completes."""
        bridge = model()
        bridge.predict_update(word(WRITE, 0x39, 0x40))
        bridge.predict_update(word(WRITE, 0x10, 0x1))
        self.assertEqual(bridge.status, BUSY)
        self.assertIsNone(bridge.predict_update(word(CLEAR, 0x11, 0x5)).request)
        self.assertEqual((bridge.status, bridge.pending), (OK, WRITE))
        self.assertEqual(image(bridge), (0x39, 0x40, OK))
        bridge.response(busy=False)
        self.assertEqual(image(bridge), (0x39, 0x40, OK))
        self.assertEqual(bridge.predict_update(word(READ, 0x3C, 0)).request.addr, 0x3C)

    def test_capture_while_read_still_pending(self) -> None:
        """After op 3, a capture before the read's answer reads busy again."""
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0x77))
        bridge.predict_update(word(CLEAR, 0x11, 0x5))
        self.assertEqual(image(bridge), (0x3C, 0x77, BUSY))
        bridge.response(busy=False, allowed=(0x1,), mask=0xFFFF_FFFF)
        self.assertEqual(image(bridge), (0x3C, 0x1, BUSY))


class BusyDmTest(unittest.TestCase):
    """CHK-TDR-BUSY-DM: S-TDR2AXI-0007, S-TDR2AXI-0043."""

    def test_read_answered_busy_shows_the_marker(self) -> None:
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0))
        bridge.response(busy=True)
        self.assertEqual(image(bridge), (0x3C, 0xB051_B051, BUSY))

    def test_write_answered_busy_keeps_its_data(self) -> None:
        bridge = model()
        bridge.predict_update(word(WRITE, 0x39, 0x40))
        bridge.response(busy=True)
        self.assertEqual(image(bridge), (0x39, 0x40, BUSY))


class StickyTest(unittest.TestCase):
    """CHK-TDR-STICKY-IGNORE, CHK-TDR-OP3-CLEAR, CHK-TDR-BUSY-RECOVERY: S-TDR2AXI-0041,
    S-TDR2AXI-0045, X-RISCV_DEBUG-busy-retry."""

    def test_ignore_until_op3(self) -> None:
        """Sticky busy ignores every op but 3, which clears status and keeps the capture fields."""
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0))
        bridge.response(busy=True)
        for op in (READ, WRITE, NOP):
            self.assertIsNone(bridge.predict_update(word(op, 0x10, 0x9)).request)
            self.assertEqual(image(bridge), (0x3C, 0xB051_B051, BUSY))
        self.assertIsNone(bridge.predict_update(word(CLEAR, 0x11, 0x5)).request)
        self.assertEqual(image(bridge), (0x3C, 0xB051_B051, OK))
        request = bridge.predict_update(word(READ, 0x3C, 0)).request
        self.assertEqual(request.addr, 0x3C)

    def test_status_is_never_failed(self) -> None:
        """S-TDR2AXI-0039: status 2 never occurs."""
        rng = random.Random(20261122)
        bridge = model()
        for _ in range(2000):
            step = rng.randrange(4)
            if step == 0:
                bridge.predict_update(word(rng.randrange(4), rng.randrange(128), 0))
            elif step == 1:
                self.assertIn(int(bridge.predict_capture().status), (0, 3))
            elif bridge.pending is not None:
                bridge.response(busy=step == 3, allowed=(0,), mask=0)


class ResetTest(unittest.TestCase):
    """CHK-RESET-ASYNC-ANY-TIME, CHK-RESET-TRST-INDEPENDENT: S-TDR2AXI-0026, S-TDR2AXI-0052,
    S-TDR2AXI-0054, S-TDR2AXI-0055, X-IEEE1149_1-trst."""

    def test_tck_reset_clears_the_bridge(self) -> None:
        for scope in ("RST-ARST-TCK", "RST-SCAN"):
            bridge = model()
            bridge.predict_update(word(READ, 0x3C, 0x5))
            bridge.predict_capture()
            bridge.reset(scope)
            self.assertIsNone(bridge.pending)
            self.assertEqual(image(bridge), (0, 0, OK))

    def test_trst_and_bus_reset_leave_it(self) -> None:
        """TRST, RST-DMACTIVE and the bus reset keep the pending operation and its answer."""
        bridge = model()
        bridge.predict_update(word(READ, 0x3C, 0x5))
        bridge.trst()
        bridge.reset("RST-DMACTIVE")
        self.assertFalse(bridge.settling)
        bridge.reset("RST-ARST-BUS")
        self.assertTrue(bridge.settling)
        self.assertEqual(bridge.pending, READ)
        bridge.response(busy=False, allowed=(0x99,), mask=0xFFFF_FFFF)
        self.assertEqual(image(bridge), (0x3C, 0x99, OK))
        with self.assertRaises(ValueError):
            bridge.reset("no such scope")

    def test_answer_without_operation(self) -> None:
        with self.assertRaises(tdr.TdrStimulusError):
            model().response(busy=False)


class ReadinessTest(unittest.TestCase):
    """CHK-RESET-ASYNC-ANY-TIME, CHK-TDR-BUSY-RECOVERY: S-TDR2AXI-0043, S-TDR2AXI-0052,
    S-TDR2AXI-0053, S-TDR2AXI-0088, T-TDR2AXI-cdc-clear-20to1, T-TDR2AXI-cdc-clear-1to2:
    until the clear sequence ends an operation may be answered busy, and op 3 recovers."""

    def _released(self) -> object:
        bridge = model()
        bridge.reset("RST-ARST-TCK")
        self.assertTrue(bridge.settling)
        return bridge

    def test_capture_after_release_is_exact(self) -> None:
        cap = self._released().predict_capture()
        self.assertEqual(cap.alternatives, ())
        self.assertTrue(cap.matches(0))
        self.assertFalse(cap.matches(word(BUSY, 0, 0)))

    def test_first_operation_accepted_or_busy(self) -> None:
        """The request may not be sent, and the capture shows either outcome."""
        bridge = self._released()
        update = bridge.predict_update(word(WRITE, 0x10, 1))
        self.assertTrue(update.optional)
        self.assertEqual((update.request.addr, update.request.data), (0x10, 1))
        cap = bridge.predict_capture()
        self.assertEqual(images(cap), {(0x10, 1, OK), (0, 0, BUSY), (0x10, 1, BUSY)})
        self.assertEqual(bridge.pending, WRITE)

    def test_answer_rules_out_the_busy_outcome(self) -> None:
        """An answer proves the operation crossed: one outcome and an exact image remain."""
        bridge = self._released()
        bridge.predict_update(word(READ, 0x11, 0))
        bridge.predict_update(word(CLEAR, 0, 0))
        bridge.response(busy=False, allowed=(0x3002,), mask=0xFFFF_FFFF)
        self.assertFalse(bridge.settling)
        cap = bridge.predict_capture()
        self.assertEqual(cap.alternatives, ())
        self.assertEqual(image(bridge), (0x11, 0x3002, OK))
        bridge.predict_update(word(CLEAR, 0, 0))
        self.assertFalse(bridge.predict_update(word(READ, 0x11, 0)).optional)

    def test_op3_and_retry_recover_a_busy_outcome(self) -> None:
        """After the sequence ends, op 3 and a retry bring every outcome to one image."""
        bridge = self._released()
        bridge.predict_update(word(READ, 0x11, 0))
        bridge.settled()
        bridge.predict_update(word(CLEAR, 0, 0))
        cap = bridge.predict_capture()
        self.assertEqual(images(cap), {(0, 0, OK), (0x11, 0, OK), (0x11, 0, BUSY)})
        update = bridge.predict_update(word(READ, 0x11, 0))
        self.assertTrue(update.optional)
        bridge.response(busy=False, allowed=(0x5,), mask=0xFFFF_FFFF)
        bridge.predict_update(word(CLEAR, 0, 0))
        update = bridge.predict_update(word(WRITE, 0x10, 1))
        self.assertFalse(update.optional)
        bridge.response(busy=False)
        self.assertEqual(bridge.predict_capture().alternatives, ())
        self.assertEqual(image(bridge), (0x10, 1, OK))

    def test_settled_bridge_accepts_exactly(self) -> None:
        bridge = self._released()
        bridge.settled()
        update = bridge.predict_update(word(WRITE, 0x10, 1))
        self.assertFalse(update.optional)
        self.assertEqual(bridge.predict_capture().alternatives, ())


class CapturePruneTest(unittest.TestCase):
    """CHK-TDR-CAPTURE-EDGE, CHK-TDR-OP-DECODE, CHK-TDR-BUSY-CAPTURE, CHK-RESET-ASYNC-ANY-TIME:
    S-TDR2AXI-0037, S-TDR2AXI-0038, S-TDR2AXI-0039, S-TDR2AXI-0043, T-TDR2AXI-cdc-clear-20to1,
    T-TDR2AXI-cdc-clear-1to2: before the clear sequence ends, the word captured at an edge
    drops each outcome whose image differs from it, and no other."""

    def _released(self) -> object:
        bridge = model()
        bridge.reset("RST-ARST-TCK")
        return bridge

    @staticmethod
    def _scan(bridge: object, update: int, seen: int) -> object:
        """One scan as ``TdrHost`` runs it: the capture edge, the captured ``seen`` handed to
        the image's ``matches``, then the update edge; returns the update's prediction."""
        bridge.predict_capture().matches(seen)
        return bridge.predict_update(update)

    def test_busy_capture_drops_the_refused_outcomes(self) -> None:
        """A dmcontrol read scanned into the clear stays pending. After op 3 the capture reads
        busy, which only the outcome that accepted the read gives, so the read update after
        it sends nothing; the requests are the first read, its retry and the dmcontrol
        write."""
        bridge = self._released()
        busy = word(BUSY, sba.DMCONTROL, 0)
        read = word(READ, sba.DMCONTROL, 0)
        sent = []

        def scan(update: int, seen: int) -> object:
            expect = self._scan(bridge, update, seen)
            if expect.request is not None:
                request = expect.request
                sent.append((request.op, request.addr, request.data, expect.optional))
            return expect

        scan(read, 0)
        scan(word(NOP, 0, 0), busy)
        scan(read, busy)
        scan(word(CLEAR, 0, 0), busy)
        expect = scan(read, busy)
        self.assertEqual((expect.request, expect.optional), (None, False))
        self.assertEqual((bridge.pending, bridge.status), (READ, BUSY))
        scan(word(CLEAR, 0, 0), busy)
        bridge.response(busy=False, allowed=(1,), mask=0xFFFF_FFFF)
        scan(read, word(OK, sba.DMCONTROL, 1))
        bridge.response(busy=False, allowed=(1,), mask=0xFFFF_FFFF)
        scan(word(WRITE, sba.DMCONTROL, 1), word(OK, sba.DMCONTROL, 1))
        self.assertEqual(
            sent,
            [
                (READ, sba.DMCONTROL, 0, True),
                (READ, sba.DMCONTROL, 0, False),
                (WRITE, sba.DMCONTROL, 1, False),
            ],
        )

    def test_word_several_outcomes_give_keeps_them_all(self) -> None:
        """Busy with the read's address is the image of both the accepted read and the
        refused read that loaded its fields, so only the refused read that kept them goes."""
        bridge = self._released()
        self._scan(bridge, word(READ, 0x10, 0), 0)
        self._scan(bridge, word(CLEAR, 0, 0), word(BUSY, 0x10, 0))
        self.assertEqual(images(bridge.predict_capture()), {(0x10, 0, BUSY), (0x10, 0, OK)})

    def test_word_no_outcome_gives_drops_none(self) -> None:
        bridge = self._released()
        self._scan(bridge, word(READ, 0x10, 0), 0)
        cap = bridge.predict_capture()
        self.assertFalse(cap.matches(word(OK, 0x22, 5)))
        bridge.predict_update(word(CLEAR, 0, 0))
        self.assertEqual(
            images(bridge.predict_capture()), {(0x10, 0, BUSY), (0, 0, OK), (0x10, 0, OK)}
        )

    def test_first_word_of_the_latest_capture_applies(self) -> None:
        """A second word for the same capture, and a word for a capture the model has moved
        past, drop nothing."""
        bridge = self._released()
        bridge.predict_update(word(READ, 0x10, 0))
        cap = bridge.predict_capture()
        cap.matches(word(BUSY, 0, 0))
        cap.matches(word(BUSY, 0x10, 0))
        self.assertEqual(images(bridge.predict_capture()), {(0, 0, BUSY)})
        stale = self._released()
        stale.predict_update(word(READ, 0x10, 0))
        cap = stale.predict_capture()
        stale.predict_update(word(NOP, 0, 0))
        cap.matches(word(BUSY, 0, 0))
        self.assertEqual(images(stale.predict_capture()), {(0x10, 0, BUSY), (0, 0, BUSY)})

    def test_capture_only_a_dropped_outcome_gives_fails(self) -> None:
        """Once the busy capture after op 3 leaves the accepted read alone, a capture of
        success with the read's address, which only the refused outcomes give, is rejected."""
        bridge = self._released()
        busy = word(BUSY, 0x10, 0)
        self._scan(bridge, word(READ, 0x10, 0), 0)
        self._scan(bridge, word(CLEAR, 0, 0), busy)
        self._scan(bridge, word(READ, 0x10, 0), busy)
        self._scan(bridge, word(CLEAR, 0, 0), busy)
        cap = bridge.predict_capture()
        self.assertEqual(cap.alternatives, ())
        self.assertFalse(cap.matches(word(OK, 0x10, 0)))
        self.assertTrue(cap.matches(busy))

    def test_one_outcome_has_no_observer(self) -> None:
        self.assertIsNone(model().predict_capture().observer)
        self.assertIsNone(self._released().predict_capture().observer)

    def test_negative_knob_leaves_the_outcomes_kept(self) -> None:
        """The knob corrupts the published address, not which outcomes a word keeps."""
        bridge = model(negative=True)
        bridge.reset("RST-ARST-TCK")
        bridge.predict_update(word(READ, 0x10, 0))
        self.assertFalse(bridge.predict_capture().matches(word(BUSY, 0x10, 0)))
        bridge.predict_update(word(CLEAR, 0, 0))
        self.assertEqual(images(bridge.predict_capture()), {(0x11, 0, BUSY), (0x11, 0, OK)})


class ResetReleaseTest(unittest.TestCase):
    """CHK-RESET-SYNC-RELEASE: S-TDR2AXI-0018, S-TDR2AXI-0026, S-TDR2AXI-0052,
    S-TDR2AXI-0053, T-TDR2AXI-tck-reset-release (3 to 4 TCK edges at SYNC_STAGES = 3) and
    T-TDR2AXI-tck-reset-release-ss4 (4 to 5 at SYNC_STAGES = 4)."""

    def test_windows(self) -> None:
        """Each side releases SYNC_STAGES to SYNC_STAGES + 1 edges of its own clock later."""
        cases = (
            (3, "T-TDR2AXI-tck-reset-release"),
            (4, "T-TDR2AXI-tck-reset-release-ss4"),
            (6, "T-TDR2AXI-tck-reset-release"),
        )
        for stages, timing in cases:
            with self.subTest(sync_stages=stages):
                release = tdr.TdrStatusModel(sync_stages=stages).predict_reset_release()
                tck, clk = release.tck, release.clk
                self.assertEqual(
                    (tck.earliest, tck.latest, tck.clock, tck.timing),
                    (stages, stages + 1, "CLK-TCK", timing),
                )
                self.assertEqual(
                    (clk.earliest, clk.latest, clk.clock), (stages, stages + 1, "CLK-BUS")
                )


class ResultWindowTest(unittest.TestCase):
    """CHK-TDR-RESULT-LATENCY, CHK-TDR-TCK-STOP-HOLD, CHK-TDR-TCK-STOP-HOLD-SS4:
    S-TDR2AXI-0018, S-TDR2AXI-0049, T-TDR2AXI-resp-to-tdr (5 to 6 TCK edges at
    SYNC_STAGES = 3) and T-TDR2AXI-resp-to-tdr-ss4 (6 to 7 at SYNC_STAGES = 4)."""

    def test_window(self) -> None:
        window = model().predict_result_window()
        self.assertEqual(
            (window.earliest, window.latest, window.clock, window.timing),
            (5, 6, "CLK-TCK", "T-TDR2AXI-resp-to-tdr"),
        )
        window = tdr.TdrStatusModel(sync_stages=4).predict_result_window()
        self.assertEqual(
            (window.earliest, window.latest, window.timing), (6, 7, "T-TDR2AXI-resp-to-tdr-ss4")
        )
        with self.assertRaises(ValueError):
            tdr.TdrStatusModel(sync_stages=2)


class NegativeKnobTest(unittest.TestCase):
    """The negative knob corrupts every prediction, so a paired check must fail."""

    def test_predictions_corrupted(self) -> None:
        bridge = model(negative=True)
        self.assertFalse(bridge.predict_capture().matches(0))
        self.assertEqual(bridge.predict_update(word(READ, 0x38, 0)).request.addr, 0x39)
        self.assertEqual(bridge.predict_result_window().earliest, 7)
        self.assertEqual(bridge.predict_reset_release().tck.earliest, 5)
        self.assertEqual(bridge.predict_reset_release().clk.latest, 6)
        self.assertEqual(tdr.NEGATIVE_KNOB, "TDR2AXI_TDR_STATUS_MODEL_NEGATIVE")


class PairedModelsTest(unittest.TestCase):
    """CHK-SBA-SBBUSYERROR with CHK-TDR-BUSY-DM: an sbdata0 read during a stalled access is
    answered busy by the DM (X-RISCV_DEBUG-sbbusyerror) and the bridge shows the marker."""

    def test_collision_through_the_bridge(self) -> None:
        """The bridge shows the marker and busy; after op 3 sbcs shows sbbusyerror."""
        dm = sba.SbaRefModel(sba.SbaModelCfg(False, 32, 32))
        bridge = model()
        scans = (
            (WRITE, sba.DMCONTROL, 1),
            (WRITE, sba.SBCS, 2 << 17),
            (WRITE, sba.SBADDRESS0, 0x100),
            (WRITE, sba.SBDATA0, 0x5),
        )
        for op, addr, data in scans:
            request = bridge.predict_update(word(op, addr, data)).request
            answer = dm.predict_dmi_write(request.addr, request.data)
            bridge.response(busy=answer.busy, allowed=answer.data.allowed, mask=answer.data.mask)
        self.assertIsNotNone(answer.access)
        request = bridge.predict_update(word(READ, sba.SBDATA0, 0)).request
        answer = dm.predict_dmi_read(request.addr)
        bridge.response(busy=answer.busy, allowed=answer.data.allowed, mask=answer.data.mask)
        self.assertEqual(image(bridge), (sba.SBDATA0, 0xB051_B051, BUSY))
        self.assertIsNone(answer.access)
        bridge.predict_update(word(CLEAR, 0, 0))
        dm.bus_response(error=False)
        request = bridge.predict_update(word(READ, sba.SBCS, 0)).request
        answer = dm.predict_dmi_read(request.addr)
        bridge.response(busy=answer.busy, allowed=answer.data.allowed, mask=answer.data.mask)
        cap = bridge.predict_capture()
        self.assertEqual(int(cap.status), 0)
        self.assertTrue(all(value & (1 << 22) for value in cap.data.allowed))


if __name__ == "__main__":
    unittest.main()
