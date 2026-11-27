# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi Debug Module and System Bus Access reference model.

``SbaRefModel`` predicts, at transaction level, the Debug Module's answer to each DMI
request that reaches it, the AXI transaction each system bus access issues, and the
subordinate memory image after each write. Its sources are the tdr2axi specification
atoms, the RISC-V External Debug Support 0.13.2 and AXI X-atoms of the trace store, and the
DV readings recorded there. It reads no RTL, no DUT signal and no observation it predicts.

Inputs are stimulus: DMI requests in the order they reach the DM, the subordinate's
response to each bus access (the R data and response code it drives into ``i_axi_rsp``),
memory fills, resets, and the point at which the DM reports an access complete. Outputs are
frozen expected items for the bench's scoreboard; the model compares nothing. It has no
clock, so no prediction depends on the ``i_clk`` to TCK ratio. Python core only: the bench
has a single cocotb realization.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, IntEnum

__all__ = [
    "ABSTRACTCS",
    "COMMAND",
    "DMCONTROL",
    "DMSTATUS",
    "NEGATIVE_KNOB",
    "SBADDRESS0",
    "SBADDRESS1",
    "SBCS",
    "SBDATA0",
    "SBDATA1",
    "BusDir",
    "DmAnswer",
    "Expected",
    "SbError",
    "SbaBusAccess",
    "SbaModelCfg",
    "SbaPhase",
    "SbaRefModel",
    "SbaStimulusError",
    "predict_bus_access",
]

NEGATIVE_KNOB = "TDR2AXI_SBA_REF_MODEL_NEGATIVE"

# DMI addresses: S-TDR2AXI-0062, X-RISCV_DEBUG-dm-addresses.
DMCONTROL = 0x10
DMSTATUS = 0x11
ABSTRACTCS = 0x16
COMMAND = 0x17
SBCS = 0x38
SBADDRESS0 = 0x39
SBADDRESS1 = 0x3A
SBDATA0 = 0x3C
SBDATA1 = 0x3D

DMI_ADDR_LIMIT = 1 << 7
WORD_MASK = 0xFFFF_FFFF
_MASK64 = (1 << 64) - 1

# dmcontrol: X-RISCV_DEBUG-dmactive, X-RISCV_DEBUG-ndmreset.
DMCONTROL_DMACTIVE = 1 << 0
DMCONTROL_NDMRESET = 1 << 1

# dmstatus: X-RISCV_DEBUG-dm-version, X-RISCV_DEBUG-hart-unavail,
# X-RISCV_DEBUG-authenticated, X-RISCV_DEBUG-authbusy.
DMSTATUS_VERSION_013 = 2
DMSTATUS_VERSION_MASK = 0xF
DMSTATUS_AUTHBUSY = 1 << 6
DMSTATUS_AUTHENTICATED = 1 << 7
DMSTATUS_ANYUNAVAIL = 1 << 12
DMSTATUS_ALLUNAVAIL = 1 << 13

# abstractcs.cmderr: X-RISCV_DEBUG-cmderr.
ABSTRACTCS_CMDERR_LSB = 8
CMDERR_FIELD = 0x7
CMDERR_NOT_SUPPORTED = 2

# sbcs: X-RISCV_DEBUG-sbcs-fields, X-RISCV_DEBUG-sbbusy, X-RISCV_DEBUG-sbbusyerror,
# X-RISCV_DEBUG-sberror. Bits 28:23 hold no field and are left out of SBCS_DEFINED.
SBCS_SBVERSION_LSB = 29
SBCS_SBBUSYERROR = 1 << 22
SBCS_SBBUSY = 1 << 21
SBCS_SBREADONADDR = 1 << 20
SBCS_SBACCESS_LSB = 17
SBCS_SBACCESS_FIELD = 0x7
SBCS_SBAUTOINCREMENT = 1 << 16
SBCS_SBREADONDATA = 1 << 15
SBCS_SBERROR_LSB = 12
SBCS_SBERROR_FIELD = 0x7
SBCS_SBASIZE_LSB = 5
SBCS_SBACCESS8_16_32 = 0b0_0111
SBCS_SBACCESS64 = 0b0_1000
SBCS_DEFINED = 0xE07F_FFFF
SBVERSION_1 = 1
SBACCESS_RESET = 2
_SBCS_RW = (
    SBCS_SBREADONADDR
    | (SBCS_SBACCESS_FIELD << SBCS_SBACCESS_LSB)
    | SBCS_SBAUTOINCREMENT
    | SBCS_SBREADONDATA
)
# The sbcs fields compared while dmactive = 0 outside a hold, or from a held access's
# completion until sbbusy falls
_SBCS_BUSY_PAIR = SBCS_SBBUSY | SBCS_SBBUSYERROR

_SBA_DATA_REGS = frozenset({SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1})
_SBADDRESS_REGS = frozenset({SBADDRESS0, SBADDRESS1})
_RESET_SCOPES_DM = frozenset({"RST-ARST-BUS", "RST-SCAN"})
_RESET_SCOPES_OTHER = frozenset({"RST-ARST-TCK", "RST-TRST"})
_RESET_SCOPE_DMACTIVE = "RST-DMACTIVE"

