#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Run the tdr2axi cocotb tests of testlists/all.toml through the bench Makefile.

    python3 tb/tdr2axi/scripts/run_sim.py --list
    python3 tb/tdr2axi/scripts/run_sim.py --group smoke --jobs 8
    python3 tb/tdr2axi/scripts/run_sim.py --group signoff --jobs 8 --cov
    python3 tb/tdr2axi/scripts/run_sim.py --test tdr2axi_sba_single_test \\
        --target axil_a32_d32 --seed 7 --ratio 1:2 --wave
    python3 tb/tdr2axi/scripts/run_sim.py --group nightly --target all --reseed --jobs 8
    python3 tb/tdr2axi/scripts/run_sim.py --expect-build-fail axi4_a64_d64:AXI_ADDR_WIDTH=2 \\
        --expect-message 'AXI_ADDR_WIDTH must be'
    TDR2AXI_DV_SITE=<site.toml> python3 tb/tdr2axi/scripts/run_sim.py --executor lsf \\
        --sim vcs --group signoff --reseed --cov --cov-merge

Each run gets sim/<test>.<target>.<seed>.<timestamp>/ under the bench, holding sim.log,
results.xml (the cocotb JUnit file), its coverage data (coverage.dat on Verilator, and
coverage.vdb on VCS under --cov) and with --wave waves.fst. A run passes when its JUnit
file records the test with no failure, because cocotb exits 0 when a test fails, and its
log's TDR2AXI_CLOCKS line reports the clock periods the run asked for; a run whose log has
no such line, or other periods, is an error. Every invocation writes a JSON manifest of
its runs, with the requested ratio and periods beside the periods and phase that ran.

A build target is one of the Makefile's BUILDS. Builds live in
sim/build/<sim>_<target>[_waves][_cov]/, one per target, made once before the runs on it
fan out. A build is remade from scratch when its parameter values, make variables or
compile environment differ from the ones recorded beside its model in run_sim.build.json,
or, on Verilator, when a source is newer than the model. --cov compiles Verilator with
--coverage, or VCS with -cm line+cond+fsm+tgl+branch and a -cm_dir per run; Verilator
always writes the user coverage the Makefile compiles in.

The signoff group runs every test of the group, or every test when the testlist has no
such group, with its reseed count on every target, unless a seed or target option is given.

The builds, the reference build (the Makefile's default CONFIG), the bring-up-only builds
and the TCK period come from the Makefile. The clock ratios come from the CFG-CLK-RATIO
dimension of trace/configs.toml: a ratio a:b is i_clk:TCK in frequency, so a run sets
CLK_PERIOD_PS = TCK_PERIOD_PS * b / a. The ratio random passes CLK_PERIOD_PS=random: the
bench draws i_clk from the seed between 20:1 and 1:8 of TCK and logs the period it drew,
which must lie in that span. Without --ratio a run uses the dimension's default, except
under --reseed, where each test runs at least six seeds rotating through six slots, the
named ratios and random, so every test runs at every named ratio and at random.

An expect-build-fail check builds an illegal parameter set in its own run directory and
passes when the build, or the model's time-0 parameter checks, stop with a line matching
the --expect-message that follows the check (default 'Fatal:'); it fails when the model
comes up and passes the bring-up test. The check runs make silently and drops make's own
messages, so the pattern meets the tools' output and never a command line or a path. On
Verilator it builds with -Werror-USERERROR and -Werror-USERFATAL, so an elaboration-time
$error or $fatal stops the build as it does on other simulators. On VCS it builds with
+error+100, so the expected message prints even after a dependency's errors.

A testlist entry with build_cases and build_check is a build-check test: each case names
a target, optionally a param with values, and either a message pattern (an illegal set
that must stop with it) or legal = true (a set that must build and pass bring-up). An
illegal case may add message_<sim>, the pattern on that simulator, whose elaboration
message differs; message serves every simulator without one. Each
seed runs every case once on its own target, with the param's value drawn from the seed,
and writes "<build_check> PASS: ..." or "<build_check> FAIL: ..." to check.log in its run
directory, which its manifest entry names. --target keeps only the cases on the builds it
names, each with the value the full run of that seed draws. A legal case's bring-up run
records its coverage data wherever an ordinary run records its own, and under --cov the
case builds with coverage; an illegal case records none.

A testlist entry with expect_fail = true expects every run to fail: a run passes, and
prints XFAIL, only when it fails and a line of its log matches the entry's pin, the
defect's own signature; it fails when it passes or fails without that line.
expect_fail_match is the pin on Verilator and on any simulator with no pin of its own;
expect_fail_match_vcs is the pin on VCS. expect_fail_allow (a pattern or a list) names
the lines the same defect brings beside the pin, in any log order, and counts for nothing
without the pin's line. Every other error line of the run must match the pin or the allow
list: a check's FAIL, a cocotb ERROR record, a simulator error, an exception and the
exception that ended the test grade the run FAIL otherwise, and so does a failing run
whose make exits nonzero. A VCS assertion report grades as one line, its header joined to
its message, and when the report that stopped VCS is a $fatal the pin or the allow list
matches, cocotb's shutdown records after it count as part of it. Every run of such a test
names its pin in the manifest's "expected_fail_match" and its allow list in
"expected_fail_allow", and a run graded XFAIL carries "expected_fail": true.

A testlist entry's target (a build or a list of builds) and ratio (a ratio, a list, or
all) are the builds and clock ratios it runs on when the command line names none, as
under --reseed and the signoff group; --target replaces the entry's builds and --ratio
the entry's ratios. Each seed count applies per build and ratio. Only the bring-up test runs
on the Makefile's BRINGUP_ONLY builds, and any test on those its entry's target names, or
its bringup_targets names whenever the selected builds include them.

The manifest follows the runs manifest of the trace store: each run has "kind" normal,
the code "revision" (the commit, plus a hash of git diff HEAD when the tree differs) and
"realization" cocotb. --mutation NAME --targets CHK-A,CHK-B records every run of the
command as kind mutation, naming NAME and the checks it must fail; a NAME of the form
VAR=VALUE is also set in each simulation's environment, as a negative knob is armed, and
a mutation run is never graded as an expected failure.

--wave on VCS passes +TDR2AXI_VCD=<run directory>/waves.vcd when the bench's tb/tb_top.sv
dumps VCD behind that plusarg; the site file's [simulators.vcs] wave settings replace it
with the arguments and sources of another format.

--cov-merge merges the coverage of the passing runs, expected failures left out, into
sim/cov_merge.<timestamp>/ once the runs end: verilator_coverage on Verilator, and on VCS
the command cov_merge_argv names in the site file, with each coverage build's simv.vdb ahead
of the runs' databases. On VCS, when the runs span more than one testlist build, the same
command also merges each testlist build's runs alone into sim/cov_merge.<timestamp>/<build>/;
the runs of a legal build check join only the cross-build merge. VCS joins builds whose
parameters differ only through the flexible-merge options of cov_merge_argv, and even then
keeps toggle, and some conditions, only for instances shaped like the first build it reads.
Sign-off reads toggle, and any condition the cross-build merge misses on a parameter-shaped
instance, from the per-build merges. Every other figure comes from the cross-build merge.
The manifest records the merges under "coverage_merge", each build's under its "builds".

--cov also writes each test's cover hits to sim/cover_hits.<timestamp>/<test>.json as
{"test", "hits", "runs", "expected_fail_runs"}, the form the trace store's results check
reads: "hits" maps the label of every cover property in the design to its matches, summed
over the instances that share the label and over the test's passing runs. Expected failures
count, as do the bring-up runs of a build-check test's legal cases; failing runs do not, and
a --mutation command writes no file. A test with a passing run that left no coverage data
gets no file and fails the run; its manifest entry counts its passing runs and, when some
of them left data, names those that did not. On Verilator the counts come from each run's
coverage.dat. On VCS one merge job per test runs the command cov_hits_argv names in
the site file over the simv.vdb of each build the test's runs used and over their databases,
and the runner reads the matches from the text assertion report it writes; without
cov_hits_argv no file is written, and a note on stderr says why. The manifest records each
file under "cover_hits".

--executor lsf runs every build, build check and run as an LSF job (lsf_executor.py): each
model builds once as a job, its coverage build under --cov; the runs of a build go out once
it succeeds, as job arrays unless the site turns arrays off, and a failed build errors its
runs with the build log named. The runner polls the jobs, grades each run from its run
directory as it grades a local run and writes the same manifest. Once the runs end, each
coverage merge and each test's cover-hit merge is a job of its own with the build
resources. An interrupt, SIGTERM or SIGHUP kills every queued or running job; --timeout
kills a job that runs longer, and the site's max_pend_s one that waits longer in the queue.
When the scheduler stops answering, the manifest holds every settled result and an error
for each run that did not settle. The site layer (site_layer.py) supplies every site value:
the file TDR2AXI_DV_SITE names, else scripts/site.local.toml, which git ignores, holds the
queue and resource defaults, extra scheduler arguments, the shell file a job sources before
make, and the limits; scripts/site.local.example.toml lists the keys. Without a site file a
job asks for no more than a name and a log. --dry-run with --executor lsf also prints the
bsub commands.

Needs Python 3.12 or later, cocotb, bender, GNU make and Verilator (or VCS); --executor lsf
needs bsub, bjobs and bkill on this host and the rest where the jobs run. Exit status: 0
when every run, merge and cover-hit file passes, 1 when any fails or errors, 2 on a usage
error, a missing tool or a scheduler that stops answering, 130 on an interrupt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

import site_layer
from lsf_executor import LsfExecutor, Outcome, SchedulerError, Step, Task, render_argv

SCRIPT_DIR = Path(__file__).resolve().parent
BENCH_DIR = SCRIPT_DIR.parent
MAKEFILE = BENCH_DIR / "Makefile"
TESTLIST = BENCH_DIR / "testlists" / "all.toml"
CONFIGS = BENCH_DIR / "trace" / "configs.toml"
SIM_DIR = BENCH_DIR / "sim"
# Relative to the bench: the Makefile places the file list at $(CURDIR)/$(SIM_BUILD).
BUILD_ROOT = Path("sim") / "build"

BRINGUP_TEST = "tdr2axi_bringup_test"
RATIO_DIMENSION = "CFG-CLK-RATIO"
SIMULATORS = ("verilator", "vcs")
# The files a build makes; a run names them with make -o so that it never rebuilds them.
MODEL_FILES = {"verilator": ("Vtop.mk", "Vtop"), "vcs": ("simv",)}
# From this cocotb release the test filter is COCOTB_TEST_FILTER and the seed COCOTB_RANDOM_SEED.
COCOTB_RENAMED_KNOBS = 2
SIGNOFF_GROUP = "signoff"
VCS_COVERAGE = "line+cond+fsm+tgl+branch"
# The build identity a model was made with, kept in its build directory.
BUILD_KEY_FILE = "run_sim.build.json"
# The bench logs this once per test with the periods and i_clk phase it runs.
RANDOM_RATIO = "random"
# The i_clk:TCK span the bench draws a random ratio's i_clk period from.
RANDOM_RATIO_SPAN = ("20:1", "1:8")
CLOCKS_LINE = re.compile(r"\bTDR2AXI_CLOCKS\s+clk_ps=(\d+)\s+tck_ps=(\d+)\s+phase_ps=(\d+)")
# make's own messages, which name targets and directories rather than tool output.
MAKE_MESSAGE = re.compile(r"^\S*make(\[\d+\])?: ")
# make without recipe echo or directory messages, for checks that match tool output.
QUIET_MAKE_FLAGS = ("-s", "--no-print-directory")
DEFAULT_EXPECT_MESSAGE = r"Fatal:"
# Testlist fields: an expected failure and its pins, and the builds of a build-check test.
EXPECTED_FAIL_FIELD = "expect_fail"
PIN_FIELD = "expect_fail_match"
ALLOW_FIELD = "expect_fail_allow"
BUILD_CASES_FIELD = "build_cases"
CASE_MESSAGE_FIELD = "message"
BUILD_CHECK_FIELD = "build_check"
RATIO_FIELD = "ratio"
BRINGUP_TARGETS_FIELD = "bringup_targets"
REALIZATION = "cocotb"
NORMAL_RUN = "normal"
MUTATION_RUN = "mutation"
CHECK_ID = re.compile(r"CHK-[A-Z0-9]+(?:-[A-Z0-9]+)*")
# Lines that report an error: a check's FAIL, a cocotb ERROR or CRITICAL record, a
# simulator error or fatal, and the last line of a Python traceback.
CHECK_FAIL = re.compile(r"\bCHK-[A-Z0-9][A-Z0-9_-]*\s+FAIL\b(?!=)")
COCOTB_ERROR = re.compile(r"^\s*[-.\d]+[a-z]*s\s+(?:ERROR|CRITICAL)\s")
SIM_ERROR = re.compile(r"%Error|%Fatal|^\s*(?:Error|Fatal)[-:]")
EXCEPTION = re.compile(r"^\s*(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception)(?::\s|$)")
# A cocotb log record; the lines after one that do not start a record continue it.
COCOTB_RECORD = re.compile(r"^\s*[-.\d]+[a-z]*s\s+[A-Z]+\s")
# cocotb's record of a failed test, which the traceback of the exception that ended it
# continues, that exception last.
TEST_FAILED = re.compile(r"^\s*[-.\d]+[a-z]*s\s+INFO\s+cocotb\.regression\s+\S+ failed\b")
# A VCS assertion report's header; its message follows the time on the same line or
# fills the next line.
VCS_REPORT = re.compile(
    r'^\s*(?P<kind>Error|Fatal): "[^"]+", \d+: \S+: at time \d+ ?[a-z]*s\s*(?P<rest>:.*)?$'
)
# The records cocotb logs when the simulator stops the run, on each simulator whose
# stopping $fatal leaves a results file. Verilator aborts on a $fatal without one, which
# grades the run an error.
SHUTDOWN_RECORDS = {
    "vcs": re.compile(
        r"Failing test at simulator request before test run completion: "
        r"|Test error has lead to simulator shutting us down"
        r"|^\s*(?:cocotb\.result\.)?SimFailure: "
    ),
}
KNOB_SETTING = re.compile(r"[A-Za-z_]\w*=\S*")
# The log a build check of a testlist entry writes its check line to.
CHECK_LOG = "check.log"
# Verilator reports an elaboration-time $error or $fatal under these warning codes.
VERILATOR_STRICT = "-Werror-USERERROR -Werror-USERFATAL"
# VCS stops a compile at 10 errors by default; a build check's expected message may follow
# that many errors from a dependency.
VCS_STRICT = "+error+100"
STRICT_OPTIONS = {"verilator": VERILATOR_STRICT, "vcs": VCS_STRICT}
DEFAULT_TIMEOUT_S = 3600.0
TERM_GRACE_S = 10.0
EXECUTORS = ("local", "lsf")
# Signals that stop a scheduler run as Ctrl-C does: a kill, and the login session ending.
INTERRUPT_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
SITE_FILE = SCRIPT_DIR / site_layer.SITE_FILE_NAME
SITE_FILE_ENV = os.environ.get(site_layer.SITE_ENV, "")
# The bench top dumps VCD to the file this plusarg names; relative to the bench.
VCD_PLUSARG = "TDR2AXI_VCD"
TB_TOP_FILE = Path("tb") / "tb_top.sv"
VCS_WAVE_FILE = "waves.vcd"
# The --cov-merge command and output on a simulator the site file gives none for.
MERGE_ARGV: dict[str, site_layer.Template] = {
    "verilator": ("verilator_coverage", "--write", "{output}", "{inputs}")
}
MERGE_OUTPUT = {"verilator": "merged.dat", "vcs": "merged.vdb"}
# The database a VCS coverage build writes beside simv, which holds the design's shape.
VCS_BUILD_COVERAGE = "simv.vdb"
MERGE_INPUTS = "inputs.txt"
NO_COVERAGE = "no passing run left coverage data"
# A coverage.dat point: its fields, each \x01 key \x02 value, then its count.
VERILATOR_POINT = re.compile(r"^C '(.*)' (\d+)$")
# The type of a cover property's point in a coverage.dat.
VERILATOR_COVER_TYPE = "user"
# A cover-hit merge writes its text assertion report into HITS_REPORT_DIR; in the report a
# line ending in a colon opens a section, and a cover-property section's header row names
# the column of each cover's matches.
HITS_REPORT_DIR = "report"
VCS_COVER_REPORT = "asserts.txt"
REPORT_SECTION = re.compile(r"^[A-Z][\w ]*:\s*$")
COVER_SECTION = re.compile(r"^Cover Properties\b")
MATCHES_HEADER = "MATCHES"
COCOTB_DEFAULT_MAJOR = 1
SEED_LIMIT = 2**31 - 1
PASS, FAIL, ERROR = "pass", "fail", "error"
STATUSES = (PASS, FAIL, ERROR)
INSTALL_HINTS = {
    "make": "install GNU make",
    "bender": "install bender: https://github.com/pulp-platform/bender",
    "cocotb-config": "activate a Python environment with cocotb 1.9 (pip install cocotb==1.9.2)",
    "verilator": "install Verilator 5.036 or later",
    "vcs": "set up Synopsys VCS so that vcs is on PATH",
}
SCHEDULER_HINT = "run on a host where the LSF commands are on PATH"


