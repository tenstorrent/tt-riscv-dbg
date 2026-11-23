# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""A make stand-in for the scheduler tests of run_sim.py: builds and runs without a simulator.

install() writes an executable named make that runs this file; a test puts its directory
first on the PATH the fake scheduler's jobs see. A command whose last argument ends in
results.xml is a run: it writes the cocotb JUnit file for the test TESTCASE or
COCOTB_TEST_FILTER names, failing when FAKE_MAKE_FAILING_TESTS lists that test, prints the
bench's TDR2AXI_CLOCKS line for the knob periods and FAKE_MAKE_RUN_LOG, and writes the
coverage file VERILATOR_COVERAGE_FILE names with the points of COVERS, or the -cm_dir
database SIM_ARGS names. Any other command is a build, which prints an AXI_ADDR_WIDTH error
and exits 2 when an argument contains FAKE_MAKE_BUILD_FAIL, else prints "built", writes a
VCS coverage build's simv.vdb and exits 0.

    python3 tb/tdr2axi/scripts/tests/fake_make.py -C <bench> SIM=verilator ... <target>
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

FAILING_TESTS = os.environ.get("FAKE_MAKE_FAILING_TESTS", "").split()
BUILD_FAIL = os.environ.get("FAKE_MAKE_BUILD_FAIL", "")
RUN_LOG = os.environ.get("FAKE_MAKE_RUN_LOG", "")
COMPILE_ARGS = os.environ.get("COMPILE_ARGS", "")
SIM_ARGS = os.environ.get("SIM_ARGS", "")
DRAWN_CLK_PS = 54322
JUNIT = """<testsuites name="results"><testsuite name="all" package="all">
<testcase name="{test}" classname="tests.x" time="0.1">{failure}</testcase>
</testsuite></testsuites>"""
FAILURE = '<failure message="Test failed" />'
# Each run's cover-property points by hierarchical name: two instances share c_twice.
COVERS = {
    "tb_top.u_fcov.c_seen": 1,
    "tb_top.u_fcov.c_never": 0,
    "tb_top.u_a.c_twice": 1,
    "tb_top.u_b.c_twice": 1,
}


def install(bin_dir: Path, python: str = sys.executable) -> None:
    """Write ``bin_dir/make``, which runs this file with ``python``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "make"
    script = shlex.quote(str(Path(__file__).resolve()))
    wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} {script} "$@"\n')
    wrapper.chmod(0o755)


def coverage_dat(covers: Mapping[str, int]) -> str:
    """A Verilator coverage.dat with a cover-property point per hierarchical name in
    ``covers``, and a toggle point whose signal is a cover's label and whose instance name
    starts with the cover-property point type, user."""
    points = [
        ({"t": "user", "page": "v_user/fcov", "o": name.rsplit(".", 1)[-1], "h": name}, count)
        for name, count in covers.items()
    ]
    points.append(({"t": "toggle", "page": "v_toggle/top", "o": "c_seen", "h": "user_top"}, 7))
    lines = ["# SystemC::Coverage-3"]
    for fields, count in points:
        text = "".join(f"\x01{key}\x02{value}" for key, value in {"f": "x.sv", **fields}.items())
        lines.append(f"C '{text}' {count}")
    return "\n".join(lines) + "\n"


def knobs(argv: Sequence[str]) -> dict[str, str]:
    """The VAR=VALUE arguments of a make command."""
    return dict(arg.split("=", 1) for arg in argv if re.match(r"[A-Za-z_]\w*=", arg))


def simulate(argv: Sequence[str]) -> int:
    """Write a run's results file, coverage and log lines; exit status 0."""
    values = knobs(argv)
    test = values.get("TESTCASE") or values.get("COCOTB_TEST_FILTER", "").strip("^$")
    test = test.replace("\\", "")
    results = Path(argv[-1])
    results.parent.mkdir(parents=True, exist_ok=True)
    failure = FAILURE if test in FAILING_TESTS else ""
    results.write_text(JUNIT.format(test=test, failure=failure))
    clk = values.get("CLK_PERIOD_PS", "1000")
    clk_ps = DRAWN_CLK_PS if clk == "random" else int(clk)
    tck_ps = int(values.get("TCK_PERIOD_PS", "20000"))
    print("sim")
    print(
        f"     0.00ns INFO     cocotb.tdr2axi    TDR2AXI_CLOCKS clk_ps={clk_ps}"
        f" tck_ps={tck_ps} phase_ps=137"
    )
    if RUN_LOG:
        print(RUN_LOG)
    if "VERILATOR_COVERAGE_FILE" in values:
        Path(values["VERILATOR_COVERAGE_FILE"]).write_text(coverage_dat(COVERS))
    cm_dir = re.search(r"-cm_dir (\S+)", SIM_ARGS)
    if cm_dir:
        Path(cm_dir.group(1)).mkdir(parents=True, exist_ok=True)
    return 0


def build(argv: Sequence[str]) -> int:
    """Pretend to build a model; exit status 2 on the failing argument, else 0."""
    if BUILD_FAIL and any(BUILD_FAIL in arg for arg in argv):
        print("%Error: AXI_ADDR_WIDTH must be 2..64")
        return 2
    values = knobs(argv)
    bench = Path(argv[argv.index("-C") + 1]) if "-C" in argv else Path.cwd()
    if values.get("SIM") == "vcs" and "-cm " in COMPILE_ARGS and "SIM_BUILD" in values:
        (bench / values["SIM_BUILD"] / "simv.vdb").mkdir(parents=True, exist_ok=True)
    print("built")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run or build as the command's target says."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    return simulate(args) if args[-1].endswith("results.xml") else build(args)


if __name__ == "__main__":
    raise SystemExit(main())
