# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi scoreboard: what the DUT did against what the reference models predicted.

``Tdr2axiScoreboard`` compares four streams. Expected items come only from the models:
each update edge's DMI request and each capture image of ``TdrStatusModel``, through
``Tdr2axiTdrModelTap``, and each DM answer of ``SbaRefModel`` with the bus access it
starts, as ``Tdr2axiPredictor.dm_answers`` publishes them. Observed items come from the
bench: every DMI request and response handshake of the DM, every R and B response code,
every AR, AW and W handshake of ``AxiSub``, and ``o_dmi_tdr`` at each capture edge. The
scoreboard reads no DUT signal and predicts nothing; its matching rules and the contexts
that pick a check ID come from the observed stimulus.

Comparisons, by check ID, plan comparison kind and stream:

* DMI requests, in order (``write_update``, ``write_dmi_request``): CHK-TDR-OP-DECODE
  compares op, address and data of each predicted request; requests of an update strobe
  held over consecutive TCK edges grade CHK-TDR-UPDATE-EDGE instead. A request the model
  marks optional may be skipped once a later one pairs.
* DM answers, in order (``write_dm_answer``, ``write_dmi_response``): the response code
  and the read data on the model's mask. sbcs bit 21 grades CHK-SBA-SBBUSY (state) against
  the sbbusy window the answer carries: a value whose other fields match a predicted value
  but which matches no predicted value whole fails CHK-SBA-SBBUSY. When the window allows
  the sbbusy read, the other fields and field checks grade against the predicted values
  with that sbbusy, so a value that a wrong sbbusy or a missing sberror explains equally
  fails both CHK-SBA-SBBUSY and the sberror check. The rest of the answer grades, first
  match wins: CHK-SBA-SBBUSYERROR (state) for a predicted busy
  answer, which also takes a busy answer the model does not predict; CHK-DM-DMACTIVE-CLEAR
  (state) for reads of sbaddress0, sbaddress1, sbdata0, sbdata1 and abstractcs while
  dmactive = 0 and the first sbcs read after it is set again; CHK-DM-OP3-KEEPS-DM (state)
  for reads after an op 3 update until the next DMI write; CHK-SBA-NO-INC-AFTER-ERROR
  (state) for sbaddress reads after an SLVERR or DECERR to an access an sbaddress0 or
  sbdata0 write started with sbautoincrement set, until the next access;
  CHK-SBA-ADDRESS-WIDTH for other sbaddress reads; for other sbdata reads, CHK-SBA-BLOCK-READ
  when the read starts a bus read, else the check of the bus read whose bytes sbdata holds,
  CHK-SBA-SINGLE-READ or CHK-SBA-BLOCK-READ, each with CHK-SBA-DATA-LANES for the lanes, and
  CHK-SBA-DATA-LANES alone for bytes an sbdata write puts in sbdata;
  CHK-TDR-RATIO-INVARIANT, the result every clock ratio must share, for the rest.
* Bus transactions, in order (``write_bus``): each predicted AR, or AW with its W, is seen
  once and nothing else is. The whole transaction grades the check of the request that
  started it: CHK-SBA-SINGLE-READ for an sbaddress0 write, CHK-SBA-SINGLE-WRITE or, with
  sbautoincrement set, CHK-SBA-BLOCK-WRITE for an sbdata0 write, CHK-SBA-BLOCK-READ for an
  sbdata0 read. AxADDR also grades CHK-SBA-ADDRESS-WIDTH, AxSIZE and WSTRB on AXI4
  CHK-AXI-AXI4-SIZE, WSTRB on AXI4-Lite CHK-AXI-LITE-FULL-WIDTH, and WDATA on the model's
  mask CHK-SBA-DATA-LANES. A lost, repeated or unpredicted transaction fails every check
  its fields carry.
* Capture status, one image per capture edge (``write_capture_expect``,
  ``write_tck_edge``), first match wins: CHK-TDR-UPDATE-EDGE at the first capture after a
  strobe held over several edges, CHK-TDR-BUSY-CAPTURE (state) at a capture with a read
  pending and at every capture after it until op 3, and CHK-DM-OP3-KEEPS-DM at the first
  capture after op 3. An X or Z capture is CHK-TDR-CAPTURE-EDGE's failure in ``TdrHost``
  and is compared here on nothing.

Further comparisons on the same streams, each beside the first-match check above:

* DM answers, state: dmstatus reads grade CHK-DM-VERSION on version, authenticated and
  authbusy, CHK-DM-HART-UNAVAIL on allunavail and anyunavail and CHK-DM-REGISTER-MAP on
  every predicted bit, as dmcontrol reads do. With dmactive = 1: sbaddress, sbdata and
  abstractcs reads grade CHK-DM-REGISTER-MAP on every predicted bit and sbcs reads on every
  predicted bit but sbbusy, except in the read contexts of CHK-DM-DMACTIVE-CLEAR,
  CHK-DM-OP3-KEEPS-DM and CHK-SBA-NO-INC-AFTER-ERROR; abstractcs reads grade CHK-DM-CMDERR
  on cmderr; and sbcs reads grade CHK-DM-VERSION on sbversion, CHK-SBA-SBCS-FIELDS on
  sbversion, sbasize and the sbaccess sizes (and sbaccess until sbcs is written after
  reset), and on sberror the check of the error the model predicts: CHK-SBA-BUS-ERROR for
  2, CHK-SBA-MISALIGNED for 3, CHK-SBA-OVERSIZE for 4.
  A field check failure names the bits that differ from the nearest predicted value.
  Whole answers grade CHK-DM-NDMRESET for sbaddress and sbcs reads after an ndmreset
  write until the next SBA register write, CHK-RESET-ASYNC-ANY-TIME for reads after an
  i_arst_n or i_arst_scan_n reset, the bring-up reset included, until dmactive is set,
  CHK-RESET-TRST-INDEPENDENT for reads after a TAP reset until the next sbcs write, and
  CHK-TDR-BUSY-RECOVERY for the answer to a resend.
* Capture images, against every image the model allows, on the fields each check names:
  CHK-TDR-CAPTURE-IMAGE (in-order) address, data and status at every capture the model
  predicts with status 0; CHK-TDR-PIPELINED-READ (in-order) address and data at the
  first such capture after a read is taken; CHK-TDR-OP3-CLEAR (state) status at the first
  capture after op 3; CHK-TDR-BUSY-UPDATE (state) status at a capture with a write
  pending and at every capture from an update refused while an operation is pending to
  op 3; CHK-TDR-STICKY-IGNORE (state) status at every capture from an update made with
  the sticky busy set to op 3; CHK-TDR-BUSY-DM (state) every field from a busy DM answer
  to op 3; CHK-TDR-BUSY-RECOVERY (in-order) every field at the first capture after the
  resend, the first operation taken after an op 3 that cleared a busy status;
  CHK-RESET-ASYNC-ANY-TIME every field at the first capture after a TCK-side reset, the
  bring-up reset included;
  CHK-RESET-TRST-INDEPENDENT every field from a TAP reset to the first capture with status
  0; CHK-DM-NDMRESET status while its context lasts; the watched result's check every
  field at the first capture after a read is taken; CHK-TDR-TCK-STOP-HOLD every field at
  the first capture after a TCK stop. A capture's data is not compared while the read
  answer it holds failed its own check, which already reported it.
* Requests: one nothing predicted fails CHK-TDR-OP3-CLEAR after op 3,
  CHK-TDR-BUSY-UPDATE after a refused update and CHK-TDR-STICKY-IGNORE after an update
  ignored under the sticky busy, beside its own check; a resend's request also grades
  CHK-TDR-BUSY-RECOVERY.
* Stale accesses: an i_arst_n reset (RST-ARST-BUS) opens a context that lasts until the
  first access predicted after it pairs, and that access grades CHK-RESET-NO-STALE-AXI,
  a checker-kind check outside ``SCOREBOARD_CHECKS``. While the context lasts, a bus
  transaction nothing predicts, or only a dropped prediction matches, fails it at its
  handshake, as does a DMI request or DM response only a dropped prediction matches, a
  DMI request that repeats an operation the reset cut off, and a capture the predicted
  image does not allow that holds the address, and for a write the data, of such an
  operation. With no access after the reset, the context lasts to ``drain``.
* Bus transactions: one nothing predicted also fails the context it falls in:
  CHK-DM-CMDERR after a command write, CHK-SBA-MISALIGNED or CHK-SBA-OVERSIZE after an
  access the observed sbcs makes wider than the bus or whose address is misaligned (the
  value of the sbaddress0 write that starts it, else sbaddress0 as written and advanced
  by each predicted access with sbautoincrement),
  CHK-SBA-BUS-ERROR after an error response, CHK-TDR-BUSY-RECOVERY after a resent
  access, each until the next predicted access, and CHK-TDR-STICKY-IGNORE,
  CHK-TDR-BUSY-UPDATE, CHK-DM-NDMRESET, CHK-TDR-TCK-STOP-HOLD and
  CHK-RESET-NO-STALE-AXI while their contexts last. A predicted transaction also grades CHK-SBA-BUS-ERROR when its response is an
  error, CHK-TDR-BUSY-RECOVERY when a resend started it, CHK-TDR-BUSY-UPDATE while a
  refused update's context lasts, CHK-RESET-TRST-INDEPENDENT when it is queued or in
  flight at a TAP reset, and CHK-TDR-TCK-STOP-HOLD when it falls in a TCK stop's round.