class ConfigError(Exception):
    """A testlist, Makefile, configuration model or command line the runner cannot use."""


class Launch(Protocol):
    """Runs one command with its output in a log file."""

    timeout_s: float

    def __call__(self, cmd: Sequence[str], log: Path) -> int | None:
        """The command's exit code, or None when it times out or launching has stopped."""

    def stop(self) -> None:
        """Terminate every live command and refuse new ones."""


@dataclass(frozen=True)
class BuildCase:
    """One build of a build-check test: a target, with ``param`` drawn from ``values`` per
    seed. ``message`` is the pattern an illegal build stops with; None marks a legal build,
    which must elaborate and pass bring-up. ``sim_messages`` maps a simulator to the pattern
    that replaces ``message`` on it."""

    target: str
    param: str | None = None
    values: tuple[str, ...] = ()
    message: str | None = None
    sim_messages: tuple[tuple[str, str], ...] = ()

    def message_on(self, sim: str) -> str | None:
        """The pattern an illegal build stops with on simulator ``sim``; None when legal."""
        return dict(self.sim_messages).get(sim, self.message)


@dataclass(frozen=True)
class ListedTest:
    """One [[tests]] record: the cocotb test name, its seed count, and the builds and clock
    ratios it runs on when the command line names none.

    ``pins`` maps a simulator to the pattern a failing log of an expected-fail test must
    match, the empty key holding ``expect_fail_match``, which serves every simulator
    without a pin of its own, and ``allow`` holds the patterns of the lines the same defect
    brings beside the pin. A test with ``build_cases`` runs those builds and grades
    ``build_check`` on them instead of simulating. ``bringup_targets`` are the bring-up-only
    builds the test also runs on when they are selected.
    """

    name: str
    reseed: int
    targets: tuple[str, ...] = ()
    expected_fail: bool = False
    pins: tuple[tuple[str, str], ...] = ()
    build_check: str | None = None
    build_cases: tuple[BuildCase, ...] = ()
    ratios: tuple[str, ...] = ()
    bringup_targets: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()

    def pin(self, sim: str) -> str | None:
        """The expected-failure pattern on simulator ``sim``; None unless expected to fail."""
        if not self.expected_fail:
            return None
        by_sim = dict(self.pins)
        return by_sim.get(sim, by_sim.get(""))


@dataclass(frozen=True)
class Group:
    """One [[groups]] record."""

    name: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Testlist:
    """The tests and groups of the TOML testlist, in file order."""

    tests: tuple[ListedTest, ...]
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class BenchMake:
    """What the runner reads from the Makefile."""

    builds: tuple[str, ...]
    reference: str
    bringup_only: frozenset[str]
    params: tuple[str, ...]
    tck_period_ps: int
    rows: Mapping[str, tuple[str, ...]]

    def param_values(
        self, build: str, overrides: Sequence[tuple[str, str]] = ()
    ) -> tuple[tuple[str, str], ...]:
        """A build's parameter values from its build_<name> row, with overrides applied."""
        values = dict(zip(self.params, self.rows[build], strict=True))
        values.update(overrides)
        return tuple(values.items())


@dataclass(frozen=True)
class Ratio:
    """A clock ratio i_clk:TCK and the periods that realize it.

    ``clk_period_ps`` is None for the random ratio, whose i_clk period the bench draws.
    """

    name: str
    clk_period_ps: int | None
    tck_period_ps: int


@dataclass(frozen=True)
class Bench:
    """The bench's Makefile, testlist and clock ratios."""

    make: BenchMake
    testlist: Testlist
    ratios: tuple[Ratio, ...]
    default_ratio: Ratio

    @property
    def random_ratio(self) -> Ratio:
        """The ratio whose i_clk period the bench draws from the seed."""
        return Ratio(RANDOM_RATIO, None, self.make.tck_period_ps)

    @property
    def slots(self) -> tuple[Ratio, ...]:
        """The named ratios and random: what --reseed rotates through and --ratio all means."""
        return (*self.ratios, self.random_ratio)


@dataclass(frozen=True)
class Selection:
    """The tests, builds, ratios, seeding, simulator and waves a command line asks for.

    ``default_builds`` marks ``builds`` as the default of a command that names no target,
    which a test's own targets replace. ``named_builds`` marks builds the command line
    names, which a test's own targets do not narrow.
    """

    tests: tuple[ListedTest, ...]
    builds: tuple[str, ...]
    ratios: tuple[Ratio, ...] = ()
    seed: int | None = None
    seeds: int = 1
    reseed: bool = False
    sim: str = "verilator"
    wave: bool = False
    cov: bool = False
    default_builds: bool = False
    named_builds: bool = False


@dataclass(frozen=True)
class Mutation:
    """The fault every run of a command carries and the checks it must fail."""

    name: str
    targets: tuple[str, ...]

    @property
    def env(self) -> tuple[str, ...]:
        """The environment setting a VAR=VALUE name arms in each simulation; else none."""
        return (self.name,) if KNOB_SETTING.fullmatch(self.name) else ()


@dataclass(frozen=True)
class ExpectFail:
    """A build, with parameter overrides, that must not elaborate."""

    build: str
    overrides: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RunSpec:
    """One simulation, or one build check: an expect-build-fail check when expect_message
    is set, an expect-build-pass check when ``legal`` is.

    ``expected_fail`` is the pin of a simulation the testlist expects to fail, and
    ``expected_fail_allow`` the patterns of the lines its defect brings beside the pin. A
    build check of a testlist entry names that entry in ``owner`` and the check it grades in
    ``check_id``; ``test`` is the bring-up test its model starts with.
    """

    test: str
    build: str
    seed: int
    ratio: Ratio
    run_dir: Path
    build_dir: Path
    overrides: tuple[tuple[str, str], ...] = ()
    expect_message: str | None = None
    params: tuple[tuple[str, str], ...] = ()
    expected_fail: str | None = None
    legal: bool = False
    owner: str | None = None
    check_id: str | None = None
    expected_fail_allow: tuple[str, ...] = ()

    @property
    def is_build_check(self) -> bool:
        """Whether this spec builds and grades a parameter set instead of running a test."""
        return self.expect_message is not None or self.legal

    @property
    def run_kind(self) -> str:
        """``test``, ``expect-build-fail`` or ``expect-build-pass``."""
        if self.legal:
            return "expect-build-pass"
        return "test" if self.expect_message is None else "expect-build-fail"


@dataclass(frozen=True)
class Clocks:
    """The clock periods and i_clk phase a run's log reports it ran with."""

    clk_ps: int
    tck_ps: int
    phase_ps: int


@dataclass(frozen=True)
class RunResult:
    """The verdict of one RunSpec and where its evidence is."""

    spec: RunSpec
    status: str
    reason: str
    duration_s: float
    command: str
    log: Path | None
    results: Path | None
    build_log: Path | None
    coverage: Path | None = None
    clocks: Clocks | None = None
    started: str = ""


@dataclass(frozen=True)
class SimTool:
    """The simulator a run uses and the cocotb release driving it."""

    name: str
    version: str
    cocotb_major: int


@dataclass(frozen=True)
class BuildJob:
    """One model to build in one build directory, with the parameter values it compiles in.

    ``extra`` holds the compile arguments the site layer adds.
    """

    build: str
    build_dir: Path
    overrides: tuple[tuple[str, str], ...] = ()
    wave: bool = False
    cov: bool = False
    params: tuple[tuple[str, str], ...] = ()
    strict: bool = False
    extra: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedBuild:
    """A build's make command and log, and the key its model records once it is built."""

    cmd: tuple[str, ...]
    log: Path
    key_file: Path
    key: str


@dataclass(frozen=True)
class Context:
    """How builds and runs execute: simulator, launcher, waves, coverage, make jobs, rebuild.

    ``quiet`` runs make without recipe echo or directory messages; ``env`` holds the
    VAR=VALUE settings every simulation runs with; ``site`` is the site layer's table for
    the simulator.
    """

    sim: SimTool
    launch: Launch
    wave: bool = False
    make_jobs: int = 1
    rebuild: bool = False
    cov: bool = False
    quiet: bool = False
    env: tuple[str, ...] = ()
    site: site_layer.SimSite = field(default_factory=site_layer.SimSite)


@dataclass(frozen=True)
class MergeResult:
    """One --cov-merge: its status, merged database, input count, command and log, and the
    build whose runs alone it merges, None for the merge of every build."""

    status: str
    output: Path
    inputs: int
    command: str
    log: Path
    reason: str = ""
    build: str | None = None


@dataclass(frozen=True)
class HitsResult:
    """One test's cover-hit file: its status, where it goes, the runs it counts and how many
    of them are expected failures, and on VCS its merge's command and log."""

    test: str
    status: str
    path: Path
    runs: int
    expected_fail_runs: int
    command: str = ""
    log: Path | None = None
    reason: str = ""


@dataclass(frozen=True)
class CoverageResult:
    """The coverage steps after the runs: each --cov-merge, the merge of every build first,
    and under --cov each test's cover-hit file in ``hits_dir``, or why there are none."""

    merges: tuple[MergeResult, ...] = ()
    hits: tuple[HitsResult, ...] = ()
    hits_dir: Path | None = None
    hits_note: str = ""

    @property
    def passed(self) -> bool:
        """Whether every merge and every cover-hit file succeeded."""
        statuses = [merge.status for merge in self.merges] + [hits.status for hits in self.hits]
        return all(status == PASS for status in statuses)


@dataclass(frozen=True)
class ToolJob:
    """A command that runs once the simulations end, a coverage merge or a test's cover-hit
    merge: its scheduler task ID, the command and its log."""

    task_id: str
    cmd: tuple[str, ...]
    log: Path


@dataclass(frozen=True)
class Stage:
    """A finished make step: its exit code (None on a timeout) and its log text."""

    rc: int | None
    log: str


# ---------------------------------------------------------------------------------------
# Inputs


def parse_makefile(text: str) -> BenchMake:
    """The builds, reference build, bring-up-only builds, build parameters and TCK period.

    Only column-0 assignments count, so values inside conditionals and recipes are ignored.
    """
    joined = re.sub(r"\\\n", " ", text)
    assigns: dict[str, str] = {}
    for line in joined.splitlines():
        match = re.match(r"([A-Za-z_]\w*)\s*(?::=|\?=|=)\s*(.*?)\s*$", line)
        if match:
            assigns[match.group(1)] = match.group(2)
    word_re = re.compile(r"(\w+)\s*:=\s*\$\(word\s+(\d+),\s*\$\(build_\$\(BUILD\)\)\)")
    indexed = sorted((int(m.group(2)), m.group(1)) for m in word_re.finditer(joined))
    params = tuple(name for _, name in indexed)
    builds = tuple(assigns.get("BUILDS", "").split())
    reference = assigns.get("CONFIG", "")
    bringup_only = frozenset(assigns.get("BRINGUP_ONLY", "").split())
    if not builds or not params:
        raise ConfigError(f"{MAKEFILE}: no BUILDS or no build parameters")
    if reference not in builds:
        raise ConfigError(f"{MAKEFILE}: default CONFIG {reference!r} is not in BUILDS")
    rows = {build: tuple(assigns.get(f"build_{build}", "").split()) for build in builds}
    for build, row in rows.items():
        if len(row) != len(params):
            raise ConfigError(f"{MAKEFILE}: build_{build} does not list {len(params)} values")
    if not bringup_only <= set(builds):
        raise ConfigError(f"{MAKEFILE}: BRINGUP_ONLY names a build outside BUILDS")
    try:
        tck_period_ps = int(assigns["TCK_PERIOD_PS"])
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"{MAKEFILE}: no numeric TCK_PERIOD_PS default") from exc
    return BenchMake(builds, reference, bringup_only, params, tck_period_ps, rows)


def _pattern(text: object, where: str) -> str:
    """``text`` as a regular expression; a non-string or one that does not compile is an error."""
    if not isinstance(text, str) or not text:
        raise ConfigError(f"{TESTLIST}: {where} needs a non-empty pattern")
    try:
        re.compile(text)
    except re.error as exc:
        raise ConfigError(f"{TESTLIST}: {where}: {exc}") from exc
    return text


def parse_pins(record: Mapping[str, Any], name: str) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """``expect_fail`` and its pins: ``expect_fail_match`` under the empty key and each
    ``expect_fail_match_<simulator>`` under its simulator.

    An expected failure needs ``expect_fail_match``, and a pin needs ``expect_fail``.
    """
    expected_fail = record.get(EXPECTED_FAIL_FIELD, False)
    if not isinstance(expected_fail, bool):
        raise ConfigError(f"{TESTLIST}: {name}: {EXPECTED_FAIL_FIELD} must be true or false")
    pins: list[tuple[str, str]] = []
    for key, value in record.items():
        if key == PIN_FIELD:
            pins.append(("", _pattern(value, f"{name}: {key}")))
        elif key.startswith(f"{PIN_FIELD}_"):
            sim = key.removeprefix(f"{PIN_FIELD}_")
            if sim not in SIMULATORS:
                raise ConfigError(f"{TESTLIST}: {name}: {key} names no simulator {SIMULATORS}")
            pins.append((sim, _pattern(value, f"{name}: {key}")))
    if expected_fail and "" not in dict(pins):
        raise ConfigError(f"{TESTLIST}: {name}: {EXPECTED_FAIL_FIELD} needs {PIN_FIELD}")
    if pins and not expected_fail:
        raise ConfigError(f"{TESTLIST}: {name}: a pin needs {EXPECTED_FAIL_FIELD} = true")
    return expected_fail, tuple(sorted(pins))


def parse_allow(record: Mapping[str, Any], name: str, *, expected_fail: bool) -> tuple[str, ...]:
    """``expect_fail_allow``, one pattern or a list, which needs ``expect_fail``."""
    value = record.get(ALLOW_FIELD, ())
    patterns = (value,) if isinstance(value, str) else value
    if not isinstance(patterns, list | tuple):
        raise ConfigError(f"{TESTLIST}: {name}: {ALLOW_FIELD} must be a pattern or a list")
    if patterns and not expected_fail:
        raise ConfigError(f"{TESTLIST}: {name}: {ALLOW_FIELD} needs {EXPECTED_FAIL_FIELD} = true")
    return tuple(
        _pattern(text, f"{name}: {ALLOW_FIELD}[{index}]")
        for index, text in enumerate(patterns, start=1)
    )


