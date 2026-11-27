# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of run_sim.py's per-test cover-hit files under --cov and per-build merges under
--cov-merge.

Covers reading cover counts from a Verilator coverage.dat and a VCS text assertion report,
which runs a test's file counts, the files a Verilator run writes on this host, the merge
job per test a VCS run sends to the fake scheduler, a merge job that fails or writes no
report, a VCS site without the cover-hit command, mutation runs, the per-build merges of a
VCS run over several builds, and the dry run.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from collections import Counter
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fake_make
import run_sim
import site_layer
import test_run_sim as base
import test_run_sim_lsf as lsf_base

# The shape of a VCS text assertion report, cut to a few covers: two instances share
# cov_held, and a cover-sequence row must not count.
ASSERT_REPORT = """\
Assertions


Assertions by Category
           ASSERT PROPERTIES SEQUENCES
Total           0          4         1

-------------------------------------------------------------------------------

Summary for Cover Properties
             NUMBER PERCENT
Total Number      4  100.00
Uncovered         1   25.00
Matches           3   75.00

-------------------------------------------------------------------------------

Detail Report for Cover Properties

Cover Properties Uncovered:
                COVER PROPERTIES                CATEGORY SEVERITY ATTEMPTS MATCHES INCOMPLETE
tb_top.u_fcov.c_axi_r_late                             0        0  2097124       0          0

Cover Properties Matches:
               COVER PROPERTIES                CATEGORY SEVERITY ATTEMPTS MATCHES INCOMPLETE
tb_top.u_axi_mgr_sva.cov_held                         0        0  2097124     491          0
tb_top.u_fcov.c_tdr_write_in_idle                     0        0   872129    1960          0
tb_top.u_other_sva.cov_held                           0        0  2097124       9          0

Cover Sequences Matches:
               COVER SEQUENCES                 CATEGORY SEVERITY ATTEMPTS MATCHES INCOMPLETE
tb_top.u_fcov.s_sequence                              0        0       10       4          0
"""
REPORT_COUNTS = {"c_axi_r_late": 0, "c_tdr_write_in_idle": 1960, "cov_held": 500}
Manifest = dict[str, Any]


def result(
    test: str,
    status: str,
    *,
    coverage: bool = True,
    expected_fail: str | None = None,
    owner: str | None = None,
    build: str = "big",
    message: str | None = None,
) -> run_sim.RunResult:
    """A settled run of ``test`` whose coverage data sits in its run directory; with an
    ``owner`` a build check of that test, illegal when ``message`` is set."""
    run_dir = Path("/sim") / f"{test}.{build}.{status}"
    spec = run_sim.RunSpec(
        test=test,
        build=build,
        seed=1,
        ratio=run_sim.Ratio("20:1", 1000, 20000),
        run_dir=run_dir,
        build_dir=Path("sim/build") / build,
        expected_fail=expected_fail,
        owner=owner,
        legal=owner is not None and message is None,
        expect_message=message,
    )
    data = run_dir / "coverage.dat" if coverage else None
    return run_sim.RunResult(spec, status, "", 1.0, "", None, None, None, data)


