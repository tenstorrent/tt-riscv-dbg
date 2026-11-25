# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Spec-derived unit tests of ``SbaRefModel``; plain Python, no simulator.

Each test cites the atoms it reproduces. The programming examples of S-TDR2AXI-0064 to
S-TDR2AXI-0082 are replayed step by step, and each predict function gets the boundary
cases its atoms name: the address and data width limits, every access size and lane, the
sbaddress1 and sbdata1 existence limits, and the 32-bit sbaddress word boundary.
"""

from __future__ import annotations

import importlib.util
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


sba = _load("sba_ref_model")

SBVERSION = 1 << 29
SBACCESS_RESET_BITS = 2 << 17
CLEAR_ERRORS = (1 << 22) | (0x7 << 12)
READONADDR = 1 << 20
READONDATA = 1 << 15
AUTOINC = 1 << 16
BUSY = 1 << 21
BUSYERROR = 1 << 22
# dmstatus: version 2, allunavail and anyunavail 1, authenticated 1, authbusy 0
DMSTATUS_SPEC = 0x3082
AUTHENTICATED = 1 << 7
AUTHBUSY = 1 << 6
AUTHDATA = 0x30


def sbcs_write(
    size: int, *, readonaddr: bool = False, readondata: bool = False, autoinc: bool = False
) -> int:
    """sbcs value with sbaccess = ``size`` and the chosen read and increment controls."""
    return (
        (size << 17)
        | (READONADDR if readonaddr else 0)
        | (READONDATA if readondata else 0)
        | (AUTOINC if autoinc else 0)
    )


def sbcs_idle(
    cfg: object, *, rw: int = SBACCESS_RESET_BITS, sberror: int = 0, busyerror: bool = False
) -> int:
    """The sbcs value the spec fixes for ``cfg`` with sbbusy = 0."""
    access = 0b111 | (0b1000 if cfg.data_width == 64 else 0)
    return (
        SBVERSION
        | rw
        | (sberror << 12)
        | (cfg.addr_width << 5)
        | access
        | (BUSYERROR if busyerror else 0)
    )


def enabled(
    *, lite: bool = False, addr_width: int = 64, data_width: int = 64, negative: bool = False
) -> object:
    """A model with the DM enabled by dmcontrol = 1 (S-TDR2AXI-0064)."""
    model = sba.SbaRefModel(sba.SbaModelCfg(lite, addr_width, data_width), negative=negative)
    model.predict_dmi_write(sba.DMCONTROL, 1)
    return model


def single_write(model: object, addr: int, size: int, data: int) -> object:
    """S-TDR2AXI-0065 to S-TDR2AXI-0071: the single write steps; returns the bus access."""
    model.predict_dmi_write(sba.SBCS, sbcs_write(size))
    if model.cfg.addr_width > 32:
        model.predict_dmi_write(sba.SBADDRESS1, addr >> 32)
    model.predict_dmi_write(sba.SBADDRESS0, addr & 0xFFFF_FFFF)
    if size == 3:
        model.predict_dmi_write(sba.SBDATA1, data >> 32)
    return model.predict_dmi_write(sba.SBDATA0, data & 0xFFFF_FFFF).access


def single_read(model: object, addr: int, size: int) -> object:
    """S-TDR2AXI-0073 to S-TDR2AXI-0076: the single read steps; returns the bus access."""
    model.predict_dmi_write(sba.SBCS, sbcs_write(size, readonaddr=True))
    if model.cfg.addr_width > 32:
        model.predict_dmi_write(sba.SBADDRESS1, addr >> 32)
    return model.predict_dmi_write(sba.SBADDRESS0, addr & 0xFFFF_FFFF).access


def complete(model: object, *, error: bool = False, rdata: int = 0) -> None:
    """The subordinate answers and the DM reports the access complete."""
    model.bus_response(error=error, rdata=rdata)
    model.access_retired()


class ExpectedTest(unittest.TestCase):
    """The prediction forms: exact, allowed set, free bits."""

    def test_mask_and_set(self) -> None:
        exp = sba.Expected((0x12, 0x34), 0xFF)
        self.assertTrue(exp.matches(0x12))
        self.assertTrue(exp.matches(0x1034))
        self.assertFalse(exp.matches(0x13))
        self.assertTrue(sba.Expected.free(()).matches(0xDEAD))

    def test_flipped_changes_lowest_compared_bit(self) -> None:
        exp = sba.Expected.exact(0x10, 0xF0, ())
        self.assertFalse(exp.flipped().matches(0x10))
        self.assertTrue(exp.flipped().matches(0x00))


class CfgTest(unittest.TestCase):
    """S-TDR2AXI-0014, S-TDR2AXI-0015, S-TDR2AXI-0021: the legal widths."""

    def test_limits(self) -> None:
        for data_width, addr_width in ((32, 2), (64, 3), (32, 64), (64, 64)):
            sba.SbaModelCfg(False, addr_width, data_width)
        for data_width, addr_width in ((64, 2), (32, 1), (16, 32), (64, 65)):
            with self.assertRaises(ValueError):
                sba.SbaModelCfg(False, addr_width, data_width)


class SbcsFieldsTest(unittest.TestCase):
    """CHK-SBA-SBCS-FIELDS, CHK-DM-VERSION, CHK-DM-HART-UNAVAIL."""

    def test_sbcs_per_build(self) -> None:
        """S-TDR2AXI-0014, S-TDR2AXI-0015, X-RISCV_DEBUG-sbcs-fields: sbasize, sbaccessN."""
        for data_width, addr_width in ((32, 2), (64, 3), (32, 33), (64, 48), (64, 64)):
            model = enabled(addr_width=addr_width, data_width=data_width)
            want = sbcs_idle(model.cfg)
            answer = model.predict_dmi_read(sba.SBCS)
            self.assertFalse(answer.busy)
            self.assertEqual(answer.data.allowed, (want,))
            self.assertTrue(answer.data.matches(want | (0x3F << 23)))
            self.assertFalse(answer.data.matches(want ^ (1 << 5)))
            self.assertEqual(bool(want & 0b1000), data_width == 64)
            self.assertFalse(want & 0b1_0000)

    def test_dmstatus(self) -> None:
        """X-RISCV_DEBUG-dm-version, S-TDR2AXI-0009: version 2, hart unavailable."""
        model = enabled()
        for dmcontrol in (0x8000_0001, 0x4000_0001, 0x03FF_FFC1, 0x0000_0001):
            model.predict_dmi_write(sba.DMCONTROL, dmcontrol)
            answer = model.predict_dmi_read(sba.DMSTATUS)
            self.assertTrue(answer.data.matches(DMSTATUS_SPEC))
            self.assertFalse(answer.data.matches(DMSTATUS_SPEC ^ 1))
            self.assertFalse(answer.data.matches(DMSTATUS_SPEC ^ (1 << 12)))

    def test_inactive_reads(self) -> None:
        """X-RISCV_DEBUG-dmactive, X-RISCV_DEBUG-sbbusy, X-RISCV_DEBUG-sbbusyerror: while
        dmactive = 0 with no access in flight, sbcs compares sbbusy = 0 and sbbusyerror = 0
        and dmstatus compares nothing."""
        model = sba.SbaRefModel(sba.SbaModelCfg(False, 64, 64))
        data = model.predict_dmi_read(sba.SBCS).data
        self.assertEqual(data.mask, BUSY | BUSYERROR)
        self.assertTrue(data.matches(0x0004_0000))
        self.assertFalse(data.matches(BUSY))
        self.assertFalse(data.matches(BUSYERROR))
        self.assertEqual(model.predict_dmi_read(sba.DMSTATUS).data.mask, 0)


class AuthenticationTest(unittest.TestCase):
    """CHK-DM-VERSION: X-RISCV_DEBUG-authenticated, X-RISCV_DEBUG-authbusy, S-TDR2AXI-0002,
    S-TDR2AXI-0062. A DM without authentication presets authenticated to 1, and authbusy,
    which only an authdata access sets, reads 0."""

    def assert_unauthenticated_dm(self, exp: object) -> None:
        """``exp`` compares both bits and allows only authenticated 1 and authbusy 0."""
        self.assertEqual(exp.mask & (AUTHENTICATED | AUTHBUSY), AUTHENTICATED | AUTHBUSY)
        self.assertTrue(exp.matches(DMSTATUS_SPEC))
        self.assertFalse(exp.matches(DMSTATUS_SPEC & ~AUTHENTICATED))
        self.assertFalse(exp.matches(DMSTATUS_SPEC | AUTHBUSY))
        self.assertLessEqual(
            {"X-RISCV_DEBUG-authenticated", "X-RISCV_DEBUG-authbusy", "S-TDR2AXI-0062"},
            set(exp.atoms),
        )

    def test_every_build(self) -> None:
        """Both bits on AXI4 and AXI4-Lite, at the narrowest and widest address."""
        for lite, addr_width, data_width in ((False, 64, 64), (True, 32, 32), (False, 2, 32)):
            with self.subTest(lite=lite, addr_width=addr_width, data_width=data_width):
                model = enabled(lite=lite, addr_width=addr_width, data_width=data_width)
                self.assert_unauthenticated_dm(model.predict_dmi_read(sba.DMSTATUS).data)
                self.assert_unauthenticated_dm(model.predict_dmstatus())

    def test_authdata_access_sets_nothing(self) -> None:
        """S-TDR2AXI-0062 maps no authdata, so a write or read of 0x30 leaves authbusy 0."""
        model = enabled()
        model.predict_dmi_write(AUTHDATA, 0xFFFF_FFFF)
        self.assert_unauthenticated_dm(model.predict_dmstatus())
        model.predict_dmi_read(AUTHDATA)
        self.assert_unauthenticated_dm(model.predict_dmstatus())

    def test_access_in_flight(self) -> None:
        """A system bus access in flight leaves both bits as preset."""
        model = enabled()
        self.assertIsNotNone(single_write(model, 0x40, 2, 0x1234_5678))
        self.assert_unauthenticated_dm(model.predict_dmstatus())
        model.bus_response(error=False)
        self.assert_unauthenticated_dm(model.predict_dmstatus())

    def test_dm_resets(self) -> None:
        """X-RISCV_DEBUG-dmactive: a dmactive = 0 write and a bus reset return the DM to its
        reset values, where authenticated is preset to 1; dmstatus is free while
        dmactive = 0."""
        model = enabled()
        model.predict_dmi_write(sba.DMCONTROL, 0)
        self.assertEqual(model.predict_dmstatus().mask, 0)
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assert_unauthenticated_dm(model.predict_dmstatus())
        model.reset("RST-ARST-BUS")
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assert_unauthenticated_dm(model.predict_dmstatus())

    def test_ndmreset(self) -> None:
        """X-RISCV_DEBUG-ndmreset resets the system, not the DM: both bits keep their values
        while ndmreset is set, after it is cleared, and when dmactive and ndmreset are set
        together from dmactive = 0."""
        model = enabled()
        model.predict_dmi_write(sba.DMCONTROL, 0x3)
        self.assert_unauthenticated_dm(model.predict_dmstatus())
        model.predict_dmi_write(sba.DMCONTROL, 0x1)
        self.assert_unauthenticated_dm(model.predict_dmstatus())
        together = sba.SbaRefModel(sba.SbaModelCfg(False, 64, 64))
        together.predict_dmi_write(sba.DMCONTROL, 0x3)
        self.assert_unauthenticated_dm(together.predict_dmstatus())

    def test_tck_side_resets(self) -> None:
        """A TAP reset (S-TDR2AXI-0054) and the TCK side of an i_arst_n reset leave the DM,
        which sits on the bus side, and both bits as they were."""
        for scope in ("RST-TRST", "RST-ARST-TCK"):
            with self.subTest(scope=scope):
                model = enabled()
                model.reset(scope)
                self.assert_unauthenticated_dm(model.predict_dmstatus())
                self.assert_unauthenticated_dm(model.predict_dmi_read(sba.DMSTATUS).data)

    def test_scan_reset(self) -> None:
        """An i_arst_scan_n reset clears dmactive, so dmstatus is free until dmactive is set
        again, when both bits read their reset values."""
        model = enabled()
        model.reset("RST-SCAN")
        self.assertEqual(model.predict_dmstatus().mask, 0)
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assert_unauthenticated_dm(model.predict_dmstatus())


class RegisterMapTest(unittest.TestCase):
    """CHK-DM-REGISTER-MAP: S-TDR2AXI-0062, X-RISCV_DEBUG-dm-addresses."""

    def test_distinct_values_stay_in_their_registers(self) -> None:
        """Each register reads back its own value and no write starts a bus access."""
        model = enabled()
        writes = {
            sba.SBADDRESS0: 0x1111_1110,
            sba.SBADDRESS1: 0x2222_2222,
            sba.SBDATA1: 0x3333_3333,
            sba.SBCS: sbcs_write(1, autoinc=True),
            sba.DMCONTROL: 0x3,
        }
        for addr, value in writes.items():
            answer = model.predict_dmi_write(addr, value)
            self.assertIsNone(answer.access)
        self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(0x1111_1110))
        self.assertTrue(model.predict_dmi_read(sba.SBADDRESS1).data.matches(0x2222_2222))
        self.assertTrue(model.predict_dmi_read(sba.SBDATA1).data.matches(0x3333_3333))
        sbcs = sbcs_idle(model.cfg, rw=sbcs_write(1, autoinc=True))
        self.assertTrue(model.predict_dmi_read(sba.SBCS).data.matches(sbcs))
        self.assertTrue(model.predict_dmi_read(sba.DMCONTROL).data.matches(0x3))
        self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(0))

    def test_unmapped_address_is_free(self) -> None:
        answer = enabled().predict_dmi_read(0x7F)
        self.assertFalse(answer.busy)
        self.assertEqual(answer.data.mask, 0)

    def test_dmi_limits(self) -> None:
        with self.assertRaises(ValueError):
            enabled().predict_dmi_read(0x80)
        with self.assertRaises(ValueError):
            enabled().predict_dmi_write(sba.SBDATA0, 1 << 32)


class SingleWriteTest(unittest.TestCase):
    """CHK-SBA-SINGLE-WRITE, CHK-SBA-WRITE-MEMORY, CHK-AXI-AXI4-SIZE, CHK-AXI-LITE-FULL-WIDTH:
    S-TDR2AXI-0059, S-TDR2AXI-0065 to S-TDR2AXI-0072, X-AXI4-strobe-lanes."""

    DATA = 0x8877_6655_4433_2211

    def test_every_size_and_lane(self) -> None:
        for lite in (False, True):
            for size in range(4):
                nbytes = 1 << size
                for lane in range(0, 8, nbytes):
                    with self.subTest(lite=lite, size=size, lane=lane):
                        self._check(size, lane, lite=lite)

    def _check(self, size: int, lane: int, *, lite: bool) -> None:
        model = enabled(lite=lite)
        base = 0x1_2345_6780
        model.load_memory(base, bytes([0xEE] * 8))
        access = single_write(model, base + lane, size, self.DATA)
        nbytes = 1 << size
        width = (1 << (8 * nbytes)) - 1
        self.assertIs(access.direction, sba.BusDir.WRITE)
        self.assertEqual(access.addr, base + lane)
        self.assertEqual(access.size, None if lite else size)
        self.assertEqual(access.wstrb, ((1 << nbytes) - 1) << lane)
        self.assertTrue(access.wdata.matches((self.DATA & width) << (8 * lane)))
        self.assertFalse(
            access.wdata.matches(((self.DATA & width) << (8 * lane)) ^ (1 << 8 * lane))
        )
        self.assertEqual(model.phase, sba.SbaPhase.PENDING)
        complete(model)
        memory = model.predict_memory(base, 8)
        for offset in range(8):
            want = (
                (self.DATA >> (8 * (offset - lane))) & 0xFF
                if lane <= offset < lane + nbytes
                else 0xEE
            )
            self.assertEqual(memory[offset], want)
        self.assertEqual(model.predict_dmi_read(sba.SBDATA0).data.mask, 0)
        self.assertTrue(
            model.predict_dmi_read(sba.SBCS).data.matches(sbcs_idle(model.cfg, rw=sbcs_write(size)))
        )

    def test_sbdata0_write_starts_one_access(self) -> None:
        """S-TDR2AXI-0071: the write starts at sbdata0, not at sbdata1."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(3))
        model.predict_dmi_write(sba.SBADDRESS0, 0x100)
        self.assertIsNone(model.predict_dmi_write(sba.SBDATA1, 0xAABB_CCDD).access)
        self.assertIsNotNone(model.predict_dmi_write(sba.SBDATA0, 0x1122_3344).access)