def parse_build_cases(record: Mapping[str, Any], name: str) -> tuple[BuildCase, ...]:
    """The ``build_cases`` of a build-check test: each a ``target``, an optional ``param``
    with its ``values``, and either a ``message`` pattern or ``legal = true``.

    ``message_<simulator>`` needs ``message`` beside it and names a known simulator.
    """
    cases: list[BuildCase] = []
    for index, case in enumerate(record.get(BUILD_CASES_FIELD, ()), start=1):
        where = f"{name}: {BUILD_CASES_FIELD}[{index}]"
        target, param = case.get("target"), case.get("param")
        values = tuple(str(value) for value in case.get("values", ()))
        legal = case.get("legal", False)
        if not isinstance(target, str) or legal not in (True, False):
            raise ConfigError(f"{TESTLIST}: {where} needs a target and a boolean legal")
        if (param is None) != (not values) or not (param is None or isinstance(param, str)):
            raise ConfigError(f"{TESTLIST}: {where}: param and values go together")
        if legal == (CASE_MESSAGE_FIELD in case):
            raise ConfigError(f"{TESTLIST}: {where} needs exactly one of message and legal")
        message = None if legal else _pattern(case.get(CASE_MESSAGE_FIELD), f"{where}: message")
        cases.append(BuildCase(target, param, values, message, sim_messages(case, where)))
    check = record.get(BUILD_CHECK_FIELD)
    if cases and not isinstance(check, str):
        raise ConfigError(f"{TESTLIST}: {name}: {BUILD_CASES_FIELD} needs {BUILD_CHECK_FIELD}")
    return tuple(cases)


def sim_messages(case: Mapping[str, Any], where: str) -> tuple[tuple[str, str], ...]:
    """A build case's ``message_<simulator>`` patterns, which need ``message`` beside them."""
    messages: list[tuple[str, str]] = []
    for key, value in case.items():
        if not key.startswith(f"{CASE_MESSAGE_FIELD}_"):
            continue
        sim = key.removeprefix(f"{CASE_MESSAGE_FIELD}_")
        if sim not in SIMULATORS:
            raise ConfigError(f"{TESTLIST}: {where}: {key} names no simulator {SIMULATORS}")
        if CASE_MESSAGE_FIELD not in case:
            raise ConfigError(f"{TESTLIST}: {where}: {key} needs {CASE_MESSAGE_FIELD}")
        messages.append((sim, _pattern(value, f"{where}: {key}")))
    return tuple(sorted(messages))


def _names(record: Mapping[str, Any], field: str, name: str) -> tuple[str, ...]:
    """A field holding one name or a list of names, as a tuple."""
    value = record.get(field, ())
    names = (value,) if isinstance(value, str) else value
    if not isinstance(names, list | tuple) or not all(isinstance(n, str) for n in names):
        raise ConfigError(f"{TESTLIST}: {name}: {field} must be a name or a list of names")
    return tuple(names)


def parse_testlist(data: Mapping[str, Any]) -> Testlist:
    """The [[tests]] and [[groups]] records; every group member must be a listed test."""
    tests: list[ListedTest] = []
    for record in data.get("tests", []):
        name = record.get("name")
        reseed = record.get("reseed", 1)
        if not isinstance(name, str) or not isinstance(reseed, int) or reseed < 1:
            raise ConfigError(f"{TESTLIST}: test record {record!r} needs a name and reseed >= 1")
        targets = _names(record, "target", name)
        ratios = _names(record, RATIO_FIELD, name)
        bringup = _names(record, BRINGUP_TARGETS_FIELD, name)
        expected_fail, pins = parse_pins(record, name)
        allow = parse_allow(record, name, expected_fail=expected_fail)
        cases = parse_build_cases(record, name)
        check = record.get(BUILD_CHECK_FIELD) if cases else None
        tests.append(
            ListedTest(
                name, reseed, targets, expected_fail, pins, check, cases, ratios, bringup, allow
            )
        )
    names = [test.name for test in tests]
    if len(set(names)) != len(names):
        raise ConfigError(f"{TESTLIST}: a test name repeats")
    groups: list[Group] = []
    for record in data.get("groups", []):
        members = tuple(record.get("tests", ()))
        unknown = sorted(set(members) - set(names))
        if unknown:
            raise ConfigError(f"{TESTLIST}: group {record.get('name')!r} names {unknown}")
        groups.append(Group(str(record.get("name")), members))
    if not tests:
        raise ConfigError(f"{TESTLIST}: no tests")
    return Testlist(tuple(tests), tuple(groups))


def ratio_period_ps(name: str, tck_period_ps: int) -> int:
    """The i_clk period of ratio ``a:b`` (i_clk:TCK in frequency): tck_period_ps * b / a.

    The period must be an even number of picoseconds, since each clock half is a whole
    simulation step at the bench's 1 ps precision.
    """
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", name)
    if not match or int(match.group(1)) == 0:
        raise ConfigError(f"clock ratio {name!r} is not a:b")
    clk_share, tck_share = int(match.group(1)), int(match.group(2))
    period, remainder = divmod(tck_period_ps * tck_share, clk_share)
    if remainder or period % 2:
        raise ConfigError(f"clock ratio {name} gives an i_clk period that is not an even ps")
    return period


def parse_ratios(data: Mapping[str, Any], tck_period_ps: int) -> tuple[tuple[Ratio, ...], Ratio]:
    """The CFG-CLK-RATIO values of the configuration model with their periods, and its default."""
    for dimension in data.get("dimension", []):
        if dimension.get("id") != RATIO_DIMENSION:
            continue
        ratios = tuple(
            Ratio(str(value), ratio_period_ps(str(value), tck_period_ps), tck_period_ps)
            for value in dimension.get("values", [])
            if str(value) != RANDOM_RATIO
        )
        default = next((r for r in ratios if r.name == dimension.get("default")), None)
        if not ratios or default is None:
            raise ConfigError(f"{CONFIGS}: {RATIO_DIMENSION} needs values and a default among them")
        return ratios, default
    raise ConfigError(f"{CONFIGS}: no {RATIO_DIMENSION} dimension")


def load_bench() -> Bench:
    """The Makefile, testlist and clock ratios read from the bench."""
    try:
        make = parse_makefile(MAKEFILE.read_text())
        testlist = parse_testlist(tomllib.loads(TESTLIST.read_text()))
        ratios, default = parse_ratios(tomllib.loads(CONFIGS.read_text()), make.tck_period_ps)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(str(exc)) from exc
    problems = testlist_problems(testlist, make)
    if problems:
        raise ConfigError(f"{TESTLIST}: {'; '.join(problems)}")
    return Bench(make, testlist, ratios, default)


# ---------------------------------------------------------------------------------------
# Selection and planning


def select_tests(
    testlist: Testlist, *, names: Sequence[str], groups: Sequence[str]
) -> tuple[ListedTest, ...]:
    """The tests named directly or through a group, in testlist order; all when none is named.

    The signoff group is every test when the testlist does not define it.
    """
    by_group = {group.name: group.tests for group in testlist.groups}
    by_group.setdefault(SIGNOFF_GROUP, tuple(test.name for test in testlist.tests))
    known = {test.name for test in testlist.tests}
    unknown_groups = [g for g in groups if g not in by_group]
    if unknown_groups:
        raise ConfigError(f"unknown group {unknown_groups}; groups: {sorted(by_group)}")
    unknown_tests = [n for n in names if n not in known]
    if unknown_tests:
        raise ConfigError(f"unknown test {unknown_tests}; --list shows the tests")
    if not names and not groups:
        return testlist.tests
    wanted = set(names).union(*(by_group[g] for g in groups))
    return tuple(test for test in testlist.tests if test.name in wanted)


def select_builds(make: BenchMake, requested: Sequence[str]) -> tuple[str, ...]:
    """The builds asked for, ``all`` expanded to the Makefile's BUILDS; the reference by default."""
    builds: list[str] = []
    for build in requested or (make.reference,):
        expanded = make.builds if build == "all" else (build,)
        for name in expanded:
            if name not in make.builds:
                raise ConfigError(f"unknown build {name!r}; builds: {' '.join(make.builds)}")
            if name not in builds:
                builds.append(name)
    return tuple(builds)


def select_ratios(bench: Bench, requested: Sequence[str]) -> tuple[Ratio, ...]:
    """The ratios asked for, ``all`` expanded to the named ratios and random; empty for none."""
    by_name = {ratio.name: ratio for ratio in bench.slots}
    chosen: list[Ratio] = []
    for name in requested:
        expanded = bench.slots if name == "all" else (by_name.get(name),)
        for ratio in expanded:
            if ratio is None:
                raise ConfigError(f"unknown ratio {name!r}; ratios: {' '.join(by_name)}")
            if ratio not in chosen:
                chosen.append(ratio)
    return tuple(chosen)


def parse_expect_fail(text: str, make: BenchMake) -> ExpectFail:
    """``BUILD[:PARAM=VALUE,...]`` as an ExpectFail; PARAM is one of the Makefile's parameters."""
    build, _, rest = text.partition(":")
    if build not in make.builds:
        raise ConfigError(f"unknown build {build!r} in --expect-build-fail {text}")
    overrides: list[tuple[str, str]] = []
    for item in filter(None, rest.split(",")):
        param, _, value = item.partition("=")
        if param not in make.params or not re.fullmatch(r"[\w']+", value):
            raise ConfigError(f"bad override {item!r}; parameters: {' '.join(make.params)}")
        overrides.append((param, value))
    return ExpectFail(build, tuple(overrides))


def runs_on(test: ListedTest, build: str, make: BenchMake, *, named: bool = False) -> bool:
    """Whether a test runs on a build: its targets, if any, unless the command line
    ``named`` the build, and the bring-up-only rule, which the bring-up test and a build the
    test's targets or bringup_targets name pass."""
    if test.targets and build not in test.targets and not named:
        return False
    if build not in make.bringup_only:
        return True
    return test.name == BRINGUP_TEST or build in test.targets or build in test.bringup_targets


def testlist_problems(testlist: Testlist, make: BenchMake) -> list[str]:
    """Testlist entries whose target names no Makefile build, or whose bringup_targets
    name a build that is not bring-up-only."""
    unknown = [
        f"{test.name}: target names {build}, which the Makefile does not build"
        for test in testlist.tests
        for build in test.targets
        if build not in make.builds
    ]
    return unknown + [
        f"{test.name}: {BRINGUP_TARGETS_FIELD} names {build}, not a BRINGUP_ONLY build"
        for test in testlist.tests
        for build in test.bringup_targets
        if build not in make.bringup_only
    ]


def draw_seeds(rng: random.Random, count: int) -> list[int]:
    """``count`` distinct seeds in 1 .. 2**31 - 2."""
    seeds: list[int] = []
    while len(seeds) < count:
        seed = rng.randrange(1, SEED_LIMIT)
        if seed not in seeds:
            seeds.append(seed)
    return seeds


def test_builds(test: ListedTest, selection: Selection) -> tuple[str, ...]:
    """The builds a test runs on: its own targets when the command names none, else the
    selected builds, which ``runs_on`` filters by the test's targets."""
    if selection.default_builds and test.targets:
        return test.targets
    return selection.builds


def planned_builds(selection: Selection) -> tuple[str, ...]:
    """Every build some selected test runs on, the selected builds first."""
    builds = dict.fromkeys(selection.builds if not selection.default_builds else ())
    for test in selection.tests:
        builds.update(dict.fromkeys(test_builds(test, selection)))
    return tuple(builds) or selection.builds


def run_ratios(test: ListedTest, offset: int, *, selection: Selection, bench: Bench) -> list[Ratio]:
    """The ratio of each run of one test on one build, one entry per run.

    Ratios from the command line, else the test's own, cross each with the seed count.
    --reseed without ratios runs the reseed count or one seed per slot, whichever is more,
    seed i at slot (offset + i) modulo the slot count. Otherwise every seed runs at the
    default.
    """
    count = test.reseed if selection.reseed else selection.seeds
    ratios = selection.ratios or select_ratios(bench, test.ratios)
    if ratios:
        return [ratio for ratio in ratios for _ in range(count)]
    if selection.reseed:
        slots = bench.slots
        return [slots[(offset + i) % len(slots)] for i in range(max(count, len(slots)))]
    return [bench.default_ratio] * count


def ratio_tag(ratio: Ratio) -> str:
    """A directory-safe form of a ratio name: ``1:20`` is ``r1-20``, random is ``random``."""
    return "r" + ratio.name.replace(":", "-") if ":" in ratio.name else ratio.name


def plan_runs(
    bench: Bench, selection: Selection, *, stamp: str, rng: random.Random
) -> list[RunSpec]:
    """Every run the selection asks for, with its run and build directories.

    Run directories are <test>.<build>.<seed>.<stamp>; runs that share all four also carry
    their ratio tag. Seeds are distinct within one test on one build unless --seed fixes it.
    """
    runs: list[RunSpec] = []
    suffix = ("_waves" if selection.wave else "") + ("_cov" if selection.cov else "")
    for build in planned_builds(selection):
        build_dir = BUILD_ROOT / f"{selection.sim}_{build}{suffix}"
        params = bench.make.param_values(build)
        for offset, test in enumerate(selection.tests):
            if test.build_cases or build not in test_builds(test, selection):
                continue
            if not runs_on(test, build, bench.make, named=selection.named_builds):
                continue
            ratios = run_ratios(test, offset, selection=selection, bench=bench)
            seeds = (
                [selection.seed] * len(ratios)
                if selection.seed is not None
                else draw_seeds(rng, len(ratios))
            )
            for seed, ratio in zip(seeds, ratios, strict=True):
                run_dir = SIM_DIR / f"{test.name}.{build}.{seed}.{stamp}"
                runs.append(
                    RunSpec(
                        test.name,
                        build,
                        seed,
                        ratio,
                        run_dir,
                        build_dir,
                        params=params,
                        expected_fail=test.pin(selection.sim),
                        expected_fail_allow=test.allow,
                    )
                )
    shared = Counter(run.run_dir for run in runs)
    return [
        replace(run, run_dir=run.run_dir.with_name(f"{run.run_dir.name}.{ratio_tag(run.ratio)}"))
        if shared[run.run_dir] > 1
        else run
        for run in runs
    ]


def plan_expect_fail(
    check: ExpectFail, *, bench: Bench, message: str, stamp: str, index: int = 1
) -> RunSpec:
    """The RunSpec of the index-th expect-build-fail check, building in its own run directory."""
    label = check.build + "".join(f"-{param}-{value}" for param, value in check.overrides)
    run_dir = SIM_DIR / f"expect_build_fail.{label}.{index}.{stamp}"
    return RunSpec(
        test=BRINGUP_TEST,
        build=check.build,
        seed=1,
        ratio=bench.default_ratio,
        run_dir=run_dir,
        build_dir=run_dir.relative_to(BENCH_DIR) / "build",
        overrides=check.overrides,
        expect_message=message,
        params=bench.make.param_values(check.build, check.overrides),
    )


def plan_build_cases(
    bench: Bench, selection: Selection, *, stamp: str, rng: random.Random
) -> list[RunSpec]:
    """The build checks of every selected build-check test: each case once per seed, on its
    own target, with its parameter value drawn from the seed. Builds named by --target keep
    only the cases on those builds; every case draws its value whether it runs or not, so a
    narrowed run repeats the values of the full run of the same seed.

    Run directories are <test>.c<case>.<target>[-<param>-<value>].<seed>.<stamp>.
    """
    specs: list[RunSpec] = []
    make = bench.make
    for test in selection.tests:
        if not test.build_cases:
            continue
        count = test.reseed if selection.reseed else selection.seeds
        seeds = [selection.seed] * count if selection.seed is not None else draw_seeds(rng, count)
        for seed in dict.fromkeys(seeds):
            draw = random.Random(f"{seed}:{test.name}")
            for index, case in enumerate(test.build_cases, start=1):
                if case.target not in make.builds or (case.param and case.param not in make.params):
                    raise ConfigError(f"{test.name}: case {index} names an unknown build or param")
                overrides = ((case.param, draw.choice(case.values)),) if case.param else ()
                if not selection.default_builds and case.target not in selection.builds:
                    continue
                label = case.target + "".join(f"-{param}-{value}" for param, value in overrides)
                run_dir = SIM_DIR / f"{test.name}.c{index}.{label}.{seed}.{stamp}"
                specs.append(
                    RunSpec(
                        test=BRINGUP_TEST,
                        build=case.target,
                        seed=seed,
                        ratio=bench.default_ratio,
                        run_dir=run_dir,
                        build_dir=run_dir.relative_to(BENCH_DIR) / "build",
                        overrides=overrides,
                        expect_message=case.message_on(selection.sim),
                        params=make.param_values(case.target, overrides),
                        legal=case.message is None,
                        owner=test.name,
                        check_id=test.build_check,
                    )
                )
    return specs