_ATOMS_MAP = ("S-TDR2AXI-0062", "X-RISCV_DEBUG-dm-addresses")
_ATOMS_DMCONTROL = (
    "S-TDR2AXI-0011",
    "S-TDR2AXI-0056",
    "S-TDR2AXI-0064",
    "X-RISCV_DEBUG-dmactive",
    "X-RISCV_DEBUG-ndmreset",
)
_ATOMS_DMACTIVE = ("S-TDR2AXI-0056", "S-TDR2AXI-0064", "X-RISCV_DEBUG-dmactive")
_ATOMS_DMSTATUS = (
    "S-TDR2AXI-0002",
    "S-TDR2AXI-0009",
    "S-TDR2AXI-0062",
    "X-RISCV_DEBUG-dm-version",
    "X-RISCV_DEBUG-hart-unavail",
    "X-RISCV_DEBUG-authenticated",
    "X-RISCV_DEBUG-authbusy",
)
_ATOMS_ABSTRACTCS = ("S-TDR2AXI-0010", "X-RISCV_DEBUG-cmderr")
_ATOMS_SBCS = (
    "S-TDR2AXI-0002",
    "S-TDR2AXI-0014",
    "S-TDR2AXI-0015",
    "S-TDR2AXI-0023",
    "S-TDR2AXI-0060",
    "S-TDR2AXI-0061",
    "X-RISCV_DEBUG-sbcs-fields",
    "X-RISCV_DEBUG-sbbusy",
    "X-RISCV_DEBUG-sbbusyerror",
    "X-RISCV_DEBUG-sberror",
)
_ATOMS_SBADDRESS = (
    "S-TDR2AXI-0014",
    "S-TDR2AXI-0022",
    "S-TDR2AXI-0061",
    "S-TDR2AXI-0079",
    "S-TDR2AXI-0081",
    "S-TDR2AXI-0093",
    "S-TDR2AXI-0094",
    "X-RISCV_DEBUG-sbaddress-regs",
    "X-RISCV_DEBUG-sbautoincrement",
    "X-RISCV_DEBUG-sbaddress0-read",
    "X-RISCV_DEBUG-sbdata0-write",
    "X-RISCV_DEBUG-sbdata0-read",
)
_ATOMS_SBDATA = (
    "S-TDR2AXI-0015",
    "S-TDR2AXI-0076",
    "S-TDR2AXI-0077",
    "S-TDR2AXI-0081",
    "X-AXI4_LITE-full-width",
    "X-RISCV_DEBUG-sbdata-lanes",
    "X-RISCV_DEBUG-sbdata0-read",
)
_ATOMS_BUSY = ("S-TDR2AXI-0043", "X-RISCV_DEBUG-sbbusy", "X-RISCV_DEBUG-sbbusyerror")
# The busy answer to an sbcs write while an access is in flight. The answer is the class
# docstring's reading of the RTL, which no atom gives, and the write itself breaks
# X-RISCV_DEBUG-sbbusy. S-TDR2AXI-0043 gives the TDR's sticky busy after a busy answer.
_ATOMS_SBCS_BUSY = ("S-TDR2AXI-0043",)
_ATOMS_OPEN = ("S-TDR2AXI-0072", "S-TDR2AXI-0078", "X-RISCV_DEBUG-sbbusy")
# An access in flight across a dmactive = 0 write: the registers it holds, its drop on
# completion, and the answer to an SBA register access meanwhile, which the R-RISCV_DEBUG
# profile's dm_reset entry leaves free.
_ATOMS_HOLD = ("S-TDR2AXI-0090", "X-RISCV_DEBUG-dmactive")
_ATOMS_REARM = ("S-TDR2AXI-0091", "X-RISCV_DEBUG-dmactive")
# sbaddress1 at AXI_ADDR_WIDTH <= 32, where it does not exist and reads 0.
_ATOMS_ABSENT = ("S-TDR2AXI-0014", "X-RISCV_DEBUG-sbaddress-regs")
_ATOMS_ACCESS = (
    "S-TDR2AXI-0060",
    "S-TDR2AXI-0071",
    "S-TDR2AXI-0076",
    "S-TDR2AXI-0079",
    "S-TDR2AXI-0081",
    "X-RISCV_DEBUG-sbaddress0-read",
    "X-RISCV_DEBUG-sbdata0-write",
    "X-RISCV_DEBUG-sbdata0-read",
    "X-RISCV_DEBUG-sberror",
)
_ATOMS_BUS = (
    "S-TDR2AXI-0001",
    "S-TDR2AXI-0013",
    "S-TDR2AXI-0014",
    "S-TDR2AXI-0015",
    "S-TDR2AXI-0022",
    "S-TDR2AXI-0058",
    "S-TDR2AXI-0059",
    "X-AXI4-strobe-lanes",
    "X-AXI4_LITE-full-width",
    "X-AXI4_LITE-strobe-lanes",
)
_ATOMS_MEMORY = ("S-TDR2AXI-0001", "S-TDR2AXI-0011", "S-TDR2AXI-0071", "X-AXI4-response-codes")


class SbaStimulusError(ValueError):
    """Stimulus outside what the spec defines, so the model has no prediction for it."""


class BusDir(Enum):
    """Direction of a system bus access."""

    READ = "read"
    WRITE = "write"


class SbError(IntEnum):
    """sbcs.sberror codes the model sets (X-RISCV_DEBUG-sberror, S-TDR2AXI-0060, -0061)."""

    NONE = 0
    BAD_ADDRESS = 2
    ALIGNMENT = 3
    SIZE = 4


class SbaPhase(Enum):
    """System bus access engine phase.

    PENDING runs from an access's start to the subordinate's response; RESPONDED from that
    response to the point the DM reports the access complete.
    """

    IDLE = "idle"
    PENDING = "pending"
    RESPONDED = "responded"


class _Trigger(Enum):
    SBADDRESS0_WRITE = "sbaddress0 write"
    SBDATA0_WRITE = "sbdata0 write"
    SBDATA0_READ = "sbdata0 read"


@dataclass(frozen=True)
class SbaModelCfg:
    """Build parameters: CFG-USE-AXI-LITE, CFG-AXI-ADDR-WIDTH and CFG-AXI-DATA-WIDTH."""

    lite: bool
    addr_width: int
    data_width: int

    def __post_init__(self) -> None:
        if self.data_width not in (32, 64):
            raise ValueError(
                f"AXI_DATA_WIDTH must be 32 or 64 (S-TDR2AXI-0015), got {self.data_width}"
            )
        low = 2 if self.data_width == 32 else 3
        if not low <= self.addr_width <= 64:
            raise ValueError(
                f"AXI_ADDR_WIDTH must be {low}..64 with {self.data_width}-bit data "
                f"(S-TDR2AXI-0014, S-TDR2AXI-0021), got {self.addr_width}"
            )

    @property
    def bus_bytes(self) -> int:
        """Bytes per AXI data beat."""
        return self.data_width // 8

    @property
    def addr_mask(self) -> int:
        """Mask of the address bits below AXI_ADDR_WIDTH."""
        return (1 << self.addr_width) - 1


@dataclass(frozen=True)
class Expected:
    """A predicted value: an observation matches when it equals any allowed value on the mask.

    One allowed value is an exact prediction, several are the set the spec allows, and a
    clear mask bit is a bit the spec leaves free.
    """

    allowed: tuple[int, ...]
    mask: int
    atoms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed:
            raise ValueError("Expected needs at least one allowed value")

    @classmethod
    def exact(cls, value: int, mask: int, atoms: tuple[str, ...]) -> Expected:
        """One value compared on ``mask``."""
        return cls((value & mask,), mask, atoms)

    @classmethod
    def free(cls, atoms: tuple[str, ...]) -> Expected:
        """A value the spec leaves open: nothing is compared."""
        return cls((0,), 0, atoms)

    def matches(self, observed: int) -> bool:
        """Whether ``observed`` is one of the allowed values on the mask bits."""
        return any((observed ^ value) & self.mask == 0 for value in self.allowed)

    def flipped(self) -> Expected:
        """The same prediction with its lowest compared bit inverted."""
        low = self.mask & -self.mask
        return replace(self, allowed=tuple(value ^ low for value in self.allowed))


@dataclass(frozen=True)
class SbaBusAccess:
    """One predicted AXI transaction: AR for a read, AW and W for a write.

    ``size`` is AxSIZE on AXI4 and None on AXI4-Lite, which has none. ``wdata`` compares
    only the strobed bytes whose sbdata value is known.
    """

    direction: BusDir
    addr: int
    size: int | None
    wstrb: int | None
    wdata: Expected | None
    atoms: tuple[str, ...]


@dataclass(frozen=True)
class DmAnswer:
    """The DM's predicted answer to one DMI request and the bus access the request starts.

    ``busy`` is the DM's busy answer of S-TDR2AXI-0043; the DM answers nothing else but
    success. ``data`` is the read data, free for a write. ``open_status`` marks an answer
    whose response code the spec leaves open, busy or success: ``busy`` is then False and
    ``data`` free, and the DM's response code decides the TDR's status.
    """

    busy: bool
    data: Expected
    access: SbaBusAccess | None
    atoms: tuple[str, ...]
    open_status: bool = False


