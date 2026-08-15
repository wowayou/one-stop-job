use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

struct BackendProcess(Mutex<Option<Child>>);

#[cfg(target_os = "windows")]
const BACKEND_BIN: &str = "job-one-stop-backend.exe";
#[cfg(not(target_os = "windows"))]
const BACKEND_BIN: &str = "job-one-stop-backend";

fn start_backend(app: &tauri::App) -> Result<u16, String> {
    let resource_path = app
        .path()
        .resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;

    // The backend binary is bundled as a resource
    let backend_path = resource_path.join("resources").join(BACKEND_BIN);

    // If the packaged backend doesn't exist (dev mode), fall back to system python
    let (port, child) = if backend_path.exists() {
        // Production: run the PyInstaller binary
        let child = Command::new(&backend_path)
            .env("JOB_ONE_STOP_TAURI_MODE", "1")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Failed to start backend: {}", e))?;
        (8000u16, child)
    } else {
        // Dev mode: run uvicorn directly
        let child = Command::new("python3")
            .args(["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Failed to start dev backend: {}", e))?;
        (8000u16, child)
    };

    // Wait for backend to be ready
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| format!("HTTP client error: {}", e))?;

    for _ in 0..30 {
        if client
            .get(format!("http://127.0.0.1:{}/api/health", port))
            .send()
            .is_ok()
        {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }

    let state = app.state::<BackendProcess>();
    *state.0.lock().unwrap() = Some(child);

    Ok(port)
}

#[tauri::command]
fn backend_port() -> u16 {
    8000
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            match start_backend(app) {
                Ok(_port) => {}
                Err(e) => {
                    eprintln!("Failed to start backend: {}", e);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Kill the backend process on window close
                let state = window.state::<BackendProcess>();
                let child = state.0.lock().unwrap().take();
                if let Some(mut child) = child {
                    let _ = child.kill();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![backend_port])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