def skipped_tests(bench: Bench, selection: Selection) -> dict[str, list[str]]:
    """Per build, the selected simulation tests it does not run; builds that run them all
    are absent."""
    skipped = {
        build: [
            test.name
            for test in selection.tests
            if not test.build_cases
            and build in test_builds(test, selection)
            and not runs_on(test, build, bench.make, named=selection.named_builds)
        ]
        for build in planned_builds(selection)
    }
    return {build: names for build, names in skipped.items() if names}


# ---------------------------------------------------------------------------------------
# Make commands and verdicts


def make_command(
    variables: Iterable[str], target: str, *, flags: Sequence[str] = (), env: Sequence[str] = ()
) -> list[str]:
    """``[env <env>] make -C <bench> <flags> <variables> <target>``."""
    prefix = ["env", *env] if env else []
    return [*prefix, "make", "-C", str(BENCH_DIR), *flags, *variables, target]


# Coverage options reach make through the environment: the Makefile's += appends to an
# environment value, where a command-line value would replace the Makefile's own options.
def build_environment(job: BuildJob, sim: str) -> list[str]:
    """The environment of a build: for a build check the simulator's strict options (on
    Verilator those that make an elaboration-time $error or $fatal stop the build, which
    the Makefile's -Wno-fatal would leave a warning; on VCS a higher error limit), the
    coverage compile options under --cov, and the site's options."""
    options: list[str] = []
    if job.strict:
        options.append(STRICT_OPTIONS[sim])
    if job.cov:
        options.append("--coverage" if sim == "verilator" else f"-cm {VCS_COVERAGE}")
    options += job.extra
    return [f"COMPILE_ARGS={' '.join(options)}"] if options else []


def coverage_path(run_dir: Path, sim: str, *, cov: bool) -> Path | None:
    """Where a run writes its coverage data: always on Verilator, under --cov on VCS."""
    if sim == "verilator":
        return run_dir / "coverage.dat"
    return run_dir / "coverage.vdb" if cov else None


def run_environment(spec: RunSpec, sim: str, *, cov: bool, extra: Sequence[str] = ()) -> list[str]:
    """The environment of a run: VCS's coverage options with its database in the run
    directory, then ``extra``, the site's run options and the wave dump's."""
    options = (
        ["-cm", VCS_COVERAGE, "-cm_dir", str(spec.run_dir / "coverage.vdb")]
        if sim == "vcs" and cov
        else []
    )
    options += extra
    return [f"SIM_ARGS={' '.join(options)}"] if options else []


def compile_extra(ctx: Context, *, wave: bool) -> tuple[str, ...]:
    """The compile options the site adds to a build: its compile_args, and on a VCS wave
    build its wave_compile_args and wave_sources."""
    if not wave or ctx.sim.name != "vcs":
        return ctx.site.compile_args
    sources = tuple(str(source) for source in ctx.site.wave_sources)
    return (*ctx.site.compile_args, *ctx.site.wave_compile_args, *sources)


def wave_arguments(spec: RunSpec, ctx: Context) -> list[str]:
    """The run options that dump a VCS run's waves into its run directory: the site's
    wave_sim_args, else the bench's VCD plusarg; none on Verilator or without --wave."""
    if not ctx.wave or ctx.sim.name != "vcs":
        return []
    wave_file = spec.run_dir / (ctx.site.wave_file or VCS_WAVE_FILE)
    if ctx.site.wave_sim_args:
        values = {"wave_file": str(wave_file), "run_dir": str(spec.run_dir)}
        return render_argv(ctx.site.wave_sim_args, values)
    return [f"+{VCD_PLUSARG}={wave_file}"]


def run_extra(spec: RunSpec, ctx: Context) -> list[str]:
    """The VCS run options beyond coverage: the site's sim_args and the wave dump's."""
    if ctx.sim.name != "vcs":
        return []
    return [*ctx.site.sim_args, *wave_arguments(spec, ctx)]


def bench_dumps_vcd() -> bool:
    """Whether the bench top names the VCD plusarg, behind which it dumps its waves."""
    return VCD_PLUSARG in read_log(BENCH_DIR / TB_TOP_FILE)


def wave_problem(sim: str, site: site_layer.SimSite) -> str | None:
    """Why --wave cannot dump waves on ``sim``; None when it can."""
    if sim != "vcs" or site.wave_sim_args or bench_dumps_vcd():
        return None
    return (
        f"--wave on VCS needs {TB_TOP_FILE} to dump VCD behind +{VCD_PLUSARG}=<file>, or"
        f" wave_sim_args under [simulators.vcs] in the site file ({site_layer.SITE_ENV})"
    )


def merge_problem(sim: str, site: site_layer.SimSite, *, cov: bool) -> str | None:
    """Why --cov-merge cannot merge on ``sim``; None when it can."""
    if sim == "vcs" and not cov:
        return "--cov-merge on VCS needs --cov"
    if not site.cov_merge_argv and sim not in MERGE_ARGV:
        return (
            f"--cov-merge on {sim} needs cov_merge_argv under [simulators.{sim}] in the site"
            f" file ({site_layer.SITE_ENV})"
        )
    return None


def build_variables(job: BuildJob, sim: str) -> list[str]:
    """The make variables that select a build, its build directory and its overrides."""
    variables = [f"SIM={sim}", f"CONFIG={job.build}", f"SIM_BUILD={job.build_dir}"]
    if job.wave:
        variables.append("WAVES=1")
    return variables + [f"{param}={value}" for param, value in job.overrides]


def run_variables(spec: RunSpec, sim: SimTool, *, wave: bool) -> list[str]:
    """The make variables of one run: build, test, seed, clocks, results, coverage, waves."""
    job = BuildJob(spec.build, spec.build_dir, spec.overrides, wave)
    if sim.cocotb_major >= COCOTB_RENAMED_KNOBS:
        test_filter = f"COCOTB_TEST_FILTER=^{re.escape(spec.test)}$"
        seed_var = "COCOTB_RANDOM_SEED"
    else:
        test_filter = f"TESTCASE={spec.test}"
        seed_var = "RANDOM_SEED"
    clk_period_ps = spec.ratio.clk_period_ps
    variables = [
        *build_variables(job, sim.name),
        test_filter,
        f"{seed_var}={spec.seed}",
        f"COCOTB_RESULTS_FILE={spec.run_dir / 'results.xml'}",
        f"CLK_PERIOD_PS={RANDOM_RATIO if clk_period_ps is None else clk_period_ps}",
        f"TCK_PERIOD_PS={spec.ratio.tck_period_ps}",
    ]
    if sim.name == "verilator":
        variables.append(f"VERILATOR_COVERAGE_FILE={spec.run_dir / 'coverage.dat'}")
        if wave:
            variables.append(f"SIM_ARGS=--trace --trace-file {spec.run_dir / 'waves.fst'}")
    return variables


def build_key(job: BuildJob, sim: str) -> str:
    """The identity of a build's model: its make variables, environment and parameter values.

    The Makefile remakes a model only when a source changes, so a model whose recorded key
    differs from this one is remade from scratch.
    """
    identity = {
        "variables": build_variables(job, sim),
        "environment": build_environment(job, sim),
        "params": dict(job.params),
    }
    return json.dumps(identity, sort_keys=True) + "\n"


def model_is_stale(build_dir: Path) -> bool:
    """Whether a source Verilator read for this build is newer than the model, or is gone."""
    ver_files = build_dir / "Vtop__verFiles.dat"
    model = build_dir / "Vtop"
    if not ver_files.is_file() or not model.is_file():
        return False
    built = model.stat().st_mtime
    for line in ver_files.read_text(errors="replace").splitlines():
        match = re.match(r'S\s.*"([^"]+)"\s*$', line)
        if match:
            source = Path(match.group(1))
            if not source.exists() or source.stat().st_mtime > built:
                return True
    return False


def junit_status(results: Path, test: str) -> tuple[str, str]:
    """(status, reason) of a test in a cocotb JUnit file.

    Pass needs the test's testcase with no failure, error or skipped child.
    """
    if not results.is_file():
        return ERROR, "no results file"
    try:
        root = ET.parse(results).getroot()
    except ET.ParseError as exc:
        return ERROR, f"unreadable results file: {exc}"
    cases = [case for case in root.iter("testcase") if case.get("name") == test]
    if not cases:
        return ERROR, f"{test} is not in the results file"
    for case in cases:
        for tag, status in (("failure", FAIL), ("error", FAIL), ("skipped", ERROR)):
            child = case.find(tag)
            if child is not None:
                return status, f"{tag}: {child.get('message', '')}".strip()
    return PASS, ""


def parse_clocks(log: str) -> list[Clocks]:
    """Every TDR2AXI_CLOCKS line of a run log, in log order."""
    return [Clocks(*map(int, match.groups())) for match in CLOCKS_LINE.finditer(log)]


def random_period_span(tck_period_ps: int) -> tuple[int, int]:
    """The shortest and longest i_clk period the bench draws for the random ratio."""
    fast, slow = (ratio_period_ps(name, tck_period_ps) for name in RANDOM_RATIO_SPAN)
    return fast, slow


def clocks_problem(clocks: Sequence[Clocks], ratio: Ratio) -> str | None:
    """Why the clocks a log reports are not the ratio's; None when every line fits.

    A named ratio's periods must match; the random ratio's drawn i_clk period must lie in
    the random span.
    """
    if not clocks:
        return "no TDR2AXI_CLOCKS line in the log"
    if ratio.clk_period_ps is None:
        low, high = random_period_span(ratio.tck_period_ps)
        outside = next((ran for ran in clocks if not low <= ran.clk_ps <= high), None)
        if outside is None:
            return None
        return f"drew clk_ps={outside.clk_ps}, outside {low}..{high} for {ratio.name}"
    asked = (ratio.clk_period_ps, ratio.tck_period_ps)
    for ran in clocks:
        if (ran.clk_ps, ran.tck_ps) != asked:
            return (
                f"ran clk_ps={ran.clk_ps} tck_ps={ran.tck_ps}, asked clk_ps={asked[0]}"
                f" tck_ps={asked[1]} for {ratio.name}"
            )
    return None


def classify_run(
    rc: int | None, results: Path, *, test: str, timeout_s: float, clocks: str | None = None
) -> tuple[str, str]:
    """(status, reason) of a run from make's exit code, the JUnit file and the clock check.

    ``clocks`` is the clock check's problem, None when the log shows the asked periods; a
    pass or fail with a clock problem is an error.
    """
    if rc is None:
        return ERROR, f"timed out after {timeout_s:g} s"
    status, reason = junit_status(results, test)
    if status == PASS and rc != 0:
        return ERROR, f"make exited {rc} after a passing results file"
    if status == ERROR and rc != 0:
        return ERROR, f"make exited {rc}: {reason}"
    if clocks is not None and status != ERROR:
        return ERROR, clocks + (f"; test {status}: {reason}" if reason else "")
    return status, reason


def tool_output(log: str) -> str:
    """A log without make's own messages; under quiet make the rest is the tools' output."""
    return "\n".join(line for line in log.splitlines() if not MAKE_MESSAGE.match(line))


def first_match(pattern: str, text: str) -> str | None:
    """The first line of ``text`` that ``pattern`` matches, stripped."""
    regex = re.compile(pattern)
    return next((line.strip() for line in text.splitlines() if regex.search(line)), None)


def expect_fail_verdict(
    build: Stage, probe: Stage | None, *, probe_passed: bool, pattern: str
) -> tuple[str, str]:
    """(status, reason) of an expect-build-fail check.

    Pass when the build fails with a line matching ``pattern``, or builds and its model
    stops with such a line; fail when the model passes bring-up or stops for another reason.
    ``probe`` is the model's run, None when the build did not succeed. Lines that are make's
    own messages never match.
    """
    if build.rc is None or (probe is not None and probe.rc is None):
        return ERROR, "timed out"
    if build.rc != 0 or probe is None:
        hit = first_match(pattern, tool_output(build.log))
        if hit is None:
            return FAIL, "build failed without the expected message"
        return PASS, f"build rejected: {hit}"
    if probe_passed:
        return FAIL, "the illegal configuration built and passed bring-up"
    hit = first_match(pattern, tool_output(probe.log))
    if hit is None:
        return FAIL, "the model stopped without the expected message"
    return PASS, f"rejected at elaboration: {hit}"


def expect_pass_verdict(
    build: Stage, probe: Stage | None, *, probe_passed: bool
) -> tuple[str, str]:
    """(status, reason) of an expect-build-pass check: pass when the build elaborates and
    its model passes bring-up, fail when either stops."""
    if build.rc is None or (probe is not None and probe.rc is None):
        return ERROR, "timed out"
    if build.rc != 0 or probe is None:
        return FAIL, f"the legal configuration did not build (make exited {build.rc})"
    if not probe_passed:
        return FAIL, "the legal configuration built but did not pass bring-up"
    return PASS, "built and passed bring-up"


def report_lines(text: str, sim: str) -> list[str]:
    """The lines of ``text``; on VCS each assertion header without a message is joined to
    the message line after it, so that one report is one line."""
    lines = text.splitlines()
    if sim != "vcs":
        return lines
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        report = VCS_REPORT.match(line)
        if report and report.group("rest") is None and index + 1 < len(lines):
            joined.append(f"{line.rstrip()}: {lines[index + 1].strip()}")
            index += 2
            continue
        joined.append(line)
        index += 1
    return joined


def ending_exceptions(lines: Sequence[str]) -> list[str]:
    """The last line of each traceback that continues cocotb's record of a failed test:
    the exception that ended the test, whatever its class is named."""
    found: list[str] = []
    block: list[str] | None = None
    for line in [*lines, ""]:
        if TEST_FAILED.match(line):
            block = []
        elif block is not None and not line.strip():
            continue
        elif block is not None and line[:1].isspace() and not COCOTB_RECORD.match(line):
            block.append(line.strip())
        else:
            found += block[-1:] if block else []
            block = None
    found += block[-1:] if block else []
    return found


def error_lines(lines: Sequence[str]) -> list[str]:
    """The lines that report an error, stripped: those the error patterns match and the
    exception that ended each failed test."""
    patterns = (CHECK_FAIL, COCOTB_ERROR, SIM_ERROR, EXCEPTION)
    matched = [line.strip() for line in lines if any(p.search(line) for p in patterns)]
    return matched + [line for line in ending_exceptions(lines) if line not in matched]


def stopped_on(lines: Sequence[str], sim: str, expected: re.Pattern[str]) -> bool:
    """Whether the last VCS assertion report is a $fatal that ``expected`` matches, the
    report that stopped the simulator; always False on other simulators."""
    reports = [VCS_REPORT.match(line) for line in lines] if sim == "vcs" else []
    last = next((report for report in reversed(reports) if report), None)
    return last is not None and last.group("kind") == "Fatal" and bool(expected.search(last.string))


