# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The checks each tdr2axi test owes, read from the verification plan when the test runs.

``planned_checks`` reads ``trace/vplan.toml`` of this bench and returns the checks of the
cocotb plan items a test realizes: the items whose ``test`` is the test's name. The bench
bring-up realizes no item; it owes ``BRINGUP_CHECKS``, the checks its steps make.
``DEFERRED_CHECKS`` is the one list of planned checks that no test evaluates, each with
the owner that adds it. There is no SV twin: the bench has one cocotb realization.
"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path
from typing import Any

__all__ = [
    "BRINGUP_CHECKS",
    "BRINGUP_TEST",
    "DEFERRED_CHECKS",
    "PLAN_PATH",
    "PlanError",
    "planned_checks",
]

PLAN_PATH = Path(__file__).resolve().parents[2] / "trace" / "vplan.toml"
REALIZATION = "cocotb"

BRINGUP_TEST = "tdr2axi_bringup_test"
BRINGUP_CHECKS = frozenset(
    {
        "CHK-RESET-ASYNC-ANY-TIME",
        "CHK-DM-REGISTER-MAP",
        "CHK-SBA-SBCS-FIELDS",
        "CHK-TDR-NEVER-FAILED",
        "CHK-TDR-CAPTURE-EDGE",
        "CHK-AXISUB-RESET-VALID-LOW",
        "CHK-AXISUB-DRAIN",
        "CHK-NONVAC",
    }
)

# Planned checks that no test evaluates, and the owner that adds each
DEFERRED_CHECKS: dict[str, str] = {}


class PlanError(LookupError):
    """The plan has no cocotb item for the test."""


@cache
def _items() -> tuple[dict[str, Any], ...]:
    with PLAN_PATH.open("rb") as plan:
        items = tomllib.load(plan).get("item", [])
    return tuple(item for item in items if REALIZATION in item.get("realizations", ()))


def planned_checks(test: str) -> frozenset[str]:
    """The checks of every cocotb plan item ``test`` realizes, or ``BRINGUP_CHECKS``."""
    if test == BRINGUP_TEST:
        return BRINGUP_CHECKS
    checks = frozenset(
        check for item in _items() if item["test"] == test for check in item["checks"]
    )
    if not checks:
        raise PlanError(f"{test} realizes no {REALIZATION} item of {PLAN_PATH}")
    return checks
