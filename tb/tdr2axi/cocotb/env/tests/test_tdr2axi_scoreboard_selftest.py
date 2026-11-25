# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Fault-matrix self-test of ``Tdr2axiScoreboard``; plain Python, no simulator, no DUT.

Each case feeds hand-built expected items and observed handshakes, in the order the bench
delivers them, into a scoreboard that reports to ``Tdr2axiChecker`` with the check under
test required, and grades ``finalize``. ``none`` must pass. ``vacuous`` gives the check no
traffic and must fail CHK-NONVAC. Every other fault must fail the run under the check's own
ID. The faults follow the comparison kind ``trace/vplan.toml`` plans for the check: in-order
faces mismatch, drop, duplicate, spurious, reorder and reset-stale; state faces mismatch and
stale; CHK-SBA-SBBUSY also faces a late and an early sbbusy fall. Each case logs one line

    SBTEST <CHK-ID> compare=<kind> fault=<fault> result=<caught|missed|pass|fail>

which ``fault_matrix.py`` of the dv-scoreboard skill grades; run the module as a script to
print them.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import tomllib
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import Any

COCOTB_DIR = Path(__file__).resolve().parents[2]
PLAN_PATH = COCOTB_DIR.parent / "trace" / "vplan.toml"
CLOCKRESET_PATH = COCOTB_DIR.parent / "trace" / "clockreset.toml"


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


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_package("env", COCOTB_DIR / "env")
sb_mod = importlib.import_module("env.tdr2axi_scoreboard")
sba = importlib.import_module("env.sba_ref_model")
tdr = importlib.import_module("env.tdr_status_model")
checker_mod = _load("tdr2axi_checker", COCOTB_DIR / "tests" / "tdr2axi_checker.py")

LOG = logging.getLogger("tdr2axi.scoreboard.selftest")
ATOMS = ("S-TDR2AXI-SELFTEST",)

ALWAYS = ("none", "vacuous")
KIND_FAULTS = {
    "in-order": ("mismatch", "drop", "duplicate", "spurious", "reorder", "reset-stale"),
    "memory": ("mismatch", "stale", "spurious"),
    "state": ("mismatch", "stale"),
}
EXTRA_FAULTS = {
    sb_mod.SBBUSY: ("late", "early", "early-in-error-window"),
    sb_mod.SYNC_RELEASE: ("late", "early"),
    sb_mod.RESULT_LATENCY: ("late", "early"),
    sb_mod.TCK_STOP_HOLD: ("late", "early"),
    sb_mod.TCK_STOP_HOLD_SS4: ("late", "early"),
    sb_mod.SINGLE_READ: ("wrong-data",),
    sb_mod.BLOCK_READ: ("wrong-data",),
    sb_mod.VERSION: ("authenticated-clear", "authbusy-set"),
    sb_mod.REGISTER_MAP: ("sbaddress-alias", "sbcs-alias", "abstractcs-alias"),
    # The no-bus and no-request halves of the state checks whose context forbids them
    sb_mod.CMDERR: ("spurious-access",),
    sb_mod.MISALIGNED: (
        "spurious-access",
        "spurious-access-after-autoinc",
        "spurious-access-after-error",
    ),
    sb_mod.OVERSIZE: ("spurious-access",),
    sb_mod.BUS_ERROR: ("spurious-access", "sberror-dropped"),
    sb_mod.NDMRESET: ("spurious-access",),
    sb_mod.STICKY_IGNORE: ("spurious-access", "spurious-request"),
    sb_mod.BUSY_UPDATE: ("spurious-access", "spurious-request"),
    sb_mod.OP3_CLEAR: ("spurious-request",),
    sb_mod.SBADDRESS1_ABSENT: ("write-busy", "spurious-access", "next-address-changed"),
    # sbcs read while dmactive = 0 with no access held
    sb_mod.DMACTIVE_CLEAR: ("sbbusyerror-kept", "sbbusy-set"),
}

# DMI ops and responses, DM register addresses (RISC-V External Debug Support 0.13.2)
READ, WRITE, CLEAR = 1, 2, 3
OK, BUSY_RESP = 0, 3
DMSTATUS, HARTINFO, DATA0, DATA1 = 0x11, 0x12, 0x04, 0x05
SBCS_BUSY = 1 << 21
SBCS_BUSYERROR = 1 << 22
SBCS_READONADDR = 1 << 20
SBCS_AUTOINC = 1 << 16
SBCS_READONDATA = 1 << 15
SBACCESS16 = 1 << 17
SBACCESS32 = 2 << 17
SBACCESS128 = 4 << 17
SBACCESS_FIELD = 7 << 17
SBERROR_LSB = 12
DMCONTROL_NDMRESET = 1 << 1
# dmstatus: version 2, allunavail and anyunavail 1, authenticated 1, authbusy 0
DMSTATUS_SPEC = 0x0040_3082
DMSTATUS_AUTHENTICATED = 1 << 7
DMSTATUS_AUTHBUSY = 1 << 6
ABSTRACTCS_CMDERR_LSB = 8
STATUS_FAILED = 2  # a capture status the bridge never shows
# The result window of T-TDR2AXI-resp-to-tdr at SYNC_STAGES 3, in TCK edges
RESULT_EARLIEST, RESULT_LATEST = 5, 6
# The result windows of each SYNC_STAGES, by timing item of the clock-reset model
RESULT_TIMING = {3: "T-TDR2AXI-resp-to-tdr", 4: "T-TDR2AXI-resp-to-tdr-ss4"}
SBCS_IDLE = 0x2004_0E0F  # an arbitrary sbcs read value with sbbusy = 0
WORD = 0xFFFF_FFFF
BUS_BYTES = 8
# Bus reads above 4 GiB, so sbaddress1 carries address bits; a wrong address for each
# compared field: an offset, and the high word lost
READ_BASE = 0x1_0000_0100
READ_OFFSET_WRONG = READ_BASE + 0x40
READ_HIGH_LOST = READ_BASE & WORD
READ_ELEMENT = 0x5A5A_1234  # the bytes an sbdata0 read returns from a bus read

Event = tuple[Any, ...]


@dataclass(frozen=True)
class _Addr:
    """The AR or AW fields the scoreboard reads."""

    addr: int
    size: int


@dataclass(frozen=True)
class _W:
    """The W fields the scoreboard reads."""

    strb: int
    data: int


@dataclass
class Scenario:
    """A clean event list and the observed events its faults act on.

    ``t1`` and ``t2`` index two observed items of the stream under test; ``wrong`` replaces
    ``t1`` for mismatch, ``stale`` for stale, and ``spurious`` is appended for spurious.
    ``custom`` holds whole event lists for faults no generic edit expresses.
    """

    events: list[Event]
    t1: int
    t2: int | None = None
    wrong: Event | None = None
    stale: Event | None = None
    spurious: Event | None = None
    reset_scope: str = sb_mod.RST_ARST_BUS
    lite: bool = False
    sync_stages: int = 3
    addr_width: int = 64
    custom: dict[str, list[Event]] = field(default_factory=dict)


# --- Expected items and event builders ---------------------------------------------------
def tdr_word(op: int, addr: int, data: int) -> int:
    """The 41-bit update word: address [40:34], data [33:2], op [1:0]."""
    return (addr << 34) | ((data & WORD) << 2) | op


def request(op: int, addr: int, data: int) -> Any:
    return tdr.DmiRequest(tdr.TdrOp(op), addr, data & WORD, ATOMS)


def exact(value: int, mask: int = WORD) -> Any:
    return sba.Expected((value & mask,), mask, ATOMS)


def answer(data: Any = None, access: Any = None, *, busy: bool = False) -> Any:
    return sba.DmAnswer(busy, data or sba.Expected.free(ATOMS), access, ATOMS)


def open_answer() -> Any:
    """An answer whose response code the model leaves open."""
    return sba.DmAnswer(False, sba.Expected.free(ATOMS), None, ATOMS, open_status=True)


def read_access(addr: int, size: int, *, lite: bool = False) -> Any:
    return sba.SbaBusAccess(sba.BusDir.READ, addr, None if lite else size, None, None, ATOMS)


def write_access(addr: int, size: int, data: int, *, lite: bool = False) -> Any:
    nbytes = 1 << size
    lane = addr % BUS_BYTES
    width = (1 << (8 * nbytes)) - 1
    wdata = sba.Expected(((data & width) << (8 * lane),), width << (8 * lane), ATOMS)
    wstrb = ((1 << nbytes) - 1) << lane
    return sba.SbaBusAccess(sba.BusDir.WRITE, addr, None if lite else size, wstrb, wdata, ATOMS)


def update(op: int, addr: int, data: int, *, sends: bool = True) -> Event:
    """An update edge and the idle edge after it; the model predicts the request or none."""
    return ("update", tdr_word(op, addr, data), request(op, addr, data) if sends else None)


def held(op: int, addr: int, data: int, *, sends: bool = True) -> Event:
    """An update edge whose strobe stays high at the next edge."""
    return ("held", tdr_word(op, addr, data), request(op, addr, data) if sends else None)


def dmi(op: int, addr: int, data: int, ans: Any, *, response: tuple[int, int] | None) -> Event:
    """A DMI request handshake, the DM answer the model predicts and the DM's response."""
    return ("dmi", op, addr, data, ans, response)


def access(op: int, addr: int, data: int, ans: Any, *, response: tuple[int, int]) -> list[Event]:
    """One whole DMI operation: its update edge, then its handshake and response."""
    return [update(op, addr, data), dmi(op, addr, data, ans, response=response)]