class SingleReadTest(unittest.TestCase):
    """CHK-SBA-SINGLE-READ, CHK-SBA-DATA-LANES, CHK-AXI-LITE-FULL-WIDTH, CHK-SBA-NO-READ-ON-ADDR,
    CHK-SBA-SBDATA1-NO-BUS: S-TDR2AXI-0073 to S-TDR2AXI-0078, X-RISCV_DEBUG-sbdata-lanes."""

    WORD = 0xF0E0_D0C0_B0A0_9080

    def test_every_size_and_lane(self) -> None:
        for lite in (False, True):
            for size in range(4):
                for lane in range(0, 8, 1 << size):
                    with self.subTest(lite=lite, size=size, lane=lane):
                        self._check(size, lane, lite=lite)

    def _check(self, size: int, lane: int, *, lite: bool) -> None:
        model = enabled(lite=lite)
        addr = 0x2_0000_0000 + lane
        access = single_read(model, addr, size)
        self.assertIs(access.direction, sba.BusDir.READ)
        self.assertEqual(access.addr, addr)
        self.assertEqual(access.size, None if lite else size)
        self.assertIsNone(access.wstrb)
        complete(model, rdata=self.WORD)
        width = (1 << (8 << size)) - 1
        value = (self.WORD >> (8 * lane)) & width
        data0 = model.predict_dmi_read(sba.SBDATA0)
        self.assertIsNone(data0.access)
        self.assertEqual(data0.data.mask, width & 0xFFFF_FFFF)
        self.assertTrue(data0.data.matches(value & 0xFFFF_FFFF | 0xFFFF_FFFF & ~width))
        data1 = model.predict_dmi_read(sba.SBDATA1)
        self.assertIsNone(data1.access)
        self.assertEqual(data1.data.mask, width >> 32)
        if size == 3:
            self.assertTrue(data1.data.matches(value >> 32))
            self.assertFalse(data1.data.matches((value >> 32) ^ 1))

    def test_no_read_without_readonaddr(self) -> None:
        """S-TDR2AXI-0076, X-RISCV_DEBUG-sbaddress0-read: sbreadonaddr gates the read."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2))
        self.assertIsNone(model.predict_dmi_write(sba.SBADDRESS0, 0x40).access)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)

    def test_sbdata1_never_reaches_the_bus(self) -> None:
        """S-TDR2AXI-0077: sbdata1 has no side effect."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(3, readonaddr=True, readondata=True))
        self.assertIsNone(model.predict_dmi_write(sba.SBDATA1, 0x5).access)
        self.assertIsNone(model.predict_dmi_read(sba.SBDATA1).access)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)

    def test_sbdata1_absent_on_32_bit_data(self) -> None:
        """X-RISCV_DEBUG-sbdata-lanes: sbdata1 exists only with a 64-bit access."""
        model = enabled(data_width=32)
        model.predict_dmi_write(sba.SBDATA1, 0x5)
        self.assertEqual(model.predict_dmi_read(sba.SBDATA1).data.mask, 0)


