// Checks if there is a connection to the backend
#[tauri::command]
async fn backend_health() -> Result<String, String> {
    let response = reqwest::get("http://127.0.0.1:5051/health")
        .await
        .map_err(|e| e.to_string())?;

    let body = response.text().await.map_err(|e| e.to_string())?;

    Ok(body)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(
            tauri::generate_handler![
                backend_health,

                analyze_audio,
                analyze_uploaded_audio,

                list_uploaded_audio,
                load_uploaded_audio,

                begin_recording,
                end_recording,

                load_audio_file
            ]
        )
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}




use reqwest::multipart;
use serde::{Deserialize, Serialize};
use std::path::Path;

const BACKEND_URL: &str = "http://127.0.0.1:5051";

#[derive(
    Debug,
    Clone,
    Deserialize,
    Serialize
)]
struct UploadedAudio {
    name: String,
    size: u64,
    modified: String,
    download_url: String,
}


#[derive(
    Debug,
    Clone,
    Deserialize
)]
struct UploadedAudioListResponse {

    success: bool,

    #[serde(default)]
    files: Vec<UploadedAudio>,

    #[serde(default)]
    error: Option<String>,
}

#[tauri::command]
async fn list_uploaded_audio()
    -> Result<Vec<UploadedAudio>, String>
{

    let response =
        reqwest::get(
            format!(
                "{BACKEND_URL}/api/uploads"
            )
        )
        .await
        .map_err(|e|
            format!(
                "Could not reach ChordSense library: {e}"
            )
        )?;


    let status =
        response.status();


    let text =
        response
            .text()
            .await
            .map_err(|e|
                format!(
                    "Could not read library response: {e}"
                )
            )?;


    if !status.is_success() {

        return Err(
            format!(
                "Library request failed ({status}): {text}"
            )
        );
    }


    let payload:
        UploadedAudioListResponse =
        serde_json::from_str(
            &text
        )
        .map_err(|e|
            format!(
                "Could not parse library response: {e}"
            )
        )?;


    if !payload.success {

        return Err(
            payload
                .error
                .unwrap_or_else(
                    ||
                    "Could not load audio library."
                        .to_string()
                )
        );
    }


    Ok(payload.files)
}

fn uploaded_audio_url(
    filename: &str
) -> Result<reqwest::Url, String> {

    let mut url =
        reqwest::Url::parse(
            BACKEND_URL
        )
        .map_err(|e|
            format!(
                "Invalid backend URL: {e}"
            )
        )?;


    {
        let mut segments =
            url
                .path_segments_mut()
                .map_err(|_|
                    "Could not build uploaded audio URL."
                        .to_string()
                )?;


        segments.push("api");
        segments.push("uploads");
        segments.push(filename);
    }


    Ok(url)
}

async fn fetch_uploaded_audio_bytes(
    filename: &str
) -> Result<Vec<u8>, String> {

    let url =
        uploaded_audio_url(
            filename
        )?;


    let response =
        reqwest::Client::new()
            .get(url)
            .send()
            .await
            .map_err(|e|
                format!(
                    "Could not load uploaded audio: {e}"
                )
            )?;


    let status =
        response.status();


    if !status.is_success() {

        let message =
            response
                .text()
                .await
                .unwrap_or_default();


        return Err(
            format!(
                "Audio request failed ({status}): {message}"
            )
        );
    }


    let bytes =
        response
            .bytes()
            .await
            .map_err(|e|
                format!(
                    "Could not read audio data: {e}"
                )
            )?;


    Ok(
        bytes.to_vec()
    )
}

