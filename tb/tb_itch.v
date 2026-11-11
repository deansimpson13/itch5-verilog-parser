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
    wire [7:0]  out_type, out_side;
    wire [15:0] out_len, out_locate, out_tracking;
    wire [47:0] out_timestamp;
    wire [63:0] out_order_ref, out_stock;
    wire [31:0] out_shares, out_price;

    itch_top dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
        .out_valid(out_valid), .out_type(out_type), .out_len(out_len),
        .out_supported(out_supported),
        .out_locate(out_locate), .out_tracking(out_tracking), .out_timestamp(out_timestamp),
        .out_order_ref(out_order_ref), .out_side(out_side), .out_shares(out_shares),
        .out_stock(out_stock), .out_price(out_price)
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
                case (out_type)
                    "A": begin
                        $fwrite(fd, " ref=%0d side=", out_order_ref);  put_alpha(out_side, 1);
                        $fwrite(fd, " shares=%0d stock=", out_shares); put_alpha(out_stock, 8);
                        $fwrite(fd, " price=");                        put_price(out_price);
                    end
                    default: $fwrite(fd, " ???");   // decoder said supported but TB can't print it
                endcase
                $fwrite(fd, "\n");
            end
        end
    end

    // ---- main sequence ----
    reg [1023:0] out_path;
    integer cycles = 0;
    initial begin
        if (!$value$plusargs("STALL=%d", stall_pct)) stall_pct = 0;
        if (!$value$plusargs("SEED=%d", seed))       seed = 1;
        if (!$value$plusargs("OUT=%s", out_path))    out_path = "build/actual.txt";
        if ($test$plusargs("DUMP")) begin
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
        end
        repeat (10) @(posedge clk);          // let the last message drain out

        $fclose(fd);
        $display("tb_itch: %0d bytes in %0d cycles, stall=%0d%%: %0d messages (%0d decoded, %0d skipped)",
                 `STREAM_LEN, cycles, stall_pct, seq, n_decoded, n_skipped);
        if (ptr < `STREAM_LEN) $fatal(1, "timeout: input not fully consumed");
        $finish;
    end

endmodule
