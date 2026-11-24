# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of run_sim.py against a fake launcher that stands in for make; no simulator runs.

Covers testlist, Makefile and clock-ratio parsing, test, build and ratio selection, the
builds and ratios a testlist entry carries, run planning, JUnit and logged-clock grading,
build reuse, the expect-build-fail options and verdict, expected failures and the error
lines they allow, build-check tests, mutation runs, the revision, manifest writing and
main's exit status.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -v
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import tempfile
import tomllib
import unittest
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_make
import run_sim
import site_layer

BRINGUP = run_sim.BRINGUP_TEST

MAKEFILE_TEXT = """\
BUILDS := big small \\
          tiny
build_big   := 0 64 64
build_small := 1 32 32
build_tiny  := 0 2 32
BRINGUP_ONLY := tiny
CONFIG ?= big
ifneq ($(filter $(CONFIG),$(RATIOS)),)
  CONFIG := small
  TCK_PERIOD_PS ?= 99
endif
USE_AXI_LITE   := $(word 1,$(build_$(BUILD)))
AXI_ADDR_WIDTH := $(word 2,$(build_$(BUILD)))
AXI_DATA_WIDTH := $(word 3,$(build_$(BUILD)))
COMPILE_ARGS += -GAXI_ADDR_WIDTH=$(AXI_ADDR_WIDTH)
TCK_PERIOD_PS ?= 20000
"""

TESTLIST_TEXT = f"""\
[[tests]]
name = "{BRINGUP}"
reseed = 5

[[tests]]
name = "t_single"
reseed = 20

[[tests]]
name = "t_targeted"
target = "small"

[[groups]]
name = "smoke"
tests = ["{BRINGUP}", "t_single"]

[[groups]]
name = "solo"
tests = ["t_targeted"]
"""

CONFIGS_TEXT = """\
[[dimension]]
id = "CFG-AXI-DATA-WIDTH"
values = ["32", "64"]
default = "64"

[[dimension]]
id = "CFG-CLK-RATIO"
values = ["20:1", "20:19", "20:21", "1:2", "1:20"]
default = "20:1"
"""

JUNIT_PASS = """<testsuites name="results"><testsuite name="all" package="all">
<testcase name="{test}" classname="tests.x" time="0.1" sim_time_ns="1" ratio_time="1" />
</testsuite></testsuites>"""

JUNIT_FAIL = """<testsuites name="results"><testsuite name="all" package="all">
<testcase name="{test}" classname="tests.x" time="0.1" sim_time_ns="1" ratio_time="1">
<failure message="Test failed with RANDOM_SEED=7" /></testcase>
</testsuite></testsuites>"""


XFAIL_TESTLIST_TEXT = f"""\
[[tests]]
name = "{BRINGUP}"
reseed = 5

[[tests]]
name = "t_xfail"
reseed = 2
expect_fail = true
expect_fail_match = "CHK-X FAIL: pinned"
expect_fail_match_vcs = "mem_addr_i has to be stable"
expect_fail_allow = ["CHK-Y FAIL: consequence"]

[[tests]]
name = "t_params"
reseed = 3
build_check = "CHK-CFG-P"
build_cases = [
  {{ target = "big", param = "AXI_ADDR_WIDTH", values = [1, 65], message = "ADDR_WIDTH must be" }},
  {{ target = "small", legal = true }},
]
"""

SIM_MESSAGE_TESTLIST_TEXT = XFAIL_TESTLIST_TEXT.replace(
    'message = "ADDR_WIDTH must be"',
    'message = "ADDR_WIDTH must be", message_vcs = "vcs words for ADDR_WIDTH"',
)

RECIPE_TESTLIST_TEXT = f"""\
[[tests]]
name = "{BRINGUP}"
reseed = 5

[[tests]]
name = "t_single"
reseed = 20

[[tests]]
name = "t_recipe"
reseed = 5
target = ["big", "small"]
ratio = "all"

[[tests]]
name = "t_pair"
reseed = 2
ratio = ["1:2", "1:20"]

[[tests]]
name = "t_narrow"
reseed = 2
bringup_targets = "tiny"

[[tests]]
name = "t_wide"
reseed = 2
target = ["big", "tiny"]
"""

PIN_LINE = "  1.00ns ERROR    cocotb.tdr2axi.checker   CHK-X FAIL: pinned signature\n"


def fixture_bench(makefile: str = MAKEFILE_TEXT, testlist: str = TESTLIST_TEXT) -> run_sim.Bench:
    make = run_sim.parse_makefile(makefile)
    ratios, default = run_sim.parse_ratios(tomllib.loads(CONFIGS_TEXT), make.tck_period_ps)
    return run_sim.Bench(make, run_sim.parse_testlist(tomllib.loads(testlist)), ratios, default)


def write_junit(path: Path, template: str, test: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.format(test=test))


def clocks_line(clk_ps: int, tck_ps: int, phase_ps: int = 137) -> str:
    """A cocotb INFO record carrying the bench's TDR2AXI_CLOCKS line."""
    return (
        f"     0.00ns INFO     cocotb.tdr2axi                     "
        f"TDR2AXI_CLOCKS clk_ps={clk_ps} tck_ps={tck_ps} phase_ps={phase_ps}\n"
    )


