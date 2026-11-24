# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""An LSF stand-in for the scheduler tests of run_sim.py, answering bsub, bjobs and bkill.

A FakeLsf is the executor's run_command. bsub records one job, or one element job per index
of an array -J name[1-N], and answers "Job <N> is submitted to queue <q>." on stdout with a
submission filter's banner on stderr. Time is the sequence of bjobs calls: each call moves
every job it names one step through its scripted states, and a job that enters RUN runs its
script under bash right then, with LSB_JOBINDEX set for an array element and its output in
the -o path with %I expanded, so the status files appear when a real job would write them.

The answers keep the shapes a driver must not misread: bjobs -json reports an element as
JOBID and JOBINDEX, an unknown id as a record with an ERROR field and exit status 255, a
queued job's PEND_REASON as the multi-line text LSF prints, and bkill refuses a finished
job with exit status 255 and accepts, without effect, a job whose host is lost.

``states`` maps a job-name substring to its state list (default PEND, RUN, AUTO): PEND,
PSUSP, RUN, HANG (RUN without running the script), UNKWN, ZOMBI, DONE, EXIT:<code>, AUTO
(DONE when the script exited 0, else EXIT with its code) and VANISH (the scheduler forgets
the job). The last state holds. bkill ends a job as EXIT 130 unless it is UNKWN or ZOMBI.
``pend_reason`` is every queued job's PEND_REASON. ``fail_submits`` and ``fail_queries``
are the 1-based bsub and bjobs calls that fail. A FakeClock is the executor's clock and
sleep: each sleep moves it forward, and one of them may interrupt.

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from lsf_executor import CommandResult

ARRAY_NAME = re.compile(r"^(.*)\[(\d+)-(\d+)\]$")
JOB_ID = re.compile(r"^\d+(?:\[\d+\])?$")
VALUE_FLAGS = frozenset({"-q", "-n", "-R", "-W", "-J", "-o", "-e", "-P", "-G", "-app", "-m"})
DEFAULT_STATES = ("PEND", "RUN", "AUTO")
FILTER_BANNER = "*** INFO *** memory limit set by the site's submission filter"
FINISHED = frozenset({"DONE", "EXIT"})
QUEUED = frozenset({"PEND", "PSUSP"})
LOST = frozenset({"UNKWN", "ZOMBI"})


class FakeClock:
    """A clock that the executor's sleep moves forward; a sleep may also interrupt."""

    def __init__(self, interrupt_on: int | None = None) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.interrupt_on = interrupt_on

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps == self.interrupt_on:
            raise KeyboardInterrupt
        self.now += seconds


class FakeJob:
    """One plain job or array element and where it is in its states."""

    def __init__(self, *, base: str, index: int, name: str, script: str, joblog: Path) -> None:
        self.base = base
        self.index = index
        self.name = name
        self.script = script
        self.joblog = joblog
        self.options: dict[str, str] = {}
        self.states: tuple[str, ...] = DEFAULT_STATES
        self.step = -1
        self.stat = ""
        self.exit_code: int | None = None
        self.reason = ""
        self.script_rc: int | None = None
        self.gone = False

    @property
    def job_id(self) -> str:
        """``<id>[<index>]`` for an array element, else the job id."""
        return f"{self.base}[{self.index}]" if self.index else self.base