def expected_fail_verdict(
    status: str,
    reason: str,
    log: str,
    pin: str,
    allow: Sequence[str] = (),
    *,
    sim: str = SIMULATORS[0],
    rc: int | None = 0,
) -> tuple[str, str]:
    """(status, reason) of a run of a test the testlist expects to fail on ``pin``.

    PASS, an expected failure, when the run failed, a line of its tool output on ``sim``
    matches the pin, and every error line matches the pin or a pattern of ``allow``, or is
    a shutdown record of the pinned $fatal that stopped the simulator. FAIL when it passed,
    failed with no such line, or logged another error. A failing run whose make exited
    ``rc`` nonzero is an error unless such a $fatal stopped it, and an error stays an error.
    """
    if status == ERROR:
        return status, reason
    if status == PASS:
        return FAIL, f"passed, but the testlist expects a failure matching {pin!r}"
    lines = report_lines(tool_output(log), sim)
    signature = re.compile(pin)
    hit = next((line.strip() for line in lines if signature.search(line)), None)
    if hit is None:
        return FAIL, f"failed with no line matching its pin {pin!r}: {reason}"
    expected = re.compile("|".join(f"(?:{pattern})" for pattern in (pin, *allow)))
    stopped = stopped_on(lines, sim, expected)
    if rc not in (0, None) and not stopped:
        return ERROR, f"make exited {rc} after a failing results file: {reason}"
    shutdown = SHUTDOWN_RECORDS.get(sim) if stopped else None
    for line in error_lines(lines):
        if not expected.search(line) and not (shutdown and shutdown.search(line)):
            return FAIL, f"failed on its pin {pin!r} and on another error: {line}"
    return PASS, f"expected failure: {hit}"


# ---------------------------------------------------------------------------------------
# Execution


