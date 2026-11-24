# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Check evidence of one tdr2axi test: pass counts, failures, required IDs and CHK-NONVAC.

``Tdr2axiChecker`` is where every verdict of a tdr2axi test lands. Monitors, the TDR host
and the scoreboard report through ``record``, which counts a pass, or logs a failure that
fails the test at ``finalize``. A test's own comparisons go through ``expect_true`` and
``expect_equal``, which also fail the test at once. A test that did not create the
stimulus its checks need fails through ``require_stimulus``.

``required_ids`` hold CHK-NONVAC from construction, and ``add_required`` adds to them
until ``finalize``, which runs once: it grades CHK-NONVAC, which fails when a required ID
other than itself logged no pass, logs the pass count of every ID, and fails the test on
any failure. A pass of an ID in ``unarmed_ids`` is not counted, so an unarmed check never
shows as passing, and its failures fail the test. There is no SV twin: the bench has one
cocotb realization.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable

__all__ = [
    "NONVAC",
    "Tdr2axiCheckError",
    "Tdr2axiChecker",
    "Tdr2axiInconclusiveError",
]

NONVAC = "CHK-NONVAC"


class Tdr2axiCheckError(AssertionError):
    """A check failed."""


class Tdr2axiInconclusiveError(AssertionError):
    """The test did not create the stimulus its required checks need."""


class Tdr2axiChecker:
    """Evidence of one test: counts passes, keeps failures and grades the required IDs."""

    def __init__(
        self, *, name: str, required_ids: Iterable[str], unarmed_ids: Iterable[str] = ()
    ) -> None:
        self.name = name
        self.unarmed_ids = frozenset(unarmed_ids)
        self._required: set[str] = set()
        self._log = logging.getLogger("cocotb.tdr2axi.checker")
        self._passes: Counter[str] = Counter()
        self._failures: list[str] = []
        self._finalized = False
        self.add_required(required_ids)
        if NONVAC not in self._required:
            raise ValueError(f"{name}: required_ids must hold {NONVAC}")

    @property
    def required_ids(self) -> frozenset[str]:
        """The IDs that must each log a pass for CHK-NONVAC to pass."""
        return frozenset(self._required)

    def add_required(self, check_ids: Iterable[str]) -> None:
        """Require ``check_ids`` as well; an unarmed ID cannot be required."""
        if self._finalized:
            raise RuntimeError(f"{self.name}: required IDs are fixed once finalize ran")
        added = frozenset(check_ids)
        unarmed_required = added & self.unarmed_ids
        if unarmed_required:
            raise ValueError(
                f"{self.name}: unarmed IDs cannot be required: {sorted(unarmed_required)}"
            )
        self._required |= added

    def record(self, check_id: str, *, passed: bool, detail: str) -> None:
        """Count a pass, or log a failure that fails the test at ``finalize``."""
        if passed:
            if check_id not in self.unarmed_ids:
                self._passes[check_id] += 1
            return
        message = f"{check_id} FAIL: {detail}"
        self._log.error("%s", message)
        self._failures.append(message)

    def expect_true(self, check_id: str, *, passed: bool, detail: str) -> None:
        """Record a comparison of the test, and fail the test now when it failed."""
        self.record(check_id, passed=passed, detail=detail)
        if not passed:
            raise Tdr2axiCheckError(f"{check_id} FAIL: {detail}")

    def expect_equal(self, check_id: str, observed: int, expected: int, what: str) -> None:
        """``observed`` equals ``expected``; the failure detail names both and their XOR."""
        self.expect_true(
            check_id,
            passed=observed == expected,
            detail=f"{what}: observed 0x{observed:x}, expected 0x{expected:x}, "
            f"diff 0x{observed ^ expected:x}",
        )

    def require_stimulus(self, *, created: bool, reason: str) -> None:
        """Fail the test as inconclusive when it did not create the stimulus it needs."""
        if created:
            return
        message = f"{self.name} inconclusive: {reason}"
        self._log.error("%s", message)
        raise Tdr2axiInconclusiveError(message)

    def passes(self, check_id: str) -> int:
        """The passes ``check_id`` has logged."""
        return self._passes[check_id]

    def finalize(self) -> None:
        """Grade CHK-NONVAC over the required IDs, log the evidence and fail on any failure."""
        if self._finalized:
            raise RuntimeError(f"{self.name}: finalize runs once")
        self._finalized = True
        missing = sorted(i for i in self._required - {NONVAC} if not self._passes[i])
        self.record(
            NONVAC, passed=not missing, detail=f"required IDs with no pass: {' '.join(missing)}"
        )
        for check_id, count in sorted(self._passes.items()):
            self._log.info("%s PASS: %d comparisons", check_id, count)
        if not missing:
            self._log.info(
                "%s: all %d required IDs passed: %s",
                self.name,
                len(self._required),
                " ".join(sorted(self._required)),
            )
        if self._failures:
            raise Tdr2axiCheckError("\n".join(self._failures))
