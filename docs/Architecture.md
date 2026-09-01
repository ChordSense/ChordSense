# ChordSense High-Level Architecture and FSM

## Purpose

ChordSense helps guitarists record performances, generate chord tabs, and play along with songs while viewing synchronized chord diagrams. This specification defines the high-level device contract: system boundaries, interfaces, modes, canonical states, state transitions, and required behavior.

## System Boundary

### ChordSense device responsibilities

ChordSense shall:

- capture mono guitar input
- import song audio from the companion service over Wi-Fi or from a USB flash drive
- locally cache songs for offline use
- analyze audio to generate time-aligned chord and tab data
- play backing-track audio while displaying synchronized chord diagrams
- optionally compare the detected live guitar chord with the expected chord
- save a limited set of recordings and tabs locally while keeping a larger data store in a database
- synchronize recordings, tabs, song metadata, and cache selections with the companion app
- remain usable offline

### Companion app and cloud responsibilities

The companion system shall provide:

- user accounts and authentication
- the user's full song library
- audio upload and song metadata management
- selection of songs to cache on ChordSense
- long-term recording and tab storage
- viewing saved tabs away from ChordSense

## Compute and Display Platform

- Compute platform: **Raspberry Pi 5**.
- Local display: official Raspberry Pi Touch Display 2, 10-inch, rotated to 1920 × 1200 landscape.
- Touch input may duplicate physical controls but shall not replace them.
- HDMI shall mirror the device display to an external monitor or television.

## Interfaces

### Physical ports

| Interface                          | Direction | Contract                                                                                                              |
| ---------------------------------- | --------: | --------------------------------------------------------------------------------------------------------------------- |
| 6.35 mm high-impedance guitar jack |     Input | Captures guitar for recording and Live Feedback analysis.                                                             |
| 6.35 mm line/audio jack            |    Output | Sends backing-track audio to a compatible amplifier, powered speaker, or downstream audio device.                     |
| 3.5 mm stereo jack                 |    Output | Sends backing-track audio to headphones or a powered speaker. Insertion automatically mutes the 6.35 mm audio output. |
| USB-A host                         |     Input | Imports supported audio files from FAT32 or exFAT flash drives. ChordSense shall not modify the drive.                |
| HDMI                               |    Output | Mirrors the ChordSense UI to a monitor or television.                                                                 |
| USB-C power                        |     Input | Powers the Raspberry Pi, display, and ChordSense electronics subject to the finalized power budget.                   |

### Wireless interfaces

| Interface            | Contract                                                                                                                                                |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wi-Fi                | Primary path for audio files, library synchronization, recording upload, tab upload, and account communication.                                         |
| Bluetooth Low Energy | Device discovery, pairing, Wi-Fi credential provisioning, connection status, and lightweight controls. BLE is not the primary audio-file transfer path. |

### Physical controls

| Control               | Stable-state behavior                  | Context-specific behavior                                          |
| --------------------- | -------------------------------------- | ------------------------------------------------------------------ |
| Power                 | Boot or request safe shutdown          | A long hold is emergency shutdown.                                 |
| Mode                  | Toggle Play Along and Record           | Disabled in locked states.                                         |
| Song Select / Library | Open the song library                  | Return to the library from an idle song screen.                    |
| Play                  | Select, start, confirm, play, or pause | Starts recording in Record Ready; accepts a take in Record Review. |
| Stop                  | Stop recording or playback             | Discards the take in Record Review and returns to Record Ready.    |
| Back                  | Previous song, option, or tab region   | Hold to seek backward during playback.                             |
| Forward               | Next song, option, or tab region       | Hold to seek forward during playback.                              |
| Volume knob           | Set backing-track volume               | Does not affect chord analysis.                                    |
| Mute                  | Toggle audio-output mute               | Does not pause playback or analysis.                               |

The UI shall show the current meaning of Play and Stop whenever their actions are context-dependent.

## Audio Contract