class FakeLsf:
    """The scheduler: its jobs, every call it answered, and the scripted behaviour."""

    def __init__(
        self,
        *,
        states: Mapping[str, Sequence[str]] | None = None,
        fail_submits: Iterable[int] = (),
        fail_queries: Iterable[int] = (),
        env: Mapping[str, str] | None = None,
        run_scripts: bool = True,
        pend_reason: str = "",
    ) -> None:
        self.states = dict(states or {})
        self.pend_reason = pend_reason
        self.fail_submits = set(fail_submits)
        self.fail_queries = set(fail_queries)
        self.env = dict(env or {})
        self.run_scripts = run_scripts
        self.jobs: list[FakeJob] = []
        self.calls: list[list[str]] = []
        self.counts: Counter[str] = Counter()
        self.live_at_submit: list[int] = []
        self.next_id = 1001

    def __call__(self, argv: Sequence[str], timeout_s: float) -> CommandResult:
        """Answer one scheduler command."""
        args = list(argv)
        self.calls.append(args)
        self.counts[args[0]] += 1
        handlers = {"bsub": self.bsub, "bjobs": self.bjobs, "bkill": self.bkill}
        handler = handlers.get(args[0])
        if handler is None:
            return CommandResult(tuple(args), 127, "", f"{args[0]}: command not found")
        return handler(args)

    def submissions(self) -> list[list[str]]:
        """Every bsub argv, in order."""
        return [call for call in self.calls if call[0] == "bsub"]

    def live(self) -> list[FakeJob]:
        """The jobs neither finished nor forgotten."""
        return [job for job in self.jobs if not job.gone and job.stat not in FINISHED]

    def bsub(self, args: list[str]) -> CommandResult:
        """Record a job or an array, or refuse it on a failing call."""
        if self.counts["bsub"] in self.fail_submits:
            return CommandResult(
                tuple(args), 255, "", "Request aborted by esub. Job not submitted."
            )
        options: dict[str, str] = {}
        index = 1
        while index < len(args) - 1:
            if args[index] in VALUE_FLAGS:
                options[args[index]] = args[index + 1]
                index += 2
            else:
                index += 1
        self.live_at_submit.append(len(self.live()))
        base, name = str(self.next_id), options.get("-J", "job")
        self.next_id += 1
        array = ARRAY_NAME.match(name)
        indexes = range(int(array.group(2)), int(array.group(3)) + 1) if array else [0]
        for element in indexes:
            log = options.get("-o", "job.log").replace("%I", str(element))
            job_name = f"{array.group(1)}[{element}]" if array else name
            job = FakeJob(
                base=base, index=element, name=job_name, script=args[-1], joblog=Path(log)
            )
            job.options = options
            job.states = self._states(job_name)
            self.jobs.append(job)
        queue = options.get("-q", "normal")
        return CommandResult(
            tuple(args), 0, f"Job <{base}> is submitted to queue <{queue}>.\n", FILTER_BANNER
        )

    def bjobs(self, args: list[str]) -> CommandResult:
        """Advance and report every job each asked id names."""
        if self.counts["bjobs"] in self.fail_queries:
            return CommandResult(tuple(args), 255, "", "LSF is down. Please wait ...")
        records: list[dict[str, str]] = []
        for job_id in (arg for arg in args[1:] if JOB_ID.match(arg)):
            jobs = self._named(job_id)
            for job in jobs:
                self._advance(job)
            seen = [job for job in jobs if not job.gone]
            if not seen:
                records.append({"JOBID": job_id, "ERROR": f"Job <{job_id}> is not found"})
            records += [self._record(job) for job in seen]
        failed = any("ERROR" in record for record in records)
        stdout = json.dumps({"COMMAND": "bjobs", "JOBS": len(records), "RECORDS": records})
        return CommandResult(tuple(args), 255 if failed else 0, stdout, "")

    def bkill(self, args: list[str]) -> CommandResult:
        """Kill every unfinished job each id names; refuse a finished one."""
        out, err = [], []
        for job_id in (arg for arg in args[1:] if JOB_ID.match(arg)):
            jobs = [job for job in self._named(job_id) if not job.gone]
            if not jobs:
                err.append(f"Job <{job_id}>: No matching job found")
            for job in jobs:
                if job.stat in FINISHED:
                    err.append(f"Job <{job.job_id}>: Job has already finished")
                    continue
                if job.stat in LOST:
                    out.append(f"Job <{job.job_id}> is being terminated")
                    continue
                job.stat, job.exit_code = "EXIT", 130
                job.reason = "TERM_OWNER: job killed by owner"
                job.states = ("EXIT:130",)
                job.step = 0
                out.append(f"Job <{job.job_id}> is being terminated")
        rc = 255 if err else 0
        return CommandResult(tuple(args), rc, "\n".join(out), "\n".join(err))

    def _states(self, name: str) -> tuple[str, ...]:
        for key, states in self.states.items():
            if key in name:
                return tuple(states)
        return tuple(self.states.get("default", DEFAULT_STATES))

    def _named(self, job_id: str) -> list[FakeJob]:
        return [job for job in self.jobs if job_id in (job.base, job.job_id)]

    def _advance(self, job: FakeJob) -> None:
        if job.gone or job.stat in FINISHED:
            return
        job.step = min(job.step + 1, len(job.states) - 1)
        state = job.states[job.step]
        if state == "VANISH":
            job.gone = True
            return
        if state == "HANG":
            job.stat = "RUN"
            return
        if state == "RUN" and job.script_rc is None:
            job.script_rc = self._run(job)
        if state == "AUTO":
            rc = self._run(job) if job.script_rc is None else job.script_rc
            state = "DONE" if rc == 0 else f"EXIT:{rc}"
        if state.startswith("EXIT:"):
            job.stat, job.exit_code = "EXIT", int(state.split(":", 1)[1])
            return
        job.stat = state

    def _run(self, job: FakeJob) -> int:
        if not self.run_scripts:
            job.script_rc = 0
            return 0
        job.joblog.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **self.env, "LSB_JOBINDEX": str(job.index)}
        with job.joblog.open("w") as log:
            proc = subprocess.run(
                ["bash", job.script], env=env, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        job.script_rc = proc.returncode
        return proc.returncode

    def _record(self, job: FakeJob) -> dict[str, str]:
        exit_code = "" if job.exit_code is None else str(job.exit_code)
        return {
            "JOBID": job.base,
            "JOBINDEX": str(job.index),
            "STAT": job.stat,
            "EXIT_CODE": exit_code if job.stat == "EXIT" else "",
            "EXIT_REASON": job.reason,
            "PEND_REASON": self.pend_reason if job.stat in QUEUED else "",
        }
