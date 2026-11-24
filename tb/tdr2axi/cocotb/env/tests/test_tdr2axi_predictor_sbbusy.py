# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of the sbbusy window ``Tdr2axiPredictor`` publishes for CHK-SBA-SBBUSY.

The DV reading of CHK-SBA-SBBUSY (S-TDR2AXI-0002, X-RISCV_DEBUG-sbbusy): every sbcs read
from the request that starts an access until its bus response reads sbbusy = 1; after the
response at most one sbcs read may read 1, and every later read reads 0 until the next
access starts. Each test drives the predictor edge by edge on a stand-in for the tb_top
observables, for a read and a write access, and checks both directions of the window: an
early 0 before the response and a late 1 on the second read after it are outside it.

A DMI handshake the predictor did not predict, such as one an update ignored under the
sticky busy sends (CHK-TDR-STICKY-IGNORE), raises ``PredictorSyncError`` only at the next
i_clk edge, after the scoreboard has filed it under its check.

The predictor imports cocotb, so these tests run in the bench's Python environment and
are skipped where cocotb is missing; no simulator runs.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

COCOTB_DIR = Path(__file__).resolve().parents[2]


def _has_cocotb() -> bool:
    """Whether the cocotb package is installed; the bench's own cocotb directory, a namespace
    package from its parent, is not it."""
    try:
        return importlib.util.find_spec("cocotb.triggers") is not None
    except ModuleNotFoundError:
        return False


HAS_COCOTB = _has_cocotb()

# DMI ops, the sbcs fields the accesses use (RISC-V External Debug Support 0.13.2)
READ, WRITE = 1, 2
SBBUSY = 1 << 21
SBREADONADDR = 1 << 20
SBACCESS32 = 2 << 17
SIGNALS = (
    "rst_n",
    "bridge_state",
    "cdc_req_src_clear_pending",
    "cdc_req_dst_clear_pending",
    "cdc_resp_src_clear_pending",
    "cdc_resp_dst_clear_pending",
    "sbbusy",
    "dmi_req_valid",
    "dmi_req_ready",
    "dmi_req_op",
    "dmi_req_addr",
    "dmi_req_data",
    "r_valid",
    "r_ready",
    "r_resp",
    "r_data",
    "b_valid",
    "b_ready",
    "b_resp",
)
CLEAR_PENDING = SIGNALS[2:6]
ADDR = 0x1000
WDATA = 0x1122_3344
RDATA = 0x5566_7788


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


class _Value:
    """A resolved signal value as ``resolve`` reads it."""

    is_resolvable = True

    def __init__(self, value: int) -> None:
        self._value = value

    def __int__(self) -> int:
        return self._value


class _Signal:
    def __init__(self, name: str) -> None:
        self._name = name
        self.value = _Value(0)

    def set(self, value: int) -> None:
        self.value = _Value(value)


class _Dut:
    """The tb_top observables the predictor reads, all 0 until set."""

    def __init__(self) -> None:
        for name in SIGNALS:
            setattr(self, name, _Signal(name))

    def sig(self, name: str) -> _Signal:
        signal: _Signal = getattr(self, name)
        return signal


