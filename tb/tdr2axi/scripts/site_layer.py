# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The site layer of run_sim.py: scheduler and simulator settings kept outside the repository.

run_sim.py reads one TOML file, the one TDR2AXI_DV_SITE names, else site.local.toml beside
the runner when it exists; git ignores that name. Without either file no site value reaches
a command. site.local.example.toml beside the runner shows every key:

    schema_version = 1
    [lsf]                    argv templates, extra arguments, memory unit, job arrays
    [lsf.defaults]           queue, cores, mem_mb and walltime of every job
    [lsf.build_defaults]     the same keys for build, build-check and merge jobs
    [lsf.limits]             max_in_flight, poll_interval_s, array_chunk_size, ...
    [simulators.<sim>]       setup_hook, compile and run arguments, waves, coverage merge,
                             and on VCS the cover-hit merge

A path in the file (setup_hook, wave_sources) is relative to the file's directory. Imported
by run_sim.py and lsf_executor.py; the tests run with

    python3 -m unittest discover -s tb/tdr2axi/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Any

SITE_ENV = "TDR2AXI_DV_SITE"
SITE_FILE_NAME = "site.local.toml"
SCHEMA_VERSION = 1
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
# A template element is a token or an optional group of tokens, which renders only when
# every placeholder in it has a value.
Template = tuple[str | tuple[str, ...], ...]

TOP_KEYS = frozenset({"schema_version", "description", "lsf", "simulators"})
LSF_KEYS = frozenset(
    {
        "submit_argv",
        "query_argv",
        "cancel_argv",
        "extra_args",
        "build_extra_args",
        "mem_unit",
        "arrays",
        "defaults",
        "build_defaults",
        "limits",
    }
)
RESOURCE_KEYS = frozenset({"queue", "cores", "mem_mb", "walltime"})
LIMIT_KEYS = frozenset(
    {
        "max_in_flight",
        "poll_interval_s",
        "array_chunk_size",
        "artifact_grace_s",
        "command_timeout_s",
        "max_pend_s",
        "kill_grace_s",
    }
)
COMMON_SIM_KEYS = frozenset(
    {"setup_hook", "cocotb_major", "compile_args", "cov_merge_argv", "cov_merge_output"}
)
# Verilator's run arguments and waves come from the Makefile and the runner, and its cover
# hits from each run's coverage.dat.
VCS_SIM_KEYS = COMMON_SIM_KEYS | {
    "sim_args",
    "wave_compile_args",
    "wave_sources",
    "wave_sim_args",
    "wave_file",
    "cov_hits_argv",
}
SIM_KEYS = {"verilator": COMMON_SIM_KEYS, "vcs": VCS_SIM_KEYS}

