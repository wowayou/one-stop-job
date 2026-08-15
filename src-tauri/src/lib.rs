use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

struct BackendProcess(Mutex<Option<Child>>);

#[cfg(target_os = "windows")]
const BACKEND_BIN: &str = "job-one-stop-backend.exe";
#[cfg(not(target_os = "windows"))]
const BACKEND_BIN: &str = "job-one-stop-backend";

/// Find the project root (parent of src-tauri).
fn project_root() -> std::path::PathBuf {
    let manifest_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.parent().unwrap_or(&manifest_dir).to_path_buf()
}

/// In dev mode, find the venv Python.
fn dev_python() -> String {
    let root = project_root();
    let candidates = [
        root.join(".venv").join("bin").join("python"),
        root.join(".venv").join("Scripts").join("python.exe"),
    ];
    for path in &candidates {
        if path.exists() {
            return path.to_string_lossy().to_string();
        }
    }
    "python3".to_string()
}

fn start_backend(app: &tauri::App) -> Result<u16, String> {
    let root = project_root();
    let resource_path = app
        .path()
        .resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;

    let backend_path = resource_path.join("resources").join(BACKEND_BIN);

    let (port, child) = if backend_path.exists() {
        // Production: run the PyInstaller binary
        // 用 inherit 让后端的 stdout/stderr 直接输出到 Tauri 进程的终端，
        // 不用 piped（piped 的 pipe 没人读，buffer 满了后端会挂死）。
        let child = Command::new(&backend_path)
            .current_dir(&root)
            .env("JOB_ONE_STOP_TAURI_MODE", "1")
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Failed to start backend: {}", e))?;
        (8000u16, child)
    } else {
        // Dev mode: run uvicorn with the venv Python from project root
        let python = dev_python();
        let child = Command::new(&python)
            .current_dir(&root)
            .args(["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Failed to start dev backend ({}): {}", python, e))?;
        (8000u16, child)
    };

    // Wait for backend health check
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| format!("HTTP client error: {}", e))?;

    let mut ready = false;
    for _ in 0..40 {
        if client
            .get(format!("http://127.0.0.1:{}/api/health", port))
            .send()
            .is_ok()
        {
            ready = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }

    if !ready {
        eprintln!("Warning: backend did not become ready within 20s");
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
