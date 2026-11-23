# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# cocotb testbench for tdr2axi (see doc/tdr2axi.md).
#
#   make                                  # default CONFIG (axi4_a64_d64) with Verilator
#   make CONFIG=axil_a32_d32              # one configuration
#   make CONFIG=axi4_a64_d64_slowclk      # a build at another i_clk to TCK ratio
#   make CLK_PERIOD_PS=3000               # any ratio: the clock periods are run-time knobs
#   make CLK_PERIOD_PS=random             # i_clk period drawn from the seed, 20:1 to 1:8 of TCK
#   make sim_all                          # every configuration
#   make bringup_all SEEDS="1 2 3 4 5"    # bring-up on every build and seed
#   make SIM=vcs                          # Synopsys VCS instead of Verilator
#   make TESTCASE=tdr2axi_sba_single_test # one test
#   make RANDOM_SEED=7                    # replay a seed
#   make TDR2AXI_RANDOMIZE=0              # the knob clocks and fixed subordinate timing
#   make TDR2AXI_CLOCK_JITTER=1           # both clock periods drawn within 20 % of the knobs
#   make WAVES=1                          # FST waves, from a separate traced build
#
# Needs cocotb (1.9), bender, and Verilator 5.036 or later (or VCS).

SIM           ?= verilator
TOPLEVEL_LANG  = verilog
TOPLEVEL       = tb_top
MODULE         = tests.test_tdr2axi,tests.test_tdr2axi_dm,tests.test_tdr2axi_sba,tests.test_tdr2axi_tdr,tests.test_tdr2axi_reset,tests.test_tdr2axi_axi,tests.test_tdr2axi_clock,tests.test_tdr2axi_dmactive
COCOTB_TEST_MODULES = $(MODULE)

COCOTB_HDL_TIMEUNIT      = 1ps
COCOTB_HDL_TIMEPRECISION = 1ps

# Builds, one compile each: USE_AXI_LITE AXI_ADDR_WIDTH AXI_DATA_WIDTH AXI_PROT AXI_CACHE
# SYNC_STAGES AXI_REQ_DATA_WIDTH AXI_RSP_DATA_WIDTH AXI_DEFAULT_TYPES. AXI_PROT and AXI_CACHE
# are decimal: 2 = 3'b010, 5 = 3'b101, 15 = 4'b1111. A struct data width of 0 is
# AXI_DATA_WIDTH; any value other than 0 or AXI_DATA_WIDTH, or AXI_DEFAULT_TYPES = 1, builds
# struct types tdr2axi rejects.
BUILDS := axi4_a64_d64 axi4_a64_d32 axi4_a32_d32 axil_a64_d64 axil_a64_d32 axil_a32_d32 \
          axi4_a48_d64 axi4_a2_d32_cachef_sync4 axil_a2_d32_prot5 axi4_a3_d32_cachef \
          axil_a3_d64_prot5_sync4 axil_a32_d64_prot5_cachef_sync4 \
          axi4_a64_d64_prot5_cachef_sync4 axil_a48_d32_prot5_cachef_sync4
build_axi4_a64_d64                    := 0 64 64 2 0 3 0 0 0
build_axi4_a64_d32                    := 0 64 32 2 0 3 0 0 0
build_axi4_a32_d32                    := 0 32 32 2 0 3 0 0 0
build_axil_a64_d64                    := 1 64 64 2 0 3 0 0 0
build_axil_a64_d32                    := 1 64 32 2 0 3 0 0 0
build_axil_a32_d32                    := 1 32 32 2 0 3 0 0 0
build_axi4_a48_d64                    := 0 48 64 2 0 3 0 0 0
build_axi4_a2_d32_cachef_sync4        := 0 2 32 2 15 4 0 0 0
build_axil_a2_d32_prot5               := 1 2 32 5 0 3 0 0 0
build_axi4_a3_d32_cachef              := 0 3 32 2 15 3 0 0 0
build_axil_a3_d64_prot5_sync4         := 1 3 64 5 0 4 0 0 0
build_axil_a32_d64_prot5_cachef_sync4 := 1 32 64 5 15 4 0 0 0
build_axi4_a64_d64_prot5_cachef_sync4 := 0 64 64 5 15 4 0 0 0
build_axil_a48_d32_prot5_cachef_sync4 := 1 48 32 5 15 4 0 0 0

# Builds whose address space is smaller than the test regions run bring-up only
BRINGUP_ONLY := axi4_a2_d32_cachef_sync4 axil_a2_d32_prot5 axi4_a3_d32_cachef \
                axil_a3_d64_prot5_sync4