* Memory (``write_memory``, memory): each byte of the subordinate against the model's
  image, under the check the bench names: CHK-SBA-WRITE-MEMORY, CHK-DM-NDMRESET or
  CHK-RESET-TRST-INDEPENDENT. A byte the model leaves unknown is not compared.
* Results (``watch_results``, ``write_result_arrival``, in-order): while a result check
  is watched, each DM response predicts one result: the request's address, the predicted
  read data or the write data, and the model's window in TCK edges after the response
  handshake. Each arrival the timing monitor reports pairs with the oldest, under the
  watched check: CHK-TDR-RESULT-LATENCY, which applies at SYNC_STAGES 3 only and is
  watched only there, or CHK-TDR-TCK-STOP-HOLD.
* Hold (``write_hold_start``, ``write_hold_sample``, ``write_hold_end``, state): with TCK
  stopped, TCK-side state is static (S-TDR2AXI-0029, X-IEEE1149_1-tck-static), so the
  bridge and o_dmi_tdr sampled at each i_clk edge of the stop must equal their values at
  its start; each stop grades CHK-TDR-TCK-STOP-HOLD once. Every comparison this
  docstring files under CHK-TDR-TCK-STOP-HOLD is made under CHK-TDR-TCK-STOP-HOLD-SS4
  instead on a SYNC_STAGES 4 build, where that check applies (``tck_stop_check``).
* Events (``write_event_expect``, ``write_event``, in-order): each measured count pairs
  with the oldest window of its check; CHK-RESET-SYNC-RELEASE, which applies at
  SYNC_STAGES 3 only and is fed only there, takes the release counts, the assertion step
  and the hold of a side whose clock is stopped.

Only the result and event windows are cycle windows, taken from the model's timing
items; the sbbusy window is the order of the requests and the bus response the DM saw.

Resets, per the clock-reset model: RST-ARST-BUS and RST-SCAN drop the DM answers, bus
accesses, results, events and DMI requests in flight; RST-ARST-TCK drops the DMI requests
in flight and the capture contexts; RST-DMACTIVE drops only the DM register contexts,
since an access in flight completes; RST-TRST drops nothing and opens its own context
until ``end_trst``, and any other reset inside it fails CHK-RESET-TRST-INDEPENDENT, since
the operation the TAP reset leaves in place then never shows. An item seen after a reset
that only a dropped prediction matches fails that prediction's checks, and after an
i_arst_n reset CHK-RESET-NO-STALE-AXI too. ``drain`` fails
every prediction left unobserved. Every comparison reports to the bench's ``Reporter``
under one check ID, with the transaction in the detail; a test that requires a check and gives it no comparison
fails through CHK-NONVAC.

``Tdr2axiTdrModelTap`` is ``TdrStatusModel`` writing each update prediction and capture
image to the scoreboard, its one consumer, beside which it lives. The module imports
nothing from cocotb, so its self-test runs in plain Python. There is no SV twin: the bench
has one cocotb realization.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from env.sba_ref_model import (
    ABSTRACTCS,
    COMMAND,
    DMCONTROL,
    DMSTATUS,
    SBADDRESS0,
    SBADDRESS1,
    SBCS,
    SBDATA0,
    SBDATA1,
    BusDir,
    DmAnswer,
    Expected,
    SbaBusAccess,
)
from env.tdr_status_model import (
    BUSY_MARKER,
    DmiRequest,
    TdrCaptureExpect,
    TdrOp,
    TdrResultWindow,
    TdrStatus,
    TdrStatusModel,
    TdrUpdateExpect,
    decode_tdr,
)

if TYPE_CHECKING:
    from env.axi_sub import AxiAddr, AxiW
    from env.tdr_host import Reporter

__all__ = [
    "ADDRESS_WIDTH",
    "ASYNC_ANY_TIME",
    "AXI4_SIZE",
    "BLOCK_READ",
    "BLOCK_WRITE",
    "BUSY_CAPTURE",
    "BUSY_DM",
    "BUSY_RECOVERY",
    "BUSY_UPDATE",
    "BUS_ERROR",
    "CAPTURE_IMAGE",
    "CMDERR",
    "DATA_LANES",
    "DMACTIVE_CLEAR",
    "HART_UNAVAIL",
    "LITE_FULL_WIDTH",
    "MISALIGNED",
    "NDMRESET",
    "NO_INC_AFTER_ERROR",
    "NO_STALE_AXI",
    "OP3_CLEAR",
    "OP3_KEEPS_DM",
    "OP_DECODE",
    "OVERSIZE",
    "PIPELINED_READ",
    "RATIO_INVARIANT",
    "REGISTER_MAP",
    "RESET_SCOPES",
    "RESULT_LATENCY",
    "SBBUSY",
    "SBBUSYERROR",
    "SBCS_FIELDS",
    "SCOREBOARD_CHECKS",
    "SINGLE_READ",
    "SINGLE_WRITE",
    "STICKY_IGNORE",
    "SYNC_RELEASE",
    "TCK_STOP_HOLD",
    "TCK_STOP_HOLD_SS4",
    "TRST_INDEPENDENT",
    "UPDATE_EDGE",
    "VERSION",
    "WRITE_MEMORY",
    "ScoreboardSyncError",
    "Tdr2axiScoreboard",
    "Tdr2axiScoreboardCfg",
    "Tdr2axiTdrModelTap",
]

OP_DECODE = "CHK-TDR-OP-DECODE"
UPDATE_EDGE = "CHK-TDR-UPDATE-EDGE"
BUSY_CAPTURE = "CHK-TDR-BUSY-CAPTURE"
RATIO_INVARIANT = "CHK-TDR-RATIO-INVARIANT"
SBBUSY = "CHK-SBA-SBBUSY"
SBBUSYERROR = "CHK-SBA-SBBUSYERROR"
DATA_LANES = "CHK-SBA-DATA-LANES"
ADDRESS_WIDTH = "CHK-SBA-ADDRESS-WIDTH"
NO_INC_AFTER_ERROR = "CHK-SBA-NO-INC-AFTER-ERROR"
SINGLE_READ = "CHK-SBA-SINGLE-READ"
SINGLE_WRITE = "CHK-SBA-SINGLE-WRITE"
BLOCK_READ = "CHK-SBA-BLOCK-READ"
BLOCK_WRITE = "CHK-SBA-BLOCK-WRITE"
AXI4_SIZE = "CHK-AXI-AXI4-SIZE"
LITE_FULL_WIDTH = "CHK-AXI-LITE-FULL-WIDTH"
DMACTIVE_CLEAR = "CHK-DM-DMACTIVE-CLEAR"
OP3_KEEPS_DM = "CHK-DM-OP3-KEEPS-DM"
CMDERR = "CHK-DM-CMDERR"
HART_UNAVAIL = "CHK-DM-HART-UNAVAIL"
NDMRESET = "CHK-DM-NDMRESET"
REGISTER_MAP = "CHK-DM-REGISTER-MAP"
VERSION = "CHK-DM-VERSION"
ASYNC_ANY_TIME = "CHK-RESET-ASYNC-ANY-TIME"
SYNC_RELEASE = "CHK-RESET-SYNC-RELEASE"
TRST_INDEPENDENT = "CHK-RESET-TRST-INDEPENDENT"
BUS_ERROR = "CHK-SBA-BUS-ERROR"
MISALIGNED = "CHK-SBA-MISALIGNED"
OVERSIZE = "CHK-SBA-OVERSIZE"
SBCS_FIELDS = "CHK-SBA-SBCS-FIELDS"
WRITE_MEMORY = "CHK-SBA-WRITE-MEMORY"
BUSY_DM = "CHK-TDR-BUSY-DM"
BUSY_RECOVERY = "CHK-TDR-BUSY-RECOVERY"
BUSY_UPDATE = "CHK-TDR-BUSY-UPDATE"
CAPTURE_IMAGE = "CHK-TDR-CAPTURE-IMAGE"
OP3_CLEAR = "CHK-TDR-OP3-CLEAR"
PIPELINED_READ = "CHK-TDR-PIPELINED-READ"
RESULT_LATENCY = "CHK-TDR-RESULT-LATENCY"
STICKY_IGNORE = "CHK-TDR-STICKY-IGNORE"
TCK_STOP_HOLD = "CHK-TDR-TCK-STOP-HOLD"
TCK_STOP_HOLD_SS4 = "CHK-TDR-TCK-STOP-HOLD-SS4"
# The TCK-stop check of each SYNC_STAGES, which applies only at its own count
TCK_STOP_HOLD_IDS = {3: TCK_STOP_HOLD, 4: TCK_STOP_HOLD_SS4}
# A checker-kind check, outside SCOREBOARD_CHECKS, that the reset context below also files
NO_STALE_AXI = "CHK-RESET-NO-STALE-AXI"