class _Feed:
    """A predictor on the stand-in observables, after the reset and CDC clear sequence."""

    def __init__(self) -> None:
        _load_package("env", COCOTB_DIR / "env")
        pred = importlib.import_module("env.tdr2axi_predictor")
        sba = importlib.import_module("env.sba_ref_model")
        tdr = importlib.import_module("env.tdr_status_model")
        self.sba = sba
        self.dut = _Dut()
        self.dut.sig("rst_n").set(1)
        self.predictor = pred.Tdr2axiPredictor(
            self.dut,
            tdr.TdrStatusModel(sync_stages=3),
            sba.SbaRefModel(sba.SbaModelCfg(lite=False, addr_width=64, data_width=64)),
        )
        self.predictor.sba.reset("RST-ARST-BUS")
        self.predictor.tdr.reset("RST-ARST-TCK")
        for level in (1, 0):
            for name in CLEAR_PENDING:
                self.dut.sig(name).set(level)
            self.tck()
        self.dmi(WRITE, sba.DMCONTROL, 1)

    def tck(self, word: int | None = None, *, capture: bool = False) -> Any:
        return self.predictor.tck_edge(capture=capture, update_word=word)

    def clk(self) -> None:
        self.predictor._clk_edge()

    def handshake(self, op: int, addr: int, data: int = 0) -> None:
        """A DMI request handshake at one i_clk edge, with no update before it."""
        d = self.dut
        for name, value in (("dmi_req_op", op), ("dmi_req_addr", addr), ("dmi_req_data", data)):
            d.sig(name).set(value)
        d.sig("dmi_req_valid").set(1)
        d.sig("dmi_req_ready").set(1)
        self.clk()
        d.sig("dmi_req_valid").set(0)

    def dmi(self, op: int, addr: int, data: int = 0) -> Any:
        """One DMI operation: its update edge, the DM's handshake, and the answer's return
        to the bridge; returns the answer the predictor published."""
        d = self.dut
        self.tck((addr << 34) | (data << 2) | op)
        for name, value in (("dmi_req_op", op), ("dmi_req_addr", addr), ("dmi_req_data", data)):
            d.sig(name).set(value)
        d.sig("dmi_req_valid").set(1)
        d.sig("dmi_req_ready").set(1)
        self.clk()
        d.sig("dmi_req_valid").set(0)
        d.sig("bridge_state").set(1)
        self.tck()
        d.sig("bridge_state").set(0)
        self.tck()
        return self.predictor.dm_answers[-1]

    def bus_response(self, channel: str) -> None:
        """An OKAY R or B handshake; sbbusy falls at the next i_clk edge."""
        d = self.dut
        if channel == "r":
            d.sig("r_data").set(RDATA)
        d.sig(f"{channel}_valid").set(1)
        d.sig(f"{channel}_ready").set(1)
        self.clk()
        d.sig(f"{channel}_valid").set(0)
        d.sig("sbbusy").set(0)

    def start_read(self) -> None:
        self.dmi(WRITE, self.sba.SBCS, SBACCESS32 | SBREADONADDR)
        self.dut.sig("sbbusy").set(1)
        self.dmi(WRITE, self.sba.SBADDRESS0, ADDR)

    def start_write(self) -> None:
        self.dmi(WRITE, self.sba.SBCS, SBACCESS32)
        self.dmi(WRITE, self.sba.SBADDRESS0, ADDR)
        self.dut.sig("sbbusy").set(1)
        self.dmi(WRITE, self.sba.SBDATA0, WDATA)

    def sbcs_window(self) -> set[int]:
        """The sbbusy values the predicted answer of one sbcs read allows."""
        data = self.dmi(READ, self.sba.SBCS).data
        if not data.mask & SBBUSY:
            raise AssertionError("the sbcs answer does not compare sbbusy")
        return {(value >> 21) & 1 for value in data.allowed}


@unittest.skipUnless(HAS_COCOTB, "the predictor imports cocotb")
class Tdr2axiPredictorSbbusyWindow(unittest.TestCase):
    """The published sbcs answers hold the restated sbbusy rule for reads and writes."""

    def check_window(self, feed: _Feed, channel: str) -> None:
        self.assertEqual(feed.sbcs_window(), {1}, "before the response: an early 0 is outside")
        feed.bus_response(channel)
        self.assertEqual(feed.sbcs_window(), {0, 1}, "first read after the response")
        self.assertEqual(feed.sbcs_window(), {0}, "second read after the response: a late 1")

    def test_read_access(self) -> None:
        feed = _Feed()
        feed.start_read()
        self.check_window(feed, "r")

    def test_write_access(self) -> None:
        feed = _Feed()
        feed.start_write()
        self.check_window(feed, "b")

    def test_late_one_after_another_request(self) -> None:
        """A request that retires the access first leaves one late 1 to the next sbcs read."""
        feed = _Feed()
        feed.start_read()
        feed.bus_response("r")
        self.assertFalse(feed.dmi(READ, feed.sba.SBDATA0).busy)
        self.assertEqual(feed.sbcs_window(), {0, 1})
        self.assertEqual(feed.sbcs_window(), {0})

    def test_next_access_closes_the_late_window(self) -> None:
        """An access started before the next sbcs read reads sbbusy = 1 only."""
        feed = _Feed()
        feed.start_write()
        feed.bus_response("b")
        feed.dmi(WRITE, feed.sba.SBADDRESS0, ADDR + 4)
        feed.dut.sig("sbbusy").set(1)
        feed.dmi(WRITE, feed.sba.SBDATA0, WDATA)
        self.assertEqual(feed.sbcs_window(), {1})

    def test_tdr_model_leaves_sbbusy_free(self) -> None:
        """The TDR model's capture of an sbcs read compares no sbbusy bit."""
        feed = _Feed()
        feed.start_write()
        feed.sbcs_window()
        image = feed.tck(capture=True)
        self.assertFalse(image.data.mask & SBBUSY)


@unittest.skipUnless(HAS_COCOTB, "the predictor imports cocotb")
class Tdr2axiPredictorUnpredictedRequest(unittest.TestCase):
    """An unpredicted DMI handshake raises at the next i_clk edge, not at its own."""

    def test_raise_waits_one_edge(self) -> None:
        feed = _Feed()
        pred = importlib.import_module("env.tdr2axi_predictor")
        answers = len(feed.predictor.dm_answers)
        feed.handshake(WRITE, feed.sba.SBDATA0, WDATA)
        self.assertEqual(len(feed.predictor.dm_answers), answers, "no answer is published")
        with self.assertRaisesRegex(pred.PredictorSyncError, "no request predicted"):
            feed.clk()


if __name__ == "__main__":
    unittest.main()