- Headphone insertion automatically selects the headphone route and mutes the 6.35 mm audio output.
- Removing headphones restores the prior output route and volume unless global Mute is active.
- Recorded tracks do not include playback audio, as they are limited to tabs.

## Storage Contract

ChordSense maintains up to seven unique locally cached audio songs:

- up to six user-pinned songs selected through the companion app; and
- one automatically managed Most Recent slot for the latest opened or imported unpinned song.

If the most recent song is already pinned, it occupies only one cache entry.

Eviction priority:

1. Never automatically evict a pinned song.
2. Never evict an unsynchronized recording or generated tab.
3. Evict the least-recently-used unpinned audio file first.
4. Retain small tab and metadata records after audio eviction when storage permits.
5. If no safe candidate exists, enter `STORAGE_BLOCKED`.

Local saves shall be atomic. Cloud synchronization may finish later and shall not block offline use.

## Modes

### Play Along

The default mode after boot. A user selects a cached song or imports audio through the companion service or USB. Unanalyzed audio must be analyzed and its tab saved before playback.

Play Along provides two playback choices:

- **Standard Playback:** play the backing track and display synchronized previous, current, and next chord diagrams.
- **Live Feedback:** perform Standard Playback while detecting guitar input and comparing the detected chord identity with the expected current chord.

### Record

The user connects a guitar, presses Play to start, and presses Stop to finish the take. ChordSense presents a review state instead of analyzing automatically:

- Play accepts the take, analyzes it, and saves it.
- Stop discards the take and returns to Record Ready.

The saved recording becomes available in Play Along and its tab/recording is queued for account synchronization.

## FSM Rules

- ChordSense boots into `PLAY_BROWSE`.
- Mode changes are allowed only in stable, non-destructive states.
- Mode and Library inputs are rejected in locked states, with an explanation on screen.
- Volume, Mute, headphone detection, HDMI connection, and connectivity status are orthogonal to the primary activity state.
- A recoverable error shall preserve user data where possible and map Play to Retry/Confirm and Stop to Cancel/Back.
- A normal power request during capture, import, analysis, or save is deferred until a safe boundary.
- Emergency power-off may lose the active temporary operation but shall not corrupt previously committed content.

## Canonical States

### System lifecycle states

| State           | Definition                                                             |
| --------------- | ---------------------------------------------------------------------- |
| `OFF`           | Device is powered down.                                                |
| `BOOTING`       | Hardware, storage, database, UI, audio, and services are initializing. |
| `SYSTEM_ERROR`  | Initialization or system health prevents normal operation.             |
| `SHUTTING_DOWN` | Services stop and storage is safely unmounted.                         |

Successful boot transitions to `PLAY_BROWSE`.

### Record states

