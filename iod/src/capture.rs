//! continuous ADC sampling loop

use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use hound::{SampleFormat, WavSpec, WavWriter};

use crate::spi::{Mcp3201, raw12_to_i16};

pub const SAMPLE_RATE_HZ: u32 = 22_050;

/// how many pending frames a stream subscriber's channel can hold before we
/// start dropping the newest frame instead of blocking the sampling loop
const STREAM_CHANNEL_CAPACITY: usize = 16;

/// default batch size for a stream subscriber that doesn't ask for a
/// specific one: ~20ms of audio at 22050 Hz
pub const DEFAULT_STREAM_FRAME_SAMPLES: usize = 441;

/// represents a WAV file currently being written to
struct Sink {
    writer: WavWriter<BufWriter<File>>,
    path: PathBuf,
    samples_written: u64,
}

pub struct CaptureResult {
    pub wav_path: PathBuf,
    pub duration_s: f64,
}

/// one raw-sample batch pushed to a live stream subscriber
pub struct SampleFrame {
    /// sampling-loop index of the first sample in this frame
    pub sample_index: u64,
    pub samples: Vec<i16>,
}

struct StreamSubscriber {
    id: u64,
    frame_samples: usize,
    buffer: Vec<i16>,
    buffer_start_index: u64,
    tx: SyncSender<SampleFrame>,
}

/// always exactly one of these — recording and streaming are mutually
/// exclusive by construction (the enum can only ever hold one), not by a
/// runtime check spread across separate flags
enum Mode {
    Idle,
    Capturing(Sink),
    Streaming(Vec<StreamSubscriber>),
}

/// handle for the backend to attach/detach the recording sink or a live
/// stream subscriber
#[derive(Clone)]
pub struct AdcSampler {
    mode: Arc<Mutex<Mode>>,
    running: Arc<AtomicBool>,
    next_subscriber_id: Arc<AtomicU64>,
}

impl AdcSampler {
    /// spawns the free running sampling thread
    pub fn spawn(spi: Mcp3201) -> Self {
        let mode = Arc::new(Mutex::new(Mode::Idle));
        let running = Arc::new(AtomicBool::new(true));

        let thread_mode = Arc::clone(&mode);
        let thread_running = Arc::clone(&running);
        // try to get real-time scheduling priority in the thread
        thread::spawn(move || {
            try_raise_realtime_priority();
            sampling_loop(spi, thread_mode, thread_running);
        });

        Self { mode, running, next_subscriber_id: Arc::new(AtomicU64::new(0)) }
    }

    /// attaches a new WAV sink, errors if a capture or stream is already in progress
    pub fn start_capture(&self, wav_path: impl Into<PathBuf>) -> Result<(), String> {
        let wav_path = wav_path.into();
        let mut guard = self.mode.lock().unwrap();
        match &*guard {
            Mode::Capturing(_) => return Err("capture already in progress".to_string()),
            Mode::Streaming(_) => return Err("cannot start capture while streaming".to_string()),
            Mode::Idle => {}
        }
        if let Some(parent) = wav_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let spec = WavSpec {
            channels: 1,
            sample_rate: SAMPLE_RATE_HZ,
            bits_per_sample: 16,
            sample_format: SampleFormat::Int,
        };
        let writer = WavWriter::create(&wav_path, spec).map_err(|e| e.to_string())?;
        *guard = Mode::Capturing(Sink { writer, path: wav_path, samples_written: 0 });
        Ok(())
    }

    /// detaches and finalizes the current sink, returning where it was written.
    pub fn stop_capture(&self) -> Result<CaptureResult, String> {
        let mut guard = self.mode.lock().unwrap();
        let sink = match std::mem::replace(&mut *guard, Mode::Idle) {
            Mode::Capturing(sink) => sink,
            other => {
                *guard = other;
                return Err("no capture in progress".to_string());
            }
        };
        if sink.samples_written == 0 {
            return Err("no frames captured".to_string());
        }
        let duration_s = sink.samples_written as f64 / SAMPLE_RATE_HZ as f64;
        let path = sink.path.clone();
        sink.writer.finalize().map_err(|e| e.to_string())?;
        Ok(CaptureResult { wav_path: path, duration_s })
    }

