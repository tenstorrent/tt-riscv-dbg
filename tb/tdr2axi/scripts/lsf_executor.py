# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The LSF executor of run_sim.py: builds, build checks and runs as scheduler jobs.

A task is one or more commands, each with its log. Its job script sources the simulator's
setup hook from the site layer, changes to the bench, runs the commands in order, stops at
the first that fails, and appends each command's exit status, start and end time to the
task's status file. The verdict comes from the status file and the logs; the scheduler's
state only says when a job is over.

A submission renders SUBMIT_ARGV, or the site's submit_argv. An optional group (-q {queue},
-n {cores}, -R rusage[mem={mem}], -W {walltime}) renders only when the site layer sets its
value; {mem} is the request in the site's mem_unit, {walltime} is minutes and {extra_args}
splices the site's extra arguments. Ready tasks go out as job arrays name[1-N] of at most
array_chunk_size elements while fewer than max_in_flight jobs are queued or running; an
element reads its task script from the array's task list by LSB_JOBINDEX, and logs to the
array's -o path with %I replaced by its index. A task that names another in ``after`` is
submitted once that task succeeded, and settles as blocked, without a job, when it did not.

Every poll_interval_s the executor asks bjobs -json about every job it submitted, backing
off up to eight intervals after failed queries. A job that reports DONE or EXIT, or that the
scheduler has forgotten, settles from its status file, after up to artifact_grace_s for a
shared filesystem to show the file. A job queued (PEND, PSUSP) longer than max_pend_s is
killed and its reason names the scheduler's pending reason; a job dispatched longer than
its task's timeout is killed; a job that goes ZOMBI is killed. A killed job, or one the
scheduler reports UNKWN or ZOMBI, settles from a complete status file at once, and a killed
job that has not finished kill_grace_s after the kill settles without one. Any exception, an
interrupt included, kills every queued or running job. Every few polls the executor reports
how many jobs run and wait, and the oldest wait with its reason, through ``notify``. Every
scheduler command and its output goes to executor.log in the work directory, and jobs.json
there maps each task to its job.

Imported by run_sim.py; the tests run with

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from site_layer import PLACEHOLDER, LsfSite, Resources, SiteError, Template, mem_in_unit

SUBMIT_ARGV: Template = (
    "bsub",
    ("-q", "{queue}"),
    ("-n", "{cores}"),
    ("-R", "rusage[mem={mem}]"),
    ("-W", "{walltime}"),
    "-J",
    "{jobname}",
    "-o",
    "{joblog}",
    "{extra_args}",
    "{script}",
)
# bjobs reports an array element's index in JOBINDEX, which tells its record from the others.
QUERY_ARGV: Template = (
    "bjobs",
    "-json",
    "-o",
    "jobid jobindex stat exit_code exit_reason pend_reason",
    "{job_ids}",
)
CANCEL_ARGV: Template = ("bkill", "{job_ids}")
# bsub's only answer that names the job; a site's submission filter may also write to stderr.
SUBMITTED = re.compile(r"^Job <(\d+)> is submitted to (?:default )?queue <([^>]+)>", re.MULTILINE)
ARRAY_INDEX_ENV = "LSB_JOBINDEX"
ARRAY_LOG_TOKEN = "%I"
FINISHED = frozenset({"DONE", "EXIT"})
# Queued and waiting for a host; PSUSP is a queued job its owner or an admin suspended.
QUEUED = frozenset({"PEND", "PSUSP"})
# The scheduler has lost the job's host; a bkill does not move a job out of these states.
LOST = frozenset({"UNKWN", "ZOMBI"})
ZOMBIE = "ZOMBI"
QUERY_BATCH = 200
SUBMIT_FAILURE_LIMIT = 3
QUERY_FAILURE_LIMIT = 5
QUERY_BACKOFF_LIMIT = 8
PROGRESS_POLLS = 10
REASON_LIMIT = 200
OUTPUT_LOG_LIMIT = 4000
EXECUTOR_LOG = "executor.log"
JOBS_RECORD = "jobs.json"


class SchedulerError(Exception):
    """The scheduler refuses or fails in a way no single task can absorb."""