| State                    |   Locked?   | Definition and transitions                                                     |
| ------------------------ | :---------: | ------------------------------------------------------------------------------ |
| `RECORD_READY`           |     No      | Input is ready. Play starts the countdown. Mode may return to Play Along.      |
| `RECORD_COUNTDOWN`       |     Yes     | Visual count-in. Completion starts recording; Stop cancels.                    |
| `RECORDING`              |     Yes     | Captures guitar and shows elapsed time/input level. Stop finalizes the take.   |
| `RECORD_FINALIZING`      |     Yes     | Closes and validates the recorded file. Success enters Review.                 |
| [118;1:3u`RECORD_REVIEW` |     Yes     | Play accepts/analyzes/saves; Stop discards and returns to Ready.               |
| `RECORD_ANALYZING`       |     Yes     | Generates time-aligned chords and tab data.                                    |
| `RECORD_SAVING`          |     Yes     | Atomically commits audio, metadata, and tab.                                   |
| `RECORD_COMPLETE`        |     No      | Confirms local save and sync status. The result is available in Play Along.    |
| `RECORD_ERROR`           | Conditional | Preserves the take when possible and allows Retry, Back to Review, or Discard. |

### Play Along browse, import, and analysis states

| State               |   Locked?   | Definition and transitions                                                   |
| ------------------- | :---------: | ---------------------------------------------------------------------------- |
| `PLAY_BROWSE`       |     No      | Default post-boot state. Browse cached, preloaded, app, and USB sources.     |
| `SOURCE_BROWSE`     |     No      | Browse companion-library or USB candidates.                                  |
| `IMPORT_VALIDATING` |     Yes     | Validate file type, readability, duration, and available storage.            |
| `IMPORT_COPYING`    |     Yes     | Copy into temporary local storage and verify the checksum.                   |
| `CACHE_COMMITTING`  |     Yes     | Apply eviction rules and atomically commit the imported song.                |
| `SONG_DETAILS`      |     No      | Show metadata, cache state, and analysis readiness.                          |
| `PLAY_ANALYZING`    |     Yes     | Generate and locally save chord/tab data for an unanalyzed song.             |
| `PLAY_READY`        |     No      | Song and tab are ready; choose Standard Playback or Live Feedback.           |
| `IMPORT_ERROR`      | Conditional | Unsupported file, I/O, disconnect, network, checksum, or analysis failure.   |
| `STORAGE_BLOCKED`   |     Yes     | No safe cache entry can be evicted. User must unpin, synchronize, or cancel. |

### Standard Playback states

| State               | Locked? | Definition and transitions                                                              |
| ------------------- | :-----: | --------------------------------------------------------------------------------------- |
| `STANDARD_PLAYING`  |   Yes   | Play backing audio and advance the synchronized tab. Play pauses; Stop resets to Ready. |
| `STANDARD_PAUSED`   |   Yes   | Hold playback position and tab display. Play resumes; Stop resets.                      |
| `PLAYBACK_COMPLETE` |   No    | End-of-song screen. Play replays; Library returns to Browse.                            |

### Live Feedback states

| State                     | Locked? | Definition and transitions                                                             |
| ------------------------- | :-----: | -------------------------------------------------------------------------------------- |
| `FEEDBACK_CHECKING_INPUT` |   Yes   | Confirm that guitar input is present and usable.                                       |
| `FEEDBACK_PLAYING`        |   Yes   | Play backing track, detect guitar chord, compare with expected chord, and advance tab. |
| `FEEDBACK_PAUSED`         |   Yes   | Pause backing track, tab advancement, and feedback evaluation.                         |
| `FEEDBACK_COMPLETE`       |   No    | Show a basic correct/incorrect summary.                                                |
| `FEEDBACK_INPUT_ERROR`    |   Yes   | Guitar is disconnected or unusable. Backing track pauses; Retry or Stop is available.  |

Live Feedback comparison contract:

- detected chord equals normalized expected chord: green overlay;
- detected chord differs: red overlay;
- no reliable detection: neutral/gray state, not an incorrect result; and
- rhythm, onset timing, voicing quality, and fingering quality are outside the MVP.

### Connectivity and synchronization states

These states run alongside the primary activity FSM.

| State           | Definition                                                           |
| --------------- | -------------------------------------------------------------------- |
| `UNPAIRED`      | No companion relationship exists.                                    |
| `BLE_PAIRED`    | Companion discovery and provisioning are available.                  |
| `WIFI_OFFLINE`  | Cloud is unavailable; local features remain operational.             |
| `ONLINE_IDLE`   | Account and cloud services are reachable.                            |
| `SYNCING`       | Audio, tab, recording, metadata, or cache selection is transferring. |
| `SYNC_PENDING`  | Local changes are waiting for connectivity.                          |
| `SYNC_CONFLICT` | Local and remote metadata need deterministic resolution.             |
| `AUTH_REQUIRED` | Authentication is missing or expired.                                |

Conflict rules:

- audio and generated analysis are immutable versioned assets;
- newest user-edited metadata wins;
- app pin selections are authoritative; and
- unsynchronized local recordings are never deleted because of a cloud update.

### Orthogonal output states

| State                 | Definition                                                             |
| --------------------- | ---------------------------------------------------------------------- |
| `AUDIO_OUTPUT_ACTIVE` | 6.35 mm backing-track output is active.                                |
| `HEADPHONES_ACTIVE`   | Headphones are detected and the 6.35 mm output is automatically muted. |
| `AUDIO_MUTED`         | User Mute suppresses all backing-track output without pausing.         |
| `LOCAL_DISPLAY_ONLY`  | UI is shown only on the integrated display.                            |
| `HDMI_MIRRORED`       | Integrated UI is also shown through HDMI.                              |

## Required Transition Behavior

### Record flow

`RECORD_READY` → `RECORD_COUNTDOWN` → `RECORDING` → `RECORD_FINALIZING` → `RECORD_REVIEW` → `RECORD_ANALYZING` → `RECORD_SAVING` → `RECORD_COMPLETE`

Alternative transitions:

- Countdown + Stop → `RECORD_READY`.
- Review + Stop → discard → `RECORD_READY`.
- Finalization, analysis, or save failure → `RECORD_ERROR`.
- Complete + open result → `PLAY_READY`.

### Import and preparation flow

`PLAY_BROWSE` → `SOURCE_BROWSE` → `IMPORT_VALIDATING` → `IMPORT_COPYING` → `CACHE_COMMITTING` → `SONG_DETAILS`

From Song Details:

- compatible saved tab → `PLAY_READY`;
- no compatible tab → `PLAY_ANALYZING` → `PLAY_READY`;
- invalid input or failure → `IMPORT_ERROR`; and
- no safe cache space → `STORAGE_BLOCKED`.

### Playback flow

- `PLAY_READY` + Standard selection → `STANDARD_PLAYING`.
- `STANDARD_PLAYING` ⇄ `STANDARD_PAUSED` using Play.
- Stop from either state → `PLAY_READY` at time zero.
- End of song → `PLAYBACK_COMPLETE`.
- `PLAY_READY` + Live Feedback selection → `FEEDBACK_CHECKING_INPUT`.
- Valid input → `FEEDBACK_PLAYING`; invalid input → `FEEDBACK_INPUT_ERROR`.
- `FEEDBACK_PLAYING` ⇄ `FEEDBACK_PAUSED` using Play.
- Guitar loss pauses playback and enters `FEEDBACK_INPUT_ERROR`.
- Stop from feedback playback or pause → `PLAY_READY`.
- End of song → `FEEDBACK_COMPLETE`.

## Failure and Power Behavior

The implementation shall distinguish at least:

- missing guitar input and clipping;
- unsupported or corrupt audio;
- USB removal or network loss during import;
- insufficient safe cache space;
- analysis failure;
- local database or save failure;
- playback output failure;
- authentication or synchronization failure; and
- thermal, undervoltage, or storage-health warnings.

A stable-state Power press enters `SHUTTING_DOWN`. A Power press in a locked state displays a pending-shutdown indication and waits for the nearest safe boundary. Temporary files are cleaned during the next boot after unexpected power loss.

## Acceptance Contract

1. A cold boot ends in `PLAY_BROWSE`.
2. A user can record, stop, review, discard, or analyze/save using Play and Stop.
3. A saved recording becomes available in Play Along and queues for account synchronization.
4. A supported song can be imported over Wi-Fi or from USB.
5. An unanalyzed song cannot enter playback until analysis and local tab save succeed.
6. Playback keeps the tab synchronized through play, pause, stop, and seek.
7. Live Feedback distinguishes correct, incorrect, and no-detection results without timing evaluation.
8. Headphone insertion automatically mutes the 6.35 mm output.
9. Mode switching is rejected in every locked state.
10. Six selected songs plus the most recent unpinned song remain available offline, subject to unsynchronized-data protections.
11. Wi-Fi loss does not prevent cached playback, recording, analysis, or local save.
12. A normal shutdown never interrupts an atomic local commit.