class TestCoverCounts(unittest.TestCase):
    def test_a_coverage_dat_counts_cover_points_by_label(self) -> None:
        text = fake_make.coverage_dat({**fake_make.COVERS, "tb_top.u_fcov.c_seen": 5})
        self.assertEqual(
            run_sim.verilator_cover_counts(text),
            Counter({"c_seen": 5, "c_never": 0, "c_twice": 2}),
        )
        self.assertEqual(run_sim.verilator_cover_counts("# SystemC::Coverage-3\n"), Counter())

    def test_a_point_without_its_own_label_takes_its_name_from_the_hierarchy(self) -> None:
        line = "C '\x01f\x02x.sv\x01t\x02user\x01h\x02tb_top.u_fcov.c_bare' 3\n"
        self.assertEqual(run_sim.verilator_cover_counts(line), Counter({"c_bare": 3}))

    def test_an_assertion_report_counts_cover_property_matches_by_label(self) -> None:
        counts = run_sim.vcs_cover_counts(ASSERT_REPORT)
        self.assertEqual(counts, Counter(REPORT_COUNTS))
        self.assertIn("c_axi_r_late", counts)

    def test_the_matches_column_comes_from_the_header_row(self) -> None:
        report = (
            "Cover Properties Matches:\n"
            "    COVER PROPERTIES    MATCHES ATTEMPTS INCOMPLETE\n"
            "tb_top.u_fcov.c_x            7      100          0\n"
        )
        self.assertEqual(run_sim.vcs_cover_counts(report), Counter({"c_x": 7}))

    def test_a_row_of_numbers_alone_is_no_cover(self) -> None:
        report = (
            "Cover Properties Matches:\n"
            "    COVER PROPERTIES    CATEGORY SEVERITY ATTEMPTS MATCHES INCOMPLETE\n"
            "tb_top.u_fcov.c_name_too_long_for_its_column\n"
            "                           0        0      100       7          0\n"
            "tb_top.u_fcov.c_short      0        0      100       3          0\n"
        )
        self.assertEqual(run_sim.vcs_cover_counts(report), Counter({"c_short": 3}))

    def test_a_row_without_a_hierarchical_name_is_no_cover(self) -> None:
        report = (
            "Cover Properties Matches:\n"
            "    COVER PROPERTIES    CATEGORY SEVERITY ATTEMPTS MATCHES INCOMPLETE\n"
            "tb_top.u_fcov.c_short      0        0      100       3          0\n"
            "Total 4 100\n"
            "Total                      4        0      100       7          0\n"
        )
        self.assertEqual(run_sim.vcs_cover_counts(report), Counter({"c_short": 3}))

    def test_a_test_takes_its_passing_runs_and_legal_build_checks(self) -> None:
        runs = [
            result("t_a", run_sim.PASS),
            result("t_a", run_sim.FAIL),
            result("t_a", run_sim.PASS, coverage=False),
            result("t_x", run_sim.PASS, expected_fail="CHK-X FAIL"),
            result(base.BRINGUP, run_sim.PASS, owner="t_params", build="small"),
            result(base.BRINGUP, run_sim.PASS, owner="t_params", coverage=False, message="M"),
            result(base.BRINGUP, run_sim.PASS, owner="t_bad", coverage=False, message="M"),
            result(base.BRINGUP, run_sim.ERROR),
        ]
        by_test = run_sim.hits_runs(runs)
        self.assertEqual(list(by_test), ["t_a", "t_params", "t_x"])
        self.assertEqual(by_test["t_a"], [runs[0], runs[2]])
        self.assertEqual(by_test["t_x"], [runs[3]])
        self.assertEqual(by_test["t_params"], [runs[4]])
        hits = run_sim.unsettled_hits("t_x", by_test["t_x"], Path("/sim/hits"))
        self.assertEqual((hits.runs, hits.expected_fail_runs), (1, 1))
        self.assertEqual(hits.path, Path("/sim/hits/t_x.json"))


