//! ADC sampling while a recording or live stream is active

use std::fs::File;
use std::io::{self, BufWriter};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
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
    /// CLOCK_MONOTONIC time when the final sample became available
    pub captured_at_ns: u64,
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

struct SamplingState {
    mode: Mode,
    /// Changes whenever sampling restarts, even if the worker did not observe
    /// the intervening Idle mode. Interpolation must never bridge that gap.
    generation: u64,
}

struct SamplerControl {
    state: Mutex<SamplingState>,
    wake: Condvar,
}

impl SamplerControl {
    fn new() -> Self {
        Self {
            state: Mutex::new(SamplingState { mode: Mode::Idle, generation: 0 }),
            wake: Condvar::new(),
        }
    }

    fn wait_for_active<'a>(&'a self, running: &AtomicBool) -> Option<MutexGuard<'a, SamplingState>> {
        let mut guard = self.state.lock().unwrap();
        while matches!(guard.mode, Mode::Idle) && running.load(Ordering::Acquire) {
            guard = self.wake.wait(guard).unwrap();
        }
        running.load(Ordering::Acquire).then_some(guard)
    }
}

/// handle for the backend to attach/detach the recording sink or a live
/// stream subscriber
#[derive(Clone)]
pub struct AdcSampler {
    control: Arc<SamplerControl>,
    running: Arc<AtomicBool>,
    next_subscriber_id: Arc<AtomicU64>,
}

impl AdcSampler {
    /// Spawns a sampling thread that sleeps until a capture or stream starts.
    pub fn spawn(spi: Mcp3201) -> Self {
        let control = Arc::new(SamplerControl::new());
        let running = Arc::new(AtomicBool::new(true));

        let thread_control = Arc::clone(&control);
        let thread_running = Arc::clone(&running);
        // try to get real-time scheduling priority in the thread
        thread::spawn(move || {
            try_raise_realtime_priority();
            sampling_loop(move || spi.read_raw12(), thread_control, thread_running);
        });

        Self { control, running, next_subscriber_id: Arc::new(AtomicU64::new(0)) }
    }

