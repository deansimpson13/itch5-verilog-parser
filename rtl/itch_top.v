// ============================================================================
// itch_top.v - The complete parser: itch_framer feeding itch_decoder.
//
//   in_valid/in_data/in_ready --> [ itch_framer ] --msg_*--> [ itch_decoder ] --> out_*
//
// Latency: a message's decoded fields appear (out_valid = 1) two clocks
// after its last byte is accepted (1 clock in the framer, 1 in the decoder).
// ============================================================================
`timescale 1ns / 1ps

module itch_top (
    input  wire        clk,
    input  wire        rst,

    input  wire        in_valid,
    input  wire [7:0]  in_data,
    output wire        in_ready,

    output wire        out_valid,
    output wire [7:0]  out_type,
    output wire [15:0] out_len,
    output wire        out_supported,
    output wire [15:0] out_locate,
    output wire [15:0] out_tracking,
    output wire [47:0] out_timestamp,
    output wire [63:0] out_order_ref,
    output wire [7:0]  out_side,
    output wire [31:0] out_shares,
    output wire [63:0] out_stock,
    output wire [31:0] out_price
);

    // Wires between the two modules.
    wire        msg_valid, msg_first, msg_last;
    wire [7:0]  msg_data;
    wire [15:0] msg_len;

    itch_framer u_framer (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
        .msg_valid(msg_valid), .msg_data(msg_data),
        .msg_first(msg_first), .msg_last(msg_last), .msg_len(msg_len)
    );

    itch_decoder u_decoder (
        .clk(clk), .rst(rst),
        .msg_valid(msg_valid), .msg_data(msg_data),
        .msg_first(msg_first), .msg_last(msg_last), .msg_len(msg_len),
        .out_valid(out_valid), .out_type(out_type), .out_len(out_len),
        .out_supported(out_supported),
        .out_locate(out_locate), .out_tracking(out_tracking), .out_timestamp(out_timestamp),
        .out_order_ref(out_order_ref), .out_side(out_side), .out_shares(out_shares),
        .out_stock(out_stock), .out_price(out_price)
    );

endmodule