#[tauri::command]
async fn load_uploaded_audio(
    filename: String
) -> Result<Vec<u8>, String> {

    fetch_uploaded_audio_bytes(
        &filename
    )
    .await
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct ChordSegment {
    start: f64,
    end: f64,
    chord: String,

    #[serde(default = "default_confidence")]
    confidence: f64,
}

fn default_confidence() -> f64 {
    1.0
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct AnalyzeResponse {
    success: bool,

    #[serde(default)]
    chords: Vec<ChordSegment>,

    #[serde(default)]
    total_chords: usize,

    #[serde(default)]
    duration: f64,

    #[serde(default)]
    model_used: String,

    #[serde(default)]
    model_name: String,

    #[serde(default)]
    chord_dict: String,

    #[serde(default)]
    processing_time: f64,

    #[serde(default)]
    stdout: Option<String>,

    #[serde(default)]
    stderr: Option<String>,

    #[serde(default)]
    error: Option<String>,

    /// Absolute path to the captured WAV, set by /end_recording so the UI can
    /// play the take back. Absent for /analyze (the UI already has that file).
    #[serde(default)]
    wav_path: Option<String>,
}

async fn analyze_audio_bytes(
    bytes: Vec<u8>,
    file_name: String,
    chord_dict: String,
) -> Result<AnalyzeResponse, String> {

    let part =
        multipart::Part::bytes(
            bytes
        )
        .file_name(
            file_name
        );


    let form =
        multipart::Form::new()
            .part(
                "file",
                part
            )
            .text(
                "chord_dict",
                chord_dict
            );


    let client =
        reqwest::Client::builder()
            .timeout(
                std::time::Duration::from_secs(
                    3600
                )
            )
            .build()
            .map_err(|e|
                format!(
                    "Failed to build HTTP client: {e}"
                )
            )?;


    let response =
        client
            .post(
                format!(
                    "{BACKEND_URL}/analyze"
                )
            )
            .multipart(
                form
            )
            .send()
            .await
            .map_err(|e|
                format!(
                    "Backend request failed: {e}"
                )
            )?;


    let status =
        response.status();


    let text =
        response
            .text()
            .await
            .map_err(|e|
                format!(
                    "Could not read backend response: {e}"
                )
            )?;


    let payload:
        AnalyzeResponse =
        serde_json::from_str(
            &text
        )
        .map_err(|e|
            format!(
                "Could not parse backend JSON.\n\
                 HTTP status: {status}\n\
                 Parse error: {e}\n\
                 Response: {text}"
            )
        )?;


    if !payload.success {

        return Err(
            payload
                .error
                .clone()
                .unwrap_or_else(
                    ||
                    "Analysis failed"
                        .to_string()
                )
        );
    }


    Ok(payload)
}

#[tauri::command]
async fn analyze_uploaded_audio(
    filename: String,
    chord_dict: String,
) -> Result<AnalyzeResponse, String> {

    let bytes =
        fetch_uploaded_audio_bytes(
            &filename
        )
        .await?;


    analyze_audio_bytes(
        bytes,
        filename,
        chord_dict
    )
    .await
}

#[tauri::command]
async fn analyze_audio(
    path: String,
    chord_dict: String,
) -> Result<AnalyzeResponse, String> {

    let audio_path =
        Path::new(
            &path
        );


    if !audio_path.exists() {

        return Err(
            format!(
                "Audio file does not exist: {}",
                path
            )
        );
    }


    let bytes =
        tokio::fs::read(
            audio_path
        )
        .await
        .map_err(|e|
            format!(
                "Failed to read audio file: {e}"
            )
        )?;


    let file_name =
        audio_path
            .file_name()
            .and_then(
                |name|
                name.to_str()
            )
            .unwrap_or(
                "audio.wav"
            )
            .to_string();


    analyze_audio_bytes(
        bytes,
        file_name,
        chord_dict
    )
    .await
}

#[derive(
    Debug,
    Clone,
    Deserialize,
    Serialize
)]
struct RecordBackendResponse {
    success: bool,

    #[serde(default)]
    message: Option<String>,

    #[serde(default)]
    error: Option<String>,
}

#[tauri::command]
async fn begin_recording()
    -> Result<RecordBackendResponse, String>
{
    let client =
        reqwest::Client::builder()
            .timeout(
                std::time::Duration::from_secs(
                    30
                )
            )
            .build()
            .map_err(|e|
                format!(
                    "Failed to create HTTP client: {e}"
                )
            )?;


    let response =
        client
            .post(
                format!(
                    "{BACKEND_URL}/begin_recording"
                )
            )
            .send()
            .await
            .map_err(|e|
                format!(
                    "Could not start recording: {e}"
                )
            )?;


    let status =
        response.status();


    let text =
        response
            .text()
            .await
            .map_err(|e|
                format!(
                    "Could not read recording response: {e}"
                )
            )?;


    let payload:
        RecordBackendResponse =
        serde_json::from_str(&text)
            .map_err(|e|
                format!(
                    "Could not parse recording response.\n\
                     HTTP status: {status}\n\
                     Error: {e}\n\
                     Response: {text}"
                )
            )?;


    if !payload.success {

        return Err(
            payload
                .error
                .clone()
                .unwrap_or_else(||
                    "Could not start recording."
                        .to_string()
                )
        );
    }


    Ok(payload)
}

#[tauri::command]
async fn load_audio_file(path: String) -> Result<Vec<u8>, String> {
    let audio_path = Path::new(&path);

    if !audio_path.exists() {
        return Err(format!("Audio file does not exist: {}", path));
    }

    tokio::fs::read(audio_path)
        .await
        .map_err(|e| format!("Failed to read audio file: {e}"))
}

#[tauri::command]
async fn end_recording()
    -> Result<AnalyzeResponse, String>
{
    let client =
        reqwest::Client::builder()
            .timeout(
                std::time::Duration::from_secs(
                    3600
                )
            )
            .build()
            .map_err(|e|
                format!(
                    "Failed to create HTTP client: {e}"
                )
            )?;


    let response =
        client
            .post(
                format!(
                    "{BACKEND_URL}/end_recording"
                )
            )
            .send()
            .await
            .map_err(|e|
                format!(
                    "Could not stop recording: {e}"
                )
            )?;


    let status =
        response.status();


    let text =
        response
            .text()
            .await
            .map_err(|e|
                format!(
                    "Could not read analysis response: {e}"
                )
            )?;


    let payload:
        AnalyzeResponse =
        serde_json::from_str(&text)
            .map_err(|e|
                format!(
                    "Could not parse recorded analysis.\n\
                     HTTP status: {status}\n\
                     Error: {e}\n\
                     Response: {text}"
                )
            )?;


    if !payload.success {

        return Err(
            payload
                .error
                .clone()
                .unwrap_or_else(||
                    "Recorded analysis failed."
                        .to_string()
                )
        );
    }


    Ok(payload)
}