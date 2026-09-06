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
target. The resampler in `capture.rs` keeps a dominant tone pitch-accurate to
~1–2 cents even when starved, but real broadband audio would lose its top
octave whenever the native rate drops below ~44 kHz. Batching conversions into
one `SPI_IOC_MESSAGE` makes it *worse* (per-CS-change overhead). The durable
fix, if the headroom proves inadequate in the field, is the kernel `mcp320x`
IIO driver with an hrtimer trigger (hardware-paced, DMA).