    /// subscribes the caller to the live sample feed, errors if a capture is
    /// in progress. returns a subscriber id (needed to unsubscribe) plus the
    /// receiving end of the channel frames get pushed onto
    pub fn start_stream(
        &self,
        frame_samples: usize,
    ) -> Result<(u64, Receiver<SampleFrame>), String> {
        let mut guard = self.mode.lock().unwrap();
        if let Mode::Capturing(_) = &*guard {
            return Err("cannot start stream while capturing".to_string());
        }
        let id = self.next_subscriber_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = mpsc::sync_channel(STREAM_CHANNEL_CAPACITY);
        let subscriber = StreamSubscriber {
            id,
            frame_samples: frame_samples.max(1),
            buffer: Vec::with_capacity(frame_samples.max(1)),
            buffer_start_index: 0,
            tx,
        };
        match &mut *guard {
            Mode::Idle => *guard = Mode::Streaming(vec![subscriber]),
            Mode::Streaming(subs) => subs.push(subscriber),
            Mode::Capturing(_) => unreachable!("checked above"),
        }
        Ok((id, rx))
    }

    /// unsubscribes a stream subscriber (called on client disconnect).
    /// no-op if the id isn't present; falls back to Idle once the last
    /// subscriber is gone
    pub fn stop_stream(&self, id: u64) {
        let mut guard = self.mode.lock().unwrap();
        if let Mode::Streaming(subs) = &mut *guard {
            subs.retain(|s| s.id != id);
            if subs.is_empty() {
                *guard = Mode::Idle;
            }
        }
    }

    pub fn is_capturing(&self) -> bool {
        matches!(&*self.mode.lock().unwrap(), Mode::Capturing(_))
    }

    pub fn is_streaming(&self) -> bool {
        matches!(&*self.mode.lock().unwrap(), Mode::Streaming(_))
    }

    pub fn stream_subscriber_count(&self) -> usize {
        match &*self.mode.lock().unwrap() {
            Mode::Streaming(subs) => subs.len(),
            _ => 0,
        }
    }

    pub fn shutdown(&self) {
        self.running.store(false, Ordering::Relaxed);
    }
}

/// ADC conversions read per loop iteration before draining to the sink. This
/// only bounds how often the loop takes the mode lock and re-times itself — the
/// reads themselves are back-to-back either way. Kept small so a stalled read
/// is noticed promptly.
const READ_CHUNK: usize = 16;