class BlockWriteTest(unittest.TestCase):
    """CHK-SBA-BLOCK-WRITE: S-TDR2AXI-0079, X-RISCV_DEBUG-sbautoincrement."""

    def test_address_advances_across_the_word_boundary(self) -> None:
        """Autoincrement carries from sbaddress0 into sbaddress1 at the 32-bit boundary."""
        model = enabled()
        base = 0xFFFF_FFF8
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        model.predict_dmi_write(sba.SBADDRESS0, base)
        for element in range(4):
            access = model.predict_dmi_write(sba.SBDATA0, 0xA0 + element).access
            self.assertEqual(access.addr, base + 4 * element)
            complete(model)
            nxt = base + 4 * (element + 1)
            self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(nxt & 0xFFFF_FFFF))
            self.assertTrue(model.predict_dmi_read(sba.SBADDRESS1).data.matches(nxt >> 32))
        self.assertEqual(model.predict_memory(0x1_0000_0000, 4), (0xA2, 0, 0, 0))


class AutoIncrementTest(unittest.TestCase):
    """CHK-SBA-BLOCK-WRITE, CHK-SBA-BLOCK-READ: S-TDR2AXI-0079, S-TDR2AXI-0081,
    X-RISCV_DEBUG-sbautoincrement: sbaddress advances by 2**sbaccess bytes."""

    def test_every_size(self) -> None:
        """Three writes and a pipelined read at each size step by the access size."""
        for size in range(4):
            with self.subTest(size=size):
                model = enabled()
                nbytes = 1 << size
                model.predict_dmi_write(sba.SBCS, sbcs_write(size, autoinc=True))
                model.predict_dmi_write(sba.SBADDRESS0, 0x1000)
                for element in range(3):
                    access = model.predict_dmi_write(sba.SBDATA0, 0xA0 + element).access
                    self.assertEqual(access.addr, 0x1000 + nbytes * element)
                    complete(model)
                self.assertTrue(model.predict_sbaddress(0).matches(0x1000 + 3 * nbytes))
                self.assertEqual(model.predict_memory(0x1000 + 2 * nbytes, 1), (0xA2,))
                model.predict_dmi_write(sba.SBCS, sbcs_write(size, readondata=True, autoinc=True))
                access = model.predict_dmi_read(sba.SBDATA0).access
                self.assertEqual(access.addr, 0x1000 + 3 * nbytes)
                complete(model)
                self.assertTrue(model.predict_sbaddress(0).matches(0x1000 + 4 * nbytes))


