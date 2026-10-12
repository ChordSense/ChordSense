use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};
use tokio::task::JoinHandle;

use super::BACKEND_URL;

const EVENT_NAME: &str = "live-feedback-event";

#[derive(Default)]
pub struct FeedbackBridge {
    stream_task: Mutex<Option<JoinHandle<()>>>,
}

async fn post_control(action: &str) -> Result<Value, String> {
    let response = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|error| error.to_string())?
        .post(format!("{BACKEND_URL}/feedback/{action}"))
        .send()
        .await
        .map_err(|error| format!("Could not {action} live feedback: {error}"))?;
    let status = response.status();
    let body = response.text().await.map_err(|error| error.to_string())?;
    let payload: Value = serde_json::from_str(&body)
        .map_err(|error| format!("Invalid feedback response: {error}"))?;
    if !status.is_success() || payload.get("success") != Some(&Value::Bool(true)) {
        return Err(payload["error"]
            .as_str()
            .unwrap_or("Feedback request failed")
            .to_string());
    }
    Ok(payload)
}

fn is_existing_feedback_session(error: &str) -> bool {
    error.starts_with("feedback is already ")
}

#[tauri::command]
pub async fn start_live_feedback(
    app: AppHandle,
    bridge: State<'_, FeedbackBridge>,
) -> Result<Value, String> {
    let payload = match post_control("start").await {
        Ok(payload) => payload,
        Err(error) if is_existing_feedback_session(&error) => {
            // The backend outlives the desktop UI, so a killed or restarted UI
            // can leave a session behind without an owner. This app is the sole
            // feedback client on the device; release that orphan and retry.
            post_control("stop").await.map_err(|stop_error| {
                format!("Could not recover the previous feedback session: {stop_error}")
            })?;
            post_control("start").await?
        }
        Err(error) => return Err(error),
    };
    let session_id = payload["session_id"]
        .as_str()
        .ok_or("Feedback start response has no session ID")?
        .to_string();
    let task = tokio::spawn(forward_events(app, session_id));
    let mut current = bridge
        .stream_task
        .lock()
        .map_err(|error| error.to_string())?;
    if let Some(previous) = current.replace(task) {
        previous.abort();
    }
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::is_existing_feedback_session;

    #[test]
    fn identifies_an_existing_backend_feedback_session() {
        assert!(is_existing_feedback_session("feedback is already paused"));
        assert!(is_existing_feedback_session("feedback is already running"));
        assert!(!is_existing_feedback_session(
            "audio capture is already in use by recording"
        ));
    }
}

#[tauri::command]
pub async fn pause_live_feedback() -> Result<Value, String> {
    post_control("pause").await
}

#[tauri::command]
pub async fn resume_live_feedback() -> Result<Value, String> {
    post_control("resume").await
}

#[tauri::command]
pub async fn stop_live_feedback(bridge: State<'_, FeedbackBridge>) -> Result<Value, String> {
    let previous = {
        let mut current = bridge
            .stream_task
            .lock()
            .map_err(|error| error.to_string())?;
        current.take()
    };
    if let Some(task) = previous {
        task.abort();
    }
    post_control("stop").await
}

fn forward_packet(app: &AppHandle, packet: &[u8], session_id: &str, after: &mut u64) -> bool {
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
        let _ = app.emit(EVENT_NAME, payload);
        if terminal {
            return false;
        }
    }
    true
}

async fn forward_events(app: AppHandle, session_id: String) {
    let client = reqwest::Client::new();
    let mut after = 0_u64;
    loop {
        let after_text = after.to_string();
        let request = client
            .get(format!("{BACKEND_URL}/feedback/events"))
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
                        if !forward_packet(&app, &packet, &session_id, &mut after) {
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
                    EVENT_NAME,
                    json!({
                        "session_id": session_id,
                        "type": "status",
                        "status": "error",
                        "error": "Feedback session ended on the backend"
                    }),
                );
                return;
            }
            _ => {}
        }
        let _ = app.emit(
            EVENT_NAME,
            json!({
                "session_id": session_id,
                "type": "status",
                "status": "disconnected"
            }),
        );
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}
