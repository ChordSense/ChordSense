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
        /// try to get real time scheduling priority in the thread
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

/// sample one spi conversion at 22050 Hz
fn sampling_loop(spi: Mcp3201, mode: Arc<Mutex<Mode>>, running: Arc<AtomicBool>) {
    let period = Duration::from_secs_f64(1.0 / SAMPLE_RATE_HZ as f64);
    let start = Instant::now();
    let mut n: u64 = 0;

    while running.load(Ordering::Relaxed) {
        // block until we need to sample
        pace(start, period, n);
        let current_index = n;
        n += 1;

        let raw = match spi.read_raw12() {
            Ok(raw) => raw,
            Err(err) => {
                eprintln!("chordsense-iod: SPI read failed: {err}");
                continue;
            }
        };
        let sample = raw12_to_i16(raw);

        let mut guard = mode.lock().unwrap();
        match &mut *guard {
            Mode::Idle => {}
            Mode::Capturing(sink) => {
                if let Err(err) = sink.writer.write_sample(sample) {
                    eprintln!("chordsense-iod: WAV write failed: {err}");
                    continue;
                }
                sink.samples_written += 1;
            }
            Mode::Streaming(subs) => {
                for sub in subs.iter_mut() {
                    if sub.buffer.is_empty() {
                        sub.buffer_start_index = current_index;
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

/// blocks until the nth sample's deadline arrives
fn pace(start: Instant, period: Duration, n: u64) {
    let target = start + period.mul_f64(n as f64);
    loop {
        let now = Instant::now();
        if now >= target {
            return;
        }
        let remaining = target - now;
        if remaining > Duration::from_micros(200) {
            thread::sleep(remaining - Duration::from_micros(100));
        } else {
            std::hint::spin_loop();
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
