use std::process::{Child, Command};
use std::sync::Mutex;
use std::fs;
use std::path::Path;
use tauri::{AppHandle, Manager, State};

// ── Backend process state ──────────────────────────────────────────────────────
struct BackendState(Mutex<Option<Child>>);

// ── Helpers ────────────────────────────────────────────────────────────────────

fn is_wsl() -> bool {
    fs::read_to_string("/proc/version")
        .map(|v| v.to_lowercase().contains("microsoft"))
        .unwrap_or(false)
}

fn read_dot_env(dir: &Path) -> Vec<(String, String)> {
    let env_path = dir.join(".env");
    let Ok(content) = fs::read_to_string(&env_path) else { return vec![] };
    content.lines()
        .filter(|l| !l.trim().is_empty() && !l.trim().starts_with('#'))
        .filter_map(|l| {
            let idx = l.find('=')?;
            let k = l[..idx].trim().to_string();
            let v = l[idx + 1..].trim().to_string();
            if k.is_empty() || v.is_empty() { None } else { Some((k, v)) }
        })
        .collect()
}

fn backend_is_running() -> bool {
    std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8000".parse().unwrap(),
        std::time::Duration::from_millis(300),
    ).is_ok()
}

// ── Tauri Commands ─────────────────────────────────────────────────────────────

#[tauri::command]
async fn start_backend(
    app: AppHandle,
    state: State<'_, BackendState>,
) -> Result<String, String> {
    if backend_is_running() {
        return Ok("already_running".into());
    }

    let backend_dir = if cfg!(debug_assertions) {
        app.path().resource_dir()
            .map(|p| p.parent().unwrap_or(&p).parent().unwrap_or(&p).join("backend"))
            .unwrap_or_else(|_| Path::new("../backend").to_path_buf())
    } else {
        app.path().resource_dir()
            .map(|p| p.join("backend"))
            .map_err(|e| e.to_string())?
    };

    let env_vars = read_dot_env(&backend_dir);

    let python = if cfg!(windows) { "python" } else { "python3" };
    let mut cmd = Command::new(python);
    cmd.args(["-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"])
       .current_dir(&backend_dir);

    for (k, v) in &env_vars {
        cmd.env(k, v);
    }

    let child = cmd.spawn().map_err(|e| format!("Failed to start backend: {e}"))?;
    *state.0.lock().unwrap() = Some(child);
    Ok("started".into())
}

#[tauri::command]
async fn stop_backend(state: State<'_, BackendState>) -> Result<(), String> {
    if let Some(mut child) = state.0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    Ok(())
}

#[tauri::command]
async fn get_backend_status() -> Result<serde_json::Value, String> {
    let running = backend_is_running();
    Ok(serde_json::json!({ "running": running }))
}