class BlockReadTest(unittest.TestCase):
    """CHK-SBA-BLOCK-READ, CHK-SBA-READ-NO-READONDATA, CHK-TDR-PIPELINED-READ:
    S-TDR2AXI-0080 to S-TDR2AXI-0082, X-RISCV_DEBUG-sbdata0-read."""

    def test_programming_example(self) -> None:
        """The spec's block-read example, clearing sbreadondata before the last sbdata0 read."""
        model = enabled(data_width=32)
        base, count = 0x1000, 4
        model.predict_dmi_write(
            sba.SBCS, sbcs_write(2, readonaddr=True, readondata=True, autoinc=True)
        )
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        access = model.predict_dmi_write(sba.SBADDRESS0, base).access
        self.assertEqual(access.addr, base)
        complete(model, rdata=0x100)
        for element in range(count):
            last = element == count - 1
            if last:
                model.predict_dmi_write(sba.SBCS, sbcs_write(2, readonaddr=True, autoinc=True))
            answer = model.predict_dmi_read(sba.SBDATA0)
            self.assertTrue(answer.data.matches(0x100 + element))
            if last:
                self.assertIsNone(answer.access)
                continue
            self.assertEqual(answer.access.addr, base + 4 * (element + 1))
            complete(model, rdata=0x100 + element + 1)
        want = base + 4 * count
        self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(want))

    def test_sbdata0_read_without_readondata_keeps_the_address(self) -> None:
        """The Debug 1.0 reading of X-RISCV_DEBUG-sbdata0-read: no read, no increment."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS0, 0x80)
        self.assertIsNone(model.predict_dmi_read(sba.SBDATA0).access)
        self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(0x80))


class AccessErrorTest(unittest.TestCase):
    """CHK-SBA-MISALIGNED, CHK-SBA-OVERSIZE: S-TDR2AXI-0060, X-RISCV_DEBUG-sberror."""

    def _sberror(self, model: object, rw: int, code: int) -> None:
        responded = model.predict_dmi_read(sba.SBCS).data
        self.assertTrue(responded.matches(sbcs_idle(model.cfg, rw=rw, sberror=code)))
        model.access_retired()
        settled = model.predict_dmi_read(sba.SBCS).data
        self.assertEqual(settled.allowed, (sbcs_idle(model.cfg, rw=rw, sberror=code),))

    def test_misaligned(self) -> None:
        for size, offset in ((1, 1), (2, 2), (3, 4)):
            model = enabled()
            self.assertIsNone(single_write(model, 0x100 + offset, size, 0))
            self._sberror(model, sbcs_write(size), 3)

    def test_oversize(self) -> None:
        for data_width, size in ((32, 3), (64, 4)):
            model = enabled(data_width=data_width)
            self.assertIsNone(single_read(model, 0x100, size))
            self._sberror(model, sbcs_write(size, readonaddr=True), 4)

    def test_both_is_outside_the_spec(self) -> None:
        with self.assertRaises(sba.SbaStimulusError):
            single_read(enabled(data_width=32), 0x104, 3)


class BusErrorTest(unittest.TestCase):
    """CHK-SBA-BUS-ERROR, CHK-SBA-ERROR-BLOCKS: S-TDR2AXI-0061, X-RISCV_DEBUG-sberror."""

    def test_read_error_blocks_until_cleared(self) -> None:
        """With sberror set, no trigger starts an access until sberror is written 1 to clear."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, readonaddr=True, readondata=True))
        model.predict_dmi_write(sba.SBADDRESS0, 0x200)
        complete(model, error=True)
        rw = sbcs_write(2, readonaddr=True, readondata=True)
        self.assertTrue(
            model.predict_dmi_read(sba.SBCS).data.matches(sbcs_idle(model.cfg, rw=rw, sberror=2))
        )
        self.assertIsNone(model.predict_dmi_write(sba.SBDATA0, 1).access)
        self.assertIsNone(model.predict_dmi_write(sba.SBADDRESS0, 0x204).access)
        self.assertIsNone(model.predict_dmi_read(sba.SBDATA0).access)
        model.predict_dmi_write(sba.SBCS, rw | CLEAR_ERRORS)
        self.assertTrue(model.predict_dmi_read(sba.SBCS).data.matches(sbcs_idle(model.cfg, rw=rw)))
        self.assertIsNotNone(model.predict_dmi_write(sba.SBADDRESS0, 0x208).access)

    def test_read_error_leaves_sbdata_free(self) -> None:
        """No atom fixes sbdata after a failed read, so neither word is compared."""
        model = enabled()
        model.predict_dmi_write(sba.SBDATA1, 0x1234)
        single_read(model, 0x200, 3)
        complete(model, error=True, rdata=0xFFFF_FFFF_FFFF_FFFF)
        self.assertEqual(model.predict_sbdata(0).mask, 0)
        self.assertEqual(model.predict_sbdata(1).mask, 0)

    def test_partial_clear_is_refused(self) -> None:
        """The narrowing: a write of 1s to sberror must cover every set bit."""
        rw = sbcs_write(2, readonaddr=True)
        for cleared in (0b001, 0b100, 0b101):
            with self.subTest(cleared=cleared):
                model = enabled()
                single_read(model, 0x200, 2)
                complete(model, error=True)
                with self.assertRaises(sba.SbaStimulusError):
                    model.predict_dmi_write(sba.SBCS, rw | (cleared << 12))
        for cleared in (0b010, 0b111):
            with self.subTest(cleared=cleared):
                model = enabled()
                single_read(model, 0x200, 2)
                complete(model, error=True)
                model.predict_dmi_write(sba.SBCS, rw | (cleared << 12))
                self.assertTrue(model.predict_sbcs().matches(sbcs_idle(model.cfg, rw=rw)))

    def test_write_error_leaves_memory_unknown(self) -> None:
        """X-AXI4-response-codes: a failed write's bytes are not predicted."""
        model = enabled()
        model.load_memory(0x300, bytes(4))
        single_write(model, 0x300, 2, 0x1234_5678)
        complete(model, error=True)
        self.assertEqual(model.predict_memory(0x300, 4), (None, None, None, None))


class NoIncrementAfterErrorTest(unittest.TestCase):
    """CHK-SBA-NO-INC-AFTER-ERROR: S-TDR2AXI-0061, S-TDR2AXI-0076, S-TDR2AXI-0079,
    S-TDR2AXI-0093, S-TDR2AXI-0094, X-RISCV_DEBUG-sbaddress0-read,
    X-RISCV_DEBUG-sbdata0-write, X-RISCV_DEBUG-sbdata0-read."""

    def _address(self, model: object) -> object:
        data = model.predict_dmi_read(sba.SBADDRESS0).data
        self.assertLessEqual({"S-TDR2AXI-0093", "S-TDR2AXI-0094"}, set(data.atoms))
        return data

    def test_sbaddress0_read_error(self) -> None:
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, readonaddr=True, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS0, 0x400)
        complete(model, error=True)
        self.assertTrue(self._address(model).matches(0x400))

    def test_sbdata0_write_error_on_a_later_element(self) -> None:
        """sbaddress holds the failing address of a block-write element."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS0, 0x500)
        model.predict_dmi_write(sba.SBDATA0, 1)
        complete(model)
        model.predict_dmi_write(sba.SBDATA0, 2)
        complete(model, error=True)
        self.assertTrue(self._address(model).matches(0x504))

    def test_sbdata0_read_error(self) -> None:
        """S-TDR2AXI-0094, the Debug 1.0 reading of X-RISCV_DEBUG-sbdata0-read: a read an
        sbdata0 read starts does not increment after an error, and does after a success."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, readondata=True, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS0, 0x600)
        self.assertEqual(model.predict_dmi_read(sba.SBDATA0).access.addr, 0x600)
        complete(model)
        self.assertEqual(self._address(model).allowed, (0x604,))
        self.assertEqual(model.predict_dmi_read(sba.SBDATA0).access.addr, 0x604)
        complete(model, error=True)
        self.assertEqual(self._address(model).allowed, (0x604,))