def write(addr: int, data: int, bus: Any = None) -> list[Event]:
    return access(WRITE, addr, data, answer(access=bus), response=(0, OK))


def read(addr: int, value: int, *, mask: int = WORD) -> list[Event]:
    return access(READ, addr, 0, answer(exact(value, mask)), response=(value, OK))


def activate() -> list[Event]:
    return write(sba.DMCONTROL, 1)


def upd(
    op: int, addr: int, data: int, *, sends: bool = True, pending: bool = False, busy: bool = False
) -> Event:
    """An update edge and the idle edge after it, with the model's bridge before it: an
    operation pending, or the sticky busy set."""
    return ("upd", op, addr, data, sends, pending, busy)


def cap_word(addr: int, data: int, status: int) -> int:
    """The 41-bit capture word: address [40:34], data [33:2], status [1:0]."""
    return tdr_word(status, addr, data)


def cap(
    addr: int,
    data: int,
    status: int = OK,
    *,
    seen: int | None = None,
    read_pending: bool = False,
    write_pending: bool = False,
) -> Event:
    """A capture edge: the image the model predicts and the word o_dmi_tdr holds, the image
    itself unless ``seen`` is given."""
    word = cap_word(addr, data, status) if seen is None else seen
    return ("cap", addr, data, status, word, read_pending, write_pending)


def seen_as(event: Event, word: int) -> Event:
    """A capture edge whose o_dmi_tdr holds ``word``."""
    return (*event[:4], word, *event[5:])


def wr(addr: int, size: int, data: int) -> Event:
    """One observed AW and W, data on the lanes ``addr`` selects."""
    nbytes = 1 << size
    lane = addr % BUS_BYTES
    strb = ((1 << nbytes) - 1) << lane
    return ("wr", addr, size, strb, (data & ((1 << (8 * nbytes)) - 1)) << (8 * lane))


# --- Feeding the scoreboard --------------------------------------------------------------
def _feed_update(sb: Any, event: Event) -> None:
    kind, word, predicted = event
    sb.write_update(tdr.TdrUpdateExpect(predicted, ATOMS))
    sb.write_tck_edge(update_word=word, capture=False, captured=None)
    if kind == "update":
        sb.write_tck_edge(update_word=None, capture=False, captured=None)


def _feed_capture(sb: Any, event: Event) -> None:
    _, status, pending, seen = event
    image = tdr.TdrCaptureExpect(0, tdr.TdrField.exact(0), tdr.TdrStatus(status), ATOMS)
    sb.write_capture_expect(image, read_pending=pending)
    sb.write_tck_edge(update_word=None, capture=True, captured=seen)


def _feed_dmi(sb: Any, event: Event) -> None:
    _, op, addr, data, ans, response = event
    sb.write_dmi_request(op, addr, data)
    sb.write_dm_answer(ans)
    if response is not None:
        sb.write_dmi_response(*response)


def _feed_write(sb: Any, event: Event) -> None:
    _, addr, size, strb, data = event
    sb.write_bus("aw", _Addr(addr, size))
    sb.write_bus("w", _W(strb, data))


def _feed_upd(sb: Any, event: Event) -> None:
    _, op, addr, data, sends, pending, busy = event
    predicted = request(op, addr, data) if sends else None
    sb.write_update(tdr.TdrUpdateExpect(predicted, ATOMS), pending=pending, busy=busy)
    sb.write_tck_edge(update_word=tdr_word(op, addr, data), capture=False, captured=None)
    sb.write_tck_edge(update_word=None, capture=False, captured=None)


def _feed_cap(sb: Any, event: Event) -> None:
    _, addr, data, status, word, read_pending, write_pending = event
    image = tdr.TdrCaptureExpect(addr, tdr.TdrField.exact(data), tdr.TdrStatus(status), ATOMS)
    sb.write_capture_expect(image, read_pending=read_pending, write_pending=write_pending)
    sb.write_tck_edge(update_word=None, capture=True, captured=word)


def _feed_watch(sb: Any, event: Event) -> None:
    _, check, earliest, latest, timing = event
    window = tdr.TdrResultWindow(earliest, latest, "tck", timing, ATOMS)
    sb.watch_results(check, window)


def _feed_arrival(sb: Any, event: Event) -> None:
    _, edges, is_read, word, resp = event
    sb.write_result_arrival(edges=edges, read=is_read, word=word, resp=resp)


FEEDERS: dict[str, Callable[[Any, Event], None]] = {
    "upd": _feed_upd,
    "cap": _feed_cap,
    "watch": _feed_watch,
    "arrival": _feed_arrival,
    "mem": lambda sb, event: sb.write_memory(*event[1:]),
    "hold-start": lambda sb, event: sb.write_hold_start(event[1]),
    "hold-sample": lambda sb, event: sb.write_hold_sample(*event[1:]),
    "hold-end": lambda sb, event: sb.write_hold_end(),
    "event-expect": lambda sb, event: sb.write_event_expect(*event[1:]),
    "event": lambda sb, event: sb.write_event(*event[1:]),
    "trst-end": lambda sb, event: sb.end_trst(),
    "update": _feed_update,
    "held": _feed_update,
    "capture": _feed_capture,
    "dmi": _feed_dmi,
    "req": lambda sb, event: sb.write_dmi_request(*event[1:]),
    "resp": lambda sb, event: sb.write_dmi_response(*event[1:]),
    "ar": lambda sb, event: sb.write_bus("ar", _Addr(*event[1:])),
    "wr": _feed_write,
    "bresp": lambda sb, event: sb.write_bus_response(error=event[1]),
    "reset": lambda sb, event: sb.reset(event[1]),
}


def feed(sb: Any, event: Event) -> None:
    """Deliver one event the way the bench does."""
    FEEDERS[event[0]](sb, event)


def verdicts(start: list[Event], events: list[Event], check: str) -> list[bool]:
    """The verdicts ``check`` records while ``events`` are fed after ``start``."""
    records: list[tuple[str, bool]] = []

    def report(check_id: str, *, passed: bool, detail: str) -> None:
        del detail
        records.append((check_id, passed))

    sb = sb_mod.Tdr2axiScoreboard(sb_mod.Tdr2axiScoreboardCfg(lite=False), report)
    for event in start:
        feed(sb, event)
    before = len(records)
    for event in events:
        feed(sb, event)
    return [passed for check_id, passed in records[before:] if check_id == check]


def observed_only(event: Event) -> Event:
    """The DUT side of an event: a DMI handshake with no answer the model would give."""
    if event[0] == "dmi":
        return ("req", *event[1:4])
    return event


def swap_observed(first: Event, second: Event) -> tuple[Event, Event]:
    """Two observed items exchanged; a DMI handshake keeps the answer of its slot."""
    if first[0] == "dmi" and second[0] == "dmi":
        return (("dmi", *second[1:4], *first[4:]), ("dmi", *first[1:4], *second[4:]))
    return second, first


def _replace(events: list[Event], index: int, event: Event) -> list[Event]:
    return [*events[:index], event, *events[index + 1 :]]


def _reorder(scenario: Scenario, events: list[Event]) -> list[Event]:
    t1, t2 = scenario.t1, _needed(scenario.t2, "reorder")
    first, second = swap_observed(events[t1], events[t2])
    return _replace(_replace(events, t1, first), t2, second)


FAULTS: dict[str, Callable[[Scenario, list[Event]], list[Event]]] = {
    "none": lambda _sc, events: events,
    "vacuous": lambda _sc, _events: [],
    "mismatch": lambda sc, events: _replace(events, sc.t1, _needed(sc.wrong, "mismatch")),
    "stale": lambda sc, events: _replace(events, sc.t1, _needed(sc.stale, "stale")),
    "drop": lambda sc, events: [*events[: sc.t1], *events[sc.t1 + 1 :]],
    "duplicate": lambda sc, events: [
        *events[: sc.t1 + 1],
        observed_only(events[sc.t1]),
        *events[sc.t1 + 1 :],
    ],
    "spurious": lambda sc, events: [*events, _needed(sc.spurious, "spurious")],
    "reorder": _reorder,
    "reset-stale": lambda sc, events: [
        *events[: sc.t1],
        ("reset", sc.reset_scope),
        observed_only(events[sc.t1]),
    ],
}


def faulted(scenario: Scenario, fault: str) -> list[Event]:
    """The scenario's events with ``fault`` injected."""
    if fault in scenario.custom:
        return scenario.custom[fault]
    return FAULTS[fault](scenario, list(scenario.events))


def _needed[T](value: T | None, fault: str) -> T:
    if value is None:
        raise ValueError(f"scenario has no {fault} fault")
    return value


def run_case(check: str, scenario: Scenario, fault: str) -> str:
    """Feed one case and grade it: pass or fail for ``none``, caught or missed otherwise."""
    checker = checker_mod.Tdr2axiChecker(
        name=f"{check}:{fault}", required_ids={checker_mod.NONVAC, check}
    )
    sb = sb_mod.Tdr2axiScoreboard(
        sb_mod.Tdr2axiScoreboardCfg(
            lite=scenario.lite, sync_stages=scenario.sync_stages, addr_width=scenario.addr_width
        ),
        report=checker.record,
    )
    for event in faulted(scenario, fault):
        feed(sb, event)
    sb.drain()
    try:
        checker.finalize()
    except checker_mod.Tdr2axiCheckError as error:
        failures = str(error).splitlines()
    else:
        failures = []
    if fault == "none":
        return "fail" if failures else "pass"
    if fault == "vacuous":
        nonvac = f"{checker_mod.NONVAC} FAIL"
        hit = any(line.startswith(nonvac) and check in line.split() for line in failures)
    else:
        hit = any(line.startswith(f"{check} FAIL") for line in failures)
    return "caught" if hit else "missed"


