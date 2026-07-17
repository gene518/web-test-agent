mod backend;

#[cfg(target_os = "windows")]
fn configure_portable_webview2() {
    let Some(executable_dir) = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(std::path::Path::to_path_buf))
    else {
        return;
    };
    let fixed_runtime = executable_dir.join("runtime/webview2");
    if fixed_runtime.join("msedgewebview2.exe").is_file()
        || fixed_runtime.join("msedge.exe").is_file()
    {
        std::env::set_var("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", fixed_runtime);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "windows")]
    configure_portable_webview2();

    let app = tauri::Builder::default()
        .manage(backend::BackendManagerState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .invoke_handler(tauri::generate_handler![
            backend::backend_status,
            backend::restart_backend,
            backend::stop_backend,
            backend::backend_log,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            backend::shutdown(app_handle);
        }
    });
}