# The placeholders each template may use, and those it must use outside an optional group.
SUBMIT_PLACEHOLDERS = frozenset(
    {"queue", "cores", "mem", "mem_mb", "mem_gb", "walltime", "jobname", "joblog"}
    | {"extra_args", "script"}
)
JOB_ID_PLACEHOLDERS = frozenset({"job_ids"})
MERGE_PLACEHOLDERS = frozenset({"output", "inputs", "inputs_file", "merge_dir"})
HITS_PLACEHOLDERS = frozenset({"inputs", "inputs_file", "report_dir", "merge_dir"})
WAVE_PLACEHOLDERS = frozenset({"wave_file", "run_dir"})
# Megabytes per unit of {mem}: LSF reads rusage[mem=...] in the cluster's LSF_UNIT_FOR_LIMITS.
MEM_UNITS = {"KB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1024 * 1024}


class SiteError(Exception):
    """A site file the runner cannot use."""


@dataclass(frozen=True)
class Resources:
    """A job's resource request; None leaves the template's group for that value out."""

    queue: str | None = None
    cores: int | None = None
    mem_mb: int | None = None
    walltime_min: int | None = None

    def over(self, base: Resources) -> Resources:
        """These values where they are set, ``base``'s elsewhere."""
        mine, theirs = astuple(self), astuple(base)
        return Resources(*(m if m is not None else t for m, t in zip(mine, theirs, strict=True)))


@dataclass(frozen=True)
class Limits:
    """How the executor paces the scheduler; ``max_in_flight`` None is unbounded.

    A job queued longer than ``max_pend_s`` is killed, and a killed job the scheduler reports
    unfinished ``kill_grace_s`` later settles without it.
    """

    max_in_flight: int | None = None
    poll_interval_s: float = 30.0
    array_chunk_size: int = 100
    artifact_grace_s: float = 60.0
    command_timeout_s: float = 120.0
    max_pend_s: float = 21600.0
    kill_grace_s: float = 300.0


@dataclass(frozen=True)
class LsfSite:
    """The [lsf] table: templates that replace the executor's own, and job settings."""

    submit_argv: Template | None = None
    query_argv: Template | None = None
    cancel_argv: Template | None = None
    extra_args: tuple[str, ...] = ()
    build_extra_args: tuple[str, ...] = ()
    mem_unit: str = "MB"
    arrays: bool = True
    defaults: Resources = Resources()
    build_defaults: Resources = Resources()
    limits: Limits = Limits()


@dataclass(frozen=True)
class SimSite:
    """One [simulators.<sim>] table.

    ``setup_hook`` is sourced by every scheduler job before make runs, and ``cocotb_major``
    is the cocotb release it provides, for a submitting host without cocotb. ``compile_args`` join
    every build's compile arguments and ``sim_args`` every VCS run's. Under --wave on VCS,
    ``wave_compile_args`` and ``wave_sources`` join the wave build and ``wave_sim_args``,
    with ``{wave_file}`` and ``{run_dir}``, replace the runner's VCD plusarg; ``wave_file``
    names the dump in the run directory. ``cov_merge_argv`` is the --cov-merge command and
    ``cov_merge_output`` the merged database's name. ``cov_hits_argv`` merges one test's VCS
    databases under --cov and writes their text assertion report into ``{report_dir}``.
    """

    setup_hook: Path | None = None
    cocotb_major: int | None = None
    compile_args: tuple[str, ...] = ()
    sim_args: tuple[str, ...] = ()
    wave_compile_args: tuple[str, ...] = ()
    wave_sources: tuple[Path, ...] = ()
    wave_sim_args: tuple[str, ...] = ()
    wave_file: str | None = None
    cov_merge_argv: tuple[str, ...] = ()
    cov_merge_output: str | None = None
    cov_hits_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class Site:
    """The active site file's settings; ``path`` None when no site file is active."""

    path: Path | None = None
    lsf: LsfSite = LsfSite()
    simulators: tuple[tuple[str, SimSite], ...] = ()

    def sim(self, name: str) -> SimSite:
        """The settings of simulator ``name``, empty when the file has none."""
        return dict(self.simulators).get(name, SimSite())


def site_path(env_value: str, default: Path) -> Path | None:
    """The active site file: the one ``env_value`` names, else ``default`` when it exists.

    A file the environment names must exist; None when neither names a file.
    """
    text = env_value.strip()
    if text:
        path = Path(text).expanduser()
        if not path.is_file():
            raise SiteError(f"{SITE_ENV} names {path}, which is not a file")
        return path.resolve()
    return default if default.is_file() else None


def load_site(env_value: str, default: Path) -> Site:
    """The settings of the active site file; an empty Site when none is active."""
    path = site_path(env_value, default)
    if path is None:
        return Site()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SiteError(f"{path}: {exc}") from exc
    return parse_site(data, path=path)


def parse_site(data: Mapping[str, Any], *, path: Path) -> Site:
    """A site file's tables, every key checked; paths resolve against ``path``'s directory."""
    where = str(path)
    _check_keys(data, TOP_KEYS, where)
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise SiteError(f"{where}: schema_version must be {SCHEMA_VERSION}")
    lsf = parse_lsf(_table(data, "lsf", where), f"{where} [lsf]")
    simulators: list[tuple[str, SimSite]] = []
    for name, table in _table(data, "simulators", where).items():
        if name not in SIM_KEYS:
            raise SiteError(f"{where}: [simulators.{name}] names none of {sorted(SIM_KEYS)}")
        if not isinstance(table, dict):
            raise SiteError(f"{where}: [simulators.{name}] must be a table")
        sim_where = f"{where} [simulators.{name}]"
        simulators.append((name, parse_simulator(table, name, where=sim_where, base=path.parent)))
    return Site(path, lsf, tuple(simulators))


def parse_lsf(table: Mapping[str, Any], where: str) -> LsfSite:
    """The [lsf] table and its defaults, build_defaults and limits."""
    _check_keys(table, LSF_KEYS, where)
    mem_unit = table.get("mem_unit", "MB")
    if mem_unit not in MEM_UNITS:
        raise SiteError(f"{where}: mem_unit must be one of {' '.join(MEM_UNITS)}")
    arrays = table.get("arrays", True)
    if not isinstance(arrays, bool):
        raise SiteError(f"{where}: arrays must be true or false")
    return LsfSite(
        submit_argv=_template(table, "submit_argv", where, SUBMIT_PLACEHOLDERS, {"script"}),
        query_argv=_template(table, "query_argv", where, JOB_ID_PLACEHOLDERS, {"job_ids"}),
        cancel_argv=_template(table, "cancel_argv", where, JOB_ID_PLACEHOLDERS, {"job_ids"}),
        extra_args=_strings(table, "extra_args", where),
        build_extra_args=_strings(table, "build_extra_args", where),
        mem_unit=mem_unit,
        arrays=arrays,
        defaults=parse_resources(_table(table, "defaults", where), f"{where}.defaults"),
        build_defaults=parse_resources(
            _table(table, "build_defaults", where), f"{where}.build_defaults"
        ),
        limits=parse_limits(_table(table, "limits", where), f"{where}.limits"),
    )


def parse_walltime(value: object, where: str) -> int:
    """A wall time in minutes: an integer of minutes, or ``"H:MM"``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    match = re.fullmatch(r"(\d+):([0-5]\d)", value) if isinstance(value, str) else None
    if match is None or (int(match.group(1)), int(match.group(2))) == (0, 0):
        raise SiteError(f"{where}: walltime must be minutes or H:MM, more than zero")
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_resources(table: Mapping[str, Any], where: str) -> Resources:
    """A defaults or build_defaults table."""
    _check_keys(table, RESOURCE_KEYS, where)
    queue = table.get("queue")
    if queue is not None and (not isinstance(queue, str) or not queue):
        raise SiteError(f"{where}: queue must be a non-empty string")
    walltime = table.get("walltime")
    return Resources(
        queue=queue,
        cores=_positive_int(table, "cores", where),
        mem_mb=_positive_int(table, "mem_mb", where),
        walltime_min=None if walltime is None else parse_walltime(walltime, where),
    )


def parse_limits(table: Mapping[str, Any], where: str) -> Limits:
    """The limits table; an unset key keeps its Limits default."""
    _check_keys(table, LIMIT_KEYS, where)
    base = Limits()
    numbers: dict[str, float] = {}
    seconds = ("poll_interval_s", "artifact_grace_s", "command_timeout_s", "max_pend_s")
    for key in (*seconds, "kill_grace_s"):
        value = table.get(key, getattr(base, key))
        if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
            raise SiteError(f"{where}: {key} must be a number of seconds, zero or more")
        numbers[key] = float(value)
    nonzero = ("poll_interval_s", "command_timeout_s", "max_pend_s")
    if any(numbers[key] == 0 for key in nonzero):
        raise SiteError(f"{where}: {', '.join(nonzero)} must be more than zero")
    return Limits(
        max_in_flight=_positive_int(table, "max_in_flight", where),
        poll_interval_s=numbers["poll_interval_s"],
        array_chunk_size=_positive_int(table, "array_chunk_size", where) or base.array_chunk_size,
        artifact_grace_s=numbers["artifact_grace_s"],
        command_timeout_s=numbers["command_timeout_s"],
        max_pend_s=numbers["max_pend_s"],
        kill_grace_s=numbers["kill_grace_s"],
    )


def parse_simulator(table: Mapping[str, Any], name: str, *, where: str, base: Path) -> SimSite:
    """One [simulators.<name>] table, its paths resolved against ``base``."""
    _check_keys(table, SIM_KEYS[name], where)
    hook = table.get("setup_hook")
    merge = _plain_template(table, "cov_merge_argv", where, MERGE_PLACEHOLDERS, {"output"})
    hits = _plain_template(table, "cov_hits_argv", where, HITS_PLACEHOLDERS, {"report_dir"})
    wave_args = _template(table, "wave_sim_args", where, WAVE_PLACEHOLDERS, set())
    return SimSite(
        setup_hook=None if hook is None else _existing_file(hook, base, f"{where}.setup_hook"),
        cocotb_major=_positive_int(table, "cocotb_major", where),
        compile_args=_strings(table, "compile_args", where),
        sim_args=_strings(table, "sim_args", where),
        wave_compile_args=_strings(table, "wave_compile_args", where),
        wave_sources=tuple(
            _existing_file(source, base, f"{where}.wave_sources")
            for source in _strings(table, "wave_sources", where)
        ),
        wave_sim_args=tuple(str(token) for token in wave_args or ()),
        wave_file=_file_name(table, "wave_file", where),
        cov_merge_argv=merge,
        cov_merge_output=_file_name(table, "cov_merge_output", where),
        cov_hits_argv=hits,
    )


def mem_in_unit(mem_mb: int, unit: str) -> int:
    """``mem_mb`` megabytes in ``unit``, rounded up to a whole unit."""
    return max(1, math.ceil(mem_mb / MEM_UNITS[unit]))


def template_placeholders(template: Template) -> set[str]:
    """Every placeholder a template names, inside optional groups or not."""
    tokens = [token for element in template for token in _group(element)]
    return {name for token in tokens for name in PLACEHOLDER.findall(token)}


def _group(element: str | tuple[str, ...]) -> tuple[str, ...]:
    return (element,) if isinstance(element, str) else element


def _check_keys(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise SiteError(f"{where}: unknown keys {unknown}; known: {sorted(allowed)}")


def _table(table: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = table.get(key, {})
    if not isinstance(value, dict):
        raise SiteError(f"{where}: {key} must be a table")
    return value


def _strings(table: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SiteError(f"{where}: {key} must be a list of non-empty strings")
    return tuple(value)


def _positive_int(table: Mapping[str, Any], key: str, where: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SiteError(f"{where}: {key} must be a whole number, 1 or more")
    return value


def _file_name(table: Mapping[str, Any], key: str, where: str) -> str | None:
    value = table.get(key)
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[\w.-]+", value)):
        raise SiteError(f"{where}: {key} must be a file name without a directory")
    return value


def _existing_file(text: object, base: Path, where: str) -> Path:
    if not isinstance(text, str) or not text:
        raise SiteError(f"{where} must be a non-empty path")
    path = Path(text).expanduser()
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_file():
        raise SiteError(f"{where}: {path} is not a file")
    return path


def _plain_template(
    table: Mapping[str, Any],
    key: str,
    where: str,
    allowed: frozenset[str],
    required: set[str],
) -> tuple[str, ...]:
    """An argv template without optional groups; empty when the table has none."""
    template = _template(table, key, where, allowed, required)
    if template is not None and any(isinstance(element, tuple) for element in template):
        raise SiteError(f"{where}: {key} takes no optional groups")
    return tuple(str(token) for token in template or ())


def _template(
    table: Mapping[str, Any],
    key: str,
    where: str,
    allowed: frozenset[str],
    required: set[str],
) -> Template | None:
    """An argv template: tokens and optional groups (lists of tokens) over ``allowed``
    placeholders, with every ``required`` one outside the groups."""
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise SiteError(f"{where}: {key} must be a non-empty list")
    elements: list[str | tuple[str, ...]] = []
    for element in value:
        if isinstance(element, str):
            elements.append(element)
        elif isinstance(element, list) and element and all(isinstance(t, str) for t in element):
            elements.append(tuple(element))
        else:
            raise SiteError(f"{where}: {key} holds strings and lists of strings only")
    template = tuple(elements)
    unknown = sorted(template_placeholders(template) - allowed)
    if unknown:
        raise SiteError(f"{where}: {key} names unknown placeholders {unknown}")
    plain = template_placeholders(tuple(e for e in template if isinstance(e, str)))
    missing = sorted(required - plain)
    if missing:
        raise SiteError(f"{where}: {key} needs {', '.join(f'{{{m}}}' for m in missing)}")
    return template