# --- Scenarios, one per scoreboard check -------------------------------------------------
def op_decode() -> Scenario:
    """Two predicted requests, then their handshakes; the faults act on the first."""
    events = [update(READ, DMSTATUS, 0), update(WRITE, DATA0, 0x1234)]
    t1 = len(events)
    events.append(dmi(READ, DMSTATUS, 0, answer(exact(0x0040_0C82)), response=(0x0040_0C82, OK)))
    t2 = len(events)
    events.append(dmi(WRITE, DATA0, 0x1234, answer(), response=(0, OK)))
    return Scenario(
        events,
        t1,
        t2,
        wrong=dmi(READ, HARTINFO, 0, answer(exact(0x0040_0C82)), response=(0x0040_0C82, OK)),
        spurious=("req", READ, DATA1, 0),
        reset_scope=sb_mod.RST_ARST_TCK,
    )


def update_edge() -> Scenario:
    """Two strobes each held over two edges, each sending one request and a busy capture."""
    events = [
        held(WRITE, DATA0, 0x11),
        update(WRITE, DATA0, 0x11, sends=False),
        ("capture", tdr.TdrStatus.BUSY, False, BUSY_RESP),
        held(WRITE, DATA1, 0x22),
        update(WRITE, DATA1, 0x22, sends=False),
    ]
    t1 = len(events)
    events.append(dmi(WRITE, DATA0, 0x11, answer(), response=(0, OK)))
    t2 = len(events)
    events.append(dmi(WRITE, DATA1, 0x22, answer(), response=(0, OK)))
    return Scenario(
        events,
        t1,
        t2,
        wrong=dmi(WRITE, DATA0, 0x12, answer(), response=(0, OK)),
        spurious=("req", WRITE, DATA0, 0x33),
        reset_scope=sb_mod.RST_ARST_TCK,
    )


def ratio_invariant() -> Scenario:
    """Two reads answered in order; the faults act on the first response."""
    events = [
        update(READ, DMSTATUS, 0),
        dmi(READ, DMSTATUS, 0, answer(exact(0x0040_0C82)), response=None),
        update(READ, HARTINFO, 0),
        dmi(READ, HARTINFO, 0, answer(exact(0x0011_2380)), response=None),
    ]
    t1 = len(events)
    events.append(("resp", 0x0040_0C82, OK))
    t2 = len(events)
    events.append(("resp", 0x0011_2380, OK))
    return Scenario(events, t1, t2, wrong=("resp", 0x0040_0C83, OK), spurious=("resp", 0x5, OK))


def sbbusyerror() -> Scenario:
    """An sbdata0 read during an access is answered busy; stale is busy after the access."""
    start = [
        *activate(),
        *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR),
        *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
        update(READ, sba.SBDATA0, 0),
    ]
    t1 = len(start)
    events = [
        *start,
        dmi(READ, sba.SBDATA0, 0, answer(busy=True), response=(0, BUSY_RESP)),
        ("ar", 0x100, 2),
        ("bresp", False),
    ]
    stale = [
        *start[:-1],
        ("ar", 0x100, 2),
        ("bresp", False),
        update(READ, sba.SBDATA0, 0),
        dmi(READ, sba.SBDATA0, 0, answer(exact(0xCAFE)), response=(0, BUSY_RESP)),
    ]
    return Scenario(
        events,
        t1,
        wrong=dmi(READ, sba.SBDATA0, 0, answer(busy=True), response=(0xCAFE, OK)),
        custom={"stale": stale},
    )


def sbbusy() -> Scenario:
    """sbcs reads before, during and after one access, in the window the predictor publishes."""
    busy, idle = SBCS_IDLE | SBCS_BUSY, SBCS_IDLE
    window = sba.Expected((busy, idle), WORD, ATOMS)
    start = [*activate(), *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR)]
    idle_read = len(start) + 1
    start += read(sba.SBCS, idle)
    access_start = [*start, *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2))]
    pending_read = len(access_start) + 1
    events = [
        *access_start,
        *read(sba.SBCS, busy),
        ("ar", 0x100, 2),
        ("bresp", False),
        *access(READ, sba.SBCS, 0, answer(window), response=(busy, OK)),
        update(READ, sba.SBCS, 0),
    ]
    last_read = len(events)
    events.append(dmi(READ, sba.SBCS, 0, answer(exact(idle)), response=(idle, OK)))

    def observed(index: int, value: int) -> list[Event]:
        changed = list(events)
        _, op, addr, data, ans, _ = changed[index]
        changed[index] = dmi(op, addr, data, ans, response=(value, OK))
        return changed

    stale = [
        *access_start,
        ("reset", sb_mod.RST_ARST_BUS),
        *access(READ, sba.SBCS, 0, answer(exact(idle)), response=(busy, OK)),
    ]
    # An error response's window: sberror 2 may be set before sbbusy falls
    error = 2 << SBERROR_LSB
    error_window = sba.Expected((busy, busy | error, idle | error), WORD, ATOMS)
    early_in_error_window = [
        *access_start,
        *access(READ, sba.SBCS, 0, answer(error_window), response=(idle, OK)),
    ]
    return Scenario(
        events,
        idle_read,
        custom={
            "mismatch": observed(idle_read, busy),
            "stale": stale,
            "late": observed(last_read, busy),
            "early": observed(pending_read, idle),
            "early-in-error-window": early_in_error_window,
        },
    )


def dmactive_clear() -> Scenario:
    """sbaddress0 and sbcs read while dmactive = 0 with no access held, then sbcs read after
    dmactive is set again. sbbusyerror-kept and sbbusy-set read sbcs while dmactive = 0 with
    that bit set."""
    start = [*activate(), *write(sba.SBADDRESS0, 0x100), *write(sba.DMCONTROL, 0)]
    t1 = len(start) + 1
    events = [
        *start,
        *read(sba.SBADDRESS0, 0),
        *read(sba.SBCS, 0, mask=SBCS_BUSY | SBCS_BUSYERROR),
        *activate(),
        *read(sba.SBCS, SBCS_IDLE),
    ]
    probe = events[t1]
    inactive = t1 + 2
    sbcs = events[inactive]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(0xDEAD, OK)),
        stale=dmi(*probe[1:5], response=(0x100, OK)),
        custom={
            "sbbusyerror-kept": _replace(
                events, inactive, dmi(*sbcs[1:5], response=(SBCS_BUSYERROR, OK))
            ),
            "sbbusy-set": _replace(events, inactive, dmi(*sbcs[1:5], response=(SBCS_BUSY, OK))),
        },
    )


SBCS_INACTIVE = 0x0004_0000  # an sbcs read value with sbbusy = 0 while dmactive = 0


def dmactive_inflight_hold() -> Scenario:
    """dmactive cleared with a bus read in flight: sbcs reads its held value with sbbusy = 1,
    an open answer to a hold-time sbdata0 read grades nothing, and after the response
    sbaddress0 reads 0 before dmactive is set again. stale reads the cleared sbcs."""
    sbcs = SBCS_IDLE | SBACCESS32 | SBCS_READONADDR
    start = [
        *activate(),
        *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR),
        *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
        *write(sba.DMCONTROL, 0),
        *access(READ, sba.SBDATA0, 0, open_answer(), response=(0, BUSY_RESP)),
    ]
    t1 = len(start) + 1
    events = [
        *start,
        *read(sba.SBCS, sbcs | SBCS_BUSY),
        ("ar", 0x100, 2),
        ("bresp", False),
        *read(sba.SBCS, 0, mask=SBCS_BUSY),
        *read(sba.SBADDRESS0, 0),
    ]
    probe = events[t1]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(sbcs, OK)),
        stale=dmi(*probe[1:5], response=(SBCS_INACTIVE, OK)),
    )


def dmactive_rearm() -> Scenario:
    """dmactive cleared and set again before a bus read with sbautoincrement is answered:
    sbaddress0 reads the advanced address; stale reads it without the increment."""
    start = [
        *activate(),
        *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR | SBCS_AUTOINC),
        *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
        *write(sba.DMCONTROL, 0),
        *activate(),
        ("ar", 0x100, 2),
        ("bresp", False),
    ]
    t1 = len(start) + 1
    events = [*start, *read(sba.SBADDRESS0, 0x104)]
    probe = events[t1]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(0, OK)),
        stale=dmi(*probe[1:5], response=(0x100, OK)),
    )


def op3_keeps_dm() -> Scenario:
    """Op 3 after two sbaddress0 writes, then its capture and an sbaddress0 read."""
    start = [
        *activate(),
        *write(sba.SBADDRESS0, 0x100),
        *write(sba.SBADDRESS0, 0x200),
        update(CLEAR, 0, 0, sends=False),
        ("capture", tdr.TdrStatus.SUCCESS, False, OK),
    ]
    t1 = len(start) + 1
    events = [*start, *read(sba.SBADDRESS0, 0x200)]
    probe = events[t1]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(0, OK)),
        stale=dmi(*probe[1:5], response=(0x100, OK)),
    )