@dataclass(frozen=True)
class CommandResult:
    """One scheduler command as it ran; ``returncode`` None when it did not answer in time."""

    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str

    @property
    def failure(self) -> str:
        """The line that best says why the command did not do its job."""
        text = self.stderr.strip() or self.stdout.strip()
        if text:
            return text.splitlines()[-1][:300]
        return "no answer" if self.returncode is None else f"exit status {self.returncode}"


RunCommand = Callable[[Sequence[str], float], CommandResult]


@dataclass(frozen=True)
class Step:
    """One command of a task and the log its output goes to."""

    cmd: tuple[str, ...]
    log: Path


@dataclass(frozen=True)
class Task:
    """One job's work: its steps, whether it takes the build resources, the task it waits
    for, and its time limit once running (None for none)."""

    task_id: str
    steps: tuple[Step, ...] = ()
    build: bool = False
    after: str | None = None
    timeout_s: float | None = None


@dataclass(frozen=True)
class StepStatus:
    """A step's exit status and its start and end in seconds since the epoch."""

    rc: int
    start: int
    end: int


@dataclass(frozen=True)
class Outcome:
    """How a task settled: the status of each step that reported one, the job, and the
    reason a status is missing. ``blocked_by`` is the outcome of the task it waited for,
    when that one did not succeed and this one never ran."""

    task: Task
    statuses: tuple[StepStatus | None, ...] = ()
    job_id: str = ""
    state: str = ""
    reason: str = ""
    joblog: Path | None = None
    blocked_by: Outcome | None = None

    @property
    def rcs(self) -> tuple[int | None, ...]:
        """Each step's exit status, None for a step that reported none."""
        statuses = (*self.statuses, *(None,) * (len(self.task.steps) - len(self.statuses)))
        return tuple(None if status is None else status.rc for status in statuses)

    @property
    def complete(self) -> bool:
        """Whether every step that ran reported, through the last or the first that failed."""
        return statuses_complete(self.statuses, len(self.task.steps))

    @property
    def ok(self) -> bool:
        """Whether every step ran and exited 0."""
        return self.complete and all(rc == 0 for rc in self.rcs)

    @property
    def started(self) -> int | None:
        """When the first step started, in seconds since the epoch."""
        return next((status.start for status in self.statuses if status is not None), None)

    @property
    def duration_s(self) -> float:
        """The time the reported steps took."""
        return float(sum(s.end - s.start for s in self.statuses if s is not None))


@dataclass(frozen=True)
class JobRecord:
    """One job's line of a bjobs answer: its state, exit code and exit reason, and why it
    waits while queued."""

    stat: str
    exit_code: int | None = None
    reason: str = ""
    pend_reason: str = ""


@dataclass(frozen=True)
class Submission:
    """One bsub: its label, its tasks (an array when more than one), argv, script and log.

    An array's ``joblog`` carries %I, which LSF replaces with each element's index.
    """

    label: str
    tasks: tuple[Task, ...]
    argv: tuple[str, ...]
    script: Path
    joblog: Path

    @property
    def is_array(self) -> bool:
        """Whether the submission is a job array."""
        return len(self.tasks) > 1


class _Live:
    """A submitted task's job and what the polls have seen of it: when it was submitted,
    first seen dispatched and first seen finished, and when and why the executor killed it."""

    def __init__(self, task: Task, job_id: str, joblog: Path, submitted_at: float) -> None:
        self.task = task
        self.job_id = job_id
        self.base_id = base_job_id(job_id)
        self.joblog = joblog
        self.submitted_at = submitted_at
        self.record: JobRecord | None = None
        self.running_since: float | None = None
        self.finished_since: float | None = None
        self.killed_at: float | None = None
        self.kill_reason = ""

    @property
    def killed(self) -> bool:
        """Whether the executor has killed the job."""
        return self.killed_at is not None


def run_scheduler_command(argv: Sequence[str], timeout_s: float) -> CommandResult:
    """Run one scheduler command and capture its output; never raises for its outcome."""
    try:
        proc = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(argv), 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(tuple(argv), None, "", f"no answer within {timeout_s:g} s")
    return CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)