# Clock ratios, run on an existing build: BUILD CLK_PERIOD_PS TCK_PERIOD_PS
RATIOS := axi4_a64_d64_slowclk
ratio_axi4_a64_d64_slowclk := axi4_a64_d64 40000 20000

CONFIGS := $(BUILDS) $(RATIOS)
CONFIG  ?= axi4_a64_d64
ifneq ($(filter $(CONFIG),$(RATIOS)),)
  BUILD         := $(word 1,$(ratio_$(CONFIG)))
  CLK_PERIOD_PS ?= $(word 2,$(ratio_$(CONFIG)))
  TCK_PERIOD_PS ?= $(word 3,$(ratio_$(CONFIG)))
else ifneq ($(filter $(CONFIG),$(BUILDS)),)
  BUILD := $(CONFIG)
else
  $(error unknown CONFIG '$(CONFIG)', use one of: $(CONFIGS))
endif
USE_AXI_LITE   := $(word 1,$(build_$(BUILD)))
AXI_ADDR_WIDTH := $(word 2,$(build_$(BUILD)))
AXI_DATA_WIDTH := $(word 3,$(build_$(BUILD)))
AXI_PROT       := $(word 4,$(build_$(BUILD)))
AXI_CACHE      := $(word 5,$(build_$(BUILD)))
SYNC_STAGES    := $(word 6,$(build_$(BUILD)))
AXI_REQ_DATA_WIDTH := $(word 7,$(build_$(BUILD)))
AXI_RSP_DATA_WIDTH := $(word 8,$(build_$(BUILD)))
AXI_DEFAULT_TYPES  := $(word 9,$(build_$(BUILD)))

# Clock periods are read by the test, not compiled in, and hold exactly unless
# TDR2AXI_CLOCK_JITTER is 1; CLK_PERIOD_PS=random draws the i_clk period from the seed. Each
# test draws the i_clk phase, reset, scan shape and subordinate timing from the seed unless
# TDR2AXI_RANDOMIZE is 0.
CLK_PERIOD_PS        ?= 1000
TCK_PERIOD_PS        ?= 20000
TDR2AXI_RANDOMIZE    ?= 1
TDR2AXI_CLOCK_JITTER ?= 0
export CLK_PERIOD_PS TCK_PERIOD_PS TDR2AXI_RANDOMIZE TDR2AXI_CLOCK_JITTER

# The tests import the packages env and tests from the cocotb directory
export PYTHONPATH := $(CURDIR)/cocotb$(if $(PYTHONPATH),:$(PYTHONPATH))

SIM_BUILD           := sim_build/$(SIM)_$(BUILD)$(if $(filter 1,$(WAVES)),_waves)
COCOTB_RESULTS_FILE := results_$(SIM)_$(CONFIG).xml

ROOT  := $(abspath $(CURDIR)/../..)
FLIST := $(CURDIR)/$(SIM_BUILD)/sources.f

VERILOG_SOURCES = $(CURDIR)/sva/tdr2axi_axi_mgr_sva.sv $(CURDIR)/cov/sv/tdr2axi_fcov.sv \
                  $(CURDIR)/tb/tb_top.sv
COMPILE_ARGS   += -f $(FLIST)
# The model rebuilds when the file list or an RTL file in it changes. Some bender releases
# write // comment lines into the list, which name no file.
FLIST_SOURCES       := $(if $(wildcard $(FLIST)),$(filter-out +%,$(shell sed 's|//.*||' $(FLIST))))
CUSTOM_COMPILE_DEPS += $(FLIST) $(FLIST_SOURCES)

ifeq ($(SIM),verilator)
  COMPILE_ARGS += -GUSE_AXI_LITE=$(USE_AXI_LITE) -GAXI_ADDR_WIDTH=$(AXI_ADDR_WIDTH) \
                  -GAXI_DATA_WIDTH=$(AXI_DATA_WIDTH) -GAXI_PROT=$(AXI_PROT) \
                  -GAXI_CACHE=$(AXI_CACHE) -GSYNC_STAGES=$(SYNC_STAGES) \
                  -GAXI_REQ_DATA_WIDTH=$(AXI_REQ_DATA_WIDTH) \
                  -GAXI_RSP_DATA_WIDTH=$(AXI_RSP_DATA_WIDTH) \
                  -GAXI_DEFAULT_TYPES=$(AXI_DEFAULT_TYPES)
  COMPILE_ARGS += --timing --assert --coverage-user -Wno-fatal -Wno-lint -Wno-style \
                  -DCOMMON_CELLS_ASSERTS_OFF
  # Coverage data of a run; a runner gives each run its own path
  VERILATOR_COVERAGE_FILE ?= $(SIM_BUILD)/coverage_$(CONFIG).dat
  ifeq ($(WAVES),1)
    COMPILE_ARGS += --trace-fst --trace-structs
    SIM_ARGS     += --trace
    # The FST writer links lz4, which Homebrew installs off the default search paths
    LZ4_PREFIX := $(shell brew --prefix lz4 2>/dev/null)
    ifneq ($(wildcard $(LZ4_PREFIX)/include/lz4.h),)
      export CPATH        := $(LZ4_PREFIX)/include$(if $(CPATH),:$(CPATH))
      export LIBRARY_PATH := $(LZ4_PREFIX)/lib$(if $(LIBRARY_PATH),:$(LIBRARY_PATH))
    endif
  endif
