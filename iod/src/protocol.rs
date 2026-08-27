//! interface exposed to the backend: a unix domain socket carrying
//! newline delimited json
//!
//! a connection is either a control connection or a stream connection for
//! its whole lifetime: send start_stream and the daemon stops replying to
//! further commands on that connection and instead pushes one line per
//! sample frame until the connection closes. everything else is plain
//! request/response.
//!
//! example traffic:
//!
//! ```text
//! -> {"cmd":"start_capture"}
//! <- {"ok":true}
//! -> {"cmd":"stop_capture"}
//! <- {"ok":true,"wav_path":"/.../runtime/captures/20260826-...wav","duration_s":4.2}
//!
//! -> {"cmd":"play","path":"/.../runtime/outputs/song.wav"}
//! <- {"ok":true}
//! -> {"cmd":"status"}
//! <- {"ok":true,"capturing":false,"streaming":false,"stream_subscribers":0,"playing":true,"paused":false,"position_secs":1.2,"duration_secs":4.2}
//!
//! -> {"cmd":"start_stream"}
//! <- {"ok":true}
//! <- {"sample_index":88200,"playback_position_secs":4.35,"samples":"<base64 pcm16>"}
//! <- {"sample_index":88641,"playback_position_secs":4.37,"samples":"<base64 pcm16>"}
//! ...
//! ```

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Mutex};
use std::thread;

use base64::Engine;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde::{Deserialize, Serialize};

use crate::capture::{AdcSampler, DEFAULT_STREAM_FRAME_SAMPLES, SampleFrame};
use crate::i2s::Playback;

/// list of all commands
#[derive(Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
enum Command {
    StartCapture,
    StopCapture,
    Play { path: String },
    Pause,
    Resume,
    StopPlayback,
    Seek { position_secs: f64 },
    SetVolume { volume: f32 },
    Status,
    /// switches this connection into stream mode. frame_samples defaults to
    /// DEFAULT_STREAM_FRAME_SAMPLES if omitted.
    StartStream {
        #[serde(default)]
        frame_samples: Option<u32>,
    },
}

/// possible responses
#[derive(Serialize, Default)]
struct Response {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    wav_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    duration_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    capturing: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    streaming: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stream_subscribers: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    playing: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    paused: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    position_secs: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    duration_secs: Option<f64>,
}

impl Response {
    fn ok() -> Self {
        Self { ok: true, ..Default::default() }
    }

    fn err(message: impl Into<String>) -> Self {
        Self { ok: false, error: Some(message.into()), ..Default::default() }
    }
}

/// one line pushed unprompted to a stream connection
#[derive(Serialize)]
struct StreamFrameMsg {
    sample_index: u64,
    playback_position_secs: f64,
    /// base64-encoded little-endian pcm16 samples
    samples: String,
}

pub struct Daemon {
    sampler: AdcSampler,
    playback: Arc<Mutex<Playback>>,
}

impl Daemon {
    pub fn new(sampler: AdcSampler, playback: Playback) -> Self {
        Self { sampler, playback: Arc::new(Mutex::new(playback)) }
    }

    /// serves connections until the process is killed
    pub fn serve(self, socket_path: impl AsRef<Path>) -> std::io::Result<()> {
        let socket_path = socket_path.as_ref();
        if let Some(parent) = socket_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let _ = std::fs::remove_file(socket_path);

        let listener = UnixListener::bind(socket_path)?;
        let this = Arc::new(self);
        for stream in listener.incoming() {
            let stream = match stream {
                Ok(stream) => stream,
                Err(err) => {
                    eprintln!("chordsense-iod: accept failed: {err}");
                    continue;
                }
            };
            let this = Arc::clone(&this);
            thread::spawn(move || this.handle_connection(stream));
        }
        Ok(())
    }

    fn handle_connection(&self, stream: UnixStream) {
        let reader = BufReader::new(stream.try_clone().expect("clone unix stream"));
        let mut writer = stream;
        for line in reader.lines() {
            let line = match line {
                Ok(line) => line,
                Err(_) => break,
            };
            if line.trim().is_empty() {
                continue;
            }
            let cmd = match serde_json::from_str::<Command>(&line) {
                Ok(cmd) => cmd,
                Err(err) => {
                    write_line(&mut writer, &Response::err(format!("bad request: {err}")));
                    continue;
                }
            };

            // start_stream hands this connection over to the frame-pushing
            // loop for good; it never goes back to reading commands.
            if let Command::StartStream { frame_samples } = cmd {
                let frame_samples =
                    frame_samples.map(|n| n as usize).unwrap_or(DEFAULT_STREAM_FRAME_SAMPLES);
                match self.sampler.start_stream(frame_samples) {
                    Ok((id, rx)) => {
                        if write_line(&mut writer, &Response::ok()) {
                            self.forward_stream(&mut writer, rx);
                        }
                        self.sampler.stop_stream(id);
                    }
                    Err(err) => {
                        write_line(&mut writer, &Response::err(err));
                    }
                }
                break;
            }

            let response = self.dispatch(cmd);
            if !write_line(&mut writer, &response) {
                break;
            }
        }
    }