@dataclass(frozen=True)
class _Access:
    trigger: _Trigger
    addr: int
    size: int
    data: int
    data_known: int
    bus: SbaBusAccess | None = None
    held: bool = False


@dataclass(frozen=True)
class _Snapshot:
    sbaddress: int
    sbaddress_known: int
    sbdata: int
    sbdata_known: int


def predict_bus_access(
    cfg: SbaModelCfg,
    direction: BusDir,
    addr: int,
    size: int,
    *,
    data: int = 0,
    data_known: int = 0,
) -> SbaBusAccess:
    """Predict the AXI transaction of one aligned system bus access of 2**size bytes.

    Serves CHK-SBA-SINGLE-WRITE, CHK-SBA-SINGLE-READ, CHK-SBA-BLOCK-WRITE,
    CHK-SBA-BLOCK-READ, CHK-AXI-AXI4-SIZE, CHK-AXI-LITE-FULL-WIDTH, CHK-SBA-ADDRESS-WIDTH
    and CHK-TDR-RATIO-INVARIANT. Atoms: S-TDR2AXI-0001, S-TDR2AXI-0013, S-TDR2AXI-0014,
    S-TDR2AXI-0022, S-TDR2AXI-0058, S-TDR2AXI-0059, X-AXI4-strobe-lanes,
    X-AXI4_LITE-full-width, X-AXI4_LITE-strobe-lanes.

    Exact prediction: AxADDR is the access address; AxSIZE equals sbaccess on AXI4 and is
    absent on AXI4-Lite; a write's WSTRB marks the 2**size bytes on the lanes the address
    selects within the bus word, and WDATA carries ``data`` on those lanes, compared where
    ``data_known`` marks its bits known. An address at or above 2**AXI_ADDR_WIDTH raises
    ``SbaStimulusError``: the model predicts no address bit above the width.
    """
    nbytes = 1 << size
    if nbytes > cfg.bus_bytes or addr % nbytes:
        raise ValueError(f"access of {nbytes} bytes at 0x{addr:x} is not a legal bus access")
    if addr & ~cfg.addr_mask:
        raise SbaStimulusError(
            f"address 0x{addr:x} has a bit at or above AXI_ADDR_WIDTH = {cfg.addr_width}"
        )
    bus_addr = addr
    axsize = None if cfg.lite else size
    if direction is BusDir.READ:
        return SbaBusAccess(direction, bus_addr, axsize, None, None, _ATOMS_BUS)
    shift = 8 * (bus_addr % cfg.bus_bytes)
    width = (1 << (8 * nbytes)) - 1
    known = _byte_known(data_known & width) << shift
    wdata = Expected(((data & width) << shift,), known, _ATOMS_BUS)
    wstrb = ((1 << nbytes) - 1) << (bus_addr % cfg.bus_bytes)
    return SbaBusAccess(direction, bus_addr, axsize, wstrb, wdata, _ATOMS_BUS)


def _byte_known(known: int) -> int:
    """Narrow a bit mask to whole bytes whose eight bits are all known."""
    result = 0
    for lane in range(8):
        byte = 0xFF << (8 * lane)
        if known & byte == byte:
            result |= byte
    return result


def _check_dmi(addr: int, data: int) -> None:
    if not 0 <= addr < DMI_ADDR_LIMIT:
        raise ValueError(f"DMI address 0x{addr:x} is wider than abits = 7")
    if not 0 <= data <= WORD_MASK:
        raise ValueError(f"DMI data 0x{data:x} is wider than 32 bits")


