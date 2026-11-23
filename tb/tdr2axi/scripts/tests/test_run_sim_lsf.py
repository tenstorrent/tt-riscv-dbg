# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of run_sim.py's --executor lsf, VCS commands, waves and --cov-merge.

The scheduler runs go through fake_lsf.py, whose jobs run their scripts under bash with
fake_make.py first on PATH as make; the local runs go through test_run_sim's FakeLauncher.
Covers builds as jobs and the manifest a local run writes, a failed build, expected
failures, build checks and mutations on the scheduler, a run killed in the queue, a
scheduler that stops answering, the site's cocotb release, an interrupt, the dry run with
and without a site file, the VCS wave dump and site options, and the coverage merge.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import io
import json
import os
import shlex
import signal
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_make
import lsf_executor
import run_sim
import site_layer
import test_run_sim as base
from fake_lsf import FakeClock, FakeLsf

EXAMPLE = Path(__file__).resolve().parents[1] / "site.local.example.toml"
FAST = site_layer.Limits(poll_interval_s=1, artifact_grace_s=0)
Manifest = dict[str, Any]


def check_setting(run: Mapping[str, Any], name: str, *, step: int = 0) -> str | None:
    """The value a build check's build (step 0) or bring-up (step 1) command gives ``name``."""
    tokens = shlex.split(run["command"].splitlines()[step])
    return next((t.split("=", 1)[1] for t in tokens if t.startswith(f"{name}=")), None)


class MainCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim_dir = self.root / "sim"
        bin_dir = self.root / "bin"
        fake_make.install(bin_dir)
        self.path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        self.site = site_layer.Site(lsf=site_layer.LsfSite(limits=FAST))
        self.bench: Callable[[], run_sim.Bench] = base.fixture_bench
        self.fake = FakeLsf()
        self.clock = FakeClock()
        self.stderr = ""
        self.patches = mock.patch.multiple(
            run_sim,
            BENCH_DIR=self.root,
            SIM_DIR=self.sim_dir,
            load_bench=lambda: self.bench(),
            missing_tools=lambda sim: [],
            missing_binaries=lambda names: [],
            probe_simulator=lambda sim: run_sim.SimTool(sim, "Verilator 5.054", 1),
            active_site=lambda: self.site,
            LsfExecutor=self.executor,
        )
        self.patches.start()

    def tearDown(self) -> None:
        self.patches.stop()
        self.tmp.cleanup()

    def executor(self, site: site_layer.LsfSite, **fields: Any) -> lsf_executor.LsfExecutor:
        return lsf_executor.LsfExecutor(
            site, run_command=self.fake, clock=self.clock, sleep=self.clock.sleep, **fields
        )

    def scheduler(self, env: Mapping[str, str] | None = None, **fields: Any) -> FakeLsf:
        self.fake = FakeLsf(env={"PATH": self.path, **(env or {})}, **fields)
        return self.fake

    def run_main(self, argv: Sequence[str]) -> tuple[int, Manifest, str]:
        manifest = self.sim_dir / "m.json"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = run_sim.main([*argv, "--manifest", str(manifest)])
        self.stderr = err.getvalue()
        document = json.loads(manifest.read_text()) if manifest.is_file() else {}
        return rc, document, out.getvalue()

    def run_local(self, argv: Sequence[str], fake: base.FakeLauncher) -> tuple[int, Manifest, str]:
        with mock.patch.object(run_sim, "Launcher", lambda timeout: fake):
            return self.run_main(argv)

    def dry_run(self, argv: Sequence[str]) -> list[str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(run_sim.main([*argv, "--dry-run"]), 0)
        return out.getvalue().splitlines()


class TestLsfRuns(MainCase):
    ARGV = ("--test", "t_single", "--seeds", "2", "--target", "big", "--target", "small")

    def test_models_build_once_as_jobs_and_the_manifest_matches_a_local_run(self) -> None:
        local = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        _, local_manifest, _ = self.run_local(self.ARGV, local)
        self.scheduler()
        before = [signal.getsignal(sig) for sig in run_sim.INTERRUPT_SIGNALS]
        rc, manifest, _ = self.run_main([*self.ARGV, "--executor", "lsf"])
        self.assertEqual(rc, 0)
        self.assertEqual([signal.getsignal(sig) for sig in run_sim.INTERRUPT_SIGNALS], before)
        self.assertEqual(manifest["summary"], {"pass": 4, "fail": 0, "error": 0})
        self.assertEqual(set(manifest), set(local_manifest))
        self.assertEqual(set(manifest["runs"][0]), set(local_manifest["runs"][0]))
        names = [argv[argv.index("-J") + 1] for argv in self.fake.submissions()]
        self.assertEqual(
            [name.rsplit(".", 1)[1] for name in names], ["arr0001[1-2]", "arr0002[1-4]"]
        )
        for run in manifest["runs"]:
            self.assertTrue(run["command"].startswith("make -C "))
            self.assertEqual(run["coverage"], f"{run['run_dir']}/coverage.dat")
            self.assertEqual(run["clk_ps"], 1000)
        builds = sorted(self.sim_dir.glob("build/*/run_sim.build.json"))
        self.assertEqual(
            [path.parent.name for path in builds], ["verilator_big", "verilator_small"]
        )

    def test_a_failed_build_errors_its_runs_with_the_build_log_named(self) -> None:
        self.scheduler(env={"FAKE_MAKE_BUILD_FAIL": "CONFIG=small"})
        rc, manifest, _ = self.run_main([*self.ARGV, "--executor", "lsf"])
        self.assertEqual(rc, 1)
        by_build: dict[str, list[dict[str, Any]]] = {}
        for run in manifest["runs"]:
            by_build.setdefault(run["build"], []).append(run)
        self.assertEqual({run["status"] for run in by_build["big"]}, {"pass"})
        for run in by_build["small"]:
            self.assertEqual(run["status"], "error")
            self.assertIn("build failed (make exited 2); build log ", run["reason"])
            self.assertTrue(run["build_log"].endswith("verilator_small/build.log"))
        self.assertEqual(len(self.fake.submissions()), 2)

    def test_expected_failures_build_checks_and_mutations_grade_as_locally(self) -> None:
        self.bench = lambda: base.fixture_bench(testlist=base.XFAIL_TESTLIST_TEXT)
        self.scheduler(
            env={
                "FAKE_MAKE_FAILING_TESTS": "t_xfail",
                "FAKE_MAKE_RUN_LOG": base.PIN_LINE.rstrip("\n"),
                "FAKE_MAKE_BUILD_FAIL": "AXI_ADDR_WIDTH=",
            }
        )
        argv = ["--test", "t_xfail", "--test", "t_params", "--seed", "4", "--executor", "lsf"]
        rc, manifest, out = self.run_main(argv)
        self.assertEqual(rc, 0)
        runs = {(run["test"], run["run_kind"], run["build"]): run for run in manifest["runs"]}
        xfail = runs[("t_xfail", "test", "big")]
        self.assertEqual((xfail["status"], xfail["expected_fail"]), ("pass", True))
        self.assertIn("XFAIL", out)
        illegal = runs[("t_params", "expect-build-fail", "big")]
        legal = runs[("t_params", "expect-build-pass", "small")]
        for check in (illegal, legal):
            self.assertEqual(check["status"], "pass")
            self.assertTrue(Path(check["log"]).read_text().startswith("CHK-CFG-P PASS: "))
        self.scheduler()
        argv = ["--test", "t_xfail", "--seed", "5", "--executor", "lsf"]
        rc, manifest, _ = self.run_main([*argv, "--mutation", "TDR2AXI_X=1", "--targets", "CHK-X"])
        run = manifest["runs"][0]
        self.assertEqual((run["kind"], run["expected_fail"]), ("mutation", False))
        self.assertTrue(run["command"].startswith("env TDR2AXI_X=1 make "))

    def checks_by_kind(self, argv: Sequence[str]) -> dict[str, dict[str, Any]]:
        """t_params' checks under the scheduler by run kind; the illegal case's build fails."""
        self.bench = lambda: base.fixture_bench(testlist=base.XFAIL_TESTLIST_TEXT)
        self.scheduler(env={"FAKE_MAKE_BUILD_FAIL": "AXI_ADDR_WIDTH="})
        rc, manifest, _ = self.run_main(["--test", "t_params", *argv, "--executor", "lsf"])
        self.assertEqual(rc, 0)
        return {run["run_kind"]: run for run in manifest["runs"]}

    def test_a_legal_build_check_records_coverage_as_a_run_does(self) -> None:
        strict = run_sim.VERILATOR_STRICT
        for seed, cov, compile_args in (
            ("4", ["--cov"], f"{strict} --coverage"),
            ("5", [], strict),
        ):
            with self.subTest(cov=cov):
                checks = self.checks_by_kind(["--seed", seed, *cov])
                legal, illegal = checks["expect-build-pass"], checks["expect-build-fail"]
                self.assertEqual(check_setting(legal, "COMPILE_ARGS"), compile_args)
                self.assertEqual(check_setting(illegal, "COMPILE_ARGS"), strict)
                self.assertEqual(legal["coverage"], f"{legal['run_dir']}/probe/coverage.dat")
                self.assertTrue(Path(legal["coverage"]).is_file())
                self.assertIsNone(illegal["coverage"])

    def test_a_legal_vcs_build_check_writes_its_coverage_in_its_probe_directory(self) -> None:
        checks = self.checks_by_kind(["--seed", "4", "--sim", "vcs", "--cov"])
        legal, illegal = checks["expect-build-pass"], checks["expect-build-fail"]
        cm = f"-cm {run_sim.VCS_COVERAGE}"
        database = f"{legal['run_dir']}/probe/coverage.vdb"
        self.assertEqual(check_setting(legal, "COMPILE_ARGS"), f"{run_sim.VCS_STRICT} {cm}")
        self.assertEqual(check_setting(legal, "SIM_ARGS", step=1), f"{cm} -cm_dir {database}")
        self.assertEqual(legal["coverage"], database)
        self.assertTrue(Path(database).is_dir())
        self.assertEqual(check_setting(illegal, "COMPILE_ARGS"), run_sim.VCS_STRICT)
        self.assertIsNone(illegal["coverage"])

    def test_a_run_killed_in_the_queue_errors_with_the_pending_reason(self) -> None:
        self.site = site_layer.Site(lsf=site_layer.LsfSite(limits=replace(FAST, max_pend_s=5)))
        self.scheduler(states={"arr0002": ["PEND"]}, pend_reason="Not enough job slots;")
        rc, manifest, _ = self.run_main([*self.ARGV, "--executor", "lsf"])
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["summary"], {"pass": 0, "fail": 0, "error": 4})
        for run in manifest["runs"]:
            self.assertIn("over max_pend_s 5 s (PEND: Not enough job slots)", run["reason"])
        self.assertIn("run_sim: lsf: task r0000: queued 6 s, over max_pend_s 5 s", self.stderr)

    def test_a_scheduler_that_stops_answering_still_leaves_a_manifest(self) -> None:
        self.scheduler(fail_queries=range(4, 9))
        rc, manifest, _ = self.run_main([*self.ARGV, "--executor", "lsf"])
        self.assertEqual(rc, 2)
        self.assertEqual(manifest["summary"], {"pass": 0, "fail": 0, "error": 4})
        for run in manifest["runs"]:
            self.assertEqual(run["reason"], "not settled: 5 job queries in a row failed")
        self.assertIn(["bkill", "1002"], self.fake.calls)

    def test_the_site_declares_the_cocotb_release_its_jobs_run(self) -> None:
        verilator = site_layer.SimSite(cocotb_major=2)
        self.site = replace(self.site, simulators=(("verilator", verilator),))
        self.scheduler()
        rc, manifest, _ = self.run_main(["--test", "t_single", "--seed", "3", "--executor", "lsf"])
        self.assertEqual(rc, 0)
        self.assertIn("COCOTB_TEST_FILTER=", manifest["runs"][0]["command"])

    def test_an_interrupt_kills_the_jobs_and_exits_130(self) -> None:
        self.scheduler(states={"default": ["PEND"]})
        self.clock.interrupt_on = 2
        rc, manifest, _ = self.run_main([*self.ARGV, "--executor", "lsf"])
        self.assertEqual((rc, manifest), (130, {}))
        self.assertIn(["bkill", "1001"], self.fake.calls)