def no_inc_after_error(*, trigger: str = "addr") -> Scenario:
    """A read with sbautoincrement fails with an error response; sbaddress0 is read back.
    The read is started by an sbaddress0 write (``addr``) or by an sbdata0 read with
    sbreadondata (``data``, S-TDR2AXI-0094)."""
    if trigger == "addr":
        started = [
            *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR | SBCS_AUTOINC),
            *write(sba.SBADDRESS0, 0x140, read_access(0x140, 2)),
        ]
    else:
        sbdata0 = answer(exact(0x55), read_access(0x140, 2))
        started = [
            *write(sba.SBCS, SBACCESS32 | SBCS_READONDATA | SBCS_AUTOINC),
            *write(sba.SBADDRESS0, 0x140),
            *access(READ, sba.SBDATA0, 0, sbdata0, response=(0x55, OK)),
        ]
    start = [*activate(), *started, ("ar", 0x140, 2), ("bresp", True)]
    t1 = len(start) + 1
    events = [*start, *read(sba.SBADDRESS0, 0x140)]
    probe = events[t1]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(0x144, OK)),
        stale=dmi(*probe[1:5], response=(0x100, OK)),
    )


def busy_capture() -> Scenario:
    """A capture with a read pending, a sticky busy capture, then op 3 and its capture."""
    events = [update(READ, DATA0, 0)]
    t1 = len(events)
    events += [
        ("capture", tdr.TdrStatus.BUSY, True, BUSY_RESP),
        dmi(READ, DATA0, 0, answer(exact(0x77)), response=(0x77, OK)),
        ("capture", tdr.TdrStatus.BUSY, False, BUSY_RESP),
        update(CLEAR, 0, 0, sends=False),
        ("capture", tdr.TdrStatus.SUCCESS, False, OK),
    ]
    return Scenario(
        events,
        t1,
        wrong=("capture", tdr.TdrStatus.BUSY, True, 2),
        stale=("capture", tdr.TdrStatus.BUSY, True, OK),
    )


def bus_reads(
    check_addr: int, *, size2: int = 2, trigger: str = "addr", wrong: Event | None = None
) -> Scenario:
    """Two reads, started by sbaddress0 writes or by sbdata0 reads with sbreadondata, and an
    sbdata0 read returning read bytes; wrong-data changes those bytes."""
    first, second = READ_BASE, READ_BASE + 8
    element = answer(exact(READ_ELEMENT), read_access(second, size2))
    if trigger == "addr":
        events = [
            *activate(),
            *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR),
            *write(sba.SBADDRESS1, first >> 32),
            *write(sba.SBADDRESS0, first & WORD, read_access(first, 2)),
            *write(sba.SBADDRESS0, second & WORD, read_access(second, size2)),
        ]
    else:
        events = [
            *activate(),
            *write(sba.SBCS, SBACCESS32 | SBCS_READONDATA | SBCS_AUTOINC),
            *write(sba.SBADDRESS1, first >> 32),
            *write(sba.SBADDRESS0, first & WORD),
            *access(READ, sba.SBDATA0, 0, answer(access=read_access(first, 2)), response=(0, OK)),
            *access(READ, sba.SBDATA0, 0, element, response=(READ_ELEMENT, OK)),
        ]
    data_read = len(events) - 1
    t1 = len(events)
    events += [("ar", first, 2), ("bresp", False)]
    t2 = len(events)
    events += [("ar", second, size2), ("bresp", False)]
    if trigger == "addr":
        data_read = len(events) + 1
        events += read(sba.SBDATA0, READ_ELEMENT)
    wrong_data = list(events)
    wrong_data[data_read] = dmi(*events[data_read][1:5], response=(READ_ELEMENT ^ 1, OK))
    return Scenario(
        events,
        t1,
        t2,
        wrong=wrong or ("ar", check_addr, 2),
        spurious=("ar", READ_BASE + 0x10, 2),
        custom={"wrong-data": wrong_data},
    )


def bus_writes(
    *,
    autoinc: bool = False,
    lite: bool = False,
    size: int = 2,
    wrong: Callable[[int, int, int], Event] | None = None,
) -> Scenario:
    """Two writes started by sbdata0 writes, with sbautoincrement or at two addresses."""
    first = 0x1_0000_0101 if size == 0 else 0x1_0000_0104
    second = first + (1 << size)
    data1, data2 = 0xA5, 0x5A
    if size:
        data1, data2 = 0x1122_3344, 0x5566_7788
    events = [
        *activate(),
        *write(sba.SBCS, (size << 17) | (SBCS_AUTOINC if autoinc else 0)),
        *write(sba.SBADDRESS1, first >> 32),
        *write(sba.SBADDRESS0, first & WORD),
        *write(sba.SBDATA0, data1, write_access(first, size, data1, lite=lite)),
    ]
    if not autoinc:
        events += write(sba.SBADDRESS0, second & WORD)
    events += write(sba.SBDATA0, data2, write_access(second, size, data2, lite=lite))
    t1 = len(events)
    events += [wr(first, size, data1), ("bresp", False)]
    t2 = len(events)
    events += [wr(second, size, data2), ("bresp", False)]
    bad = wrong(first, size, data1) if wrong else wr(first, size, data1 ^ 0xFF)
    return Scenario(
        events,
        t1,
        t2,
        wrong=bad,
        spurious=wr(second + (1 << size), size, 0x99),
        lite=lite,
    )


def _wide_strobe(addr: int, size: int, data: int) -> Event:
    _, addr, size, _, data_lanes = wr(addr, size, data)
    return ("wr", addr, size, 0xFF, data_lanes)


def _lane_zero(addr: int, size: int, data: int) -> Event:
    _, addr, size, strb, _ = wr(addr, size, data)
    return ("wr", addr, size, strb, data)


# --- Scenarios of the further comparisons ------------------------------------------------
def dm_field(prefix: list[Event], addr: int, value: int, *, wrong: int, stale: int) -> Scenario:
    """A read of DM register ``addr`` after ``prefix``; mismatch answers ``wrong``, and stale
    answers ``stale``, the value of the state before the one ``prefix`` sets up."""
    t1 = len(prefix) + 1
    events = [*prefix, *read(addr, value)]
    probe = events[t1]
    return Scenario(
        events,
        t1,
        wrong=dmi(*probe[1:5], response=(wrong, OK)),
        stale=dmi(*probe[1:5], response=(stale, OK)),
    )


def register_map() -> Scenario:
    """A dmcontrol read after dmactive is set. Each alias fault answers a read of another
    mapped register with a value the field checks do not cover: sbaddress0 with
    sbaddress1's value, sbcs with sbreadonaddr flipped and abstractcs with datacount 1. The
    first sbcs read after dmactive is set belongs to CHK-DM-DMACTIVE-CLEAR, so the prefix
    makes it."""
    scenario = dm_field(activate(), sba.DMCONTROL, 1, wrong=0x8000_0001, stale=0)
    prefix = [
        *activate(),
        *read(sba.SBCS, SBCS_IDLE),
        *write(sba.SBADDRESS1, 0x2222_2222),
        *write(sba.SBADDRESS0, 0x100),
    ]

    def aliased(addr: int, value: int, seen: int) -> list[Event]:
        return [*prefix, *access(READ, addr, 0, answer(exact(value)), response=(seen, OK))]

    scenario.custom.update(
        {
            "sbaddress-alias": aliased(sba.SBADDRESS0, 0x100, 0x2222_2222),
            "sbcs-alias": aliased(sba.SBCS, SBCS_IDLE, SBCS_IDLE ^ SBCS_READONADDR),
            # The bench predicts abstractcs on cmderr alone, so only this row's whole-word
            # prediction lets a datacount alias fail CHK-DM-REGISTER-MAP.
            "abstractcs-alias": aliased(sba.ABSTRACTCS, 0, 1),
        }
    )
    return scenario


def modelled_dmstatus(seen: int) -> list[Event]:
    """dmactive set, then a dmstatus read answering ``seen``, each against the answer the
    SBA model predicts."""
    model = sba.SbaRefModel(sba.SbaModelCfg(lite=False, addr_width=64, data_width=64))
    enable = model.predict_dmi_write(sba.DMCONTROL, 1)
    status = model.predict_dmi_read(DMSTATUS)
    return [
        *access(WRITE, sba.DMCONTROL, 1, enable, response=(0, OK)),
        *access(READ, DMSTATUS, 0, status, response=(seen, OK)),
    ]


def dm_version() -> Scenario:
    """A dmstatus read; authenticated-clear and authbusy-set answer it with authenticated 0
    or authbusy 1 against the SBA model's own prediction."""
    scenario = dm_field([], DMSTATUS, 0x0040_0C82, wrong=0x0040_0C83, stale=0x0040_0C80)
    scenario.custom["authenticated-clear"] = modelled_dmstatus(
        DMSTATUS_SPEC & ~DMSTATUS_AUTHENTICATED
    )
    scenario.custom["authbusy-set"] = modelled_dmstatus(DMSTATUS_SPEC | DMSTATUS_AUTHBUSY)
    return scenario


