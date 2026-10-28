//! Native HTTP and event-stream boundary. Network work never runs on the UI thread.
use reqwest::blocking::{multipart, Client, Response};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant};

pub const BACKEND_URL: &str = "http://127.0.0.1:5051";

#[derive(Debug)]
pub struct Event {
    pub tag: String,
    pub generation: u64,
    pub result: Result<Value, String>,
}

struct Request {
    tag: String,
    generation: u64,
    method: String,
    path: String,
    body: Value,
    revision: u64,
    shutdown_ack: Option<mpsc::Sender<bool>>,
}

pub struct Services {
    pub rx: mpsc::Receiver<Event>,
    tx: mpsc::Sender<Event>,
    controls: mpsc::Sender<Request>,
    playbacks: mpsc::Sender<Request>,
    feedback_revision: Arc<AtomicU64>,
    shutdown: Arc<AtomicBool>,
    control_thread: Option<std::thread::JoinHandle<()>>,
    playback_thread: Option<std::thread::JoinHandle<()>>,
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    audio: crate::audio::AudioWorker,
}

impl Default for Services {
    fn default() -> Self {
        Self::new()
    }
}

impl Services {
    pub fn new() -> Self {
        let (tx, rx) = mpsc::channel();
        let (controls, control_rx) = mpsc::channel::<Request>();
        let (playbacks, playback_rx) = mpsc::channel::<Request>();
        let feedback_revision = Arc::new(AtomicU64::new(0));
        let shutdown = Arc::new(AtomicBool::new(false));
        let event_tx = tx.clone();
        let revision = feedback_revision.clone();
        let stopping = shutdown.clone();
        let playback_tx = tx.clone();
        let playback_stopping = shutdown.clone();
        let playback_thread = std::thread::spawn(move || {
            let mut owns_playback = false;
            for request in playback_rx {
                if let Some(ack) = &request.shutdown_ack {
                    let clean = !owns_playback || cleanup_request("/playback/stop");
                    let _ = ack.send(clean);
                    break;
                }
                if playback_stopping.load(Ordering::SeqCst) {
                    continue;
                }
                let result = perform(&request);
                if result.is_ok() {
                    if request.path == "/playback/load" {
                        owns_playback = true;
                    }
                    if request.path == "/playback/stop" {
                        owns_playback = false;
                    }
                }
                let _ = playback_tx.send(Event {
                    tag: request.tag,
                    generation: request.generation,
                    result,
                });
            }
        });
        // Pairing and feedback are process-owned resources. FIFO control calls
        // ensure closing a dialog/session cannot race an in-flight start.
        let control_thread = std::thread::spawn(move || {
            let mut owns_feedback = false;
            let mut owns_pairing = false;
            let mut owns_capture = false;
            for request in control_rx {
                if let Some(ack) = &request.shutdown_ack {
                    let capture = !owns_capture || cleanup_request("/end_recording?async=1");
                    let feedback = !owns_feedback || cleanup_request("/feedback/stop");
                    let pairing = !owns_pairing || cleanup_request("/api/pairing/cancel");
                    let _ = ack.send(capture && feedback && pairing);
                    break;
                }
                if stopping.load(Ordering::SeqCst) {
                    continue;
                }
                let unowned = (request.path == "/feedback/stop" && !owns_feedback)
                    || (request.path == "/api/pairing/cancel" && !owns_pairing);
                let result = if unowned {
                    Ok(json!({"success":true,"state":"idle"}))
                } else {
                    perform(&request)
                };
                if result.is_ok() {
                    match request.path.as_str() {
                        "/feedback/start" => owns_feedback = true,
                        "/feedback/stop" => owns_feedback = false,
                        "/api/pairing/start" => owns_pairing = true,
                        "/api/pairing/cancel" | "/api/pairing/revoke" => owns_pairing = false,
                        "/begin_recording" => owns_capture = true,
                        path if path.starts_with("/end_recording") => owns_capture = false,
                        _ => {}
                    }
                }
                let session = result
                    .as_ref()
                    .ok()
                    .and_then(|v| v["session_id"].as_str())
                    .map(str::to_owned);
                let starts_feedback = request.path == "/feedback/start";
                let generation = request.generation;
                let current_revision = request.revision;
                let _ = event_tx.send(Event {
                    tag: request.tag,
                    generation,
                    result,
                });
                if starts_feedback && current_revision == revision.load(Ordering::SeqCst) {
                    if let Some(session_id) = session {
                        let tx = event_tx.clone();
                        let revision = revision.clone();
                        let stopping = stopping.clone();
                        std::thread::spawn(move || {
                            stream_feedback(
                                tx,
                                generation,
                                session_id,
                                revision,
                                current_revision,
                                stopping,
                            )
                        });
                    }
                }
            }
        });
        Self {
            #[cfg(any(target_os = "windows", target_os = "macos"))]
            audio: crate::audio::AudioWorker::new(tx.clone()),
            rx,
            tx,
            controls,
            playbacks,
            feedback_revision,
            shutdown,
            control_thread: Some(control_thread),
            playback_thread: Some(playback_thread),
        }
    }

