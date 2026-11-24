/* Copyright 2018 ETH Zurich and University of Bologna.
* Copyright and related rights are licensed under the Solderpad Hardware
* License, Version 0.51 (the “License”); you may not use this file except in
* compliance with the License.  You may obtain a copy of the License at
* http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
* or agreed to in writing, software, hardware and materials distributed under
* this License is distributed on an “AS IS” BASIS, WITHOUT WARRANTIES OR
* CONDITIONS OF ANY KIND, either express or implied. See the License for the
* specific language governing permissions and limitations under the License.
*
* File:   dm_sba.sv
* Author: Florian Zaruba <zarubaf@iis.ee.ethz.ch>
* Date:   1.8.2018
*
* Description: System Bus Access Module
*
*/
module dm_sba #(
  parameter int unsigned BusWidth = 32,
  parameter bit          ReadByteEnable = 1,
  // Width of the system bus address. Data width stays BusWidth.
  parameter int unsigned SbaAddrWidth = BusWidth
) (
  input  logic                   clk_i,       // Clock
  input  logic                   rst_ni,
  // dmcontrol.dmactive. While it is 0 no access starts; an access already in flight
  // completes on the bus with the payload it started with, and its result is dropped.
  input  logic                   dmactive_i,

  output logic                   master_req_o,
  output logic [SbaAddrWidth-1:0] master_add_o,
  output logic                   master_we_o,
  output logic [BusWidth-1:0]    master_wdata_o,
  output logic [BusWidth/8-1:0]  master_be_o,
  // sbaccess of the access in flight (log2 of its size in bytes), for buses that carry
  // an access size (e.g. AXI AxSIZE). Holds with the rest of the request.
  output logic [2:0]             master_size_o,
  input  logic                   master_gnt_i,
  input  logic                   master_r_valid_i,
  input  logic                   master_r_err_i,
  input  logic                   master_r_other_err_i, // *other_err_i has priority over *err_i
  input  logic [BusWidth-1:0]    master_r_rdata_i,

  input  logic [SbaAddrWidth-1:0] sbaddress_i,
  input  logic                   sbaddress_write_valid_i,
  // control signals in
  input  logic                   sbreadonaddr_i,
  output logic [SbaAddrWidth-1:0] sbaddress_o,
  input  logic                   sbautoincrement_i,
  input  logic [2:0]             sbaccess_i,
  // data in
  input  logic                   sbreadondata_i,
  input  logic [BusWidth-1:0]    sbdata_i,
  input  logic                   sbdata_read_valid_i,
  input  logic                   sbdata_write_valid_i,
  // read data out
  output logic [BusWidth-1:0]    sbdata_o,
  output logic                   sbdata_valid_o,
  // control signals
  output logic                   sbbusy_o,
  output logic                   sberror_valid_o, // bus error occurred
  output logic [2:0]             sberror_o // bus error occurred
);

  localparam int BeIdxWidth = $clog2(BusWidth/8);

  initial begin : p_validate_sba_addr_width
    assert (SbaAddrWidth >= $clog2(BusWidth/8) && SbaAddrWidth <= 64) else
      $fatal(1, "SbaAddrWidth must cover the BusWidth byte offset and be at most 64.");
  end

  dm::sba_state_e state_d, state_q;

  logic [SbaAddrWidth-1:0]       address;
  logic                          req;
  logic                          gnt;
  logic                          we;
  logic [BusWidth/8-1:0]         be;
  logic [BusWidth/8-1:0]         be_mask;
  logic [BeIdxWidth-1:0] be_idx;

  assign sbbusy_o = logic'(state_q != dm::Idle);

  // Payload of the access in flight. dm_csrs updates sbaddress and sbdata one cycle
  // after the DMI write that starts an access, so the first cycle of Read or Write
  // takes the live CSR values and registers them; from then on the access uses the
  // registered copy. dmactive = 0 clears the CSRs, and a bus request must keep its
  // payload until it is granted (AXI: a payload holds while VALID is high).
  logic                    payload_held_q;
  logic [SbaAddrWidth-1:0] sbaddress_q;
  logic [2:0]              sbaccess_q;
  logic [BusWidth-1:0]     sbdata_q;
  logic [SbaAddrWidth-1:0] acc_address;
  logic [2:0]              acc_sbaccess;
  logic [BusWidth-1:0]     acc_wdata;

  assign acc_address  = payload_held_q ? sbaddress_q : sbaddress_i;
  assign acc_sbaccess = payload_held_q ? sbaccess_q  : sbaccess_i;
  assign acc_wdata    = payload_held_q ? sbdata_q    : sbdata_i;

  // The access in flight lost its DM: dmactive fell after it started. It still
  // completes on the bus, but its read data, error and address increment are dropped,
  // so the DM comes out of reset with its reset values.
  logic orphan_q;
  logic drop_result;
  assign drop_result = orphan_q | ~dmactive_i;

  // The read was started by a read of sbdata0 (sbreadondata). Debug spec 0.13.2
  // increments sbaddress after such a read whether or not it succeeded; every other
  // access increments only when it succeeded.
  logic read_on_data_q, read_on_data_d;
  logic bus_err;
  assign bus_err = master_r_err_i | master_r_other_err_i;

  always_comb begin : p_be_mask
    be_mask = '0;

    // generate byte enable mask
    unique case (acc_sbaccess)
      3'b000: begin
        be_mask[be_idx] = '1;
      end
      3'b001: begin
        be_mask[int'({be_idx[$high(be_idx):1], 1'b0}) +: 2] = '1;
      end
      3'b010: begin
        if (BusWidth == 32'd64) be_mask[int'({be_idx[$high(be_idx)], 2'h0}) +: 4] = '1;
        else                    be_mask = '1;
      end
      3'b011: be_mask = '1;
      default: ;
    endcase
  end

  logic [SbaAddrWidth-1:0] sbaccess_mask;
  assign sbaccess_mask = {SbaAddrWidth{1'b1}} << acc_sbaccess;

  logic addr_incr_en;
  logic [SbaAddrWidth-1:0] addr_incr;
  assign addr_incr = (addr_incr_en) ? (SbaAddrWidth'(1'b1) << acc_sbaccess) : '0;
  assign sbaddress_o = sbaddress_i + addr_incr;


  always_comb begin : p_fsm
    req     = 1'b0;
    address = acc_address;
    we      = 1'b0;
    be      = '0;
    be_idx  = acc_address[BeIdxWidth-1:0];

    sberror_o       = '0;
    sberror_valid_o = 1'b0;

    addr_incr_en    = 1'b0;
    read_on_data_d  = read_on_data_q;

    state_d = state_q;

    unique case (state_q)
      dm::Idle: begin
        // no access starts while the DM is held in reset (dmactive = 0)
        if (dmactive_i) begin
          // debugger requested a read
          if (sbaddress_write_valid_i && sbreadonaddr_i) begin
            state_d        = dm::Read;
            read_on_data_d = 1'b0;
          end
          // debugger requested a write
          if (sbdata_write_valid_i) begin
            state_d = dm::Write;
          end
          // perform another read
          if (sbdata_read_valid_i && sbreadondata_i) begin
            state_d        = dm::Read;
            read_on_data_d = 1'b1;
          end
        end
      end

      dm::Read: begin
        req = 1'b1;
        if (ReadByteEnable) be = be_mask;
        if (gnt) state_d = dm::WaitRead;
      end

      dm::Write: begin
        req = 1'b1;
        we  = 1'b1;
        be = be_mask;
        if (gnt) state_d = dm::WaitWrite;
      end

      dm::WaitRead: begin
        if (master_r_valid_i) begin
          state_d = dm::Idle;
          // auto-increment address: after a successful read, or after any read started
          // by a read of sbdata0
          addr_incr_en = sbautoincrement_i & (read_on_data_q | ~bus_err);
          // check whether an "other" error has been encountered.
          if (master_r_other_err_i) begin
            sberror_valid_o = 1'b1;
            sberror_o = 3'd7;
          // check whether there was a bus error (== bad address).
          end else if (master_r_err_i) begin
            sberror_valid_o = 1'b1;
            sberror_o = 3'd2;
          end
        end
      end

      dm::WaitWrite: begin
        if (master_r_valid_i) begin
          state_d = dm::Idle;
          // auto-increment address after a successful write
          addr_incr_en = sbautoincrement_i & ~bus_err;
          // check whether an "other" error has been encountered.
          if (master_r_other_err_i) begin
            sberror_valid_o = 1'b1;
            sberror_o = 3'd7;
          // check whether there was a bus error (== bad address).
          end else if (master_r_err_i) begin
            sberror_valid_o = 1'b1;
            sberror_o = 3'd2;
          end
        end
      end

      default: state_d = dm::Idle; // catch parasitic state
    endcase

    // handle error case
    if (32'(acc_sbaccess) > BeIdxWidth && state_q != dm::Idle) begin
      req             = 1'b0;
      state_d         = dm::Idle;
      sberror_valid_o = 1'b1;
      sberror_o       = 3'd4; // unsupported size was requested
    end

    //if sbaccess lsbs of address are not 0 - report misalignment error
    if (|(acc_address & ~sbaccess_mask) && state_q != dm::Idle) begin
      req             = 1'b0;
      state_d         = dm::Idle;
      sberror_valid_o = 1'b1;
      sberror_o       = 3'd3; // alignment error
    end
    // further error handling should go here ...

    // an access that lost its DM reports nothing back
    if (drop_result) begin
      sberror_valid_o = 1'b0;
      addr_incr_en    = 1'b0;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin : p_regs
    if (!rst_ni) begin
      state_q        <= dm::Idle;
      read_on_data_q <= 1'b0;
    end else begin
      state_q        <= state_d;
      read_on_data_q <= read_on_data_d;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin : p_payload
    if (!rst_ni) begin
      payload_held_q <= 1'b0;
      sbaddress_q    <= '0;
      sbaccess_q     <= '0;
      sbdata_q       <= '0;
    end else if (state_q == dm::Idle) begin
      payload_held_q <= 1'b0;
    end else if (!payload_held_q) begin
      payload_held_q <= 1'b1;
      sbaddress_q    <= sbaddress_i;
      sbaccess_q     <= sbaccess_i;
      sbdata_q       <= sbdata_i;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin : p_orphan
    if (!rst_ni) begin
      orphan_q <= 1'b0;
    end else if (state_d == dm::Idle) begin
      orphan_q <= 1'b0;
    end else if (state_q != dm::Idle && !dmactive_i) begin
      orphan_q <= 1'b1;
    end
  end

  logic [BeIdxWidth-1:0] be_idx_masked;
  assign be_idx_masked   = be_idx & BeIdxWidth'(sbaccess_mask);
  assign master_req_o    = req;
  assign master_add_o    = address[SbaAddrWidth-1:0];
  assign master_we_o     = we;
  assign master_wdata_o  = acc_wdata[BusWidth-1:0] << (8 * be_idx_masked);
  assign master_be_o     = be[BusWidth/8-1:0];
  assign master_size_o   = acc_sbaccess;
  assign gnt             = master_gnt_i;
  assign sbdata_valid_o  = master_r_valid_i & ~drop_result;
  assign sbdata_o        = master_r_rdata_i[BusWidth-1:0] >> (8 * be_idx_masked);

endmodule : dm_sba