class BusyTest(unittest.TestCase):
    """CHK-SBA-SBBUSY, CHK-SBA-SBBUSYERROR: S-TDR2AXI-0043, X-RISCV_DEBUG-sbbusy,
    X-RISCV_DEBUG-sbbusyerror."""

    COLLISIONS = (
        ("write", sba.SBADDRESS0),
        ("write", sba.SBADDRESS1),
        ("write", sba.SBDATA0),
        ("write", sba.SBDATA1),
        ("read", sba.SBDATA0),
        ("read", sba.SBDATA1),
    )
    QUIET = (
        ("read", sba.SBADDRESS0),
        ("read", sba.SBADDRESS1),
    )

    def _stalled(self) -> object:
        model = enabled()
        single_write(model, 0x700, 2, 0xCAFE_F00D)
        return model

    def _access(self, model: object, kind: str, addr: int) -> object:
        if kind == "write":
            return model.predict_dmi_write(addr, 0x5)
        return model.predict_dmi_read(addr)

    def _collide(self) -> object:
        model = self._stalled()
        self.assertTrue(model.predict_dmi_read(sba.SBDATA0).busy)
        complete(model)
        return model

    def test_sbbusy_from_request_to_completion(self) -> None:
        """sbbusy holds from start to retirement, and either value is allowed after the response."""
        model = self._stalled()
        rw = sbcs_write(2)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg, rw=rw) | BUSY,)
        )
        model.bus_response(error=False)
        allowed = set(model.predict_dmi_read(sba.SBCS).data.allowed)
        self.assertEqual(allowed, {sbcs_idle(model.cfg, rw=rw) | BUSY, sbcs_idle(model.cfg, rw=rw)})
        model.access_retired()
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg, rw=rw),)
        )

    def test_each_collision(self) -> None:
        """Each sbaddress write or sbdata access in flight answers busy and does nothing else."""
        for kind, addr in self.COLLISIONS:
            with self.subTest(kind=kind, addr=hex(addr)):
                model = self._stalled()
                answer = self._access(model, kind, addr)
                self.assertTrue(answer.busy)
                self.assertIsNone(answer.access)
                complete(model)
                rw = sbcs_write(2)
                self.assertTrue(
                    model.predict_dmi_read(sba.SBCS).data.matches(
                        sbcs_idle(model.cfg, rw=rw, busyerror=True)
                    )
                )
                answer = model.predict_dmi_read(sba.SBADDRESS0)
                self.assertFalse(answer.busy)
                self.assertTrue(answer.data.matches(0x700))
                self.assertTrue(model.predict_dmi_read(sba.SBADDRESS1).data.matches(0))
                model.predict_dmi_write(sba.SBCS, rw | BUSYERROR)
                self.assertTrue(
                    model.predict_dmi_read(sba.SBCS).data.matches(sbcs_idle(model.cfg, rw=rw))
                )
                self.assertEqual(model.predict_memory(0x700, 4), (0x0D, 0xF0, 0xFE, 0xCA))

    def test_sbaddress_reads_do_not_collide(self) -> None:
        """An sbaddress0 or sbaddress1 read during an access answers success, sets no flag."""
        for kind, addr in self.QUIET:
            with self.subTest(kind=kind, addr=hex(addr)):
                model = self._stalled()
                answer = self._access(model, kind, addr)
                self.assertFalse(answer.busy)
                self.assertIsNone(answer.access)
                self.assertEqual(model.phase, sba.SbaPhase.PENDING)
                complete(model)
                self.assertEqual(
                    model.predict_dmi_read(sba.SBCS).data.allowed,
                    (sbcs_idle(model.cfg, rw=sbcs_write(2)),),
                )

    def test_stalled_reads_are_exact(self) -> None:
        """Before the bus response both sbaddress reads hold their values."""
        model = self._stalled()
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (0x700,))
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.allowed, (0,))

    def test_sbbusyerror_alone_blocks_a_new_access(self) -> None:
        """With only sbbusyerror set, every trigger answers busy and starts nothing."""
        model = self._collide()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, readonaddr=True, readondata=True))
        for kind, addr in self.COLLISIONS:
            with self.subTest(kind=kind, addr=hex(addr)):
                answer = self._access(model, kind, addr)
                self.assertTrue(answer.busy)
                self.assertIsNone(answer.access)
                self.assertEqual(model.phase, sba.SbaPhase.IDLE)
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, readonaddr=True) | BUSYERROR)
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        self.assertIsNotNone(model.predict_dmi_write(sba.SBADDRESS0, 0x740).access)

    def test_responded_values_are_open(self) -> None:
        """Between the response and sbbusy falling, a read shows the value before or after."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(3, readonaddr=True, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        model.predict_dmi_write(sba.SBDATA1, 0x1111_1111)
        model.predict_dmi_write(sba.SBADDRESS0, 0x7F8)
        model.bus_response(error=False, rdata=0x2222_2222_3333_3333)
        self.assertEqual(set(model.predict_sbaddress(0).allowed), {0x7F8, 0x800})
        self.assertEqual(set(model.predict_sbdata(1).allowed), {0x1111_1111, 0x2222_2222})
        self.assertEqual(set(model.predict_sbdata(0).allowed), {0, 0x3333_3333})
        self.assertTrue(model.predict_dmi_read(sba.SBDATA0).busy)
        self.assertTrue(all(value & BUSYERROR for value in model.predict_sbcs().allowed))
        model.access_retired()
        self.assertEqual(model.predict_sbaddress(0).allowed, (0x800,))
        self.assertEqual(model.predict_sbdata(1).allowed, (0x2222_2222,))

    def test_sbaddress1_collision_with_autoincrement(self) -> None:
        """sbautoincrement does not change the sbaddress1 collision: the write is dropped."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        model.predict_dmi_write(sba.SBADDRESS0, 0x10)
        model.predict_dmi_write(sba.SBDATA0, 1)
        self.assertTrue(model.predict_dmi_write(sba.SBADDRESS1, 0x2).busy)
        complete(model)
        self.assertEqual(model.predict_sbaddress(1).allowed, (0,))
        self.assertNotEqual(model.predict_sbaddress(1).mask, 0)
        self.assertTrue(model.predict_sbaddress(0).matches(0x14))

    def test_sbcs_write_while_busy_is_outside_the_spec(self) -> None:
        """With sbbusyerror clear, no reading gives the write's effect on sbbusyerror."""
        with self.assertRaises(sba.SbaStimulusError):
            self._stalled().predict_dmi_write(sba.SBCS, 0)

    def test_sbcs_write_while_busy_after_a_collision(self) -> None:
        """With sbbusyerror set, an sbcs write in flight or before sbbusy falls answers busy
        and changes no field, the W1C bits included. The answer cites S-TDR2AXI-0043 alone,
        the TDR's sticky busy after it."""
        model = self._stalled()
        self.assertTrue(model.predict_dmi_read(sba.SBDATA0).busy)
        rw = sbcs_write(2)
        for respond in (False, True):
            with self.subTest(responded=respond):
                if respond:
                    model.bus_response(error=False)
                answer = model.predict_dmi_write(
                    sba.SBCS, sbcs_write(0, readonaddr=True, autoinc=True) | CLEAR_ERRORS
                )
                self.assertTrue(answer.busy)
                self.assertIsNone(answer.access)
                self.assertEqual(answer.atoms, ("S-TDR2AXI-0043",))
                busy = sbcs_idle(model.cfg, rw=rw, busyerror=True) | BUSY
                self.assertIn(busy, model.predict_sbcs().allowed)
        model.access_retired()
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=rw, busyerror=True),),
        )

    def test_sbcs_write_without_w1c_keeps_sbbusyerror(self) -> None:
        """With the bus idle and only sbbusyerror set, an sbcs write is taken; sbbusyerror
        holds unless the write sets bit 22."""
        model = self._collide()
        rw = sbcs_write(1, readondata=True)
        self.assertFalse(model.predict_dmi_write(sba.SBCS, rw).busy)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=rw, busyerror=True),),
        )
        model.predict_dmi_write(sba.SBCS, rw | BUSYERROR)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg, rw=rw),)
        )

    def test_event_order(self) -> None:
        model = self._stalled()
        with self.assertRaises(sba.SbaStimulusError):
            model.access_retired()
        complete(model)
        with self.assertRaises(sba.SbaStimulusError):
            model.bus_response(error=False)


class DmactiveTest(unittest.TestCase):
    """CHK-DM-DMACTIVE-CLEAR, CHK-DM-DMACTIVE-NO-BUS: S-TDR2AXI-0056, S-TDR2AXI-0064,
    X-RISCV_DEBUG-dmactive."""

    def test_clear_holds_reset_values(self) -> None:
        """dmactive = 0 returns every DM register to its reset value."""
        model = enabled()
        model.predict_dmi_write(sba.SBADDRESS0, 0x1234_5670)
        model.predict_dmi_write(sba.SBADDRESS1, 0x1)
        model.predict_dmi_write(sba.SBDATA1, 0x9)
        model.predict_dmi_write(sba.COMMAND, 0)
        model.predict_dmi_write(sba.DMCONTROL, 0)
        for addr in (sba.SBADDRESS0, sba.SBADDRESS1, sba.SBDATA0, sba.SBDATA1, sba.ABSTRACTCS):
            data = model.predict_dmi_read(addr).data
            self.assertNotEqual(data.mask, 0)
            self.assertTrue(data.matches(0))
        self.assertEqual(model.predict_dmi_read(sba.SBCS).data.mask, BUSY | BUSYERROR)
        self.assertTrue(model.predict_dmi_read(sba.DMCONTROL).data.matches(0))

    def test_no_bus_access_while_inactive(self) -> None:
        """No trigger starts an access while dmactive = 0."""
        model = sba.SbaRefModel(sba.SbaModelCfg(False, 64, 64))
        for addr, value in (
            (sba.SBCS, sbcs_write(2, readonaddr=True)),
            (sba.SBADDRESS0, 0x40),
            (sba.SBDATA0, 0xA5A5_A5A5),
        ):
            self.assertIsNone(model.predict_dmi_write(addr, value).access)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assertEqual(model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),))

    def test_access_in_flight_completes_with_its_payload(self) -> None:
        """S-TDR2AXI-0090: an access in flight when dmactive drops completes with its original
        payload, and an error response leaves its bytes unknown."""
        for error in (False, True):
            with self.subTest(error=error):
                model = enabled()
                model.load_memory(0x800, bytes(4))
                self.assertEqual(single_write(model, 0x800, 2, 0x0BAD_CAFE).addr, 0x800)
                model.predict_dmi_write(sba.DMCONTROL, 0)
                self.assertEqual(model.phase, sba.SbaPhase.PENDING)
                self.assertTrue(model.held)
                model.bus_response(error=error)
                want = (None,) * 4 if error else (0xFE, 0xCA, 0xAD, 0x0B)
                self.assertEqual(model.predict_memory(0x800, 4), want)
                model.predict_dmi_write(sba.DMCONTROL, 1)
                self.assertFalse(model.held)
                self.assertEqual(
                    model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),)
                )


