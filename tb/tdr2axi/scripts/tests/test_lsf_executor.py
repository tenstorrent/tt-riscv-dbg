# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests of lsf_executor.py against fake_lsf.py, which stands in for bsub, bjobs and bkill.

Covers argv template rendering with and without site values, memory units, job arrays and
their split under max_in_flight, bsub and bjobs -json parsing, the job and array scripts
under bash, dependencies and a failed build, refused submissions, failed queries,
interrupts, query backoff, timeouts, the pending limit and progress reports, jobs whose
host is lost (UNKWN, ZOMBI) or that the scheduler forgets, and the executor log.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lsf_executor as lsf
from fake_lsf import FakeClock, FakeLsf
from site_layer import Limits, LsfSite, Resources, SiteError

LIMITS = Limits(poll_interval_s=10, artifact_grace_s=0)


class ExecutorCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.clock = FakeClock()
        self.notes: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def executor(
        self, fake: FakeLsf, site: LsfSite | None = None, hook: Path | None = None
    ) -> lsf.LsfExecutor:
        return lsf.LsfExecutor(
            site or LsfSite(limits=LIMITS),
            work_dir=self.work,
            bench_dir=self.root,
            name="tdr2axi.stamp",
            hook=hook,
            run_command=fake,
            clock=self.clock,
            sleep=self.clock.sleep,
            notify=self.notes.append,
        )

    def task(self, task_id: str, *commands: str, **fields: object) -> lsf.Task:
        steps = tuple(
            lsf.Step(("sh", "-c", command), self.root / "logs" / f"{task_id}.{n}.log")
            for n, command in enumerate(commands, start=1)
        )
        return replace(lsf.Task(task_id, steps), **fields)

    def run_tasks(
        self, executor: lsf.LsfExecutor, tasks: Sequence[lsf.Task]
    ) -> dict[str, lsf.Outcome]:
        outcomes: dict[str, lsf.Outcome] = {}
        executor.run(tasks, lambda outcome: outcomes.setdefault(outcome.task.task_id, outcome))
        return outcomes


class TestRenderArgv(unittest.TestCase):
    VALUES: ClassVar[Mapping[str, str]] = {"jobname": "j", "joblog": "l", "script": "s"}

    def test_optional_groups_render_only_with_values(self) -> None:
        argv = lsf.render_argv(lsf.SUBMIT_ARGV, self.VALUES, {"extra_args": []})
        self.assertEqual(argv, ["bsub", "-J", "j", "-o", "l", "s"])

    def test_every_value_and_the_extra_arguments(self) -> None:
        values = {**self.VALUES, **lsf.resource_values(Resources("q", 2, 3000, 90), "GB")}
        argv = lsf.render_argv(lsf.SUBMIT_ARGV, values, {"extra_args": ["-P", "p"]})
        self.assertEqual(
            argv,
            ["bsub", "-q", "q", "-n", "2", "-R", "rusage[mem=3]", "-W", "90"]
            + ["-J", "j", "-o", "l", "-P", "p", "s"],
        )

    def test_a_token_without_its_value_is_an_error(self) -> None:
        with self.assertRaisesRegex(SiteError, "jobname"):
            lsf.render_argv(("bsub", "-J", "{jobname}"), {})

    def test_job_ids_splice(self) -> None:
        argv = lsf.render_argv(lsf.CANCEL_ARGV, {}, {"job_ids": ["1001", "1002[3]"]})
        self.assertEqual(argv, ["bkill", "1001", "1002[3]"])