def sbaddress1_absent() -> Scenario:
    """At AXI_ADDR_WIDTH 32: sbaddress0 written twice, sbaddress1 written all ones with
    sbbusy and sbbusyerror clear, sbaddress1, sbaddress0 and sbcs read, and an sbdata0 read
    with sbreadondata that reads at the address sbaddress0 holds; then an sbaddress0 write
    with sbreadonaddr starts a read, an sbaddress1 write collides with it and is answered
    busy, and sbcs reads sbbusyerror set. The collision is CHK-SBA-SBBUSYERROR's alone.

    Mismatch reads sbaddress1 as written; stale reads sbaddress0 as written before its last
    write; write-busy answers the first sbaddress1 write busy; spurious-access adds an AR
    after it; next-address-changed moves the sbdata0 read's AR. Collision-success answers
    the colliding write with success, and sbbusyerror-clear reads sbcs after it with
    sbbusyerror clear, for the unit test that neither grades this check."""
    on_data = SBACCESS32 | SBCS_READONDATA
    on_addr = SBACCESS32 | SBCS_READONADDR
    events = [
        *activate(),
        *write(sba.SBCS, on_data),
        *write(sba.SBADDRESS0, 0x80),
        *write(sba.SBADDRESS0, 0x100),
    ]
    ones = len(events) + 1
    events += access(WRITE, sba.SBADDRESS1, WORD, answer(), response=(0, OK))
    t1 = len(events) + 1
    events += read(sba.SBADDRESS1, 0)
    sbaddress0 = len(events) + 1
    events += [*read(sba.SBADDRESS0, 0x100), *read(sba.SBCS, SBCS_IDLE | on_data)]
    events += access(
        READ, sba.SBDATA0, 0, answer(exact(0), read_access(0x100, 2)), response=(0, OK)
    )
    next_ar = len(events)
    events += [("ar", 0x100, 2), ("bresp", False)]
    events += [*write(sba.SBCS, on_addr), *write(sba.SBADDRESS0, 0x140, read_access(0x140, 2))]
    collide = len(events) + 1
    events += access(WRITE, sba.SBADDRESS1, WORD, answer(busy=True), response=(0, BUSY_RESP))
    events += [("ar", 0x140, 2), ("bresp", False)]
    after = len(events) + 1
    events += read(sba.SBCS, SBCS_IDLE | on_addr | SBCS_BUSYERROR)

    def edited(index: int, event: Event) -> list[Event]:
        return _replace(events, index, event)

    def answered(index: int, response: tuple[int, int]) -> list[Event]:
        return edited(index, dmi(*events[index][1:5], response=response))

    return Scenario(
        events,
        t1,
        wrong=dmi(*events[t1][1:5], response=(WORD, OK)),
        addr_width=32,
        custom={
            "stale": answered(sbaddress0, (0x80, OK)),
            "write-busy": answered(ones, (0, BUSY_RESP)),
            "spurious-access": [*events[: ones + 1], ("ar", 0x100, 2), *events[ones + 1 :]],
            "next-address-changed": edited(next_ar, ("ar", 0x104, 2)),
            "collision-success": answered(collide, (0, OK)),
            "sbbusyerror-clear": answered(after, (SBCS_IDLE | on_addr, OK)),
        },
    )


def forbid(
    scenario: Scenario, at: int, *, access: Event | None = None, req: Event | None = None
) -> Scenario:
    """``scenario`` with spurious-access, a bus transaction, or spurious-request, a DMI
    request, inserted at ``at``, where the check's context forbids it."""
    events = scenario.events
    custom = dict(scenario.custom)
    for fault, event in (("spurious-access", access), ("spurious-request", req)):
        if event is not None:
            custom[fault] = [*events[:at], event, *events[at:]]
    return replace(scenario, custom=custom)


def sbcs_error(prefix: list[Event], sbcs: int, sberror: int, wrong: int) -> Scenario:
    """An sbcs read with sberror ``sberror`` after ``prefix``; stale reads sberror 0."""
    value = sbcs | (sberror << SBERROR_LSB)
    return dm_field(
        prefix,
        sba.SBCS,
        value,
        wrong=sbcs | (wrong << SBERROR_LSB),
        stale=sbcs,
    )


def bus_error() -> Scenario:
    """A bus read answered with an error response, then sbcs with sberror 2.

    sberror-dropped reads sbcs in the sbbusy window, where sberror 2 may be set before
    sbbusy falls, and answers sbbusy 0 with sberror 0."""
    sbcs = SBACCESS32 | SBCS_READONADDR
    prefix = [
        *activate(),
        *write(sba.SBCS, sbcs),
        *write(sba.SBADDRESS0, 0x140, read_access(0x140, 2)),
        ("ar", 0x140, 2),
        ("bresp", True),
    ]
    idle, error = SBCS_IDLE | sbcs, 2 << SBERROR_LSB
    window = sba.Expected((idle | SBCS_BUSY, idle | SBCS_BUSY | error, idle | error), WORD, ATOMS)
    scenario = sbcs_error(prefix, idle, 2, 3)
    scenario.custom["sberror-dropped"] = [
        *prefix,
        *access(READ, sba.SBCS, 0, answer(window), response=(idle, OK)),
    ]
    return forbid(scenario, scenario.t1 - 1, access=("ar", 0x144, 2))


def misaligned() -> Scenario:
    """A 32-bit read that an sbaddress0 write to an odd address starts makes no access;
    sbcs reads sberror 3. spurious-access is an AR from that read; spurious-access-after-
    autoinc an AW and W from a 32-bit write at the address a byte write with
    sbautoincrement left misaligned; spurious-access-after-error an AW and W from a 16-bit
    write at the address of a byte write with sbautoincrement answered with an error,
    which keeps sbaddress0 there."""
    sbcs = SBACCESS32 | SBCS_READONADDR
    prefix = [*activate(), *write(sba.SBCS, sbcs), *write(sba.SBADDRESS0, 0x141)]
    scenario = forbid(
        sbcs_error(prefix, SBCS_IDLE | sbcs, 3, 4),
        len(prefix),
        access=("ar", 0x141, 2),
    )
    advanced = [
        *activate(),
        *write(sba.SBCS, SBCS_AUTOINC),
        *write(sba.SBADDRESS0, 0x100),
        *write(sba.SBDATA0, 0x5A, write_access(0x100, 0, 0x5A)),
        wr(0x100, 0, 0x5A),
        ("bresp", False),
        *write(sba.SBCS, SBACCESS32 | SBCS_AUTOINC),
        *write(sba.SBDATA0, 0x1234),
        wr(0x101, 2, 0x1234),
    ]
    restored = [
        *activate(),
        *write(sba.SBCS, SBCS_AUTOINC),
        *write(sba.SBADDRESS0, 0x101),
        *write(sba.SBDATA0, 0x5A, write_access(0x101, 0, 0x5A)),
        wr(0x101, 0, 0x5A),
        ("bresp", True),
        *write(sba.SBCS, SBACCESS16 | SBCS_AUTOINC | (sba.SbError.BAD_ADDRESS << SBERROR_LSB)),
        *write(sba.SBDATA0, 0x1234),
        wr(0x101, 1, 0x1234),
    ]
    scenario.custom["spurious-access-after-autoinc"] = advanced
    scenario.custom["spurious-access-after-error"] = restored
    return scenario


def oversize() -> Scenario:
    """A 128-bit read on a 64-bit bus starts no access; sbcs reads sberror 4."""
    sbcs = SBACCESS128 | SBCS_READONADDR
    prefix = [*activate(), *write(sba.SBCS, sbcs), *write(sba.SBADDRESS0, 0x100)]
    scenario = sbcs_error(prefix, (SBCS_IDLE & ~SBACCESS_FIELD) | sbcs, 4, 3)
    return forbid(scenario, len(prefix), access=("ar", 0x100, 4))


def capture_state(prefix: list[Event], probe: Event, *, wrong: int, stale: int) -> Scenario:
    """The capture ``probe`` after ``prefix``; mismatch and stale capture other words."""
    events = [*prefix, probe]
    t1 = len(prefix)
    return Scenario(events, t1, wrong=seen_as(probe, wrong), stale=seen_as(probe, stale))


def busy_dm() -> Scenario:
    """An sbdata0 read answered busy by the DM; the capture after it reads busy."""
    prefix = [
        *activate(),
        *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR),
        *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
        update(READ, sba.SBDATA0, 0),
        dmi(READ, sba.SBDATA0, 0, answer(busy=True), response=(0, BUSY_RESP)),
        ("ar", 0x100, 2),
        ("bresp", False),
    ]
    probe = cap(sba.SBDATA0, 0, BUSY_RESP)
    return capture_state(
        prefix,
        probe,
        wrong=cap_word(sba.SBDATA0, 0, OK),
        stale=cap_word(sba.SBADDRESS0, 0, BUSY_RESP),
    )


def busy_update() -> Scenario:
    """A write pending, a second op refused; the capture after it reads busy."""
    prefix = [
        upd(WRITE, DATA0, 0x11),
        upd(WRITE, DATA0, 0x22, sends=False, pending=True),
        dmi(WRITE, DATA0, 0x11, answer(), response=(0, OK)),
    ]
    probe = cap(DATA0, 0x11, BUSY_RESP)
    scenario = capture_state(
        prefix,
        probe,
        wrong=cap_word(DATA0, 0x11, STATUS_FAILED),
        stale=cap_word(DATA0, 0x11, OK),
    )
    return forbid(scenario, len(prefix), access=wr(0x100, 2, 0x22), req=("req", WRITE, DATA0, 0x22))


def sticky_ignore() -> Scenario:
    """An update made with the sticky busy set; the capture after it reads busy."""
    probe = cap(DATA0, 0, BUSY_RESP)
    scenario = capture_state(
        [upd(WRITE, DATA0, 0x5, sends=False, busy=True)],
        probe,
        wrong=cap_word(DATA0, 0, STATUS_FAILED),
        stale=cap_word(DATA0, 0, OK),
    )
    return forbid(scenario, 1, access=wr(0x100, 2, 0x5), req=("req", WRITE, DATA0, 0x5))


