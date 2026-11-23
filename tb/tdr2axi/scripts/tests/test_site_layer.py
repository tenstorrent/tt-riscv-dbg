# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of site_layer.py: which site file is active, what each table may hold, and
that the committed example loads.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import site_layer

EXAMPLE = Path(__file__).resolve().parents[1] / "site.local.example.toml"


class TestSitePath(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.default = self.root / site_layer.SITE_FILE_NAME
        self.named = self.root / "elsewhere.toml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_environment_wins_over_the_default_file(self) -> None:
        self.default.write_text("")
        self.named.write_text("")
        self.assertEqual(site_layer.site_path(str(self.named), self.default), self.named.resolve())

    def test_the_default_file_serves_when_the_environment_names_none(self) -> None:
        self.assertIsNone(site_layer.site_path("", self.default))
        self.default.write_text("")
        self.assertEqual(site_layer.site_path("  ", self.default), self.default)

    def test_a_named_file_that_is_missing_is_an_error(self) -> None:
        self.default.write_text("")
        with self.assertRaisesRegex(site_layer.SiteError, site_layer.SITE_ENV):
            site_layer.site_path(str(self.named), self.default)

    def test_no_site_file_gives_no_site_value(self) -> None:
        site = site_layer.load_site("", self.default)
        self.assertEqual(site, site_layer.Site())
        self.assertIsNone(site.path)
        self.assertEqual(site.lsf.defaults, site_layer.Resources())
        self.assertEqual(site.lsf.extra_args, ())
        self.assertEqual(site.sim("vcs"), site_layer.SimSite())

    def test_the_named_file_supplies_its_values(self) -> None:
        self.default.write_text('[lsf.defaults]\nqueue = "from-default"\n')
        self.named.write_text('[lsf.defaults]\nqueue = "from-environment"\n')
        site = site_layer.load_site(str(self.named), self.default)
        self.assertEqual(site.lsf.defaults.queue, "from-environment")
        self.assertEqual(site_layer.load_site("", self.default).lsf.defaults.queue, "from-default")


class TestParseSite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "vcs.env").write_text("export VCS_READY=1\n")
        (self.root / "dump.sv").write_text("module dump; endmodule\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def load(self, text: str) -> site_layer.Site:
        path = self.root / "site.toml"
        path.write_text(text)
        return site_layer.load_site(str(path), self.root / "absent.toml")

    def test_the_committed_example_loads(self) -> None:
        site = site_layer.load_site(str(EXAMPLE), self.root / "absent.toml")
        self.assertEqual(site.lsf.defaults, site_layer.Resources("normal", 1, 4096, 60))
        self.assertEqual(site.lsf.build_defaults.walltime_min, 120)
        self.assertEqual(site.lsf.limits.max_in_flight, 400)
        self.assertEqual(site.lsf.limits.array_chunk_size, 200)
        self.assertEqual((site.lsf.limits.max_pend_s, site.lsf.limits.kill_grace_s), (21600, 300))
        self.assertIsNone(site.sim("vcs").setup_hook)

    def test_every_table(self) -> None:
        site = self.load(
            """
schema_version = 1
[lsf]
mem_unit = "GB"
arrays = false
extra_args = ["-P", "proj"]
build_extra_args = ["-x"]
submit_argv = ["bsub", ["-q", "{queue}"], "-R", "rusage[mem={mem}]", "{script}"]
[lsf.defaults]
queue = "q"
mem_mb = 2048
walltime = "1:30"
[lsf.build_defaults]
cores = 8
[lsf.limits]
max_in_flight = 10
poll_interval_s = 2.5
artifact_grace_s = 0
max_pend_s = 600
kill_grace_s = 0
[simulators.vcs]
setup_hook = "vcs.env"
cocotb_major = 2
compile_args = ["-lca"]
sim_args = ["+opt"]
wave_sources = ["dump.sv"]
wave_sim_args = ["+dump={wave_file}"]
wave_file = "waves.out"
cov_merge_argv = ["merge", "-o", "{output}", "{inputs}"]
cov_hits_argv = ["merge", "-f", "{inputs_file}", "-report", "{report_dir}", "-log", "{merge_dir}/l"]
"""
        )
        lsf = site.lsf
        self.assertEqual((lsf.mem_unit, lsf.arrays), ("GB", False))
        self.assertEqual((lsf.extra_args, lsf.build_extra_args), (("-P", "proj"), ("-x",)))
        self.assertEqual(
            lsf.submit_argv, ("bsub", ("-q", "{queue}"), "-R", "rusage[mem={mem}]", "{script}")
        )
        self.assertEqual(lsf.defaults, site_layer.Resources("q", None, 2048, 90))
        self.assertEqual(
            lsf.build_defaults.over(lsf.defaults), site_layer.Resources("q", 8, 2048, 90)
        )
        self.assertEqual((lsf.limits.max_in_flight, lsf.limits.poll_interval_s), (10, 2.5))
        self.assertEqual(lsf.limits.artifact_grace_s, 0.0)
        self.assertEqual((lsf.limits.max_pend_s, lsf.limits.kill_grace_s), (600.0, 0.0))
        self.assertEqual(site_layer.Limits().max_pend_s, 21600.0)
        vcs = site.sim("vcs")
        self.assertEqual(vcs.setup_hook, (self.root / "vcs.env").resolve())
        self.assertEqual((vcs.cocotb_major, site.sim("verilator").cocotb_major), (2, None))
        self.assertEqual(vcs.wave_sources, ((self.root / "dump.sv").resolve(),))
        self.assertEqual((vcs.compile_args, vcs.sim_args), (("-lca",), ("+opt",)))
        self.assertEqual((vcs.wave_sim_args, vcs.wave_file), (("+dump={wave_file}",), "waves.out"))
        self.assertEqual(vcs.cov_merge_argv, ("merge", "-o", "{output}", "{inputs}"))
        self.assertEqual(
            vcs.cov_hits_argv,
            ("merge", "-f", "{inputs_file}", "-report", "{report_dir}", "-log", "{merge_dir}/l"),
        )

    def test_rejects_what_it_cannot_use(self) -> None:
        cases = {
            "unknown top key": "[farm]\n",
            "unknown lsf key": "[lsf]\nqueue = 'q'\n",
            "schema": "schema_version = 2\n",
            "mem unit": "[lsf]\nmem_unit = 'PB'\n",
            "walltime": "[lsf.defaults]\nwalltime = '1h'\n",
            "zero cores": "[lsf.defaults]\ncores = 0\n",
            "submit without script": "[lsf]\nsubmit_argv = ['bsub', '-J', '{jobname}']\n",
            "script only in a group": "[lsf]\nsubmit_argv = ['bsub', ['{script}']]\n",
            "unknown placeholder": "[lsf]\nsubmit_argv = ['bsub', '{host}', '{script}']\n",
            "query without ids": "[lsf]\nquery_argv = ['bjobs', '-json']\n",
            "simulator": "[simulators.othersim]\n",
            "vcs key on verilator": "[simulators.verilator]\nwave_file = 'w.vcd'\n",
            "missing hook": "[simulators.vcs]\nsetup_hook = 'absent.env'\n",
            "merge without output": "[simulators.vcs]\ncov_merge_argv = ['merge', '{inputs}']\n",
            "merge with a group": "[simulators.vcs]\ncov_merge_argv = ['m', ['{output}']]\n",
            "hits without a report": "[simulators.vcs]\ncov_hits_argv = ['merge', '{inputs}']\n",
            "hits with a group": "[simulators.vcs]\ncov_hits_argv = ['m', ['{report_dir}']]\n",
            "hits with an output": "[simulators.vcs]\ncov_hits_argv = ['{report_dir}{output}']\n",
            "hits on verilator": "[simulators.verilator]\ncov_hits_argv = ['m', '{report_dir}']\n",
            "wave file with a directory": "[simulators.vcs]\nwave_file = 'a/b.vcd'\n",
            "zero poll": "[lsf.limits]\npoll_interval_s = 0\n",
            "zero pending limit": "[lsf.limits]\nmax_pend_s = 0\n",
            "negative kill grace": "[lsf.limits]\nkill_grace_s = -1\n",
            "zero cocotb release": "[simulators.vcs]\ncocotb_major = 0\n",
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(site_layer.SiteError):
                self.load(text)

    def test_memory_renders_in_the_site_unit(self) -> None:
        self.assertEqual(site_layer.mem_in_unit(16384, "MB"), 16384)
        self.assertEqual(site_layer.mem_in_unit(16384, "GB"), 16)
        self.assertEqual(site_layer.mem_in_unit(1500, "GB"), 2)
        self.assertEqual(site_layer.mem_in_unit(2, "KB"), 2048)


if __name__ == "__main__":
    unittest.main()