class TestVerilatorHits(lsf_base.MainCase):
    def test_each_test_gets_a_file_of_its_passing_runs(self) -> None:
        fake = base.FakeLauncher(
            build_rc=lambda cmd: 0, run_passes=lambda cmd: "TESTCASE=t_single" in cmd
        )
        argv = ["--test", "t_single", "--test", base.BRINGUP, "--seeds", "2", "--cov"]
        rc, manifest, out = self.run_local(argv, fake)
        self.assertEqual(rc, 1)
        hits = manifest["cover_hits"]
        hits_dir = Path(hits["dir"])
        self.assertEqual(hits_dir.parent, self.sim_dir)
        self.assertTrue(hits_dir.name.startswith("cover_hits."))
        self.assertIn(f"cover hits: 1 of 1 tests in {hits_dir}", out)
        self.assertEqual(sorted(path.name for path in hits_dir.iterdir()), ["t_single.json"])
        document = json.loads((hits_dir / "t_single.json").read_text())
        self.assertEqual(
            document,
            {
                "test": "t_single",
                "hits": {"c_never": 0, "c_seen": 2, "c_twice": 4},
                "runs": 2,
                "expected_fail_runs": 0,
            },
        )
        self.assertEqual(hits["tests"]["t_single"]["file"], str(hits_dir / "t_single.json"))

    def test_expected_failures_and_legal_build_checks_count(self) -> None:
        self.bench = lambda: base.fixture_bench(testlist=base.XFAIL_TESTLIST_TEXT)
        self.scheduler(
            env={
                "FAKE_MAKE_FAILING_TESTS": "t_xfail",
                "FAKE_MAKE_RUN_LOG": base.PIN_LINE.rstrip("\n"),
                "FAKE_MAKE_BUILD_FAIL": "AXI_ADDR_WIDTH=",
            }
        )
        argv = ["--test", "t_xfail", "--test", "t_params", "--seeds", "2", "--cov"]
        rc, manifest, _ = self.run_main([*argv, "--executor", "lsf"])
        self.assertEqual(rc, 0)
        tests = manifest["cover_hits"]["tests"]
        self.assertEqual(sorted(tests), ["t_params", "t_xfail"])
        counted = {test: (e["runs"], e["expected_fail_runs"]) for test, e in tests.items()}
        self.assertEqual(counted, {"t_xfail": (2, 2), "t_params": (2, 0)})
        document = json.loads(Path(tests["t_params"]["file"]).read_text())
        self.assertEqual(document["hits"], {"c_never": 0, "c_seen": 2, "c_twice": 4})

    def test_coverage_without_covers_is_an_error(self) -> None:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True, covers={})
        rc, manifest, out = self.run_local(["--test", "t_single", "--seed", "3", "--cov"], fake)
        self.assertEqual(rc, 1)
        entry = manifest["cover_hits"]["tests"]["t_single"]
        self.assertEqual((entry["status"], entry["file"]), ("error", None))
        self.assertIn("cover hits error: t_single: its coverage data holds no cover", out)

    def test_runs_without_coverage_data_err_their_test(self) -> None:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True, covers=None)
        rc, manifest, out = self.run_local(["--test", "t_single", "--seed", "3", "--cov"], fake)
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["summary"], {"pass": 1, "fail": 0, "error": 0})
        hits = manifest["cover_hits"]
        entry = hits["tests"]["t_single"]
        self.assertEqual((entry["status"], entry["file"]), ("error", None))
        self.assertEqual(entry["reason"], run_sim.NO_COVERAGE)
        self.assertEqual((entry["runs"], entry["expected_fail_runs"]), (1, 0))
        self.assertEqual(list(Path(hits["dir"]).iterdir()), [])
        self.assertIn(f"cover hits: 0 of 1 tests in {hits['dir']}", out)
        self.assertIn(f"cover hits error: t_single: {run_sim.NO_COVERAGE}", out)

    def test_a_run_without_coverage_data_errs_its_test_beside_runs_with_data(self) -> None:
        fake = base.FakeLauncher(
            build_rc=lambda cmd: 0,
            run_passes=lambda cmd: True,
            covered=lambda cmd: "CONFIG=small" not in cmd,
        )
        argv = ["--test", "t_single", "--seed", "3", "--target", "big", "--target", "small"]
        rc, manifest, out = self.run_local([*argv, "--cov"], fake)
        self.assertEqual(rc, 1)
        self.assertEqual(manifest["summary"], {"pass": 2, "fail": 0, "error": 0})
        hits = manifest["cover_hits"]
        entry = hits["tests"]["t_single"]
        self.assertEqual((entry["status"], entry["file"]), ("error", None))
        self.assertEqual((entry["runs"], entry["expected_fail_runs"]), (2, 0))
        bare = [Path(run["run_dir"]).name for run in manifest["runs"] if run["build"] == "small"]
        self.assertEqual(len(bare), 1)
        reason = f"1 of 2 passing runs left no coverage data: {bare[0]}"
        self.assertEqual(entry["reason"], reason)
        self.assertEqual(list(Path(hits["dir"]).iterdir()), [])
        self.assertIn(f"cover hits error: t_single: {reason}", out)

    def test_mutation_runs_write_no_file(self) -> None:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: False)
        argv = ["--test", "t_single", "--seed", "3", "--cov", "--mutation", "M"]
        _, manifest, _ = self.run_local([*argv, "--targets", "CHK-X"], fake)
        self.assertNotIn("cover_hits", manifest)
        self.assertEqual(list(self.sim_dir.glob("cover_hits.*")), [])

    def test_without_cov_no_file(self) -> None:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        rc, manifest, _ = self.run_local(["--test", "t_single", "--seed", "3"], fake)
        self.assertEqual(rc, 0)
        self.assertNotIn("cover_hits", manifest)