HOLD_ADDR = 0x1000
HOLD_RW = sbcs_write(2, readonaddr=True, autoinc=True)
HOLD_RDATA = 0x5A5A_5A5A_1234_5678


def held_read(*, busyerror: bool = False) -> object:
    """A single read with sbautoincrement in flight, abstractcs.cmderr = 2, and dmactive
    cleared; ``busyerror`` sets sbbusyerror with a colliding sbdata0 write first."""
    model = enabled()
    model.predict_dmi_write(sba.SBCS, HOLD_RW)
    model.predict_dmi_write(sba.SBADDRESS1, 0)
    model.predict_dmi_write(sba.SBADDRESS0, HOLD_ADDR)
    if busyerror:
        model.predict_dmi_write(sba.SBDATA0, 0)
    model.predict_dmi_write(sba.COMMAND, 0x0022_1000)
    model.predict_dmi_write(sba.DMCONTROL, 0)
    return model


class HoldTest(unittest.TestCase):
    """CHK-DM-DMACTIVE-INFLIGHT-HOLD, CHK-DM-DMACTIVE-CLEAR: S-TDR2AXI-0090,
    X-RISCV_DEBUG-dmactive. The DMI answer to an access during the hold is open, the
    R-RISCV_DEBUG profile's dm_reset entry."""

    def assert_held(self, model: object, *, busyerror: bool = False) -> None:
        """sbcs reads its value at the fall with sbbusy = 1, sbaddress the access address."""
        sbcs = model.predict_dmi_read(sba.SBCS).data
        want = sbcs_idle(model.cfg, rw=HOLD_RW, busyerror=busyerror) | BUSY
        self.assertEqual(sbcs.mask, sba.SBCS_DEFINED)
        self.assertEqual(sbcs.allowed, (want,))
        self.assertIn("S-TDR2AXI-0090", sbcs.atoms)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (HOLD_ADDR,))
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.mask, 0xFFFF_FFFF)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.allowed, (0,))

    def test_registers_held_and_the_rest_reset(self) -> None:
        """While held, sbcs and sbaddress keep their values; cmderr and dmcontrol reset."""
        model = held_read()
        self.assertTrue(model.held)
        self.assertFalse(model.dmactive)
        self.assertEqual(model.phase, sba.SbaPhase.PENDING)
        self.assert_held(model)
        cmderr = model.predict_dmi_read(sba.ABSTRACTCS).data
        self.assertEqual(cmderr.mask, 0x7 << 8)
        self.assertTrue(cmderr.matches(0))
        self.assertTrue(model.predict_dmi_read(sba.DMCONTROL).data.matches(0))

    def test_hold_time_accesses_change_nothing(self) -> None:
        """A write of any SBA register and an sbdata read change nothing, sbbusyerror
        included, start nothing, and have an open answer."""
        for busyerror in (False, True):
            model = held_read(busyerror=busyerror)
            for op, addr, data in (
                ("w", sba.SBADDRESS0, 0x3000),
                ("w", sba.SBADDRESS1, 0x1),
                ("w", sba.SBDATA0, 0xFFFF_FFFF),
                ("w", sba.SBDATA1, 0xFFFF_FFFF),
                ("w", sba.SBCS, BUSYERROR | CLEAR_ERRORS | sbcs_write(0, readondata=True)),
                ("r", sba.SBDATA0, 0),
                ("r", sba.SBDATA1, 0),
            ):
                with self.subTest(busyerror=busyerror, op=op, addr=addr):
                    if op == "w":
                        answer = model.predict_dmi_write(addr, data)
                    else:
                        answer = model.predict_dmi_read(addr)
                    self.assertTrue(answer.open_status)
                    self.assertFalse(answer.busy)
                    self.assertEqual(answer.data.mask, 0)
                    self.assertIsNone(answer.access)
                    self.assert_held(model, busyerror=busyerror)
            self.assertEqual(model.phase, sba.SbaPhase.PENDING)

    def test_hold_time_sbaddress_above_the_width_is_open(self) -> None:
        """A hold-time sbaddress0 write with bits at and above AXI_ADDR_WIDTH changes
        nothing and has an open answer."""
        model = enabled(addr_width=20, data_width=32)
        model.predict_dmi_write(sba.SBCS, HOLD_RW)
        model.predict_dmi_write(sba.SBADDRESS0, HOLD_ADDR)
        model.predict_dmi_write(sba.DMCONTROL, 0)
        self.assertTrue(model.held)
        answer = model.predict_dmi_write(sba.SBADDRESS0, 0xFFFF_FFFF)
        self.assertTrue(answer.open_status)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (HOLD_ADDR,))

    def test_completion_drops_the_result(self) -> None:
        """The held access's response lands nowhere: after it sbbusy reads 0 and sbaddress
        and sbdata read their reset values, before and after dmactive is set again."""
        for error in (False, True):
            with self.subTest(error=error):
                model = held_read()
                model.bus_response(error=error, rdata=HOLD_RDATA)
                self.assertEqual(model.phase, sba.SbaPhase.RESPONDED)
                self.assertTrue(model.held)
                self.assertEqual(
                    set(model.predict_dmi_read(sba.SBADDRESS0).data.allowed), {HOLD_ADDR, 0}
                )
                self.assertEqual(model.predict_dmi_read(sba.SBCS).data.mask, BUSY | BUSYERROR)
                model.access_retired()
                self.assertFalse(model.held)
                sbcs = model.predict_dmi_read(sba.SBCS).data
                self.assertEqual((sbcs.allowed, sbcs.mask), ((0,), BUSY | BUSYERROR))
                for addr in (sba.SBADDRESS0, sba.SBADDRESS1, sba.SBDATA0, sba.SBDATA1):
                    data = model.predict_dmi_read(addr).data
                    self.assertEqual((data.allowed, data.mask), ((0,), 0xFFFF_FFFF))
                model.predict_dmi_write(sba.DMCONTROL, 1)
                self.assertEqual(
                    model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),)
                )
                for addr in (sba.SBADDRESS0, sba.SBADDRESS1, sba.SBDATA0, sba.SBDATA1):
                    self.assertEqual(model.predict_dmi_read(addr).data.allowed, (0,))
                self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(0))

    def test_completion_window_pairs_sbbusy_with_sbbusyerror(self) -> None:
        """Between the held response and sbbusy's fall, sbcs reads sbbusy and sbbusyerror
        as held or both 0, never one without the other; once sbbusy has fallen, both 0."""
        for busyerror in (False, True):
            with self.subTest(busyerror=busyerror):
                model = held_read(busyerror=busyerror)
                model.bus_response(error=False, rdata=HOLD_RDATA)
                held = BUSY | (BUSYERROR if busyerror else 0)
                sbcs = model.predict_dmi_read(sba.SBCS).data
                self.assertEqual(sbcs.mask, BUSY | BUSYERROR)
                self.assertEqual(set(sbcs.allowed), {held, 0})
                self.assertTrue(sbcs.matches(sbcs_idle(model.cfg, rw=HOLD_RW) | held))
                self.assertTrue(sbcs.matches(0x0004_0000))
                self.assertFalse(sbcs.matches(held ^ BUSYERROR))
                self.assertFalse(sbcs.matches(BUSYERROR))
                model.access_retired()
                sbcs = model.predict_dmi_read(sba.SBCS).data
                self.assertEqual((sbcs.allowed, sbcs.mask), ((0,), BUSY | BUSYERROR))
                self.assertFalse(sbcs.matches(BUSYERROR))
                model.predict_dmi_write(sba.DMCONTROL, 1)
                self.assertEqual(
                    model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),)
                )

    def test_fall_with_no_access_clears_sbbusyerror(self) -> None:
        """sbbusyerror set with the bus idle, then dmactive = 0: sbcs reads it 0."""
        model = enabled()
        model.predict_dmi_write(sba.SBCS, HOLD_RW)
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        model.predict_dmi_write(sba.SBADDRESS0, HOLD_ADDR)
        self.assertTrue(model.predict_dmi_write(sba.SBDATA0, 0).busy)
        complete(model, rdata=HOLD_RDATA)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=HOLD_RW, busyerror=True),),
        )
        model.predict_dmi_write(sba.DMCONTROL, 0)
        self.assertFalse(model.held)
        sbcs = model.predict_dmi_read(sba.SBCS).data
        self.assertEqual((sbcs.allowed, sbcs.mask), ((0,), BUSY | BUSYERROR))
        self.assertFalse(sbcs.matches(BUSYERROR))

    def test_reads_in_the_completion_window(self) -> None:
        """Between the response and sbbusy's fall the registers read held or reset values."""
        model = held_read()
        model.bus_response(error=False, rdata=HOLD_RDATA)
        sbcs = model.predict_dmi_read(sba.SBCS).data
        self.assertTrue(sbcs.matches(sbcs_idle(model.cfg, rw=HOLD_RW) | BUSY))
        self.assertTrue(sbcs.matches(0x0004_0000))
        answer = model.predict_dmi_read(sba.SBDATA0)
        self.assertTrue(answer.open_status)
        self.assertTrue(model.predict_dmi_write(sba.SBADDRESS0, 0x40).open_status)
        model.access_retired()
        self.assertFalse(model.predict_dmi_write(sba.SBADDRESS0, 0x40).open_status)

    def test_bus_reset_ends_the_hold(self) -> None:
        """RST-ARST-BUS drops the held access and its unanswered bytes."""
        model = enabled()
        model.load_memory(0x800, bytes(4))
        single_write(model, 0x800, 2, 0x0BAD_CAFE)
        model.predict_dmi_write(sba.DMCONTROL, 0)
        model.reset("RST-ARST-BUS")
        self.assertFalse(model.held)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)
        self.assertEqual(model.predict_memory(0x800, 4), (None,) * 4)