class TestLsfDryRun(MainCase):
    ARGV = ("--test", "t_single", "--seeds", "3", "--target", "all", "--executor", "lsf")

    def bsub_lines(self) -> list[str]:
        return [line for line in self.dry_run(self.ARGV) if line.startswith("bsub ")]

    def test_without_a_site_file_a_job_asks_for_a_name_and_a_log_only(self) -> None:
        self.site = site_layer.Site()
        lines = self.dry_run(self.ARGV)
        self.assertIn("site file: none", lines)
        bsubs = [line for line in lines if line.startswith("bsub ")]
        self.assertEqual(len(bsubs), 3)
        for line in bsubs:
            words = line.split("  # ")[0].split()
            self.assertEqual((len(words), words[0], words[1], words[3]), (6, "bsub", "-J", "-o"))
            self.assertTrue(words[5].endswith(".sh"))
        self.assertFalse(self.sim_dir.exists())

    def test_the_example_site_file_renders_its_requests(self) -> None:
        self.site = site_layer.load_site(str(EXAMPLE), self.root / "absent.toml")
        bsubs = self.bsub_lines()
        self.assertIn("bsub -q normal -n 4 -R 'rusage[mem=16384]' -W 120 -J ", bsubs[0])
        self.assertTrue(bsubs[0].endswith("# 2 builds"))
        self.assertIn("bsub -q normal -n 1 -R 'rusage[mem=4096]' -W 60 -J ", bsubs[1])
        self.assertTrue(bsubs[1].endswith("# 3 runs once b00 succeeds"))
        self.assertFalse(self.sim_dir.exists())