def render_argv(
    template: Template, values: Mapping[str, str], lists: Mapping[str, Sequence[str]] | None = None
) -> list[str]:
    """An argv from a template.

    A token renders each ``{name}`` from ``values``; a token that is exactly one placeholder
    named in ``lists`` splices that list. An optional group renders whole when every
    placeholder in it has a value and is dropped otherwise. A token's placeholder without a
    value is an error.
    """
    lists = lists or {}
    out: list[str] = []
    for element in template:
        if isinstance(element, tuple):
            names = {name for token in element for name in PLACEHOLDER.findall(token)}
            if all(name in values or name in lists for name in names):
                out.extend(render_argv(element, values, lists))
            continue
        whole = PLACEHOLDER.fullmatch(element)
        if whole and whole.group(1) in lists:
            out.extend(lists[whole.group(1)])
            continue
        missing = [name for name in PLACEHOLDER.findall(element) if name not in values]
        if missing:
            raise SiteError(f"argv template needs a value for {{{missing[0]}}} in {element!r}")
        out.append(PLACEHOLDER.sub(lambda found: values[found.group(1)], element))
    return out


def resource_values(resources: Resources, mem_unit: str) -> dict[str, str]:
    """The submit placeholders a resource request sets; an unset value sets none."""
    values: dict[str, str] = {}
    if resources.queue:
        values["queue"] = resources.queue
    if resources.cores is not None:
        values["cores"] = str(resources.cores)
    if resources.mem_mb is not None:
        values["mem"] = str(mem_in_unit(resources.mem_mb, mem_unit))
        values["mem_mb"] = str(resources.mem_mb)
        values["mem_gb"] = str(mem_in_unit(resources.mem_mb, "GB"))
    if resources.walltime_min is not None:
        values["walltime"] = str(resources.walltime_min)
    return values


def parse_submit(stdout: str) -> str | None:
    """The job id bsub's answer names; None when it names none."""
    match = SUBMITTED.search(stdout)
    return match.group(1) if match else None


def base_job_id(job_id: str) -> str:
    """A job id without its array index: ``1001[3]`` is ``1001``."""
    return job_id.split("[", 1)[0]


def record_job_id(record: Mapping[str, object]) -> str:
    """The id a bjobs record describes, ``<id>[<index>]`` for an array element; JOBID may
    carry the index itself or leave it to JOBINDEX."""
    job_id = str(record.get("JOBID", "")).strip()
    index = str(record.get("JOBINDEX", "")).strip()
    if "[" not in job_id and index.isdigit() and int(index) > 0:
        return f"{job_id}[{index}]"
    return job_id


def parse_bjobs(result: CommandResult) -> dict[str, JobRecord] | None:
    """The jobs a ``bjobs -json`` answer describes, by job id; None when the answer is not one.

    A record with an ERROR field is a job the scheduler does not know, which the answer
    leaves out; the command's exit status says nothing about that.
    """
    text = result.stdout.strip()
    if not text.startswith("{"):
        quiet = not text and not result.stderr.strip()
        return {} if result.returncode == 0 and quiet else None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    records = data.get("RECORDS") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return None
    jobs: dict[str, JobRecord] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("ERROR"):
            continue
        code = str(record.get("EXIT_CODE") or "").strip()
        jobs[record_job_id(record)] = JobRecord(
            stat=str(record.get("STAT", "")).strip().upper(),
            exit_code=int(code) if code.isdigit() else None,
            reason=str(record.get("EXIT_REASON") or "").strip(),
            pend_reason=first_line(str(record.get("PEND_REASON") or "")),
        )
    return jobs


def first_line(text: str) -> str:
    """The first non-blank line of ``text``, stripped of the separators bjobs ends it with
    and cut to REASON_LIMIT characters."""
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line.rstrip(";").strip()[:REASON_LIMIT]


def wait_text(seconds: float) -> str:
    """A duration as hours and minutes, or seconds under a minute."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole} s"
    hours, minutes = divmod(whole // 60, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def statuses_complete(statuses: Sequence[StepStatus | None], count: int) -> bool:
    """Whether the steps reported through the last or through the first that failed."""
    for index in range(count):
        status = statuses[index] if index < len(statuses) else None
        if status is None:
            return False
        if status.rc != 0:
            return True
    return True


def read_statuses(path: Path, count: int) -> tuple[StepStatus | None, ...]:
    """Each step's status from a status file of ``<step> <rc> <start> <end>`` lines."""
    found: dict[int, StepStatus] = {}
    text = path.read_text(errors="replace") if path.is_file() else ""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 4 and all(re.fullmatch(r"-?\d+", f) for f in fields):
            index, rc, start, end = map(int, fields)
            if 1 <= index <= count:
                found[index] = StepStatus(rc, start, end)
    return tuple(found.get(index) for index in range(1, count + 1))