def op3_clear() -> Scenario:
    """Op 3 with the sticky busy set; the capture after it reads status 0."""
    prefix = [
        upd(WRITE, DATA0, 0x5, sends=False, busy=True),
        cap(0, 0, BUSY_RESP),
        upd(CLEAR, 0, 0, sends=False, busy=True),
    ]
    scenario = capture_state(
        prefix,
        cap(0, 0),
        wrong=cap_word(0, 0, STATUS_FAILED),
        stale=cap_word(0, 0, BUSY_RESP),
    )
    return forbid(scenario, len(prefix), req=("req", WRITE, DATA0, 0))


def ordered_captures(*, op: int) -> Scenario:
    """Two operations, each answered and then captured; captures are per edge, so each
    fault is the word one capture shows: drop the image before it, duplicate the image
    before it again, spurious an image no operation left, reorder the two images swapped,
    reset-stale the old image at the capture of an operation after a TCK-side reset."""
    first, second = (DATA0, 0x77), (DATA1, 0x88)

    def operation(addr: int, data: int) -> list[Event]:
        if op == READ:
            return [
                update(READ, addr, 0),
                dmi(READ, addr, 0, answer(exact(data)), response=(data, OK)),
            ]
        return write(addr, data)

    events = [*operation(*first), cap(*first), *operation(*second), cap(*second)]
    t1, t2 = 2, 5
    old, new = cap_word(*first, OK), cap_word(*second, OK)
    reset = [
        *events[:t1],
        ("reset", sb_mod.RST_ARST_TCK),
        *operation(*second),
        seen_as(cap(*second), old),
    ]
    return Scenario(
        events,
        t1,
        t2,
        wrong=seen_as(events[t1], cap_word(first[0], first[1] ^ 1, OK)),
        custom={
            "drop": _replace(events, t1, seen_as(events[t1], cap_word(0, 0, OK))),
            "duplicate": _replace(events, t2, seen_as(events[t2], old)),
            "spurious": _replace(events, t2, seen_as(events[t2], cap_word(0x7F, 0xDEAD, OK))),
            "reorder": _replace(
                _replace(events, t1, seen_as(events[t1], new)), t2, seen_as(events[t2], old)
            ),
            "reset-stale": reset,
        },
    )


def busy_recovery() -> Scenario:
    """Two resends of an sbdata0 write, each the first operation after an op 3 that
    cleared a sticky busy; their bus writes pair in order."""
    first, second = 0x100, 0x200
    data1, data2 = 0x1122_3344, 0x5566_7788
    events = [
        *activate(),
        *write(sba.SBCS, SBACCESS32),
        *write(sba.SBADDRESS0, first),
        upd(CLEAR, 0, 0, sends=False, busy=True),
        *write(sba.SBDATA0, data1, write_access(first, 2, data1)),
        *write(sba.SBADDRESS0, second),
        upd(CLEAR, 0, 0, sends=False, busy=True),
        *write(sba.SBDATA0, data2, write_access(second, 2, data2)),
    ]
    t1 = len(events)
    events += [wr(first, 2, data1), ("bresp", False)]
    t2 = len(events)
    events += [wr(second, 2, data2), ("bresp", False)]
    return Scenario(
        events,
        t1,
        t2,
        wrong=wr(first, 2, data1 ^ 0xFF),
        spurious=wr(second + 4, 2, 0x99),
    )


def write_memory() -> Scenario:
    """Four bytes written and the two after them left as filled, against the model."""
    expected = (0x11, 0x22, 0x33, 0x44, 0xAA, 0xAA)
    check = sb_mod.WRITE_MEMORY

    def mem(observed: tuple[int | None, ...]) -> Event:
        return ("mem", check, 0x100, observed, expected)

    events = [mem(expected)]
    return Scenario(
        events,
        0,
        wrong=mem((0x11, 0x23, 0x33, 0x44, 0xAA, 0xAA)),
        stale=mem((0xAA,) * 6),
        spurious=("mem", check, 0x106, (0x55,), (0xAA,)),
    )


def results(check: str) -> Scenario:
    """Two reads whose results come back in order within the result window, each followed
    by the capture of its poll."""
    events: list[Event] = [("watch", check, RESULT_EARLIEST, RESULT_LATEST, RESULT_TIMING[3])]
    arrivals = []
    for addr, data in ((DATA0, 0x77), (DATA1, 0x88)):
        events += [
            update(READ, addr, 0),
            dmi(READ, addr, 0, answer(exact(data)), response=(data, OK)),
        ]
        arrivals.append(len(events))
        events += [
            ("arrival", RESULT_EARLIEST, True, cap_word(addr, data, OK), OK),
            cap(addr, data),
        ]
    t1, t2 = arrivals

    def timed(edges: int) -> list[Event]:
        _, _, is_read, word, resp = events[t1]
        return _replace(events, t1, ("arrival", edges, is_read, word, resp))

    _, _, is_read, word, resp = events[t1]
    return Scenario(
        events,
        t1,
        t2,
        wrong=("arrival", RESULT_EARLIEST, is_read, word ^ (1 << 2), resp),
        spurious=("arrival", RESULT_EARLIEST, True, cap_word(DATA1, 0x88, OK), OK),
        custom={"late": timed(RESULT_LATEST + 1), "early": timed(RESULT_EARLIEST - 1)},
    )


def timing_window(item: str) -> tuple[int, int]:
    """The earliest and latest cycle count of timing item ``item`` in the clock-reset model."""
    with CLOCKRESET_PATH.open("rb") as model:
        timings = tomllib.load(model).get("timing", [])
    entry = next(t for t in timings if t["id"] == item)
    return entry["min_cycles"], entry["max_cycles"]


def tck_stop_hold(sync_stages: int) -> Scenario:
    """A write with TCK stopped on a build with ``sync_stages``: the nets hold at every
    i_clk edge of the stop, the result comes back in that build's window, and the capture
    after TCK resumes holds it."""
    nets = (1, 0, DATA0, 0x11, 0)
    check = sb_mod.TCK_STOP_HOLD_IDS[sync_stages]
    earliest, latest = timing_window(RESULT_TIMING[sync_stages])
    events = [
        ("watch", check, earliest, latest, RESULT_TIMING[sync_stages]),
        update(WRITE, DATA0, 0x11),
        ("hold-start", nets),
        ("hold-sample", nets, 1),
    ]
    t1 = len(events)
    events += [
        ("hold-sample", nets, 2),
        dmi(WRITE, DATA0, 0x11, answer(), response=(0, OK)),
        ("hold-end",),
    ]
    arrival = len(events)
    events += [
        ("arrival", earliest, False, cap_word(DATA0, 0x11, OK), OK),
        cap(DATA0, 0x11),
    ]

    def timed(edges: int) -> list[Event]:
        _, _, is_read, word, resp = events[arrival]
        return _replace(events, arrival, ("arrival", edges, is_read, word, resp))

    return Scenario(
        events,
        t1,
        wrong=("hold-sample", (1, 0, DATA0, 0x12, 0), 2),
        custom={
            "stale": _replace(events, len(events) - 1, seen_as(events[-1], cap_word(0, 0, OK))),
            "late": timed(latest + 1),
            "early": timed(earliest - 1),
        },
        sync_stages=sync_stages,
    )


