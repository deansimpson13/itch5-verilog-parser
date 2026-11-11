// ============================================================================
// itch_decoder.v - Pulls ITCH fields out of a framed message, by byte offset.
//
// Input: the msg_* beats produced by itch_framer (one message byte per beat).
//
// How it works (two simple steps):
//
//   1. CAPTURE. Each message byte is written into a small byte buffer,
//      mbuf[0..BUF_BYTES-1], at its offset in the message (byte 0 = type).
//      Bytes past the end of the buffer are not stored. Every supported
//      message fits, and longer messages are skipped anyway.
//
//   2. EXTRACT. One clock after the last byte arrives, the whole message
//      is sitting in mbuf. Each field is copied out using the offsets from
//      the spec (docs/SPEC_NOTES.md), and out_valid pulses high for exactly
//      one clock.
//
// Why buffer the whole message instead of grabbing fields "on the fly"?
// The buffer approach keeps the offsets in one readable place (the case
// statement below) that looks just like the spec tables. Grabbing fields
// on the fly would need an offset comparison for every byte of every
// field. Cost: BUF_BYTES x 8 flip-flops (312 for 39 bytes), which is
// tiny for any FPGA.
//
// out_supported = 1 only when the type is one this decoder knows AND the
// length prefix equals the spec length for that type. Everything else is
// reported with out_supported = 0 (the testbench prints "SKIP").
// ============================================================================
`timescale 1ns / 1ps

module itch_decoder #(
    parameter BUF_BYTES = 39            // largest supported message
) (
    input  wire        clk,
    input  wire        rst,

    // ---- framed bytes from itch_framer ----
    input  wire        msg_valid,
    input  wire [7:0]  msg_data,
    input  wire        msg_first,
    input  wire        msg_last,
    input  wire [15:0] msg_len,

    // ---- decoded output: valid for one clock per message ----
    output reg         out_valid,
    output reg  [7:0]  out_type,       // message type byte, e.g. "A" = 8'h41
    output reg  [15:0] out_len,        // length from the prefix
    output reg         out_supported,  // 1 = fields below are meaningful

    // common header (offsets 0..10, same in every message)
    output reg  [15:0] out_locate,     // stock locate        @1, 2 bytes
    output reg  [15:0] out_tracking,   // tracking number     @3, 2 bytes
    output reg  [47:0] out_timestamp,  // ns since midnight   @5, 6 bytes

    // order fields (A)
    output reg  [63:0] out_order_ref,  // order reference number
    output reg  [7:0]  out_side,       // "B" or "S"
    output reg  [31:0] out_shares,
    output reg  [63:0] out_stock,      // 8 ASCII chars, first char in [63:56]
    output reg  [31:0] out_price       // Price(4): 4 implied decimal places
);

    // ------------------------------------------------------------------
    // Step 1: capture bytes into the buffer.
    // ------------------------------------------------------------------
    reg  [7:0]  mbuf [0:BUF_BYTES-1];  // ("buf" is a Verilog keyword, so "mbuf")
    reg  [15:0] offset;                // where the NEXT byte of this message goes
    reg         done;                  // 1 for one clock after a message's last byte
    reg  [15:0] len_q;                 // saved length of the finished message

    // Byte 0 of a message always goes to mbuf[0], whatever offset says.
    wire [15:0] pos = msg_first ? 16'd0 : offset;

    always @(posedge clk) begin
        if (rst) begin
            offset <= 16'd0;
            done   <= 1'b0;
            len_q  <= 16'd0;
        end else begin
            done <= 1'b0;
            if (msg_valid) begin
                if (pos < BUF_BYTES)
                    mbuf[pos] <= msg_data;
                offset <= pos + 16'd1;
                if (msg_last) begin
                    done  <= 1'b1;
                    len_q <= msg_len;
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // Helper: read N bytes starting at offset OFF as one big-endian number.
    // Example: be(20, 4) = {mbuf[20], mbuf[21], mbuf[22], mbuf[23]}.
    // It is always called with constant arguments, so synthesis turns it
    // into plain wires (no actual loop runs in hardware).
    // ------------------------------------------------------------------
    function [63:0] be;
        input integer off;
        input integer n;
        integer k;
        begin
            be = 64'd0;
            for (k = 0; k < n; k = k + 1)
                be = {be[55:0], mbuf[off + k]};   // shift left 8, append next byte
        end
    endfunction

    // Spec length of each supported type (0 = not supported).
    function [15:0] spec_len;
        input [7:0] t;
        begin
            case (t)
                "A":     spec_len = 16'd36;   // Add Order            §1.3.1
                default: spec_len = 16'd0;
            endcase
        end
    endfunction

    wire [7:0]  mtype     = mbuf[0];
    wire [15:0] want_len  = spec_len(mtype);
    wire        supported = (want_len != 16'd0) && (len_q == want_len);

    // ------------------------------------------------------------------
    // Step 2: extract fields and pulse out_valid.
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            out_valid     <= 1'b0;
            out_type      <= 8'd0;
            out_len       <= 16'd0;
            out_supported <= 1'b0;
            out_locate    <= 16'd0;
            out_tracking  <= 16'd0;
            out_timestamp <= 48'd0;
            out_order_ref <= 64'd0;
            out_side      <= 8'd0;
            out_shares    <= 32'd0;
            out_stock     <= 64'd0;
            out_price     <= 32'd0;
        end else begin
            out_valid <= done;               // one-clock pulse per message
            if (done) begin
                out_type      <= mtype;
                out_len       <= len_q;
                out_supported <= supported;
                out_locate    <= be(1, 2);
                out_tracking  <= be(3, 2);
                out_timestamp <= be(5, 6);

                // Clear the type-specific fields, then fill in the ones this type has.
                out_order_ref <= 64'd0;
                out_side      <= 8'd0;
                out_shares    <= 32'd0;
                out_stock     <= 64'd0;
                out_price     <= 32'd0;

                if (supported) begin
                    case (mtype)
                        "A": begin                    // Add Order §1.3.1
                            out_order_ref <= be(11, 8);
                            out_side      <= be(19, 1);
                            out_shares    <= be(20, 4);
                            out_stock     <= be(24, 8);
                            out_price     <= be(32, 4);
                        end
                        default: ;                   // nothing extra
                    endcase
                end
            end
        end
    end

endmodule
