# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi TDR bridge status reference model.

``TdrStatusModel`` predicts the bridge's side of the 41-bit DMI TDR: which DMI request
each update edge sends, the capture image (address, data, status) at each capture edge,
the sticky busy status and op 3, the window in which a DM response reaches ``o_dmi_tdr``,
and the windows in which each side leaves reset after ``i_arst_n`` rises. Its sources are
the tdr2axi specification atoms, the RISC-V External Debug Support 0.13.2 and IEEE 1149.1
X-atoms of the trace store, its timing items T-TDR2AXI-resp-to-tdr,
T-TDR2AXI-resp-to-tdr-ss4, T-TDR2AXI-tck-reset-release, T-TDR2AXI-tck-reset-release-ss4,
T-TDR2AXI-cdc-clear-20to1 and T-TDR2AXI-cdc-clear-1to2, and the DV readings recorded
there. It reads no RTL, no DUT
signal and no observation it predicts.

Inputs are stimulus: the update-stage value at each update edge, each capture edge, the
DM's answer as ``SbaRefModel`` predicts it, delivered when the bridge receives it, the end
of the post-reset CDC clear sequence, and resets. Outputs are frozen expected items for
the bench's scoreboard; the model compares nothing and counts no cycles. Python core only:
the bench has a single cocotb realization.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import IntEnum
from functools import partial

__all__ = [
    "BUSY_MARKER",
    "NEGATIVE_KNOB",
    "TDR_WIDTH",
    "DmiRequest",
    "TdrCaptureExpect",
    "TdrField",
    "TdrOp",
    "TdrResetRelease",
    "TdrResultWindow",
    "TdrStatus",
    "TdrStatusModel",
    "TdrStimulusError",
    "TdrUpdateExpect",
    "decode_tdr",
]

NEGATIVE_KNOB = "TDR2AXI_TDR_STATUS_MODEL_NEGATIVE"

# TDR layout: S-TDR2AXI-0036 to S-TDR2AXI-0039, X-RISCV_DEBUG-dmi-format.
TDR_WIDTH = 41
TDR_ADDR_LSB = 34
TDR_ADDR_FIELD = 0x7F
TDR_DATA_LSB = 2
TDR_DATA_FIELD = 0xFFFF_FFFF
TDR_OP_FIELD = 0x3

# Read data of a read the DM answers busy: S-TDR2AXI-0007. No other marker reaches the
# capture image.
BUSY_MARKER = 0xB051_B051

_RESET_SCOPES_TCK = frozenset({"RST-ARST-TCK", "RST-SCAN"})
_RESET_SCOPES_BUS = frozenset({"RST-ARST-BUS"})
_RESET_SCOPES_OTHER = frozenset({"RST-TRST", "RST-DMACTIVE"})

_ATOMS_DECODE = (
    "S-TDR2AXI-0030",
    "S-TDR2AXI-0036",
    "S-TDR2AXI-0039",
    "S-TDR2AXI-0063",
    "X-RISCV_DEBUG-dmi-format",
    "X-RISCV_DEBUG-dmi-op",
)
_ATOMS_OP3 = ("S-TDR2AXI-0039", "S-TDR2AXI-0041", "S-TDR2AXI-0056", "X-RISCV_DEBUG-dmi-op")
_ATOMS_OP3_PENDING = ("S-TDR2AXI-0041", "S-TDR2AXI-0043", "X-RISCV_DEBUG-dmi-status")
_ATOMS_BUSY_UPDATE = (
    "S-TDR2AXI-0007",
    "S-TDR2AXI-0032",
    "S-TDR2AXI-0042",
    "S-TDR2AXI-0043",
    "X-RISCV_DEBUG-dmi-op",
    "X-RISCV_DEBUG-dmi-status",
)
_ATOMS_STICKY = ("S-TDR2AXI-0041", "S-TDR2AXI-0045", "X-RISCV_DEBUG-dmi-status")
_ATOMS_CAPTURE = (
    "S-TDR2AXI-0007",
    "S-TDR2AXI-0031",
    "S-TDR2AXI-0033",
    "S-TDR2AXI-0037",
    "S-TDR2AXI-0038",
    "S-TDR2AXI-0039",
    "S-TDR2AXI-0043",
    "S-TDR2AXI-0063",
    "X-RISCV_DEBUG-dmi-update-capture",
    "X-RISCV_DEBUG-nop-capture",
    "X-RISCV_DEBUG-dmi-status",
)
_ATOMS_WINDOW = ("S-TDR2AXI-0018", "S-TDR2AXI-0029", "S-TDR2AXI-0049", "X-IEEE1149_1-tck-static")
_ATOMS_READY = ("S-TDR2AXI-0043", "S-TDR2AXI-0052", "S-TDR2AXI-0053", "S-TDR2AXI-0088")
_ATOMS_RELEASE = ("S-TDR2AXI-0018", "S-TDR2AXI-0026", "S-TDR2AXI-0052", "S-TDR2AXI-0053")


