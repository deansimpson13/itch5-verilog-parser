// ============================================================================
// itch_framer.v - Finds ITCH message boundaries in a length-prefixed byte stream.
//
// Input stream format (one byte per clock when in_valid is high):
//
//     [LEN_HI][LEN_LO][ msg byte 0 (type) ][ msg byte 1 ] ... [ msg byte LEN-1 ]
//     [LEN_HI][LEN_LO][ next message ... ]
//
// The framer is a 3-state machine:
//
//     S_LEN_HI --byte--> S_LEN_LO --byte--> S_BODY --(LEN bytes)--> S_LEN_HI
//                             |
//                             +-- length was 0: nothing to send, go back to S_LEN_HI
//
// For every body byte it outputs one "beat" on the msg_* port, tagged with
//   msg_first = this is byte 0 (the message type)
//   msg_last  = this is the final byte of the message
// and msg_len holds the length from the prefix for the whole message.
//
// The framer never needs to pause the sender, so in_ready is always 1.
// The port still exists so the handshake looks like a standard
// valid/ready interface, and a later version could apply backpressure.
// A byte is transferred on a clock edge only when in_valid && in_ready.
//
// Outputs are registered, so they appear one clock after the byte arrives.
// Everything here is synthesizable: no $display, no delays, no initial blocks.
// ============================================================================
`timescale 1ns / 1ps

module itch_framer (
    input  wire        clk,
    input  wire        rst,        // synchronous, active-high reset

    // ---- upstream byte stream (valid/ready handshake) ----
    input  wire        in_valid,   // sender has a byte on in_data
    input  wire [7:0]  in_data,
    output wire        in_ready,   // we can accept a byte (always, in v1)

    // ---- framed message bytes (one beat per message byte) ----
    output reg         msg_valid,  // msg_data holds a message byte this cycle
    output reg  [7:0]  msg_data,
    output reg         msg_first,  // this beat is byte 0 (the message type)
    output reg         msg_last,   // this beat is the final byte of the message
    output reg  [15:0] msg_len     // length of the current message (from prefix)
);

    // State encoding. localparam = a named constant that only exists inside this module.
    localparam [1:0] S_LEN_HI = 2'd0,   // waiting for the high byte of the length
                     S_LEN_LO = 2'd1,   // waiting for the low byte of the length
                     S_BODY   = 2'd2;   // passing message bytes through

    reg  [1:0]  state;
    reg  [7:0]  len_hi;        // high byte of the length, saved until the low byte arrives
    reg  [15:0] remaining;     // body bytes still to come (counts down to 1)
    reg         at_first;      // the next body byte is byte 0

    assign in_ready = 1'b1;

    // A "beat" = one byte actually transferred this clock.
    wire beat = in_valid & in_ready;

    // The full 16-bit length is ready on the cycle the low byte arrives.
    wire [15:0] new_len = {len_hi, in_data};

    always @(posedge clk) begin
        if (rst) begin
            state     <= S_LEN_HI;
            len_hi    <= 8'd0;
            remaining <= 16'd0;
            at_first  <= 1'b0;
            msg_valid <= 1'b0;
            msg_data  <= 8'd0;
            msg_first <= 1'b0;
            msg_last  <= 1'b0;
            msg_len   <= 16'd0;
        end else begin
            // Default: no output beat this cycle. The S_BODY branch overrides this.
            // These are "pulse" signals: high for exactly one clock per byte.
            msg_valid <= 1'b0;
            msg_first <= 1'b0;
            msg_last  <= 1'b0;

            if (beat) begin
                case (state)
                    S_LEN_HI: begin
                        len_hi <= in_data;
                        state  <= S_LEN_LO;
                    end

                    S_LEN_LO: begin
                        if (new_len == 16'd0) begin
                            // Zero-length record: no body follows (see SPEC_NOTES).
                            state <= S_LEN_HI;
                        end else begin
                            msg_len   <= new_len;
                            remaining <= new_len;
                            at_first  <= 1'b1;
                            state     <= S_BODY;
                        end
                    end

                    S_BODY: begin
                        msg_valid <= 1'b1;
                        msg_data  <= in_data;
                        msg_first <= at_first;
                        msg_last  <= (remaining == 16'd1);
                        at_first  <= 1'b0;
                        remaining <= remaining - 16'd1;
                        if (remaining == 16'd1)
                            state <= S_LEN_HI;     // message done; next is a new prefix
                    end

                    default: state <= S_LEN_HI;    // unreachable; safe recovery
                endcase
            end
        end
    end

endmodule
