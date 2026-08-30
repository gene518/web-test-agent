mod backend;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(backend::BackendManagerState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .invoke_handler(tauri::generate_handler![
            backend::backend_status,
            backend::restart_backend,
            backend::stop_backend,
            backend::backend_log,
            backend::reveal_path_in_file_manager,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            backend::shutdown(app_handle);
        }
    });
}
