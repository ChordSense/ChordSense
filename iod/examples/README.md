# `iod` diagnostic examples

Bring-up / bench tools for the MCP3201 ADC path. All run on the Pi and need SPI
enabled (`dtparam=spi=on` in `/boot/firmware/config.txt`, then reboot; check
`ls -l /dev/spidev*`). None of them touch the daemon socket — run them with
`chordsense-iod` stopped, and don't run two at once (they contend for the SPI
bus and halve each other's sample rate).

Build all: `cargo build --release --examples`

| Example | What it answers |
|---|---|
| `spi_probe` | Is a signal reaching the ADC at all? Prints per-burst min/max/mean/pk-pk/RMS and the achieved kS/s. Strum → `signal` column reads `YES`. |
| `spi_freq` | What frequency is on the front-end? Autocorrelation pitch detection + a zero-crossing cross-check, plus the measured sample rate. Feed a known tone and confirm `f0`. |
| `spi_capture_test` | Does the *real* capture path keep time? Runs `capture.rs`'s free-running sampler + resampler, records a few seconds to a temp WAV while you feed a steady tone, then reports whole-file `f0` (average timing), per-window `f0` spread (jitter), and WAV-vs-wall-clock duration. |

```bash
cargo run --release --example spi_probe
cargo run --release --example spi_freq                      # -- <device> <spi_hz>
cargo run --release --example spi_capture_test -- 449 6     # expect 449 Hz, record 6 s
```

`examples/support/pitch.rs` is a shared helper module (autocorrelation f0, note
naming, DC removal), not an example binary — hence `autoexamples = false` in
`Cargo.toml` and the explicit `[[example]]` entries.

## Known result (Pi 5 / RP1, MCP3201 @ 1 MHz)

Userspace `spidev` polling tops out around **45 kS/s** and sags toward
~15–20 kS/s under CPU/bus contention — thin headroom over the 22 050 Hz capture
target. Earlier tone tests found roughly 1–2 cents of pitch error, but output
cadence and a single tone do not establish broadband fidelity. Native rates
below 22,050 Hz cannot provide the full bandwidth of the 22,050 Hz output;
linear interpolation cannot recover missing information. Downsampling also
needs a verified anti-alias filter. Batching conversions into one
`SPI_IOC_MESSAGE` was slower in those tests (per-CS-change overhead).

Buffered, paced acquisition is a candidate if headroom is inadequate. The
[upstream `mcp320x` driver](https://github.com/torvalds/linux/blob/master/drivers/iio/adc/mcp320x.c)
uses direct reads, with no triggered-buffer setup. A software hrtimer trigger
alone does not supply hardware pacing or DMA. Check the deployed kernel and
driver capabilities before choosing that migration. See the
[current live feedback audit](../../docs/live-feedback-audit.md).