class Launcher:
    """Runs each command in its own process group with its output in a log file."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self._live: set[subprocess.Popen[bytes]] = set()
        self._lock = threading.Lock()
        self._stopped = False

    def __call__(self, cmd: Sequence[str], log: Path) -> int | None:
        """make's exit code, or None when the command times out or the launcher is stopped."""
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as out, self._lock:
            if self._stopped:
                return None
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
            )
            self._live.add(proc)
        try:
            return proc.wait(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
            return None
        finally:
            with self._lock:
                self._live.discard(proc)

    def stop(self) -> None:
        """Terminate every live command and refuse new ones."""
        with self._lock:
            self._stopped = True
            live = list(self._live)
        for proc in live:
            self._terminate(proc)

    @staticmethod
    def _terminate(proc: subprocess.Popen[bytes]) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
                proc.wait(timeout=TERM_GRACE_S)
                return
            except ProcessLookupError:
                return
            except subprocess.TimeoutExpired:
                continue


def utc_now() -> str:
    """The current time in UTC, ISO 8601 to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_log(path: Path) -> str:
    """A file's text, empty when the file is missing."""
    return path.read_text(errors="replace") if path.is_file() else ""


def prepare_build(job: BuildJob, ctx: Context) -> PreparedBuild:
    """The make command of one build's model, from scratch when its key changed, and its
    log; removes the key file, which only a model that finished building with it carries."""
    build_dir = BENCH_DIR / job.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    sim = ctx.sim.name
    key, key_file = build_key(job, sim), build_dir / BUILD_KEY_FILE
    force = (
        ctx.rebuild
        or read_log(key_file) != key
        or (sim == "verilator" and model_is_stale(build_dir))
    )
    flags = [f"-j{ctx.make_jobs}", *(["-B"] if force else [])]
    flags += QUIET_MAKE_FLAGS if ctx.quiet else ()
    target = str(job.build_dir / MODEL_FILES[sim][-1])
    cmd = make_command(
        build_variables(job, sim), target, flags=flags, env=build_environment(job, sim)
    )
    key_file.unlink(missing_ok=True)
    return PreparedBuild(tuple(cmd), build_dir / "build.log", key_file, key)


def finish_build(build: PreparedBuild, rc: int | None) -> None:
    """Record the build's key beside its model when make built it."""
    if rc == 0:
        build.key_file.write_text(build.key)


def build_model(job: BuildJob, ctx: Context) -> tuple[int | None, str]:
    """Make one build's model in its build directory, from scratch when its key changed.

    Returns make's exit code, None on a timeout, and the command.
    """
    build = prepare_build(job, ctx)
    rc = ctx.launch(list(build.cmd), build.log)
    finish_build(build, rc)
    return rc, shlex.join(build.cmd)


def prepare_run(spec: RunSpec, ctx: Context) -> list[str]:
    """Make the run directory and return the make command that simulates the run on its
    built model."""
    spec.run_dir.mkdir(parents=True, exist_ok=False)
    results = spec.run_dir / "results.xml"
    flags = [
        flag for name in MODEL_FILES[ctx.sim.name] for flag in ("-o", str(spec.build_dir / name))
    ]
    flags += QUIET_MAKE_FLAGS if ctx.quiet else ()
    env = [
        *run_environment(spec, ctx.sim.name, cov=ctx.cov, extra=run_extra(spec, ctx)),
        *ctx.env,
    ]
    return make_command(
        run_variables(spec, ctx.sim, wave=ctx.wave), str(results), flags=flags, env=env
    )


def run_test(spec: RunSpec, ctx: Context) -> tuple[int | None, str]:
    """Simulate one run on its built model; make's exit code and the command."""
    cmd = prepare_run(spec, ctx)
    return ctx.launch(cmd, spec.run_dir / "sim.log"), shlex.join(cmd)


def simulate(spec: RunSpec, ctx: Context) -> RunResult:
    """Run one test on its built model; grade it from the JUnit file and the logged clocks."""
    started = utc_now()
    start = time.monotonic()
    rc, command = run_test(spec, ctx)
    return grade_run(
        spec, ctx, rc=rc, command=command, started=started, duration_s=time.monotonic() - start
    )


def grade_run(
    spec: RunSpec,
    ctx: Context,
    *,
    rc: int | None,
    command: str,
    started: str,
    duration_s: float,
) -> RunResult:
    """Grade a finished run from make's exit code, the JUnit file and the logged clocks."""
    results = spec.run_dir / "results.xml"
    log = read_log(spec.run_dir / "sim.log")
    clocks = parse_clocks(log)
    status, reason = classify_run(
        rc,
        results,
        test=spec.test,
        timeout_s=ctx.launch.timeout_s,
        clocks=clocks_problem(clocks, spec.ratio),
    )
    if spec.expected_fail is not None:
        status, reason = expected_fail_verdict(
            status,
            reason,
            log,
            spec.expected_fail,
            spec.expected_fail_allow,
            sim=ctx.sim.name,
            rc=rc,
        )
    coverage = coverage_path(spec.run_dir, ctx.sim.name, cov=ctx.cov)
    return RunResult(
        spec,
        status,
        reason,
        duration_s,
        command,
        spec.run_dir / "sim.log",
        results,
        BENCH_DIR / spec.build_dir / "build.log",
        coverage if coverage is not None and coverage.exists() else None,
        clocks[0] if clocks else None,
        started,
    )


def check_line(spec: RunSpec, status: str, reason: str) -> str:
    """The evidence line of a build check that grades a plan check: ``<ID> PASS|FAIL: ...``."""
    overrides = "".join(f" {param}={value}" for param, value in spec.overrides)
    return f"{spec.check_id} {status.upper()}: {spec.run_kind} {spec.build}{overrides}: {reason}\n"


def check_expect_fail(spec: RunSpec, ctx: Context) -> RunResult:
    """Build a parameter set and, when it builds, start its model once with bring-up.

    An illegal set must stop with its message; a legal one must pass bring-up. A check of
    a testlist entry writes its pass or fail line to its run directory's check log, which
    becomes the result's log.
    """
    started = utc_now()
    start = time.monotonic()
    spec.run_dir.mkdir(parents=True, exist_ok=False)
    quiet = replace(ctx, quiet=True)
    build_rc, command = build_model(check_build_job(spec, ctx), quiet)
    probe_dir = spec.run_dir / "probe"
    probe: Stage | None = None
    if build_rc == 0:
        probe_spec = replace(spec, run_dir=probe_dir)
        probe_rc, probe_command = run_test(probe_spec, probe_context(quiet, spec))
        command = f"{command}\n{probe_command}"
        probe = Stage(probe_rc, read_log(probe_dir / "sim.log"))
    build = Stage(build_rc, read_log(BENCH_DIR / spec.build_dir / "build.log"))
    return grade_check(
        spec,
        build=build,
        probe=probe,
        command=command,
        started=started,
        duration_s=time.monotonic() - start,
        coverage=check_coverage(spec, ctx),
    )


def check_build_job(spec: RunSpec, ctx: Context) -> BuildJob:
    """The build of a build check: its parameter set, strict on Verilator, without waves,
    with coverage for a legal set under --cov."""
    return BuildJob(
        spec.build,
        spec.build_dir,
        spec.overrides,
        cov=ctx.cov and spec.legal,
        params=spec.params,
        strict=True,
        extra=compile_extra(ctx, wave=False),
    )


def probe_context(ctx: Context, spec: RunSpec) -> Context:
    """The context of build check ``spec``'s bring-up run: no waves, and coverage only for
    a legal set under --cov, whose build compiles it in."""
    return replace(ctx, wave=False, cov=ctx.cov and spec.legal)


def check_coverage(spec: RunSpec, ctx: Context) -> Path | None:
    """Where build check ``spec``'s bring-up run writes coverage, as an ordinary run in its
    context would; None for an illegal set, whose coverage never counts."""
    if not spec.legal:
        return None
    return coverage_path(spec.run_dir / "probe", ctx.sim.name, cov=probe_context(ctx, spec).cov)


def grade_check(
    spec: RunSpec,
    *,
    build: Stage,
    probe: Stage | None,
    command: str,
    started: str,
    duration_s: float,
    coverage: Path | None = None,
) -> RunResult:
    """Grade a build check from its build and, when it built, its bring-up run ``probe``,
    which wrote ``coverage`` when that file exists."""
    build_log = BENCH_DIR / spec.build_dir / "build.log"
    probe_dir = spec.run_dir / "probe"
    results = probe_dir / "results.xml"
    probe_passed = junit_status(results, spec.test)[0] == PASS
    if spec.legal:
        status, reason = expect_pass_verdict(build, probe, probe_passed=probe_passed)
    else:
        status, reason = expect_fail_verdict(
            build,
            probe,
            probe_passed=probe_passed,
            pattern=spec.expect_message or DEFAULT_EXPECT_MESSAGE,
        )
    log = build_log if probe is None else probe_dir / "sim.log"
    if spec.check_id is not None and status != ERROR:
        log = spec.run_dir / CHECK_LOG
        log.write_text(check_line(spec, status, reason))
    return RunResult(
        spec,
        status,
        reason,
        duration_s,
        command,
        log,
        results if results.is_file() else None,
        build_log,
        coverage if probe is not None and coverage is not None and coverage.exists() else None,
        started=started,
    )


def build_failed_result(spec: RunSpec, rc: int | None) -> RunResult:
    """The error result of a run whose build failed or timed out."""
    reason = "build timed out" if rc is None else f"build failed (make exited {rc})"
    build_log = BENCH_DIR / spec.build_dir / "build.log"
    return RunResult(spec, ERROR, reason, 0.0, "", None, None, build_log)


def print_tail(path: Path, lines: int = 20) -> None:
    """The last lines of a log, on stderr."""
    print(f"--- last {lines} lines of {path}", file=sys.stderr)
    print("\n".join(read_log(path).splitlines()[-lines:]), file=sys.stderr)


def status_label(result: RunResult) -> str:
    """The status a person reads: XFAIL for a pass of a run the testlist expects to fail."""
    if result.status == PASS and result.spec.expected_fail is not None:
        return "XFAIL"
    return result.status.upper()


def report(result: RunResult) -> None:
    """One line per finished run on stdout."""
    spec = result.spec
    kind = f"{spec.run_kind} " if spec.is_build_check else ""
    name = spec.owner or spec.test
    overrides = "".join(f" {param}={value}" for param, value in spec.overrides)
    print(
        f"{status_label(result):5} {kind}{name} {spec.build}{overrides} seed={spec.seed}"
        f" ratio={spec.ratio.name} {result.duration_s:.1f}s {spec.run_dir.relative_to(BENCH_DIR)}"
        + (f"  ({result.reason})" if result.reason else ""),
        flush=True,
    )


def model_builds(runs: Sequence[RunSpec], ctx: Context) -> dict[Path, BuildJob]:
    """The model each run needs, by build directory, in the order the runs first name them."""
    extra = compile_extra(ctx, wave=ctx.wave)
    return {
        run.build_dir: BuildJob(
            run.build, run.build_dir, wave=ctx.wave, cov=ctx.cov, params=run.params, extra=extra
        )
        for run in runs
    }


def execute(
    runs: Sequence[RunSpec], checks: Sequence[RunSpec], *, ctx: Context, jobs: int
) -> list[RunResult]:
    """Build each build once, then run its tests, and the expect-build-fail checks, in parallel.

    Results come back in plan order: the runs, then the checks.
    """
    builds = model_builds(runs, ctx)
    ctx = replace(ctx, make_jobs=max(1, jobs // max(1, min(jobs, len(builds) + len(checks)))))
    pool = ThreadPoolExecutor(max_workers=jobs)
    ordered: dict[int, RunResult] = {}
    try:
        futures: dict[Future[RunResult], int] = {
            pool.submit(check_expect_fail, check, ctx): index
            for index, check in enumerate(checks, start=len(runs))
        }
        built = pool.map(lambda job: build_model(job, ctx), builds.values())
        build_rcs = {build_dir: rc for build_dir, (rc, _) in zip(builds, built, strict=True)}
        for build_dir, rc in build_rcs.items():
            if rc != 0:
                print_tail(BENCH_DIR / build_dir / "build.log")
        for index, run in enumerate(runs):
            if build_rcs[run.build_dir] != 0:
                ordered[index] = build_failed_result(run, build_rcs[run.build_dir])
                report(ordered[index])
            else:
                futures[pool.submit(simulate, run, ctx)] = index
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()
            report(ordered[futures[future]])
    except BaseException:
        ctx.launch.stop()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return [ordered[index] for index in sorted(ordered)]


# ---------------------------------------------------------------------------------------
# Scheduler execution


def lsf_tasks(
    runs: Sequence[RunSpec], checks: Sequence[RunSpec], *, ctx: Context, timeout_s: float
) -> tuple[list[BuildJob], list[Task]]:
    """The models and the scheduler jobs of a plan, the jobs without their commands: one
    per model, b<n>; one per run, r<n>, which waits for its model; one per build check,
    c<n>, which builds and starts its own model. Writes nothing."""
    builds = list(model_builds(runs, ctx).values())
    index = {job.build_dir: n for n, job in enumerate(builds)}
    tasks = [Task(f"b{n:02d}", build=True, timeout_s=timeout_s) for n in range(len(builds))]
    tasks += [
        Task(f"r{n:04d}", after=f"b{index[run.build_dir]:02d}", timeout_s=timeout_s)
        for n, run in enumerate(runs)
    ]
    tasks += [Task(f"c{n:03d}", build=True, timeout_s=2 * timeout_s) for n in range(len(checks))]
    return builds, tasks


def epoch_iso(seconds: int | None) -> str:
    """Seconds since the epoch in ISO 8601 UTC to the second; the current time for None."""
    if seconds is None:
        return utc_now()
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def blocked_result(spec: RunSpec, build: Outcome) -> RunResult:
    """The error result of a run whose build job did not succeed, naming the build log."""
    build_log = BENCH_DIR / spec.build_dir / "build.log"
    rc = build.rcs[0] if build.rcs else None
    if rc is None:
        reason = f"build did not finish: {build.reason}"
    else:
        reason = f"build failed (make exited {rc}); build log {build_log}"
    return RunResult(spec, ERROR, reason, 0.0, "", None, None, build_log)


def unfinished_result(spec: RunSpec, outcome: Outcome, command: str, build_log: Path) -> RunResult:
    """The error result of a run or check whose job ended without every exit status."""
    return RunResult(
        spec,
        ERROR,
        outcome.reason,
        outcome.duration_s,
        command,
        outcome.joblog,
        None,
        build_log,
        started=epoch_iso(outcome.started),
    )


class JobGrader:
    """Gives each task its commands and grades its outcome as the executor settles it,
    the way a local run grades the same build, run or check."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.results: dict[int, RunResult] = {}
        self._specs: dict[int, RunSpec] = {}
        self._settle: dict[str, Callable[[Outcome], None]] = {}

    def __call__(self, outcome: Outcome) -> None:
        """Grade one settled task."""
        self._settle[outcome.task.task_id](outcome)

    def ordered(self) -> list[RunResult]:
        """The results in plan order: the runs, then the checks."""
        return [self.results[index] for index in sorted(self.results)]

    def stopped(self, reason: str) -> list[RunResult]:
        """Every result in plan order, a run or check that never settled an error for
        ``reason``."""
        for index, spec in self._specs.items():
            if index not in self.results:
                build_log = BENCH_DIR / spec.build_dir / "build.log"
                self.results[index] = RunResult(spec, ERROR, reason, 0.0, "", None, None, build_log)
        return self.ordered()

    def add_build(self, task: Task, job: BuildJob) -> Task:
        """``task`` with the build's command; once built, the model records its key."""
        build = prepare_build(job, self.ctx)

        def settle(outcome: Outcome) -> None:
            finish_build(build, outcome.rcs[0])
            if outcome.rcs[0] != 0:
                print_tail(build.log)
            if not outcome.complete:
                print(f"run_sim: build {task.task_id}: {outcome.reason}", file=sys.stderr)

        self._settle[task.task_id] = settle
        return replace(task, steps=(Step(build.cmd, build.log),))

    def add_run(self, task: Task, index: int, spec: RunSpec) -> Task:
        """``task`` with the run's command, graded from its run directory."""
        cmd = prepare_run(spec, self.ctx)
        command = shlex.join(cmd)
        build_log = BENCH_DIR / spec.build_dir / "build.log"
        self._specs[index] = spec

        def settle(outcome: Outcome) -> None:
            if outcome.blocked_by is not None:
                result = blocked_result(spec, outcome.blocked_by)
            elif outcome.rcs[0] is None:
                result = unfinished_result(spec, outcome, command, build_log)
            else:
                result = grade_run(
                    spec,
                    self.ctx,
                    rc=outcome.rcs[0],
                    command=command,
                    started=epoch_iso(outcome.started),
                    duration_s=outcome.duration_s,
                )
            self._record(index, result)

        self._settle[task.task_id] = settle
        return replace(task, steps=(Step(tuple(cmd), spec.run_dir / "sim.log"),))

    def add_check(self, task: Task, index: int, spec: RunSpec) -> Task:
        """``task`` with the check's build and bring-up commands; the bring-up runs only
        when the build succeeds."""
        spec.run_dir.mkdir(parents=True, exist_ok=False)
        self._specs[index] = spec
        quiet = replace(self.ctx, quiet=True)
        build = prepare_build(check_build_job(spec, self.ctx), quiet)
        probe = replace(spec, run_dir=spec.run_dir / "probe")
        probe_cmd = prepare_run(probe, probe_context(quiet, spec))
        command = f"{shlex.join(build.cmd)}\n{shlex.join(probe_cmd)}"

        def settle(outcome: Outcome) -> None:
            built, probed = outcome.rcs
            if not outcome.complete:
                result = unfinished_result(spec, outcome, command, build.log)
            else:
                finish_build(build, built)
                ran = Stage(probed, read_log(probe.run_dir / "sim.log")) if built == 0 else None
                result = grade_check(
                    spec,
                    build=Stage(built, read_log(build.log)),
                    probe=ran,
                    command=command,
                    started=epoch_iso(outcome.started),
                    duration_s=outcome.duration_s,
                    coverage=check_coverage(spec, self.ctx),
                )
            self._record(index, result)

        self._settle[task.task_id] = settle
        steps = (Step(build.cmd, build.log), Step(tuple(probe_cmd), probe.run_dir / "sim.log"))
        return replace(task, steps=steps)

    def _record(self, index: int, result: RunResult) -> None:
        self.results[index] = result
        report(result)


class SchedulerStopped(Exception):
    """The scheduler stopped answering; ``results`` holds every run and check, settled or an
    error."""

    def __init__(self, message: str, results: list[RunResult]) -> None:
        super().__init__(message)
        self.results = results


def raise_interrupt(signum: int, frame: FrameType | None) -> None:
    """A signal handler that stops the run as an interrupt does."""
    raise KeyboardInterrupt


@contextmanager
def signals_interrupt() -> Iterator[None]:
    """SIGTERM and SIGHUP raise KeyboardInterrupt inside the block, so that the executor
    kills its jobs; the previous handlers return after it."""
    previous = {sig: signal.signal(sig, raise_interrupt) for sig in INTERRUPT_SIGNALS}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)


def execute_lsf(
    runs: Sequence[RunSpec],
    checks: Sequence[RunSpec],
    *,
    ctx: Context,
    executor: LsfExecutor,
    timeout_s: float,
) -> list[RunResult]:
    """Build each model once as a job, then run its tests as jobs, and the build checks as
    jobs of their own; make builds with the cores a build job asks for.

    Results come back in plan order: the runs, then the checks.
    """
    site = executor.site
    ctx = replace(ctx, make_jobs=site.build_defaults.over(site.defaults).cores or 1)
    builds, skeleton = lsf_tasks(runs, checks, ctx=ctx, timeout_s=timeout_s)
    grader = JobGrader(ctx)
    count = len(builds)
    tasks = [
        grader.add_build(task, job) for task, job in zip(skeleton[:count], builds, strict=True)
    ]
    tasks += [grader.add_run(skeleton[count + n], n, run) for n, run in enumerate(runs)]
    count += len(runs)
    tasks += [
        grader.add_check(skeleton[count + n], len(runs) + n, check)
        for n, check in enumerate(checks)
    ]
    with signals_interrupt():
        try:
            executor.run(tasks, grader)
        except SchedulerError as exc:
            raise SchedulerStopped(str(exc), grader.stopped(f"not settled: {exc}")) from exc
    return grader.ordered()


def submission_summary(tasks: Sequence[Task]) -> str:
    """What one submission holds, for a dry run: its task count by kind and what it waits for."""
    nouns = {"b": "builds", "r": "runs", "c": "build checks"}
    counts = Counter(task.task_id[0] for task in tasks)
    what = ", ".join(f"{count} {nouns.get(kind, 'jobs')}" for kind, count in counts.items())
    waits = sorted({task.after for task in tasks if task.after is not None})
    return what + (f" once {', '.join(waits)} succeeds" if waits else "")


def print_lsf_plan(
    runs: Sequence[RunSpec],
    checks: Sequence[RunSpec],
    *,
    ctx: Context,
    executor: LsfExecutor,
    timeout_s: float,
) -> None:
    """The bsub commands a scheduler run submits while the cluster has room, on stdout."""
    print(f"work directory: {executor.work_dir}")
    if executor.hook is not None:
        print(f"setup hook: {executor.hook}")
    _, tasks = lsf_tasks(runs, checks, ctx=ctx, timeout_s=timeout_s)
    for submission in executor.plan(tasks):
        print(f"{shlex.join(submission.argv)}  # {submission_summary(submission.tasks)}")


# ---------------------------------------------------------------------------------------
# Coverage merge and cover hits


def job_verdict(rc: int | None, reason: str) -> tuple[str, str]:
    """A tool job's status and reason: pass on exit status 0, fail on another, and an error
    with ``reason`` when it did not finish."""
    if rc == 0:
        return PASS, ""
    return (ERROR, reason) if rc is None else (FAIL, f"exited {rc}")


def run_jobs_here(
    jobs: Sequence[ToolJob], *, launch: Launch, parallel: int
) -> dict[str, tuple[int | None, str]]:
    """Run ``jobs`` on this host, ``parallel`` at a time; each one's exit status by task
    ID, None with the reason when it did not finish."""

    def run_one(job: ToolJob) -> tuple[int | None, str]:
        if missing_binaries(job.cmd[:1]):
            return None, f"{job.cmd[0]} not found on PATH"
        rc = launch(list(job.cmd), job.log)
        return rc, ("" if rc is not None else "timed out")

    pool = ThreadPoolExecutor(max_workers=max(1, parallel))
    try:
        ends = list(pool.map(run_one, jobs))
    except BaseException:
        launch.stop()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return {job.task_id: end for job, end in zip(jobs, ends, strict=True)}


def run_jobs_lsf(
    jobs: Sequence[ToolJob], *, executor: LsfExecutor, timeout_s: float
) -> dict[str, tuple[int | None, str]]:
    """Run each of ``jobs`` as a scheduler job with the build resources; each one's exit
    status by task ID, None with the reason when it did not finish."""
    tasks = [
        Task(job.task_id, (Step(job.cmd, job.log),), build=True, timeout_s=timeout_s)
        for job in jobs
    ]
    outcomes: dict[str, Outcome] = {}

    def settle(outcome: Outcome) -> None:
        outcomes[outcome.task.task_id] = outcome

    stopped = ""
    try:
        with signals_interrupt():
            executor.run(tasks, settle)
    except SchedulerError as exc:
        stopped = str(exc)
    ends = {task_id: (outcome.rcs[0], outcome.reason) for task_id, outcome in outcomes.items()}
    return {job.task_id: ends.get(job.task_id, (None, stopped)) for job in jobs}


def run_tool_jobs(
    jobs: Sequence[ToolJob], *, ctx: Context, executor: LsfExecutor | None, parallel: int
) -> dict[str, tuple[int | None, str]]:
    """Each job's exit status by task ID, None with the reason when it did not finish: on
    this host ``parallel`` at a time, else one scheduler job each."""
    if not jobs:
        return {}
    if executor is None:
        return run_jobs_here(jobs, launch=ctx.launch, parallel=parallel)
    return run_jobs_lsf(jobs, executor=executor, timeout_s=ctx.launch.timeout_s)


def settle_merge(merge: MergeResult, end: tuple[int | None, str]) -> MergeResult:
    """A merge with its job's verdict."""
    status, reason = job_verdict(*end)
    return replace(merge, status=status, reason=reason)


def merged_runs(results: Sequence[RunResult]) -> list[RunResult]:
    """The runs --cov-merge reads: each passing run that left coverage data and is not an
    expected failure."""
    return [
        result
        for result in results
        if result.status == PASS and result.spec.expected_fail is None and result.coverage
    ]


def coverage_inputs(results: Sequence[RunResult], sim: str) -> list[Path]:
    """The coverage data of ``results``: on VCS the simv.vdb of each build they used, then
    each run's database."""
    data = [result.coverage for result in results if result.coverage is not None]
    if sim != "vcs":
        return data
    shapes = dict.fromkeys(BENCH_DIR / r.spec.build_dir / VCS_BUILD_COVERAGE for r in results)
    return [*(shape for shape in shapes if shape.exists()), *data]


def merge_groups(results: Sequence[RunResult], sim: str) -> dict[str | None, list[Path]]:
    """The inputs of each --cov-merge: under None those of every merged run, and on VCS,
    when the merged runs span more than one test build, under each build's name its own."""
    merged = merged_runs(results)
    groups: dict[str | None, list[Path]] = {None: coverage_inputs(merged, sim)}
    by_build: dict[str, list[RunResult]] = {}
    for result in merged:
        if not result.spec.is_build_check:
            by_build.setdefault(result.spec.build, []).append(result)
    if sim == "vcs" and len(by_build) > 1:
        groups.update((build, coverage_inputs(runs, sim)) for build, runs in by_build.items())
    return groups


def merge_output(ctx: Context, merge_dir: Path) -> Path:
    """The merged database --cov-merge writes."""
    return merge_dir / (ctx.site.cov_merge_output or MERGE_OUTPUT[ctx.sim.name])


def merge_command(ctx: Context, merge_dir: Path, inputs: Sequence[str]) -> list[str]:
    """The --cov-merge command: the site's cov_merge_argv, else the simulator's own."""
    template = ctx.site.cov_merge_argv or MERGE_ARGV[ctx.sim.name]
    values = {
        "output": str(merge_output(ctx, merge_dir)),
        "inputs_file": str(merge_dir / MERGE_INPUTS),
        "merge_dir": str(merge_dir),
    }
    return render_argv(template, values, {"inputs": inputs})


def plan_merges(
    results: Sequence[RunResult], *, ctx: Context, stamp: str
) -> list[tuple[MergeResult, ToolJob | None]]:
    """Each --cov-merge, unsettled, with its job: the merge of every merged run in
    sim/cov_merge.<stamp>/, and each build's in the directory of its name there. Each lists
    its inputs in inputs.txt in its directory; one without inputs is an error and has no job."""
    top = SIM_DIR / f"cov_merge.{stamp}"
    planned: list[tuple[MergeResult, ToolJob | None]] = []
    for index, (build, inputs) in enumerate(merge_groups(results, ctx.sim.name).items()):
        merge_dir = top if build is None else top / build
        merge_dir.mkdir(parents=True, exist_ok=True)
        paths = [str(path) for path in inputs]
        (merge_dir / MERGE_INPUTS).write_text("".join(f"{path}\n" for path in paths))
        cmd = merge_command(ctx, merge_dir, paths)
        log = merge_dir / "merge.log"
        output = merge_output(ctx, merge_dir)
        merge = MergeResult("", output, len(paths), shlex.join(cmd), log, build=build)
        if not paths:
            planned.append((replace(merge, status=ERROR, reason=NO_COVERAGE), None))
        else:
            task_id = "merge" if build is None else f"merge{index:02d}"
            planned.append((merge, ToolJob(task_id, tuple(cmd), log)))
    return planned


def hits_runs(results: Sequence[RunResult]) -> dict[str, list[RunResult]]:
    """The passing runs of each test under the test the manifest names, expected failures and
    the bring-up runs of legal build checks included; an illegal build check records no
    coverage and is left out. A test's cover-hit file counts every one of them."""
    by_test: dict[str, list[RunResult]] = {}
    for result in results:
        if result.status == PASS and result.spec.expect_message is None:
            by_test.setdefault(result.spec.owner or result.spec.test, []).append(result)
    return dict(sorted(by_test.items()))


def cover_label(name: str) -> str:
    """A cover's label: the last component of its hierarchical name."""
    return name.rsplit(".", 1)[-1]


def verilator_cover_counts(text: str) -> Counter[str]:
    """Hits per cover label in a Verilator coverage.dat: the count of each cover-property
    point under the label it records, summed over the instances that share a label."""
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        point = VERILATOR_POINT.match(line) if f"\x02{VERILATOR_COVER_TYPE}" in line else None
        if point is None:
            continue
        pairs = (item.split("\x02", 1) for item in point.group(1).split("\x01") if "\x02" in item)
        fields = dict(pairs)
        name = fields.get("o") or fields.get("h")
        if fields.get("t") == VERILATOR_COVER_TYPE and name:
            counts[cover_label(name)] += int(point.group(2))
    return counts


def vcs_cover_counts(report: str) -> Counter[str]:
    """Matches per cover label in a VCS text assertion report: each row of its cover-property
    sections that starts with a hierarchical name, read in the column the section's header
    row names MATCHES, summed over the instances that share a label."""
    counts: Counter[str] = Counter()
    inside, column = False, 0
    for line in report.splitlines():
        if REPORT_SECTION.match(line):
            inside, column = bool(COVER_SECTION.match(line)), 0
            continue
        words = line.split()
        if not inside or not words:
            continue
        if MATCHES_HEADER in words:
            column = words.index(MATCHES_HEADER) - len(words)
        elif (
            column
            and len(words) > -column
            and "." in words[0]
            and all(word.isdigit() for word in words[1:])
        ):
            counts[cover_label(words[0])] += int(words[column])
    return counts


def hits_problem(ctx: Context) -> str | None:
    """Why --cov writes no cover-hit files on this simulator; None when it does."""
    if ctx.sim.name != "vcs" or ctx.site.cov_hits_argv:
        return None
    return (
        f"VCS needs cov_hits_argv under [simulators.vcs] in the site file ({site_layer.SITE_ENV})"
    )


def hits_command(ctx: Context, work_dir: Path, inputs: Sequence[str]) -> list[str]:
    """A test's cover-hit merge: the site's cov_hits_argv, reporting into work_dir/report."""
    values = {
        "inputs_file": str(work_dir / MERGE_INPUTS),
        "report_dir": str(work_dir / HITS_REPORT_DIR),
        "merge_dir": str(work_dir),
    }
    return render_argv(ctx.site.cov_hits_argv, values, {"inputs": inputs})


def unsettled_hits(test: str, runs: Sequence[RunResult], hits_dir: Path) -> HitsResult:
    """A test's cover-hit file before its counts: where it goes and the runs it counts."""
    xfails = sum(1 for result in runs if status_label(result) == "XFAIL")
    return HitsResult(test, "", hits_dir / f"{test}.json", len(runs), xfails)


def write_hits(hits: HitsResult, counts: Mapping[str, int]) -> HitsResult:
    """Write a test's cover-hit file, {"test", "hits", "runs", "expected_fail_runs"} with
    every cover under its label; an error, without a file, when ``counts`` name no cover."""
    if not counts:
        return replace(hits, status=ERROR, reason="its coverage data holds no cover")
    document = {
        "test": hits.test,
        "hits": dict(sorted(counts.items())),
        "runs": hits.runs,
        "expected_fail_runs": hits.expected_fail_runs,
    }
    hits.path.write_text(json.dumps(document, indent=1) + "\n")
    return replace(hits, status=PASS)


def verilator_hits(test: str, runs: Sequence[RunResult], hits_dir: Path) -> HitsResult:
    """A test's cover-hit file from its runs' coverage.dat files."""
    counts: Counter[str] = Counter()
    for result in runs:
        if result.coverage is not None:
            counts.update(verilator_cover_counts(read_log(result.coverage)))
    return write_hits(unsettled_hits(test, runs, hits_dir), counts)


def plan_vcs_hits(
    test: str, runs: Sequence[RunResult], *, ctx: Context, hits_dir: Path, task_id: str
) -> tuple[HitsResult, ToolJob]:
    """A test's cover-hit file, unsettled, and the job that merges its runs in the directory
    of the test's name in ``hits_dir``, which lists the inputs in inputs.txt."""
    work_dir = hits_dir / test
    work_dir.mkdir(parents=True, exist_ok=True)
    inputs = [str(path) for path in coverage_inputs(runs, "vcs")]
    (work_dir / MERGE_INPUTS).write_text("".join(f"{path}\n" for path in inputs))
    cmd = hits_command(ctx, work_dir, inputs)
    log = work_dir / "hits.log"
    hits = replace(unsettled_hits(test, runs, hits_dir), command=shlex.join(cmd), log=log)
    return hits, ToolJob(task_id, tuple(cmd), log)


def settle_vcs_hits(hits: HitsResult, end: tuple[int | None, str]) -> HitsResult:
    """A test's cover-hit file from the text assertion report its merge job wrote."""
    status, reason = job_verdict(*end)
    if status != PASS:
        return replace(hits, status=status, reason=reason)
    report = hits.path.parent / hits.test / HITS_REPORT_DIR / VCS_COVER_REPORT
    if not report.is_file():
        return replace(hits, status=ERROR, reason=f"the merge wrote no {report}")
    return write_hits(hits, vcs_cover_counts(report.read_text(errors="replace")))


def plan_hits(
    results: Sequence[RunResult], *, ctx: Context, hits_dir: Path
) -> tuple[list[HitsResult], list[tuple[HitsResult, ToolJob]]]:
    """Each test's cover-hit file in ``hits_dir``: on Verilator written at once, on VCS
    unsettled with its merge job; an error, without a file, for a test with a passing run
    that left no coverage data, counting its passing runs and, when some of them left
    data, naming those that did not."""
    hits_dir.mkdir(parents=True, exist_ok=True)
    ready: list[HitsResult] = []
    pending: list[tuple[HitsResult, ToolJob]] = []
    for index, (test, passing) in enumerate(hits_runs(results).items()):
        runs = [result for result in passing if result.coverage is not None]
        bare = [result.spec.run_dir.name for result in passing if result.coverage is None]
        if bare:
            reason = NO_COVERAGE
            if runs:
                reason = (
                    f"{len(bare)} of {len(passing)} passing runs left no coverage data: "
                    f"{', '.join(bare)}"
                )
            hits = unsettled_hits(test, passing, hits_dir)
            ready.append(replace(hits, status=ERROR, reason=reason))
        elif ctx.sim.name == "vcs":
            task_id = f"hits{index:03d}"
            pending.append(plan_vcs_hits(test, runs, ctx=ctx, hits_dir=hits_dir, task_id=task_id))
        else:
            ready.append(verilator_hits(test, runs, hits_dir))
    return ready, pending


def finish_coverage(
    results: Sequence[RunResult],
    *,
    ctx: Context,
    executor: LsfExecutor | None,
    stamp: str,
    merge: bool,
    hits: bool,
    parallel: int,
) -> CoverageResult:
    """Merge the runs' coverage under ``merge`` and write each test's cover-hit file in
    sim/cover_hits.<stamp>/ under ``hits``; every merge the tool runs goes in one batch."""
    merges = plan_merges(results, ctx=ctx, stamp=stamp) if merge else []
    note = (hits_problem(ctx) or "") if hits else ""
    hits_dir = SIM_DIR / f"cover_hits.{stamp}" if hits and not note else None
    ready, pending = plan_hits(results, ctx=ctx, hits_dir=hits_dir) if hits_dir else ([], [])
    jobs = [job for _, job in merges if job is not None] + [job for _, job in pending]
    ends = run_tool_jobs(jobs, ctx=ctx, executor=executor, parallel=parallel)
    settled = tuple(
        merge if job is None else settle_merge(merge, ends[job.task_id]) for merge, job in merges
    )
    written = (*ready, *(settle_vcs_hits(hits, ends[job.task_id]) for hits, job in pending))
    return CoverageResult(settled, written, hits_dir, note)


def merge_record(merge: MergeResult) -> dict[str, object]:
    """A merge's entry in the manifest's coverage_merge."""
    return {
        "status": merge.status,
        "reason": merge.reason,
        "output": str(merge.output),
        "inputs": merge.inputs,
        "command": merge.command,
        "log": str(merge.log),
    }


def hits_record(hits: HitsResult) -> dict[str, object]:
    """A test's entry in the manifest's cover_hits."""
    return {
        "status": hits.status,
        "reason": hits.reason,
        "file": str(hits.path) if hits.status == PASS else None,
        "runs": hits.runs,
        "expected_fail_runs": hits.expected_fail_runs,
        "command": hits.command,
        "log": None if hits.log is None else str(hits.log),
    }


def coverage_records(coverage: CoverageResult) -> dict[str, object]:
    """The manifest's coverage_merge entry, each build's merge under its "builds", and its
    cover_hits entry: the directory, or why there is none, and each test's file."""
    records: dict[str, object] = {}
    if coverage.merges:
        everything, *builds = coverage.merges
        per_build = {str(merge.build): merge_record(merge) for merge in builds}
        records["coverage_merge"] = {**merge_record(everything), "builds": per_build}
    if coverage.hits_dir is not None or coverage.hits_note:
        records["cover_hits"] = {
            "dir": None if coverage.hits_dir is None else str(coverage.hits_dir),
            "reason": coverage.hits_note,
            "tests": {hits.test: hits_record(hits) for hits in coverage.hits},
        }
    return records


def print_coverage(coverage: CoverageResult) -> None:
    """The run summary's coverage lines: the merges, and where the cover-hit files are."""
    if coverage.merges:
        everything, *builds = coverage.merges
        note = f" ({everything.reason})" if everything.reason else ""
        print(
            f"coverage merge {everything.status}: {everything.output},"
            f" {everything.inputs} inputs{note}"
        )
        if builds:
            passed = sum(1 for merge in builds if merge.status == PASS)
            print(
                f"coverage merges per build: {passed} of {len(builds)} pass, in"
                f" {everything.output.parent}/<build>/, for toggle and parameter-shaped conditions"
            )
        for merge in builds:
            if merge.status != PASS:
                print(f"coverage merge {merge.status}: {merge.build}: {merge.reason}")
    if coverage.hits_note:
        print_note(f"cover hits: none written; {coverage.hits_note}")
    elif coverage.hits_dir is not None:
        written = sum(1 for hits in coverage.hits if hits.status == PASS)
        print(f"cover hits: {written} of {len(coverage.hits)} tests in {coverage.hits_dir}")
        for hits in coverage.hits:
            if hits.status != PASS:
                print(f"cover hits {hits.status}: {hits.test}: {hits.reason}")


# ---------------------------------------------------------------------------------------
# Manifest


def revision_label(commit: str, diff: bytes) -> str:
    """A commit's first 12 characters, with ``+`` and 8 hex digits of the diff's SHA-256
    when the tree differs from it; ``unknown`` with no commit."""
    if not commit:
        return "unknown"
    if not diff:
        return commit[:12]
    return f"{commit[:12]}+{hashlib.sha256(diff).hexdigest()[:8]}"


def source_revision(root: Path) -> str:
    """The revision of the git work tree that holds ``root``: HEAD and the whole tree's diff
    from it, untracked files left out; ``unknown`` outside a git work tree or without git."""
    if shutil.which("git") is None:
        return "unknown"
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    if head.returncode != 0:
        return "unknown"
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "HEAD", "--binary"], check=False, capture_output=True
    )
    return revision_label(head.stdout.strip(), diff.stdout if diff.returncode == 0 else b"")