class TestVcs(MainCase):
    ARGV = ("--test", "t_single", "--seed", "3", "--sim", "vcs")

    def run_vcs(self, *extra: str) -> tuple[int, base.FakeLauncher]:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, _, _ = self.run_local([*self.ARGV, *extra], fake)
        return rc, fake

    def test_waves_pass_the_bench_vcd_plusarg(self) -> None:
        (self.root / "tb").mkdir()
        (self.root / "tb" / "tb_top.sv").write_text('$value$plusargs("TDR2AXI_VCD=%s", f);\n')
        rc, fake = self.run_vcs("--wave")
        build, run = fake.commands
        self.assertEqual(rc, 0)
        self.assertIn("WAVES=1", build)
        self.assertTrue(build[-1].endswith("vcs_big_waves/simv"))
        self.assertEqual(
            run[0:2], ["env", f"SIM_ARGS=+TDR2AXI_VCD={Path(run[-1]).parent}/waves.vcd"]
        )

    def test_waves_without_the_bench_plusarg_or_site_settings_are_a_usage_error(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(run_sim.main([*self.ARGV, "--wave"]), 2)
        self.assertIn("+TDR2AXI_VCD=<file>", err.getvalue())

    def test_the_site_supplies_options_and_another_wave_format(self) -> None:
        source = self.root / "dump.sv"
        source.write_text("module dump; endmodule\n")
        vcs = site_layer.SimSite(
            compile_args=("-lca",),
            sim_args=("+site",),
            wave_compile_args=("-kdb",),
            wave_sources=(source,),
            wave_sim_args=("+dump={wave_file}",),
            wave_file="waves.site",
        )
        self.site = site_layer.Site(simulators=(("vcs", vcs),))
        rc, fake = self.run_vcs("--wave", "--cov")
        build, run = fake.commands
        self.assertEqual(rc, 0)
        cm = f"-cm {run_sim.VCS_COVERAGE}"
        self.assertEqual(build[1], f"COMPILE_ARGS={cm} -lca -kdb {source}")
        run_dir = Path(run[-1]).parent
        self.assertEqual(
            run[1], f"SIM_ARGS={cm} -cm_dir {run_dir}/coverage.vdb +site +dump={run_dir}/waves.site"
        )


class TestCovMerge(MainCase):
    def test_verilator_merges_the_passing_runs(self) -> None:
        fake = base.FakeLauncher(
            build_rc=lambda cmd: 0, run_passes=lambda cmd: "TESTCASE=t_single" in cmd
        )
        argv = ["--test", "t_single", "--test", base.BRINGUP, "--seed", "3", "--cov-merge"]
        rc, manifest, _ = self.run_local(argv, fake)
        self.assertEqual(rc, 1)
        merge = manifest["coverage_merge"]
        self.assertEqual((merge["status"], merge["inputs"]), ("pass", 1))
        passing = next(run for run in manifest["runs"] if run["status"] == "pass")
        self.assertEqual(
            fake.commands[-1],
            ["verilator_coverage", "--write", merge["output"], passing["coverage"]],
        )
        self.assertTrue(merge["output"].endswith("/merged.dat"))
        inputs = Path(merge["output"]).parent / "inputs.txt"
        self.assertEqual(inputs.read_text(), f"{passing['coverage']}\n")

    def test_vcs_merges_with_the_site_command_as_a_job(self) -> None:
        vcs = site_layer.SimSite(cov_merge_argv=("true", "{output}", "-f", "{inputs_file}"))
        self.site = site_layer.Site(lsf=self.site.lsf, simulators=(("vcs", vcs),))
        self.scheduler()
        argv = ["--test", "t_single", "--seed", "2", "--sim", "vcs", "--cov", "--cov-merge"]
        rc, manifest, _ = self.run_main([*argv, "--executor", "lsf"])
        self.assertEqual(rc, 0)
        merge = manifest["coverage_merge"]
        self.assertEqual((merge["status"], merge["inputs"]), ("pass", 2))
        self.assertTrue(merge["command"].startswith("true "))
        inputs = (Path(merge["output"]).parent / "inputs.txt").read_text().splitlines()
        self.assertTrue(inputs[0].endswith("sim/build/vcs_big_cov/simv.vdb"))
        self.assertEqual(inputs[1], manifest["runs"][0]["coverage"])
        self.assertIn("tdr2axi.", self.fake.submissions()[-1][2])
        self.assertTrue(self.fake.submissions()[-1][2].endswith(".merge"))

    def test_vcs_needs_coverage_and_a_site_command(self) -> None:
        argv = ["--test", "t_single", "--sim", "vcs", "--cov-merge"]
        with redirect_stderr(io.StringIO()):
            self.assertEqual(run_sim.main(argv), 2)
            self.assertEqual(run_sim.main([*argv, "--cov"]), 2)


if __name__ == "__main__":
    unittest.main()