class RearmTest(unittest.TestCase):
    """CHK-DM-DMACTIVE-REARM: S-TDR2AXI-0091, X-RISCV_DEBUG-dmactive. dmactive set again
    before the held access's response: nothing resets and the result lands."""

    def test_read_result_lands(self) -> None:
        """The read data lands in sbdata0, sbaddress advances with autoinc, sbbusy falls."""
        model = held_read()
        answer = model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assertIn("S-TDR2AXI-0091", answer.atoms)
        self.assertFalse(model.held)
        self.assertEqual(model.phase, sba.SbaPhase.PENDING)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=HOLD_RW) | BUSY,),
        )
        complete(model, rdata=HOLD_RDATA)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg, rw=HOLD_RW),)
        )
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (HOLD_ADDR + 4,))
        self.assertEqual(model.predict_sbdata(0).allowed, (0x1234_5678,))
        self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(0))

    def test_error_lands(self) -> None:
        """An error response sets sberror = 2 and sbaddress does not advance."""
        model = held_read()
        model.predict_dmi_write(sba.DMCONTROL, 1)
        complete(model, error=True)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=HOLD_RW, sberror=2),),
        )
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (HOLD_ADDR,))

    def test_write_lands(self) -> None:
        """A held write completes into memory and sbaddress advances with autoinc."""
        model = enabled()
        model.load_memory(0x800, bytes(4))
        model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
        model.predict_dmi_write(sba.SBADDRESS1, 0)
        model.predict_dmi_write(sba.SBADDRESS0, 0x800)
        model.predict_dmi_write(sba.SBDATA0, 0x0BAD_CAFE)
        model.predict_dmi_write(sba.DMCONTROL, 0)
        model.predict_dmi_write(sba.DMCONTROL, 1)
        complete(model)
        self.assertEqual(model.predict_memory(0x800, 4), (0xFE, 0xCA, 0xAD, 0x0B))
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (0x804,))

    def test_active_rules_return(self) -> None:
        """After the re-arm an sbdata0 write collides and latches sbbusyerror."""
        model = held_read()
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assertTrue(model.predict_dmi_write(sba.SBDATA0, 1).busy)
        complete(model, rdata=HOLD_RDATA)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=HOLD_RW, busyerror=True),),
        )

    def test_rearm_after_the_drop_resets(self) -> None:
        """dmactive set after the held response, before sbbusy falls: the reset values."""
        model = held_read()
        model.bus_response(error=False, rdata=HOLD_RDATA)
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assertFalse(model.held)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)
        self.assertEqual(model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),))
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (0,))
        self.assertEqual(model.predict_sbdata(0).allowed, (0,))


class AbstractCommandTest(unittest.TestCase):
    """CHK-DM-CMDERR: S-TDR2AXI-0010, X-RISCV_DEBUG-cmderr."""

    def test_command_sets_cmderr_two(self) -> None:
        """Any command sets cmderr to 2 without a bus access, and cmderr clears on write 1."""
        model = enabled()
        self.assertIsNone(model.predict_dmi_write(sba.COMMAND, 0x0022_1000).access)
        self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(2 << 8))
        model.predict_dmi_write(sba.COMMAND, 0)
        self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(2 << 8))
        model.predict_dmi_write(sba.ABSTRACTCS, 0x7 << 8)
        self.assertTrue(model.predict_dmi_read(sba.ABSTRACTCS).data.matches(0))
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)


class NdmresetTest(unittest.TestCase):
    """CHK-DM-NDMRESET: S-TDR2AXI-0011, X-RISCV_DEBUG-ndmreset."""

    def test_ndmreset_resets_nothing(self) -> None:
        """ndmreset reads back but changes no SBA state or memory."""
        model = enabled()
        model.load_memory(0x900, b"\x01\x02\x03\x04")
        model.predict_dmi_write(sba.SBADDRESS0, 0x900)
        for dmcontrol in (0x3, 0x1):
            self.assertIsNone(model.predict_dmi_write(sba.DMCONTROL, dmcontrol).access)
            self.assertTrue(model.predict_dmi_read(sba.DMCONTROL).data.matches(dmcontrol))
            self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(0x900))
            self.assertEqual(model.predict_memory(0x900, 4), (1, 2, 3, 4))

    def test_enable_with_ndmreset_leaves_it_free(self) -> None:
        model = sba.SbaRefModel(sba.SbaModelCfg(False, 64, 64))
        model.predict_dmi_write(sba.DMCONTROL, 0x3)
        data = model.predict_dmi_read(sba.DMCONTROL).data
        self.assertEqual(data.mask, 0x1)


