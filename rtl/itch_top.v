// ============================================================================
// itch_top.v - The complete parser: itch_framer feeding itch_decoder.
//
//   in_valid/in_data/in_ready --> [ itch_framer ] --msg_*--> [ itch_decoder ] --> out_*
//
// Latency: a message's decoded fields appear (out_valid = 1) two clocks
// after its last byte is accepted (1 clock in the framer, 1 in the decoder).
//
// See itch_decoder.v for which output fields belong to which message types.
// ============================================================================
`timescale 1ns / 1ps

module itch_top (
    input  wire        clk,
    input  wire        rst,

    // ---- byte stream in ----
    input  wire        in_valid,
    input  wire [7:0]  in_data,
    output wire        in_ready,

    // ---- decoded message out ----
    output wire        out_valid,
    output wire [7:0]  out_type,
    output wire [15:0] out_len,
    output wire        out_supported,
    output wire [15:0] out_locate,
    output wire [15:0] out_tracking,
    output wire [47:0] out_timestamp,
    output wire [7:0]  out_event,
    output wire [63:0] out_stock,
    output wire [63:0] out_order_ref,
    output wire [63:0] out_new_order_ref,
    output wire [7:0]  out_side,
    output wire [31:0] out_shares,
    output wire [31:0] out_price,
    output wire [31:0] out_mpid,
    output wire [63:0] out_match,
    output wire [7:0]  out_printable,
    output wire [7:0]  out_mkt_cat,
    output wire [7:0]  out_fin_status,
    output wire [31:0] out_round_lot,
    output wire [7:0]  out_round_lots_only,
    output wire [7:0]  out_issue_class,
    output wire [15:0] out_issue_subtype,
    output wire [7:0]  out_authenticity,
    output wire [7:0]  out_ssti,
    output wire [7:0]  out_ipo_flag,
    output wire [7:0]  out_luld_tier,
    output wire [7:0]  out_etp_flag,
    output wire [31:0] out_etp_leverage,
    output wire [7:0]  out_inverse
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
        .out_event(out_event), .out_stock(out_stock),
        .out_order_ref(out_order_ref), .out_new_order_ref(out_new_order_ref),
        .out_side(out_side), .out_shares(out_shares), .out_price(out_price),
        .out_mpid(out_mpid), .out_match(out_match), .out_printable(out_printable),
        .out_mkt_cat(out_mkt_cat), .out_fin_status(out_fin_status),
        .out_round_lot(out_round_lot), .out_round_lots_only(out_round_lots_only),
        .out_issue_class(out_issue_class), .out_issue_subtype(out_issue_subtype),
        .out_authenticity(out_authenticity), .out_ssti(out_ssti),
        .out_ipo_flag(out_ipo_flag), .out_luld_tier(out_luld_tier),
        .out_etp_flag(out_etp_flag), .out_etp_leverage(out_etp_leverage),
        .out_inverse(out_inverse)
    );

endmodule