class TestVcsHits(lsf_base.MainCase):
    ARGV = ("--test", "t_single", "--sim", "vcs", "--cov", "--executor", "lsf")

    def use_hits_command(self, *argv: str) -> None:
        vcs = site_layer.SimSite(cov_hits_argv=argv, cov_merge_argv=("true", "{output}"))
        self.site = site_layer.Site(lsf=self.site.lsf, simulators=(("vcs", vcs),))
        self.scheduler()

    def copy_report(self) -> tuple[str, ...]:
        """A cover-hit command that writes ASSERT_REPORT into the report directory."""
        report = self.root / "asserts.fixture"
        report.write_text(ASSERT_REPORT)
        script = f"mkdir -p {{report_dir}} && cp {report} {{report_dir}}/asserts.txt"
        return ("sh", "-c", script)

    def test_each_test_merges_as_a_job_and_its_report_gives_the_counts(self) -> None:
        self.use_hits_command(*self.copy_report())
        argv = [*self.ARGV, "--seed", "2", "--target", "big", "--target", "small"]
        rc, manifest, out = self.run_main(argv)
        self.assertEqual(rc, 0)
        entry = manifest["cover_hits"]["tests"]["t_single"]
        self.assertEqual((entry["status"], entry["runs"]), ("pass", 2))
        document = json.loads(Path(entry["file"]).read_text())
        self.assertEqual(document["hits"], REPORT_COUNTS)
        work = Path(entry["file"]).with_suffix("")
        inputs = (work / "inputs.txt").read_text().splitlines()
        self.assertTrue(inputs[0].endswith("sim/build/vcs_big_cov/simv.vdb"))
        self.assertTrue(inputs[1].endswith("sim/build/vcs_small_cov/simv.vdb"))
        self.assertEqual(sorted(inputs[2:]), sorted(run["coverage"] for run in manifest["runs"]))
        self.assertEqual(entry["log"], str(work / "hits.log"))
        self.assertIn(f"{work}/report", entry["command"])
        self.assertTrue(self.fake.submissions()[-1][2].endswith(".hits000"))
        self.assertIn(f"cover hits: 1 of 1 tests in {work.parent}", out)

    def test_a_failing_merge_or_a_missing_report_errs_the_file(self) -> None:
        for seed, command, status, reason in (
            ("4", ("sh", "-c", "exit 3", "sh", "{report_dir}"), "fail", "exited 3"),
            ("5", ("true", "{report_dir}"), "error", "the merge wrote no "),
        ):
            with self.subTest(status=status):
                self.use_hits_command(*command)
                rc, manifest, _ = self.run_main([*self.ARGV, "--seed", seed])
                self.assertEqual(rc, 1)
                entry = manifest["cover_hits"]["tests"]["t_single"]
                self.assertEqual((entry["status"], entry["file"]), (status, None))
                self.assertTrue(entry["reason"].startswith(reason), entry["reason"])

    def test_without_the_site_command_no_file_is_written(self) -> None:
        self.site = site_layer.Site(lsf=self.site.lsf)
        self.scheduler()
        rc, manifest, out = self.run_main([*self.ARGV, "--seed", "2"])
        self.assertEqual(rc, 0)
        hits = manifest["cover_hits"]
        self.assertEqual((hits["dir"], hits["tests"]), (None, {}))
        self.assertIn("cov_hits_argv", hits["reason"])
        self.assertIn("run_sim: cover hits: none written; VCS needs cov_hits_argv", self.stderr)
        self.assertNotIn("cover hits", out)