SCOREBOARD_CHECKS = frozenset(
    {
        OP_DECODE,
        UPDATE_EDGE,
        BUSY_CAPTURE,
        RATIO_INVARIANT,
        SBBUSY,
        SBBUSYERROR,
        DATA_LANES,
        ADDRESS_WIDTH,
        NO_INC_AFTER_ERROR,
        SINGLE_READ,
        SINGLE_WRITE,
        BLOCK_READ,
        BLOCK_WRITE,
        AXI4_SIZE,
        LITE_FULL_WIDTH,
        DMACTIVE_CLEAR,
        OP3_KEEPS_DM,
        CMDERR,
        HART_UNAVAIL,
        NDMRESET,
        REGISTER_MAP,
        VERSION,
        ASYNC_ANY_TIME,
        SYNC_RELEASE,
        TRST_INDEPENDENT,
        BUS_ERROR,
        MISALIGNED,
        OVERSIZE,
        SBCS_FIELDS,
        WRITE_MEMORY,
        BUSY_DM,
        BUSY_RECOVERY,
        BUSY_UPDATE,
        CAPTURE_IMAGE,
        OP3_CLEAR,
        PIPELINED_READ,
        RESULT_LATENCY,
        STICKY_IGNORE,
        TCK_STOP_HOLD,
        TCK_STOP_HOLD_SS4,
    }
)

# Reset entries of the clock-reset model
RST_ARST_BUS = "RST-ARST-BUS"
RST_ARST_TCK = "RST-ARST-TCK"
RST_SCAN = "RST-SCAN"
RST_TRST = "RST-TRST"
RST_DMACTIVE = "RST-DMACTIVE"
RESET_SCOPES = frozenset({RST_ARST_BUS, RST_ARST_TCK, RST_SCAN, RST_TRST, RST_DMACTIVE})
_DM_SIDE_RESETS = frozenset({RST_ARST_BUS, RST_SCAN})
_TCK_SIDE_RESETS = frozenset({RST_ARST_TCK, RST_SCAN})
_DM_REGISTER_RESETS = frozenset({RST_ARST_BUS, RST_SCAN, RST_DMACTIVE})

# DMI response codes (RISC-V External Debug Support 0.13.2, dmi.op read back)
RESP_SUCCESS = 0
RESP_BUSY = 3
TDR_STATUS_FIELD = 0x3
TDR_ADDR_LSB = 34
TDR_ADDR_FIELD = 0x7F
TDR_DATA_LSB = 2
TDR_DATA_FIELD = 0xFFFF_FFFF

# dmcontrol.dmactive, sbcs.sbbusy and sbcs.sbautoincrement
DMCONTROL_DMACTIVE = 1 << 0
DMCONTROL_NDMRESET = 1 << 1
SBCS_SBBUSY_LSB = 21
SBCS_SBBUSY = 1 << SBCS_SBBUSY_LSB
SBCS_SBAUTOINCREMENT = 1 << 16
SBCS_SBREADONADDR = 1 << 20
SBCS_SBREADONDATA = 1 << 15
SBCS_SBACCESS_LSB = 17
SBCS_SBACCESS = 0x7 << SBCS_SBACCESS_LSB
SBCS_SBERROR_LSB = 12
SBCS_SBERROR = 0x7 << SBCS_SBERROR_LSB
# sbversion, sbasize and sbaccess128 to sbaccess8 (X-RISCV_DEBUG-sbcs-fields)
SBCS_SBVERSION = 0x7 << 29
SBCS_STATIC = SBCS_SBVERSION | (0x7F << 5) | 0x1F
# dmstatus version, authbusy and authenticated (CHK-DM-VERSION), anyunavail and allunavail;
# abstractcs cmderr
DMSTATUS_VERSION = 0xF | (1 << 6) | (1 << 7)
DMSTATUS_UNAVAIL = (1 << 12) | (1 << 13)
ABSTRACTCS_CMDERR = 0x7 << 8
SBERROR_CHECKS = {2: BUS_ERROR, 3: MISALIGNED, 4: OVERSIZE}

_SBADDRESS = frozenset({SBADDRESS0, SBADDRESS1})
_CLEARED_BY_DMACTIVE = frozenset({SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1, ABSTRACTCS})
_SBDATA = frozenset({SBDATA0, SBDATA1})
_WORD = 0xFFFF_FFFF
_NO_INC_TRIGGERS = frozenset({(TdrOp.WRITE, SBADDRESS0), (TdrOp.WRITE, SBDATA0)})
_DEFAULT_TRIGGER = {BusDir.READ: SINGLE_READ, BusDir.WRITE: SINGLE_WRITE}
_SBA_REGISTERS = frozenset({SBCS, SBADDRESS0, SBADDRESS1, SBDATA0, SBDATA1})
_SBADDRESS0_MASK = 0xFFFF_FFFF
_NDMRESET_READS = frozenset({SBCS, SBADDRESS0, SBADDRESS1})

# Capture fields a check compares
STATUS, ADDRESS, DATA = "status", "address", "data"
_ALL_FIELDS = (STATUS, ADDRESS, DATA)


class ScoreboardSyncError(RuntimeError):
    """The bench fed an observed item before the prediction it pairs with by construction."""


