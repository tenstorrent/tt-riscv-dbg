# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tdr2axi bring-up and smoke: seeded clocks, reset length and subordinate timing.

Bring-up brings the DUT up on the build the Makefile selects and checks it idles cleanly:
the scoreboard grades the first capture after reset, the dmactive read back and the sbcs
reset fields against the models, and the subordinate ends drained with no transaction.
Smoke adds the port list and the Bender build, the DM and SBA versions the scoreboard
grades, and one SBA write and read.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

import cocotb
from cocotb.handle import SimHandleBase

from env.tdr_host import DMCONTROL, DMSTATUS, SBCS, DmiOp, tdr_word
from tests.tdr2axi_base_test import TIMEOUT_MS, Tdr2axiTb

BENCH_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BENCH_DIR.parents[1]
WORD = 2
SMOKE_OFFSET = 0x1_0000
SMOKE_WORDS = 64
TDR2AXI_TARGET_DEFINE = "+define+TARGET_TDR2AXI"
HDL_SUFFIXES = frozenset({".sv", ".svh", ".v", ".vh"})
# A VCS compile log names each file it reads in one of these lines
VCS_PARSED_FILE = re.compile(r"^Parsing (?:design|included) file '([^']+)'", re.MULTILINE)
# Verilator's own packages, which every model reads
SIM_INCLUDE_MARK = "verilator"
# Port tables of doc/tdr2axi.md: name, direction, type and width; a struct port's width is
# that of its type parameter
TDR_PORTS = {
    "i_tck": ("input", "logic", 1),
    "i_dmi_tdr": ("input", "logic", 41),
    "i_dmi_tdr_capture": ("input", "logic", 1),
    "i_dmi_tdr_update": ("input", "logic", 1),
    "o_dmi_tdr": ("output", "logic", 41),
}
BUS_PORTS = {
    "i_clk": ("input", "logic", 1),
    "i_arst_n": ("input", "logic", 1),
    "i_test_mode": ("input", "logic", 1),
    "i_arst_scan_n": ("input", "logic", 1),
    "o_axi_req": ("output", "axi_req_t", None),
    "i_axi_rsp": ("input", "axi_rsp_t", None),
}


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_bringup_test(dut: SimHandleBase) -> None:
    """Reset, a NOP scan, dmactive, sbcs, and an idle bus; the scoreboard grades the first
    capture after reset (CHK-RESET-ASYNC-ANY-TIME), the dmcontrol read
    (CHK-DM-REGISTER-MAP) and the sbcs read (CHK-SBA-SBCS-FIELDS)."""
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_bringup_test", dmactive=False)
    await tb.scan(tdr_word(DmiOp.NOP, 0, 0))
    await tb.dmi_write(DMCONTROL, 1)
    await tb.dmi_read(DMCONTROL)
    await tb.dmi_read(SBCS)
    tb.finish()


@cocotb.test(timeout_time=TIMEOUT_MS, timeout_unit="ms")
async def tdr2axi_smoke_test(dut: SimHandleBase) -> None:
    """The port list and the Bender build before the first scan, then dmactive, dmstatus,
    sbcs, and one 32-bit SBA write and read of the same seeded address, in a 256-byte range
    or in the whole address space where that is smaller.

    Before any scan the tdr2axi ports, as declared in the source the build compiled and as
    elaborated in tb_top, must be exactly those of the port tables of doc/tdr2axi.md
    (CHK-PORT-TDR-LIST, CHK-PORT-BUS-LIST), and the model must be built from the Bender file
    list of target tdr2axi (CHK-CFG-BENDER-TARGET). The scoreboard grades dmstatus.version,
    authenticated and authbusy and sbcs.sbversion (CHK-DM-VERSION) and the sbcs reset
    fields (CHK-SBA-SBCS-FIELDS) against the SBA model, and the write and the read each give
    exactly one bus transaction, which the scoreboard compares with the SBA model
    (CHK-SBA-SINGLE-WRITE, CHK-SBA-SINGLE-READ).
    """
    tb = await Tdr2axiTb.setup(dut, "tdr2axi_smoke_test", dmactive=False)
    build_dir = _build_dir()
    _check_ports(tb, build_dir)
    _check_bender_target(tb, build_dir)
    await tb.dmi_write(DMCONTROL, 1)
    await tb.dmi_read(DMSTATUS)
    await tb.dmi_read(SBCS)
    base, span = tb.address_range(SMOKE_OFFSET, SMOKE_WORDS * 4)
    tb.mem_fill(base, span)
    addr = base + 4 * tb.rng.randrange(span // 4)
    data = tb.rand_data(WORD)
    tb.log.info("smoke access: 0x%08x at 0x%x", data, addr)
    tb.clear_monitor()
    await tb.sba_write(addr, WORD, data)
    tb.expect_write(addr, WORD, data, check_id="CHK-SBA-SINGLE-WRITE")
    tb.expect_no_bus("CHK-SBA-SINGLE-WRITE", f"write @0x{addr:x}")
    tb.ref_write(addr, WORD, data)
    tb.mem_check(base, span, check_id="CHK-SBA-SINGLE-WRITE")
    got = await tb.sba_read(addr, WORD)
    tb.expect_read(addr, WORD, check_id="CHK-SBA-SINGLE-READ")
    tb.expect_no_bus("CHK-SBA-SINGLE-READ", f"read @0x{addr:x}")
    tb.checker.expect_equal("CHK-SBA-SINGLE-READ", got, data, f"read @0x{addr:x}")
    tb.finish()


def _build_dir() -> Path:
    """The directory of the model this run simulates, which holds its Bender file list."""
    sim_build = os.environ.get("SIM_BUILD")
    return Path(sim_build) if sim_build else Path(cocotb.argv[0]).parent


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL))