    pub fn request(&self, tag: &str, generation: u64, method: &str, path: &str, body: Value) {
        if self.shutdown.load(Ordering::SeqCst) {
            return;
        }
        let revision = if matches!(path, "/feedback/start" | "/feedback/stop") {
            self.feedback_revision.fetch_add(1, Ordering::SeqCst) + 1
        } else {
            self.feedback_revision.load(Ordering::SeqCst)
        };
        let request = Request {
            tag: tag.into(),
            generation,
            method: method.into(),
            path: path.into(),
            body,
            revision,
            shutdown_ack: None,
        };
        if path.starts_with("/api/pairing/")
            || path.starts_with("/feedback/")
            || path == "/begin_recording"
            || path.starts_with("/end_recording")
        {
            let _ = self.controls.send(request);
        } else if path.starts_with("/playback/") {
            let _ = self.playbacks.send(request);
        } else {
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let result = perform(&request);
                let _ = tx.send(Event {
                    tag: request.tag,
                    generation,
                    result,
                });
            });
        }
    }

    pub fn analyze(&self, tag: &str, generation: u64, path: PathBuf, chord_dict: &str) {
        let tx = self.tx.clone();
        let tag = tag.to_owned();
        let chord_dict = chord_dict.to_owned();
        std::thread::spawn(move || {
            let result = (|| {
                let form = multipart::Form::new()
                    .file("file", &path)
                    .map_err(|e| format!("Could not open audio: {e}"))?
                    .text("chord_dict", chord_dict);
                let client = client(3600)?;
                checked(
                    client
                        .post(format!("{BACKEND_URL}/analyze"))
                        .multipart(form)
                        .send()
                        .map_err(|e| format!("Analysis request failed: {e}"))?,
                )
            })();
            let _ = tx.send(Event {
                tag,
                generation,
                result,
            });
        });
    }

    pub fn playback(&self, tag: &str, generation: u64, action: &str, body: Value) {
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        self.audio.command(tag, generation, action, body);
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        self.request(
            tag,
            generation,
            if action == "status" { "GET" } else { "POST" },
            &format!("/playback/{action}"),
            body,
        );
    }

    pub fn stop_feedback(&self, generation: u64) {
        self.request(
            "feedback_stop",
            generation,
            "POST",
            "/feedback/stop",
            json!({}),
        );
    }

    /// Release resources acquired by this UI and await worker acknowledgments.
    /// False means an unavailable backend prevented cleanup within eight seconds.
    pub fn shutdown(&mut self) -> bool {
        if self.shutdown.swap(true, Ordering::SeqCst) {
            return true;
        }
        self.feedback_revision.fetch_add(1, Ordering::SeqCst);
        let deadline = Instant::now() + Duration::from_secs(8);
        let (control_ack, control_done) = mpsc::channel();
        let (playback_ack, playback_done) = mpsc::channel();
        for (sender, ack) in [
            (&self.controls, control_ack),
            (&self.playbacks, playback_ack),
        ] {
            let _ = sender.send(Request {
                tag: String::new(),
                generation: 0,
                method: String::new(),
                path: String::new(),
                body: Value::Null,
                revision: 0,
                shutdown_ack: Some(ack),
            });
        }
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        let native_clean = self.audio.shutdown();
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        let native_clean = true;
        let control_result =
            control_done.recv_timeout(deadline.saturating_duration_since(Instant::now()));
        let playback_result =
            playback_done.recv_timeout(deadline.saturating_duration_since(Instant::now()));
        if control_result.is_ok() {
            if let Some(handle) = self.control_thread.take() {
                let _ = handle.join();
            }
        }
        if playback_result.is_ok() {
            if let Some(handle) = self.playback_thread.take() {
                let _ = handle.join();
            }
        }
        native_clean && control_result == Ok(true) && playback_result == Ok(true)
    }
}

impl Drop for Services {
    fn drop(&mut self) {
        let _ = self.shutdown();
    }
}

fn cleanup_request(path: &str) -> bool {
    client(2)
        .and_then(|client| {
            client
                .post(format!("{BACKEND_URL}{path}"))
                .json(&json!({}))
                .send()
                .map_err(|e| e.to_string())
        })
        .and_then(checked)
        .is_ok()
}

pub(crate) fn client(timeout_secs: u64) -> Result<Client, String> {
    Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| e.to_string())
}