class TestPerBuildMerges(lsf_base.MainCase):
    ARGV = ("--test", "t_single", "--seed", "2", "--sim", "vcs", "--cov", "--cov-merge")

    def setUp(self) -> None:
        super().setUp()
        vcs = site_layer.SimSite(cov_merge_argv=("true", "{output}", "-f", "{inputs_file}"))
        self.site = site_layer.Site(lsf=self.site.lsf, simulators=(("vcs", vcs),))
        self.scheduler()

    def run_merges(self, *targets: str) -> tuple[int, Manifest, str]:
        return self.run_merges_of(self.ARGV, *targets)

    def run_merges_of(self, argv: Sequence[str], *targets: str) -> tuple[int, Manifest, str]:
        chosen = [word for target in targets for word in ("--target", target)]
        return self.run_main([*argv, *chosen, "--executor", "lsf"])

    def test_each_build_merges_alone_beside_the_merge_of_every_build(self) -> None:
        rc, manifest, out = self.run_merges("big", "small")
        self.assertEqual(rc, 0)
        merge = manifest["coverage_merge"]
        top = Path(merge["output"]).parent
        self.assertEqual((merge["status"], merge["inputs"]), ("pass", 4))
        self.assertEqual(sorted(merge["builds"]), ["big", "small"])
        coverage = {run["build"]: run["coverage"] for run in manifest["runs"]}
        for build, entry in merge["builds"].items():
            self.assertEqual((entry["status"], entry["inputs"]), ("pass", 2))
            self.assertEqual(entry["output"], str(top / build / "merged.vdb"))
            inputs = (top / build / "inputs.txt").read_text().splitlines()
            self.assertTrue(inputs[0].endswith(f"sim/build/vcs_{build}_cov/simv.vdb"))
            self.assertEqual(inputs[1:], [coverage[build]])
        self.assertIn(f"coverage merges per build: 2 of 2 pass, in {top}/<build>/", out)

    def test_a_legal_build_check_joins_only_the_cross_build_merge(self) -> None:
        self.bench = lambda: base.fixture_bench(testlist=base.XFAIL_TESTLIST_TEXT)
        self.scheduler(env={"FAKE_MAKE_BUILD_FAIL": "AXI_ADDR_WIDTH="})
        argv = ["--test", base.BRINGUP, "--test", "t_params", "--seed", "3", "--sim", "vcs"]
        rc, manifest, _ = self.run_merges_of([*argv, "--cov", "--cov-merge"], "big", "small")
        self.assertEqual(rc, 0)
        legal = next(r for r in manifest["runs"] if r["run_kind"] == "expect-build-pass")
        merge = manifest["coverage_merge"]
        top = Path(merge["output"]).parent
        self.assertIn(legal["coverage"], (top / "inputs.txt").read_text().splitlines())
        self.assertEqual(sorted(merge["builds"]), ["big", "small"])
        for build, entry in merge["builds"].items():
            inputs = (top / build / "inputs.txt").read_text().splitlines()
            self.assertEqual(entry["inputs"], 2, inputs)
            self.assertNotIn(legal["coverage"], inputs)

    def test_one_build_needs_no_merge_of_its_own(self) -> None:
        rc, manifest, out = self.run_merges("big")
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["coverage_merge"]["builds"], {})
        self.assertNotIn("per build", out)

    def test_verilator_merges_every_build_once(self) -> None:
        fake = base.FakeLauncher(build_rc=lambda cmd: 0, run_passes=lambda cmd: True)
        argv = ["--test", "t_single", "--seed", "2", "--target", "big", "--target", "small"]
        rc, manifest, _ = self.run_local([*argv, "--cov-merge"], fake)
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["coverage_merge"]["builds"], {})
        self.assertEqual(manifest["coverage_merge"]["inputs"], 2)


class TestDryRun(lsf_base.MainCase):
    def test_the_dry_run_prints_the_cover_hit_step(self) -> None:
        vcs = site_layer.SimSite(
            cov_hits_argv=("merge", "-f", "{inputs_file}", "-report", "{report_dir}"),
            cov_merge_argv=("merge", "-o", "{output}", "{inputs}"),
        )
        self.site = site_layer.Site(simulators=(("vcs", vcs),))
        lines = self.dry_run(["--test", "t_single", "--sim", "vcs", "--cov", "--cov-merge"])
        hits = next(line for line in lines if line.startswith("cover hits: "))
        self.assertRegex(hits, r"^cover hits: merge -f '\S+/cover_hits\.[\d-]+/<test>/inputs.txt'")
        self.assertTrue(hits.endswith("/<test>/report'"))
        self.assertTrue(any(line.startswith("coverage merge per build: ") for line in lines))
        out = io.StringIO()
        with redirect_stdout(out):
            run_sim.main(["--test", "t_single", "--cov", "--dry-run"])
        self.assertIn("cover hits: from each passing run's coverage.dat into ", out.getvalue())
        self.assertFalse(self.sim_dir.exists())


if __name__ == "__main__":
    unittest.main()