class TestParseTestlist(unittest.TestCase):
    def test_reads_tests_reseed_targets_and_groups(self) -> None:
        testlist = run_sim.parse_testlist(tomllib.loads(TESTLIST_TEXT))
        self.assertEqual([t.name for t in testlist.tests], [BRINGUP, "t_single", "t_targeted"])
        self.assertEqual([t.reseed for t in testlist.tests], [5, 20, 1])
        self.assertEqual(testlist.tests[2].targets, ("small",))
        self.assertEqual(testlist.groups[0], run_sim.Group("smoke", (BRINGUP, "t_single")))

    def test_rejects_bad_records(self) -> None:
        bad = {
            "reseed zero": '[[tests]]\nname = "a"\nreseed = 0\n',
            "repeated name": '[[tests]]\nname = "a"\n[[tests]]\nname = "a"\n',
            "unknown member": '[[tests]]\nname = "a"\n[[groups]]\nname = "g"\ntests = ["b"]\n',
            "no tests": '[[groups]]\nname = "g"\ntests = []\n',
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(run_sim.ConfigError):
                run_sim.parse_testlist(tomllib.loads(text))

    def test_reads_expected_failure_pins_per_simulator(self) -> None:
        tests = run_sim.parse_testlist(tomllib.loads(XFAIL_TESTLIST_TEXT)).tests
        xfail = tests[1]
        self.assertTrue(xfail.expected_fail)
        self.assertEqual(xfail.pin("verilator"), "CHK-X FAIL: pinned")
        self.assertEqual(xfail.pin("vcs"), "mem_addr_i has to be stable")
        self.assertIsNone(tests[0].pin("verilator"))
        only_default = run_sim.parse_testlist(
            tomllib.loads('[[tests]]\nname = "a"\nexpect_fail = true\nexpect_fail_match = "x"\n')
        ).tests[0]
        self.assertEqual(only_default.pin("vcs"), "x")
        self.assertEqual((xfail.allow, only_default.allow), (("CHK-Y FAIL: consequence",), ()))
        one = run_sim.parse_testlist(
            tomllib.loads(
                '[[tests]]\nname = "a"\nexpect_fail = true\nexpect_fail_match = "x"\n'
                'expect_fail_allow = "y"\n'
            )
        ).tests[0]
        self.assertEqual(one.allow, ("y",))

    def test_rejects_unpinned_or_unexpected_pins(self) -> None:
        bad = {
            "no pin": "expect_fail = true\n",
            "pin alone": 'expect_fail_match = "x"\n',
            "simulator pin alone": 'expect_fail = true\nexpect_fail_match_vcs = "x"\n',
            "bad pattern": 'expect_fail = true\nexpect_fail_match = "("\n',
            "unknown simulator": (
                'expect_fail = true\nexpect_fail_match = "x"\nexpect_fail_match_xsim = "y"\n'
            ),
            "not a bool": 'expect_fail = "yes"\nexpect_fail_match = "x"\n',
            "allow alone": 'expect_fail_allow = ["x"]\n',
            "bad allow pattern": 'expect_fail = true\nexpect_fail_match = "x"\nexpect_fail_allow = ["("]\n',
            "allow not a list": 'expect_fail = true\nexpect_fail_match = "x"\nexpect_fail_allow = 3\n',
        }
        for label, fields in bad.items():
            with self.subTest(label), self.assertRaises(run_sim.ConfigError):
                run_sim.parse_testlist(tomllib.loads(f'[[tests]]\nname = "a"\n{fields}'))

    def test_reads_build_cases(self) -> None:
        params = run_sim.parse_testlist(tomllib.loads(XFAIL_TESTLIST_TEXT)).tests[2]
        self.assertEqual(params.build_check, "CHK-CFG-P")
        self.assertEqual(
            params.build_cases,
            (
                run_sim.BuildCase("big", "AXI_ADDR_WIDTH", ("1", "65"), "ADDR_WIDTH must be"),
                run_sim.BuildCase("small"),
            ),
        )

    def test_reads_a_build_message_per_simulator(self) -> None:
        params = run_sim.parse_testlist(tomllib.loads(SIM_MESSAGE_TESTLIST_TEXT)).tests[2]
        illegal, legal = params.build_cases
        self.assertEqual(illegal.sim_messages, (("vcs", "vcs words for ADDR_WIDTH"),))
        self.assertEqual(illegal.message_on("vcs"), "vcs words for ADDR_WIDTH")
        self.assertEqual(illegal.message_on("verilator"), "ADDR_WIDTH must be")
        self.assertIsNone(legal.message_on("vcs"))
        plain = run_sim.parse_testlist(tomllib.loads(XFAIL_TESTLIST_TEXT)).tests[2]
        self.assertEqual(plain.build_cases[0].message_on("vcs"), "ADDR_WIDTH must be")

    def test_rejects_bad_build_cases(self) -> None:
        bad = {
            "no check": 'build_cases = [{ target = "big", legal = true }]\n',
            "message and legal": (
                'build_check = "C"\nbuild_cases = [{ target = "big", legal = true, message = "m" }]\n'
            ),
            "neither": 'build_check = "C"\nbuild_cases = [{ target = "big" }]\n',
            "param without values": (
                'build_check = "C"\nbuild_cases = [{ target = "big", param = "P", legal = true }]\n'
            ),
            "no target": 'build_check = "C"\nbuild_cases = [{ legal = true }]\n',
            "simulator message on a legal case": (
                'build_check = "C"\n'
                'build_cases = [{ target = "big", legal = true, message_vcs = "m" }]\n'
            ),
            "simulator message alone": (
                'build_check = "C"\nbuild_cases = [{ target = "big", message_vcs = "m" }]\n'
            ),
            "unknown simulator": (
                'build_check = "C"\n'
                'build_cases = [{ target = "big", message = "m", message_othersim = "m" }]\n'
            ),
            "bad simulator pattern": (
                'build_check = "C"\n'
                'build_cases = [{ target = "big", message = "m", message_vcs = "(" }]\n'
            ),
        }
        for label, fields in bad.items():
            with self.subTest(label), self.assertRaises(run_sim.ConfigError):
                run_sim.parse_testlist(tomllib.loads(f'[[tests]]\nname = "a"\n{fields}'))

    def test_bench_testlist_loads(self) -> None:
        bench = run_sim.load_bench()
        self.assertIn("smoke", [group.name for group in bench.testlist.groups])
        self.assertIn(BRINGUP, [test.name for test in bench.testlist.tests])


class TestParseMakefile(unittest.TestCase):
    def test_reads_builds_parameters_and_defaults(self) -> None:
        make = run_sim.parse_makefile(MAKEFILE_TEXT)
        self.assertEqual(make.builds, ("big", "small", "tiny"))
        self.assertEqual(make.reference, "big")
        self.assertEqual(make.bringup_only, frozenset({"tiny"}))
        self.assertEqual(make.params, ("USE_AXI_LITE", "AXI_ADDR_WIDTH", "AXI_DATA_WIDTH"))
        self.assertEqual(make.tck_period_ps, 20000)
        self.assertEqual(make.rows["small"], ("1", "32", "32"))
        self.assertEqual(
            make.param_values("big", [("AXI_ADDR_WIDTH", "2")]),
            (("USE_AXI_LITE", "0"), ("AXI_ADDR_WIDTH", "2"), ("AXI_DATA_WIDTH", "64")),
        )

    def test_rejects_inconsistent_makefiles(self) -> None:
        bad = {
            "short build": MAKEFILE_TEXT.replace("build_small := 1 32 32", "build_small := 1 32"),
            "reference outside builds": MAKEFILE_TEXT.replace("CONFIG ?= big", "CONFIG ?= huge"),
            "no TCK default": MAKEFILE_TEXT.replace("TCK_PERIOD_PS ?= 20000\n", ""),
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(run_sim.ConfigError):
                run_sim.parse_makefile(text)


class TestRatios(unittest.TestCase):
    def test_periods_follow_the_ratio(self) -> None:
        expected = {"20:1": 1000, "20:19": 19000, "20:21": 21000, "1:2": 40000, "1:20": 400000}
        for name, period in expected.items():
            with self.subTest(name):
                self.assertEqual(run_sim.ratio_period_ps(name, 20000), period)

    def test_rejects_periods_that_are_not_even_ps(self) -> None:
        for name in ("3:1", "32:1", "0:1", "fast"):
            with self.subTest(name), self.assertRaises(run_sim.ConfigError):
                run_sim.ratio_period_ps(name, 20000)

    def test_dimension_values_and_default(self) -> None:
        ratios, default = run_sim.parse_ratios(tomllib.loads(CONFIGS_TEXT), 20000)
        self.assertEqual([r.name for r in ratios], ["20:1", "20:19", "20:21", "1:2", "1:20"])
        self.assertEqual(default, run_sim.Ratio("20:1", 1000, 20000))
        with self.assertRaises(run_sim.ConfigError):
            run_sim.parse_ratios({"dimension": []}, 20000)


class TestSelection(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = fixture_bench()

    def test_tests_by_name_and_group_in_testlist_order(self) -> None:
        chosen = run_sim.select_tests(self.bench.testlist, names=["t_targeted"], groups=["smoke"])
        self.assertEqual([t.name for t in chosen], [BRINGUP, "t_single", "t_targeted"])
        everything = run_sim.select_tests(self.bench.testlist, names=[], groups=[])
        self.assertEqual(everything, self.bench.testlist.tests)
        with self.assertRaises(run_sim.ConfigError):
            run_sim.select_tests(self.bench.testlist, names=[], groups=["nightly"])
        with self.assertRaises(run_sim.ConfigError):
            run_sim.select_tests(self.bench.testlist, names=["t_missing"], groups=[])

    def test_builds_default_all_and_unknown(self) -> None:
        make = self.bench.make
        self.assertEqual(run_sim.select_builds(make, []), ("big",))
        self.assertEqual(run_sim.select_builds(make, ["small", "all"]), ("small", "big", "tiny"))
        with self.assertRaises(run_sim.ConfigError):
            run_sim.select_builds(make, ["huge"])

    def test_ratios_named_and_all(self) -> None:
        self.assertEqual(run_sim.select_ratios(self.bench, []), ())
        self.assertEqual(len(run_sim.select_ratios(self.bench, ["all", "1:2"])), 6)
        self.assertEqual(
            run_sim.select_ratios(self.bench, ["random"]), (run_sim.Ratio("random", None, 20000),)
        )
        with self.assertRaises(run_sim.ConfigError):
            run_sim.select_ratios(self.bench, ["3:1"])

    def test_expect_fail_spec(self) -> None:
        check = run_sim.parse_expect_fail("big:AXI_ADDR_WIDTH=2,AXI_DATA_WIDTH=64", self.bench.make)
        self.assertEqual(check.overrides, (("AXI_ADDR_WIDTH", "2"), ("AXI_DATA_WIDTH", "64")))
        self.assertEqual(run_sim.parse_expect_fail("tiny", self.bench.make).overrides, ())
        for text in ("huge", "big:SYNC=4", "big:AXI_ADDR_WIDTH=", "big:AXI_ADDR_WIDTH=2;rm"):
            with self.subTest(text), self.assertRaises(run_sim.ConfigError):
                run_sim.parse_expect_fail(text, self.bench.make)


class TestPlanning(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = fixture_bench()
        self.rng = random.Random(1)

    def plan(self, **fields: Any) -> list[run_sim.RunSpec]:
        selection = run_sim.Selection(
            tests=self.bench.testlist.tests, builds=self.bench.make.builds, **fields
        )
        return run_sim.plan_runs(self.bench, selection, stamp="20260101-000000", rng=self.rng)

    def test_bringup_only_and_target_rules(self) -> None:
        pairs = {(run.test, run.build) for run in self.plan()}
        self.assertEqual(
            pairs,
            {(BRINGUP, "big"), ("t_single", "big"), (BRINGUP, "small"), ("t_single", "small")}
            | {("t_targeted", "small"), (BRINGUP, "tiny")},
        )
        selection = run_sim.Selection(self.bench.testlist.tests, self.bench.make.builds)
        self.assertEqual(
            run_sim.skipped_tests(self.bench, selection),
            {"big": ["t_targeted"], "tiny": ["t_single", "t_targeted"]},
        )

    def test_reseed_rotates_every_test_through_every_ratio_and_random(self) -> None:
        runs = self.plan(reseed=True)
        slots = {ratio.name for ratio in self.bench.ratios} | {"random"}
        for test in (BRINGUP, "t_single"):
            for build in ("big", "small"):
                ratios = [r.ratio.name for r in runs if (r.test, r.build) == (test, build)]
                with self.subTest(test=test, build=build):
                    self.assertEqual(len(ratios), 6 if test == BRINGUP else 20)
                    self.assertEqual(set(ratios), slots)

    def test_default_ratio_without_reseed(self) -> None:
        self.assertEqual({run.ratio.name for run in self.plan(seeds=3)}, {"20:1"})

    def test_explicit_ratios_cross_the_seed_count(self) -> None:
        ratios = run_sim.select_ratios(self.bench, ["1:2", "1:20"])
        runs = [r for r in self.plan(seeds=2, ratios=ratios) if r.build == "big"]
        self.assertEqual(len(runs), 2 * 2 * 2)
        self.assertEqual({r.ratio.clk_period_ps for r in runs}, {40000, 400000})

    def test_seeds_are_distinct_and_named_in_the_run_directory(self) -> None:
        runs = [r for r in self.plan(seeds=4) if (r.test, r.build) == ("t_single", "big")]
        self.assertEqual(len({r.seed for r in runs}), 4)
        for run in runs:
            self.assertEqual(run.run_dir.name, f"t_single.big.{run.seed}.20260101-000000")
            self.assertEqual(run.build_dir, run_sim.BUILD_ROOT / "verilator_big")

    def test_fixed_seed_at_two_ratios_tags_the_directories(self) -> None:
        ratios = run_sim.select_ratios(self.bench, ["20:1", "1:20"])
        runs = [
            r for r in self.plan(seed=7, ratios=ratios) if (r.test, r.build) == (BRINGUP, "big")
        ]
        self.assertEqual(
            sorted(r.run_dir.name for r in runs),
            [f"{BRINGUP}.big.7.20260101-000000.r1-20", f"{BRINGUP}.big.7.20260101-000000.r20-1"],
        )

    def test_waves_and_coverage_get_their_own_build_directory(self) -> None:
        runs = self.plan(wave=True, sim="vcs")
        self.assertEqual(
            {r.build_dir.name for r in runs}, {"vcs_big_waves", "vcs_small_waves", "vcs_tiny_waves"}
        )
        self.assertIn("verilator_big_cov", {r.build_dir.name for r in self.plan(cov=True)})


class TestCommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = fixture_bench()

    def plan(self, argv: list[str]) -> tuple[list[run_sim.RunSpec], run_sim.Selection]:
        args = run_sim.build_parser().parse_args(argv)
        runs, _, selection = run_sim.plan_from_args(args, self.bench, stamp="20260101-000000")
        return runs, selection

    def test_target_and_build_name_the_same_option(self) -> None:
        args = run_sim.build_parser().parse_args(["--build", "small", "--target", "big"])
        self.assertEqual(args.target, ["small", "big"])

    def test_signoff_reseeds_every_test_on_every_target(self) -> None:
        runs, selection = self.plan(["--group", "signoff"])
        self.assertTrue(selection.reseed)
        self.assertEqual(selection.builds, ("big", "small", "tiny"))
        self.assertEqual(len(runs), 6 * 3 + 20 * 2 + 6)
        for build in ("big", "small", "tiny"):
            ratios = {r.ratio.name for r in runs if (r.test, r.build) == (BRINGUP, build)}
            self.assertEqual(len(ratios), 6)

    def test_signoff_yields_to_a_given_seed_and_target(self) -> None:
        runs, _ = self.plan(["--group", "signoff", "--seed", "3", "--target", "big"])
        self.assertEqual(
            sorted((r.test, r.build, r.seed) for r in runs),
            sorted([(BRINGUP, "big", 3), ("t_single", "big", 3), ("t_targeted", "big", 3)]),
        )

    def test_a_named_target_replaces_an_entry_target(self) -> None:
        def builds(argv: list[str]) -> list[str]:
            return sorted(run.build for run in self.plan(["--test", "t_targeted", *argv])[0])

        self.assertEqual(builds(["--seed", "1", "--target", "big"]), ["big"])
        self.assertEqual(builds(["--seed", "1", "--target", "all"]), ["big", "small"])
        self.assertEqual(builds(["--seed", "1"]), ["small"])
        self.assertEqual(set(builds(["--reseed"])), {"small"})
        runs, _ = self.plan(["--group", "signoff"])
        self.assertEqual({run.build for run in runs if run.test == "t_targeted"}, {"small"})

    def test_a_target_narrows_the_build_checks_to_its_cases(self) -> None:
        bench = fixture_bench(testlist=XFAIL_TESTLIST_TEXT)

        def checks(argv: list[str]) -> list[run_sim.RunSpec]:
            args = run_sim.build_parser().parse_args(["--test", "t_params", "--seed", "9", *argv])
            return run_sim.plan_from_args(args, bench, stamp="20260101-000000")[1]

        full, narrow = checks([]), checks(["--target", "big"])
        self.assertEqual([check.build for check in full], ["big", "small"])
        self.assertEqual([check.build for check in narrow], ["big"])
        self.assertEqual(narrow[0].overrides, full[0].overrides)

    def test_a_build_check_expects_the_selected_simulators_message(self) -> None:
        bench = fixture_bench(testlist=SIM_MESSAGE_TESTLIST_TEXT)

        def messages(argv: list[str]) -> list[str | None]:
            args = run_sim.build_parser().parse_args(["--test", "t_params", "--seed", "9", *argv])
            checks = run_sim.plan_from_args(args, bench, stamp="20260101-000000")[1]
            return [check.expect_message for check in checks]

        self.assertEqual(messages([]), ["ADDR_WIDTH must be", None])
        self.assertEqual(messages(["--sim", "vcs"]), ["vcs words for ADDR_WIDTH", None])

    def test_each_expect_message_belongs_to_the_check_before_it(self) -> None:
        args = run_sim.build_parser().parse_args(
            ["--expect-build-fail", "big:AXI_ADDR_WIDTH=2", "--expect-message", "WIDTH must"]
            + ["--expect-build-fail", "small"]
        )
        self.assertEqual(
            args.expect_build_fail, [("big:AXI_ADDR_WIDTH=2", "WIDTH must"), ("small", None)]
        )
        _, checks, _ = run_sim.plan_from_args(args, self.bench, stamp="20260101-000000")
        self.assertEqual(
            [c.expect_message for c in checks], ["WIDTH must", run_sim.DEFAULT_EXPECT_MESSAGE]
        )
        self.assertEqual(dict(checks[0].params)["AXI_ADDR_WIDTH"], "2")
        self.assertEqual([c.run_dir.name.split(".")[2] for c in checks], ["1", "2"])
        for argv in (
            ["--expect-message", "m", "--expect-build-fail", "big"],
            ["--expect-build-fail", "big", "--expect-message", "a", "--expect-message", "b"],
        ):
            with (
                self.subTest(argv),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                run_sim.build_parser().parse_args(argv)
            self.assertEqual(caught.exception.code, 2)


class TestTestlistRecipe(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = fixture_bench(testlist=RECIPE_TESTLIST_TEXT)

    def plan(self, argv: list[str]) -> list[run_sim.RunSpec]:
        args = run_sim.build_parser().parse_args(argv)
        runs, _, _ = run_sim.plan_from_args(args, self.bench, stamp="20260101-000000")
        return runs

    def per_build_ratio(self, runs: list[run_sim.RunSpec], test: str) -> Counter[tuple[str, str]]:
        return Counter((run.build, run.ratio.name) for run in runs if run.test == test)

    def test_reads_the_targets_and_ratios_of_an_entry(self) -> None:
        tests = self.bench.testlist.tests
        self.assertEqual((tests[2].targets, tests[2].ratios), (("big", "small"), ("all",)))
        self.assertEqual(tests[3].ratios, ("1:2", "1:20"))
        with self.assertRaises(run_sim.ConfigError):
            run_sim.parse_testlist(tomllib.loads('[[tests]]\nname = "a"\nratio = 3\n'))

    def test_reseed_runs_the_recipe_on_its_builds_at_every_ratio(self) -> None:
        slots = [ratio.name for ratio in self.bench.slots]
        expected = Counter({(build, slot): 5 for build in ("big", "small") for slot in slots})
        for argv in (["--test", "t_recipe", "--reseed"], ["--group", "signoff"]):
            with self.subTest(argv):
                self.assertEqual(self.per_build_ratio(self.plan(argv), "t_recipe"), expected)

    def test_the_command_line_narrows_the_recipe(self) -> None:
        runs = self.plan(["--test", "t_recipe", "--reseed", "--target", "big", "--target", "tiny"])
        self.assertEqual({run.build for run in runs}, {"big"})
        runs = self.plan(["--test", "t_recipe", "--seeds", "1", "--ratio", "1:2"])
        self.assertEqual(
            self.per_build_ratio(runs, "t_recipe"),
            Counter({("big", "1:2"): 1, ("small", "1:2"): 1}),
        )

    def test_tests_without_a_recipe_keep_their_selection(self) -> None:
        runs = self.plan(["--test", "t_single", "--reseed"])
        self.assertEqual({run.build for run in runs}, {"big"})
        self.assertEqual(len(runs), 20)
        self.assertEqual(len({run.ratio.name for run in runs}), 6)
        runs = self.plan(["--test", "t_pair", "--seeds", "3"])
        self.assertEqual(
            self.per_build_ratio(runs, "t_pair"), Counter({("big", "1:2"): 3, ("big", "1:20"): 3})
        )

    def test_bringup_targets_admit_a_test_to_those_builds_only(self) -> None:
        argv = ["--test", "t_narrow", "--test", "t_single", "--seed", "1"]
        runs = self.plan([*argv, "--target", "all"])
        self.assertEqual(
            sorted((run.test, run.build) for run in runs),
            [("t_narrow", "big"), ("t_narrow", "small"), ("t_narrow", "tiny")]
            + [("t_single", "big"), ("t_single", "small")],
        )
        runs = self.plan(["--test", "t_narrow", "--seed", "1"])
        self.assertEqual([run.build for run in runs], ["big"])
        self.assertEqual(self.bench.testlist.tests[4].bringup_targets, ("tiny",))
        listed = run_sim.parse_testlist(
            tomllib.loads('[[tests]]\nname = "a"\nbringup_targets = ["big"]\n')
        )
        self.assertEqual(
            run_sim.testlist_problems(listed, self.bench.make),
            ["a: bringup_targets names big, not a BRINGUP_ONLY build"],
        )

    def test_a_target_may_name_a_bringup_only_build(self) -> None:
        runs = self.plan(["--test", "t_wide", "--seed", "1"])
        self.assertEqual(sorted(run.build for run in runs), ["big", "tiny"])
        runs = self.plan(
            ["--test", "t_wide", "--test", "t_single", "--seed", "1", "--target", "all"]
        )
        self.assertEqual(
            sorted((run.test, run.build) for run in runs),
            [("t_single", "big"), ("t_single", "small")]
            + [("t_wide", "big"), ("t_wide", "small"), ("t_wide", "tiny")],
        )
        runs = self.plan(["--test", "t_recipe", "--seed", "1", "--target", "all"])
        self.assertEqual(sorted({run.build for run in runs}), ["big", "small"])
        listed = run_sim.parse_testlist(tomllib.loads('[[tests]]\nname = "a"\ntarget = "huge"\n'))
        self.assertEqual(
            run_sim.testlist_problems(listed, self.bench.make),
            ["a: target names huge, which the Makefile does not build"],
        )


class TestRevision(unittest.TestCase):
    def test_label_is_the_commit_and_a_hash_of_any_diff(self) -> None:
        commit = "0123456789abcdef0123"
        self.assertEqual(run_sim.revision_label(commit, b""), "0123456789ab")
        label = run_sim.revision_label(commit, b"diff --git a/x b/x\n")
        self.assertRegex(label, r"^0123456789ab\+[0-9a-f]{8}$")
        self.assertNotEqual(label, run_sim.revision_label(commit, b"diff --git a/y b/y\n"))
        self.assertEqual(run_sim.revision_label("", b"x"), "unknown")

    def test_a_directory_outside_git_has_no_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_sim.source_revision(Path(tmp)), "unknown")


class TestCommands(unittest.TestCase):
    def test_run_names_the_test_seed_clocks_results_and_coverage(self) -> None:
        bench = fixture_bench()
        spec = run_sim.RunSpec(
            "t_single", "big", 42, bench.ratios[3], Path("/r/x"), Path("sim/build/v_big")
        )
        sim = run_sim.SimTool("verilator", "Verilator 5", 1)
        variables = run_sim.run_variables(spec, sim, wave=True)
        for expected in (
            "CONFIG=big",
            "SIM_BUILD=sim/build/v_big",
            "TESTCASE=t_single",
            "RANDOM_SEED=42",
            "CLK_PERIOD_PS=40000",
            "TCK_PERIOD_PS=20000",
            "COCOTB_RESULTS_FILE=/r/x/results.xml",
            "VERILATOR_COVERAGE_FILE=/r/x/coverage.dat",
            "WAVES=1",
            "SIM_ARGS=--trace --trace-file /r/x/waves.fst",
        ):
            self.assertIn(expected, variables)
        drawn = replace(spec, ratio=bench.random_ratio)
        self.assertIn("CLK_PERIOD_PS=random", run_sim.run_variables(drawn, sim, wave=False))
        cocotb2 = run_sim.run_variables(spec, run_sim.SimTool("verilator", "v", 2), wave=False)
        self.assertIn("COCOTB_TEST_FILTER=^t_single$", cocotb2)
        self.assertIn("COCOTB_RANDOM_SEED=42", cocotb2)

    def test_coverage_options_go_through_the_environment(self) -> None:
        cm = f"-cm {run_sim.VCS_COVERAGE}"
        job = run_sim.BuildJob("big", Path("sim/build/vcs_big_cov"), cov=True)
        self.assertEqual(run_sim.build_environment(job, "verilator"), ["COMPILE_ARGS=--coverage"])
        self.assertEqual(run_sim.build_environment(job, "vcs"), [f"COMPILE_ARGS={cm}"])
        plain = run_sim.BuildJob("big", Path("sim/build/vcs_big"))
        self.assertEqual(run_sim.build_environment(plain, "vcs"), [])
        ratio = fixture_bench().default_ratio
        spec = run_sim.RunSpec("t_single", "big", 1, ratio, Path("/r/x"), job.build_dir)
        self.assertEqual(
            run_sim.run_environment(spec, "vcs", cov=True),
            [f"SIM_ARGS={cm} -cm_dir /r/x/coverage.vdb"],
        )
        self.assertEqual(run_sim.run_environment(spec, "verilator", cov=True), [])
        self.assertEqual(
            run_sim.make_command(["A=1"], "t", env=["X=1"])[:3], ["env", "X=1", "make"]
        )
        self.assertIsNone(run_sim.coverage_path(Path("/r/x"), "vcs", cov=False))

    def test_a_build_check_compiles_with_the_simulators_strict_options(self) -> None:
        cm = f"-cm {run_sim.VCS_COVERAGE}"
        check = run_sim.BuildJob("big", Path("sim/c1/build"), strict=True)
        self.assertEqual(run_sim.build_environment(check, "vcs"), ["COMPILE_ARGS=+error+100"])
        self.assertEqual(
            run_sim.build_environment(replace(check, cov=True), "vcs"),
            [f"COMPILE_ARGS=+error+100 {cm}"],
        )
        self.assertEqual(
            run_sim.build_environment(check, "verilator"),
            [f"COMPILE_ARGS={run_sim.VERILATOR_STRICT}"],
        )


class TestJunit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.results = Path(self.tmp.name) / "results.xml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_grades(self) -> None:
        cases = {
            "pass": (JUNIT_PASS.format(test="t"), "t", run_sim.PASS),
            "failure": (JUNIT_FAIL.format(test="t"), "t", run_sim.FAIL),
            "skipped": (
                JUNIT_PASS.format(test="t").replace("/>", "><skipped /></testcase>", 1),
                "t",
                run_sim.ERROR,
            ),
            "other test": (JUNIT_PASS.format(test="u"), "t", run_sim.ERROR),
            "truncated": ("<testsuites><testsuite>", "t", run_sim.ERROR),
        }
        for label, (text, test, status) in cases.items():
            with self.subTest(label):
                self.results.write_text(text)
                self.assertEqual(run_sim.junit_status(self.results, test)[0], status)
        self.results.unlink()
        self.assertEqual(
            run_sim.junit_status(self.results, "t"), (run_sim.ERROR, "no results file")
        )

    def test_exit_code_and_timeout(self) -> None:
        self.results.write_text(JUNIT_PASS.format(test="t"))
        self.assertEqual(
            run_sim.classify_run(0, self.results, test="t", timeout_s=9)[0], run_sim.PASS
        )
        self.assertEqual(
            run_sim.classify_run(2, self.results, test="t", timeout_s=9)[0], run_sim.ERROR
        )
        status, reason = run_sim.classify_run(None, self.results, test="t", timeout_s=9)
        self.assertEqual((status, reason), (run_sim.ERROR, "timed out after 9 s"))


class TestClocks(unittest.TestCase):
    RATIO = run_sim.Ratio("1:2", 40000, 20000)

    def test_parses_the_bench_line_from_a_cocotb_log(self) -> None:
        log = "cocotb start\n" + clocks_line(40000, 20000, 23117) + "     5.00ns INFO  done\n"
        self.assertEqual(run_sim.parse_clocks(log), [run_sim.Clocks(40000, 20000, 23117)])
        self.assertEqual(run_sim.parse_clocks("TDR2AXI_CLOCKS clk_ps=1 tck_ps=x\n"), [])

    def test_missing_or_other_periods_are_problems(self) -> None:
        self.assertIsNone(run_sim.clocks_problem([run_sim.Clocks(40000, 20000, 5)], self.RATIO))
        self.assertEqual(
            run_sim.clocks_problem([], self.RATIO), "no TDR2AXI_CLOCKS line in the log"
        )
        jittered = run_sim.clocks_problem([run_sim.Clocks(42120, 17032, 5)], self.RATIO)
        self.assertEqual(
            jittered, "ran clk_ps=42120 tck_ps=17032, asked clk_ps=40000 tck_ps=20000 for 1:2"
        )

    def test_random_needs_a_drawn_period_in_its_span(self) -> None:
        drawn = run_sim.Ratio("random", None, 20000)
        self.assertEqual(run_sim.random_period_span(20000), (1000, 160000))
        for clk_ps in (1000, 54322, 160000):
            with self.subTest(clk_ps):
                self.assertIsNone(run_sim.clocks_problem([run_sim.Clocks(clk_ps, 20000, 3)], drawn))
        for clk_ps in (998, 160002):
            with self.subTest(clk_ps):
                self.assertEqual(
                    run_sim.clocks_problem([run_sim.Clocks(clk_ps, 20000, 3)], drawn),
                    f"drew clk_ps={clk_ps}, outside 1000..160000 for random",
                )
        self.assertEqual(run_sim.clocks_problem([], drawn), "no TDR2AXI_CLOCKS line in the log")

    def test_a_clock_problem_turns_a_pass_or_a_fail_into_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.xml"
            for template in (JUNIT_PASS, JUNIT_FAIL):
                results.write_text(template.format(test="t"))
                with self.subTest(template[:60]):
                    status, reason = run_sim.classify_run(
                        0, results, test="t", timeout_s=9, clocks="no TDR2AXI_CLOCKS line"
                    )
                    self.assertEqual(status, run_sim.ERROR)
                    self.assertTrue(reason.startswith("no TDR2AXI_CLOCKS line"))


class TestBuildKey(unittest.TestCase):
    def test_a_parameter_value_changes_the_key(self) -> None:
        make = fixture_bench().make
        job = run_sim.BuildJob(
            "big", Path("sim/build/verilator_big"), params=make.param_values("big")
        )
        edited = run_sim.BuildJob(
            "big", job.build_dir, params=make.param_values("big", [("AXI_DATA_WIDTH", "32")])
        )
        self.assertEqual(run_sim.build_key(job, "verilator"), run_sim.build_key(job, "verilator"))
        self.assertNotEqual(
            run_sim.build_key(job, "verilator"), run_sim.build_key(edited, "verilator")
        )
        self.assertNotEqual(
            run_sim.build_key(job, "verilator"),
            run_sim.build_key(
                run_sim.BuildJob("big", job.build_dir, cov=True, params=job.params), "verilator"
            ),
        )


class TestModelIsStale(unittest.TestCase):
    def test_a_source_newer_than_the_model_or_gone_makes_it_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            source = build_dir / "dut.sv"
            source.write_text("module dut; endmodule\n")
            model = build_dir / "Vtop"
            model.write_text("")
            ver_files = build_dir / "Vtop__verFiles.dat"
            ver_files.write_text(f'S  10 1 100 0 100 0 "abc" "{source}"\n')
            os.utime(source, (100, 100))
            os.utime(model, (200, 200))
            self.assertFalse(run_sim.model_is_stale(build_dir))
            os.utime(source, (300, 300))
            self.assertTrue(run_sim.model_is_stale(build_dir))
            ver_files.write_text(f'S  10 1 100 0 100 0 "abc" "{build_dir / "gone.sv"}"\n')
            self.assertTrue(run_sim.model_is_stale(build_dir))
            model.unlink()
            self.assertFalse(run_sim.model_is_stale(build_dir))


# The VCS reports of the param-checks cases, as the farm logs print them.
VCS_WIDTH_FATALS = (
    "AXI_ADDR_WIDTH must be 2..64 for 32-bit data, 3..64 for 64-bit data.",
    "SbaAddrWidth must cover the BusWidth byte offset and be at most 64.",
    "SbaAddrWidth must be between 1 and 64.",
)
VCS_XMRE = """\
Error-[XMRE] Cross-module reference resolution error
<ws>/src/{source}, 71
  Error found while trying to resolve cross-module reference.
  token '{token}'.  Originating module '{module}'.
"""


class TestParamChecksOnVcs(unittest.TestCase):
    def setUp(self) -> None:
        tests = run_sim.load_bench().testlist.tests
        test = next(test for test in tests if test.name == "tdr2axi_param_checks_test")
        self.cases = {case.param: case for case in test.build_cases if case.param}
        self.width_cases = [case for case in test.build_cases if case.param == "AXI_ADDR_WIDTH"]

    def verdict(self, case: run_sim.BuildCase, build: str, probe: str | None) -> str:
        status: str = run_sim.expect_fail_verdict(
            run_sim.Stage(0 if probe is not None else 1, build),
            None if probe is None else run_sim.Stage(1, probe),
            probe_passed=False,
            pattern=case.message_on("vcs") or "",
        )[0]
        return status

    def test_an_address_width_case_accepts_whichever_width_check_stops_first(self) -> None:
        self.assertEqual(len(self.width_cases), 3)
        for case in self.width_cases:
            self.assertEqual(case.message_on("verilator"), case.message)
            for message in VCS_WIDTH_FATALS:
                fatal = f'Fatal: "<ws>/src/dm_sba.sv", 63: tb_top.u_dut: at time 0 ps\n{message}\n'
                with self.subTest(case=case.target, message=message):
                    self.assertEqual(self.verdict(case, "built\n", fatal), run_sim.PASS)
            other = 'Fatal: "<ws>/src/tdr2axi/tdr2axi.sv", 314: at time 0 ps\nother.\n'
            self.assertEqual(self.verdict(case, "built\n", other), run_sim.FAIL)

    def test_the_default_types_case_matches_tdr2axis_own_reference_errors(self) -> None:
        case = self.cases["AXI_DEFAULT_TYPES"]
        dependency = "".join(
            VCS_XMRE.format(source="axi_lite_to_axi.sv", token=token, module="axi_lite_to_axi")
            for token in ("aw_ready", "w_ready", "b", "b_valid", "ar_ready", "r", "r", "r_valid")
        )
        own = "".join(
            VCS_XMRE.format(source="tdr2axi/tdr2axi.sv", token=token, module="tdr2axi")
            for token in ("w", "r")
        )
        self.assertEqual(self.verdict(case, dependency + own, None), run_sim.PASS)
        self.assertEqual(self.verdict(case, dependency, None), run_sim.FAIL)
        self.assertIn("Member selection of non-struct/union", case.message_on("verilator") or "")

    def test_the_other_cases_keep_one_pattern_for_both_simulators(self) -> None:
        for param in ("AXI_DATA_WIDTH", "SYNC_STAGES", "AXI_REQ_DATA_WIDTH", "AXI_RSP_DATA_WIDTH"):
            with self.subTest(param):
                self.assertEqual(self.cases[param].sim_messages, ())


class TestExpectFailVerdict(unittest.TestCase):
    FATAL = (
        "[0] %Fatal: tdr2axi.sv:317: Assertion failed in tb_top.u_dut: AXI_ADDR_WIDTH must be 2..64"
    )

    def verdict(
        self, build: run_sim.Stage, probe: run_sim.Stage | None, *, passed: bool = False
    ) -> str:
        return run_sim.expect_fail_verdict(
            build, probe, probe_passed=passed, pattern="AXI_ADDR_WIDTH must be"
        )[0]

    def test_rejection_at_build_or_time_zero_passes(self) -> None:
        self.assertEqual(
            self.verdict(run_sim.Stage(1, f"%Error: x\n{self.FATAL}\n"), None), run_sim.PASS
        )
        self.assertEqual(
            self.verdict(run_sim.Stage(0, ""), run_sim.Stage(2, self.FATAL)), run_sim.PASS
        )

    def test_a_build_that_comes_up_or_fails_otherwise_fails(self) -> None:
        self.assertEqual(self.verdict(run_sim.Stage(2, "%Error: syntax"), None), run_sim.FAIL)
        self.assertEqual(
            self.verdict(run_sim.Stage(0, ""), run_sim.Stage(0, ""), passed=True), run_sim.FAIL
        )
        self.assertEqual(
            self.verdict(run_sim.Stage(0, ""), run_sim.Stage(2, "%Fatal: other")), run_sim.FAIL
        )

    def test_make_messages_and_paths_never_match(self) -> None:
        make_line = (
            "make[1]: *** [sim/expect_build_fail.big-AXI_ADDR_WIDTH-2.1.x/build/Vtop] Error 1"
        )
        status, _ = run_sim.expect_fail_verdict(
            run_sim.Stage(2, f"%Error: syntax\n{make_line}\n"),
            None,
            probe_passed=False,
            pattern="AXI_ADDR_WIDTH",
        )
        self.assertEqual(status, run_sim.FAIL)
        self.assertEqual(run_sim.tool_output(f"{make_line}\n{self.FATAL}"), self.FATAL)

    def test_timeouts_are_errors(self) -> None:
        self.assertEqual(self.verdict(run_sim.Stage(None, ""), None), run_sim.ERROR)
        self.assertEqual(self.verdict(run_sim.Stage(0, ""), run_sim.Stage(None, "")), run_sim.ERROR)


class TestExpectedFailVerdict(unittest.TestCase):
    PIN = r"CHK-X FAIL: pinned"

    def verdict(self, status: str, log: str) -> str:
        return run_sim.expected_fail_verdict(status, "failure: x", log, self.PIN)[0]

    def test_a_failure_on_the_pin_passes(self) -> None:
        self.assertEqual(self.verdict(run_sim.FAIL, f"sim\n{PIN_LINE}"), run_sim.PASS)

    def test_a_pass_or_another_failure_fails(self) -> None:
        self.assertEqual(self.verdict(run_sim.PASS, PIN_LINE), run_sim.FAIL)
        self.assertEqual(self.verdict(run_sim.FAIL, "CHK-Y FAIL: other\n"), run_sim.FAIL)

    def test_an_error_stays_an_error(self) -> None:
        self.assertEqual(self.verdict(run_sim.ERROR, PIN_LINE), run_sim.ERROR)

    def test_every_error_line_on_the_pin_is_an_expected_failure(self) -> None:
        log = (
            PIN_LINE
            + "  1.00ns INFO     cocotb.regression   t_xfail failed\n"
            + '        raise Tdr2axiCheckError(f"{check_id} FAIL: {detail}")\n'
            + "    tests.tdr2axi_checker.Tdr2axiCheckError: CHK-X FAIL: pinned signature\n"
            + "  1.00ns INFO     cocotb.regression   ****************\n"
            + "    ** TESTS=1 PASS=0 FAIL=1 SKIP=0 **\n"
        )
        self.assertEqual(self.verdict(run_sim.FAIL, log), run_sim.PASS)

    def test_another_check_failing_beside_the_pin_fails(self) -> None:
        other = (
            "  2.00ns ERROR    cocotb.tdr2axi.checker   CHK-AXISUB-ID-ORDER FAIL: B id 3 early\n"
        )
        status, reason = run_sim.expected_fail_verdict(
            run_sim.FAIL, "failure: x", PIN_LINE + other, self.PIN
        )
        self.assertEqual(status, run_sim.FAIL)
        self.assertIn("CHK-AXISUB-ID-ORDER FAIL: B id 3 early", reason)

    def test_an_unexpected_error_or_timeout_beside_the_pin_fails(self) -> None:
        for other in (
            "  2.00ns ERROR    ..sk 5.Predictor._watch Exception raised by this forked coroutine\n",
            "    env.sba_ref_model.SbaStimulusError: bus response with no bus access pending\n",
            "    cocotb.result.SimTimeoutError: Timed out after 5 ms\n",
            "[2000] %Error: x_sva.sv:9: Assertion failed in tb_top.u_sva.a_held: held\n",
        ):
            with self.subTest(other):
                self.assertEqual(self.verdict(run_sim.FAIL, PIN_LINE + other), run_sim.FAIL)

    def test_make_messages_never_match(self) -> None:
        self.assertEqual(
            self.verdict(run_sim.FAIL, "make[1]: *** [CHK-X FAIL: pinned] Error 1\n"), run_sim.FAIL
        )

    def test_an_allowed_line_passes_only_beside_the_pin(self) -> None:
        allowed = "  2.00ns ERROR    cocotb.tdr2axi.checker   CHK-Y FAIL: consequence\n"
        other = "  3.00ns ERROR    cocotb.tdr2axi.checker   CHK-Z FAIL: unrelated\n"

        def verdict(log: str) -> tuple[str, str]:
            return run_sim.expected_fail_verdict(
                run_sim.FAIL, "failure: x", log, self.PIN, ["CHK-Y FAIL: consequence"]
            )

        self.assertEqual(verdict(allowed + PIN_LINE)[0], run_sim.PASS)
        status, reason = verdict(allowed)
        self.assertEqual(status, run_sim.FAIL)
        self.assertIn("no line matching its pin", reason)
        self.assertEqual(verdict(PIN_LINE + allowed + other)[0], run_sim.FAIL)

    def test_the_exception_that_ended_the_test_must_be_expected(self) -> None:
        failed = "  9.00ns INFO     cocotb.regression   t_xfail failed\n"
        for ending in ("cocotb.result.TestFailure: unrelated", "StopIteration"):
            with self.subTest(ending):
                log = PIN_LINE + failed + "        Traceback (most recent call last):\n"
                log += f'          File "t.py", line 3, in t\n        {ending}\n'
                self.assertEqual(self.verdict(run_sim.FAIL, log), run_sim.FAIL)
        self.assertEqual(
            run_sim.ending_exceptions(
                [failed, "    Traceback (most recent call last):", "", "    KeyError: 3"]
            ),
            ["KeyError: 3"],
        )

    def test_a_failing_run_whose_make_exits_nonzero_is_an_error(self) -> None:
        status, _ = run_sim.expected_fail_verdict(
            run_sim.FAIL, "failure: x", PIN_LINE, self.PIN, rc=2
        )
        self.assertEqual(status, run_sim.ERROR)


# A VCS run that a dependency's $fatal stops, in the line shapes VCS and cocotb 1.9 print.
VCS_FATAL_LOG = """\
"/w/.bender/git/checkouts/axi/src/axi_lite_from_mem.sv", 242: tb_top.i_dut.i_tdr2axi.\
i_axi_lite_from_mem.unnamed$$_1: started at 17580000ps failed at 17600000ps
\tOffending '$stable(mem_addr_i)'
Fatal: "/w/.bender/git/checkouts/axi/src/axi_lite_from_mem.sv", 242: tb_top.i_dut.i_tdr2axi.\
i_axi_lite_from_mem.unnamed$$_1: at time 17600000 ps
mem_addr_i has to be stable if request is not granted!
 17600.00ns ERROR    cocotb.regression                  Failing test at simulator request \
before test run completion: Simulator shut down prematurely
 17600.00ns ERROR    cocotb.regression                  Test error has lead to simulator \
shutting us down
 17600.00ns INFO     cocotb.regression                  t_xfail failed
"""

# The bench's own SVA failing in the same timestep, as VCS reports a $error.
VCS_SVA_ERROR = """\
"/w/tb/tdr2axi/sva/tdr2axi_axi_mgr_sva.sv", 155: tb_top.u_axi_mgr_sva.a_ar_payload_stable: \
started at 17580000ps failed at 17600000ps
\tOffending '$stable(ar_payload)'
Error: "/w/tb/tdr2axi/sva/tdr2axi_axi_mgr_sva.sv", 155: tb_top.u_axi_mgr_sva.a_ar_payload_stable: \
at time 17600000 ps
CHK-AXI-HANDSHAKE-SVA FAIL: AR payload changed while ARVALID waited for ARREADY
"""


class TestVcsExpectedFailure(unittest.TestCase):
    PIN = r"(mem_addr_i|mem_wdata_i|mem_be_i) has to be stable if request is not granted"
    ALLOW = (r"CHK-AXI-HANDSHAKE-SVA FAIL: AR payload changed while",)
    PINNED_MESSAGE = "mem_addr_i has to be stable if request is not granted"

    def verdict(self, log: str, *, sim: str = "vcs", rc: int = 1) -> tuple[str, str]:
        return run_sim.expected_fail_verdict(
            run_sim.FAIL, "failure: x", log, self.PIN, self.ALLOW, sim=sim, rc=rc
        )

    def test_a_pinned_fatal_and_its_shutdown_records_pass(self) -> None:
        status, reason = self.verdict(VCS_FATAL_LOG)
        self.assertEqual(status, run_sim.PASS, reason)
        self.assertIn("Fatal:", reason)
        self.assertIn(self.PINNED_MESSAGE, reason)

    def test_a_message_on_the_fatal_line_passes(self) -> None:
        log = VCS_FATAL_LOG.replace(" ps\nmem_addr_i", " ps: mem_addr_i")
        self.assertEqual(self.verdict(log)[0], run_sim.PASS)

    def test_an_allowed_error_report_joins_its_message(self) -> None:
        self.assertEqual(self.verdict(VCS_SVA_ERROR + VCS_FATAL_LOG)[0], run_sim.PASS)
        other = VCS_SVA_ERROR.replace("CHK-AXI-HANDSHAKE-SVA FAIL: AR", "held: AR")
        status, reason = self.verdict(other + VCS_FATAL_LOG)
        self.assertEqual(status, run_sim.FAIL)
        self.assertIn("tdr2axi_axi_mgr_sva.sv", reason)

    def test_shutdown_records_after_another_fatal_fail(self) -> None:
        other = VCS_FATAL_LOG.replace(
            f"{self.PINNED_MESSAGE}!",
            "It is not allowed to deassert the request if it was not granted!",
        )
        log = f"{self.PINNED_MESSAGE}\n{other}"
        self.assertEqual(self.verdict(log)[0], run_sim.ERROR)
        status, reason = self.verdict(log, rc=0)
        self.assertEqual(status, run_sim.FAIL)
        self.assertIn("It is not allowed to deassert", reason)

    def test_shutdown_records_count_only_on_vcs(self) -> None:
        status, reason = self.verdict(VCS_FATAL_LOG, sim="verilator", rc=0)
        self.assertEqual(status, run_sim.FAIL)
        self.assertIn("Fatal:", reason)

    def test_a_nonzero_exit_without_a_pinned_fatal_is_an_error(self) -> None:
        log = f"{self.PINNED_MESSAGE}\n"
        self.assertEqual(self.verdict(log)[0], run_sim.ERROR)
        self.assertEqual(self.verdict(log, rc=0)[0], run_sim.PASS)

    def test_expect_pass_verdict(self) -> None:
        ok, built = run_sim.Stage(0, ""), run_sim.Stage(0, "")
        self.assertEqual(run_sim.expect_pass_verdict(ok, built, probe_passed=True)[0], run_sim.PASS)
        self.assertEqual(
            run_sim.expect_pass_verdict(ok, built, probe_passed=False)[0], run_sim.FAIL
        )
        self.assertEqual(
            run_sim.expect_pass_verdict(run_sim.Stage(2, ""), None, probe_passed=False)[0],
            run_sim.FAIL,
        )
        self.assertEqual(
            run_sim.expect_pass_verdict(run_sim.Stage(None, ""), None, probe_passed=False)[0],
            run_sim.ERROR,
        )


class FakeLauncher:
    """Stands in for make: a build returns ``build_rc``; a run writes a JUnit file and,
    unless ``covers`` is None, a Verilator coverage.dat with its points."""

    def __init__(
        self,
        *,
        build_rc: Callable[[Sequence[str]], int],
        run_passes: Callable[[Sequence[str]], bool],
        clocks: Callable[[int, int], str] = clocks_line,
        run_log: str = "",
        covers: Mapping[str, int] | None = fake_make.COVERS,
    ) -> None:
        self.timeout_s = 60.0
        self.build_rc = build_rc
        self.run_passes = run_passes
        self.clocks = clocks
        self.run_log = run_log
        self.covers = covers
        self.drawn_clk_ps = 54322
        self.commands: list[list[str]] = []

    def __call__(self, cmd: Sequence[str], log: Path) -> int | None:
        self.commands.append(list(cmd))
        log.parent.mkdir(parents=True, exist_ok=True)
        target = cmd[-1]
        if target.endswith("results.xml"):
            test = next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("TESTCASE="))
            template = JUNIT_PASS if self.run_passes(cmd) else JUNIT_FAIL
            write_junit(Path(target), template, test)
            knobs = dict(arg.split("=", 1) for arg in cmd if "=" in arg)
            for arg in cmd:
                if arg.startswith("VERILATOR_COVERAGE_FILE=") and self.covers is not None:
                    Path(arg.split("=", 1)[1]).write_text(fake_make.coverage_dat(self.covers))
            clk_knob, tck_ps = knobs["CLK_PERIOD_PS"], int(knobs["TCK_PERIOD_PS"])
            clk_ps = self.drawn_clk_ps if clk_knob == "random" else int(clk_knob)
            log.write_text("sim\n" + self.clocks(clk_ps, tck_ps) + self.run_log)
            return 0
        rc = self.build_rc(cmd)
        log.write_text("%Error: AXI_ADDR_WIDTH must be 2..64\n" if rc else "built\n")
        return rc

    def stop(self) -> None:
        pass


class TestMain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sim_dir = root / "sim"
        self.patches = mock.patch.multiple(
            run_sim,
            BENCH_DIR=root,
            SIM_DIR=self.sim_dir,
            load_bench=fixture_bench,
            missing_tools=lambda sim: [],
            probe_simulator=lambda sim: run_sim.SimTool(sim, "Verilator 5.054", 1),
            active_site=site_layer.Site,
        )
        self.patches.start()

    def tearDown(self) -> None:
        self.patches.stop()
        self.tmp.cleanup()

    def run_main(self, argv: list[str], fake: FakeLauncher) -> tuple[int, dict[str, object]]:
        manifest = self.sim_dir / "m.json"
        with (
            mock.patch.object(run_sim, "Launcher", lambda timeout: fake),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            rc = run_sim.main([*argv, "--manifest", str(manifest)])
        return rc, json.loads(manifest.read_text())

    def test_two_seeds_build_once_and_pass(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(["--test", "t_single", "--seeds", "2", "--jobs", "2"], fake)
        self.assertEqual(rc, 0)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(len({run["seed"] for run in runs}), 2)
        self.assertEqual({run["status"] for run in runs}, {"pass"})
        self.assertEqual(sum(1 for cmd in fake.commands if cmd[-1].endswith("/Vtop")), 1)
        self.assertTrue(fake.commands[0][-1].endswith("/Vtop"))
        self.assertEqual(
            set(runs[0]),
            {
                "test",
                "kind",
                "mutation",
                "targets",
                "revision",
                "realization",
                "attempt",
                "started",
                "run_kind",
                "check",
                "expected_fail",
                "expected_fail_match",
                "expected_fail_allow",
                "build",
                "overrides",
                "seed",
                "ratio",
                "clk_period_ps",
                "tck_period_ps",
                "clk_ps",
                "tck_ps",
                "phase_ps",
                "status",
                "reason",
                "duration_s",
                "run_dir",
                "log",
                "results",
                "coverage",
                "build_log",
                "command",
                "simulator",
                "simulator_version",
            },
        )
        self.assertEqual(manifest["summary"], {"pass": 2, "fail": 0, "error": 0})
        self.assertEqual(runs[0]["coverage"], f"{runs[0]['run_dir']}/coverage.dat")
        self.assertEqual(
            [runs[0][key] for key in ("ratio", "clk_period_ps", "clk_ps", "tck_ps", "phase_ps")],
            ["20:1", 1000, 1000, 20000, 137],
        )

    def test_a_missing_or_jittered_clock_line_is_an_error(self) -> None:
        cases: dict[str, Callable[[int, int], str]] = {
            "missing": lambda clk, tck: "",
            "jittered": lambda clk, tck: clocks_line(clk + 54, tck - 2968),
        }
        for label, clocks in cases.items():
            fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True, clocks=clocks)
            with self.subTest(label):
                rc, manifest = self.run_main(["--test", "t_single"], fake)
                self.assertEqual(rc, 1)
                self.assertEqual(manifest["summary"], {"pass": 0, "fail": 0, "error": 1})

    def test_a_random_run_records_its_draw_and_errors_outside_the_span(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(["--test", "t_single", "--ratio", "random"], fake)
        self.assertEqual(rc, 0)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        keys = ("ratio", "clk_period_ps", "tck_period_ps", "clk_ps", "tck_ps", "phase_ps")
        self.assertEqual([runs[0][key] for key in keys], ["random", None, 20000, 54322, 20000, 137])
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        fake.drawn_clk_ps = 200000
        rc, manifest = self.run_main(["--test", "t_single", "--ratio", "random"], fake)
        self.assertEqual((rc, manifest["summary"]), (1, {"pass": 0, "fail": 0, "error": 1}))

    def test_a_build_is_reused_until_its_parameters_change(self) -> None:
        def force_flags(fake: FakeLauncher) -> list[bool]:
            return ["-B" in cmd for cmd in fake.commands if cmd[-1].endswith("/Vtop")]

        fakes = [
            FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True) for _ in range(3)
        ]
        self.run_main(["--test", "t_single"], fakes[0])
        self.run_main(["--test", "t_single"], fakes[1])
        edited = MAKEFILE_TEXT.replace("build_big   := 0 64 64", "build_big   := 0 64 32")
        with mock.patch.object(run_sim, "load_bench", lambda: fixture_bench(edited)):
            self.run_main(["--test", "t_single"], fakes[2])
        self.assertEqual([force_flags(fake) for fake in fakes], [[True], [False], [True]])

    def test_cov_builds_its_own_model_with_coverage(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, _ = self.run_main(["--test", "t_single", "--cov"], fake)
        self.assertEqual(rc, 0)
        build, _ = fake.commands
        self.assertEqual(build[:3], ["env", "COMPILE_ARGS=--coverage", "make"])
        self.assertTrue(build[-1].endswith("verilator_big_cov/Vtop"))

    def test_vcs_cov_commands(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        self.run_main(["--test", "t_single", "--cov", "--sim", "vcs"], fake)
        build, run = fake.commands
        self.assertEqual(build[:2], ["env", f"COMPILE_ARGS=-cm {run_sim.VCS_COVERAGE}"])
        self.assertTrue(build[-1].endswith("vcs_big_cov/simv"))
        self.assertTrue(run[1].startswith(f"SIM_ARGS=-cm {run_sim.VCS_COVERAGE} -cm_dir "))
        self.assertTrue(run[1].endswith("/coverage.vdb"))
        self.assertIn("SIM=vcs", run)

    def test_a_failing_test_exits_nonzero(self) -> None:
        fake = FakeLauncher(
            build_rc=lambda cmd: 0, run_passes=lambda cmd: "RANDOM_SEED=7" not in cmd
        )
        rc, manifest = self.run_main(["--group", "smoke", "--seed", "7"], fake)
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["summary"], {"pass": 0, "fail": 2, "error": 0})

    def test_a_failed_build_errors_its_runs(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 2, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(["--group", "smoke"], fake)
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["summary"], {"pass": 0, "fail": 0, "error": 2})

    def test_expect_build_fail(self) -> None:
        rejecting = FakeLauncher(
            build_rc=lambda cmd: 2 if "AXI_ADDR_WIDTH=2" in cmd else 0, run_passes=lambda cmd: True
        )
        rc, manifest = self.run_main(
            ["--expect-build-fail", "big:AXI_ADDR_WIDTH=2", "--expect-message", "must be"],
            rejecting,
        )
        self.assertEqual(rc, 0)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(
            (len(runs), runs[0]["run_kind"], runs[0]["status"]), (1, "expect-build-fail", "pass")
        )
        self.assertEqual(runs[0]["overrides"], {"AXI_ADDR_WIDTH": "2"})
        self.assertTrue(all("-s" in cmd for cmd in rejecting.commands))
        accepting = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(
            ["--expect-build-fail", "big", "--expect-message", "must be"], accepting
        )
        self.assertEqual(rc, 1)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(runs[0]["status"], "fail")
        self.assertEqual(len(accepting.commands), 2)
        self.assertTrue(all("-s" in cmd for cmd in accepting.commands))

    def run_xfail_main(self, argv: list[str], fake: FakeLauncher) -> tuple[int, list[Any]]:
        with mock.patch.object(
            run_sim, "load_bench", lambda: fixture_bench(testlist=XFAIL_TESTLIST_TEXT)
        ):
            rc, manifest = self.run_main(argv, fake)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        return rc, runs

    def test_an_expected_failure_on_its_pin_is_an_xfail(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: False, run_log=PIN_LINE)
        rc, runs = self.run_xfail_main(["--test", "t_xfail", "--reseed"], fake)
        self.assertEqual(rc, 0)
        self.assertTrue(runs)
        self.assertEqual({run["status"] for run in runs}, {"pass"})
        self.assertEqual({run["expected_fail"] for run in runs}, {True})
        self.assertEqual({run["expected_fail_match"] for run in runs}, {"CHK-X FAIL: pinned"})
        self.assertEqual(runs[0]["expected_fail_allow"], ["CHK-Y FAIL: consequence"])
        self.assertIn("CHK-X FAIL: pinned signature", runs[0]["reason"])

    def test_an_expected_failure_that_passes_or_misses_its_pin_fails(self) -> None:
        passing = FakeLauncher(
            build_rc=lambda cmd: 0, run_passes=lambda cmd: True, run_log=PIN_LINE
        )
        rc, runs = self.run_xfail_main(["--test", "t_xfail", "--seed", "3"], passing)
        self.assertEqual((rc, runs[0]["status"], runs[0]["expected_fail"]), (1, "fail", False))
        self.assertEqual(runs[0]["expected_fail_match"], "CHK-X FAIL: pinned")
        other = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: False)
        rc, runs = self.run_xfail_main(["--test", "t_xfail", "--seed", "4"], other)
        self.assertEqual((rc, runs[0]["status"]), (1, "fail"))
        self.assertIn("no line matching its pin", runs[0]["reason"])

    def test_other_tests_are_not_expected_to_fail(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(["--test", "t_single"], fake)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(
            (rc, runs[0]["expected_fail"], runs[0]["expected_fail_match"]), (0, False, None)
        )

    def test_vcs_runs_carry_the_vcs_pin(self) -> None:
        bench = fixture_bench(testlist=XFAIL_TESTLIST_TEXT)
        selection = run_sim.Selection(
            tests=bench.testlist.tests[1:2], builds=("big",), sim="vcs", seed=1
        )
        runs = run_sim.plan_runs(bench, selection, stamp="s", rng=random.Random(1))
        self.assertEqual([run.expected_fail for run in runs], ["mem_addr_i has to be stable"])

    def test_a_build_check_test_runs_its_cases_per_seed(self) -> None:
        fake = FakeLauncher(
            build_rc=lambda cmd: 2 if any(a.startswith("AXI_ADDR_WIDTH=") for a in cmd) else 0,
            run_passes=lambda cmd: True,
        )
        rc, runs = self.run_xfail_main(["--test", "t_params", "--reseed"], fake)
        self.assertEqual(rc, 0)
        self.assertEqual(len(runs), 3 * 2)
        self.assertEqual({run["test"] for run in runs}, {"t_params"})
        self.assertEqual({run["check"] for run in runs}, {"CHK-CFG-P"})
        kinds = Counter(run["run_kind"] for run in runs)
        self.assertEqual(kinds, Counter({"expect-build-fail": 3, "expect-build-pass": 3}))
        for run in runs:
            self.assertEqual(run["status"], "pass")
            line = Path(run["log"]).read_text()
            self.assertTrue(line.startswith("CHK-CFG-P PASS: "), line)
        illegal = [run for run in runs if run["run_kind"] == "expect-build-fail"]
        self.assertTrue({run["overrides"]["AXI_ADDR_WIDTH"] for run in illegal} <= {"1", "65"})
        strict = [cmd for cmd in fake.commands if f"COMPILE_ARGS={run_sim.VERILATOR_STRICT}" in cmd]
        self.assertEqual(len(strict), 6)

    def check_kinds(
        self, argv: list[str], *, illegal_builds: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """The exit status, and t_params' checks by run kind with the build commands under
        "builds"; the illegal case's build fails unless ``illegal_builds``."""

        def build_rc(cmd: Sequence[str]) -> int:
            illegal = any(a.startswith("AXI_ADDR_WIDTH=") for a in cmd)
            return 2 if illegal and not illegal_builds else 0

        fake = FakeLauncher(build_rc=build_rc, run_passes=lambda cmd: True)
        rc, runs = self.run_xfail_main(["--test", "t_params", *argv], fake)
        builds = [cmd for cmd in fake.commands if not cmd[-1].endswith("results.xml")]
        return rc, {"builds": builds, **{run["run_kind"]: run for run in runs}}

    def test_a_legal_build_check_records_coverage_as_a_run_does(self) -> None:
        rc, covered = self.check_kinds(["--seed", "4", "--cov"])
        self.assertEqual(rc, 0)
        legal = covered["expect-build-pass"]
        self.assertEqual(legal["coverage"], f"{legal['run_dir']}/probe/coverage.dat")
        self.assertIsNone(covered["expect-build-fail"]["coverage"])
        both = f"COMPILE_ARGS={run_sim.VERILATOR_STRICT} --coverage"
        self.assertEqual(sum(both in cmd for cmd in covered["builds"]), 1)
        rc, plain = self.check_kinds(["--seed", "5"])
        self.assertEqual(rc, 0)
        legal = plain["expect-build-pass"]
        self.assertEqual(legal["coverage"], f"{legal['run_dir']}/probe/coverage.dat")
        self.assertFalse(any("--coverage" in " ".join(cmd) for cmd in plain["builds"]))

    def test_an_illegal_build_check_never_records_coverage(self) -> None:
        for seed, argv in (("6", ["--cov"]), ("7", [])):
            with self.subTest(argv=argv):
                rc, kinds = self.check_kinds(["--seed", seed, *argv], illegal_builds=True)
                self.assertEqual(rc, 1)
                illegal = kinds["expect-build-fail"]
                self.assertEqual(illegal["status"], "fail")
                self.assertTrue((Path(illegal["run_dir"]) / "probe" / "coverage.dat").is_file())
                self.assertIsNone(illegal["coverage"])

    def test_a_build_check_that_misses_logs_its_failure(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, runs = self.run_xfail_main(["--test", "t_params", "--seed", "4"], fake)
        self.assertEqual(rc, 1)
        illegal = next(run for run in runs if run["run_kind"] == "expect-build-fail")
        self.assertEqual(illegal["status"], "fail")
        self.assertTrue(Path(illegal["log"]).read_text().startswith("CHK-CFG-P FAIL: "))

    def test_runs_follow_the_trace_runs_contract(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest = self.run_main(["--test", "t_single", "--seeds", "2"], fake)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(rc, 0)
        for run in runs:
            self.assertEqual((run["kind"], run["mutation"], run["targets"]), ("normal", "", []))
            self.assertEqual(run["realization"], "cocotb")
            self.assertIsInstance(run["seed"], int)
            self.assertTrue(run["revision"])
            self.assertEqual(run["revision"], manifest["revision"])
            self.assertRegex(run["started"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")

    def test_a_mutation_arms_its_knob_and_records_its_targets(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: False)
        argv = ["--test", "t_single", "--seed", "5", "--mutation", "TDR2AXI_X_NEGATIVE=skip"]
        rc, manifest = self.run_main([*argv, "--targets", "CHK-A-B, CHK-C"], fake)
        runs = manifest["runs"]
        assert isinstance(runs, list)
        self.assertEqual(rc, 1)
        self.assertEqual(
            (runs[0]["kind"], runs[0]["mutation"], runs[0]["targets"], runs[0]["status"]),
            ("mutation", "TDR2AXI_X_NEGATIVE=skip", ["CHK-A-B", "CHK-C"], "fail"),
        )
        run = next(cmd for cmd in fake.commands if cmd[-1].endswith("results.xml"))
        self.assertEqual(run[:3], ["env", "TDR2AXI_X_NEGATIVE=skip", "make"])
        build = next(cmd for cmd in fake.commands if cmd[-1].endswith("/Vtop"))
        self.assertNotIn("TDR2AXI_X_NEGATIVE=skip", build)

    def test_a_mutation_of_an_expected_fail_test_is_never_an_xfail(self) -> None:
        fake = FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: False, run_log=PIN_LINE)
        argv = ["--test", "t_xfail", "--seed", "6", "--mutation", "rtl-mutant"]
        rc, runs = self.run_xfail_main([*argv, "--targets", "CHK-X"], fake)
        self.assertEqual((rc, runs[0]["status"], runs[0]["expected_fail"]), (1, "fail", False))
        self.assertEqual(
            (runs[0]["kind"], runs[0]["expected_fail_match"], runs[0]["expected_fail_allow"]),
            ("mutation", None, []),
        )
        run = next(cmd for cmd in fake.commands if cmd[-1].endswith("results.xml"))
        self.assertEqual(run[0], "make")

    def test_mutation_usage_errors_exit_2(self) -> None:
        for argv in (
            ["--test", "t_single", "--mutation", "X"],
            ["--test", "t_single", "--targets", "CHK-A"],
            ["--test", "t_single", "--mutation", "X", "--targets", "a,b"],
        ):
            with self.subTest(argv), redirect_stderr(io.StringIO()):
                self.assertEqual(run_sim.main(argv), 2)

    def test_usage_errors_exit_2(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(run_sim.main(["--group", "nightly"]), 2)
            self.assertEqual(run_sim.main(["--build", "huge"]), 2)
            self.assertEqual(run_sim.main(["--wave", "--sim", "vcs"]), 2)


if __name__ == "__main__":
    unittest.main()