fn perform(request: &Request) -> Result<Value, String> {
    if !request.path.starts_with('/') || request.path.starts_with("//") {
        return Err("Expected a local API path".into());
    }
    let long = matches!(
        request.path.as_str(),
        "/analyze_uploaded" | "/end_recording"
    );
    let client = client(if long { 3600 } else { 30 })?;
    let method =
        reqwest::Method::from_bytes(request.method.as_bytes()).map_err(|e| e.to_string())?;
    let mut builder = client.request(method.clone(), format!("{BACKEND_URL}{}", request.path));
    if method != reqwest::Method::GET {
        builder = builder.json(&request.body);
    }
    let result = checked(
        builder
            .send()
            .map_err(|e| format!("Could not reach ChordSense: {e}"))?,
    );
    if request.path == "/feedback/start"
        && result
            .as_ref()
            .err()
            .is_some_and(|e| e.starts_with("feedback is already "))
    {
        checked(
            client
                .post(format!("{BACKEND_URL}/feedback/stop"))
                .json(&json!({}))
                .send()
                .map_err(|e| format!("Could not recover previous feedback session: {e}"))?,
        )?;
        return checked(
            client
                .post(format!("{BACKEND_URL}/feedback/start"))
                .json(&json!({}))
                .send()
                .map_err(|e| e.to_string())?,
        );
    }
    result
}

fn checked(response: Response) -> Result<Value, String> {
    let status = response.status();
    let payload: Value = response
        .json()
        .map_err(|e| format!("Invalid ChordSense response ({status}): {e}"))?;
    validate_response(status.as_u16(), payload)
}

fn validate_response(status: u16, payload: Value) -> Result<Value, String> {
    if !(200..300).contains(&status) || payload["success"].as_bool() != Some(true) {
        return Err(payload["error"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| format!("ChordSense request failed (HTTP {status})")));
    }
    Ok(payload)
}

fn stream_feedback(
    tx: mpsc::Sender<Event>,
    generation: u64,
    session: String,
    revision: Arc<AtomicU64>,
    expected: u64,
    shutdown: Arc<AtomicBool>,
) {
    let Ok(client) = client(20) else {
        return;
    };
    let active = || !shutdown.load(Ordering::SeqCst) && revision.load(Ordering::SeqCst) == expected;
    let mut after = 0;
    while active() {
        let response = client
            .get(format!("{BACKEND_URL}/feedback/events"))
            .query(&[
                ("session_id", session.clone()),
                ("after", after.to_string()),
            ])
            .send();
        match response {
            Ok(response) if response.status().is_success() => {
                let mut reader = BufReader::new(response);
                let mut line = String::new();
                while active() {
                    line.clear();
                    // Bound packets, even when an upstream stream fails without a newline.
                    match (&mut reader).take(1_048_577).read_line(&mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) if line.len() > 1_048_576 => break,
                        Ok(_) => {}
                    }
                    if let Some(payload) = feedback_packet(&line, &session, &mut after) {
                        let terminal =
                            matches!(payload["status"].as_str(), Some("stopped" | "error"));
                        if tx
                            .send(Event {
                                tag: "feedback_event".into(),
                                generation,
                                result: Ok(payload),
                            })
                            .is_err()
                        {
                            return;
                        }
                        if terminal {
                            return;
                        }
                    }
                }
            }
            Ok(response) if response.status() == reqwest::StatusCode::NOT_FOUND => {
                let _ = tx.send(Event {
                    tag: "feedback_event".into(),
                    generation,
                    result: Ok(
                        json!({"session_id":session,"type":"status","status":"error",
                        "error":"Feedback session ended on the backend"}),
                    ),
                });
                return;
            }
            _ => {}
        }
        if !active() {
            return;
        }
        let _ = tx.send(Event {
            tag: "feedback_event".into(),
            generation,
            result: Ok(json!({"session_id":session,"type":"status","status":"disconnected"})),
        });
        for _ in 0..10 {
            if !active() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}

fn feedback_packet(line: &str, session: &str, after: &mut u64) -> Option<Value> {
    let data = line.strip_prefix("data:")?.trim();
    let payload: Value = serde_json::from_str(data).ok()?;
    if payload["session_id"].as_str()? != session {
        return None;
    }
    let sequence = payload["sequence"].as_u64()?;
    if sequence <= *after {
        return None;
    }
    *after = sequence;
    Some(payload)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn idle_shutdown_joins_workers_without_contacting_backend() {
        let mut services = Services::new();
        assert!(services.shutdown());
        assert!(services.control_thread.is_none());
        assert!(services.playback_thread.is_none());
        assert!(services.shutdown());
    }
    #[test]
    fn unsuccessful_http_and_body_are_errors() {
        assert!(validate_response(500, json!({"success":true})).is_err());
        assert_eq!(
            validate_response(200, json!({"success":false,"error":"busy"})),
            Err("busy".into())
        );
        assert!(validate_response(200, json!({"success":true})).is_ok());
    }
    #[test]
    fn stream_ignores_other_sessions_replays_and_comments() {
        let mut after = 0;
        assert!(feedback_packet(": keepalive\n", "x", &mut after).is_none());
        assert!(feedback_packet(
            "data: {\"session_id\":\"y\",\"sequence\":2}",
            "x",
            &mut after
        )
        .is_none());
        assert!(feedback_packet(
            "data: {\"session_id\":\"x\",\"sequence\":2}",
            "x",
            &mut after
        )
        .is_some());
        assert!(feedback_packet(
            "data: {\"session_id\":\"x\",\"sequence\":1}",
            "x",
            &mut after
        )
        .is_none());
        assert_eq!(after, 2);
    }
}