def sync_release() -> Scenario:
    """The TCK side's release count, then the i_clk side's, each in its window."""
    check = sb_mod.SYNC_RELEASE
    events = [
        ("event-expect", check, "tck", 3, 4),
        ("event", check, "tck", 3),
        ("event-expect", check, "clk", 3, 3),
        ("event", check, "clk", 3),
    ]
    return Scenario(
        events,
        1,
        3,
        wrong=("event", check, "tck", 9),
        spurious=("event", check, "clk", 3),
        custom={
            "late": _replace(events, 1, ("event", check, "tck", 5)),
            "early": _replace(events, 1, ("event", check, "tck", 2)),
        },
    )


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    sb_mod.OP_DECODE: op_decode,
    sb_mod.UPDATE_EDGE: update_edge,
    sb_mod.RATIO_INVARIANT: ratio_invariant,
    sb_mod.BUSY_CAPTURE: busy_capture,
    sb_mod.SBBUSY: sbbusy,
    sb_mod.SBBUSYERROR: sbbusyerror,
    sb_mod.DMACTIVE_CLEAR: dmactive_clear,
    sb_mod.DMACTIVE_INFLIGHT_HOLD: dmactive_inflight_hold,
    sb_mod.DMACTIVE_REARM: dmactive_rearm,
    sb_mod.OP3_KEEPS_DM: op3_keeps_dm,
    sb_mod.NO_INC_AFTER_ERROR: no_inc_after_error,
    sb_mod.SINGLE_READ: lambda: bus_reads(READ_OFFSET_WRONG),
    sb_mod.BLOCK_READ: lambda: bus_reads(READ_OFFSET_WRONG, trigger="data"),
    sb_mod.ADDRESS_WIDTH: lambda: bus_reads(READ_HIGH_LOST),
    sb_mod.SBADDRESS1_ABSENT: sbaddress1_absent,
    sb_mod.AXI4_SIZE: lambda: bus_reads(READ_BASE, size2=1, wrong=("ar", READ_BASE, 3)),
    sb_mod.SINGLE_WRITE: lambda: bus_writes(),
    sb_mod.BLOCK_WRITE: lambda: bus_writes(autoinc=True),
    sb_mod.DATA_LANES: lambda: bus_writes(size=0, wrong=_lane_zero),
    sb_mod.LITE_FULL_WIDTH: lambda: bus_writes(size=0, lite=True, wrong=_wide_strobe),
    sb_mod.VERSION: dm_version,
    sb_mod.HART_UNAVAIL: lambda: dm_field(
        [], DMSTATUS, 0x0040_3082, wrong=0x0040_2082, stale=0x0040_0082
    ),
    sb_mod.REGISTER_MAP: register_map,
    sb_mod.CMDERR: lambda: forbid(
        dm_field(
            [*activate(), *write(sba.COMMAND, 0x1234)],
            sba.ABSTRACTCS,
            2 << ABSTRACTCS_CMDERR_LSB,
            wrong=3 << ABSTRACTCS_CMDERR_LSB,
            stale=0,
        ),
        4,
        access=("ar", 0x100, 2),
    ),
    sb_mod.SBCS_FIELDS: lambda: dm_field(
        activate(), sba.SBCS, SBCS_IDLE, wrong=SBCS_IDLE ^ (1 << 5), stale=SBACCESS32
    ),
    sb_mod.NDMRESET: lambda: forbid(
        dm_field(
            [
                *activate(),
                *write(sba.SBADDRESS0, 0x80),
                *write(sba.SBADDRESS0, 0x100),
                *write(sba.DMCONTROL, 1 | DMCONTROL_NDMRESET),
                *write(sba.DMCONTROL, 1),
            ],
            sba.SBADDRESS0,
            0x100,
            wrong=0,
            stale=0x80,
        ),
        10,
        access=("ar", 0x100, 2),
    ),
    sb_mod.ASYNC_ANY_TIME: lambda: dm_field(
        [*activate(), *write(sba.SBADDRESS0, 0x100), ("reset", sb_mod.RST_ARST_BUS)],
        sba.SBADDRESS0,
        0,
        wrong=0x5,
        stale=0x100,
    ),
    sb_mod.TRST_INDEPENDENT: lambda: dm_field(
        [
            *activate(),
            *write(sba.SBADDRESS0, 0x100),
            ("reset", sb_mod.RST_TRST),
            ("trst-end",),
        ],
        sba.SBADDRESS0,
        0x100,
        wrong=0x300,
        stale=0,
    ),
    sb_mod.BUS_ERROR: bus_error,
    sb_mod.MISALIGNED: misaligned,
    sb_mod.OVERSIZE: oversize,
    sb_mod.BUSY_DM: busy_dm,
    sb_mod.BUSY_UPDATE: busy_update,
    sb_mod.STICKY_IGNORE: sticky_ignore,
    sb_mod.OP3_CLEAR: op3_clear,
    sb_mod.CAPTURE_IMAGE: lambda: ordered_captures(op=WRITE),
    sb_mod.PIPELINED_READ: lambda: ordered_captures(op=READ),
    sb_mod.BUSY_RECOVERY: busy_recovery,
    sb_mod.WRITE_MEMORY: write_memory,
    sb_mod.RESULT_LATENCY: lambda: results(sb_mod.RESULT_LATENCY),
    sb_mod.TCK_STOP_HOLD: lambda: tck_stop_hold(3),
    sb_mod.TCK_STOP_HOLD_SS4: lambda: tck_stop_hold(4),
    sb_mod.SYNC_RELEASE: sync_release,
}


def no_stale_axi() -> Scenario:
    """An i_arst_n reset with a bus read in flight and an sbaddress0 read whose response it
    cuts off, then a capture and a bus read after the release, whose AR pairs first.

    Each fault is one item after the release: the cut-off read's AR (stale-access), an AR
    nothing predicts (spurious-access), the cut-off read's AR in place of the predicted one
    (stale-in-place), the cut-off sbaddress0 read taken again with no request queued
    (stale-request) or ahead of a queued one (stale-request-queued), its response
    (stale-response), and a capture holding its address (stale-capture)."""
    sbcs = SBACCESS32 | SBCS_READONADDR
    before = [
        *activate(),
        *write(sba.SBCS, sbcs),
        *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
        update(READ, sba.SBADDRESS0, 0),
        dmi(READ, sba.SBADDRESS0, 0, answer(exact(0x100)), response=None),
        ("reset", sb_mod.RST_ARST_BUS),
        ("reset", sb_mod.RST_ARST_TCK),
    ]
    released = len(before)
    after = [
        cap(0, 0),
        *activate(),
        *write(sba.SBCS, sbcs),
        *write(sba.SBADDRESS0, 0x200, read_access(0x200, 2)),
        ("ar", 0x200, 2),
        ("bresp", False),
    ]
    events = [*before, *after]
    pairing = events.index(("ar", 0x200, 2))

    def inserted(event: Event) -> list[Event]:
        return [*events[:released], event, *events[released:]]

    return Scenario(
        events,
        released,
        custom={
            "stale-access": inserted(("ar", 0x100, 2)),
            "spurious-access": inserted(("ar", 0x300, 2)),
            "stale-in-place": _replace(events, pairing, ("ar", 0x100, 2)),
            "stale-request": inserted(("req", READ, sba.SBADDRESS0, 0)),
            "stale-request-queued": [
                *events[: released + 2],
                ("req", READ, sba.SBADDRESS0, 0),
                *events[released + 2 :],
            ],
            "stale-response": inserted(("resp", 0x100, OK)),
            "stale-capture": _replace(
                events, released, seen_as(after[0], cap_word(sba.SBADDRESS0, 0, OK))
            ),
        },
    )


# CHK-RESET-NO-STALE-AXI is a checker-kind check, so its rows are asserted here and logged
# outside the SBTEST lines ``fault_matrix.py`` grades against the planned scoreboard checks
NO_STALE_AXI_FAULTS = (
    *ALWAYS,
    "stale-access",
    "spurious-access",
    "stale-in-place",
    "stale-request",
    "stale-request-queued",
    "stale-response",
    "stale-capture",
)


# Each stream's check and scenario, and the clock-reset entries that end its items in flight
RESET_STREAMS: tuple[tuple[str, Callable[[], Scenario], frozenset[str]], ...] = (
    (
        sb_mod.OP_DECODE,
        op_decode,
        frozenset({sb_mod.RST_ARST_BUS, sb_mod.RST_ARST_TCK, sb_mod.RST_SCAN}),
    ),
    (sb_mod.RATIO_INVARIANT, ratio_invariant, frozenset({sb_mod.RST_ARST_BUS, sb_mod.RST_SCAN})),
    (
        sb_mod.SINGLE_READ,
        lambda: bus_reads(READ_OFFSET_WRONG),
        frozenset({sb_mod.RST_ARST_BUS, sb_mod.RST_SCAN}),
    ),
)


def reset_cases(scenario: Scenario, scope: str, *, ends: bool) -> dict[str, list[Event]]:
    """The cases of one reset entry before the stream's first observed item, by fault name.

    An entry that ends the item faces reset-stale-<entry>: the item seen after the reset.
    One that keeps it runs clean with the reset in place and faces reset-lost-<entry>: the
    item lost across the reset.
    """
    slug = scope.removeprefix("RST-").lower()
    events, t1 = scenario.events, scenario.t1
    reset: Event = ("reset", scope)
    if ends:
        return {f"reset-stale-{slug}": [*events[:t1], reset, observed_only(events[t1])]}
    return {
        "none": [*events[:t1], reset, *events[t1:]],
        f"reset-lost-{slug}": [*events[:t1], reset, *events[t1 + 1 :]],
    }


def planned_kinds() -> dict[str, str]:
    """The comparison kind the plan records for each scoreboard check."""
    with PLAN_PATH.open("rb") as plan:
        checks = tomllib.load(plan).get("check", [])
    return {c["id"]: c.get("compare", "") for c in checks if c.get("kind") == "scoreboard"}


def faults_for(check: str, kind: str) -> tuple[str, ...]:
    return (*ALWAYS, *KIND_FAULTS[kind], *EXTRA_FAULTS.get(check, ()))