def task_script(task: Task, *, bench_dir: Path, status: Path, hook: Path | None = None) -> str:
    """The job script of one task: source the hook, run each step into its log in the
    bench, record its status, and stop at the first step that fails."""
    quote = shlex.quote
    lines = ["#!/usr/bin/env bash", f"# run_sim.py job {task.task_id}"]
    if hook is not None:
        # A hook's shell options must not stop the script before a step's status is written.
        lines += [f". {quote(str(hook))} || exit 2", "set +eu"]
    lines.append(f"cd {quote(str(bench_dir))} || exit 2")
    for index, step in enumerate(task.steps, start=1):
        lines += [
            f"mkdir -p {quote(str(step.log.parent))}",
            "start=$(date +%s)",
            f"{shlex.join(step.cmd)} > {quote(str(step.log))} 2>&1",
            "rc=$?",
            f'echo "{index} $rc $start $(date +%s)" >> {quote(str(status))}',
            '[ "$rc" -eq 0 ] || exit "$rc"',
        ]
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def array_script(label: str, tasks_file: Path, count: int) -> str:
    """The job script of an array: run the task script on the element's line of the list."""
    return (
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"# run_sim.py job array {label}: element ${ARRAY_INDEX_ENV} of {count}",
                f'task=$(sed -n "${{{ARRAY_INDEX_ENV}}}p" {shlex.quote(str(tasks_file))})',
                '[ -n "$task" ] || exit 2',
                'exec bash "$task"',
            ]
        )
        + "\n"
    )