/// Free-running ADC reader.
///
/// Pulls conversions from the MCP3201 continuously and as fast as spidev allows
/// (no artificial per-sample pacing — this thread keeps one core busy for the
/// life of the daemon, which is the intended "always sampling" design), stamps
/// each chunk against a fixed start instant, and resamples that irregular
/// native stream onto an even `SAMPLE_RATE_HZ` grid locked to wall-clock time.
///
/// As long as the native rate stays above `SAMPLE_RATE_HZ`, scheduler jitter or
/// a briefly stalled read turns into a short stretch of interpolated output
/// rather than a gap or a pitch shift, and the long-run output rate stays
/// exactly `SAMPLE_RATE_HZ` (so `samples_written / SAMPLE_RATE_HZ` is an
/// accurate capture duration).
///
/// Note: on the Pi 5 / RP1, per-conversion `cs_change` in a batched
/// `SPI_IOC_MESSAGE` costs more than it saves — one `read_raw12` syscall per
/// conversion measured faster (~45 kS/s peak vs ~23 kS/s batched). If that
/// headroom over `SAMPLE_RATE_HZ` proves too thin under load, the fix is the
/// kernel `mcp320x` IIO driver with an hrtimer trigger (hardware-paced, DMA),
/// not more userspace SPI tricks.
fn sampling_loop(spi: Mcp3201, mode: Arc<Mutex<Mode>>, running: Arc<AtomicBool>) {
    let out_period = 1.0 / SAMPLE_RATE_HZ as f64;
    let start = Instant::now();

    let mut raw: Vec<u16> = Vec::with_capacity(READ_CHUNK);
    let mut out: Vec<i16> = Vec::with_capacity(READ_CHUNK * 4);

    // wall-clock time and value of the last native sample of the previous
    // chunk, so interpolation stays continuous across chunk boundaries
    let mut prev_t = 0.0f64;
    let mut prev_val = 0.0f64;
    let mut primed = false;
    // grid index of the next output sample to emit
    let mut out_index: u64 = 0;
    // throttle for the "native rate too low" warning
    let mut last_warn = Instant::now();

    while running.load(Ordering::Relaxed) {
        raw.clear();
        let mut read_err = None;
        for _ in 0..READ_CHUNK {
            match spi.read_raw12() {
                Ok(code) => raw.push(code),
                Err(err) => {
                    read_err = Some(err);
                    break;
                }
            }
        }
        if raw.is_empty() {
            if let Some(err) = read_err {
                eprintln!("chordsense-iod: SPI read failed: {err}");
            }
            // back off before retrying: under SCHED_FIFO a persistently failing
            // read would otherwise spin this thread and starve the core
            thread::sleep(Duration::from_millis(5));
            continue;
        }
        let now = start.elapsed().as_secs_f64();

        if !primed {
            // seed interpolation state; nothing to emit until we have a span
            prev_t = now;
            prev_val = raw12_to_i16(raw[raw.len() - 1]) as f64;
            out_index = (now / out_period).ceil() as u64;
            primed = true;
            continue;
        }

        // Model this chunk's samples as evenly spaced across (prev_t, now].
        // Reading is continuous (no idle gap between chunks), so this holds
        // closely as long as the native rate stays well above the target.
        let span = (now - prev_t).max(1e-9);
        let step = span / raw.len() as f64;

        let native_rate = raw.len() as f64 / span;
        if native_rate < SAMPLE_RATE_HZ as f64 * 1.1
            && last_warn.elapsed() > Duration::from_secs(2)
        {
            eprintln!(
                "chordsense-iod: ADC native rate ~{native_rate:.0} Hz is near the \
                 {SAMPLE_RATE_HZ} Hz target — capture quality will degrade"
            );
            last_warn = Instant::now();
        }

        out.clear();
        loop {
            let t_out = out_index as f64 * out_period;
            if t_out > now {
                break;
            }
            // position of t_out within this batch: 0 -> prev_val, k -> raw[k-1]
            let pos = ((t_out - prev_t) / step).clamp(0.0, raw.len() as f64);
            let i = pos.floor() as usize;
            let frac = pos - i as f64;
            let left = if i == 0 {
                prev_val
            } else {
                raw12_to_i16(raw[i - 1]) as f64
            };
            let right = if i >= raw.len() {
                raw12_to_i16(raw[raw.len() - 1]) as f64
            } else {
                raw12_to_i16(raw[i]) as f64
            };
            let sample = (left + (right - left) * frac).round();
            out.push(sample.clamp(i16::MIN as f64, i16::MAX as f64) as i16);
            out_index += 1;
        }

        prev_t = now;
        prev_val = raw12_to_i16(raw[raw.len() - 1]) as f64;

        if out.is_empty() {
            continue;
        }

        let mut guard = mode.lock().unwrap();
        match &mut *guard {
            Mode::Idle => {}
            Mode::Capturing(sink) => {
                for &sample in &out {
                    if let Err(err) = sink.writer.write_sample(sample) {
                        eprintln!("chordsense-iod: WAV write failed: {err}");
                        break;
                    }
                    sink.samples_written += 1;
                }
            }
            Mode::Streaming(subs) => {
                let batch_first_index = out_index - out.len() as u64;
                for sub in subs.iter_mut() {
                    for (k, &sample) in out.iter().enumerate() {
                        if sub.buffer.is_empty() {
                            sub.buffer_start_index = batch_first_index + k as u64;
                        }
                        sub.buffer.push(sample);
                        if sub.buffer.len() >= sub.frame_samples {
                            let frame = SampleFrame {
                                sample_index: sub.buffer_start_index,
                                samples: std::mem::take(&mut sub.buffer),
                            };
                            // drop-newest-on-full: a slow reader loses the odd
                            // frame instead of stalling the sampling thread. a
                            // disconnected reader is left for stop_stream() to
                            // clean up (called from the connection handler on
                            // disconnect), not handled here.
                            let _ = sub.tx.try_send(frame);
                        }
                    }
                }
            }
        }
    }
}

/// asks the kernel for SCHED_FIFO to stop scheduling jitter for sampling
/// might not work because needs CAP_SYS_NICE
fn try_raise_realtime_priority() {
    unsafe {
        let param = libc::sched_param { sched_priority: 20 };
        let ret = libc::sched_setscheduler(0, libc::SCHED_FIFO, &param);
        if ret != 0 {
            eprintln!(
                "chordsense-iod: could not raise sampling thread to SCHED_FIFO (needs CAP_SYS_NICE); continuing at normal priority"
            );
        }
    }
}

pub fn default_captures_dir() -> PathBuf {
    Path::new("runtime/captures").to_path_buf()
}