    /// attaches a new WAV sink, errors if a capture or stream is already in progress
    pub fn start_capture(&self, wav_path: impl Into<PathBuf>) -> Result<(), String> {
        let wav_path = wav_path.into();
        let mut guard = self.control.state.lock().unwrap();
        match &guard.mode {
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
        guard.generation += 1;
        guard.mode = Mode::Capturing(Sink { writer, path: wav_path, samples_written: 0 });
        self.control.wake.notify_one();
        Ok(())
    }

    /// detaches and finalizes the current sink, returning where it was written.
    pub fn stop_capture(&self) -> Result<CaptureResult, String> {
        let mut guard = self.control.state.lock().unwrap();
        let sink = match std::mem::replace(&mut guard.mode, Mode::Idle) {
            Mode::Capturing(sink) => sink,
            other => {
                guard.mode = other;
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
        let mut guard = self.control.state.lock().unwrap();
        if let Mode::Capturing(_) = &guard.mode {
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
        match &mut guard.mode {
            Mode::Idle => {
                guard.generation += 1;
                guard.mode = Mode::Streaming(vec![subscriber]);
            }
            Mode::Streaming(subs) => subs.push(subscriber),
            Mode::Capturing(_) => unreachable!("checked above"),
        }
        self.control.wake.notify_one();
        Ok((id, rx))
    }

    /// unsubscribes a stream subscriber (called on client disconnect).
    /// no-op if the id isn't present; falls back to Idle once the last
    /// subscriber is gone
    pub fn stop_stream(&self, id: u64) {
        let mut guard = self.control.state.lock().unwrap();
        if let Mode::Streaming(subs) = &mut guard.mode {
            subs.retain(|s| s.id != id);
            if subs.is_empty() {
                guard.mode = Mode::Idle;
            }
        }
    }

    pub fn is_capturing(&self) -> bool {
        matches!(&self.control.state.lock().unwrap().mode, Mode::Capturing(_))
    }

    pub fn is_streaming(&self) -> bool {
        matches!(&self.control.state.lock().unwrap().mode, Mode::Streaming(_))
    }

    pub fn stream_subscriber_count(&self) -> usize {
        match &self.control.state.lock().unwrap().mode {
            Mode::Streaming(subs) => subs.len(),
            _ => 0,
        }
    }

    pub fn shutdown(&self) {
        // Hold the same lock used by wait_for_active so shutdown cannot land
        // between the predicate check and the condition-variable wait.
        let _guard = self.control.state.lock().unwrap();
        self.running.store(false, Ordering::Release);
        self.control.wake.notify_all();
    }
}

/// ADC conversions read per loop iteration before draining to the sink. This
/// only bounds how often the loop takes the mode lock and re-times itself — the
/// reads themselves are back-to-back either way. Kept small so a stalled read
/// is noticed promptly.
const READ_CHUNK: usize = 16;

/// Free-running ADC reader.
///
/// While a capture or stream is active, pulls conversions from the MCP3201
/// continuously and as fast as spidev allows (no artificial per-sample pacing).
/// It stamps each chunk against a fixed start instant and resamples that irregular
/// native stream onto an even `SAMPLE_RATE_HZ` grid locked to wall-clock time.
///
/// As long as the native rate stays above `SAMPLE_RATE_HZ`, scheduler jitter or
/// a briefly stalled read turns into a short stretch of interpolated output
/// rather than a gap or a pitch shift, and the long-run output rate stays
/// exactly `SAMPLE_RATE_HZ` (so `samples_written / SAMPLE_RATE_HZ` is an
/// accurate capture duration).
/// Between sessions the thread blocks without reading SPI. A new session
/// re-primes interpolation instead of filling the idle interval with samples;
/// stream indices remain on the daemon's monotonic wall-clock grid.
///
/// Note: on the Pi 5 / RP1, per-conversion `cs_change` in a batched
/// `SPI_IOC_MESSAGE` costs more than it saves — one `read_raw12` syscall per
/// conversion measured faster (~45 kS/s peak vs ~23 kS/s batched). If that
/// headroom over `SAMPLE_RATE_HZ` proves too thin under load, the fix is the
/// kernel `mcp320x` IIO driver with an hrtimer trigger (hardware-paced, DMA),
/// not more userspace SPI tricks.
fn sampling_loop(
    mut read_raw12: impl FnMut() -> io::Result<u16>,
    control: Arc<SamplerControl>,
    running: Arc<AtomicBool>,
) {
    let out_period = 1.0 / SAMPLE_RATE_HZ as f64;
    let start = Instant::now();

    let mut raw: Vec<u16> = Vec::with_capacity(READ_CHUNK);
    let mut out: Vec<i16> = Vec::with_capacity(READ_CHUNK * 4);

    // wall-clock time and value of the last native sample of the previous
    // chunk, so interpolation stays continuous across chunk boundaries
    let mut prev_t = 0.0f64;
    let mut prev_val = 0.0f64;
    let mut primed = false;
    let mut active_generation = 0;
    // grid index of the next output sample to emit
    let mut out_index: u64 = 0;
    // throttle for the "native rate too low" warning
    let mut last_warn = Instant::now();

    while let Some(guard) = control.wait_for_active(&running) {
        let generation = guard.generation;
        if generation != active_generation {
            primed = false;
            active_generation = generation;
        }
        // Do not hold the mode lock across SPI reads. A stop request must be
        // able to detach its sink promptly even under SCHED_FIFO sampling.
        // One in-flight read batch may finish; the mode/generation check below
        // discards it after stop or restart.
        drop(guard);
        raw.clear();
        let mut read_err = None;
        for _ in 0..READ_CHUNK {
            match read_raw12() {
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
            out_index = out_index.max((now / out_period).ceil() as u64);
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

        let mut guard = control.state.lock().unwrap();
        if guard.generation != generation {
            // A stop/restart occurred while this batch was interpolated.
            // Never deliver samples from the previous session to the new one.
            continue;
        }
        match &mut guard.mode {
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
                                captured_at_ns: monotonic_ns(),
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

fn monotonic_ns() -> u64 {
    let mut value = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    let result = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut value) };
    if result != 0 {
        return 0;
    }
    (value.tv_sec as u64) * 1_000_000_000 + value.tv_nsec as u64
}

/// asks the kernel for SCHED_FIFO to stop scheduling jitter for sampling
/// might not work because needs CAP_SYS_NICE
#[cfg(target_os = "linux")]
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

// The hardware daemon runs on Linux; allow mock-reader validation on other
// development hosts without invoking an unavailable scheduling syscall.
#[cfg(not(target_os = "linux"))]
fn try_raise_realtime_priority() {}

pub fn default_captures_dir() -> PathBuf {
    Path::new("runtime/captures").to_path_buf()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct MockSampler {
        sampler: AdcSampler,
        reads: Arc<AtomicU64>,
        code: Arc<AtomicU64>,
        finished: Receiver<()>,
        worker: Option<thread::JoinHandle<()>>,
    }

    impl MockSampler {
        fn new(code: u16) -> Self {
            let control = Arc::new(SamplerControl::new());
            let running = Arc::new(AtomicBool::new(true));
            let reads = Arc::new(AtomicU64::new(0));
            let code = Arc::new(AtomicU64::new(code as u64));
            let (finished_tx, finished) = mpsc::channel();
            let sampler = AdcSampler {
                control: Arc::clone(&control),
                running: Arc::clone(&running),
                next_subscriber_id: Arc::new(AtomicU64::new(0)),
            };
            let thread_reads = Arc::clone(&reads);
            let thread_code = Arc::clone(&code);
            let worker = thread::spawn(move || {
                sampling_loop(
                    move || {
                        // Model a conversion without SPI or realtime priority.
                        thread::sleep(Duration::from_micros(20));
                        thread_reads.fetch_add(1, Ordering::Relaxed);
                        Ok(thread_code.load(Ordering::Relaxed) as u16)
                    },
                    control,
                    running,
                );
                let _ = finished_tx.send(());
            });
            Self { sampler, reads, code, finished, worker: Some(worker) }
        }

        fn assert_idle(&self) {
            let before = self.reads.load(Ordering::Relaxed);
            // A stop may leave the batch that was already in flight. It must
            // finish that batch and then block without further SPI reads.
            thread::sleep(Duration::from_millis(20));
            let settled = self.reads.load(Ordering::Relaxed);
            assert!(settled - before <= READ_CHUNK as u64, "ADC kept reading while idle");
            thread::sleep(Duration::from_millis(20));
            assert_eq!(self.reads.load(Ordering::Relaxed), settled, "ADC read while idle");
        }
    }

    impl Drop for MockSampler {
        fn drop(&mut self) {
            self.sampler.shutdown();
            self.finished.recv_timeout(Duration::from_secs(1)).unwrap();
            self.worker.take().unwrap().join().unwrap();
        }
    }

    #[test]
    fn reads_only_while_at_least_one_subscriber_is_active() {
        let mock = MockSampler::new(2048);
        mock.assert_idle();
        let (first_id, first_rx) = mock.sampler.start_stream(32).unwrap();
        let (second_id, second_rx) = mock.sampler.start_stream(32).unwrap();
        let first = first_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        let second = second_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(first.samples, vec![0; 32]);
        assert_eq!(second.samples, vec![0; 32]);

        mock.sampler.stop_stream(first_id);
        assert!(mock.sampler.is_streaming());
        let before = mock.reads.load(Ordering::Relaxed);
        // The remaining subscriber must keep new conversions flowing.
        for _ in 0..100 {
            if mock.reads.load(Ordering::Relaxed) > before {
                break;
            }
            thread::sleep(Duration::from_millis(1));
        }
        assert!(mock.reads.load(Ordering::Relaxed) > before);
        mock.sampler.stop_stream(second_id);
        assert!(!mock.sampler.is_streaming());
        mock.assert_idle();
    }

    #[test]
    fn restarting_re_primes_audio_and_keeps_sample_indices_monotonic() {
        let mock = MockSampler::new(1024);
        let (first_id, first_rx) = mock.sampler.start_stream(32).unwrap();
        let first = first_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(first.samples, vec![-16_384; 32]);
        mock.sampler.stop_stream(first_id);
        mock.assert_idle();
        mock.code.store(3072, Ordering::Relaxed);

        let (second_id, second_rx) = mock.sampler.start_stream(32).unwrap();
        let second = second_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        // No interpolation from the previous signal or synthetic idle audio.
        assert_eq!(second.samples, vec![16_384; 32]);
        assert!(second.sample_index > first.sample_index + first.samples.len() as u64);
        assert!(second.captured_at_ns >= first.captured_at_ns);
        let next = second_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(next.sample_index, second.sample_index + second.samples.len() as u64);
        mock.sampler.stop_stream(second_id);
    }

    #[test]
    fn recording_wakes_sampler_and_finalizes_only_active_samples() {
        let mock = MockSampler::new(3072);
        mock.assert_idle();
        let path = std::env::temp_dir().join(format!(
            "chordsense-idle-capture-{}-{}.wav", std::process::id(), monotonic_ns()
        ));
        mock.sampler.start_capture(&path).unwrap();
        assert!(mock.sampler.is_capturing());
        assert!(mock.sampler.start_stream(32).is_err());
        // Two batches prime and emit; a few more keep this robust on busy hosts.
        for _ in 0..1000 {
            if mock.reads.load(Ordering::Relaxed) >= READ_CHUNK as u64 * 4 {
                break;
            }
            thread::sleep(Duration::from_millis(1));
        }
        let result = mock.sampler.stop_capture().unwrap();
        mock.assert_idle();
        let mut reader = hound::WavReader::open(&result.wav_path).unwrap();
        let samples: Vec<i16> = reader.samples::<i16>().map(Result::unwrap).collect();
        assert!(!samples.is_empty());
        assert!(samples.iter().all(|&sample| sample == 16_384));
        assert_eq!(result.duration_s, samples.len() as f64 / SAMPLE_RATE_HZ as f64);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn stop_capture_does_not_wait_for_an_inflight_spi_read() {
        let control = Arc::new(SamplerControl::new());
        let running = Arc::new(AtomicBool::new(true));
        let sampler = AdcSampler {
            control: Arc::clone(&control),
            running: Arc::clone(&running),
            next_subscriber_id: Arc::new(AtomicU64::new(0)),
        };
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let worker = thread::spawn(move || {
            let mut first_read = true;
            sampling_loop(
                move || {
                    if first_read {
                        first_read = false;
                        entered_tx.send(()).unwrap();
                        release_rx.recv().unwrap();
                    }
                    Ok(2048)
                },
                control,
                running,
            );
        });
        let path = std::env::temp_dir().join(format!(
            "chordsense-stop-latency-{}-{}.wav", std::process::id(), monotonic_ns()
        ));
        sampler.start_capture(&path).unwrap();
        entered_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        let stopped_sampler = sampler.clone();
        let (stopped_tx, stopped_rx) = mpsc::channel();
        let stopper = thread::spawn(move || {
            stopped_tx.send(stopped_sampler.stop_capture()).unwrap();
        });
        let stopped_while_reader_waited = stopped_rx.recv_timeout(Duration::from_millis(300));
        release_tx.send(()).unwrap();
        stopper.join().unwrap();
        sampler.shutdown();
        worker.join().unwrap();
        assert!(stopped_while_reader_waited.is_ok(), "stop waited for SPI read");
        assert!(stopped_while_reader_waited.unwrap().is_err());
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn shutdown_wakes_an_idle_sampler() {
        // Drop waits for the worker to exit, including when it is asleep.
        let mock = MockSampler::new(2048);
        mock.assert_idle();
        drop(mock);
    }
}
