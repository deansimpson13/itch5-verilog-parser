// ============================================================================
// synth_top.v - A wrapper that lets itch_top be placed and routed on a real
//               FPGA. Used ONLY by `make synth`; not part of the design.
//
// The problem: itch_top has about 650 output bits (64-bit order refs, a
// 48-bit timestamp, ...). A Lattice iCE40-HX8K has at most 206 I/O pins, so
// nextpnr can't place itch_top as the top level: there aren't enough pins.
//
// The fix: a "scan chain". Every itch_top output goes into one long
// register, CAP. When a message comes out (out_valid = 1), CAP loads all
// the outputs at once. On every other clock, CAP shifts right by one bit,
// and its lowest bit drives the single pin scan_out. So every output bit
// eventually appears on scan_out:
//
//     out_* (650 bits) --load--> [ CAP: bit N-1 ... bit 1, bit 0 ] --> scan_out
//                                         shift right on every other clock
//
// Why this matters for measuring the design honestly: synthesis tools delete
// logic whose result never reaches an output pin. Because every output bit
// can reach scan_out, nothing in itch_top can be optimized away.
//
// Why not just XOR all the outputs together into one pin? If two output
// bits always had the same value, XOR would cancel them and the tool could
// legally delete both. A shift register never cancels anything.
//
// Cost: CAP adds one flip-flop and one small mux per output bit. That's why
// `make synth` also reports itch_top on its own, without this wrapper.
// ============================================================================
`timescale 1ns / 1ps

module synth_top (
    input  wire       clk,
    input  wire       rst,
    input  wire       in_valid,
    input  wire [7:0] in_data,
    output wire       in_ready,
    output wire       out_valid,
    output wire       scan_out
);

    wire        supported;
    wire [7:0]  typ, ev, side, printable, mkt, fsi, rlo, cls, auth, ssti, ipo, luld, etp, inv;
    wire [15:0] len, locate, tracking, subtype;
    wire [47:0] ts;
    wire [63:0] stock, oref, nref, match;
    wire [31:0] shares, price, mpid, round_lot, leverage;

    itch_top u_itch (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
        .out_valid(out_valid), .out_type(typ), .out_len(len), .out_supported(supported),
        .out_locate(locate), .out_tracking(tracking), .out_timestamp(ts),
        .out_event(ev), .out_stock(stock),
        .out_order_ref(oref), .out_new_order_ref(nref),
        .out_side(side), .out_shares(shares), .out_price(price),
        .out_mpid(mpid), .out_match(match), .out_printable(printable),
        .out_mkt_cat(mkt), .out_fin_status(fsi),
        .out_round_lot(round_lot), .out_round_lots_only(rlo),
        .out_issue_class(cls), .out_issue_subtype(subtype),
        .out_authenticity(auth), .out_ssti(ssti),
        .out_ipo_flag(ipo), .out_luld_tier(luld),
        .out_etp_flag(etp), .out_etp_leverage(leverage),
        .out_inverse(inv)
    );

    // Every itch_top output except out_valid and in_ready (those two have
    // their own pins), glued into one wide vector. Width, counted from the
    // port list: 8+16+1+16+16+48+8+64*3+8+32*3+64+8*3+32+8*2+16+8*5+32+8 = 641.
    localparam W = 641;
    wire [W-1:0] all_out = {
        typ, len, supported, locate, tracking, ts, ev, stock, oref, nref,
        side, shares, price, mpid, match, printable, mkt, fsi, round_lot,
        rlo, cls, subtype, auth, ssti, ipo, luld, etp, leverage, inv
    };

    reg [W-1:0] cap;

    always @(posedge clk) begin
        if (out_valid)
            cap <= all_out;                 // load every output in parallel
        else
            cap <= {1'b0, cap[W-1:1]};      // shift right one bit
    end

    assign scan_out = cap[0];

endmodule
