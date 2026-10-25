//! Shared Flask-session bridge for features that stream live guitar events.
//!
//! Live Feedback (`/feedback/*`) and the tuner (`/tuner/*`) expose the same
//! backend contract: POST controls returning `{success, session_id, ...}` and
//! an SSE `events` stream keyed by session ID and sequence number. This module
//! forwards one route's events to the webview under its own event name.

use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};
use tokio::task::JoinHandle;

use super::BACKEND_URL;

#[derive(Clone, Copy)]
pub struct LiveRoute {
    /// Backend path prefix, e.g. `feedback` for `/feedback/start`.
    pub path: &'static str,
    /// Webview event name the SSE payloads are emitted under.
    pub event_name: &'static str,
    /// Prefix of the backend's "already running" error for this route.
    pub busy_prefix: &'static str,
    /// Wording for user-facing errors, e.g. "live feedback" / "Feedback".
    pub noun: &'static str,
    pub title: &'static str,
}

#[derive(Default)]
pub struct LiveBridge {
    stream_task: Mutex<Option<JoinHandle<()>>>,
}

pub async fn post_control(
    route: LiveRoute,
    action: &str,
    body: Option<&Value>,
) -> Result<Value, String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|error| error.to_string())?;
    let mut request = client.post(format!("{BACKEND_URL}/{}/{action}", route.path));
    if let Some(body) = body {
        request = request.json(body);
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("Could not {action} {}: {error}", route.noun))?;
    let status = response.status();
    let body = response.text().await.map_err(|error| error.to_string())?;
    let payload: Value = serde_json::from_str(&body)
        .map_err(|error| format!("Invalid {} response: {error}", route.path))?;
    if !status.is_success() || payload.get("success") != Some(&Value::Bool(true)) {
        return Err(payload["error"]
            .as_str()
            .map(str::to_string)
            .unwrap_or_else(|| format!("{} request failed", route.title)));
    }
    Ok(payload)
}

pub fn is_existing_session(route: LiveRoute, error: &str) -> bool {
    error.starts_with(route.busy_prefix)
}

impl LiveBridge {
    pub async fn start(
        &self,
        app: AppHandle,
        route: LiveRoute,
        body: Option<&Value>,
    ) -> Result<Value, String> {
        let payload = match post_control(route, "start", body).await {
            Ok(payload) => payload,
            Err(error) if is_existing_session(route, &error) => {
                // The backend outlives the desktop UI, so a killed or restarted
                // UI can leave a session behind without an owner. This app is
                // the sole client on the device; release that orphan and retry.
                post_control(route, "stop", None).await.map_err(|stop_error| {
                    format!("Could not recover the previous {} session: {stop_error}", route.path)
                })?;
                post_control(route, "start", body).await?
            }
            Err(error) => return Err(error),
        };
        let session_id = payload["session_id"]
            .as_str()
            .ok_or("Start response has no session ID")?
            .to_string();
        let task = tokio::spawn(forward_events(app, route, session_id));
        let mut current = self
            .stream_task
            .lock()
            .map_err(|error| error.to_string())?;
        if let Some(previous) = current.replace(task) {
            previous.abort();
        }
        Ok(payload)
    }

    pub async fn stop(&self, route: LiveRoute) -> Result<Value, String> {
        let previous = {
            let mut current = self
                .stream_task
                .lock()
                .map_err(|error| error.to_string())?;
            current.take()
        };
        if let Some(task) = previous {
            task.abort();
        }
        post_control(route, "stop", None).await
    }
}

fn forward_packet(
    app: &AppHandle,
    route: LiveRoute,
    packet: &[u8],
    session_id: &str,
    after: &mut u64,
) -> bool {
    let Ok(text) = std::str::from_utf8(packet) else {
        return true;
    };
    for line in text.lines() {
        let Some(data) = line.strip_prefix("data: ") else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<Value>(data) else {
            continue;
        };
        if payload["session_id"].as_str() != Some(session_id) {
            continue;
        }
        let Some(sequence) = payload["sequence"].as_u64() else {
            continue;
        };
        if sequence <= *after {
            continue;
        }
        *after = sequence;
        let terminal = matches!(payload["status"].as_str(), Some("stopped" | "error"));
        let _ = app.emit(route.event_name, payload);
        if terminal {
            return false;
        }
    }
    true
}

async fn forward_events(app: AppHandle, route: LiveRoute, session_id: String) {
    let client = reqwest::Client::new();
    let mut after = 0_u64;
    loop {
        let after_text = after.to_string();
        let request = client
            .get(format!("{BACKEND_URL}/{}/events", route.path))
            .query(&[
                ("session_id", session_id.as_str()),
                ("after", after_text.as_str()),
            ])
            .send()
            .await;
        match request {
            Ok(mut response) if response.status().is_success() => {
                let mut buffer = Vec::new();
                while let Ok(Some(chunk)) = response.chunk().await {
                    buffer.extend_from_slice(&chunk);
                    while let Some(end) = buffer.windows(2).position(|window| window == b"\n\n") {
                        let packet = buffer.drain(..end + 2).collect::<Vec<_>>();
                        if !forward_packet(&app, route, &packet, &session_id, &mut after) {
                            return;
                        }
                    }
                    if buffer.len() > 1_048_576 {
                        buffer.clear();
                    }
                }
            }
            Ok(response) if response.status() == reqwest::StatusCode::NOT_FOUND => {
                let _ = app.emit(
                    route.event_name,
                    json!({
                        "session_id": session_id,
                        "type": "status",
                        "status": "error",
                        "error": format!("{} session ended on the backend", route.title)
                    }),
                );
                return;
            }
            _ => {}
        }
        let _ = app.emit(
            route.event_name,
            json!({
                "session_id": session_id,
                "type": "status",
                "status": "disconnected"
            }),
        );
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}
