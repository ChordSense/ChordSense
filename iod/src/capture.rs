//! continuous ADC sampling loop

use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use hound::{SampleFormat, WavSpec, WavWriter};

use crate::spi::{Mcp3201, raw12_to_i16};

pub const SAMPLE_RATE_HZ: u32 = 22_050;

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

/// handle for the backend to attach/detach the recording sink
#[derive(Clone)]
pub struct AdcSampler {
    sink: Arc<Mutex<Option<Sink>>>,
    running: Arc<AtomicBool>,
}

impl AdcSampler {
    /// spawns the free running sampling thread
    pub fn spawn(spi: Mcp3201) -> Self {
        let sink: Arc<Mutex<Option<Sink>>> = Arc::new(Mutex::new(None));
        let running = Arc::new(AtomicBool::new(true));

        let thread_sink = Arc::clone(&sink);
        let thread_running = Arc::clone(&running);
        /// try to get real time scheduling priority in the thread
        thread::spawn(move || {
            try_raise_realtime_priority();
            sampling_loop(spi, thread_sink, thread_running);
        });

        Self { sink, running }
    }

    /// Attaches a new WAV sink, errors if a capture is already in progress
    pub fn start_capture(&self, wav_path: impl Into<PathBuf>) -> Result<(), String> {
        let wav_path = wav_path.into();
        let mut guard = self.sink.lock().unwrap();
        if guard.is_some() {
            return Err("capture already in progress".to_string());
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
        *guard = Some(Sink { writer, path: wav_path, samples_written: 0 });
        Ok(())
    }

    /// detaches and finalizes the current sink, returning where it was written.
    pub fn stop_capture(&self) -> Result<CaptureResult, String> {
        let sink = self.sink.lock().unwrap().take().ok_or("no capture in progress")?;
        if sink.samples_written == 0 {
            return Err("no frames captured".to_string());
        }
        let duration_s = sink.samples_written as f64 / SAMPLE_RATE_HZ as f64;
        let path = sink.path.clone();
        sink.writer.finalize().map_err(|e| e.to_string())?;
        Ok(CaptureResult { wav_path: path, duration_s })
    }

    pub fn is_capturing(&self) -> bool {
        self.sink.lock().unwrap().is_some()
    }

    pub fn shutdown(&self) {
        self.running.store(false, Ordering::Relaxed);
    }
}

/// sample one spi conversion at 22050 Hz
fn sampling_loop(spi: Mcp3201, sink: Arc<Mutex<Option<Sink>>>, running: Arc<AtomicBool>) {
    let period = Duration::from_secs_f64(1.0 / SAMPLE_RATE_HZ as f64);
    let start = Instant::now();
    let mut n: u64 = 0;

    while running.load(Ordering::Relaxed) {
        // block until we need to sample
        pace(start, period, n);
        n += 1;

        let raw = match spi.read_raw12() {
            Ok(raw) => raw,
            Err(err) => {
                eprintln!("chordsense-iod: SPI read failed: {err}");
                continue;
            }
        };

        if let Some(sink) = sink.lock().unwrap().as_mut() {
            let sample = raw12_to_i16(raw);
            if let Err(err) = sink.writer.write_sample(sample) {
                eprintln!("chordsense-iod: WAV write failed: {err}");
                continue;
            }
            sink.samples_written += 1;
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