class TdrStimulusError(ValueError):
    """Stimulus outside what the spec defines, so the model has no prediction for it."""


class TdrOp(IntEnum):
    """The op field of an update (S-TDR2AXI-0039); CLEAR is op 3 (S-TDR2AXI-0041)."""

    NOP = 0
    READ = 1
    WRITE = 2
    CLEAR = 3


class TdrStatus(IntEnum):
    """The status field of a capture; status 2 (failed) never occurs (S-TDR2AXI-0039)."""

    SUCCESS = 0
    BUSY = 3


@dataclass(frozen=True)
class TdrField:
    """A predicted data field: it matches a value equal to any allowed value on the mask."""

    allowed: tuple[int, ...]
    mask: int

    def __post_init__(self) -> None:
        if not self.allowed:
            raise ValueError("TdrField needs at least one allowed value")

    @classmethod
    def exact(cls, value: int) -> TdrField:
        """One 32-bit value compared on every bit."""
        return cls((value & TDR_DATA_FIELD,), TDR_DATA_FIELD)

    def matches(self, observed: int) -> bool:
        """Whether ``observed`` is one of the allowed values on the mask bits."""
        return any((observed ^ value) & self.mask == 0 for value in self.allowed)


@dataclass(frozen=True)
class DmiRequest:
    """One predicted DMI request from the bridge to the DM."""

    op: TdrOp
    addr: int
    data: int
    atoms: tuple[str, ...]


@dataclass(frozen=True)
class TdrUpdateExpect:
    """What one update edge sends: a DMI request, or None when it sends nothing.

    ``optional`` marks a request the bridge may also not send, which happens only while
    the model holds several allowed outcomes after a reset.
    """

    request: DmiRequest | None
    atoms: tuple[str, ...]
    optional: bool = False


@dataclass(frozen=True)
class TdrCaptureExpect:
    """The predicted ``o_dmi_tdr`` at one capture edge.

    ``addr`` and ``status`` are exact; ``data`` is exact, a set or partly free as the DM's
    answer leaves it. ``alternatives`` holds the other images the spec allows, which occur
    only while the model holds several outcomes after a reset; the fields above are the
    first outcome the model holds. ``observer``, set while the model holds several outcomes,
    receives each word ``matches`` is given as the ``o_dmi_tdr`` value captured at this edge.
    """

    addr: int
    data: TdrField
    status: TdrStatus
    atoms: tuple[str, ...]
    alternatives: tuple[TdrCaptureExpect, ...] = ()
    observer: Callable[[int], None] | None = field(default=None, compare=False, repr=False)

    def matches(self, word: int) -> bool:
        """Whether the 41-bit captured ``word`` equals this image or one alternative; the
        ``observer``, when set, receives ``word``."""
        op, addr, data = decode_tdr(word)
        hit = addr == self.addr and op == self.status and self.data.matches(data)
        hit = hit or any(alternative.matches(word) for alternative in self.alternatives)
        if self.observer is not None:
            self.observer(word)
        return hit


@dataclass(frozen=True)
class TdrResultWindow:
    """Bounds, in rising edges of ``clock`` after a start event, of a predicted result.

    ``timing`` names the clock-reset entry that sets the bounds and its start event.
    """

    earliest: int
    latest: int
    clock: str
    timing: str
    atoms: tuple[str, ...]


@dataclass(frozen=True)
class TdrResetRelease:
    """When each internal reset releases after ``i_arst_n`` rises with i_test_mode = 0."""

    tck: TdrResultWindow
    clk: TdrResultWindow
    atoms: tuple[str, ...]