@dataclass(frozen=True)
class Tdr2axiScoreboardCfg:
    """The build's bus form: AXI4 carries AxSIZE, AXI4-Lite the full-width rule; the data
    width bounds the access sizes an oversize access exceeds; SYNC_STAGES names the
    TCK-stop check."""

    lite: bool
    data_width: int = 64
    sync_stages: int = 3

    @property
    def max_size(self) -> int:
        """The largest sbaccess the bus carries."""
        return (self.data_width // 8).bit_length() - 1

    @property
    def tck_stop_check(self) -> str:
        """The TCK-stop check that applies at this build's SYNC_STAGES."""
        return TCK_STOP_HOLD_IDS[self.sync_stages]


@dataclass(frozen=True)
class _ExpectedRequest:
    request: DmiRequest
    optional: bool
    check: str
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Request:
    """A DMI request the DM took, with the context its read answer grades in, sbautoincrement,
    and the check of the bus read whose bytes sbdata holds."""

    op: int
    addr: int
    data: int
    context: str | None
    autoinc: bool
    read_check: str | None
    whole: tuple[str, ...] = ()
    resend: bool = False
    dmactive: bool = False
    sbcs_written: bool = False


@dataclass(frozen=True)
class _ExpectedAnswer:
    """A predicted DM answer and the checks its response code and read data grade, the first
    of which also takes an unpredicted response."""

    request: _Request
    answer: DmAnswer
    checks: tuple[str, ...]
    fields: tuple[tuple[str, int], ...] = ()

    @property
    def check(self) -> str:
        """The answer's first check."""
        return self.checks[0]


@dataclass(frozen=True)
class _ExpectedAccess:
    access: SbaBusAccess
    check: str
    trigger: _Request
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Transfer:
    """One observed bus transaction: an AR, or an AW with its W."""

    direction: BusDir
    addr: int
    size: int
    strb: int = 0
    data: int = 0


@dataclass(frozen=True)
class _ExpectedCapture:
    image: TdrCaptureExpect
    read_pending: bool
    write_pending: bool = False


@dataclass(frozen=True)
class _ExpectedResult:
    """A DM response's way back to o_dmi_tdr: the window in TCK edges after the response
    handshake, the address, the data and the response code."""

    check: str
    window: TdrResultWindow
    addr: int
    data: Expected
    read: bool
    resp: int
    what: str


@dataclass(frozen=True)
class _ExpectedEvent:
    check: str
    label: str
    earliest: int
    latest: int


class Tdr2axiScoreboard:
    """Pairs each predicted DMI request, DM answer, bus transaction and capture status with
    what the DUT did, and reports every comparison under one check ID.

    The ``write_*`` methods are synchronous and never wait: expected items first, then the
    observed items that pair with them, as the bench delivers them in event order.
    """

    def __init__(self, cfg: Tdr2axiScoreboardCfg, report: Reporter) -> None:
        self.cfg = cfg
        self._report = report
        self._log = logging.getLogger("cocotb.tdr2axi.scoreboard")
        self._requests: list[_ExpectedRequest] = []
        self._stale_requests: list[_ExpectedRequest] = []
        self._last_request_check = OP_DECODE
        self._edge_request: _ExpectedRequest | None = None
        self._prev_request: _ExpectedRequest | None = None
        self._prev_update = False
        self._unanswered: deque[_Request] = deque()
        self._answers: deque[_ExpectedAnswer] = deque()
        self._stale_answers: list[_ExpectedAnswer] = []
        self._last_answer_check = RATIO_INVARIANT
        self._accesses: deque[_ExpectedAccess] = deque()
        self._stale_accesses: list[_ExpectedAccess] = []
        self._aw: deque[AxiAddr] = deque()
        self._w: deque[AxiW] = deque()
        self._last_access: _ExpectedAccess | None = None
        self._inflight: _ExpectedAccess | None = None
        self._capture: _ExpectedCapture | None = None
        self._dmactive = False
        self._dmactive_set = False
        self._autoinc = False
        self._read_check: str | None = None
        self._after_op3 = False
        self._failed_access = False
        self._busy_capture = False
        self._op3_capture = False
        self._update_edge_capture = False
        self._init_tdr_contexts()
        self._init_dm_contexts()

    def _init_tdr_contexts(self) -> None:
        """The TDR side's contexts: the model's bridge before this edge's update, and the
        captures each context grades."""
        self._edge_pending = False
        self._edge_busy = False
        self._busy_update = False
        self._sticky = False
        self._busy_dm = False
        self._recovery_armed = False
        self._resend_capture = False
        self._op3_clear_capture = False
        self._pipelined_capture = False
        # A run starts out of the bring-up reset, which the reset watchers do not see
        self._async_capture = True
        self._trst_capture = False
        self._result_poll = False
        self._hold_capture = False
        self._answer_failed = False
        self._no_request: str | None = None

    def _init_dm_contexts(self) -> None:
        """The DM side's contexts, and the results, holds and events."""
        self._sbcs_written = False
        self._sbaccess = 2
        self._sbcs_flags = 0
        self._sbaddr0 = 0
        self._ndmreset = False
        self._async_dm = True
        self._trst_dm = False
        self._trst_window = False
        self._stale_axi = False
        self._cut_off: list[tuple[int, int, int]] = []
        self._no_bus: str | None = None
        self._inflight_ok = True
        self._access_since_update: tuple[_ExpectedAccess, bool] | None = None
        self._result_check: str | None = None
        self._watched_check: str | None = None
        self._result_window: TdrResultWindow | None = None
        self._results: deque[_ExpectedResult] = deque()
        self._stale_results: list[_ExpectedResult] = []
        self._hold_expected: tuple[int, ...] | None = None
        self._hold_change = ""
        self._hold_round = False
        self._events: dict[str, deque[_ExpectedEvent]] = {}

    # --- Expected items -------------------------------------------------------------------
    def write_update(
        self, expect: TdrUpdateExpect, *, pending: bool = False, busy: bool = False
    ) -> None:
        """What one update edge sends, from the TDR status model, before ``write_tck_edge``
        of the same edge; ``pending`` and ``busy`` are the model's bridge before the edge:
        an operation awaiting its answer, and the sticky busy status."""
        self._edge_request = None
        self._edge_pending = pending
        self._edge_busy = busy
        if expect.request is None:
            return
        item = _ExpectedRequest(expect.request, expect.optional, OP_DECODE)
        self._requests.append(item)
        self._edge_request = item

    def write_capture_expect(
        self, image: TdrCaptureExpect, *, read_pending: bool, write_pending: bool = False
    ) -> None:
        """The capture image of one capture edge, before ``write_tck_edge`` of that edge;
        ``read_pending`` and ``write_pending`` say a read or a write awaited its answer at
        the edge."""
        if self._capture is not None:
            raise ScoreboardSyncError("a capture image predicted before the last one was seen")
        self._capture = _ExpectedCapture(image, read_pending, write_pending)

    def watch_results(self, check: str, window: TdrResultWindow) -> None:
        """Grade each DM response's way back to o_dmi_tdr under ``check``, within the TDR
        model's ``window``, until ``stop_results``."""
        self._result_check = check
        self._watched_check = check
        self._result_window = window

    def stop_results(self) -> None:
        """End the watch ``watch_results`` started; the results it predicted stay queued."""
        self._result_check = None
        self._result_window = None
        self._result_poll = False

    def write_hold_start(self, nets: tuple[int, ...]) -> None:
        """TCK stops: ``nets`` (the bridge and o_dmi_tdr) is the state every i_clk edge of
        the stop must show, and the stop's round of bus accesses opens."""
        self._hold_expected = nets
        self._hold_change = ""
        self._hold_round = True
        self._accesses = deque(_tagged(item, self.cfg.tck_stop_check) for item in self._accesses)
        if self._inflight is not None:
            self._record(
                self.cfg.tck_stop_check,
                passed=self._inflight_ok,
                detail=f"{_describe_access(self._inflight.access)} in flight as TCK stops",
            )
        elif self._access_since_update is not None:
            access, ok = self._access_since_update
            self._record(
                self.cfg.tck_stop_check,
                passed=ok,
                detail=f"{_describe_access(access.access)} done before TCK stops",
            )

    def end_trst(self) -> None:
        """The TAP reset ends: each transaction it found queued or that was predicted during
        it must have been seen by now."""
        self._trst_window = False
        for item in self._accesses:
            if TRST_INDEPENDENT in item.extra:
                self._record(
                    TRST_INDEPENDENT,
                    passed=False,
                    detail=f"{_describe_access(item.access)} not taken by the end of the TAP reset",
                )

    def write_event_expect(self, check: str, label: str, earliest: int, latest: int) -> None:
        """A window, from the model, of the next ``label`` count of ``check``."""
        self._events.setdefault(check, deque()).append(
            _ExpectedEvent(check, label, earliest, latest)
        )

    def write_dm_answer(self, answer: DmAnswer) -> None:
        """The SBA model's answer to the oldest DMI request taken without one, with the bus
        access it starts."""
        if not self._unanswered:
            raise ScoreboardSyncError("a DM answer predicted with no DMI request taken")
        request = self._unanswered.popleft()
        fields = (*((c, -1) for c in request.whole), *_field_checks(request, answer))
        self._answers.append(
            _ExpectedAnswer(request, answer, _data_checks(request, answer), fields)
        )
        if answer.access is None:
            return
        self._failed_access = False
        self._no_bus = None
        check = _trigger_check(request, answer.access)
        extra = (BUSY_RECOVERY,) if request.resend else ()
        if self._hold_round:
            extra += (self.cfg.tck_stop_check,)
        if self._trst_window:
            extra += (TRST_INDEPENDENT,)
        self._accesses.append(_ExpectedAccess(answer.access, check, request, extra))
        if answer.access.direction is BusDir.READ:
            self._read_check = check
        if request.autoinc:
            self._sbaddr0 = (answer.access.addr + (1 << self._sbaccess)) & _SBADDRESS0_MASK

    # --- Observed items -------------------------------------------------------------------
    def write_tck_edge(
        self, *, update_word: int | None, capture: bool, captured: int | None
    ) -> None:
        """One TCK rising edge: the update word when the update strobe is high, and
        ``o_dmi_tdr`` at a capture edge, None when it holds X or Z."""
        if update_word is None:
            self._prev_update = False
            self._prev_request = None
        else:
            self._observe_update(update_word)
        self._edge_request = None
        if capture:
            self._observe_capture(captured)

    def write_dmi_request(self, op: int, addr: int, data: int) -> None:
        """A DMI request handshake of the DM."""
        paired = self._pair_request((op, addr, data))
        resend = paired is not None and BUSY_RECOVERY in paired.extra
        context = self._read_context(addr) if op == TdrOp.READ else None
        whole = self._whole_checks(op, addr, resend=resend)
        self._unanswered.append(
            _Request(
                op,
                addr,
                data,
                context,
                self._autoinc,
                self._read_check,
                whole,
                resend,
                self._dmactive,
                self._sbcs_written,
            )
        )
        self._apply_request(op, addr, data)

    def write_dmi_response(self, data: int | None, resp: int) -> None:
        """A DMI response handshake of the DM; ``data`` is None when it holds X or Z."""
        if not self._answers:
            self._unexpected_response(data, resp)
            return
        expected = self._answers.popleft()
        self._last_answer_check = expected.check
        self._compare_answer(expected, data, resp)
        if self._result_check is not None and self._result_window is not None:
            self._results.append(self._expected_result(expected))

    def write_bus(self, channel: str, item: AxiAddr | AxiW) -> None:
        """An AR, AW or W handshake, as ``AxiSub`` publishes it; a write pairs once its AW
        and W have both been seen."""
        if channel == "w":
            self._w.append(cast("AxiW", item))
        elif channel == "ar":
            ar = cast("AxiAddr", item)
            self._pair_access(_Transfer(BusDir.READ, ar.addr, ar.size))
        else:
            self._aw.append(cast("AxiAddr", item))
        if self._aw and self._w:
            aw, w = self._aw.popleft(), self._w.popleft()
            self._pair_access(_Transfer(BusDir.WRITE, aw.addr, aw.size, w.strb, w.data))

    def write_bus_response(self, *, error: bool) -> None:
        """The R or B handshake of the transaction in flight; ``error`` is SLVERR or DECERR."""
        access, self._inflight = self._inflight, None
        if not error or access is None:
            return
        self._record(
            BUS_ERROR,
            passed=self._inflight_ok,
            detail=f"{_describe_access(access.access)} answered with an error response",
        )
        self._no_bus = BUS_ERROR
        trigger = access.trigger
        if trigger.autoinc:
            self._sbaddr0 = access.access.addr & _SBADDRESS0_MASK
        if trigger.autoinc and (trigger.op, trigger.addr) in _NO_INC_TRIGGERS:
            self._failed_access = True

    def write_memory(
        self,
        check: str,
        addr: int,
        observed: Sequence[int | None],
        expected: Sequence[int | None],
    ) -> None:
        """The subordinate's bytes from ``addr`` against the model's image of them, under
        ``check``; a byte the model leaves unknown (None) is not compared."""
        for offset, (got, exp) in enumerate(zip(observed, expected, strict=True)):
            if exp is None:
                continue
            got_text = "unwritten" if got is None else f"0x{got:02x}"
            self._record(
                check,
                passed=got == exp,
                detail=f"mem[0x{addr + offset:x}] = {got_text}, predicted 0x{exp:02x}",
            )

    def write_result_arrival(
        self, *, edges: int | None, read: bool, word: int, resp: int | None
    ) -> None:
        """A result back in o_dmi_tdr, as the timing monitor measures it: TCK edges from the
        DM's response handshake (None with no handshake before it), whether the bridge
        waited for a read, the word it holds and the response code of the handshake."""
        _, addr, data = decode_tdr(word)
        seen = f"o_dmi_tdr 0x{word:011x} {edges} TCK edges after the response handshake"
        if not self._results:
            stale = _take_first(self._stale_results, lambda e: e.addr == addr)
            check = stale.check if stale is not None else self._watched_check
            if check is None:
                raise ScoreboardSyncError("a result arrival with no result check ever watched")
            why = "with no response pending" if stale is None else "matching only a reset's"
            self._record(check, passed=False, detail=f"{seen} {why}")
            return
        expected = self._results.popleft()
        window = expected.window
        passed = (
            edges is not None
            and window.earliest <= edges <= window.latest
            and read == expected.read
            and addr == expected.addr
            and _matches(expected.data, data)
            and resp == expected.resp
        )
        self._record(
            expected.check,
            passed=passed,
            detail=f"{seen} ({'read' if read else 'write'}, response {resp}) for "
            f"{expected.what}: predicted {window.earliest} to {window.latest} edges "
            f"({window.timing}), address 0x{expected.addr:02x}, data "
            f"{_describe_expected(expected.data)}, response {expected.resp}",
        )

    def write_hold_sample(self, nets: tuple[int, ...], edge: int) -> None:
        """The bridge and o_dmi_tdr at i_clk rising edge ``edge`` of a TCK stop."""
        if self._hold_expected is None:
            raise ScoreboardSyncError("a TCK-stop sample with no stop started")
        if nets == self._hold_expected or self._hold_change:
            return
        changed = ", ".join(
            f"net {i} 0x{a:x} to 0x{b:x}"
            for i, (a, b) in enumerate(zip(self._hold_expected, nets, strict=True))
            if a != b
        )
        self._hold_change = f"{changed} at i_clk rising edge {edge} of the stop"

    def write_hold_end(self) -> None:
        """TCK runs again: the stop grades whether every sample held, and the next capture
        grades the result."""
        if self._hold_expected is None:
            raise ScoreboardSyncError("a TCK stop ended that never started")
        self._record(
            self.cfg.tck_stop_check,
            passed=not self._hold_change,
            detail=f"with TCK stopped: {self._hold_change or 'every sample held'}",
        )
        self._hold_expected = None
        self._hold_capture = True

    def write_event(self, check: str, label: str, value: int | None) -> None:
        """A measured ``label`` count of ``check``, None when nothing was seen."""
        queue = self._events.get(check)
        if not queue:
            self._record(check, passed=False, detail=f"{label} count {value} with no window")
            return
        expected = queue.popleft()
        self._record(
            check,
            passed=expected.label == label
            and value is not None
            and expected.earliest <= value <= expected.latest,
            detail=f"{label} count {value}, predicted {expected.label} "
            f"{expected.earliest} to {expected.latest}",
        )

    def reset(self, scope: str) -> None:
        """Apply the reset entry ``scope`` of the clock-reset model: drop the predictions it
        ends, keeping them to name the checks of an item seen after the reset."""
        if scope not in RESET_SCOPES:
            raise ValueError(f"unknown reset scope {scope!r}")
        if scope == RST_TRST:
            self._open_trst()
        elif self._trst_window:
            self._record(
                TRST_INDEPENDENT,
                passed=False,
                detail=f"{scope} during the TAP reset drops the operation the TAP reset "
                "leaves in place",
            )
        if scope == RST_ARST_BUS:
            self._open_stale_axi()
        if scope in _DM_SIDE_RESETS:
            self._flush_dm_side()
        if scope in _DM_SIDE_RESETS or scope in _TCK_SIDE_RESETS:
            self._flush_requests()
        if scope in _TCK_SIDE_RESETS:
            self._flush_bridge()
        if scope in _DM_REGISTER_RESETS:
            self._clear_dm_registers()

    def drain(self) -> None:
        """End of test: each prediction left unobserved and each AW or W without its partner
        fails its checks, and every queue and context clears."""
        for item in self._requests:
            if not item.optional:
                for check in (item.check, *item.extra):
                    self._record(
                        check,
                        passed=False,
                        detail=f"predicted {_describe_request(item)} never sent",
                    )
        for request in self._unanswered:
            self._record(
                RATIO_INVARIANT,
                passed=False,
                detail=f"no DM answer predicted for {_describe(request)}",
            )
        for expected in self._answers:
            detail = f"no DM response to {_describe(expected.request)}"
            for check in _answer_checks(expected):
                self._record(check, passed=False, detail=detail)
        for access in self._accesses:
            detail = f"{_describe_access(access.access)} never seen"
            checks = self._access_checks(access.access.direction, access.check, access.access)
            for check in (*checks, *access.extra):
                self._record(check, passed=False, detail=detail)
        for half in (*self._aw, *self._w):
            self._record(SINGLE_WRITE, passed=False, detail=f"{half} without its AW or W partner")
        for result in self._results:
            self._record(result.check, passed=False, detail=f"{result.what} never came back")
        for events in self._events.values():
            for event in events:
                self._record(
                    event.check, passed=False, detail=f"{event.label} count never measured"
                )
        if self._hold_expected is not None:
            self._record(
                self.cfg.tck_stop_check, passed=False, detail="a TCK stop that never ended"
            )
        self._results.clear()
        self._stale_results.clear()
        self._events.clear()
        self._hold_expected = None
        self._requests.clear()
        self._unanswered.clear()
        self._answers.clear()
        self._accesses.clear()
        self._aw.clear()
        self._w.clear()
        self._stale_requests.clear()
        self._stale_answers.clear()
        self._stale_accesses.clear()

    # --- Private: DMI requests ------------------------------------------------------------
    def _observe_update(self, word: int) -> None:
        current = self._edge_request
        if self._prev_update:
            self._retag(self._prev_request)
            current = self._retag(current)
            self._update_edge_capture = True
        if decode_tdr(word)[0] == TdrOp.CLEAR:
            self._after_op3 = True
            self._op3_capture = True
            self._busy_capture = False
        self._prev_update = True
        self._prev_request = self._update_contexts(decode_tdr(word)[0], current)

    def _update_contexts(
        self, op: int, current: _ExpectedRequest | None
    ) -> _ExpectedRequest | None:
        """Open and close the TDR-side contexts an update starts, from the model's bridge
        before it; a resend's request also grades CHK-TDR-BUSY-RECOVERY."""
        pending, busy = self._edge_pending, self._edge_busy
        self._edge_pending = self._edge_busy = False
        if op == TdrOp.CLEAR:
            self._op3_clear_capture = True
            self._recovery_armed = self._recovery_armed or pending or busy
            self._busy_update = self._sticky = self._busy_dm = False
            self._no_request = OP3_CLEAR
            return current
        if pending:
            self._busy_update = True
            self._no_request = BUSY_UPDATE
            return current
        if busy:
            self._sticky = True
            self._no_request = STICKY_IGNORE
            return current
        self._no_request = None
        self._answer_failed = False
        self._access_since_update = None
        self._pipelined_capture = op == TdrOp.READ
        self._result_poll = self._result_check is not None and op == TdrOp.READ
        if current is None or not self._recovery_armed:
            return current
        self._recovery_armed = False
        self._resend_capture = True
        for index, queued in enumerate(self._requests):
            if queued is current:
                self._requests[index] = replace(queued, extra=(*queued.extra, BUSY_RECOVERY))
                return self._requests[index]
        return current

    def _retag(self, item: _ExpectedRequest | None) -> _ExpectedRequest | None:
        """A queued request of a strobe held over consecutive edges grades
        CHK-TDR-UPDATE-EDGE."""
        for index, queued in enumerate(self._requests):
            if queued is item:
                self._requests[index] = replace(queued, check=UPDATE_EDGE)
                return self._requests[index]
        return item

    def _pair_request(self, observed: tuple[int, int, int]) -> _ExpectedRequest | None:
        index = self._match_request(observed)
        if index is None:
            stale = _take_first(self._stale_requests, lambda e: _request_fields(e) == observed)
            if stale is None:
                checks = [self._last_request_check]
                if self._no_request is not None and self._no_request not in checks:
                    checks.append(self._no_request)
            else:
                checks = [stale.check, *stale.extra]
            if self._stale_axi and (stale is not None or observed in self._cut_off):
                checks.append(NO_STALE_AXI)
            why = "with no request predicted" if stale is None else "matching only a reset's"
            for check in checks:
                self._record(check, passed=False, detail=f"DMI {_describe_dmi(*observed)} {why}")
            return None
        expected = self._requests[index]
        del self._requests[: index + 1]
        self._last_request_check = expected.check
        if self._stale_axi and _request_fields(expected) != observed and observed in self._cut_off:
            self._record(
                NO_STALE_AXI,
                passed=False,
                detail=f"DMI {_describe_dmi(*observed)} repeats an operation the "
                f"{RST_ARST_BUS} reset cut off, in place of {_describe_request(expected)}",
            )
        for check in (expected.check, *expected.extra):
            self._record(
                check,
                passed=_request_fields(expected) == observed,
                detail=f"DMI {_describe_dmi(*observed)}, predicted {_describe_request(expected)}",
            )
        return expected

    def _match_request(self, observed: tuple[int, int, int]) -> int | None:
        """The queued request a handshake pairs with: the first with its fields when every one
        before it is optional, else the oldest."""
        if not self._requests:
            return None
        for index, item in enumerate(self._requests):
            if _request_fields(item) != observed:
                continue
            if all(skipped.optional for skipped in self._requests[:index]):
                return index
            break
        return 0

    # --- Private: DM answers --------------------------------------------------------------
    def _read_context(self, addr: int) -> str | None:
        if not self._dmactive and addr in _CLEARED_BY_DMACTIVE:
            return DMACTIVE_CLEAR
        if self._dmactive_set and addr == SBCS:
            return DMACTIVE_CLEAR
        if self._after_op3:
            return OP3_KEEPS_DM
        if self._failed_access and addr in _SBADDRESS:
            return NO_INC_AFTER_ERROR
        return None

    def _whole_checks(self, op: int, addr: int, *, resend: bool) -> tuple[str, ...]:
        """The checks a read's whole answer grades in the contexts open when it is taken."""
        if op != TdrOp.READ:
            return ()
        checks = []
        if self._ndmreset and addr in _NDMRESET_READS:
            checks.append(NDMRESET)
        if self._async_dm:
            checks.append(ASYNC_ANY_TIME)
        if self._trst_dm:
            checks.append(TRST_INDEPENDENT)
        if resend:
            checks.append(BUSY_RECOVERY)
        return tuple(checks)

    def _apply_request(self, op: int, addr: int, data: int) -> None:
        self._open_access_error(op, addr, data)
        if op == TdrOp.READ:
            if addr == SBCS:
                self._dmactive_set = False
            return
        self._after_op3 = False
        if addr in _SBA_REGISTERS:
            self._ndmreset = False
        if addr == DMCONTROL:
            self._apply_dmcontrol(data)
            return
        if addr == SBCS:
            self._apply_sbcs(data)
            return
        if addr == COMMAND and self._dmactive:
            self._no_bus = CMDERR
        if addr == SBADDRESS0:
            self._sbaddr0 = data
        if addr in _SBDATA:
            self._read_check = None

    def _apply_dmcontrol(self, data: int) -> None:
        if not data & DMCONTROL_DMACTIVE:
            self.reset(RST_DMACTIVE)
            return
        self._async_dm = False
        self._ndmreset = bool(data & DMCONTROL_NDMRESET) or self._ndmreset
        if not self._dmactive:
            self._dmactive = True
            self._dmactive_set = True

    def _apply_sbcs(self, data: int) -> None:
        self._trst_dm = False
        if not self._dmactive:
            return
        self._autoinc = bool(data & SBCS_SBAUTOINCREMENT)
        self._dmactive_set = False
        self._sbcs_written = True
        self._sbaccess = (data & SBCS_SBACCESS) >> SBCS_SBACCESS_LSB
        self._sbcs_flags = data & (SBCS_SBREADONADDR | SBCS_SBREADONDATA)

    def _open_access_error(self, op: int, addr: int, data: int) -> None:
        """A request that starts an access opens that error's no-bus context when the
        observed sbcs makes the access wider than the bus, or when the address the access
        is made from is misaligned: the value an sbaddress0 write sets, else the sbaddress0
        the DM holds."""
        sets_address = op == TdrOp.WRITE and addr == SBADDRESS0
        starts = (
            (sets_address and self._sbcs_flags & SBCS_SBREADONADDR)
            or (op == TdrOp.WRITE and addr == SBDATA0)
            or (op == TdrOp.READ and addr == SBDATA0 and self._sbcs_flags & SBCS_SBREADONDATA)
        )
        if not starts or not self._dmactive:
            return
        address = data if sets_address else self._sbaddr0
        if self._sbaccess > self.cfg.max_size:
            self._no_bus = OVERSIZE
        elif address & ((1 << self._sbaccess) - 1):
            self._no_bus = MISALIGNED

    def _compare_answer(self, expected: _ExpectedAnswer, data: int | None, resp: int) -> None:
        answer = expected.answer
        what = f"DM answer to {_describe(expected.request)}: response {resp} data {_hex(data)}"
        if answer.busy:
            self._record(SBBUSYERROR, passed=resp == RESP_BUSY, detail=f"{what}, predicted busy")
            for check, mask in expected.fields:
                if mask < 0:
                    self._record(check, passed=resp == RESP_BUSY, detail=f"{what}, predicted busy")
            if resp == RESP_BUSY:
                self._busy_dm = True
            return
        if resp != RESP_SUCCESS:
            self._answer_failed = True
            for check in _answer_checks(expected):
                self._record(check, passed=False, detail=f"{what}, predicted success")
            if resp == RESP_BUSY:
                self._record(SBBUSYERROR, passed=False, detail=f"{what}, predicted success")
            return
        graded = _given_sbbusy(expected, data)
        self._compare_fields(expected, graded, data, what)
        if _grades_sbbusy(expected):
            self._compare_sbcs(expected, graded, data, what)
            return
        passed = _matches(answer.data, data)
        if not passed and expected.request.op == TdrOp.READ:
            self._answer_failed = True
        detail = f"{what}, predicted {_describe_expected(answer.data)}"
        for check in expected.checks:
            self._record(check, passed=passed, detail=detail)

    def _compare_fields(
        self, expected: _ExpectedAnswer, graded: Expected, data: int | None, what: str
    ) -> None:
        """Each field check of a successful answer against ``graded``, on the bits the model
        predicts within the check's field; a mask below zero is the whole answer. A failure
        names the bits that differ from the nearest allowed value."""
        for check, mask in expected.fields:
            bits = graded.mask if mask < 0 else graded.mask & mask
            if not bits:
                continue
            field = replace(graded, mask=bits)
            passed = _matches(field, data)
            detail = f"{what}, predicted {_describe_values(graded.allowed)} on mask 0x{bits:x}"
            self._record(
                check,
                passed=passed,
                detail=detail if passed else f"{detail}, differs on {_differing_bits(field, data)}",
            )

    def _compare_sbcs(
        self, expected: _ExpectedAnswer, graded: Expected, data: int | None, what: str
    ) -> None:
        """sbbusy grades CHK-SBA-SBBUSY; the other fields grade the answer's check against
        ``graded``. A value whose other fields match some predicted value fails
        CHK-SBA-SBBUSY unless it matches one whole, so a value that a wrong sbbusy or a
        wrong field explains equally fails both."""
        exp = expected.answer.data
        rest = replace(graded, mask=graded.mask & ~SBCS_SBBUSY)
        rest_ok = _matches(rest, data)
        if not rest_ok:
            self._answer_failed = True
        allowed = sorted({(value >> SBCS_SBBUSY_LSB) & 1 for value in exp.allowed})
        seen = None if data is None else (data >> SBCS_SBBUSY_LSB) & 1
        detail = f"{what}, predicted {_describe_expected(exp)}"
        self._record(expected.check, passed=rest_ok, detail=detail)
        any_rest = replace(exp, mask=exp.mask & ~SBCS_SBBUSY)
        sbbusy_ok = _matches(exp, data) or (not _matches(any_rest, data) and seen in allowed)
        self._record(
            SBBUSY, passed=sbbusy_ok, detail=f"{detail}: sbbusy {seen}, window allows {allowed}"
        )

    def _unexpected_response(self, data: int | None, resp: int) -> None:
        stale = _take_first(self._stale_answers, lambda e: _matches(e.answer.data, data))
        check = self._last_answer_check if stale is None else stale.check
        why = "with no DMI request pending" if stale is None else "matching only a reset's"
        detail = f"DM response {resp} data {_hex(data)} {why}"
        self._record(check, passed=False, detail=detail)
        if stale is not None and self._stale_axi:
            self._record(NO_STALE_AXI, passed=False, detail=detail)

    # --- Private: bus transactions --------------------------------------------------------
    def _pair_access(self, observed: _Transfer) -> None:
        if not self._accesses:
            self._unexpected_transfer(observed)
            return
        expected = self._accesses.popleft()
        self._last_access = expected
        self._inflight = expected
        self._inflight_ok = self._compare_access(expected, observed)
        self._access_since_update = (expected, self._inflight_ok)
        extra = list(expected.extra)
        if self._busy_update and BUSY_UPDATE not in extra:
            extra.append(BUSY_UPDATE)
        if self._hold_round and self.cfg.tck_stop_check not in extra:
            extra.append(self.cfg.tck_stop_check)
        if self._stale_axi:
            self._stale_axi = False
            self._cut_off = []
            extra.append(NO_STALE_AXI)
        for check in extra:
            self._record(
                check,
                passed=self._inflight_ok,
                detail=f"{_describe_transfer(observed)}, predicted "
                f"{_describe_access(expected.access)}",
            )
        if BUSY_RECOVERY in extra:
            self._no_bus = BUSY_RECOVERY

    def _compare_access(self, expected: _ExpectedAccess, observed: _Transfer) -> bool:
        """Grade one transaction and return whether it matched as a whole."""
        access = expected.access
        what = (
            f"{_describe_transfer(observed)}, predicted {_describe_access(access)} for "
            f"{_describe(expected.trigger)}"
        )
        if observed.direction is not access.direction:
            for check in self._access_checks(access.direction, expected.check, access):
                self._record(check, passed=False, detail=what)
            return False
        addr_ok = observed.addr == access.addr
        size_ok = access.size is None or observed.size == access.size
        strb_ok = access.wstrb is None or observed.strb == access.wstrb
        data_ok = access.wdata is None or access.wdata.matches(observed.data)
        whole_ok = addr_ok and size_ok and strb_ok and data_ok
        self._record(expected.check, passed=whole_ok, detail=what)
        self._record(ADDRESS_WIDTH, passed=addr_ok, detail=what)
        if not self.cfg.lite:
            self._record(AXI4_SIZE, passed=size_ok and strb_ok, detail=what)
        elif access.direction is BusDir.WRITE:
            self._record(LITE_FULL_WIDTH, passed=strb_ok, detail=what)
        if access.wdata is not None and access.wdata.mask:
            self._record(DATA_LANES, passed=data_ok, detail=what)
        return whole_ok

    def _access_checks(
        self, direction: BusDir, trigger: str, access: SbaBusAccess | None
    ) -> list[str]:
        """The checks a transaction carries; ``access`` None is one nothing predicted, whose
        WDATA counts as compared."""
        checks = [trigger, ADDRESS_WIDTH]
        if not self.cfg.lite:
            checks.append(AXI4_SIZE)
        elif direction is BusDir.WRITE:
            checks.append(LITE_FULL_WIDTH)
        if direction is BusDir.WRITE and (
            access is None or (access.wdata is not None and access.wdata.mask)
        ):
            checks.append(DATA_LANES)
        return checks

    def _unexpected_transfer(self, observed: _Transfer) -> None:
        stale = _take_first(
            self._stale_accesses,
            lambda e: e.access.direction is observed.direction and e.access.addr == observed.addr,
        )
        if stale is not None:
            checks = self._access_checks(observed.direction, stale.check, stale.access)
            checks += list(stale.extra)
            why = "matching only a reset's prediction"
        else:
            last = self._last_access
            same = last is not None and last.access.direction is observed.direction
            trigger = (
                last.check if last is not None and same else _DEFAULT_TRIGGER[observed.direction]
            )
            checks = self._access_checks(observed.direction, trigger, None)
            why = "with no access predicted"
        checks += [c for c in self._no_bus_checks() if c not in checks]
        for check in checks:
            self._record(check, passed=False, detail=f"{_describe_transfer(observed)} {why}")

    def _no_bus_checks(self) -> list[str]:
        """The checks a transaction nothing predicted also fails in the open contexts."""
        contexts = [
            (self._no_bus is not None, self._no_bus),
            (self._sticky, STICKY_IGNORE),
            (self._busy_update, BUSY_UPDATE),
            (self._ndmreset, NDMRESET),
            (self._hold_round, self.cfg.tck_stop_check),
            (self._stale_axi, NO_STALE_AXI),
        ]
        return [check for open_, check in contexts if open_ and check is not None]

    # --- Private: captures ----------------------------------------------------------------
    def _observe_capture(self, captured: int | None) -> None:
        expected, self._capture = self._capture, None
        if expected is None:
            raise ScoreboardSyncError("a capture edge with no capture image predicted")
        check = self._capture_check(read_pending=expected.read_pending)
        self._grade_capture_contexts(expected, captured)
        self._grade_cut_off_capture(expected.image, captured)
        if check is None or captured is None:
            return
        image = expected.image
        allowed = sorted({int(image.status)} | {int(alt.status) for alt in image.alternatives})
        status = captured & TDR_STATUS_FIELD
        self._record(
            check,
            passed=status in allowed,
            detail=f"capture status {status} in o_dmi_tdr 0x{captured:011x}, predicted {allowed}",
        )

    def _grade_capture_contexts(self, expected: _ExpectedCapture, captured: int | None) -> None:
        """Grade every capture context open at this edge on the fields it names, and close
        the contexts that last one capture."""
        image = expected.image
        success = image.status is TdrStatus.SUCCESS
        contexts: list[tuple[bool, str | None, tuple[str, ...]]] = [
            (success, CAPTURE_IMAGE, _ALL_FIELDS),
            (success and self._pipelined_capture, PIPELINED_READ, (ADDRESS, DATA)),
            (self._op3_clear_capture, OP3_CLEAR, (STATUS,)),
            (self._busy_update or expected.write_pending, BUSY_UPDATE, (STATUS,)),
            (self._sticky, STICKY_IGNORE, (STATUS,)),
            (self._busy_dm, BUSY_DM, _ALL_FIELDS),
            (self._resend_capture, BUSY_RECOVERY, _ALL_FIELDS),
            (self._async_capture, ASYNC_ANY_TIME, _ALL_FIELDS),
            (self._trst_capture, TRST_INDEPENDENT, _ALL_FIELDS),
            (self._ndmreset, NDMRESET, (STATUS,)),
            (self._result_poll, self._result_check, _ALL_FIELDS),
            (self._hold_capture, self.cfg.tck_stop_check, _ALL_FIELDS),
        ]
        if self._hold_capture:
            self._hold_round = False
        if self._trst_capture and success:
            self._trst_capture = False
        self._pipelined_capture = self._op3_clear_capture = self._resend_capture = False
        self._async_capture = self._result_poll = self._hold_capture = False
        for open_, check, fields in contexts:
            if not open_ or check is None:
                continue
            if self._answer_failed:
                fields = tuple(f for f in fields if f != DATA)
            self._record(
                check,
                passed=captured is not None and _image_fields_match(image, captured, fields),
                detail=f"o_dmi_tdr {_tdr_hex(captured)} at capture, {'/'.join(fields)} "
                f"against {_describe_image(image)}",
            )

    def _capture_check(self, *, read_pending: bool) -> str | None:
        """The check a capture grades, or None outside every capture context."""
        if read_pending:
            self._busy_capture = True
        if self._update_edge_capture:
            check: str | None = UPDATE_EDGE
        elif self._busy_capture:
            check = BUSY_CAPTURE
        elif self._op3_capture:
            check = OP3_KEEPS_DM
        else:
            check = None
        self._update_edge_capture = False
        self._op3_capture = False
        return check

    def _grade_cut_off_capture(self, image: TdrCaptureExpect, captured: int | None) -> None:
        """A capture in the stale-access context that the predicted image does not allow
        but that holds the address, and for a write the data, of an operation the
        i_arst_n reset cut off fails CHK-RESET-NO-STALE-AXI."""
        if not self._stale_axi or captured is None:
            return
        if _image_fields_match(image, captured, _ALL_FIELDS):
            return
        _, addr, data = decode_tdr(captured)
        for op, cut_addr, cut_data in self._cut_off:
            if addr == cut_addr and (op != TdrOp.WRITE or data == cut_data):
                self._record(
                    NO_STALE_AXI,
                    passed=False,
                    detail=f"o_dmi_tdr {_tdr_hex(captured)} at capture matches only "
                    f"{_describe_dmi(op, cut_addr, cut_data)}, which the {RST_ARST_BUS} "
                    "reset cut off",
                )
                return

    # --- Private: resets and evidence -----------------------------------------------------
    def _open_stale_axi(self) -> None:
        """An i_arst_n reset cuts off the operations in flight: until the first access
        predicted after it pairs, an item only one of them matches, and a transaction
        nothing predicted, fails CHK-RESET-NO-STALE-AXI."""
        self._stale_axi = True
        self._cut_off.extend(
            [
                *(_request_fields(item) for item in self._requests),
                *((request.op, request.addr, request.data) for request in self._unanswered),
                *((e.request.op, e.request.addr, e.request.data) for e in self._answers),
            ]
        )

    def _flush_dm_side(self) -> None:
        self._stale_results.extend(self._results)
        self._results.clear()
        self._events.clear()
        self._no_bus = None
        self._async_dm = True
        self._stale_answers.extend(self._answers)
        self._answers.clear()
        self._unanswered.clear()
        self._stale_accesses.extend(self._accesses)
        self._accesses.clear()
        self._aw.clear()
        self._w.clear()
        self._inflight = None

    def _flush_requests(self) -> None:
        self._stale_requests.extend(self._requests)
        self._requests.clear()
        self._edge_request = None
        self._prev_request = None
        self._prev_update = False

    def _flush_bridge(self) -> None:
        self._capture = None
        self._busy_capture = False
        self._op3_capture = False
        self._update_edge_capture = False
        self._after_op3 = False
        self._busy_update = self._sticky = self._busy_dm = False
        self._recovery_armed = self._resend_capture = False
        self._op3_clear_capture = self._pipelined_capture = False
        self._trst_capture = self._result_poll = False
        self._answer_failed = False
        self._no_request = None
        self._async_capture = True

    def _open_trst(self) -> None:
        """A TAP reset leaves tdr2axi untouched: the next captures, the reads until the next
        sbcs write and the transactions queued or in flight grade the reset."""
        self._trst_capture = True
        self._trst_dm = True
        self._trst_window = True
        self._accesses = deque(_tagged(item, TRST_INDEPENDENT) for item in self._accesses)
        if self._inflight is not None:
            self._record(
                TRST_INDEPENDENT,
                passed=self._inflight_ok,
                detail=f"{_describe_access(self._inflight.access)} in flight at the TAP reset",
            )

    def _expected_result(self, expected: _ExpectedAnswer) -> _ExpectedResult:
        """The result a DM response predicts in o_dmi_tdr: a read's predicted data, or the
        busy marker for a busy answer, or a write's data."""
        request, answer = expected.request, expected.answer
        read = request.op == TdrOp.READ
        if answer.busy and read:
            data = Expected.exact(BUSY_MARKER, TDR_DATA_FIELD, answer.atoms)
        elif read:
            data = answer.data
        else:
            data = Expected.exact(request.data, TDR_DATA_FIELD, answer.atoms)
        if self._result_check is None or self._result_window is None:
            raise ScoreboardSyncError("a result predicted with no result check watched")
        return _ExpectedResult(
            self._result_check,
            self._result_window,
            request.addr,
            data,
            read,
            RESP_BUSY if answer.busy else RESP_SUCCESS,
            _describe(request),
        )

    def _clear_dm_registers(self) -> None:
        self._sbcs_written = False
        self._sbaccess = 2
        self._sbcs_flags = 0
        self._sbaddr0 = 0
        self._ndmreset = False
        self._dmactive = False
        self._dmactive_set = False
        self._autoinc = False
        self._read_check = None
        self._failed_access = False
        self._after_op3 = False

    def _record(self, check_id: str, *, passed: bool, detail: str) -> None:
        if passed:
            self._log.debug("%s matched: %s", check_id, detail)
        self._report(check_id, passed=passed, detail=detail)


class Tdr2axiTdrModelTap(TdrStatusModel):
    """``TdrStatusModel`` that also writes each update prediction and capture image to the
    scoreboard; every prediction is the model's own."""

    def __init__(
        self, scoreboard: Tdr2axiScoreboard, *, sync_stages: int, negative: bool = False
    ) -> None:
        super().__init__(sync_stages=sync_stages, negative=negative)
        self._scoreboard = scoreboard

    def predict_update(self, word: int) -> TdrUpdateExpect:
        """``TdrStatusModel.predict_update``, written to the scoreboard with the bridge the
        model held before it."""
        pending, busy = self.pending is not None, self.status is TdrStatus.BUSY
        expect = super().predict_update(word)
        self._scoreboard.write_update(expect, pending=pending, busy=busy)
        return expect

    def predict_capture(self) -> TdrCaptureExpect:
        """``TdrStatusModel.predict_capture``, written to the scoreboard with whether a read
        or a write was pending at the edge."""
        read_pending = self.pending is TdrOp.READ
        write_pending = self.pending is TdrOp.WRITE
        image = super().predict_capture()
        self._scoreboard.write_capture_expect(
            image, read_pending=read_pending, write_pending=write_pending
        )
        return image


def _data_checks(request: _Request, answer: DmAnswer) -> tuple[str, ...]:
    """The checks an answer's response code and read data grade, first match wins."""
    if answer.busy:
        return (SBBUSYERROR,)
    if request.op != TdrOp.READ:
        return (RATIO_INVARIANT,)
    if request.context is not None:
        return (request.context,)
    if request.addr in _SBADDRESS:
        return (ADDRESS_WIDTH,)
    if request.addr not in _SBDATA:
        return (RATIO_INVARIANT,)
    starts_read = answer.access is not None and answer.access.direction is BusDir.READ
    read_check = BLOCK_READ if starts_read else request.read_check
    return (DATA_LANES,) if read_check is None else (read_check, DATA_LANES)


def _trigger_check(request: _Request, access: SbaBusAccess) -> str:
    if access.direction is BusDir.READ:
        return BLOCK_READ if request.op == TdrOp.READ else SINGLE_READ
    return BLOCK_WRITE if request.autoinc else SINGLE_WRITE


def _grades_sbbusy(expected: _ExpectedAnswer) -> bool:
    request, answer = expected.request, expected.answer
    return (
        request.op == TdrOp.READ
        and request.addr == SBCS
        and not answer.busy
        and bool(answer.data.mask & SBCS_SBBUSY)
    )


def _given_sbbusy(expected: _ExpectedAnswer, data: int | None) -> Expected:
    """The prediction an sbcs answer's other fields are graded against: the allowed values
    whose sbbusy equals the one read, when the window allows that sbbusy. An sberror the
    model predicts with that sbbusy then fails its own check rather than CHK-SBA-SBBUSY."""
    exp = expected.answer.data
    if data is None or not _grades_sbbusy(expected):
        return exp
    seen = data & SBCS_SBBUSY
    allowed = tuple(value for value in exp.allowed if value & SBCS_SBBUSY == seen)
    return replace(exp, allowed=allowed) if allowed else exp


def _answer_checks(expected: _ExpectedAnswer) -> list[str]:
    """The checks an answer carries."""
    checks = [*expected.checks, SBBUSY] if _grades_sbbusy(expected) else list(expected.checks)
    exp = expected.answer.data
    fields = [c for c, m in expected.fields if exp.mask & (exp.mask if m < 0 else m)]
    return checks + [c for c in fields if c not in checks]


def _field_checks(request: _Request, answer: DmAnswer) -> tuple[tuple[str, int], ...]:
    """The field checks a read's answer grades: each check with the bits it compares."""
    if request.op != TdrOp.READ or answer.busy:
        return ()
    if request.addr == DMSTATUS:
        return ((VERSION, DMSTATUS_VERSION), (HART_UNAVAIL, DMSTATUS_UNAVAIL), (REGISTER_MAP, -1))
    if request.addr == DMCONTROL:
        return ((REGISTER_MAP, -1),)
    if not request.dmactive:
        return ()
    # A read in a context grades its whole value under that context's check, not under
    # CHK-DM-REGISTER-MAP
    register_map = () if request.context is not None else ((REGISTER_MAP, -1),)
    if request.addr == ABSTRACTCS:
        return ((CMDERR, ABSTRACTCS_CMDERR), *register_map)
    if request.addr in _SBADDRESS or request.addr in _SBDATA:
        return register_map
    if request.addr != SBCS:
        return ()
    fields = SBCS_STATIC if request.sbcs_written else SBCS_STATIC | SBCS_SBACCESS
    checks: list[tuple[str, int]] = [(VERSION, SBCS_SBVERSION), (SBCS_FIELDS, fields)]
    checks += [(check, _WORD & ~SBCS_SBBUSY) for check, _ in register_map]
    errors = {(value & SBCS_SBERROR) >> SBCS_SBERROR_LSB for value in answer.data.allowed}
    if answer.data.mask & SBCS_SBERROR:
        checks += [(SBERROR_CHECKS[e], SBCS_SBERROR) for e in sorted(errors) if e in SBERROR_CHECKS]
    return tuple(checks)


def _tagged(item: _ExpectedAccess, check: str) -> _ExpectedAccess:
    return item if check in item.extra else replace(item, extra=(*item.extra, check))


def _image_fields_match(image: TdrCaptureExpect, word: int, fields: tuple[str, ...]) -> bool:
    """Whether ``word`` equals this image or one alternative on ``fields``."""
    status, addr, data = decode_tdr(word)
    for candidate in (image, *image.alternatives):
        if STATUS in fields and status != int(candidate.status):
            continue
        if ADDRESS in fields and addr != candidate.addr:
            continue
        if DATA in fields and not candidate.data.matches(data):
            continue
        return True
    return False


def _tdr_hex(word: int | None) -> str:
    return "X" if word is None else f"0x{word:011x}"


def _describe_image(image: TdrCaptureExpect) -> str:
    images = (image, *image.alternatives)
    return " or ".join(
        f"address 0x{i.addr:02x} data {_describe_values(i.data.allowed)} on mask "
        f"0x{i.data.mask:x} status {int(i.status)}"
        for i in images
    )


def _matches(expected: Expected, data: int | None) -> bool:
    """A value with no compared bit always matches; an X or Z value matches nothing else."""
    if not expected.mask:
        return True
    return data is not None and expected.matches(data)


def _request_fields(item: _ExpectedRequest) -> tuple[int, int, int]:
    return int(item.request.op), item.request.addr, item.request.data


def _take_first[T](items: list[T], wanted: Callable[[T], bool]) -> T | None:
    """Remove and return the first of ``items`` that is ``wanted``."""
    for index, item in enumerate(items):
        if wanted(item):
            return items.pop(index)
    return None


def _hex(value: int | None) -> str:
    return "X" if value is None else f"0x{value:08x}"


def _describe_dmi(op: int, addr: int, data: int) -> str:
    return f"{TdrOp(op).name} 0x{addr:02x} data 0x{data:08x}"


def _describe(request: _Request) -> str:
    return _describe_dmi(request.op, request.addr, request.data)


def _describe_request(item: _ExpectedRequest) -> str:
    return _describe_dmi(*_request_fields(item))


def _describe_values(values: Iterable[int]) -> str:
    return "|".join(f"0x{value:x}" for value in values)


def _differing_bits(expected: Expected, data: int | None) -> str:
    """The compared bits where ``data`` differs from the allowed value nearest to it."""
    if data is None:
        return "X"
    diffs = ((value ^ data) & expected.mask for value in expected.allowed)
    return f"0x{min(diffs, key=int.bit_count):x}"


def _describe_expected(expected: Expected) -> str:
    if not expected.mask:
        return "any data"
    return f"{_describe_values(expected.allowed)} on mask 0x{expected.mask:x}"


def _describe_access(access: SbaBusAccess) -> str:
    text = f"{access.direction.value} 0x{access.addr:x} size {access.size}"
    if access.wdata is None:
        return text
    return f"{text} WSTRB {access.wstrb} WDATA {_describe_expected(access.wdata)}"


def _describe_transfer(transfer: _Transfer) -> str:
    if transfer.direction is BusDir.READ:
        return f"AR 0x{transfer.addr:x} size {transfer.size}"
    return (
        f"AW 0x{transfer.addr:x} size {transfer.size} W 0x{transfer.data:x} "
        f"WSTRB 0x{transfer.strb:x}"
    )
