# Audio output: Pi → I2S DAC → headphone jack

All app playback (backing tracks in Play Along, recorded takes after Record) comes out of the pedal's
headphone jack (J4). It never plays in the Tauri window. This doc covers how that path is built, what
we found bringing up the hardware, how to debug it, and what's left.

## Signal path

```
frontend (play-along.js `player`)
  └─ Tauri commands playback_command / playback_status  (src-tauri/src/lib.rs)
      └─ backend /playback/{load,play,pause,stop,seek,volume,status}  (backend/app.py)
          └─ iod socket: load / resume / pause / stop_playback / seek / set_volume / status
              └─ i2s.rs Playback (rodio → cpal → ALSA plughw:CARD=sndrpihifiberry,DEV=0)
                  └─ Pi 5 I2S0: GPIO18 BCLK (pin 12), GPIO19 LRCLK (pin 35), GPIO21 DOUT (pin 40)
                      └─ J3 cable → Adafruit PCM510x board (U1) → LOUT/ROUT
                          └─ VR1 dual volume pot → C2/C6 → IC2 MCP6002 → C4/C5 → J4
```

- **iod owns the DAC.** A hardware ALSA device has a single owner. `iod` opens it at startup and keeps
  the stream running, sending silence when nothing is playing. Anything else that tries to open the DAC
  while `iod` runs fails with "Failed to get the config for the given device" / busy.
- **iod is the clock.** The chord display in `play-along.js` doesn't keep its own time. It polls
  `/playback/status` every 250 ms and interpolates between polls. A generation counter drops any poll
  that was in flight when a newer command (play, seek, …) was sent.
- **Sources.** `load` takes `{path}` (a local file, or the recorded take's `wav_path`) or `{upload}` (a
  song in `runtime/uploads`). The backend only accepts audio extensions and existing files.
- **No fallback.** If `CHORDSENSE_I2S_DEVICE_MATCH` is set and the DAC isn't found, playback reports
  `audio output is unavailable`. It never falls back to HDMI, where it would play into silence.

## Pi setup (done on the dev Pi)

1. `dtoverlay=hifiberry-dac` in `/boot/firmware/config.txt`, then reboot. `aplay -l` shows
   `card N: sndrpihifiberry`. On a Pi 5 this puts GPIO18–21 into I2S mode (`pinctrl get 18-21` shows
   `I2S0_*`).
2. `iod/deploy/wireplumber/51-chordsense-dac.conf` copied into
   `~/.config/wireplumber/wireplumber.conf.d/` (instructions are in the file). This keeps the desktop's
   PipeWire off the card, so `iod` can open it. `wpctl status` should list no HifiBerry sink.
3. `CHORDSENSE_I2S_DEVICE_MATCH=hifiberry` is set in `iod/deploy/chordsense-iod.env` and `iod/run-dev.sh`.
   The systemd unit is installed, and `iod` reports `output_device: plughw:CARD=sndrpihifiberry,DEV=0`.

Stream format: 44.1 kHz, S32_LE stereo, so **BCK = 2.8224 MHz** (64 × fs) and **LRCLK = 44.1 kHz**.
`plughw` converts any file's rate and format.

## Hardware findings from bring-up

The DAC board is the **Adafruit PCM510x** breakout. Its labels are short names for the PCM5102A pins:

| Adafruit label | PCM5102A pin | Required level | Notes |
|---|---|---|---|
| MCK | SCK (master clock) | **GND** | Low lets the chip derive its clock from BCK. **Left no-connect on the PCB; bodge-wired to GND.** |
| MU | XSMT (soft mute) | 3.3 V | Low = muted. PCB ties it to +3.3 V. |
| FM | FMT (format) | GND | Low = I2S. |
| DE | DEMP (de-emphasis) | GND | |
| FIL | FLT (filter) | GND | |
| G | analog ground | GND | |

What we found, in the order it mattered:

1. **BCK and WSEL were swapped** between the Pi header and the DAC. The board's BCK pin measured
   45 kHz, which is the LRCLK frequency. Every pin looked "active", but the DAC couldn't lock and LOUT
   stayed flat. Swapping the two wires fixed it. **TODO:** record whether the swap was in the J3 cable
   or the footprint's pad order (`PCB/chordsense_afe/chordsense_custom.pretty/DAC.kicad_mod`, where pad 3
   = WSEL and pad 5 = BCK), and fix it there.
2. **MCK must be grounded.** The schematic marks it no-connect. Fix in the next PCB revision.
3. **Headphone plug seating.** With the output working, one or both ears only played while the plug was
   forced in. The XM4's recessed jack doesn't fully seat thick plug mouldings; use a slim plug. This is
   not a board fault.
