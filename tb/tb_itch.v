// ============================================================================
// tb_itch.v - Full-parser testbench (framer + decoder).
//
// 1. Loads data/stream.hex into a memory with $readmemh.
// 2. Sends it into itch_top one byte per clock using valid/ready.
//    With +STALL=N, in_valid is randomly dropped on N% of cycles.
// 3. Every time out_valid pulses, writes one line to the output file in
//    EXACTLY the format of tools/itch_ref.py (the Python golden model):
//        <seq> t=<type hex> len=<length> SKIP
//        <seq> t=<type hex> len=<length> <letter> loc=.. trk=.. ts=.. <fields>
// 4. tools/compare.py then diffs this file against build/expected.txt.
//
// Everything that "prints" lives here, in the testbench. The RTL in rtl/
// contains no $display/$fwrite, because real hardware can't print.
//
// Plusargs:  +STALL=<0..99>  +SEED=<n>  +OUT=<file>  +DUMP (write build/wave.vcd)
// ============================================================================
`timescale 1ns / 1ps
`include "stream_len.vh"

module tb_itch;

    // ---- clock and reset ----
    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;                  // 100 MHz

    // ---- input memory and driver state ----
    reg [7:0] mem [0:`STREAM_LEN-1];
    integer   ptr       = 0;
    integer   stall_pct = 0;
    integer   seed      = 1;
    reg       stall     = 1'b0;

    wire       in_valid = !rst && (ptr < `STREAM_LEN) && !stall;
    wire [7:0] in_data  = (ptr < `STREAM_LEN) ? mem[ptr] : 8'h00;
    wire       in_ready;

    // ---- DUT outputs ----
    wire        out_valid, out_supported;
    wire [7:0]  out_type, out_event, out_side, out_printable;
    wire [15:0] out_len, out_locate, out_tracking, out_issue_subtype;
    wire [47:0] out_timestamp;
    wire [63:0] out_stock, out_order_ref, out_new_order_ref, out_match;
    wire [31:0] out_shares, out_price, out_mpid, out_round_lot, out_etp_leverage;
    wire [7:0]  out_mkt_cat, out_fin_status, out_round_lots_only, out_issue_class,
                out_authenticity, out_ssti, out_ipo_flag, out_luld_tier,
                out_etp_flag, out_inverse;

    itch_top dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
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

    // ---- driver ----
    always @(posedge clk) begin
        if (in_valid && in_ready)
            ptr <= ptr + 1;
        stall <= (stall_pct > 0) && (($random(seed) & 32'h7fffffff) % 100 < stall_pct);
    end

    // ---- printing helpers (must match itch_ref.py exactly) ----
    integer fd;

    // Print the low N bytes of v as text: space -> "_", non-printable -> ".".
    task put_alpha;
        input [63:0] v;
        input integer n;
        integer k;
        reg [7:0] c;
        begin
            for (k = n - 1; k >= 0; k = k - 1) begin
                c = v[k*8 +: 8];
                if (c == 8'h20)                      $fwrite(fd, "_");
                else if (c < 8'h21 || c > 8'h7e)     $fwrite(fd, ".");
                else                                 $fwrite(fd, "%c", c);
            end
        end
    endtask

    // Print a Price(4) as whole.ffff. The four fraction digits are printed
    // one at a time so we never depend on "%04d" support.
    task put_price;
        input [31:0] p;
        reg [31:0] frac;
        begin
            frac = p % 10000;
            $fwrite(fd, "%0d.%0d%0d%0d%0d", p / 10000,
                    frac / 1000, (frac / 100) % 10, (frac / 10) % 10, frac % 10);
        end
    endtask

    // ---- monitor: one line per out_valid pulse ----
    integer seq = 0;
    integer n_decoded = 0;
    integer n_skipped = 0;

    always @(posedge clk) begin
        if (out_valid) begin
            seq = seq + 1;
            $fwrite(fd, "%0d t=%h len=%0d", seq, out_type, out_len);
            if (!out_supported) begin
                $fwrite(fd, " SKIP\n");
                n_skipped = n_skipped + 1;
            end else begin
                n_decoded = n_decoded + 1;
                $fwrite(fd, " %c loc=%0d trk=%0d ts=%0d",
                        out_type, out_locate, out_tracking, out_timestamp);
                // Field order and names match decode_fields() in itch_ref.py.
                case (out_type)
                    "S": begin
                        $fwrite(fd, " event=");       put_alpha(out_event, 1);
                    end
                    "R": begin
                        $fwrite(fd, " stock=");       put_alpha(out_stock, 8);
                        $fwrite(fd, " mkt=");         put_alpha(out_mkt_cat, 1);
                        $fwrite(fd, " fsi=");         put_alpha(out_fin_status, 1);
                        $fwrite(fd, " lot=%0d", out_round_lot);
                        $fwrite(fd, " rlo=");         put_alpha(out_round_lots_only, 1);
                        $fwrite(fd, " cls=");         put_alpha(out_issue_class, 1);
                        $fwrite(fd, " sub=");         put_alpha(out_issue_subtype, 2);
                        $fwrite(fd, " auth=");        put_alpha(out_authenticity, 1);
                        $fwrite(fd, " ssti=");        put_alpha(out_ssti, 1);
                        $fwrite(fd, " ipo=");         put_alpha(out_ipo_flag, 1);
                        $fwrite(fd, " luld=");        put_alpha(out_luld_tier, 1);
                        $fwrite(fd, " etp=");         put_alpha(out_etp_flag, 1);
                        $fwrite(fd, " lev=%0d", out_etp_leverage);
                        $fwrite(fd, " inv=");         put_alpha(out_inverse, 1);
                    end
                    "A", "F": begin
                        $fwrite(fd, " ref=%0d side=", out_order_ref);  put_alpha(out_side, 1);
                        $fwrite(fd, " shares=%0d stock=", out_shares); put_alpha(out_stock, 8);
                        $fwrite(fd, " price=");                        put_price(out_price);
                        if (out_type == "F") begin
                            $fwrite(fd, " mpid=");                     put_alpha(out_mpid, 4);
                        end
                    end
                    "E": begin
                        $fwrite(fd, " ref=%0d shares=%0d match=%0d",
                                out_order_ref, out_shares, out_match);
                    end
                    "C": begin
                        $fwrite(fd, " ref=%0d shares=%0d match=%0d printable=",
                                out_order_ref, out_shares, out_match);
                        put_alpha(out_printable, 1);
                        $fwrite(fd, " price=");                        put_price(out_price);
                    end
                    "X": begin
                        $fwrite(fd, " ref=%0d shares=%0d", out_order_ref, out_shares);
                    end
                    "D": begin
                        $fwrite(fd, " ref=%0d", out_order_ref);
                    end
                    "U": begin
                        $fwrite(fd, " oref=%0d nref=%0d shares=%0d price=",
                                out_order_ref, out_new_order_ref, out_shares);
                        put_price(out_price);
                    end
                    default: $fwrite(fd, " ???");   // decoder said supported but TB can't print it
                endcase
                $fwrite(fd, "\n");
            end
        end
    end

    // ---- protocol checks ----
    // out_valid must be a ONE-clock pulse. Every message is at least
    // 3 input bytes (2 length + 1 type), so two pulses can never be adjacent.
    // in_ready must always be 1 in v1 (the parser never applies backpressure).
    reg     prev_out_valid = 1'b0;
    integer proto_errors   = 0;
    always @(posedge clk) begin
        if (!rst) begin
            if (out_valid && prev_out_valid) begin
                $display("ERROR: out_valid high for 2 cycles in a row at %0t", $time);
                proto_errors = proto_errors + 1;
            end
            if (!in_ready) begin
                $display("ERROR: in_ready dropped at %0t", $time);
                proto_errors = proto_errors + 1;
            end
        end
        prev_out_valid <= out_valid;
    end

    // ---- main sequence ----
    reg [1023:0] out_path;
    integer cycles = 0;
    initial begin
        if (!$value$plusargs("STALL=%d", stall_pct)) stall_pct = 0;
        if (!$value$plusargs("SEED=%d", seed))       seed = 1;
        if (!$value$plusargs("OUT=%s", out_path))    out_path = "build/actual.txt";
        if ($test$plusargs("DUMP")) begin
            // Waveform for GTKWave. Only the first 2,000 cycles are dumped
            // (about 60 messages, including the first Add Orders), which
            // keeps the file small.
            $dumpfile("build/wave.vcd");
            $dumpvars(0, tb_itch);
        end

        $readmemh("data/stream.hex", mem);
        fd = $fopen(out_path, "w");
        if (fd == 0) $fatal(1, "cannot open output file");

        repeat (3) @(posedge clk);
        rst <= 1'b0;

        while (ptr < `STREAM_LEN && cycles < 100 * `STREAM_LEN) begin
            @(posedge clk);
            cycles = cycles + 1;
            if (cycles == 2000) $dumpoff;    // no-op unless +DUMP
        end
        repeat (10) @(posedge clk);          // let the last message drain out

        $fclose(fd);
        $display("tb_itch: %0d bytes in %0d cycles, stall=%0d%%: %0d messages (%0d decoded, %0d skipped)",
                 `STREAM_LEN, cycles, stall_pct, seq, n_decoded, n_skipped);
        if (ptr < `STREAM_LEN) $fatal(1, "timeout: input not fully consumed");
        if (proto_errors != 0) $fatal(1, "%0d protocol errors", proto_errors);
        $finish;
    end

endmodule
