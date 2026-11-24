# SPDX-FileCopyrightText: 2026 Tenstorrent USA, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# SymbiYosys jobs of the tdr2axi formal plan, one .sby per plan item under sby/.
#
#   make                                       # every job, every task
#   make tdr2axi_bridge_formal                 # one job
#   make tdr2axi_axi_mgr_formal TASKS=prove_a4_d32
#   make status                                # one line per property of every run
#
# Needs bender, SymbiYosys, yices-smt2 and Yosys with the slang frontend, built in or as
# the slang plugin; YOSYS overrides the detected command.

ROOT  := $(abspath $(CURDIR)/../../..)
BUILD := $(CURDIR)/build
FLIST := $(BUILD)/sources.f

SBY      ?= sby
SBY_ARGS ?= -j 4
TASKS    ?=
# yosys-slang is a plugin unless this Yosys has read_slang built in
YOSYS ?= $(if $(shell yosys -p 'help read_slang' 2>&1 | grep 'No such command'),yosys -m slang,yosys)

JOBS := tdr2axi_axi_attr_formal tdr2axi_axi_comb_path_formal tdr2axi_axi_mgr_formal \
        tdr2axi_bridge_formal tdr2axi_dm_resp_formal tdr2axi_dmactive_inflight_formal \
        tdr2axi_dmactive_no_bus_formal tdr2axi_reset_sync_formal

.PHONY: all status clean $(JOBS)
all: $(JOBS)

# The sby files copy sources.f into each run, so its paths are absolute
$(FLIST): $(ROOT)/Bender.yml $(wildcard $(ROOT)/Bender.lock)
	mkdir -p $(dir $@)
	cd $(ROOT) && bender script flist-plus -t tdr2axi \
	  -t tech_cells_generic:tech_cells_generic_exclude_deprecated > $@

# sby exits non-zero when a task fails, so make stops there unless run with -k
$(JOBS): %: $(FLIST)
	cd sby && $(SBY) -f $(SBY_ARGS) --yosys "$(YOSYS)" --prefix $(BUILD)/$* $*.sby $(TASKS)

status:
	@for j in $(JOBS); do (cd sby && $(SBY) --status --prefix $(BUILD)/$$j $$j.sby); done

clean:
	rm -rf $(BUILD)