else ifeq ($(SIM),vcs)
  COMPILE_ARGS += -pvalue+$(TOPLEVEL).USE_AXI_LITE=$(USE_AXI_LITE) \
                  -pvalue+$(TOPLEVEL).AXI_ADDR_WIDTH=$(AXI_ADDR_WIDTH) \
                  -pvalue+$(TOPLEVEL).AXI_DATA_WIDTH=$(AXI_DATA_WIDTH) \
                  -pvalue+$(TOPLEVEL).AXI_PROT=$(AXI_PROT) \
                  -pvalue+$(TOPLEVEL).AXI_CACHE=$(AXI_CACHE) \
                  -pvalue+$(TOPLEVEL).SYNC_STAGES=$(SYNC_STAGES) \
                  -pvalue+$(TOPLEVEL).AXI_REQ_DATA_WIDTH=$(AXI_REQ_DATA_WIDTH) \
                  -pvalue+$(TOPLEVEL).AXI_RSP_DATA_WIDTH=$(AXI_RSP_DATA_WIDTH) \
                  -pvalue+$(TOPLEVEL).AXI_DEFAULT_TYPES=$(AXI_DEFAULT_TYPES)
  # $error continues under VCS; the sva_fail_* counters fail the running test
  COMPILE_ARGS += -assert svaext
  SIM_ARGS     += -assert nopostproc
  # The smoke test reads the files VCS parsed from this log beside the model; the runner's
  # build.log holds only the last make run, which may find the model current
  COMPILE_ARGS += -l $(CURDIR)/$(SIM_BUILD)/vcs_compile.log
endif

include $(shell cocotb-config --makefiles)/Makefile.sim

# override appends to a PLUSARGS given on the command line, which += alone leaves as given
ifeq ($(SIM),verilator)
  ifeq ($(findstring +verilator+coverage+file+,$(PLUSARGS)),)
    override PLUSARGS += +verilator+coverage+file+$(VERILATOR_COVERAGE_FILE)
  endif
  # An assertion failure fails the running test through its sva_fail_* counter; the run
  # goes on to the next test instead of stopping at the first $error
  override PLUSARGS += +verilator+error+limit+1000000
endif

# Filelist of the DUT and its dependencies, generated by bender before the build
$(FLIST): $(ROOT)/Bender.yml $(wildcard $(ROOT)/Bender.lock)
	mkdir -p $(dir $@)
	cd $(ROOT) && bender script flist-plus -t tdr2axi \
	  -t tech_cells_generic:tech_cells_generic_exclude_deprecated > $@

# cocotb exits 0 even when a test fails, so check the results file
.PHONY: check
check:
	@test -f $(COCOTB_RESULTS_FILE) || (echo "ERROR: no $(COCOTB_RESULTS_FILE)" >&2; exit 1)
	@! grep -q -e '<failure' -e '<error' $(COCOTB_RESULTS_FILE) || \
	  (echo "ERROR: test failures in $(COCOTB_RESULTS_FILE)" >&2; exit 1)

.PHONY: sim_all
sim_all:
	rc=0; \
	for c in $(CONFIGS); do \
	  case " $(BRINGUP_ONLY) " in *" $$c "*) tc=tdr2axi_bringup_test;; *) tc=;; esac; \
	  $(MAKE) sim CONFIG=$$c TESTCASE=$$tc && $(MAKE) check CONFIG=$$c || rc=1; \
	done; \
	exit $$rc

SEEDS ?= 1 2 3 4 5

.PHONY: bringup_all
bringup_all:
	rc=0; \
	for c in $(CONFIGS); do \
	  for s in $(SEEDS); do \
	    r=results_$(SIM)_$${c}_bringup_$$s.xml; \
	    $(MAKE) sim CONFIG=$$c TESTCASE=tdr2axi_bringup_test RANDOM_SEED=$$s \
	      COCOTB_RESULTS_FILE=$$r && $(MAKE) check CONFIG=$$c COCOTB_RESULTS_FILE=$$r || rc=1; \
	  done; \
	done; \
	exit $$rc

clean::
	rm -rf sim_build sim_build_* results_*.xml dump.fst __pycache__