#[tauri::command]
async fn launch_application(command: String) -> Result<(), String> {
    if command.len() > 600 {
        return Err("Command too long".into());
    }
    // Block shell injection
    if command.contains('`') || command.contains("&&") || command.contains("||") || command.contains(';') {
        return Err("Blocked unsafe command".into());
    }

    if is_wsl() {
        // Find cmd.exe for WSL2 → Windows execution
        let cmd_paths = [
            "/mnt/c/Windows/System32/cmd.exe",
            "/mnt/c/WINDOWS/System32/cmd.exe",
        ];
        for path in &cmd_paths {
            if Path::new(path).exists() {
                Command::new(path)
                    .args(["/c", &command])
                    .spawn()
                    .map_err(|e| e.to_string())?;
                return Ok(());
            }
        }
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("cmd").args(["/c", &command]).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("sh").args(["-c", &command]).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        Command::new("sh").args(["-c", &command]).spawn().map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
async fn get_system_info() -> Result<Option<String>, String> {
    let ps_exe = if is_wsl() { "powershell.exe" } else if cfg!(windows) { "powershell" } else { return Ok(None) };

    let script = r#"
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$ram = [math]::Round($os.TotalVisibleMemorySize/1MB, 1)
Write-Output "$($os.Caption)|$($os.BuildNumber)|$($cpu.Name)|$($cpu.NumberOfCores)|$($cpu.NumberOfLogicalProcessors)|${ram}GB"
"#;
    let encoded = {
        let wide: Vec<u16> = script.encode_utf16().collect();
        let bytes: Vec<u8> = wide.iter().flat_map(|w| w.to_le_bytes()).collect();
        let mut enc = String::new();
        let base64_chars = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        for chunk in bytes.chunks(3) {
            let b0 = chunk[0] as usize;
            let b1 = if chunk.len() > 1 { chunk[1] as usize } else { 0 };
            let b2 = if chunk.len() > 2 { chunk[2] as usize } else { 0 };
            enc.push(base64_chars[b0 >> 2] as char);
            enc.push(base64_chars[((b0 & 3) << 4) | (b1 >> 4)] as char);
            enc.push(if chunk.len() > 1 { base64_chars[((b1 & 0xf) << 2) | (b2 >> 6)] as char } else { '=' });
            enc.push(if chunk.len() > 2 { base64_chars[b2 & 0x3f] as char } else { '=' });
        }
        enc
    };

    let output = Command::new(ps_exe)
        .args(["-NoProfile", "-EncodedCommand", &encoded])
        .output()
        .map_err(|e| e.to_string())?;

    if output.status.success() {
        Ok(Some(String::from_utf8_lossy(&output.stdout).trim().to_string()))
    } else {
        Ok(None)
    }
}

#[tauri::command]
async fn run_powershell(script: String) -> Result<String, String> {
    if script.len() > 2000 {
        return Err("Script too long".into());
    }
    let ps_exe = if is_wsl() { "powershell.exe" } else if cfg!(windows) { "powershell" } else { return Err("Not Windows/WSL".into()) };
    let output = Command::new(ps_exe)
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

#[tauri::command]
async fn open_url(url: String) -> Result<(), String> {
    if is_wsl() {
        let cmd_paths = ["/mnt/c/Windows/System32/cmd.exe", "/mnt/c/WINDOWS/System32/cmd.exe"];
        for path in &cmd_paths {
            if Path::new(path).exists() {
                Command::new(path).args(["/c", "start", "", &url]).spawn().ok();
                return Ok(());
            }
        }
    }
    #[cfg(target_os = "linux")]
    {
        let _ = Command::new("xdg-open").arg(&url).spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(&url).spawn();
    }
    #[cfg(target_os = "windows")]
    {
        let _ = Command::new("cmd").args(["/c", "start", "", &url]).spawn();
    }
    Ok(())
}

// ── App entry ──────────────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // WSL2: set PulseAudio + WebKit env vars before the WebView is created.
    // GStreamer reads PULSE_SERVER at pipeline init time, which happens during
    // getUserMedia. Without this, it deadlocks trying to find the default server,
    // freezing the entire WebKit2GTK renderer process — no JS timeout can recover.
    if is_wsl() {
        if std::env::var("PULSE_SERVER").is_err() {
            std::env::set_var("PULSE_SERVER", "unix:/mnt/wslg/PulseServer");
        }
        // Prevents WebKit2GTK from crashing due to missing DMA-BUF support in WSL2
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::default().build())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            // Auto-start backend on launch
            let handle = app.handle().clone();
            let state = app.state::<BackendState>();
            if !backend_is_running() {
                let dir = if cfg!(debug_assertions) {
                    handle.path().resource_dir()
                        .map(|p| p.parent().unwrap_or(&p).parent().unwrap_or(&p).join("backend"))
                        .unwrap_or_else(|_| Path::new("../backend").to_path_buf())
                } else {
                    handle.path().resource_dir()
                        .map(|p| p.join("backend"))
                        .unwrap_or_else(|_| Path::new("../backend").to_path_buf())
                };
                if dir.exists() {
                    let env_vars = read_dot_env(&dir);
                    let python = if cfg!(windows) { "python" } else { "python3" };
                    let mut cmd = Command::new(python);
                    cmd.args(["-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"])
                       .current_dir(&dir);
                    for (k, v) in &env_vars {
                        cmd.env(k, v);
                    }
                    if let Ok(child) = cmd.spawn() {
                        *state.0.lock().unwrap() = Some(child);
                        println!("[Xyron] Backend started");
                    }
                }
            }

            // Auto-grant microphone (and camera) permission requests from the WebView.
            // WebKit2GTK fires a permission-request signal before allowing getUserMedia.
            // Without this handler the signal is left unhandled and the request is silently denied.
            #[cfg(target_os = "linux")]
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.with_webview(|webview| {
                    use webkit2gtk::{WebViewExt, PermissionRequestExt};
                    webview.inner().connect_permission_request(|_view, request| {
                        request.allow();
                        true
                    });
                });
            }

            // System tray — skip in WSL2 (no D-Bus session bus, causes libayatana spam)
            #[cfg(desktop)]
            if !is_wsl() {
                use tauri::menu::{MenuBuilder, MenuItemBuilder};
                use tauri::tray::{TrayIconBuilder, TrayIconEvent};

                let show = MenuItemBuilder::with_id("show", "Show / Hide").build(app)?;
                let quit = MenuItemBuilder::with_id("quit", "Quit Xyron").build(app)?;
                let sep  = tauri::menu::PredefinedMenuItem::separator(app)?;
                let menu = MenuBuilder::new(app).items(&[&show, &sep, &quit]).build()?;

                let _tray = TrayIconBuilder::new()
                    .icon(app.default_window_icon().unwrap().clone())
                    .tooltip("Xyron")
                    .menu(&menu)
                    .on_menu_event(move |app, event| match event.id().as_ref() {
                        "show" => {
                            if let Some(win) = app.get_webview_window("main") {
                                if win.is_visible().unwrap_or(false) { let _ = win.hide(); }
                                else { let _ = win.show(); let _ = win.set_focus(); }
                            }
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::DoubleClick { .. } = event {
                            if let Some(win) = tray.app_handle().get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                    })
                    .build(app)?;
            }

            // Global shortcuts
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

                let shortcuts = [
                    Shortcut::new(Some(Modifiers::ALT), Code::Space),
                ];
                for shortcut in &shortcuts {
                    let _ = app.global_shortcut().on_shortcut(*shortcut, |app_handle, _, _| {
                        if let Some(win) = app_handle.get_webview_window("main") {
                            if win.is_visible().unwrap_or(false) {
                                let _ = win.hide();
                            } else {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                    });
                    break;
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if is_wsl() {
                    // No tray in WSL2 — just quit cleanly
                    window.app_handle().exit(0);
                } else {
                    // Native Linux/Windows: minimize to tray
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            launch_application,
            get_system_info,
            run_powershell,
            open_url,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Xyron");
}
