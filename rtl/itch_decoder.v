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
// field. Cost: BUF_BYTES x 8 flip-flops (320 for 40 bytes), which is
// tiny for any FPGA.
//
// out_supported = 1 only when the type is one this decoder knows AND the
// length prefix equals the spec length for that type. Everything else is
// reported with out_supported = 0 (the testbench prints "SKIP").
// ============================================================================
`timescale 1ns / 1ps

module itch_decoder #(
    // Must be >= the longest supported message: F (Add Order w/ MPID) = 40 bytes.
    // (An early version used 39, sized for R. The testbench caught the lost MPID byte.)
    parameter BUF_BYTES = 40
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

    // Fields shared by several types. Each comment lists the types that use it.
    // A field a message doesn't have is output as 0.
    output reg  [7:0]  out_event,         // S: system event code
    output reg  [63:0] out_stock,         // R A F: 8 ASCII chars, first char in [63:56]
    output reg  [63:0] out_order_ref,     // A F E C X D, U (original ref)
    output reg  [63:0] out_new_order_ref, // U: new order reference number
    output reg  [7:0]  out_side,          // A F: "B" or "S"
    output reg  [31:0] out_shares,        // A F: shares; E C: executed; X: cancelled; U: new shares
    output reg  [31:0] out_price,         // A F U: price; C: execution price. Price(4)
    output reg  [31:0] out_mpid,          // F: attribution, 4 ASCII chars
    output reg  [63:0] out_match,         // E C: match number
    output reg  [7:0]  out_printable,     // C: "Y" or "N"

    // Stock Directory (R) only
    output reg  [7:0]  out_mkt_cat,       // market category
    output reg  [7:0]  out_fin_status,    // financial status indicator
    output reg  [31:0] out_round_lot,     // round lot size
    output reg  [7:0]  out_round_lots_only,
    output reg  [7:0]  out_issue_class,
    output reg  [15:0] out_issue_subtype, // 2 ASCII chars
    output reg  [7:0]  out_authenticity,
    output reg  [7:0]  out_ssti,          // short sale threshold indicator
    output reg  [7:0]  out_ipo_flag,
    output reg  [7:0]  out_luld_tier,
    output reg  [7:0]  out_etp_flag,
    output reg  [31:0] out_etp_leverage,
    output reg  [7:0]  out_inverse
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
                "S":     spec_len = 16'd12;   // System Event         §1.1
                "R":     spec_len = 16'd39;   // Stock Directory      §1.2.1
                "A":     spec_len = 16'd36;   // Add Order            §1.3.1
                "F":     spec_len = 16'd40;   // Add Order w/ MPID    §1.3.2
                "E":     spec_len = 16'd31;   // Order Executed       §1.4.1
                "C":     spec_len = 16'd36;   // Executed w/ Price    §1.4.2
                "X":     spec_len = 16'd23;   // Order Cancel         §1.4.3
                "D":     spec_len = 16'd19;   // Order Delete         §1.4.4
                "U":     spec_len = 16'd35;   // Order Replace        §1.4.5
                default: spec_len = 16'd0;    // anything else: skip
            endcase
        end
    endfunction

    wire [7:0]  mtype     = mbuf[0];
    wire [15:0] want_len  = spec_len(mtype);
    wire        supported = (want_len != 16'd0) && (len_q == want_len);

    // ------------------------------------------------------------------
    // Step 2: extract fields and pulse out_valid.
    //
    // Only out_valid needs a reset. The data outputs are "don't care"
    // whenever out_valid is 0, so they're simply overwritten each message.
    // (Fewer reset connections = smaller, faster logic. This is common practice.)
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
        end else begin
            out_valid <= done;               // one-clock pulse per message
        end

        if (done) begin
            out_type      <= mtype;
            out_len       <= len_q;
            out_supported <= supported;
            out_locate    <= be(1, 2);
            out_tracking  <= be(3, 2);
            out_timestamp <= be(5, 6);

            // Start with every type-specific field at 0. Then the case
            // statement fills in the fields this message type actually has.
            // (In an always block, the LAST nonblocking assignment wins.)
            out_event           <= 8'd0;
            out_stock           <= 64'd0;
            out_order_ref       <= 64'd0;
            out_new_order_ref   <= 64'd0;
            out_side            <= 8'd0;
            out_shares          <= 32'd0;
            out_price           <= 32'd0;
            out_mpid            <= 32'd0;
            out_match           <= 64'd0;
            out_printable       <= 8'd0;
            out_mkt_cat         <= 8'd0;
            out_fin_status      <= 8'd0;
            out_round_lot       <= 32'd0;
            out_round_lots_only <= 8'd0;
            out_issue_class     <= 8'd0;
            out_issue_subtype   <= 16'd0;
            out_authenticity    <= 8'd0;
            out_ssti            <= 8'd0;
            out_ipo_flag        <= 8'd0;
            out_luld_tier       <= 8'd0;
            out_etp_flag        <= 8'd0;
            out_etp_leverage    <= 32'd0;
            out_inverse         <= 8'd0;

            // Offsets below are copied from docs/SPEC_NOTES.md.
            // be(offset, number_of_bytes)
            if (supported) begin
                case (mtype)
                    "S": begin                         // System Event §1.1
                        out_event <= be(11, 1);
                    end
                    "R": begin                         // Stock Directory §1.2.1
                        out_stock           <= be(11, 8);
                        out_mkt_cat         <= be(19, 1);
                        out_fin_status      <= be(20, 1);
                        out_round_lot       <= be(21, 4);
                        out_round_lots_only <= be(25, 1);
                        out_issue_class     <= be(26, 1);
                        out_issue_subtype   <= be(27, 2);
                        out_authenticity    <= be(29, 1);
                        out_ssti            <= be(30, 1);
                        out_ipo_flag        <= be(31, 1);
                        out_luld_tier       <= be(32, 1);
                        out_etp_flag        <= be(33, 1);
                        out_etp_leverage    <= be(34, 4);
                        out_inverse         <= be(38, 1);
                    end
                    "A", "F": begin                    // Add Order §1.3.1 / §1.3.2
                        out_order_ref <= be(11, 8);
                        out_side      <= be(19, 1);
                        out_shares    <= be(20, 4);
                        out_stock     <= be(24, 8);
                        out_price     <= be(32, 4);
                        if (mtype == "F")
                            out_mpid  <= be(36, 4);    // only F has attribution
                    end
                    "E": begin                         // Order Executed §1.4.1
                        out_order_ref <= be(11, 8);
                        out_shares    <= be(19, 4);
                        out_match     <= be(23, 8);
                    end
                    "C": begin                         // Executed with Price §1.4.2
                        out_order_ref <= be(11, 8);
                        out_shares    <= be(19, 4);
                        out_match     <= be(23, 8);
                        out_printable <= be(31, 1);
                        out_price     <= be(32, 4);
                    end
                    "X": begin                         // Order Cancel §1.4.3
                        out_order_ref <= be(11, 8);
                        out_shares    <= be(19, 4);
                    end
                    "D": begin                         // Order Delete §1.4.4
                        out_order_ref <= be(11, 8);
                    end
                    "U": begin                         // Order Replace §1.4.5
                        out_order_ref     <= be(11, 8);
                        out_new_order_ref <= be(19, 8);
                        out_shares        <= be(27, 4);
                        out_price         <= be(31, 4);
                    end
                    default: ;                         // unreachable: supported=0
                endcase
            end
        end
    end

endmodule