@dataclass(frozen=True)
class _Bridge:
    status: TdrStatus
    pending: TdrOp | None
    addr: int
    data: TdrField


_RESET_BRIDGE = _Bridge(TdrStatus.SUCCESS, None, 0, TdrField((0,), TDR_DATA_FIELD))


def decode_tdr(word: int) -> tuple[int, int, int]:
    """Split a 41-bit TDR value into its op or status, address and data fields."""
    if not 0 <= word < (1 << TDR_WIDTH):
        raise ValueError(f"TDR value 0x{word:x} is wider than {TDR_WIDTH} bits")
    return (
        word & TDR_OP_FIELD,
        (word >> TDR_ADDR_LSB) & TDR_ADDR_FIELD,
        (word >> TDR_DATA_LSB) & TDR_DATA_FIELD,
    )


class TdrStatusModel:
    """Bridge status rules of the DMI TDR, predicted from the spec.

    The bridge holds the capture fields, a sticky status, and at most one pending
    operation, a read or a write sent to the DM whose answer has not reached the bridge.
    ``predict_update`` and ``predict_capture`` are the TCK edges with ``i_dmi_tdr_update``
    and ``i_dmi_tdr_capture`` high, in edge order; ``response`` is the DM's answer reaching
    the bridge.

    Rules (S-TDR2AXI-0043, S-TDR2AXI-0045, and the readings of S-TDR2AXI-0037,
    S-TDR2AXI-0038 and S-TDR2AXI-0042): an update of op 0, 1 or 2 while an operation is
    pending sets busy and loads nothing, and op 3 then clears the status, loads nothing and
    leaves the operation to complete; a capture while a read is pending sets busy and reads
    busy on that same edge; a busy DM answer sets busy, and for a read puts BUSY_MARKER in
    the data field. With busy set an update loads nothing and sends nothing, and op 3
    clears it. An update in IDLE with a clear status loads its address and data, whatever
    its op, and sends a request for op 1 or 2. A read's answer replaces the data field; a
    write leaves its write data there.

    Readiness after reset: a reset of either side starts the CDC clear sequence
    (T-TDR2AXI-cdc-clear-20to1, T-TDR2AXI-cdc-clear-1to2), and until it ends an update of
    op 1 or 2 in IDLE with a clear status is either accepted as above or sets busy and
    sends nothing, with the capture fields loaded or kept; op 3 recovers it. The model then
    holds every allowed outcome, offers each one's capture image, marks a request that one
    outcome does not send as optional, and drops the outcomes a DM answer or a captured
    word rules out.

    Resynchronization: ``response`` is called when the bridge receives the answer, taken
    from the bridge's state observable or from the DM response handshake plus the window
    of ``predict_result_window``; S-TDR2AXI-0049 leaves that latency to "a few TCK
    cycles". The answer's content comes from ``SbaRefModel``, never from the DUT.
    ``settled`` is the end of the clear sequence, taken from the CDCs' isolate observables
    or from the bound of the clock ratio's clear-sequence timing item, which leaves the
    point within it open; a ``response`` also ends it. The word captured at each edge,
    which ``TdrHost`` passes to the image's ``matches``, selects among the outcomes
    T-TDR2AXI-cdc-clear-20to1 and T-TDR2AXI-cdc-clear-1to2 leave open: an outcome whose
    image differs from it is not the bridge's.

    ``TdrStimulusError`` marks an answer with no operation pending in any outcome.

    ``negative`` inverts bit 0 of every predicted address, in capture images and in DMI
    requests, and moves the result and release windows past their bounds; the bench sets
    it from the ``NEGATIVE_KNOB`` variable.
    """

    def __init__(self, *, sync_stages: int, negative: bool = False) -> None:
        if sync_stages < 3:
            raise ValueError(f"SYNC_STAGES must be 3 or more (S-TDR2AXI-0018), got {sync_stages}")
        self.sync_stages = sync_stages
        self._negative = negative
        self._states: tuple[_Bridge, ...] = (_RESET_BRIDGE,)
        self._settling = False

    @property
    def pending(self) -> TdrOp | None:
        """The operation sent and awaiting its answer, or None, in the first outcome held."""
        return self._states[0].pending

    @property
    def status(self) -> TdrStatus:
        """The sticky status in the first outcome held."""
        return self._states[0].status

    @property
    def settling(self) -> bool:
        """Whether the clear-sequence outcomes apply: from a reset to ``settled`` or an answer."""
        return self._settling

    def predict_update(self, word: int) -> TdrUpdateExpect:
        """Predict what the update edge carrying ``word`` sends, and apply it.

        Serves CHK-TDR-OP-DECODE, CHK-TDR-OP3-CLEAR, CHK-TDR-STICKY-IGNORE,
        CHK-TDR-BUSY-UPDATE, CHK-TDR-UPDATE-EDGE, CHK-TDR-BUSY-RECOVERY,
        CHK-DM-OP3-KEEPS-DM and CHK-RESET-ASYNC-ANY-TIME. Atoms: S-TDR2AXI-0030,
        S-TDR2AXI-0032, S-TDR2AXI-0036, S-TDR2AXI-0039, S-TDR2AXI-0041, S-TDR2AXI-0043,
        S-TDR2AXI-0045, S-TDR2AXI-0052, S-TDR2AXI-0053, S-TDR2AXI-0056, S-TDR2AXI-0063,
        S-TDR2AXI-0088, X-RISCV_DEBUG-dmi-format, X-RISCV_DEBUG-dmi-op,
        X-RISCV_DEBUG-dmi-status; timing items T-TDR2AXI-cdc-clear-20to1 and
        T-TDR2AXI-cdc-clear-1to2.

        Exact: a request with the update's address (bits 40:34) and data (bits 33:2) for op
        1 or 2 in IDLE with a clear status; no request otherwise. ``optional`` is set when an
        allowed outcome sends nothing. Each edge with the strobe high is one update, so a
        strobe held for two edges is two calls.

        Op 3 while an operation is pending meets two rules: S-TDR2AXI-0043 sets busy on an
        update while an operation is pending, and S-TDR2AXI-0041 has op 3 clear the sticky
        status in place of ``dtmcs.dmireset``. The spec states no precedence. In
        X-RISCV_DEBUG-dmi-status (RISC-V Debug 0.13.2 6.1.4), ``dmireset`` clears the sticky
        state and lets the DTM retry or complete the outstanding transaction, where
        ``dmihardreset`` drops it; so op 3 wins: the status clears to success, the pending
        operation stays and completes, and a later capture before a read's answer reads
        busy again. This precedence is DV's reading of the cited standard, asked of the
        designer.
        """
        op, addr, data = decode_tdr(word)
        outcomes = [step for state in self._states for step in self._update(state, op, addr, data)]
        sent = [request for _, request, _ in outcomes if request is not None]
        self._states = _distinct(state for state, _, _ in outcomes)
        optional = 0 < len(sent) < len(outcomes)
        return TdrUpdateExpect(sent[0] if sent else None, outcomes[0][2], optional)

    def predict_capture(self) -> TdrCaptureExpect:
        """Predict ``o_dmi_tdr`` at a capture edge, and apply the capture.

        Serves CHK-TDR-CAPTURE-IMAGE, CHK-TDR-CAPTURE-EDGE, CHK-TDR-BUSY-CAPTURE,
        CHK-TDR-BUSY-DM, CHK-TDR-BUSY-UPDATE, CHK-TDR-OP3-CLEAR, CHK-TDR-STICKY-IGNORE,
        CHK-TDR-PIPELINED-READ, CHK-TDR-TCK-STOP-HOLD, CHK-TDR-BUSY-RECOVERY,
        CHK-TDR-RATIO-INVARIANT, CHK-SBA-SBBUSYERROR, CHK-DM-OP3-KEEPS-DM,
        CHK-RESET-ASYNC-ANY-TIME and CHK-RESET-TRST-INDEPENDENT. Atoms: S-TDR2AXI-0007,
        S-TDR2AXI-0031, S-TDR2AXI-0033, S-TDR2AXI-0037, S-TDR2AXI-0038, S-TDR2AXI-0039,
        S-TDR2AXI-0043, S-TDR2AXI-0052, S-TDR2AXI-0063, X-RISCV_DEBUG-dmi-update-capture,
        X-RISCV_DEBUG-nop-capture, X-RISCV_DEBUG-dmi-status.

        Exact address and status (0 or 3, never 2); the data field is the last accepted
        operation's data, a read's answer once it arrives, or BUSY_MARKER after a busy
        answer to a read. A capture while a read is pending reads busy with the read's
        address and the data field of its update. After a reset, one image per allowed
        outcome.

        With several outcomes held, the image's ``observer`` takes the word captured at this
        edge and drops each outcome whose own address, data and status differ from it; a word
        that no outcome matches drops none. Only the first word observed for a capture
        applies, and only while no later call has changed the outcomes.
        """
        self._states = _distinct(
            replace(state, status=TdrStatus.BUSY) if state.pending is TdrOp.READ else state
            for state in self._states
        )
        images = tuple(dict.fromkeys(self._image(state) for state in self._states))
        observer = partial(self._observe, self._states) if len(self._states) > 1 else None
        return replace(images[0], alternatives=images[1:], observer=observer)

    def response(self, *, busy: bool, allowed: tuple[int, ...] = (0,), mask: int = 0) -> None:
        """Deliver the DM's answer to the pending operation: the resynchronization point.

        ``busy`` is the DM's busy answer, and ``allowed`` and ``mask`` the read data
        ``SbaRefModel`` predicts (its ``DmAnswer.data``); a write ignores them. Outcomes with
        no operation pending are dropped, and the clear sequence has ended.
        """
        live = [state for state in self._states if state.pending is not None]
        if not live:
            raise TdrStimulusError("DM answer with no operation pending")
        self._states = _distinct(
            self._answer(state, busy=busy, allowed=allowed, mask=mask) for state in live
        )
        self._settling = False

    def settled(self) -> None:
        """Mark the end of the post-reset CDC clear sequence: a resynchronization point.

        T-TDR2AXI-cdc-clear-20to1 and T-TDR2AXI-cdc-clear-1to2 bound it in TCK edges after
        the later reset release and leave the point within the bound open.
        """
        self._settling = False

    def predict_result_window(self) -> TdrResultWindow:
        """Predict when a DM response reaches ``o_dmi_tdr``.

        Serves CHK-TDR-RESULT-LATENCY, CHK-TDR-TCK-STOP-HOLD and
        CHK-TDR-TCK-STOP-HOLD-SS4. Atoms: S-TDR2AXI-0018, S-TDR2AXI-0029, S-TDR2AXI-0049,
        X-IEEE1149_1-tck-static; timing items T-TDR2AXI-resp-to-tdr (SYNC_STAGES = 3) and
        T-TDR2AXI-resp-to-tdr-ss4 (SYNC_STAGES = 4).

        Window: SYNC_STAGES + 2 to SYNC_STAGES + 3 TCK rising edges after the DM's response
        handshake, counting only edges that arrive, so a stopped TCK holds the result back.
        """
        shift = 2 if self._negative else 0
        timing = "T-TDR2AXI-resp-to-tdr-ss4" if self.sync_stages == 4 else "T-TDR2AXI-resp-to-tdr"
        return TdrResultWindow(
            earliest=self.sync_stages + 2 + shift,
            latest=self.sync_stages + 3 + shift,
            clock="CLK-TCK",
            timing=timing,
            atoms=_ATOMS_WINDOW,
        )

    def predict_reset_release(self) -> TdrResetRelease:
        """Predict when each side leaves reset after ``i_arst_n`` rises with i_test_mode = 0.

        Serves CHK-RESET-SYNC-RELEASE. Atoms: S-TDR2AXI-0018, S-TDR2AXI-0026,
        S-TDR2AXI-0052, S-TDR2AXI-0053; timing items T-TDR2AXI-tck-reset-release
        (SYNC_STAGES = 3) and T-TDR2AXI-tck-reset-release-ss4 (SYNC_STAGES = 4), and
        RST-ARST-BUS for the ``i_clk`` side.

        Windows: the TCK-side reset releases SYNC_STAGES to SYNC_STAGES + 1 TCK rising edges
        after ``i_arst_n`` rises, and the ``i_clk``-side reset SYNC_STAGES to
        SYNC_STAGES + 1 ``i_clk`` rising edges after it, counting only edges that arrive.
        Both assert in the step ``i_arst_n`` falls.
        """
        shift = 2 if self._negative else 0
        earliest, latest = self.sync_stages + shift, self.sync_stages + 1 + shift
        timing = (
            "T-TDR2AXI-tck-reset-release-ss4"
            if self.sync_stages == 4
            else ("T-TDR2AXI-tck-reset-release")
        )
        return TdrResetRelease(
            tck=TdrResultWindow(earliest, latest, "CLK-TCK", timing, _ATOMS_RELEASE),
            clk=TdrResultWindow(earliest, latest, "CLK-BUS", "RST-ARST-BUS", _ATOMS_RELEASE),
            atoms=_ATOMS_RELEASE,
        )

    def reset(self, scope: str) -> None:
        """Apply the reset entry ``scope`` of the clock-reset model.

        RST-ARST-TCK and RST-SCAN clear the bridge: no operation pending, status 0, and a
        capture of address 0 and data 0 (S-TDR2AXI-0026, S-TDR2AXI-0052). They and
        RST-ARST-BUS, which leaves the bridge untouched, start the CDC clear sequence.
        RST-TRST and RST-DMACTIVE leave the model untouched (S-TDR2AXI-0054,
        S-TDR2AXI-0056).
        """
        if scope in _RESET_SCOPES_OTHER:
            return
        if scope in _RESET_SCOPES_TCK:
            self._states = (_RESET_BRIDGE,)
        elif scope not in _RESET_SCOPES_BUS:
            raise ValueError(f"unknown reset scope {scope!r}")
        self._settling = True

    def trst(self) -> None:
        """Apply a TAP reset of the host, asserted and released with TCK stopped.

        Nothing in tdr2axi changes (S-TDR2AXI-0054, S-TDR2AXI-0055, X-IEEE1149_1-trst): a
        pending operation completes and the sticky status holds until op 3.
        """
        self.reset("RST-TRST")

    def _update(
        self, state: _Bridge, op: int, addr: int, data: int
    ) -> list[tuple[_Bridge, DmiRequest | None, tuple[str, ...]]]:
        if state.pending is not None:
            if op == TdrOp.CLEAR:
                return [(replace(state, status=TdrStatus.SUCCESS), None, _ATOMS_OP3_PENDING)]
            return [(replace(state, status=TdrStatus.BUSY), None, _ATOMS_BUSY_UPDATE)]
        if state.status is TdrStatus.BUSY:
            if op == TdrOp.CLEAR:
                return [(replace(state, status=TdrStatus.SUCCESS), None, _ATOMS_OP3)]
            return [(state, None, _ATOMS_STICKY)]
        loaded = replace(state, addr=addr, data=TdrField.exact(data))
        if op not in (TdrOp.READ, TdrOp.WRITE):
            return [(loaded, None, _ATOMS_OP3 if op == TdrOp.CLEAR else _ATOMS_DECODE)]
        request = DmiRequest(TdrOp(op), addr ^ self._flip(), data, _ATOMS_DECODE)
        accepted = (replace(loaded, pending=TdrOp(op)), request, _ATOMS_DECODE)
        if not self._settling:
            return [accepted]
        return [
            accepted,
            (replace(state, status=TdrStatus.BUSY), None, _ATOMS_READY),
            (replace(loaded, status=TdrStatus.BUSY), None, _ATOMS_READY),
        ]

    def _observe(self, outcomes: tuple[_Bridge, ...], word: int) -> None:
        # Every change of the outcomes, this one included, builds a new tuple, so identity
        # tells whether they are the ones the observed capture left.
        if outcomes is not self._states:
            return
        op, addr, data = decode_tdr(word)
        kept = [
            state
            for state in outcomes
            if state.addr == addr and state.status == op and state.data.matches(data)
        ]
        self._states = _distinct(kept or outcomes)

    def _answer(
        self, state: _Bridge, *, busy: bool, allowed: tuple[int, ...], mask: int
    ) -> _Bridge:
        status = TdrStatus.BUSY if busy else state.status
        data = state.data
        if state.pending is TdrOp.READ:
            data = TdrField.exact(BUSY_MARKER) if busy else TdrField(allowed, mask & TDR_DATA_FIELD)
        return _Bridge(status, None, state.addr, data)

    def _image(self, state: _Bridge) -> TdrCaptureExpect:
        return TdrCaptureExpect(state.addr ^ self._flip(), state.data, state.status, _ATOMS_CAPTURE)

    def _flip(self) -> int:
        return 1 if self._negative else 0


def _distinct(states: Iterable[_Bridge]) -> tuple[_Bridge, ...]:
    return tuple(dict.fromkeys(states))
