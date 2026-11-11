// ============================================================================
// tb_walkthrough.v - Sends ONE Add Order message through itch_top and prints
// a cycle-by-cycle table of what happens inside. It's used to write
// docs/WALKTHROUGH.md, and it's handy for learning how the design works.
//
// The message is the same hand-built Add Order as tools/test_ref.py:
//   type 'A', locate 7, tracking 42, timestamp 195312500,
//   order ref 12345, side 'B', 100 shares, "AAPL    ", price 178.8948
//
// Run with:  make walkthrough
// ============================================================================
`timescale 1ns / 1ps

module tb_walkthrough;

    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;

    // 2-byte length prefix (0x0024 = 36) followed by the 36 message bytes.
    localparam N = 38;
    reg [7:0] mem [0:N-1];
    initial begin
        mem[0]  = 8'h00; mem[1]  = 8'h24;                       // length = 36
        mem[2]  = 8'h41;                                        // @0  'A'
        mem[3]  = 8'h00; mem[4]  = 8'h07;                       // @1  locate = 7
        mem[5]  = 8'h00; mem[6]  = 8'h2A;                       // @3  tracking = 42
        mem[7]  = 8'h00; mem[8]  = 8'h00; mem[9]  = 8'h0B;      // @5  timestamp
        mem[10] = 8'hA4; mem[11] = 8'h3B; mem[12] = 8'h74;      //     = 195312500
        mem[13] = 8'h00; mem[14] = 8'h00; mem[15] = 8'h00; mem[16] = 8'h00;  // @11 order ref
        mem[17] = 8'h00; mem[18] = 8'h00; mem[19] = 8'h30; mem[20] = 8'h39;  //     = 12345
        mem[21] = 8'h42;                                        // @19 side 'B'
        mem[22] = 8'h00; mem[23] = 8'h00; mem[24] = 8'h00; mem[25] = 8'h64;  // @20 shares = 100
        mem[26] = "A"; mem[27] = "A"; mem[28] = "P"; mem[29] = "L";          // @24 stock
        mem[30] = " "; mem[31] = " "; mem[32] = " "; mem[33] = " ";
        mem[34] = 8'h00; mem[35] = 8'h1B; mem[36] = 8'h4C; mem[37] = 8'h14;  // @32 price = 1788948
    end

    integer ptr = 0;
    wire       in_valid = !rst && (ptr < N);
    wire [7:0] in_data  = (ptr < N) ? mem[ptr] : 8'h00;
    wire       in_ready;

    wire        out_valid, out_supported;
    wire [7:0]  out_type, out_side;
    wire [15:0] out_len, out_locate, out_tracking;
    wire [47:0] out_timestamp;
    wire [63:0] out_order_ref, out_stock;
    wire [31:0] out_shares, out_price;

    // Only the ports this walkthrough looks at are connected; the rest are left open.
    itch_top dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
        .out_valid(out_valid), .out_type(out_type), .out_len(out_len),
        .out_supported(out_supported),
        .out_locate(out_locate), .out_tracking(out_tracking), .out_timestamp(out_timestamp),
        .out_order_ref(out_order_ref), .out_side(out_side), .out_shares(out_shares),
        .out_stock(out_stock), .out_price(out_price),
        .out_event(), .out_new_order_ref(), .out_mpid(), .out_match(), .out_printable(),
        .out_mkt_cat(), .out_fin_status(), .out_round_lot(), .out_round_lots_only(),
        .out_issue_class(), .out_issue_subtype(), .out_authenticity(), .out_ssti(),
        .out_ipo_flag(), .out_luld_tier(), .out_etp_flag(), .out_etp_leverage(),
        .out_inverse()
    );

    always @(posedge clk)
        if (in_valid && in_ready) ptr <= ptr + 1;

    // Print what every signal looks like JUST BEFORE each rising edge,
    // i.e. the values the flip-flops are about to sample.
    integer cyc = 0;
    always @(negedge clk) begin
        if (!rst) begin
            cyc = cyc + 1;
            $display("%4d | %b  %h  | %-6s %5d | %b  %h  %b %b | %5d  %b | %b",
                cyc, in_valid, in_data,
                (dut.u_framer.state == 0) ? "LEN_HI" :
                (dut.u_framer.state == 1) ? "LEN_LO" : "BODY",
                dut.u_framer.remaining,
                dut.u_framer.msg_valid, dut.u_framer.msg_data,
                dut.u_framer.msg_first, dut.u_framer.msg_last,
                dut.u_decoder.pos, dut.u_decoder.done, out_valid);
        end
    end

    initial begin
        $display("cyc  | iv in  | framer   rem  | mv md  f l |   pos  done | out_valid");
        $display("-----+--------+---------------+------------+-------------+----------");
        repeat (2) @(posedge clk);
        rst <= 1'b0;
        wait (out_valid);
        @(negedge clk);
        #1;                       // let the table row for this cycle print first
        $display("");
        $display("Decoded: type=%c len=%0d supported=%b loc=%0d trk=%0d ts=%0d",
                 out_type, out_len, out_supported, out_locate, out_tracking, out_timestamp);
        $display("         ref=%0d side=%c shares=%0d stock=\"%s\" price=%0d (= %0d.%0d)",
                 out_order_ref, out_side, out_shares, out_stock, out_price,
                 out_price / 10000, out_price % 10000);
        repeat (2) @(posedge clk);
        $finish;
    end

endmodule
