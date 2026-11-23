# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Build parameters of one tdr2axi compile, and the checks a build can exercise.

``Tdr2axiBuild`` holds the tb_top parameters the base test reads from the ``cfg_*`` nets.
``exercises`` says whether a check ID has stimulus on this build: the AXI4 attribute,
size, burst and ID-order checks need USE_AXI_LITE = 0, the AXI4-Lite full-width check
needs it 1, and a check whose timing item holds the bounds of one SYNC_STAGES needs that
SYNC_STAGES. The base test drops a required ID its build cannot exercise. There is no SV
twin: the bench has one cocotb realization.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AXI4_ONLY_IDS",
    "LITE_ONLY_IDS",
    "SYNC_STAGES_IDS",
    "Tdr2axiBuild",
]

# sbcs fields (RISC-V External Debug Support 0.13.2, sbcs)
SBCS_SBVERSION_1 = 0x2000_0000
SBCS_SBACCESS_LSB = 17
SBCS_SBASIZE_LSB = 5
SBCS_SIZES_TO_32 = 0x07
SBCS_SIZES_TO_64 = 0x0F

SIZE_64 = 3

AXI4_ONLY_IDS = frozenset(
    {
        "CHK-AXI-AXI4-SIZE",
        "CHK-AXISUB-BOUNDARY-4K",
        "CHK-AXISUB-BURST-LENGTH",
        "CHK-AXISUB-CACHE-LEGAL",
        "CHK-AXISUB-ID-ORDER",
        "CHK-AXISUB-LAST-BEAT",
        "CHK-AXISUB-SIZE-WIDTH",
    }
)
LITE_ONLY_IDS = frozenset({"CHK-AXI-LITE-FULL-WIDTH"})
# Checks whose timing item holds the bounds of one SYNC_STAGES: T-TDR2AXI-tck-reset-release
# and T-TDR2AXI-resp-to-tdr (3), T-TDR2AXI-tck-reset-release-ss4 and
# T-TDR2AXI-resp-to-tdr-ss4 (4)
SYNC_STAGES_IDS = {
    "CHK-RESET-SYNC-RELEASE": 3,
    "CHK-RESET-SYNC-RELEASE-SS4": 4,
    "CHK-TDR-RESULT-LATENCY": 3,
    "CHK-TDR-RESULT-LATENCY-SS4": 4,
    "CHK-TDR-TCK-STOP-HOLD": 3,
    "CHK-TDR-TCK-STOP-HOLD-SS4": 4,
}


@dataclass(frozen=True)
class Tdr2axiBuild:
    """Build parameters of tb_top, read from its cfg_* nets."""

    lite: bool
    addr_width: int
    data_width: int
    prot: int
    cache: int
    sync_stages: int

    @property
    def nbytes(self) -> int:
        """Bytes per data beat."""
        return self.data_width // 8

    @property
    def max_size(self) -> int:
        """Largest sbaccess the bus carries."""
        return self.nbytes.bit_length() - 1

    @property
    def sbcs_reset(self) -> int:
        """sbcs with dmactive = 1 after reset: sbversion 1, sbaccess 2, sbasize, the sizes."""
        sizes = SBCS_SIZES_TO_64 if self.max_size == SIZE_64 else SBCS_SIZES_TO_32
        return (
            SBCS_SBVERSION_1
            | (2 << SBCS_SBACCESS_LSB)
            | (self.addr_width << SBCS_SBASIZE_LSB)
            | sizes
        )

    def exercises(self, check_id: str) -> bool:
        """Whether this build has stimulus for ``check_id``."""
        if check_id in AXI4_ONLY_IDS:
            return not self.lite
        if check_id in LITE_ONLY_IDS:
            return self.lite
        if check_id in SYNC_STAGES_IDS:
            return self.sync_stages == SYNC_STAGES_IDS[check_id]
        return True