def manifest_record(
    result: RunResult, sim: SimTool, *, revision: str, mutation: Mutation | None = None
) -> dict[str, object]:
    """One run's entry in the trace store's runs manifest, with the runner's own fields.

    ``kind`` is ``normal`` or, under ``mutation``, ``mutation`` with the fault's name and
    ``targets``. ``expected_fail`` marks a run graded XFAIL, a failure on the pin in
    ``expected_fail_match`` with only the lines of ``expected_fail_allow`` beside it;
    ``run_kind`` tells a test from a build check, and ``check`` is
    the plan check a build check of a testlist entry grades.
    """
    spec = result.spec

    def path(value: Path | None) -> str | None:
        return None if value is None else str(value)

    ran = result.clocks
    return {
        "test": spec.owner or spec.test,
        "kind": NORMAL_RUN if mutation is None else MUTATION_RUN,
        "mutation": "" if mutation is None else mutation.name,
        "targets": [] if mutation is None else list(mutation.targets),
        "revision": revision,
        "realization": REALIZATION,
        "attempt": 0,
        "started": result.started,
        "run_kind": spec.run_kind,
        "check": spec.check_id,
        "expected_fail": status_label(result) == "XFAIL",
        "expected_fail_match": spec.expected_fail,
        "expected_fail_allow": list(spec.expected_fail_allow),
        "build": spec.build,
        "overrides": dict(spec.overrides),
        "seed": spec.seed,
        "ratio": spec.ratio.name,
        "clk_period_ps": spec.ratio.clk_period_ps,
        "tck_period_ps": spec.ratio.tck_period_ps,
        "clk_ps": None if ran is None else ran.clk_ps,
        "tck_ps": None if ran is None else ran.tck_ps,
        "phase_ps": None if ran is None else ran.phase_ps,
        "status": result.status,
        "reason": result.reason,
        "duration_s": round(result.duration_s, 3),
        "run_dir": str(spec.run_dir),
        "log": path(result.log),
        "results": path(result.results),
        "coverage": path(result.coverage),
        "build_log": path(result.build_log),
        "command": result.command,
        "simulator": sim.name,
        "simulator_version": sim.version,
    }


