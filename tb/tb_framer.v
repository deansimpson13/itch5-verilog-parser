// ============================================================================
// tb_framer.v - Phase 2 testbench: checks the framer by itself.
//
// Feeds data/stream.hex into itch_framer and writes one line per message:
//     <seq> t=<type hex> len=<length>
// to build/frames_actual.txt.  The Python golden model writes the same
// format to build/frames_expected.txt, and compare.py diffs the two.
//
// The testbench also checks two rules on every beat:
//   * a message has exactly msg_len beats, from msg_first through msg_last
//   * msg_first never arrives in the middle of a message
//
// Plusargs (command-line options for vvp):
//   +STALL=<0..99>   percent of cycles where in_valid is dropped (default 0)
//   +SEED=<n>        random seed for the stalls (default 1)
//   +OUT=<file>      output path (default build/frames_actual.txt)
// ============================================================================
`timescale 1ns / 1ps
`include "stream_len.vh"   // defines STREAM_LEN (written by gen_itch.py)

module tb_framer;

    // ---- clock and reset ----
    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;            // 10 ns period = 100 MHz

    // ---- the whole input file lives in this memory ----
    reg [7:0] mem [0:`STREAM_LEN-1];
    integer   ptr = 0;               // index of the next byte to send

    // ---- stall control ----
    integer stall_pct = 0;
    integer seed      = 1;
    reg     stall     = 1'b0;        // when 1, in_valid is held low this cycle

    // ---- DUT connections ----
    wire       in_valid = !rst && (ptr < `STREAM_LEN) && !stall;
    wire [7:0] in_data  = (ptr < `STREAM_LEN) ? mem[ptr] : 8'h00;
    wire       in_ready;
    wire       msg_valid, msg_first, msg_last;
    wire [7:0] msg_data;
    wire [15:0] msg_len;

    itch_framer dut (
        .clk(clk), .rst(rst),
        .in_valid(in_valid), .in_data(in_data), .in_ready(in_ready),
        .msg_valid(msg_valid), .msg_data(msg_data),
        .msg_first(msg_first), .msg_last(msg_last), .msg_len(msg_len)
    );

    // ---- driver: advance the pointer whenever a byte is accepted ----
    always @(posedge clk) begin
        if (in_valid && in_ready)
            ptr <= ptr + 1;
        // Decide whether NEXT cycle is a stall. $random(seed) is repeatable.
        stall <= (stall_pct > 0) && (($random(seed) & 32'h7fffffff) % 100 < stall_pct);
    end

    // ---- monitor: log each message start and check beat counts ----
    integer fd;
    integer seq      = 0;
    integer beats    = 0;            // beats seen so far in the current message
    integer errors   = 0;
    reg     in_msg   = 1'b0;

    always @(posedge clk) begin
        if (msg_valid) begin
            if (msg_first) begin
                if (in_msg) begin
                    $display("ERROR: msg_first in the middle of message %0d", seq);
                    errors = errors + 1;
                end
                seq    = seq + 1;
                beats  = 0;
                in_msg = 1'b1;
                $fwrite(fd, "%0d t=%h len=%0d\n", seq, msg_data, msg_len);
            end
            beats = beats + 1;
            if (msg_last) begin
                if (beats != msg_len) begin
                    $display("ERROR: message %0d had %0d beats, msg_len=%0d", seq, beats, msg_len);
                    errors = errors + 1;
                end
                in_msg = 1'b0;
            end
        end
    end

    // ---- main sequence ----
    reg [1023:0] out_path;
    integer cycles = 0;
    initial begin
        if (!$value$plusargs("STALL=%d", stall_pct)) stall_pct = 0;
        if (!$value$plusargs("SEED=%d", seed))       seed = 1;
        if (!$value$plusargs("OUT=%s", out_path))    out_path = "build/frames_actual.txt";

        $readmemh("data/stream.hex", mem);
        fd = $fopen(out_path, "w");
        if (fd == 0) begin
            $display("ERROR: cannot open %0s", out_path);
            $finish;
        end

        repeat (3) @(posedge clk);
        rst <= 1'b0;

        // Run until every byte is sent, plus a few cycles to flush the pipeline.
        while (ptr < `STREAM_LEN && cycles < 100 * `STREAM_LEN) begin
            @(posedge clk);
            cycles = cycles + 1;
        end
        repeat (5) @(posedge clk);

        if (ptr < `STREAM_LEN) begin
            $display("ERROR: timeout, only %0d of %0d bytes sent", ptr, `STREAM_LEN);
            errors = errors + 1;
        end
        if (in_msg) begin
            $display("ERROR: stream ended in the middle of message %0d", seq);
            errors = errors + 1;
        end

        $fclose(fd);
        $display("tb_framer: %0d bytes, %0d messages, stall=%0d%%, %0d errors",
                 `STREAM_LEN, seq, stall_pct, errors);
        if (errors != 0) $fatal(1, "tb_framer FAILED");
        $finish;
    end

endmodule