def utc_stamp() -> str:
    """The current time in UTC, ISO 8601 to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class LsfExecutor:
    """Runs tasks as LSF jobs from one work directory, under one job-name prefix."""

    def __init__(
        self,
        site: LsfSite,
        *,
        work_dir: Path,
        bench_dir: Path,
        name: str,
        hook: Path | None = None,
        run_command: RunCommand = run_scheduler_command,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        notify: Callable[[str], None] = lambda line: None,
    ) -> None:
        self.site = site
        self.work_dir = work_dir
        self.bench_dir = bench_dir
        self.name = name
        self.hook = hook
        self._run_command = run_command
        self._clock = clock
        self._sleep = sleep
        self._notify = notify
        self._polls = 0
        self._arrays = 0
        self._live: dict[str, _Live] = {}
        self._done: dict[str, Outcome] = {}
        self._submitted: set[str] = set()
        self._submit_failures = 0
        self._query_failures = 0

    @property
    def binaries(self) -> tuple[str, ...]:
        """The scheduler commands the executor runs."""
        templates = (self.site.submit_argv, self.site.query_argv, self.site.cancel_argv)
        defaults = (SUBMIT_ARGV, QUERY_ARGV, CANCEL_ARGV)
        heads = (
            (custom or default)[0] for custom, default in zip(templates, defaults, strict=True)
        )
        return tuple(head for head in heads if isinstance(head, str))

    def plan(self, tasks: Sequence[Task]) -> list[Submission]:
        """The submissions ``tasks`` make while the cluster has room: the tasks that wait for
        none first, then the dependents of each task in turn. Writes nothing."""
        groups: dict[str | None, list[Task]] = {}
        for task in tasks:
            groups.setdefault(task.after, []).append(task)
        return [
            self._submission(chunk)
            for group in groups.values()
            for chunk in self.chunks(group, self.site.limits.max_in_flight)
        ]

    def chunks(self, tasks: Sequence[Task], room: int | None) -> list[tuple[Task, ...]]:
        """``tasks`` in submissions of one resource class each, build tasks first: arrays of
        at most array_chunk_size, single jobs when arrays are off, ``room`` jobs in all."""
        size = self.site.limits.array_chunk_size if self.site.arrays else 1
        left = len(tasks) if room is None else room
        chunks: list[tuple[Task, ...]] = []
        for build in (True, False):
            group = [task for task in tasks if task.build == build]
            while group and left > 0:
                take = min(size, left, len(group))
                chunks.append(tuple(group[:take]))
                group, left = group[take:], left - take
        return chunks

    def run(self, tasks: Sequence[Task], on_done: Callable[[Outcome], None]) -> None:
        """Submit ``tasks`` as their dependencies and the limits allow, and poll until every
        one has settled, calling ``on_done`` once per task as it settles."""
        for directory in (self._scripts, self._logs, self._status_dir):
            directory.mkdir(parents=True, exist_ok=True)
        pending = list(tasks)
        try:
            while True:
                pending = self._submit_ready(self._release_blocked(pending, on_done), on_done)
                if not self._live:
                    if pending:
                        raise SchedulerError(f"{len(pending)} tasks wait for tasks not in the run")
                    return
                backoff = min(2**self._query_failures, QUERY_BACKOFF_LIMIT)
                self._sleep(self.site.limits.poll_interval_s * backoff)
                self._poll(on_done)
        except BaseException:
            self.cancel_all()
            raise
        finally:
            self._write_record()

    def cancel_all(self) -> None:
        """Kill every queued or running job."""
        ids = list(dict.fromkeys(live.base_id for live in self._live.values()))
        self._cancel(ids)

    @property
    def _scripts(self) -> Path:
        return self.work_dir / "scripts"

    @property
    def _logs(self) -> Path:
        return self.work_dir / "logs"

    @property
    def _status_dir(self) -> Path:
        return self.work_dir / "status"

    def _status_path(self, task: Task) -> Path:
        return self._status_dir / f"{task.task_id}.status"

    def _resources(self, build: bool) -> tuple[Resources, tuple[str, ...]]:
        site = self.site
        if build:
            return site.build_defaults.over(site.defaults), (
                *site.extra_args,
                *site.build_extra_args,
            )
        return site.defaults, site.extra_args

    def _submission(self, tasks: Sequence[Task]) -> Submission:
        if len(tasks) == 1:
            label = tasks[0].task_id
            joblog, jobname = self._logs / f"{label}.log", f"{self.name}.{label}"
        else:
            self._arrays += 1
            label = f"arr{self._arrays:04d}"
            joblog = self._logs / f"{label}.{ARRAY_LOG_TOKEN}.log"
            jobname = f"{self.name}.{label}[1-{len(tasks)}]"
        script = self._scripts / f"{label}.sh"
        resources, extra = self._resources(tasks[0].build)
        values = {
            **resource_values(resources, self.site.mem_unit),
            "jobname": jobname,
            "joblog": str(joblog),
            "script": str(script),
        }
        argv = render_argv(self.site.submit_argv or SUBMIT_ARGV, values, {"extra_args": extra})
        return Submission(label, tuple(tasks), tuple(argv), script, joblog)

    def _release_blocked(
        self, pending: Sequence[Task], on_done: Callable[[Outcome], None]
    ) -> list[Task]:
        remaining: list[Task] = []
        for task in pending:
            waited = self._done.get(task.after) if task.after is not None else None
            if waited is not None and not waited.ok:
                reason = f"{task.after} did not succeed"
                self._finish(Outcome(task, reason=reason, blocked_by=waited), on_done)
            else:
                remaining.append(task)
        return remaining

    def _submit_ready(
        self, pending: Sequence[Task], on_done: Callable[[Outcome], None]
    ) -> list[Task]:
        ready = [
            task
            for task in pending
            if task.after is None or (task.after in self._done and self._done[task.after].ok)
        ]
        limit = self.site.limits.max_in_flight
        room = None if limit is None else max(0, limit - len(self._live))
        for chunk in self.chunks(ready, room):
            self._submit(self._submission(chunk), on_done)
        return [task for task in pending if task.task_id not in self._submitted]

    def _write_scripts(self, submission: Submission) -> None:
        for task in submission.tasks:
            status = self._status_path(task)
            status.unlink(missing_ok=True)
            script = self._scripts / f"{task.task_id}.sh"
            script.write_text(
                task_script(task, bench_dir=self.bench_dir, status=status, hook=self.hook)
            )
            script.chmod(0o755)
        if submission.is_array:
            tasks_file = self._scripts / f"{submission.label}.tasks"
            tasks_file.write_text(
                "".join(f"{self._scripts / task.task_id}.sh\n" for task in submission.tasks)
            )
            submission.script.write_text(
                array_script(submission.label, tasks_file, len(submission.tasks))
            )
            submission.script.chmod(0o755)

    def _submit(self, submission: Submission, on_done: Callable[[Outcome], None]) -> None:
        self._write_scripts(submission)
        result = self._command(submission.argv, "submit")
        job_id = parse_submit(result.stdout)
        self._submitted.update(task.task_id for task in submission.tasks)
        if job_id is None:
            self._submit_failures += 1
            for task in submission.tasks:
                reason = f"submission failed: {result.failure}"
                self._finish(Outcome(task, state="UNSUBMITTED", reason=reason), on_done)
            if self._submit_failures >= SUBMIT_FAILURE_LIMIT:
                raise SchedulerError(
                    f"{self._submit_failures} submissions in a row failed; last: {result.failure}"
                )
            return
        self._submit_failures = 0
        now = self._clock()
        for index, task in enumerate(submission.tasks, start=1):
            if submission.is_array:
                log_name = submission.joblog.name.replace(ARRAY_LOG_TOKEN, str(index))
                joblog = submission.joblog.with_name(log_name)
                live = _Live(task, f"{job_id}[{index}]", joblog, now)
            else:
                live = _Live(task, job_id, submission.joblog, now)
            self._live[task.task_id] = live

    def _query(self) -> dict[str, JobRecord] | None:
        records: dict[str, JobRecord] = {}
        ids = list(dict.fromkeys(live.base_id for live in self._live.values()))
        for start in range(0, len(ids), QUERY_BATCH):
            argv = render_argv(
                self.site.query_argv or QUERY_ARGV,
                {},
                {"job_ids": ids[start : start + QUERY_BATCH]},
            )
            answer = parse_bjobs(self._command(argv, "query"))
            if answer is None:
                return None
            records.update(answer)
        return records

    def _poll(self, on_done: Callable[[Outcome], None]) -> None:
        records = self._query()
        if records is None:
            self._query_failures += 1
            if self._query_failures >= QUERY_FAILURE_LIMIT:
                raise SchedulerError(f"{self._query_failures} job queries in a row failed")
            return
        self._query_failures = 0
        now = self._clock()
        for live in list(self._live.values()):
            self._observe(live, records.get(live.job_id), now, on_done)
        self._polls += 1
        if self._polls % PROGRESS_POLLS == 0 and self._live:
            self._notify(self._progress(now))

    def _progress(self, now: float) -> str:
        queued = [live for live in self._live.values() if self._queued(live)]
        line = (
            f"lsf: {len(self._live) - len(queued)} jobs dispatched, {len(queued)} queued,"
            f" {len(self._done)} tasks settled"
        )
        if queued:
            oldest = min(queued, key=lambda live: live.submitted_at)
            reason = oldest.record.pend_reason if oldest.record else ""
            line += f"; job {oldest.job_id} queued {wait_text(now - oldest.submitted_at)}"
            line += f": {reason or 'no pending reason reported'}"
        return line

    @staticmethod
    def _queued(live: _Live) -> bool:
        return live.running_since is None and (live.record is None or live.record.stat in QUEUED)

    def _observe(
        self, live: _Live, record: JobRecord | None, now: float, on_done: Callable[[Outcome], None]
    ) -> None:
        if record is not None:
            live.record = record
            if record.stat not in QUEUED and live.running_since is None:
                live.running_since = now
            if record.stat not in FINISHED:
                live.finished_since = None
                self._watch(live, record, now, on_done)
                return
        statuses = read_statuses(self._status_path(live.task), len(live.task.steps))
        if live.finished_since is None:
            live.finished_since = now
        waited = now - live.finished_since
        complete = statuses_complete(statuses, len(live.task.steps))
        if complete or waited >= self.site.limits.artifact_grace_s:
            self._settle(live, statuses, gone=record is None, on_done=on_done)

    def _watch(
        self, live: _Live, record: JobRecord, now: float, on_done: Callable[[Outcome], None]
    ) -> None:
        """Kill an unfinished job past its limit, and settle one the scheduler cannot end."""
        if live.killed or record.stat in LOST:
            statuses = read_statuses(self._status_path(live.task), len(live.task.steps))
            if statuses_complete(statuses, len(live.task.steps)):
                self._settle(live, statuses, gone=False, on_done=on_done)
                return
        if not live.killed:
            self._enforce_limits(live, record, now)
        if live.killed_at is not None and now - live.killed_at >= self.site.limits.kill_grace_s:
            statuses = read_statuses(self._status_path(live.task), len(live.task.steps))
            self._settle(live, statuses, gone=False, on_done=on_done)

    def _enforce_limits(self, live: _Live, record: JobRecord, now: float) -> None:
        limits, timeout = self.site.limits, live.task.timeout_s
        ran = None if live.running_since is None else now - live.running_since
        if record.stat in QUEUED and live.running_since is None:
            waited = now - live.submitted_at
            if waited > limits.max_pend_s:
                why = record.pend_reason or "no pending reason reported"
                self._kill(
                    live,
                    now,
                    f"queued {wait_text(waited)}, over max_pend_s {limits.max_pend_s:g} s"
                    f" ({record.stat}: {why}); killed job {live.job_id}",
                )
        elif record.stat == ZOMBIE:
            self._kill(live, now, f"job {live.job_id} went {ZOMBIE}; killed it")
        elif timeout is not None and ran is not None and ran > timeout:
            self._kill(live, now, f"timed out after {timeout:g} s; killed job {live.job_id}")

    def _kill(self, live: _Live, now: float, reason: str) -> None:
        live.killed_at, live.kill_reason = now, reason
        self._notify(f"lsf: task {live.task.task_id}: {reason}")
        self._cancel([live.job_id])

    def _settle(
        self,
        live: _Live,
        statuses: tuple[StepStatus | None, ...],
        *,
        gone: bool,
        on_done: Callable[[Outcome], None],
    ) -> None:
        del self._live[live.task.task_id]
        record = live.record
        state = "GONE" if gone else (record.stat if record else "")
        reason = ""
        if not statuses_complete(statuses, len(live.task.steps)):
            if live.killed:
                reason = live.kill_reason
                if not gone and state not in FINISHED:
                    grace = self.site.limits.kill_grace_s
                    reason += f"; still {state} {grace:g} s after the kill"
            elif gone:
                reason = f"job {live.job_id} left the scheduler without an exit status"
            else:
                detail = f" ({record.reason})" if record and record.reason else ""
                reason = f"job {live.job_id} ended {state}{detail} without an exit status"
            reason += f"; job log {live.joblog}"
        outcome = Outcome(live.task, statuses, live.job_id, state, reason, live.joblog)
        self._finish(outcome, on_done)

    def _finish(self, outcome: Outcome, on_done: Callable[[Outcome], None]) -> None:
        self._done[outcome.task.task_id] = outcome
        on_done(outcome)

    def _cancel(self, job_ids: Sequence[str]) -> None:
        for start in range(0, len(job_ids), QUERY_BATCH):
            batch = job_ids[start : start + QUERY_BATCH]
            self._command(
                render_argv(self.site.cancel_argv or CANCEL_ARGV, {}, {"job_ids": batch}), "cancel"
            )

    def _command(self, argv: Sequence[str], purpose: str) -> CommandResult:
        result = self._run_command(argv, self.site.limits.command_timeout_s)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with (self.work_dir / EXECUTOR_LOG).open("a") as log:
            log.write(f"[{utc_stamp()}] {purpose} rc={result.returncode}: {shlex.join(argv)}\n")
            for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
                if text.strip():
                    log.write(f"  {name}: {text.strip()[:OUTPUT_LOG_LIMIT]}\n")
        return result

    def _write_record(self) -> None:
        rows = [
            {
                "task": outcome.task.task_id,
                "job_id": outcome.job_id,
                "state": outcome.state,
                "reason": outcome.reason,
                "joblog": None if outcome.joblog is None else str(outcome.joblog),
            }
            for outcome in self._done.values()
        ]
        rows += [
            {
                "task": live.task.task_id,
                "job_id": live.job_id,
                "state": "CANCELLED",
                "reason": "the run stopped before the job finished",
                "joblog": str(live.joblog),
            }
            for live in self._live.values()
        ]
        self.work_dir.mkdir(parents=True, exist_ok=True)
        (self.work_dir / JOBS_RECORD).write_text(json.dumps(rows, indent=1) + "\n")
