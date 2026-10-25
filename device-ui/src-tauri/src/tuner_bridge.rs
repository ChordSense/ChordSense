use serde_json::Value;
use tauri::{AppHandle, State};

use super::live_bridge::{post_control, LiveBridge, LiveRoute};

const ROUTE: LiveRoute = LiveRoute {
    path: "tuner",
    event_name: "tuner-event",
    busy_prefix: "tuner is already ",
    noun: "the tuner",
    title: "Tuner",
};

#[derive(Default)]
pub struct TunerBridge(LiveBridge);

/// `settings` is passed through as the JSON body: `{a4_hz?, target_midi?}`,
/// where `target_midi: null` returns to automatic note detection.
#[tauri::command]
pub async fn start_tuner(
    app: AppHandle,
    bridge: State<'_, TunerBridge>,
    settings: Option<Value>,
) -> Result<Value, String> {
    bridge.0.start(app, ROUTE, settings.as_ref()).await
}

#[tauri::command]
pub async fn configure_tuner(settings: Value) -> Result<Value, String> {
    post_control(ROUTE, "config", Some(&settings)).await
}

#[tauri::command]
pub async fn stop_tuner(bridge: State<'_, TunerBridge>) -> Result<Value, String> {
    bridge.0.stop(ROUTE).await
}

#[cfg(test)]
mod tests {
    use super::super::live_bridge::is_existing_session;
    use super::ROUTE;

    #[test]
    fn identifies_an_existing_backend_tuner_session() {
        assert!(is_existing_session(ROUTE, "tuner is already running"));
        assert!(!is_existing_session(ROUTE, "feedback is already running"));
    }
}