def write_manifest(
    path: Path,
    results: Sequence[RunResult],
    *,
    sim: SimTool,
    argv: Sequence[str],
    revision: str = "unknown",
    mutation: Mutation | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Write the JSON manifest: the command line, the revision, the status counts, one
    entry per run, then the ``extra`` top-level entries."""
    counts = Counter(result.status for result in results)
    document: dict[str, object] = {
        "command": shlex.join(["run_sim.py", *argv]),
        "simulator": sim.name,
        "simulator_version": sim.version,
        "revision": revision,
        "summary": {status: counts.get(status, 0) for status in STATUSES},
        "runs": [
            manifest_record(result, sim, revision=revision, mutation=mutation) for result in results
        ],
        **(extra or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


# ---------------------------------------------------------------------------------------
# Command line


class AddExpectFail(argparse.Action):
    """--expect-build-fail: append (check, None); a following --expect-message replaces the None."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        pairs = list(getattr(namespace, self.dest) or [])
        setattr(namespace, self.dest, [*pairs, (values, None)])


class SetExpectMessage(argparse.Action):
    """--expect-message: the message of the --expect-build-fail just before it."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        pairs = list(getattr(namespace, self.dest) or [])
        if not pairs or pairs[-1][1] is not None:
            parser.error("each --expect-message follows the --expect-build-fail it belongs to")
        pairs[-1] = (pairs[-1][0], values)
        setattr(namespace, self.dest, pairs)


def add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """The options that choose tests, builds, seeds and ratios."""
    parser.add_argument(
        "--list", action="store_true", help="list tests, groups, builds and clock ratios, then exit"
    )
    parser.add_argument(
        "--test", action="append", default=[], metavar="NAME", help="run this test (repeatable)"
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="NAME",
        help=f"run this group (repeatable); {SIGNOFF_GROUP} runs every target with --reseed"
        " unless a target or seed option is given",
    )
    parser.add_argument(
        "--target",
        "--build",
        dest="target",
        action="append",
        default=[],
        metavar="TARGET",
        help="build target to run on, one of the Makefile's BUILDS (repeatable); 'all' is"
        " every one; default: the Makefile's CONFIG",
    )
    seeding = parser.add_mutually_exclusive_group()
    seeding.add_argument("--seed", type=int, metavar="S", help="run each selected test with seed S")
    seeding.add_argument("--seeds", type=int, metavar="N", help="random seeds per test (default 1)")
    seeding.add_argument(
        "--reseed",
        action="store_true",
        help="run each test with its testlist reseed count of random seeds; without --ratio"
        " at least six, rotating through every named ratio and random",
    )
    parser.add_argument(
        "--ratio",
        action="append",
        default=[],
        metavar="A:B|random|all",
        help="i_clk:TCK clock ratio from the CFG-CLK-RATIO dimension, 'random' (the bench"
        " draws i_clk from the seed) or 'all' (every named ratio and random); repeatable,"
        " and each seed runs at each ratio",
    )


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """The options that control execution, checks and output."""
    parser.add_argument(
        "--jobs",
        type=int,
        metavar="N",
        help="parallel jobs (default 1); under --executor lsf, the most jobs queued or running"
        " at once, within the site's max_in_flight",
    )
    parser.add_argument(
        "--wave",
        action="store_true",
        help=f"dump waves in each run directory: waves.fst on Verilator; on VCS {VCS_WAVE_FILE}"
        f" through +{VCD_PLUSARG}, or the site file's wave settings",
    )
    parser.add_argument(
        "--cov",
        action="store_true",
        help=f"compile coverage (Verilator --coverage, VCS -cm {VCS_COVERAGE}) into a build of"
        " its own; each run writes its data in its run directory, and each test's cover hits"
        " go to sim/cover_hits.<timestamp>/<test>.json",
    )
    parser.add_argument(
        "--cov-merge",
        action="store_true",
        help="merge the passing runs' coverage into sim/cov_merge.<timestamp>/ after the runs:"
        " verilator_coverage, or the site file's cov_merge_argv, which on VCS also merges each"
        " build's runs into <build>/ there",
    )
    parser.add_argument("--sim", choices=SIMULATORS, default="verilator", help="simulator")
    parser.add_argument(
        "--executor",
        choices=EXECUTORS,
        default="local",
        help="where builds and runs execute: local (this host, the default) or lsf (one job"
        f" each, set up by the site file {site_layer.SITE_ENV} names or"
        f" scripts/{site_layer.SITE_FILE_NAME})",
    )
    parser.add_argument(
        "--expect-build-fail",
        action=AddExpectFail,
        dest="expect_build_fail",
        default=[],
        metavar="BUILD[:PARAM=VALUE,...]",
        help="check that this build, with these parameter overrides, does not elaborate"
        " (repeatable)",
    )
    parser.add_argument(
        "--expect-message",
        action=SetExpectMessage,
        dest="expect_build_fail",
        metavar="REGEX",
        help="line the --expect-build-fail before it must find in its build's or bring-up"
        f" run's tool output (default {DEFAULT_EXPECT_MESSAGE!r})",
    )
    parser.add_argument(
        "--manifest", type=Path, metavar="PATH", help="default sim/manifest.<timestamp>.json"
    )
    parser.add_argument(
        "--mutation",
        metavar="NAME",
        help="record every run as a mutation run named NAME, which --targets must fail; a"
        " NAME of the form VAR=VALUE also sets VAR in each simulation's environment",
    )
    parser.add_argument(
        "--targets",
        metavar="CHK-A,CHK-B",
        help="the checks the --mutation runs must fail, comma separated",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        metavar="SECONDS",
        help=f"wall-clock limit of each build and run (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument("--rebuild", action="store_true", help="rebuild each model from scratch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned runs, and under --executor lsf the bsub commands, and exit",
    )


def build_parser() -> argparse.ArgumentParser:
    """The runner's argument parser."""
    parser = argparse.ArgumentParser(
        description="Run tdr2axi cocotb tests from testlists/all.toml through the bench Makefile.",
    )
    add_selection_arguments(parser)
    add_run_arguments(parser)
    return parser


def print_catalogue(bench: Bench) -> None:
    """The tests, groups, builds and ratios the runner can select, on stdout."""
    groups = bench.testlist.groups
    print(f"Tests ({TESTLIST.relative_to(BENCH_DIR)}):")
    for test in bench.testlist.tests:
        member_of = ", ".join(g.name for g in groups if test.name in g.tests) or "-"
        targets = f"  targets: {', '.join(test.targets)}" if test.targets else ""
        targets += f"  ratios: {', '.join(test.ratios)}" if test.ratios else ""
        if test.bringup_targets:
            targets += f"  bring-up-only builds: {', '.join(test.bringup_targets)}"
        kind = "  expected to fail" if test.expected_fail else ""
        if test.build_cases:
            kind += f"  {len(test.build_cases)} build checks of {test.build_check}"
        print(f"  {test.name:34} reseed {test.reseed:3}  groups: {member_of}{targets}{kind}")
    print("Groups:")
    for group in groups:
        print(f"  {group.name:12} {len(group.tests)} tests")
    print("Builds (Makefile BUILDS):")
    for build in bench.make.builds:
        notes = []
        if build == bench.make.reference:
            notes.append("reference")
        if build in bench.make.bringup_only:
            notes.append(f"{BRINGUP_TEST} only")
        print(f"  {build}" + (f"  ({', '.join(notes)})" if notes else ""))
    print(f"Clock ratios i_clk:TCK ({CONFIGS.relative_to(BENCH_DIR)} {RATIO_DIMENSION}):")
    for ratio in bench.ratios:
        default = "  (default)" if ratio == bench.default_ratio else ""
        print(
            f"  {ratio.name:6} CLK_PERIOD_PS={ratio.clk_period_ps}"
            f" TCK_PERIOD_PS={ratio.tck_period_ps}{default}"
        )
    low, high = random_period_span(bench.make.tck_period_ps)
    print(f"  {RANDOM_RATIO:6} CLK_PERIOD_PS={RANDOM_RATIO}, i_clk drawn in {low}..{high} ps")


def print_plan(runs: Sequence[RunSpec], checks: Sequence[RunSpec]) -> None:
    """The planned runs and checks, one per line, and their count per ratio."""
    for spec in [*runs, *checks]:
        kind = f"{spec.run_kind} " if spec.is_build_check else ""
        xfail = f" expected-fail={spec.expected_fail!r}" if spec.expected_fail else ""
        overrides = "".join(f" {param}={value}" for param, value in spec.overrides)
        print(
            f"{kind}{spec.owner or spec.test} {spec.build}{overrides} seed={spec.seed}"
            f" ratio={spec.ratio.name} {spec.run_dir.name}{xfail}"
        )
    per_ratio = Counter(run.ratio.name for run in runs)
    print(
        f"{len(runs)} runs ({', '.join(f'{k}: {v}' for k, v in per_ratio.items())}),"
        f" {len(checks)} build checks"
    )


def missing_tools(sim: str) -> list[str]:
    """The required tools that are not on PATH."""
    tools = ("make", "bender", "cocotb-config", sim)
    return [tool for tool in tools if shutil.which(tool) is None]


def missing_binaries(names: Sequence[str]) -> list[str]:
    """The scheduler commands that are not on PATH."""
    return [name for name in names if shutil.which(name) is None]


def report_missing(sim: str, executor: LsfExecutor | None) -> bool:
    """Print each tool this host lacks with its hint: the build and simulation tools for a
    local run, the scheduler commands for a scheduler run; whether any is missing."""
    missing = missing_tools(sim) if executor is None else missing_binaries(executor.binaries)
    for tool in missing:
        hint = INSTALL_HINTS.get(tool, SCHEDULER_HINT)
        print(f"run_sim: {tool} not found on PATH; {hint}", file=sys.stderr)
    return bool(missing)


def probe_cocotb_major() -> int:
    """The cocotb major release cocotb-config reports; COCOTB_DEFAULT_MAJOR without one."""
    if shutil.which("cocotb-config") is None:
        return COCOTB_DEFAULT_MAJOR
    cocotb = subprocess.run(
        ["cocotb-config", "--version"], check=False, capture_output=True, text=True
    )
    major = re.match(r"\s*(\d+)", cocotb.stdout)
    return int(major.group(1)) if major else COCOTB_DEFAULT_MAJOR


def probe_simulator(sim: str) -> SimTool:
    """The simulator's version line and the cocotb major release."""
    version_cmd = ["verilator", "--version"] if sim == "verilator" else ["vcs", "-ID"]
    out = subprocess.run(version_cmd, check=False, capture_output=True, text=True)
    lines = (out.stdout + out.stderr).strip().splitlines()
    version = next((line for line in lines if sim in line.lower()), lines[0] if lines else "")
    return SimTool(sim, version.strip() or "unknown", probe_cocotb_major())


def simulator_tool(sim: str, *, local: bool, cocotb_major: int | None = None) -> SimTool:
    """The simulator the manifest records: probed on this host, except that a scheduler run
    on a host without the tools records an unknown version at cocotb-config's release.
    ``cocotb_major``, the site's declaration for its jobs, replaces the probed release."""
    if local or not missing_tools(sim):
        tool = probe_simulator(sim)
    else:
        print(f"run_sim: {sim} is not on this host's PATH; its version is unknown", file=sys.stderr)
        tool = SimTool(sim, "unknown", probe_cocotb_major())
    return tool if cocotb_major is None else replace(tool, cocotb_major=cocotb_major)


def print_note(line: str) -> None:
    """Print a progress or diagnostic line on stderr."""
    print(f"run_sim: {line}", file=sys.stderr)


def active_site() -> site_layer.Site:
    """The settings of the site file TDR2AXI_DV_SITE names, else of scripts/site.local.toml."""
    return site_layer.load_site(SITE_FILE_ENV, SITE_FILE)


def make_lsf_executor(
    site: site_layer.Site, *, sim: str, stamp: str, jobs: int | None
) -> LsfExecutor:
    """The LSF executor of one invocation: the site's [lsf] settings with --jobs capping
    max_in_flight, the simulator's setup hook, and sim/lsf.<stamp>/ as its work directory."""
    lsf = site.lsf
    if jobs is not None:
        limit = lsf.limits.max_in_flight
        capped = jobs if limit is None else min(limit, jobs)
        lsf = replace(lsf, limits=replace(lsf.limits, max_in_flight=capped))
    return LsfExecutor(
        lsf,
        work_dir=SIM_DIR / f"lsf.{stamp}",
        bench_dir=BENCH_DIR,
        name=f"tdr2axi.{stamp}",
        hook=site.sim(sim).setup_hook,
        notify=print_note,
    )


def parse_mutation(name: str | None, targets: str | None) -> Mutation | None:
    """--mutation and --targets as a Mutation; both or neither, and every target a check ID."""
    if name is None and targets is None:
        return None
    if not name or not targets:
        raise ConfigError("--mutation and --targets go together")
    checks = tuple(filter(None, (target.strip() for target in targets.split(","))))
    wrong = [check for check in checks if not CHECK_ID.fullmatch(check)]
    if not checks or wrong:
        raise ConfigError(f"--targets needs check IDs such as CHK-X-Y; got {targets!r}")
    return Mutation(name, checks)


def plan_from_args(
    args: argparse.Namespace, bench: Bench, *, stamp: str, site: site_layer.Site | None = None
) -> tuple[list[RunSpec], list[RunSpec], Selection]:
    """The runs, the expect-build-fail checks and the selection a command line asks for.

    Under --mutation no run is graded as an expected failure. ``site`` decides whether
    --wave and --cov-merge can work on the simulator.
    """
    seeds = 1 if args.seeds is None else args.seeds
    jobs = 1 if args.jobs is None else args.jobs
    if jobs < 1 or seeds < 1 or args.timeout <= 0:
        raise ConfigError("--jobs, --seeds and --timeout must be positive")
    sim_site = (site or site_layer.Site()).sim(args.sim)
    problems = (
        wave_problem(args.sim, sim_site) if args.wave else None,
        merge_problem(args.sim, sim_site, cov=args.cov) if args.cov_merge else None,
    )
    for problem in problems:
        if problem is not None:
            raise ConfigError(problem)
    checks = [
        plan_expect_fail(
            parse_expect_fail(text, bench.make),
            bench=bench,
            message=message or DEFAULT_EXPECT_MESSAGE,
            stamp=stamp,
            index=index,
        )
        for index, (text, message) in enumerate(args.expect_build_fail, start=1)
    ]
    tests_named = args.test or args.group or not checks
    signoff = SIGNOFF_GROUP in args.group
    seeding_given = args.seed is not None or args.seeds is not None or args.reseed
    selection = Selection(
        tests=select_tests(bench.testlist, names=args.test, groups=args.group)
        if tests_named
        else (),
        builds=select_builds(bench.make, args.target or (["all"] if signoff else [])),
        ratios=select_ratios(bench, args.ratio),
        seed=args.seed,
        seeds=seeds,
        reseed=args.reseed or (signoff and not seeding_given),
        sim=args.sim,
        wave=args.wave,
        cov=args.cov,
        default_builds=not args.target and not signoff,
        named_builds=bool(args.target),
    )
    rng = random.SystemRandom()
    runs = plan_runs(bench, selection, stamp=stamp, rng=rng)
    if parse_mutation(args.mutation, args.targets) is not None:
        runs = [replace(run, expected_fail=None, expected_fail_allow=()) for run in runs]
    checks += plan_build_cases(bench, selection, stamp=stamp, rng=rng)
    return runs, checks, selection


def print_dry_run(
    runs: Sequence[RunSpec],
    checks: Sequence[RunSpec],
    *,
    ctx: Context,
    executor: LsfExecutor | None,
    args: argparse.Namespace,
    stamp: str,
    site: site_layer.Site,
) -> None:
    """The plan, then for a scheduler run the site file and the bsub commands, then the
    merge and cover-hit commands."""
    print_plan(runs, checks)
    if executor is not None:
        print(f"site file: {site.path or 'none'}")
        print_lsf_plan(runs, checks, ctx=ctx, executor=executor, timeout_s=args.timeout)
    if args.cov_merge:
        merge_dir = SIM_DIR / f"cov_merge.{stamp}"
        cmd = merge_command(ctx, merge_dir, ["<coverage of each passing run>"])
        print(f"coverage merge: {shlex.join(cmd)}")
        if ctx.sim.name == "vcs":
            print(f"coverage merge per build: the same command in {merge_dir}/<build>/")
    if args.cov and args.mutation is None:
        hits_dir = SIM_DIR / f"cover_hits.{stamp}"
        note = hits_problem(ctx)
        if note is not None:
            print(f"cover hits: none written; {note}")
        elif ctx.sim.name == "vcs":
            cmd = hits_command(ctx, hits_dir / "<test>", ["<coverage of each of its passing runs>"])
            print(f"cover hits: {shlex.join(cmd)}")
        else:
            print(f"cover hits: from each passing run's coverage.dat into {hits_dir}/")


def run_plan(
    runs: Sequence[RunSpec],
    checks: Sequence[RunSpec],
    *,
    ctx: Context,
    executor: LsfExecutor | None,
    args: argparse.Namespace,
    argv: Sequence[str],
    stamp: str,
    mutation: Mutation | None,
) -> int:
    """Run the plan here or on the scheduler, merge coverage under --cov-merge, write the
    cover-hit files under --cov, write the manifest and print the summary; the exit status."""
    revision = source_revision(BENCH_DIR)
    coverage = CoverageResult()
    hits = args.cov and mutation is None
    stopped = False
    try:
        if executor is None:
            results = execute(runs, checks, ctx=ctx, jobs=args.jobs or 1)
        else:
            results = execute_lsf(runs, checks, ctx=ctx, executor=executor, timeout_s=args.timeout)
        if args.cov_merge or hits:
            coverage = finish_coverage(
                results,
                ctx=ctx,
                executor=executor,
                stamp=stamp,
                merge=args.cov_merge,
                hits=hits,
                parallel=args.jobs or 1,
            )
    except KeyboardInterrupt:
        print("run_sim: interrupted", file=sys.stderr)
        return 130
    except SchedulerStopped as exc:
        print(f"run_sim: {exc}", file=sys.stderr)
        results, stopped = exc.results, True
    manifest = args.manifest or SIM_DIR / f"manifest.{stamp}.json"
    write_manifest(
        manifest,
        results,
        sim=ctx.sim,
        argv=argv,
        revision=revision,
        mutation=mutation,
        extra=coverage_records(coverage),
    )
    counts = Counter(result.status for result in results)
    xfails = sum(1 for result in results if status_label(result) == "XFAIL")
    print(
        f"{len(results)} runs: {counts[PASS]} pass ({xfails} expected failures),"
        f" {counts[FAIL]} fail, {counts[ERROR]} error; manifest {manifest}"
    )
    print_coverage(coverage)
    if stopped:
        return 2
    return 0 if counts[PASS] == len(results) and coverage.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line, run the selected tests and checks, and write the manifest."""
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    try:
        bench = load_bench()
        if args.list:
            print_catalogue(bench)
            return 0
        site = active_site()
        runs, checks, selection = plan_from_args(args, bench, stamp=stamp, site=site)
        mutation = parse_mutation(args.mutation, args.targets)
        executor = (
            make_lsf_executor(site, sim=args.sim, stamp=stamp, jobs=args.jobs)
            if args.executor == "lsf"
            else None
        )
    except (ConfigError, site_layer.SiteError) as exc:
        print(f"run_sim: {exc}", file=sys.stderr)
        return 2
    for build, names in skipped_tests(bench, selection).items():
        print(f"run_sim: {build} skips {len(names)} selected tests", file=sys.stderr)
    ctx = Context(
        SimTool(args.sim, "unknown", COCOTB_DEFAULT_MAJOR),
        Launcher(args.timeout),
        wave=args.wave,
        rebuild=args.rebuild,
        cov=args.cov,
        env=() if mutation is None else mutation.env,
        site=site.sim(args.sim),
    )
    if args.dry_run:
        print_dry_run(runs, checks, ctx=ctx, executor=executor, args=args, stamp=stamp, site=site)
        return 0
    if not runs and not checks:
        print("run_sim: nothing to run", file=sys.stderr)
        return 2
    if report_missing(args.sim, executor):
        return 2
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    if not (SIM_DIR / ".gitignore").exists():
        (SIM_DIR / ".gitignore").write_text("*\n")
    declared = None if executor is None else site.sim(args.sim).cocotb_major
    tool = simulator_tool(args.sim, local=executor is None, cocotb_major=declared)
    ctx = replace(ctx, sim=tool)
    return run_plan(
        runs,
        checks,
        ctx=ctx,
        executor=executor,
        args=args,
        argv=argv,
        stamp=stamp,
        mutation=mutation,
    )


if __name__ == "__main__":
    raise SystemExit(main())