4. **Output stage is weak for headphones.** IC2 (MCP6002: about 20 mA, single 3.3 V supply) drives J4
   through C4/C5, and the schematic sets no values for C4/C5/R7–R10. Into 16–50 Ω headphones, the
   coupling caps need roughly 100–470 µF. A proper headphone amp would be better.

Schematic notes that differ from older docs: **VR1 is the output volume pot** (DAC → VR1 → IC2), not a
guitar-input control. `PI_Din` is the DAC's data input (J3 pin 3), not an SPI line. The MCP3201 ADC uses
`PI_CLK`/`PI_Dout`/`PI_CS`. J3 is the 8-pin cable to the Pi: pin 1 +3.3 V, 2 WSEL, 3 DIN, 4 BCK, 5 SPI
CLK, 6 SPI data, 7 CS, 8 GND.

## Debugging no sound

`iod/deploy/dac_test.sh` plays through the running `iod` service:

```bash
iod/deploy/dac_test.sh              # 8 s of a song at 30%, prints position each second
iod/deploy/dac_test.sh tone         # 1 kHz full-scale sine for ~5 min (unplug headphones!)
iod/deploy/dac_test.sh silence      # comparison readings
iod/deploy/dac_test.sh stop
iod/deploy/dac_test.sh restart      # sudo systemctl restart chordsense-iod
```

Its test files go in `runtime/outputs/`. The service runs with `PrivateTmp=yes`, so it can't see the
user's `/tmp`.

Work from the Pi outward, with the tone playing:

1. **Pi side.** `dac_test.sh song`: the position should count about 1 s per second. The file at
   `/proc/asound/card*/pcm0p/sub0/status` should read `state: RUNNING`.
2. **Clocks at the DAC board (scope).** BCK should be 2.82 MHz, WSEL 44.1 kHz, with 64 BCK per WSEL
   period. DIN should change between tone and silence. A multimeter can't tell BCK from WSEL (both read
   about 1.6 V), so use a scope or a frequency counter.
3. **Static pins.** MCK 0 V, MU 3.3 V, FM 0 V, 3V pin about 3.3 V.
4. **LOUT vs G.** A 1 kHz sine of about 6 V peak-to-peak, centred on 0 V, for the full-scale tone. If
   it looks about 10× small, check the scope probe's ×1/×10 setting.
5. **Downstream.** VR1 wiper → IC2 pins 3/5 → IC2 pins 1/7 (about 1.65 V DC at idle) → C4/C5 → J4 tip and
   ring. Measure with and without headphones plugged in. If the signal collapses under load, the output
   stage (point 4 above) is the cause.

## What's next

Every current playback path already goes through the jack: the Play Along song (local file or upload)
and the recorded take after Record. What's missing is making that hold for the app's **whole
lifetime**. `iod` outlives the app and keeps its own state, so the app has to manage that state
explicitly.

In priority order:

1. **Stop playback when the app exits.** Today, closing the window mid-song leaves the track playing
   out of the jack, with no UI left to stop it. Handle Tauri's `RunEvent::ExitRequested` in
   `lib.rs` with a blocking `POST /playback/stop` (short timeout; ignore errors).
2. **Reset on app start.** Call `stop` from the frontend's init. That clears anything left over from a
   crash or an earlier session, so the UI and `iod` start in agreement.
3. **Recover when `iod` restarts or the output disappears.** A service restart (for example
   `dac_test.sh restart`) leaves `iod` with nothing loaded while the UI still shows a loaded song.
   `status` returns `path`. When it doesn't match what the frontend loaded, re-`load` and `seek` to the
   last position. When playback calls fail (502 / `audio output is unavailable`), show that in the
   status line and disable the transport buttons, instead of only logging to the console.
4. **Files `iod` can't see.** Because of `PrivateTmp`, a song picked from `/tmp` fails to load. In
   `/playback/load`, copy such files into `runtime/inputs/` first. Everything under home, and
   USB drives under `/media`, is already readable.

Decisions for the team before building further:

- **Backing track during Record.** Entering Record mode currently stops playback. Playing along to a
  track while capturing is the natural next feature. `iod` already runs capture and playback together,
  and stream frames carry `playback_position_secs` for aligning them. Two things need designing first:
  how the take is aligned to the track, and keeping the track out of the guitar input.
- **Enclosure buttons** (play, pause, FF, rewind). These should drive `iod` directly so they work without
  the UI. The UI already follows `iod`'s status. Item 3's `path` handling also lets it pick up a track
  that a button, not the UI, started.