def _module_files(paths: Iterable[Path], module: str) -> list[Path]:
    """The files among ``paths`` that declare ``module``."""
    pattern = re.compile(rf"^\s*module\s+{module}\b", re.MULTILINE)
    return [p for p in paths if pattern.search(_strip_comments(p.read_text(errors="replace")))]


def _balanced(text: str, start: int) -> tuple[str, int]:
    """The text inside the parentheses opening at ``start``, and the index after them."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if not depth:
                return text[start + 1 : index], index + 1
    raise ValueError("unbalanced parentheses in a module header")


def _declared_ports(source: Path, module: str) -> dict[str, tuple[str, str, int | None]]:
    """The ANSI port list of ``module`` in ``source``: name to direction, type and width,
    the width None for a port of a type parameter."""
    text = _strip_comments(source.read_text())
    match = re.search(rf"\bmodule\s+{module}\b", text)
    if match is None:
        return {}
    cursor = match.end()
    params = re.match(r"\s*#\s*\(", text[cursor:])
    if params is not None:
        _, cursor = _balanced(text, cursor + params.end() - 1)
    header, _ = _balanced(text, text.index("(", cursor))
    ports: dict[str, tuple[str, str, int | None]] = {}
    direction, kind, width = "", "", None
    for decl in (part.split() for part in header.replace("[", " [").split(",")):
        if decl and decl[0] in ("input", "output", "inout"):
            direction, decl = decl[0], decl[1:]
            kind, width = (decl[0], 1) if len(decl) > 1 else ("logic", 1)
            decl = decl[1:] if len(decl) > 1 else decl
        packed = "".join(w for w in decl if w.startswith("["))
        name = decl[-1]
        if packed:
            bounds = packed.strip("[]").split(":")
            literal = len(bounds) == 2 and all(b.strip().isdigit() for b in bounds)
            width = abs(int(bounds[0]) - int(bounds[1])) + 1 if literal else None
        elif kind not in ("logic", "wire", "bit"):
            width = None
        ports[name] = (direction, kind, width)
    return ports


def _check_ports(tb: Tdr2axiTb, build_dir: Path) -> None:
    """The tdr2axi ports, as declared in the compiled source and as elaborated, are exactly
    those of the port tables."""
    listed = [Path(p) for p in _flist(build_dir) if not p.startswith("+")]
    sources = _module_files(listed, "tdr2axi")
    tb.checker.expect_true(
        "CHK-PORT-TDR-LIST",
        passed=len(sources) == 1,
        detail=f"the file list declares module tdr2axi in {[str(s) for s in sources]}",
    )
    declared = _declared_ports(sources[0], "tdr2axi")
    inst = tb.elaborated_dut
    tb.log.info("tdr2axi ports declared in %s: %s", sources[0], declared)
    for check_id, table in (("CHK-PORT-TDR-LIST", TDR_PORTS), ("CHK-PORT-BUS-LIST", BUS_PORTS)):
        for name, (direction, kind, width) in table.items():
            tb.checker.expect_true(
                check_id,
                passed=declared.get(name) == (direction, kind, width),
                detail=f"port {name}: declared {declared.get(name)}, port table "
                f"{(direction, kind, width)}",
            )
            elaborated = getattr(inst, name, None)
            tb.checker.expect_true(
                check_id,
                passed=elaborated is not None and (width is None or len(elaborated) == width),
                detail=f"port {name} of the elaborated tdr2axi: "
                f"{None if elaborated is None else len(elaborated)} bits, port table {width}",
            )
    extra = sorted(set(declared) - TDR_PORTS.keys() - BUS_PORTS.keys())
    tb.checker.expect_true(
        "CHK-PORT-TDR-LIST",
        passed=not extra,
        detail=f"ports beyond the port tables, such as a TAP, shift or dtmcs port: {extra}",
    )


def _flist(build_dir: Path) -> list[str]:
    """The words of the Bender file list, without the // comment lines some bender
    releases write into it."""
    return _strip_comments((build_dir / "sources.f").read_text()).split()


def _compiled_files(build_dir: Path) -> list[Path] | None:
    """The HDL files the model was compiled from: Verilator's dependency record, else the
    files VCS reports parsing in its compile log beside the model."""
    record = build_dir / "Vtop__ver.d"
    if record.exists():
        tokens = record.read_text().split()
        inputs = tokens[tokens.index(":") + 1 :] if ":" in tokens else []
        return [Path(t) for t in inputs if Path(t).suffix in HDL_SUFFIXES]
    log = build_dir / "vcs_compile.log"
    if not log.exists():
        return None
    parsed = VCS_PARSED_FILE.findall(log.read_text(errors="replace"))
    files = [build_dir / p for p in dict.fromkeys(parsed) if Path(p).suffix in HDL_SUFFIXES]
    return files or None


def _check_bender_target(tb: Tdr2axiTb, build_dir: Path) -> None:
    """The file list is Bender's for target tdr2axi and lists every source under
    src/tdr2axi; the model compiled tdr2axi and its bridge from that list, and every other
    HDL file it read outside the bench comes from the list or its include paths."""
    check_id = "CHK-CFG-BENDER-TARGET"
    flist = _flist(build_dir)
    listed = {Path(p).resolve() for p in flist if not p.startswith("+")}
    incdirs = [
        Path(p.removeprefix("+incdir+")).resolve() for p in flist if p.startswith("+incdir+")
    ]
    tb.checker.expect_true(
        check_id,
        passed=TDR2AXI_TARGET_DEFINE in flist,
        detail=f"{build_dir / 'sources.f'} lacks {TDR2AXI_TARGET_DEFINE}",
    )
    expected = sorted(p.resolve() for p in (REPO_DIR / "src" / "tdr2axi").glob("*.sv"))
    missing = [str(p) for p in expected if p not in listed]
    tb.checker.expect_true(
        check_id,
        passed=bool(expected) and not missing,
        detail=f"src/tdr2axi sources missing from the file list: {missing}",
    )
    compiled = _compiled_files(build_dir)
    tb.checker.expect_true(
        check_id,
        passed=compiled is not None,
        detail=f"no record of the files the model in {build_dir} was compiled from",
    )
    resolved = [p.resolve() for p in compiled or ()]
    for module in ("tdr2axi", "dmi_tdr_bridge"):
        found = _module_files(resolved, module)
        tb.checker.expect_true(
            check_id,
            passed=len(found) == 1 and found[0] in expected,
            detail=f"module {module} compiled from {[str(p) for p in found]}",
        )
    stray = [
        str(p)
        for p in resolved
        if p not in listed
        and not p.is_relative_to(BENCH_DIR)
        and not any(p.is_relative_to(d) for d in incdirs)
        and SIM_INCLUDE_MARK not in p.parts
    ]
    tb.checker.expect_true(
        check_id,
        passed=not stray,
        detail=f"HDL files compiled from outside the Bender file list and the bench: {stray}",
    )
    tb.checker.expect_true(
        check_id,
        passed=tb.elaborated_dut._def_name == "tdr2axi",
        detail=f"the DUT instance is of module {tb.elaborated_dut._def_name}",
    )
