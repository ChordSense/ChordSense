use serde_json::Value;
use tauri::{AppHandle, State};

use super::live_bridge::{post_control, LiveBridge, LiveRoute};

const ROUTE: LiveRoute = LiveRoute {
    path: "feedback",
    event_name: "live-feedback-event",
    busy_prefix: "feedback is already ",
    noun: "live feedback",
    title: "Feedback",
};

#[derive(Default)]
pub struct FeedbackBridge(LiveBridge);

#[tauri::command]
pub async fn start_live_feedback(
    app: AppHandle,
    bridge: State<'_, FeedbackBridge>,
) -> Result<Value, String> {
    bridge.0.start(app, ROUTE, None).await
}

#[cfg(test)]
mod tests {
    use super::super::live_bridge::is_existing_session;
    use super::ROUTE;

    #[test]
    fn identifies_an_existing_backend_feedback_session() {
        assert!(is_existing_session(ROUTE, "feedback is already paused"));
        assert!(is_existing_session(ROUTE, "feedback is already running"));
        assert!(!is_existing_session(
            ROUTE,
            "audio capture is already in use by recording"
        ));
    }
}

#[tauri::command]
pub async fn pause_live_feedback() -> Result<Value, String> {
    post_control(ROUTE, "pause", None).await
}

#[tauri::command]
pub async fn resume_live_feedback() -> Result<Value, String> {
    post_control(ROUTE, "resume", None).await
}

#[tauri::command]
pub async fn stop_live_feedback(bridge: State<'_, FeedbackBridge>) -> Result<Value, String> {
    bridge.0.stop(ROUTE).await
}