class Tdr2axiScoreboardFaultMatrix(unittest.TestCase):
    """Every scoreboard check passes its clean case and catches every fault of its kind."""

    @classmethod
    def setUpClass(cls) -> None:
        quiet = logging.getLogger("cocotb.tdr2axi")
        cls._level = quiet.level
        quiet.setLevel(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls) -> None:
        logging.getLogger("cocotb.tdr2axi").setLevel(cls._level)

    def test_every_check_has_a_scenario(self) -> None:
        kinds = planned_kinds()
        self.assertEqual(set(SCENARIOS), set(sb_mod.SCOREBOARD_CHECKS))
        self.assertLessEqual(set(SCENARIOS), set(kinds))

    def test_fault_matrix(self) -> None:
        """Each check's clean case passes and every fault fails the run under its ID."""
        kinds = planned_kinds()
        for check in sorted(SCENARIOS):
            kind = kinds[check]
            for fault in faults_for(check, kind):
                with self.subTest(check=check, fault=fault):
                    result = run_case(check, SCENARIOS[check](), fault)
                    LOG.info("SBTEST %s compare=%s fault=%s result=%s", check, kind, fault, result)
                    self.assertEqual(result, "pass" if fault == "none" else "caught")

    def test_dmstatus_through_the_model(self) -> None:
        """The SBA model's dmstatus prediction passes the value the spec gives this DM, so
        authenticated-clear and authbusy-set fail on their one bit."""
        scenario = replace(dm_version(), custom={"none": modelled_dmstatus(DMSTATUS_SPEC)})
        self.assertEqual(run_case(sb_mod.VERSION, scenario, "none"), "pass")

    def test_reset_entries(self) -> None:
        """Every clock-reset entry ends what it resets and keeps what it does not."""
        kinds = planned_kinds()
        for check, build, ending in RESET_STREAMS:
            for scope in sorted(sb_mod.RESET_SCOPES):
                cases = reset_cases(build(), scope, ends=scope in ending)
                for fault, events in cases.items():
                    with self.subTest(check=check, scope=scope, fault=fault):
                        result = run_case(check, replace(build(), custom={fault: events}), fault)
                        LOG.info(
                            "SBTEST %s compare=%s fault=%s result=%s",
                            check,
                            kinds[check],
                            fault,
                            result,
                        )
                        self.assertEqual(result, "pass" if fault == "none" else "caught")

    def test_no_stale_axi(self) -> None:
        """After an i_arst_n reset, the first predicted access passes and every item of a
        cut-off operation, and a transaction nothing predicts, fails the stale-access
        check by name."""
        for fault in NO_STALE_AXI_FAULTS:
            with self.subTest(fault=fault):
                result = run_case(sb_mod.NO_STALE_AXI, no_stale_axi(), fault)
                LOG.info("checker-kind %s fault=%s result=%s", sb_mod.NO_STALE_AXI, fault, result)
                self.assertEqual(result, "pass" if fault == "none" else "caught")

    def test_no_inc_after_a_failed_sbdata0_read(self) -> None:
        """A failed read that an sbdata0 read started grades the sbaddress0 read after it
        under CHK-SBA-NO-INC-AFTER-ERROR, and catches every fault of its kind."""
        check = sb_mod.NO_INC_AFTER_ERROR
        for fault in faults_for(check, planned_kinds()[check]):
            with self.subTest(fault=fault):
                result = run_case(check, no_inc_after_error(trigger="data"), fault)
                self.assertEqual(result, "pass" if fault == "none" else "caught")

    def test_open_answer_grades_nothing(self) -> None:
        """An answer whose response code the model leaves open records no check, busy or
        success, whatever data it carries."""
        records: list[str] = []

        def report(check: str, **_kw: object) -> None:
            records.append(check)

        for resp in (OK, BUSY_RESP):
            with self.subTest(resp=resp):
                sb = sb_mod.Tdr2axiScoreboard(sb_mod.Tdr2axiScoreboardCfg(lite=False), report)
                for event in [*activate(), update(READ, sba.SBDATA0, 0)]:
                    feed(sb, event)
                before = len(records)
                feed(sb, dmi(READ, sba.SBDATA0, 0, open_answer(), response=(0xDEAD, resp)))
                self.assertEqual([c for c in records[before:] if c != sb_mod.OP_DECODE], [])

    def test_hold_completion_grades_the_sbcs_pair(self) -> None:
        """From the held access's response until sbbusy falls, the model predicts sbbusy and
        sbbusyerror as a pair, both as held or both 0: CHK-DM-DMACTIVE-INFLIGHT-HOLD passes
        a read of either pair and fails a read of one bit without the other."""
        pair = SBCS_BUSY | SBCS_BUSYERROR
        prediction = sba.Expected((pair, 0), pair, ATOMS)
        start = [
            *activate(),
            *write(sba.SBCS, SBACCESS32 | SBCS_READONADDR),
            *write(sba.SBADDRESS0, 0x100, read_access(0x100, 2)),
            *write(sba.DMCONTROL, 0),
            ("ar", 0x100, 2),
            ("bresp", False),
        ]
        cases = ((pair, True), (0, True), (SBCS_BUSYERROR, False), (SBCS_BUSY, False))
        for value, want in cases:
            with self.subTest(value=f"0x{value:08x}"):
                read_sbcs = access(READ, sba.SBCS, 0, answer(prediction), response=(value, OK))
                self.assertEqual(verdicts(start, read_sbcs, sb_mod.DMACTIVE_INFLIGHT_HOLD), [want])

    def test_absent_sbaddress1_read_keeps_its_checks(self) -> None:
        """At 32 bits, an sbaddress1 read that returns what was written fails
        CHK-SBA-SBADDRESS1-ABSENT beside the checks every sbaddress1 read grades,
        CHK-SBA-ADDRESS-WIDTH and CHK-DM-REGISTER-MAP, and no other check."""
        checker = checker_mod.Tdr2axiChecker(
            name="absent-beside", required_ids={checker_mod.NONVAC, sb_mod.SBADDRESS1_ABSENT}
        )
        sb = sb_mod.Tdr2axiScoreboard(
            sb_mod.Tdr2axiScoreboardCfg(lite=False, addr_width=32), report=checker.record
        )
        for event in faulted(sbaddress1_absent(), "mismatch"):
            feed(sb, event)
        sb.drain()
        with self.assertRaises(checker_mod.Tdr2axiCheckError) as caught:
            checker.finalize()
        failed = {line.split()[0] for line in str(caught.exception).splitlines()}
        expected = {sb_mod.SBADDRESS1_ABSENT, sb_mod.ADDRESS_WIDTH, sb_mod.REGISTER_MAP}
        self.assertEqual(failed, expected)

    def test_absent_window_access_from_sbaddress0(self) -> None:
        """At 32 bits, the read an sbaddress0 write with sbreadonaddr starts after an
        sbaddress1 write is not graded under CHK-SBA-SBADDRESS1-ABSENT: on the address
        written it passes with no record of that check, and moved it fails its own check
        and not that one."""
        records: list[tuple[str, bool]] = []

        def report(check: str, *, passed: bool, **_kw: object) -> None:
            records.append((check, passed))

        on_addr = SBACCESS32 | SBCS_READONADDR
        start = [
            *activate(),
            *write(sba.SBCS, on_addr),
            *access(WRITE, sba.SBADDRESS1, WORD, answer(), response=(0, OK)),
            *write(sba.SBADDRESS0, 0x200, read_access(0x200, 2)),
        ]
        for addr in (0x200, 0x204):
            with self.subTest(addr=addr):
                records.clear()
                sb = sb_mod.Tdr2axiScoreboard(
                    sb_mod.Tdr2axiScoreboardCfg(lite=False, addr_width=32), report
                )
                for event in start:
                    feed(sb, event)
                before = len(records)
                for event in (("ar", addr, 2), ("bresp", False)):
                    feed(sb, event)
                sb.drain()
                after = records[before:]
                self.assertNotIn(sb_mod.SBADDRESS1_ABSENT, {check for check, _ in after})
                failed = {check for check, passed in after if not passed}
                self.assertEqual(failed != set(), addr != 0x200)

    def test_absent_sbaddress1_collision_is_sbbusyerrors(self) -> None:
        """At 32 bits a colliding sbaddress1 write grades CHK-SBA-SBBUSYERROR and not
        CHK-SBA-SBADDRESS1-ABSENT: answered success it fails CHK-SBA-SBBUSYERROR, and no
        record from the collision on carries CHK-SBA-SBADDRESS1-ABSENT."""
        records: list[tuple[str, bool]] = []

        def report(check: str, *, passed: bool, **_kw: object) -> None:
            records.append((check, passed))

        for fault in ("none", "collision-success", "sbbusyerror-clear"):
            with self.subTest(fault=fault):
                records.clear()
                sb = sb_mod.Tdr2axiScoreboard(
                    sb_mod.Tdr2axiScoreboardCfg(lite=False, addr_width=32), report
                )
                events = faulted(sbaddress1_absent(), fault)
                collide = next(
                    i
                    for i, event in enumerate(events)
                    if event[0] == "dmi" and event[2] == sba.SBADDRESS1 and event[4].busy
                )
                for event in events[:collide]:
                    feed(sb, event)
                before = len(records)
                for event in events[collide:]:
                    feed(sb, event)
                after = records[before:]
                self.assertNotIn(sb_mod.SBADDRESS1_ABSENT, {check for check, _ in after})
                failed = {check for check, passed in after if not passed}
                if fault == "none":
                    self.assertEqual(failed, set())
                if fault == "collision-success":
                    self.assertIn(sb_mod.SBBUSYERROR, failed)

    def test_sbaddress1_present_above_32_bits(self) -> None:
        """Above 32 bits sbaddress1 exists, and nothing grades CHK-SBA-SBADDRESS1-ABSENT."""
        records: list[str] = []

        def report(check: str, **_kw: object) -> None:
            records.append(check)

        for width in (33, 48, 64):
            with self.subTest(width=width):
                records.clear()
                sb = sb_mod.Tdr2axiScoreboard(
                    sb_mod.Tdr2axiScoreboardCfg(lite=False, addr_width=width), report
                )
                for event in sbaddress1_absent().events:
                    feed(sb, event)
                self.assertNotIn(sb_mod.SBADDRESS1_ABSENT, records)

    def test_reset_inside_tap_reset(self) -> None:
        """Another reset between a TAP reset and its end fails the TAP reset's check."""
        scenario = Scenario([("reset", sb_mod.RST_TRST), ("reset", sb_mod.RST_ARST_BUS)], 0)
        result = run_case(sb_mod.TRST_INDEPENDENT, scenario, "none")
        self.assertEqual(result, "fail")

    def test_unknown_reset_scope(self) -> None:
        sb = sb_mod.Tdr2axiScoreboard(
            sb_mod.Tdr2axiScoreboardCfg(lite=False), report=lambda *_a, **_k: None
        )
        with self.assertRaises(ValueError):
            sb.reset("RST-NONE")


if __name__ == "__main__":
    # Run as a script, this module is the self-test's runner and owns its one handler set
    logging.basicConfig(format="%(message)s", level=logging.INFO, stream=sys.stdout)
    unittest.main(verbosity=0)