    /// pushes sample frames to a stream connection until the client
    /// disconnects (write fails) or the channel is torn down
    fn forward_stream(&self, writer: &mut UnixStream, rx: Receiver<SampleFrame>) {
        while let Ok(frame) = rx.recv() {
            let playback_position_secs = self.playback.lock().unwrap().position_secs();
            let msg = StreamFrameMsg {
                sample_index: frame.sample_index,
                playback_position_secs,
                samples: BASE64.encode(pcm16_to_bytes(&frame.samples)),
            };
            let mut payload = match serde_json::to_string(&msg) {
                Ok(payload) => payload,
                Err(_) => continue,
            };
            payload.push('\n');
            if writer.write_all(payload.as_bytes()).is_err() {
                break;
            }
        }
    }

    fn dispatch(&self, cmd: Command) -> Response {
        match cmd {
            Command::StartCapture => {
                let path = capture_path();
                match self.sampler.start_capture(&path) {
                    Ok(()) => Response::ok(),
                    Err(err) => Response::err(err),
                }
            }
            Command::StopCapture => match self.sampler.stop_capture() {
                Ok(result) => Response {
                    wav_path: Some(result.wav_path.to_string_lossy().into_owned()),
                    duration_s: Some(result.duration_s),
                    ..Response::ok()
                },
                Err(err) => Response::err(err),
            },
            Command::Play { path } => {
                let mut playback = self.playback.lock().unwrap();
                match playback.load(&path) {
                    Ok(()) => {
                        playback.play();
                        Response::ok()
                    }
                    Err(err) => Response::err(err),
                }
            }
            Command::Pause => {
                self.playback.lock().unwrap().pause();
                Response::ok()
            }
            Command::Resume => {
                self.playback.lock().unwrap().play();
                Response::ok()
            }
            Command::StopPlayback => match self.playback.lock().unwrap().stop() {
                Ok(()) => Response::ok(),
                Err(err) => Response::err(err),
            },
            Command::Seek { position_secs } => {
                match self.playback.lock().unwrap().seek(position_secs) {
                    Ok(()) => Response::ok(),
                    Err(err) => Response::err(err),
                }
            }
            Command::SetVolume { volume } => {
                self.playback.lock().unwrap().set_volume(volume);
                Response::ok()
            }
            Command::Status => {
                let playback = self.playback.lock().unwrap();
                Response {
                    capturing: Some(self.sampler.is_capturing()),
                    streaming: Some(self.sampler.is_streaming()),
                    stream_subscribers: Some(self.sampler.stream_subscriber_count() as u32),
                    playing: Some(!playback.is_paused() && !playback.is_finished()),
                    paused: Some(playback.is_paused()),
                    position_secs: Some(playback.position_secs()),
                    duration_secs: playback.duration_secs(),
                    ..Response::ok()
                }
            }
            // handled in handle_connection before dispatch() is ever called;
            // kept here so the match stays exhaustive.
            Command::StartStream { .. } => {
                Response::err("start_stream must be the only command sent on a connection")
            }
        }
    }
}

/// writes one json line + newline. returns false if the write failed (the
/// client is gone), signaling the caller to stop handling this connection.
fn write_line(writer: &mut UnixStream, response: &Response) -> bool {
    let mut payload = serde_json::to_string(response).unwrap_or_else(|_| {
        "{\"ok\":false,\"error\":\"internal: response serialization failed\"}".to_string()
    });
    payload.push('\n');
    writer.write_all(payload.as_bytes()).is_ok()
}

/// pcm16 samples as raw little-endian bytes, for base64 encoding
fn pcm16_to_bytes(samples: &[i16]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(samples.len() * 2);
    for &s in samples {
        bytes.extend_from_slice(&s.to_le_bytes());
    }
    bytes
}

fn capture_path() -> PathBuf {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    crate::capture::default_captures_dir().join(format!("capture-{timestamp}.wav"))
}