class TestPlan(ExecutorCase):
    def tasks(self) -> list[lsf.Task]:
        return [
            lsf.Task("b00", build=True),
            lsf.Task("r0000", after="b00"),
            lsf.Task("r0001", after="b00"),
        ]

    def test_no_site_value_without_a_site_file(self) -> None:
        build, runs = self.executor(FakeLsf(), LsfSite()).plan(self.tasks())
        logs, scripts = self.work / "logs", self.work / "scripts"
        self.assertEqual(
            build.argv,
            ("bsub", "-J", "tdr2axi.stamp.b00", "-o", f"{logs}/b00.log", f"{scripts}/b00.sh"),
        )
        self.assertEqual(
            runs.argv,
            ("bsub", "-J", "tdr2axi.stamp.arr0001[1-2]", "-o", f"{logs}/arr0001.%I.log")
            + (f"{scripts}/arr0001.sh",),
        )

    def test_builds_take_the_build_request_in_the_site_units(self) -> None:
        site = LsfSite(
            mem_unit="GB",
            extra_args=("-P", "proj"),
            build_extra_args=("-x",),
            defaults=Resources("q", 1, 4096, 60),
            build_defaults=Resources(cores=4, mem_mb=16384),
            limits=LIMITS,
        )
        build, runs = self.executor(FakeLsf(), site).plan(self.tasks())
        self.assertEqual(
            build.argv[:13],
            ("bsub", "-q", "q", "-n", "4", "-R", "rusage[mem=16]", "-W", "60", "-J")
            + ("tdr2axi.stamp.b00", "-o", f"{self.work}/logs/b00.log"),
        )
        self.assertEqual(build.argv[13:16], ("-P", "proj", "-x"))
        self.assertEqual(runs.argv[1:9], ("-q", "q", "-n", "1", "-R", "rusage[mem=4]", "-W", "60"))
        self.assertIn("-P", runs.argv)
        self.assertNotIn("-x", runs.argv)

    def test_arrays_split_under_the_chunk_size_and_the_room(self) -> None:
        site = LsfSite(limits=replace(LIMITS, array_chunk_size=3))
        executor = self.executor(FakeLsf(), site)
        tasks = [lsf.Task(f"r{n}") for n in range(7)] + [lsf.Task("b0", build=True)]
        sizes = [len(chunk) for chunk in executor.chunks(tasks, 5)]
        self.assertEqual(sizes, [1, 3, 1])
        self.assertEqual(executor.chunks(tasks, 5)[0][0].task_id, "b0")
        self.assertEqual([len(c) for c in executor.chunks(tasks, None)], [1, 3, 3, 1])
        single = self.executor(FakeLsf(), replace(site, arrays=False))
        self.assertEqual([len(c) for c in single.chunks(tasks, 4)], [1, 1, 1, 1])


class TestParse(unittest.TestCase):
    def test_bsub_names_its_job_on_stdout_only(self) -> None:
        answer = "Job <4242> is submitted to queue <normal>.\n"
        self.assertEqual(lsf.parse_submit(answer), "4242")
        self.assertIsNone(lsf.parse_submit("Request aborted by esub. Job not submitted."))

    def test_bjobs_json_records(self) -> None:
        records = [
            {"JOBID": "1001", "JOBINDEX": "2", "STAT": "RUN", "EXIT_CODE": "", "EXIT_REASON": ""},
            {"JOBID": "1001[3]", "JOBINDEX": "3", "STAT": "DONE", "EXIT_CODE": ""},
            {"JOBID": "1002", "JOBINDEX": "0", "STAT": "EXIT", "EXIT_CODE": "2"},
            {"JOBID": "1003", "STAT": "EXIT", "EXIT_CODE": "130", "EXIT_REASON": "TERM_OWNER"},
            {"JOBID": "1004", "ERROR": "Job <1004> is not found"},
        ]
        stdout = json.dumps({"COMMAND": "bjobs", "JOBS": 5, "RECORDS": records})
        parsed = lsf.parse_bjobs(lsf.CommandResult((), 255, stdout, ""))
        self.assertEqual(
            parsed,
            {
                "1001[2]": lsf.JobRecord("RUN"),
                "1001[3]": lsf.JobRecord("DONE"),
                "1002": lsf.JobRecord("EXIT", 2),
                "1003": lsf.JobRecord("EXIT", 130, "TERM_OWNER"),
            },
        )

    def test_an_answer_that_is_not_json_is_a_failed_query(self) -> None:
        self.assertIsNone(lsf.parse_bjobs(lsf.CommandResult((), 255, "", "LSF is down")))
        self.assertIsNone(lsf.parse_bjobs(lsf.CommandResult((), 0, "{not json", "")))
        self.assertEqual(lsf.parse_bjobs(lsf.CommandResult((), 0, "", "")), {})

    def test_statuses_complete_through_the_first_failure(self) -> None:
        ok, bad = lsf.StepStatus(0, 1, 2), lsf.StepStatus(2, 1, 2)
        self.assertTrue(lsf.statuses_complete([ok, ok], 2))
        self.assertTrue(lsf.statuses_complete([bad, None], 2))
        self.assertFalse(lsf.statuses_complete([ok, None], 2))
        self.assertFalse(lsf.statuses_complete([], 1))

    def test_a_missing_command_is_a_result(self) -> None:
        result = lsf.run_scheduler_command(["no-such-scheduler-command"], 5.0)
        self.assertEqual(result.returncode, 127)


