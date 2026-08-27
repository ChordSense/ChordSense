//! interface exposed to the backend: a unix domain socket carrying
//! newline delimited json
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
//! <- {"ok":true,"capturing":false,"playing":true,"paused":false,"position_secs":1.2,"duration_secs":4.2}
//! ```

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;

use serde::{Deserialize, Serialize};

use crate::capture::AdcSampler;
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
            let response = match serde_json::from_str::<Command>(&line) {
                Ok(cmd) => self.dispatch(cmd),
                Err(err) => Response::err(format!("bad request: {err}")),
            };
            let mut payload = serde_json::to_string(&response).unwrap_or_else(|_| {
                "{\"ok\":false,\"error\":\"internal: response serialization failed\"}".to_string()
            });
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
                    playing: Some(!playback.is_paused() && !playback.is_finished()),
                    paused: Some(playback.is_paused()),
                    position_secs: Some(playback.position_secs()),
                    duration_secs: playback.duration_secs(),
                    ..Response::ok()
                }
            }
        }
    }
}

fn capture_path() -> PathBuf {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    crate::capture::default_captures_dir().join(format!("capture-{timestamp}.wav"))
}