class AddressWidthTest(unittest.TestCase):
    """CHK-SBA-ADDRESS-WIDTH: S-TDR2AXI-0014, S-TDR2AXI-0022, X-RISCV_DEBUG-sbaddress-regs."""

    def test_walking_one(self) -> None:
        """Every address bit below AXI_ADDR_WIDTH reaches the bus and the sbaddress registers."""
        for data_width in (32, 64):
            for addr_width in (3, 32, 33, 48, 64):
                for bit in range(addr_width):
                    with self.subTest(data_width=data_width, addr_width=addr_width, bit=bit):
                        model = enabled(addr_width=addr_width, data_width=data_width)
                        access = single_read(model, 1 << bit, 0)
                        self.assertEqual(access.addr, 1 << bit)
                        complete(model)
                        low = model.predict_dmi_read(sba.SBADDRESS0).data
                        high = model.predict_dmi_read(sba.SBADDRESS1).data
                        self.assertTrue(low.matches((1 << bit) & 0xFFFF_FFFF))
                        self.assertTrue(high.matches((1 << bit) >> 32))
                        high_mask = ((1 << addr_width) - 1) >> 32 if addr_width > 32 else 0
                        self.assertEqual(high.mask, high_mask or 0xFFFF_FFFF)

    def test_bits_above_the_width_are_refused(self) -> None:
        """The H2 narrowing: no sbaddress bit at or above AXI_ADDR_WIDTH is predicted in a
        register that exists."""
        model = enabled(addr_width=33, data_width=32)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.mask, 0x1)
        model.predict_dmi_write(sba.SBADDRESS1, 0x1)
        with self.assertRaises(sba.SbaStimulusError):
            model.predict_dmi_write(sba.SBADDRESS1, 0x2)
        model = enabled(addr_width=20, data_width=32)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.mask, 0xF_FFFF)
        model.predict_dmi_write(sba.SBADDRESS0, 0xF_FFFF)
        with self.assertRaises(sba.SbaStimulusError):
            model.predict_dmi_write(sba.SBADDRESS0, 1 << 20)
        with self.assertRaises(sba.SbaStimulusError):
            sba.predict_bus_access(model.cfg, sba.BusDir.READ, 1 << 20, 0)

    def test_increment_to_the_top_is_refused(self) -> None:
        """An auto-increment that would reach 2**AXI_ADDR_WIDTH is refused, not wrapped."""
        for addr_width in (32, 48, 64):
            with self.subTest(addr_width=addr_width):
                model = enabled(addr_width=addr_width)
                top = (1 << addr_width) - 8
                model.predict_dmi_write(sba.SBCS, sbcs_write(2, autoinc=True))
                model.predict_dmi_write(sba.SBADDRESS1, top >> 32)
                model.predict_dmi_write(sba.SBADDRESS0, top & 0xFFFF_FFFF)
                self.assertEqual(model.predict_dmi_write(sba.SBDATA0, 1).access.addr, top)
                complete(model)
                with self.assertRaises(sba.SbaStimulusError):
                    model.predict_dmi_write(sba.SBDATA0, 2)

    def test_last_word_without_autoincrement(self) -> None:
        """The top word of the address space is reachable when nothing increments past it."""
        model = enabled(addr_width=32, data_width=32)
        self.assertEqual(single_read(model, 0xFFFF_FFFC, 2).addr, 0xFFFF_FFFC)


class AbsentSbaddress1Test(unittest.TestCase):
    """CHK-SBA-SBADDRESS1-ABSENT: S-TDR2AXI-0014, X-RISCV_DEBUG-sbaddress-regs. At
    AXI_ADDR_WIDTH <= 32 sbaddress1 reads 0 and a write of it changes nothing, but collides
    like the present register (the R-RISCV_DEBUG profile's departure_absent entry)."""

    WIDTHS = ((2, 32), (3, 64), (20, 32), (32, 32), (32, 64))

    def test_reads_zero_after_any_write(self) -> None:
        """Each width reads 0 before and after writes of any value, and the next access
        and sbcs are unchanged."""
        for addr_width, data_width in self.WIDTHS:
            with self.subTest(addr_width=addr_width, data_width=data_width):
                model = enabled(addr_width=addr_width, data_width=data_width)
                read = model.predict_dmi_read(sba.SBADDRESS1)
                self.assertEqual((read.data.allowed, read.data.mask), ((0,), 0xFFFF_FFFF))
                for value in (0xFFFF_FFFF, 0x8000_0000, 0x1):
                    answer = model.predict_dmi_write(sba.SBADDRESS1, value)
                    self.assertFalse(answer.busy)
                    self.assertIsNone(answer.access)
                    self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.allowed, (0,))
                self.assertEqual(single_read(model, 0, 0).addr, 0)
                complete(model)
                self.assertEqual(
                    model.predict_dmi_read(sba.SBCS).data.allowed,
                    (sbcs_idle(model.cfg, rw=sbcs_write(0, readonaddr=True)),),
                )

    def test_write_collides_while_busy_or_busyerror(self) -> None:
        """While an access is in flight, or after one collided, the write is answered busy
        and sets sbbusyerror."""
        model = enabled(addr_width=32, data_width=32)
        single_read(model, 0x40, 2)
        self.assertTrue(model.predict_dmi_write(sba.SBADDRESS1, 0x1).busy)
        complete(model)
        rw = sbcs_write(2, readonaddr=True)
        self.assertEqual(
            model.predict_dmi_read(sba.SBCS).data.allowed,
            (sbcs_idle(model.cfg, rw=rw, busyerror=True),),
        )
        self.assertTrue(model.predict_dmi_write(sba.SBADDRESS1, 0x0).busy)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.allowed, (0,))
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS0).data.allowed, (0x40,))

    def test_write_free_while_inactive(self) -> None:
        """While dmactive = 0 the write is taken and changes nothing."""
        model = sba.SbaRefModel(sba.SbaModelCfg(False, 32, 32))
        self.assertFalse(model.predict_dmi_write(sba.SBADDRESS1, 0xFFFF_FFFF).busy)
        self.assertEqual(model.predict_dmi_read(sba.SBADDRESS1).data.allowed, (0,))


class ResetTest(unittest.TestCase):
    """CHK-RESET-ASYNC-ANY-TIME: S-TDR2AXI-0026, S-TDR2AXI-0052, RST-ARST-BUS; RST-TRST and
    RST-ARST-TCK leave the DM alone (S-TDR2AXI-0054)."""

    def test_bus_reset(self) -> None:
        """The bus reset clears the DM and a pending write; the TCK reset and TRST leave both."""
        model = enabled()
        model.load_memory(0xA00, bytes(4))
        single_write(model, 0xA00, 2, 0x1111_1111)
        for scope in ("RST-ARST-TCK", "RST-TRST"):
            model.reset(scope)
            self.assertEqual(model.phase, sba.SbaPhase.PENDING)
        model.reset("RST-ARST-BUS")
        self.assertFalse(model.dmactive)
        self.assertEqual(model.phase, sba.SbaPhase.IDLE)
        self.assertEqual(model.predict_memory(0xA00, 4), (None, None, None, None))
        self.assertTrue(model.predict_dmi_read(sba.SBADDRESS0).data.matches(0))
        model.predict_dmi_write(sba.DMCONTROL, 1)
        self.assertEqual(model.predict_dmi_read(sba.SBCS).data.allowed, (sbcs_idle(model.cfg),))
        with self.assertRaises(ValueError):
            model.reset("no such scope")

    def test_dmactive_reset(self) -> None:
        """RST-DMACTIVE clears dm_csrs as a dmactive = 0 write does, and repeats harmlessly."""
        model = enabled()
        model.predict_dmi_write(sba.SBADDRESS0, 0x40)
        for _ in range(2):
            model.reset("RST-DMACTIVE")
            self.assertFalse(model.dmactive)
            self.assertTrue(model.predict_sbaddress(0).matches(0))


class NegativeKnobTest(unittest.TestCase):
    """The negative knob corrupts every prediction, so a paired check must fail."""

    def test_predictions_corrupted(self) -> None:
        """The knob flips register, bus-address and memory predictions."""
        good, bad = enabled(), enabled(negative=True)
        want = sbcs_idle(good.cfg)
        self.assertTrue(good.predict_dmi_read(sba.SBCS).data.matches(want))
        self.assertFalse(bad.predict_dmi_read(sba.SBCS).data.matches(want))
        self.assertFalse(bad.predict_sbcs().matches(want))
        self.assertEqual(single_write(bad, 0x40, 2, 0).addr, 0x41)
        bad.load_memory(0x80, b"\x10")
        self.assertEqual(bad.predict_memory(0x80, 1), (0x11,))
        self.assertEqual(sba.NEGATIVE_KNOB, "TDR2AXI_SBA_REF_MODEL_NEGATIVE")


if __name__ == "__main__":
    unittest.main()