class SbaRefModel:
    """Debug Module register file and System Bus Access engine predicted from the spec.

    Each ``predict_dmi_read`` or ``predict_dmi_write`` call is one DMI request reaching the
    DM: it applies the request and returns the DM's answer with the bus access the request
    starts. The engine is IDLE, PENDING from an access's start until ``bus_response``, or
    RESPONDED from then until ``access_retired``, the point sbbusy falls. An access refused
    for alignment or size starts nothing on the bus and is RESPONDED at once.
    X-RISCV_DEBUG-sbbusy bounds neither the RESPONDED interval nor when within it the DM's
    registers take the access's outcome, so in RESPONDED an sbcs read predicts the set of
    values the completing access may show, and an sbaddress or sbdata read the value before
    or after that outcome.

    Resynchronization: ``access_retired`` takes the point sbbusy falls from the DM's
    ``sbbusy`` observable, at the first ``i_clk`` edge in RESPONDED where it reads 0 and
    before any DMI request the DM takes at that edge, or from the poll that reads
    sbbusy = 0; for a held access, after the request the DM takes at that edge, which
    may see the held or the reset values. X-RISCV_DEBUG-sbbusy leaves that point open. No
    other DUT choice enters the model.

    Readings: RISC-V Debug 0.13.2 throughout, except that an sbdata0 read without
    sbreadondata leaves sbaddress unchanged, and the read an sbdata0 read starts increments
    sbaddress only when it succeeds, as Debug 1.0 reads both (S-TDR2AXI-0094). A write of
    sbaddress0,
    sbaddress1, sbdata0 or sbdata1, or a read of sbdata0 or sbdata1, while sbbusy or
    sbbusyerror is set collides (0.13.2 3.12.19, 3.12.20, 3.12.23, 3.12.24): the DM
    answers DMI busy, sets sbbusyerror and does nothing else. A read of sbaddress0 or
    sbaddress1 never collides. An sbcs write while an access is in flight breaks
    X-RISCV_DEBUG-sbbusy, and 0.13.2 3.12.18 leaves its effect undefined. With sbbusyerror
    already set the model answers it busy and changes no field: the DV reading of the RTL
    (dm_csrs.sv:512-514) that the R-RISCV_DEBUG profile records in its departure_busy
    entry; no spec text gives it. With sbautoincrement set, a failed access leaves sbaddress
    at the failing address, whether an sbaddress0 write, an sbdata0 write or an sbdata0
    read started it (S-TDR2AXI-0093, S-TDR2AXI-0094), and a failed read leaves sbdata
    unknown. No access starts while dmactive = 0, and one in
    flight when dmactive drops completes on the bus with its original payload and is held
    (S-TDR2AXI-0090): sbcs, sbaddress and sbdata keep their values until it completes,
    sbcs reading them with sbbusy = 1, while dmcontrol and abstractcs reset at once. A
    write of sbcs, sbaddress0, sbaddress1, sbdata0 or sbdata1 or a read of sbdata0 or
    sbdata1 during the hold changes nothing, sbbusyerror included, and its DMI answer is
    open, as Debug 1.0 lets any access fail while dmactive = 0 (the R-RISCV_DEBUG profile's
    dm_reset entry). The held access's completion drops its read data and error, and the
    SBA registers take their reset values. A dmactive = 1 write before that completion ends
    the hold, and the access completes as if dmactive had stayed set (S-TDR2AXI-0091);
    the model takes such a write as reaching the DM before the response, and S-TDR2AXI-0091
    leaves a write in the response's own cycle open. While dmactive = 0 with no access
    held, an sbcs read predicts sbbusy = 0 and sbbusyerror = 0, the other fields reading
    the values of the profile's departure_open entry. From a held access's completion to
    ``access_retired`` an sbcs read shows sbbusy and sbbusyerror both as held or both at
    their reset value 0, since the register takes its reset value whole. At
    AXI_ADDR_WIDTH <= 32 sbaddress1 does not exist: it reads 0 and a write of it changes
    nothing, but collides as the present register does (the profile's departure_absent
    entry). The model predicts no sbdata value after a bus write and no sbaddress bit at
    or above AXI_ADDR_WIDTH.

    ``SbaStimulusError`` marks stimulus the spec and those readings leave open: an sbcs
    write while an access is in flight and sbbusyerror is clear, whose effect on sbbusyerror
    no reading gives; an sbcs write of 1s to sberror that leaves a set bit set; an sbaddress
    bit at or above AXI_ADDR_WIDTH written to a register that exists; an access whose
    auto-increment would reach 2**AXI_ADDR_WIDTH; an access both misaligned and wider than
    the bus; and an access from an unknown sbaddress.

    ``negative`` inverts the lowest compared bit of every predicted value and bit 0 of every
    predicted bus address; the bench sets it from the ``NEGATIVE_KNOB`` variable.
    """

    def __init__(self, cfg: SbaModelCfg, *, negative: bool = False) -> None:
        self.cfg = cfg
        self._negative = negative
        self._memory: dict[int, int | None] = {}
        self._access: _Access | None = None
        self._phase = SbaPhase.IDLE
        self._busy_old = 0
        self._dmactive = False
        self._ndmreset = 0
        self._ndmreset_known = True
        self._cmderr = 0
        self._sbcs_rw = 0
        self._sberror = 0
        self._sbbusyerror = False
        self._sbaddress = 0
        self._sbaddress_known = _MASK64
        self._sbdata = 0
        self._sbdata_known = _MASK64
        self._reset_registers()
        self._before = self._snapshot()

    @property
    def phase(self) -> SbaPhase:
        """The engine phase."""
        return self._phase

    @property
    def dmactive(self) -> bool:
        """dmcontrol.dmactive."""
        return self._dmactive

    @property
    def held(self) -> bool:
        """Whether an access in flight when dmactive fell holds the SBA registers."""
        return self._access is not None and self._access.held

    def predict_dmi_read(self, addr: int) -> DmAnswer:
        """Predict the DM's answer to a DMI read of ``addr`` and apply its side effects.

        Serves CHK-DM-REGISTER-MAP, CHK-SBA-SINGLE-READ, CHK-SBA-BLOCK-READ,
        CHK-SBA-READ-NO-READONDATA, CHK-SBA-ERROR-BLOCKS, CHK-SBA-SBBUSYERROR,
        CHK-TDR-PIPELINED-READ, CHK-TDR-BUSY-DM, CHK-TDR-BUSY-RECOVERY,
        CHK-TDR-RATIO-INVARIANT and CHK-RESET-ASYNC-ANY-TIME. Atoms: S-TDR2AXI-0043,
        S-TDR2AXI-0062, S-TDR2AXI-0063, S-TDR2AXI-0077, S-TDR2AXI-0081,
        X-RISCV_DEBUG-dm-addresses, X-RISCV_DEBUG-sbdata0-read, X-RISCV_DEBUG-sbbusyerror.
        Also serves CHK-DM-DMACTIVE-INFLIGHT-HOLD: S-TDR2AXI-0090, X-RISCV_DEBUG-dmactive.

        The answer is busy, with free data, for an sbdata0 or sbdata1 read while an access
        is PENDING or RESPONDED or sbbusyerror is set; the read then sets sbbusyerror and
        starts nothing. While an access is held, such a read changes nothing and its answer
        is open (``open_status``). Any other read, an sbaddress read included, answers
        success with the register's predicted value. An sbdata0 read returns sbdata0, then
        with sbreadondata set and sberror clear starts a read at sbaddress.
        """
        _check_dmi(addr, 0)
        if addr in _SBA_DATA_REGS:
            return self._read_sba(addr)
        return self._answer(self._register(addr), None, _ATOMS_MAP)

    def predict_dmi_write(self, addr: int, data: int) -> DmAnswer:
        """Predict the DM's answer to a DMI write of ``data`` to ``addr`` and apply it.

        Serves CHK-DM-REGISTER-MAP, CHK-DM-CMDERR, CHK-DM-DMACTIVE-CLEAR,
        CHK-DM-DMACTIVE-NO-BUS, CHK-DM-NDMRESET, CHK-DM-HART-UNAVAIL, CHK-SBA-SINGLE-WRITE,
        CHK-SBA-SINGLE-READ, CHK-SBA-NO-READ-ON-ADDR, CHK-SBA-BLOCK-WRITE,
        CHK-SBA-SBDATA1-NO-BUS, CHK-SBA-ERROR-BLOCKS, CHK-SBA-MISALIGNED, CHK-SBA-OVERSIZE
        and CHK-SBA-SBBUSYERROR. Atoms: S-TDR2AXI-0010, S-TDR2AXI-0011, S-TDR2AXI-0043,
        S-TDR2AXI-0056, S-TDR2AXI-0060, S-TDR2AXI-0062, S-TDR2AXI-0064, S-TDR2AXI-0071,
        S-TDR2AXI-0076, S-TDR2AXI-0077, S-TDR2AXI-0079, X-RISCV_DEBUG-dmactive,
        X-RISCV_DEBUG-cmderr, X-RISCV_DEBUG-sbaddress0-read, X-RISCV_DEBUG-sbdata0-write,
        X-RISCV_DEBUG-sberror. Also serves CHK-DM-DMACTIVE-INFLIGHT-HOLD and
        CHK-DM-DMACTIVE-REARM: S-TDR2AXI-0090, S-TDR2AXI-0091. A write of the absent
        sbaddress1 follows S-TDR2AXI-0014 and X-RISCV_DEBUG-sbaddress-regs.

        The answer's ``access`` is the bus access the write starts: a write for sbdata0, a
        read for sbaddress0 with sbreadonaddr set, each only with dmactive set, the engine
        IDLE and sberror and sbbusyerror clear; an access misaligned or wider than the bus
        starts none and sets sberror. A write of sbaddress0, sbaddress1, sbdata0 or sbdata1
        while an access is in flight or sbbusyerror is set is answered busy, sets
        sbbusyerror and changes nothing else. An sbcs write while an access is in flight and
        sbbusyerror is set is answered busy and changes nothing, the RTL reading the class
        docstring records. While an access is held, a write of sbcs, sbaddress0,
        sbaddress1, sbdata0 or sbdata1 changes nothing and its answer is open
        (``open_status``). A dmcontrol write that clears dmactive holds the access in
        flight, and one that sets it during the hold ends the hold. At AXI_ADDR_WIDTH <= 32
        an sbaddress1 write that does not collide changes nothing. Every other write starts
        none and is answered success.
        """
        _check_dmi(addr, data)
        if addr == DMCONTROL:
            atoms = self._write_dmcontrol(data)
            return self._answer(Expected.free(atoms), None, atoms)
        if addr == SBCS or addr in _SBA_DATA_REGS:
            return self._write_sba(addr, data)
        if self._dmactive and addr == ABSTRACTCS:
            self._cmderr &= ~(data >> ABSTRACTCS_CMDERR_LSB) & CMDERR_FIELD
        elif self._dmactive and addr == COMMAND and self._cmderr == 0:
            self._cmderr = CMDERR_NOT_SUPPORTED
        return self._answer(Expected.free(_ATOMS_MAP), None, _ATOMS_MAP)

    def bus_response(self, *, error: bool, rdata: int = 0) -> None:
        """Apply the subordinate's response to the PENDING access.

        ``error`` is an SLVERR or DECERR response (S-TDR2AXI-0061) and ``rdata`` the whole
        R data word the subordinate drives; both are inputs to the DUT. A read loads the
        addressed lanes into sbdata, or leaves sbdata unknown on an error; a write commits
        its bytes to the memory image, or makes them unknown on an error, which AXI leaves
        open (X-AXI4-response-codes). A held access's response lands in no register: the
        SBA registers take their reset values (S-TDR2AXI-0090).
        """
        access = self._access
        if access is None or access.bus is None or self._phase is not SbaPhase.PENDING:
            raise SbaStimulusError("bus response with no bus access pending")
        if not 0 <= rdata < (1 << self.cfg.data_width):
            raise ValueError(f"R data 0x{rdata:x} is wider than {self.cfg.data_width} bits")
        if access.held:
            self._drop(access, error=error)
            return
        self._respond(SbError.BAD_ADDRESS if error else SbError.NONE, rdata)

    def access_retired(self) -> None:
        """Mark the RESPONDED access complete: sbbusy has fallen. The resynchronization point.

        A call in IDLE does nothing; a call in PENDING raises ``SbaStimulusError``.
        """
        if self._phase is SbaPhase.PENDING:
            raise SbaStimulusError("access retired before the subordinate answered it")
        self._retire()

    def load_memory(self, addr: int, data: bytes) -> None:
        """Record bytes the test writes into the subordinate's memory at bus address ``addr``."""
        if addr < 0 or addr + len(data) > (1 << self.cfg.addr_width):
            raise ValueError(f"{len(data)} bytes at 0x{addr:x} exceed AXI_ADDR_WIDTH")
        for offset, value in enumerate(data):
            self._memory[addr + offset] = value

    def predict_memory(self, addr: int, length: int) -> tuple[int | None, ...]:
        """Predict ``length`` subordinate memory bytes from bus address ``addr``.

        Serves CHK-SBA-WRITE-MEMORY, CHK-DM-NDMRESET and CHK-TDR-BUSY-RECOVERY. Atoms:
        S-TDR2AXI-0001, S-TDR2AXI-0011, S-TDR2AXI-0071, X-AXI4-response-codes.

        Exact per byte: the loaded or last written value, or None for a byte never loaded
        or written, or whose last write failed or met a reset before its response.
        """
        flip = 1 if self._negative else 0
        return tuple(
            None if (value := self._memory.get(addr + offset)) is None else value ^ flip
            for offset in range(length)
        )

    def predict_sbcs(self) -> Expected:
        """Predict an sbcs read without applying it.

        Serves CHK-SBA-SBCS-FIELDS, CHK-DM-VERSION, CHK-SBA-SBBUSY, CHK-SBA-SBBUSYERROR,
        CHK-SBA-BUS-ERROR, CHK-SBA-MISALIGNED, CHK-SBA-OVERSIZE and CHK-DM-DMACTIVE-CLEAR.
        Atoms: S-TDR2AXI-0002, S-TDR2AXI-0014, S-TDR2AXI-0015, S-TDR2AXI-0023,
        S-TDR2AXI-0060, S-TDR2AXI-0061, X-RISCV_DEBUG-sbcs-fields, X-RISCV_DEBUG-sbbusy,
        X-RISCV_DEBUG-sbbusyerror, X-RISCV_DEBUG-sberror, X-RISCV_DEBUG-dmactive. Also serves
        CHK-DM-DMACTIVE-INFLIGHT-HOLD and CHK-DM-DMACTIVE-REARM: S-TDR2AXI-0090,
        S-TDR2AXI-0091.

        Exact in IDLE and PENDING (sbbusy 0 and 1); in RESPONDED the set of sbbusy = 1 with
        the fields before or after the access's outcome, and sbbusy = 0 after it. While
        dmactive = 0: exact on the held value with sbbusy = 1 while an access is held; from
        its completion to ``access_retired`` sbbusy and sbbusyerror compared as a pair, the
        held pair (sbbusy 1 and the held sbbusyerror) or the reset pair (both 0); and
        otherwise sbbusy = 0 and sbbusyerror = 0, nothing else compared.
        """
        return self._emit(self._sbcs())

    def predict_sbaddress(self, index: int) -> Expected:
        """Predict an sbaddress0 (``index`` 0) or sbaddress1 (``index`` 1) read.

        Serves CHK-SBA-ADDRESS-WIDTH, CHK-SBA-BLOCK-WRITE, CHK-SBA-BLOCK-READ,
        CHK-SBA-NO-INC-AFTER-ERROR, CHK-SBA-READ-NO-READONDATA, CHK-DM-DMACTIVE-CLEAR and
        CHK-DM-NDMRESET. Atoms: S-TDR2AXI-0014, S-TDR2AXI-0022, S-TDR2AXI-0061,
        S-TDR2AXI-0079, S-TDR2AXI-0081, S-TDR2AXI-0093, S-TDR2AXI-0094,
        X-RISCV_DEBUG-sbaddress-regs, X-RISCV_DEBUG-sbautoincrement,
        X-RISCV_DEBUG-sbaddress0-read, X-RISCV_DEBUG-sbdata0-write,
        X-RISCV_DEBUG-sbdata0-read.

        Exact on the bits below AXI_ADDR_WIDTH; in RESPONDED the set of the values before
        and after the access's increment (X-RISCV_DEBUG-sbbusy). With sbautoincrement set,
        the increment follows only a successful access, so a failed one started by an
        sbaddress0 write, an sbdata0 write or an sbdata0 read leaves its own address. A held
        access keeps its address until its completion, and the register then reads 0
        (S-TDR2AXI-0090). sbaddress1 reads 0 when AXI_ADDR_WIDTH <= 32, where it does not
        exist (S-TDR2AXI-0014, X-RISCV_DEBUG-sbaddress-regs).
        """
        return self._emit(self._sbaddress_reg(index))

    def predict_sbdata(self, index: int) -> Expected:
        """Predict an sbdata0 (``index`` 0) or sbdata1 (``index`` 1) read value.

        Serves CHK-SBA-SINGLE-READ, CHK-SBA-DATA-LANES, CHK-SBA-BLOCK-READ,
        CHK-AXI-LITE-FULL-WIDTH, CHK-TDR-PIPELINED-READ and CHK-DM-DMACTIVE-CLEAR. Atoms:
        S-TDR2AXI-0015, S-TDR2AXI-0076, S-TDR2AXI-0077, S-TDR2AXI-0081,
        X-AXI4_LITE-full-width, X-RISCV_DEBUG-sbdata-lanes, X-RISCV_DEBUG-sbdata0-read.

        Exact on the bits the last successful read loaded from the addressed lanes of the R
        word (2**size bytes; the bits above are free) or the debugger last wrote; free after
        a bus write or a failed read. In RESPONDED, the set of the values before and after
        the access's outcome, compared on the bits known in both (X-RISCV_DEBUG-sbbusy). A
        held access's completion leaves 0, its read data dropped (S-TDR2AXI-0090). sbdata1
        compares nothing with 32-bit data, where it does not exist.
        """
        return self._emit(self._sbdata_reg(index))

    def predict_dmcontrol(self) -> Expected:
        """Predict a dmcontrol read.

        Serves CHK-DM-DMACTIVE-CLEAR, CHK-DM-NDMRESET and CHK-DM-REGISTER-MAP. Atoms:
        S-TDR2AXI-0011, S-TDR2AXI-0056, S-TDR2AXI-0064, X-RISCV_DEBUG-dmactive,
        X-RISCV_DEBUG-ndmreset.

        Exact on dmactive and ndmreset; ndmreset reads 0 while dmactive = 0 and is free
        after a write that sets dmactive and ndmreset together from dmactive = 0.
        """
        return self._emit(self._dmcontrol())

    def predict_dmstatus(self) -> Expected:
        """Predict a dmstatus read.

        Serves CHK-DM-VERSION and CHK-DM-HART-UNAVAIL. Atoms: S-TDR2AXI-0002,
        S-TDR2AXI-0009, S-TDR2AXI-0062, X-RISCV_DEBUG-dm-version,
        X-RISCV_DEBUG-hart-unavail, X-RISCV_DEBUG-authenticated, X-RISCV_DEBUG-authbusy.

        Exact while dmactive = 1: version 2; allunavail and anyunavail 1, after any write of
        haltreq, resumereq or hartsel; authenticated 1 and authbusy 0, after any DMI access,
        authdata (0x30) included. The DM implements no authentication, the reading of
        S-TDR2AXI-0002 that debuggers reach memory through it without custom tooling, so
        authenticated holds its preset of 1 and authbusy, set only by an authdata access
        the DM does not serve, stays 0. Free while dmactive = 0.
        """
        return self._emit(self._dmstatus())

    def predict_abstractcs(self) -> Expected:
        """Predict an abstractcs read.

        Serves CHK-DM-CMDERR and CHK-DM-DMACTIVE-CLEAR. Atoms: S-TDR2AXI-0010,
        X-RISCV_DEBUG-cmderr, X-RISCV_DEBUG-dmactive.

        Exact on cmderr: 2 after any command write, cleared bit by bit by writing ones, 0
        while dmactive = 0, an access held or not, and 0 after dmactive is set again.
        """
        return self._emit(self._abstractcs())

    def reset(self, scope: str) -> None:
        """Apply the reset entry ``scope`` of the clock-reset model.

        RST-ARST-BUS and RST-SCAN reset every DM register, dmactive included, and drop the
        access in flight, whose written bytes become unknown; the memory image is kept.
        RST-DMACTIVE is the dmactive = 0 clear of S-TDR2AXI-0056 and S-TDR2AXI-0064, the same
        as a dmcontrol write of 0. RST-ARST-TCK and RST-TRST leave the model untouched
        (S-TDR2AXI-0054).
        """
        if scope in _RESET_SCOPES_OTHER:
            return
        if scope == _RESET_SCOPE_DMACTIVE:
            self._write_dmcontrol(0)
            return
        if scope not in _RESET_SCOPES_DM:
            raise ValueError(f"unknown reset scope {scope!r}")
        access = self._access
        if (
            self._phase is SbaPhase.PENDING
            and access is not None
            and access.bus is not None
            and access.bus.direction is BusDir.WRITE
        ):
            self._commit_memory(access, ok=False)
        self._access = None
        self._phase = SbaPhase.IDLE
        self._reset_registers()

    def _reset_registers(self) -> None:
        self._reset_dm()
        self._reset_sba()

    def _reset_dm(self) -> None:
        self._dmactive = False
        self._ndmreset = 0
        self._ndmreset_known = True
        self._cmderr = 0

    def _reset_sba(self) -> None:
        self._sbcs_rw = SBACCESS_RESET << SBCS_SBACCESS_LSB
        self._sberror = SbError.NONE
        self._sbbusyerror = False
        self._sbaddress = 0
        self._sbaddress_known = _MASK64
        self._sbdata = 0
        self._sbdata_known = _MASK64

    def _answer(
        self, data: Expected, access: SbaBusAccess | None, atoms: tuple[str, ...]
    ) -> DmAnswer:
        if self._negative and access is not None:
            access = replace(access, addr=access.addr ^ 1)
        return DmAnswer(busy=False, data=self._emit(data), access=access, atoms=atoms)

    def _emit(self, expected: Expected) -> Expected:
        return expected.flipped() if self._negative else expected

    def _open(self) -> DmAnswer:
        data = Expected.free(_ATOMS_HOLD)
        return DmAnswer(busy=False, data=data, access=None, atoms=_ATOMS_HOLD, open_status=True)

    def _read_sba(self, addr: int) -> DmAnswer:
        if addr in _SBADDRESS_REGS:
            atoms = _ATOMS_MAP if self._dmactive else _ATOMS_DMACTIVE
            return self._answer(self._register(addr), None, atoms)
        if self.held:
            return self._open()
        if not self._dmactive:
            return self._answer(self._register(addr), None, _ATOMS_DMACTIVE)
        if self._phase is not SbaPhase.IDLE or self._sbbusyerror:
            return self._collide(addr, written=False)
        if addr == SBDATA1:
            return self._answer(self._register(addr), None, _ATOMS_MAP)
        if self._sberror:
            return self._answer(Expected.free(_ATOMS_ACCESS), None, _ATOMS_ACCESS)
        data = self._register(addr)
        access = None
        if self._sbcs_rw & SBCS_SBREADONDATA:
            access = self._start(_Trigger.SBDATA0_READ)
        return self._answer(data, access, _ATOMS_ACCESS)

    def _write_sba(self, addr: int, data: int) -> DmAnswer:
        if self.held:
            return self._open()
        absent = addr == SBADDRESS1 and self.cfg.addr_width <= 32
        if addr in _SBADDRESS_REGS and not absent:
            self._check_address(addr, data)
        if not self._dmactive:
            return self._answer(Expected.free(_ATOMS_DMACTIVE), None, _ATOMS_DMACTIVE)
        if addr == SBCS and self._phase is not SbaPhase.IDLE:
            return self._refuse_sbcs()
        if addr == SBCS:
            self._write_sbcs(data)
            return self._answer(Expected.free(_ATOMS_SBCS), None, _ATOMS_SBCS)
        if self._phase is not SbaPhase.IDLE or self._sbbusyerror:
            return self._collide(addr, written=not absent)
        if absent:
            return self._answer(Expected.free(_ATOMS_ABSENT), None, _ATOMS_ABSENT)
        self._store(addr, data)
        access = None
        if addr == SBADDRESS0 and not self._sberror and self._sbcs_rw & SBCS_SBREADONADDR:
            access = self._start(_Trigger.SBADDRESS0_WRITE)
        elif addr == SBDATA0 and not self._sberror:
            access = self._start(_Trigger.SBDATA0_WRITE)
        elif addr == SBDATA0:
            self._forget(SBDATA0)
        return self._answer(Expected.free(_ATOMS_ACCESS), access, _ATOMS_ACCESS)

    def _refuse_sbcs(self) -> DmAnswer:
        if not self._sbbusyerror:
            raise SbaStimulusError(
                "sbcs written while an access is in flight and sbbusyerror is clear, which "
                "X-RISCV_DEBUG-sbbusy forbids: the R-RISCV_DEBUG profile's departure_busy entry "
                "records the DV reading of the RTL only for a write with sbbusyerror set, and no "
                "spec text gives this write's effect on sbbusyerror"
            )
        return DmAnswer(
            busy=True,
            data=Expected.free(_ATOMS_SBCS_BUSY),
            access=None,
            atoms=_ATOMS_SBCS_BUSY,
        )

    def _write_sbcs(self, data: int) -> None:
        cleared = (data >> SBCS_SBERROR_LSB) & SBCS_SBERROR_FIELD
        if cleared and cleared & self._sberror != self._sberror:
            raise SbaStimulusError(
                f"sberror write of 0b{cleared:03b} leaves a bit of 0b{self._sberror:03b} set: "
                "the model predicts only a write that clears every set bit"
            )
        if data & SBCS_SBBUSYERROR:
            self._sbbusyerror = False
        if cleared:
            self._sberror = SbError.NONE
        self._sbcs_rw = data & _SBCS_RW

    def _check_address(self, addr: int, data: int) -> None:
        value = data << (32 if addr == SBADDRESS1 else 0)
        if value & ~self.cfg.addr_mask:
            raise SbaStimulusError(
                f"sbaddress write 0x{data:08x} to 0x{addr:02x} sets a bit at or above "
                f"AXI_ADDR_WIDTH = {self.cfg.addr_width}"
            )

    def _write_dmcontrol(self, data: int) -> tuple[str, ...]:
        """Apply a dmcontrol write and return the atoms of its answer."""
        if not data & DMCONTROL_DMACTIVE:
            if not self._dmactive:
                return _ATOMS_DMCONTROL
            self._retire()
            self._reset_dm()
            if self._access is None:
                self._reset_sba()
                return _ATOMS_DMCONTROL
            self._access = replace(self._access, held=True)
            return _ATOMS_DMCONTROL + _ATOMS_HOLD
        if self._dmactive:
            self._ndmreset = data & DMCONTROL_NDMRESET
            self._ndmreset_known = True
            return _ATOMS_DMCONTROL
        rearmed = self.held
        self._release_hold()
        self._dmactive = True
        self._ndmreset_known = not data & DMCONTROL_NDMRESET
        return _ATOMS_DMCONTROL + _ATOMS_REARM if rearmed else _ATOMS_DMCONTROL

    def _release_hold(self) -> None:
        """dmactive set during a hold: an access awaiting its response completes as
        with dmactive set throughout, and one already dropped is complete."""
        access = self._access
        if access is None or not access.held:
            return
        if self._phase is SbaPhase.RESPONDED:
            self._retire()
            return
        self._access = replace(access, held=False)

    def _collide(self, addr: int, *, written: bool) -> DmAnswer:
        if written and self._phase is SbaPhase.IDLE:
            self._forget(addr)
        self._sbbusyerror = True
        self._busy_old |= SBCS_SBBUSYERROR
        return DmAnswer(busy=True, data=Expected.free(_ATOMS_BUSY), access=None, atoms=_ATOMS_BUSY)

    def _store(self, addr: int, data: int) -> None:
        field = WORD_MASK << (32 if addr in (SBADDRESS1, SBDATA1) else 0)
        value = data << (32 if addr in (SBADDRESS1, SBDATA1) else 0)
        if addr in (SBADDRESS0, SBADDRESS1):
            self._sbaddress = (self._sbaddress & ~field) | value
            self._sbaddress_known |= field
        else:
            self._sbdata = (self._sbdata & ~field) | value
            self._sbdata_known |= field

    def _forget(self, addr: int) -> None:
        field = WORD_MASK << (32 if addr in (SBADDRESS1, SBDATA1) else 0)
        if addr in (SBADDRESS0, SBADDRESS1):
            self._sbaddress_known &= ~field
        else:
            self._sbdata_known &= ~field

    def _start(self, trigger: _Trigger) -> SbaBusAccess | None:
        size = (self._sbcs_rw >> SBCS_SBACCESS_LSB) & SBCS_SBACCESS_FIELD
        nbytes = 1 << size
        if self._sbaddress_known & self.cfg.addr_mask != self.cfg.addr_mask:
            raise SbaStimulusError("access started from an sbaddress the model does not know")
        misaligned = self._sbaddress % nbytes != 0
        oversize = 8 * nbytes > self.cfg.data_width
        if misaligned and oversize:
            raise SbaStimulusError(
                "access both misaligned and wider than the bus: S-TDR2AXI-0060 gives two codes"
            )
        steps = not (misaligned or oversize)
        autoinc = self._sbcs_rw & SBCS_SBAUTOINCREMENT
        if autoinc and steps and self._sbaddress + nbytes > self.cfg.addr_mask:
            raise SbaStimulusError(
                f"auto-increment from 0x{self._sbaddress:x} reaches 2**AXI_ADDR_WIDTH: "
                "the model predicts no address wrap"
            )
        access = _Access(trigger, self._sbaddress, size, self._sbdata, self._sbdata_known)
        write = trigger is _Trigger.SBDATA0_WRITE
        if write:
            self._sbdata_known = 0
        self._access = access
        if misaligned or oversize:
            self._respond(SbError.ALIGNMENT if misaligned else SbError.SIZE, 0)
            return None
        direction = BusDir.WRITE if write else BusDir.READ
        bus = predict_bus_access(
            self.cfg, direction, access.addr, size, data=access.data, data_known=access.data_known
        )
        self._access = replace(access, bus=bus)
        self._phase = SbaPhase.PENDING
        return bus

    def _respond(self, error: SbError, rdata: int) -> None:
        access = self._access
        if access is None:
            raise SbaStimulusError("no access to complete")
        self._busy_old = self._sbcs_value(busy=True)
        self._before = self._snapshot()
        if access.trigger is _Trigger.SBDATA0_WRITE:
            if access.bus is not None:
                self._commit_memory(access, ok=not error)
        elif error:
            self._sbdata_known = 0
        else:
            shift = 8 * (access.addr % self.cfg.bus_bytes)
            width = (1 << (8 << access.size)) - 1
            self._sbdata = (rdata >> shift) & width
            self._sbdata_known = width
        if error:
            self._sberror = error
        steps = not error
        if self._sbcs_rw & SBCS_SBAUTOINCREMENT and steps:
            self._sbaddress += 1 << access.size
        self._phase = SbaPhase.RESPONDED

    def _drop(self, access: _Access, *, error: bool) -> None:
        self._busy_old = self._sbcs_value(busy=True)
        self._before = self._snapshot()
        if access.bus is not None and access.bus.direction is BusDir.WRITE:
            self._commit_memory(access, ok=not error)
        self._reset_sba()
        self._phase = SbaPhase.RESPONDED

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(self._sbaddress, self._sbaddress_known, self._sbdata, self._sbdata_known)

    def _outcomes(
        self, value: int, known: int, before: int, before_known: int
    ) -> tuple[tuple[int, ...], int]:
        if self._phase is not SbaPhase.RESPONDED:
            return (value,), known
        return (value, before), known & before_known

    def _word(
        self, values: tuple[int, ...], mask: int, shift: int, atoms: tuple[str, ...]
    ) -> Expected:
        words = tuple(dict.fromkeys((value >> shift) & mask for value in values))
        if self._phase is SbaPhase.RESPONDED:
            atoms = atoms + _ATOMS_OPEN
        return Expected(words, mask, atoms)

    def _retire(self) -> None:
        if self._phase is SbaPhase.RESPONDED:
            self._phase = SbaPhase.IDLE
            self._access = None

    def _commit_memory(self, access: _Access, *, ok: bool) -> None:
        base = access.addr & self.cfg.addr_mask
        for lane in range(1 << access.size):
            known = (access.data_known >> (8 * lane)) & 0xFF == 0xFF
            value = (access.data >> (8 * lane)) & 0xFF
            self._memory[base + lane] = value if ok and known else None

    def _register(self, addr: int) -> Expected:
        readers = {
            DMCONTROL: self._dmcontrol,
            DMSTATUS: self._dmstatus,
            ABSTRACTCS: self._abstractcs,
            SBCS: self._sbcs,
            SBADDRESS0: lambda: self._sbaddress_reg(0),
            SBADDRESS1: lambda: self._sbaddress_reg(1),
            SBDATA0: lambda: self._sbdata_reg(0),
            SBDATA1: lambda: self._sbdata_reg(1),
        }
        reader = readers.get(addr)
        return Expected.free(_ATOMS_MAP) if reader is None else reader()

    def _sbcs_value(self, *, busy: bool) -> int:
        access_bits = SBCS_SBACCESS8_16_32 | (SBCS_SBACCESS64 if self.cfg.data_width == 64 else 0)
        value = (
            (SBVERSION_1 << SBCS_SBVERSION_LSB)
            | self._sbcs_rw
            | (self._sberror << SBCS_SBERROR_LSB)
            | (self.cfg.addr_width << SBCS_SBASIZE_LSB)
            | access_bits
        )
        if self._sbbusyerror:
            value |= SBCS_SBBUSYERROR
        return value | (SBCS_SBBUSY if busy else 0)

    def _sbcs(self) -> Expected:
        if not self._dmactive:
            return self._sbcs_inactive()
        if self._phase is SbaPhase.IDLE:
            return Expected.exact(self._sbcs_value(busy=False), SBCS_DEFINED, _ATOMS_SBCS)
        if self._phase is SbaPhase.PENDING:
            return Expected.exact(self._sbcs_value(busy=True), SBCS_DEFINED, _ATOMS_SBCS)
        allowed = (self._busy_old, self._sbcs_value(busy=True), self._sbcs_value(busy=False))
        return Expected(tuple(dict.fromkeys(allowed)), SBCS_DEFINED, _ATOMS_SBCS)

    def _sbcs_inactive(self) -> Expected:
        if not self.held:
            return Expected.exact(0, _SBCS_BUSY_PAIR, _ATOMS_DMACTIVE)
        if self._phase is SbaPhase.PENDING:
            return Expected.exact(self._sbcs_value(busy=True), SBCS_DEFINED, _ATOMS_HOLD)
        held = self._busy_old & _SBCS_BUSY_PAIR
        return Expected((held, 0), _SBCS_BUSY_PAIR, _ATOMS_HOLD + _ATOMS_OPEN)

    def _sbaddress_reg(self, index: int) -> Expected:
        shift = _word_shift(index)
        if index == 1 and self.cfg.addr_width <= 32:
            return Expected.exact(0, WORD_MASK, _ATOMS_ABSENT)
        before = self._before
        values, known = self._outcomes(
            self._sbaddress, self._sbaddress_known, before.sbaddress, before.sbaddress_known
        )
        mask = ((self.cfg.addr_mask & known) >> shift) & WORD_MASK
        return self._word(values, mask, shift, self._hold_atoms(_ATOMS_SBADDRESS))

    def _sbdata_reg(self, index: int) -> Expected:
        shift = _word_shift(index)
        if index == 1 and self.cfg.data_width == 32:
            return Expected.free(_ATOMS_SBDATA)
        before = self._before
        values, known = self._outcomes(
            self._sbdata, self._sbdata_known, before.sbdata, before.sbdata_known
        )
        mask = (known >> shift) & WORD_MASK
        return self._word(values, mask, shift, self._hold_atoms(_ATOMS_SBDATA))

    def _hold_atoms(self, atoms: tuple[str, ...]) -> tuple[str, ...]:
        return atoms + _ATOMS_HOLD if self.held else atoms

    def _dmcontrol(self) -> Expected:
        if not self._dmactive:
            return Expected.exact(0, DMCONTROL_DMACTIVE | DMCONTROL_NDMRESET, _ATOMS_DMCONTROL)
        mask = DMCONTROL_DMACTIVE | (DMCONTROL_NDMRESET if self._ndmreset_known else 0)
        return Expected.exact(DMCONTROL_DMACTIVE | self._ndmreset, mask, _ATOMS_DMCONTROL)

    def _dmstatus(self) -> Expected:
        if not self._dmactive:
            return Expected.free(_ATOMS_DMACTIVE)
        unavail = DMSTATUS_ANYUNAVAIL | DMSTATUS_ALLUNAVAIL
        value = DMSTATUS_VERSION_013 | unavail | DMSTATUS_AUTHENTICATED
        mask = DMSTATUS_VERSION_MASK | unavail | DMSTATUS_AUTHENTICATED | DMSTATUS_AUTHBUSY
        return Expected.exact(value, mask, _ATOMS_DMSTATUS)

    def _abstractcs(self) -> Expected:
        mask = CMDERR_FIELD << ABSTRACTCS_CMDERR_LSB
        return Expected.exact(self._cmderr << ABSTRACTCS_CMDERR_LSB, mask, _ATOMS_ABSTRACTCS)


def _word_shift(index: int) -> int:
    if index not in (0, 1):
        raise ValueError(f"register index must be 0 or 1, got {index}")
    return 32 * index