class TestScripts(ExecutorCase):
    def test_steps_run_in_order_and_stop_at_the_first_failure(self) -> None:
        hook = self.root / "site.env"
        hook.write_text("export SITE_READY=yes\n")
        status = self.root / "t.status"
        task = self.task("t", 'echo "ready=$SITE_READY"', "exit 3", "echo never")
        script = self.root / "t.sh"
        script.write_text(lsf.task_script(task, bench_dir=self.root, status=status, hook=hook))
        proc = subprocess.run(["bash", str(script)], check=False)
        self.assertEqual(proc.returncode, 3)
        statuses = lsf.read_statuses(status, 3)
        self.assertEqual([s.rc if s else None for s in statuses], [0, 3, None])
        self.assertEqual(task.steps[0].log.read_text(), "ready=yes\n")
        self.assertFalse(task.steps[2].log.exists())

    def test_a_hook_that_sets_errexit_still_lets_a_failing_step_report(self) -> None:
        hook = self.root / "strict.env"
        hook.write_text("set -eu\nexport SITE_READY=yes\n")
        status = self.root / "t.status"
        task = self.task("t", "exit 1")
        script = self.root / "t.sh"
        script.write_text(lsf.task_script(task, bench_dir=self.root, status=status, hook=hook))
        proc = subprocess.run(["bash", str(script)], check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([s.rc if s else None for s in lsf.read_statuses(status, 1)], [1])

    def test_an_array_element_runs_its_line_of_the_list(self) -> None:
        tasks_file = self.root / "a.tasks"
        scripts = []
        for n in (1, 2):
            path = self.root / f"t{n}.sh"
            path.write_text(f"echo element {n} > {self.root}/out.{n}\n")
            scripts.append(f"{path}\n")
        tasks_file.write_text("".join(scripts))
        script = self.root / "a.sh"
        script.write_text(lsf.array_script("a", tasks_file, 2))
        subprocess.run(
            ["bash", str(script)], env={"LSB_JOBINDEX": "2", "PATH": "/usr/bin:/bin"}, check=True
        )
        self.assertEqual((self.root / "out.2").read_text(), "element 2\n")
        self.assertFalse((self.root / "out.1").exists())


class TestRun(ExecutorCase):
    def test_runs_wait_for_their_build_and_go_out_as_one_array(self) -> None:
        fake = FakeLsf()
        tasks = [self.task("b00", "echo built", build=True)]
        tasks += [self.task(f"r{n}", f"echo run {n}", after="b00") for n in range(5)]
        outcomes = self.run_tasks(self.executor(fake), tasks)
        self.assertTrue(all(outcome.ok for outcome in outcomes.values()))
        build, runs = fake.submissions()
        self.assertIn("tdr2axi.stamp.b00", build)
        self.assertIn("tdr2axi.stamp.arr0001[1-5]", runs)
        self.assertEqual(outcomes["r3"].job_id, "1002[4]")
        self.assertEqual(tasks[4].steps[0].log.read_text(), "run 3\n")
        self.assertTrue(outcomes["r3"].joblog and outcomes["r3"].joblog.name == "arr0001.4.log")
        record = json.loads((self.work / lsf.JOBS_RECORD).read_text())
        self.assertEqual({row["task"] for row in record}, {task.task_id for task in tasks})

    def test_jobs_in_flight_never_exceed_max_in_flight(self) -> None:
        fake = FakeLsf()
        site = LsfSite(limits=replace(LIMITS, max_in_flight=3))
        tasks = [self.task(f"r{n}", "true") for n in range(7)]
        outcomes = self.run_tasks(self.executor(fake, site), tasks)
        self.assertEqual(len(outcomes), 7)
        self.assertTrue(all(outcome.ok for outcome in outcomes.values()))
        sizes = []
        for argv in fake.submissions():
            name = argv[argv.index("-J") + 1]
            sizes.append(int(name.rsplit("-", 1)[1].rstrip("]")) if name.endswith("]") else 1)
        self.assertEqual(sum(sizes), 7)
        for live, size in zip(fake.live_at_submit, sizes, strict=True):
            self.assertLessEqual(live + size, 3)

    def test_a_failed_build_blocks_its_runs_without_a_job(self) -> None:
        fake = FakeLsf()
        tasks = [
            self.task("b00", "echo broken; exit 2", build=True),
            self.task("r0", "true", after="b00"),
        ]
        outcomes = self.run_tasks(self.executor(fake), tasks)
        self.assertEqual(outcomes["b00"].rcs, (2,))
        blocked = outcomes["r0"]
        self.assertIsNotNone(blocked.blocked_by)
        self.assertEqual(blocked.blocked_by.task.task_id if blocked.blocked_by else "", "b00")
        self.assertEqual(len(fake.submissions()), 1)

    def test_a_refused_submission_settles_its_tasks(self) -> None:
        fake = FakeLsf(fail_submits={1})
        tasks = [self.task("b0", "true", build=True), self.task("r0", "true")]
        outcomes = self.run_tasks(self.executor(fake), tasks)
        self.assertIn("submission failed: Request aborted", outcomes["b0"].reason)
        self.assertTrue(outcomes["r0"].ok)

    def test_three_refused_submissions_in_a_row_stop_the_run(self) -> None:
        fake = FakeLsf(fail_submits={1, 2, 3})
        site = LsfSite(arrays=False, limits=LIMITS)
        tasks = [self.task(f"r{n}", "true") for n in range(4)]
        with self.assertRaisesRegex(lsf.SchedulerError, "3 submissions in a row"):
            self.run_tasks(self.executor(fake, site), tasks)

    def test_failed_queries_stop_the_run_and_kill_its_jobs(self) -> None:
        fake = FakeLsf(fail_queries=range(1, 6))
        with self.assertRaisesRegex(lsf.SchedulerError, "5 job queries"):
            self.run_tasks(self.executor(fake), [self.task("r0", "true")])
        self.assertEqual(self.clock.now, 10 * (1 + 2 + 4 + 8 + 8))
        self.assertIn(["bkill", "1001"], fake.calls)
        record = json.loads((self.work / lsf.JOBS_RECORD).read_text())
        self.assertEqual(record[0]["state"], "CANCELLED")

    def test_an_interrupt_kills_every_queued_or_running_job(self) -> None:
        fake = FakeLsf(states={"default": ["PEND"]})
        self.clock.interrupt_on = 2
        tasks = [self.task("b0", "true", build=True), self.task("r0", "true")]
        tasks.append(self.task("r1", "true"))
        with self.assertRaises(KeyboardInterrupt):
            self.run_tasks(self.executor(fake), tasks)
        kills = [call for call in fake.calls if call[0] == "bkill"]
        self.assertEqual(kills, [["bkill", "1001", "1002"]])
        self.assertEqual(fake.live(), [])

    def test_a_job_past_its_timeout_is_killed(self) -> None:
        fake = FakeLsf(states={"default": ["PEND", "HANG"]})
        task = self.task("r0", "true", timeout_s=15.0)
        outcome = self.run_tasks(self.executor(fake), [task])["r0"]
        self.assertIn(["bkill", "1001"], fake.calls)
        self.assertTrue(outcome.reason.startswith("timed out after 15 s; killed job 1001"))
        self.assertEqual(outcome.rcs, (None,))

    def test_a_job_the_scheduler_forgets_settles_after_the_grace(self) -> None:
        fake = FakeLsf(states={"default": ["PEND", "VANISH"]})
        site = LsfSite(limits=replace(LIMITS, artifact_grace_s=25))
        outcome = self.run_tasks(self.executor(fake, site), [self.task("r0", "true")])["r0"]
        self.assertIn("job 1001 left the scheduler without an exit status", outcome.reason)
        self.assertGreaterEqual(self.clock.now, 40)

    def test_a_finished_job_without_a_status_file_is_unfinished(self) -> None:
        fake = FakeLsf(states={"default": ["EXIT:137"]}, run_scripts=False)
        outcome = self.run_tasks(self.executor(fake), [self.task("r0", "true")])["r0"]
        self.assertFalse(outcome.complete)
        self.assertIn("job 1001 ended EXIT without an exit status; job log", outcome.reason)

    def test_the_executor_log_records_every_command(self) -> None:
        self.run_tasks(self.executor(FakeLsf()), [self.task("r0", "true")])
        log = (self.work / lsf.EXECUTOR_LOG).read_text()
        self.assertIn("submit rc=0: bsub -J tdr2axi.stamp.r0", log)
        self.assertIn("stderr: *** INFO ***", log)
        fields = "jobid jobindex stat exit_code exit_reason pend_reason"
        self.assertIn(f"query rc=0: bjobs -json -o '{fields}' 1001", log)


PEND_REASON = "New job is waiting for scheduling;\n Not enough hosts to meet the job's spec;"


class TestQueuedAndLostJobs(ExecutorCase):
    def site(self, **limits: float) -> LsfSite:
        return LsfSite(limits=replace(LIMITS, **limits))

    def test_a_job_queued_past_max_pend_s_is_killed_with_its_pending_reason(self) -> None:
        for state in ("PEND", "PSUSP"):
            with self.subTest(state):
                self.clock.now = 0.0
                fake = FakeLsf(states={"default": ["PEND", state]}, pend_reason=PEND_REASON)
                task = self.task("r0", "true", timeout_s=15.0)
                executor = self.executor(fake, self.site(max_pend_s=100))
                outcome = self.run_tasks(executor, [task])["r0"]
                self.assertIn(["bkill", "1001"], fake.calls)
                self.assertTrue(
                    outcome.reason.startswith(
                        f"queued 1 min, over max_pend_s 100 s ({state}:"
                        " New job is waiting for scheduling); killed job 1001; job log "
                    ),
                    outcome.reason,
                )
                self.assertEqual(outcome.rcs, (None,))
                self.assertIn(f"lsf: task r0: {outcome.reason.split('; job log')[0]}", self.notes)

    def test_progress_names_the_longest_queued_job_and_its_reason(self) -> None:
        fake = FakeLsf(states={"default": ["PEND"], "r1": ["RUN", "HANG"]}, pend_reason=PEND_REASON)
        site = LsfSite(arrays=False, limits=replace(LIMITS, max_pend_s=1000))
        tasks = [self.task("r0", "true"), self.task("r1", "true", timeout_s=500.0)]
        self.run_tasks(self.executor(fake, site), tasks)
        progress = [note for note in self.notes if not note.startswith("lsf: task ")]
        self.assertEqual(
            progress[0],
            "lsf: 1 jobs dispatched, 1 queued, 0 tasks settled;"
            " job 1001 queued 1 min: New job is waiting for scheduling",
        )
        self.assertEqual(len(progress), 10)

    def test_an_unknown_job_that_returns_settles_as_usual(self) -> None:
        fake = FakeLsf(states={"default": ["UNKWN", "UNKWN", "RUN", "AUTO"]})
        outcome = self.run_tasks(self.executor(fake), [self.task("r0", "true")])["r0"]
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.state, "DONE")
        self.assertEqual(fake.counts["bkill"], 0)

    def test_a_complete_status_file_settles_a_lost_job_without_a_kill(self) -> None:
        for state in ("UNKWN", "ZOMBI"):
            with self.subTest(state):
                fake = FakeLsf(states={"default": ["RUN", state]})
                outcome = self.run_tasks(self.executor(fake), [self.task(f"r{state}", "true")])
                self.assertTrue(outcome[f"r{state}"].ok)
                self.assertEqual(outcome[f"r{state}"].state, state)
                self.assertEqual(fake.counts["bkill"], 0)

    def test_a_zombie_job_is_killed_and_settles_after_the_kill_grace(self) -> None:
        fake = FakeLsf(states={"default": ["HANG", "ZOMBI"]})
        executor = self.executor(fake, self.site(kill_grace_s=50))
        outcome = self.run_tasks(executor, [self.task("r0", "true")])["r0"]
        self.assertEqual(fake.counts["bkill"], 1)
        self.assertTrue(
            outcome.reason.startswith(
                "job 1001 went ZOMBI; killed it; still ZOMBI 50 s after the kill; job log "
            ),
            outcome.reason,
        )
        self.assertEqual(outcome.rcs, (None,))

    def test_an_unknown_job_past_its_timeout_settles_after_the_kill_grace(self) -> None:
        fake = FakeLsf(states={"default": ["HANG", "UNKWN"]})
        task = self.task("r0", "true", timeout_s=15.0)
        outcome = self.run_tasks(self.executor(fake, self.site(kill_grace_s=50)), [task])["r0"]
        self.assertTrue(
            outcome.reason.startswith(
                "timed out after 15 s; killed job 1001; still UNKWN 50 s after the kill"
            ),
            outcome.reason,
        )
        self.assertGreaterEqual(self.clock.now, 15 + 50)

    def test_a_status_file_complete_at_the_kill_decides_the_outcome(self) -> None:
        for states in (["RUN"], ["RUN", "RUN", "RUN", "UNKWN"]):
            with self.subTest(states=states):
                fake = FakeLsf(states={"default": states})
                task = self.task("r0", "exit 3", timeout_s=15.0)
                outcome = self.run_tasks(self.executor(fake), [task])["r0"]
                self.assertEqual(fake.counts["bkill"], 1)
                self.assertTrue(outcome.complete)
                self.assertEqual((outcome.rcs, outcome.reason), ((3,), ""))


if __name__ == "__main__":
    unittest.main()
