use serde::Serialize;
use std::{
    fs,
    path::Path,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};

mod file_manager;
mod launch;
mod logs;
mod process;

use file_manager::reveal_path;
use launch::{
    backend_log_path, build_launch_spec, configure_background_process, current_platform,
    resolve_project_root, Platform,
};
use logs::read_log_tail;
use process::{
    currently_owned_listeners, listener_pids, probe_port, project_backend_listener_pids,
    terminate_pid, verified_listener_pids, wait_until_available, PortProbe,
};

const BACKEND_HOST: &str = "127.0.0.1";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendStatus {
    state: String,
    api_url: String,
    project_root: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

struct ManagedBackend {
    child: Child,
    port: u16,
    listener_pids: Vec<u32>,
}

#[derive(Default)]
pub struct BackendManagerState {
    managed: Mutex<Option<ManagedBackend>>,
    lifecycle: Mutex<()>,
}

fn api_url(port: u16) -> String {
    format!("http://{BACKEND_HOST}:{port}")
}

fn validate_backend_port(port: u16) -> Result<(), String> {
    if port < 1024 {
        return Err("后端端口必须处于 1024-65535 范围内。".to_string());
    }
    Ok(())
}

fn stop_managed_child(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<BackendManagerState>();
    let managed = state
        .managed
        .lock()
        .map_err(|_| "后端进程状态不可用。".to_string())?
        .take();
    if let Some(mut managed) = managed {
        let platform = current_platform()?;
        let root_pid = managed.child.id();
        let child_is_running = managed
            .child
            .try_wait()
            .map_err(|error| format!("无法检查后端进程状态：{error}"))?
            .is_none();
        let prevalidated_listener_pids = if child_is_running {
            currently_owned_listeners(managed.port, &managed.listener_pids, root_pid, platform)
        } else {
            Vec::new()
        };
        if child_is_running {
            terminate_pid(root_pid, platform, false);
            let graceful_deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < graceful_deadline {
                if managed.child.try_wait().ok().flatten().is_some() {
                    break;
                }
                thread::sleep(Duration::from_millis(100));
            }
            if managed.child.try_wait().ok().flatten().is_none() {
                let force_listener_pids = currently_owned_listeners(
                    managed.port,
                    &prevalidated_listener_pids,
                    root_pid,
                    platform,
                );
                for pid in force_listener_pids {
                    terminate_pid(pid, platform, true);
                }
                let _ = managed.child.kill();
            }
        }
        let _ = managed.child.wait();

        if !wait_until_available(managed.port, Duration::from_secs(5)) {
            return Err(format!(
                "已停止客户端管理的后端，但端口 {} 仍被归属不明的进程占用；客户端不会终止它。",
                managed.port
            ));
        }
    }
    Ok(())
}

fn stop_project_backend(root: &Path, port: u16, platform: Platform) -> Result<bool, String> {
    let listener_pids = listener_pids(port, platform)?;
    let project_listener_pids = project_backend_listener_pids(port, root, platform)?;
    if project_listener_pids.is_empty() || project_listener_pids.len() != listener_pids.len() {
        return Ok(false);
    }

    for pid in &project_listener_pids {
        terminate_pid(*pid, platform, false);
    }
    if wait_until_available(port, Duration::from_secs(5)) {
        return Ok(true);
    }

    for pid in project_backend_listener_pids(port, root, platform)? {
        terminate_pid(pid, platform, true);
    }
    if wait_until_available(port, Duration::from_secs(5)) {
        return Ok(true);
    }

    Err(format!(
        "端口 {port} 上属于当前仓库的 LangGraph 后端未能停止，客户端不会启动重复服务。"
    ))
}

fn status_impl(app: &AppHandle, project_root: &str, port: u16) -> BackendStatus {
    let platform = match current_platform() {
        Ok(platform) => platform,
        Err(message) => {
            return BackendStatus {
                state: "error".to_string(),
                api_url: api_url(port),
                project_root: project_root.to_string(),
                pid: None,
                message: Some(message),
            }
        }
    };
    let root = match resolve_project_root(project_root, platform) {
        Ok(root) => root,
        Err(message) => {
            return BackendStatus {
                state: "error".to_string(),
                api_url: api_url(port),
                project_root: String::new(),
                pid: None,
                message: Some(message),
            }
        }
    };

    let state = app.state::<BackendManagerState>();
    let managed_process = state.managed.lock().ok().and_then(|mut slot| {
        slot.as_mut()
            .and_then(|managed| match managed.child.try_wait() {
                Ok(None) if managed.port == port => {
                    Some((managed.child.id(), managed.listener_pids.clone()))
                }
                _ => None,
            })
    });
    let managed_pid = managed_process.and_then(|(child_pid, expected_listener_pids)| {
        listener_pids(port, platform)
            .ok()?
            .into_iter()
            .any(|pid| expected_listener_pids.contains(&pid))
            .then_some(child_pid)
    });
    let (state_name, message) = match probe_port(port) {
        PortProbe::Available => ("stopped", Some("本地后端未启动。".to_string())),
        PortProbe::LangGraph if managed_pid.is_some() => (
            "running",
            Some("客户端管理的 LangGraph 后端已就绪。".to_string()),
        ),
        PortProbe::LangGraph => match project_backend_listener_pids(port, &root, platform) {
            Ok(pids) if !pids.is_empty() => (
                "conflict",
                Some(
                    "端口已有当前仓库启动的 LangGraph 服务；可重新启动后由客户端管理。".to_string(),
                ),
            ),
            _ => (
                "conflict",
                Some("该端口已有其他 LangGraph 服务；客户端不会接管或终止它。".to_string()),
            ),
        },
        PortProbe::Conflict(message) => ("conflict", Some(message)),
    };
    BackendStatus {
        state: state_name.to_string(),
        api_url: api_url(port),
        project_root: root.to_string_lossy().into_owned(),
        pid: managed_pid,
        message,
    }
}

fn restart_impl(app: &AppHandle, project_root: &str, port: u16) -> Result<BackendStatus, String> {
    validate_backend_port(port)?;
    let state = app.state::<BackendManagerState>();
    let _lifecycle_guard = state
        .lifecycle
        .lock()
        .map_err(|_| "后端生命周期状态不可用。".to_string())?;
    let platform = current_platform()?;
    let root = resolve_project_root(project_root, platform)?;
    stop_managed_child(app)?;
    match probe_port(port) {
        PortProbe::Available => {}
        PortProbe::LangGraph => {
            if !stop_project_backend(&root, port, platform)? {
                return Err(format!(
                    "端口 {port} 已由其他 LangGraph 服务占用；客户端只会停止当前仓库或自己启动的后端。"
                ));
            }
        }
        PortProbe::Conflict(message) => return Err(message),
    }

    let spec = build_launch_spec(&root, platform, port);
    let bootstrap_log_path = spec.process_log.clone();
    if let Some(parent) = bootstrap_log_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("无法创建日志目录 {}：{error}", parent.display()))?;
    }
    let bootstrap_log = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&bootstrap_log_path)
        .map_err(|error| {
            format!(
                "无法创建后端启动日志 {}：{error}",
                bootstrap_log_path.display()
            )
        })?;
    let bootstrap_error_log = bootstrap_log
        .try_clone()
        .map_err(|error| format!("无法打开后端启动错误日志：{error}"))?;
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .current_dir(&spec.working_dir)
        .envs(spec.environment.iter().map(|(key, value)| (key, value)))
        .stdin(Stdio::null())
        .stdout(Stdio::from(bootstrap_log))
        .stderr(Stdio::from(bootstrap_error_log));
    configure_background_process(&mut command);
    let child = command
        .spawn()
        .map_err(|error| format!("无法启动后端脚本：{error}"))?;
    let child_id = child.id();
    {
        let mut slot = state.managed.lock().map_err(|_| "后端进程状态不可用。")?;
        *slot = Some(ManagedBackend {
            child,
            port,
            listener_pids: Vec::new(),
        });
    }

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        match probe_port(port) {
            PortProbe::LangGraph => {
                let owned_listener_pids = match verified_listener_pids(port, child_id, platform) {
                    Ok(pids) => pids,
                    Err(message) => {
                        let _ = stop_managed_child(app);
                        return Err(message);
                    }
                };
                if let Ok(mut slot) = state.managed.lock() {
                    if let Some(managed) = slot.as_mut() {
                        managed.listener_pids = owned_listener_pids.clone();
                    }
                }
                return Ok(BackendStatus {
                    state: "running".to_string(),
                    api_url: api_url(port),
                    project_root: root.to_string_lossy().into_owned(),
                    pid: owned_listener_pids.first().copied().or(Some(child_id)),
                    message: Some("LangGraph 后端已重新启动。".to_string()),
                });
            }
            PortProbe::Conflict(message) => {
                let _ = stop_managed_child(app);
                return Err(message);
            }
            PortProbe::Available => {}
        }
        let exited = {
            let mut slot = state.managed.lock().map_err(|_| "后端进程状态不可用。")?;
            slot.as_mut()
                .and_then(|managed| managed.child.try_wait().ok().flatten())
        };
        if let Some(status) = exited {
            return Err(format!(
                "后端启动进程提前退出（{status}），请查看 {}。",
                bootstrap_log_path.display()
            ));
        }
        thread::sleep(Duration::from_millis(500));
    }
    let _ = stop_managed_child(app);
    Err("后端未能在 90 秒内启动，请查看后端日志。".to_string())
}

#[tauri::command(rename_all = "camelCase")]
pub async fn backend_status(
    app: AppHandle,
    project_root: String,
    port: u16,
) -> Result<BackendStatus, String> {
    validate_backend_port(port)?;
    tauri::async_runtime::spawn_blocking(move || status_impl(&app, &project_root, port))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command(rename_all = "camelCase")]
pub async fn restart_backend(
    app: AppHandle,
    project_root: String,
    port: u16,
) -> Result<BackendStatus, String> {
    tauri::async_runtime::spawn_blocking(move || restart_impl(&app, &project_root, port))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn stop_backend(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<BackendManagerState>();
        let _lifecycle_guard = state
            .lifecycle
            .lock()
            .map_err(|_| "后端生命周期状态不可用。".to_string())?;
        stop_managed_child(&app)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command(rename_all = "camelCase")]
pub async fn backend_log(project_root: String, tail_lines: usize) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = resolve_project_root(&project_root, current_platform()?)?;
        let log_path = backend_log_path(&root);
        read_log_tail(&log_path, tail_lines.clamp(1, 2000))
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command(rename_all = "camelCase")]
pub async fn reveal_path_in_file_manager(
    project_root: String,
    base_dir: Option<String>,
    path: String,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = resolve_project_root(&project_root, current_platform()?)?;
        reveal_path(&root, base_dir.as_deref(), &path)
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn shutdown(app: &AppHandle) {
    let state = app.state::<BackendManagerState>();
    if let Ok(_lifecycle_guard) = state.lifecycle.lock() {
        let _ = stop_managed_child(app);
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::{
        launch::{start_script, validate_project_root_for, Platform},
        process::{
            currently_owned_listener_pids, is_langgraph_info, is_process_in_tree,
            is_project_backend_command,
        },
    };
    use std::{
        collections::HashMap,
        path::{Path, PathBuf},
        time::{SystemTime, UNIX_EPOCH},
    };

    fn fixture_root(platform: Platform) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("web-agent-client-root-{unique}"));
        fs::create_dir_all(root.join("web-agent")).unwrap();
        fs::create_dir_all(root.join("start/desktop")).unwrap();
        fs::write(root.join("web-agent/langgraph.json"), "{}").unwrap();
        fs::write(start_script(&root, platform), "").unwrap();
        root
    }

    #[test]
    fn validates_repository_root() {
        let root = fixture_root(Platform::MacOs);
        assert_eq!(
            validate_project_root_for(&root, Platform::MacOs).unwrap(),
            root.canonicalize().unwrap()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn generates_platform_launch_commands() {
        let root = Path::new("/repo");
        let mac = build_launch_spec(root, Platform::MacOs, 2024);
        assert_eq!(mac.program, "bash");
        assert!(mac.args[0].ends_with("start/desktop/macos-start.command"));
        assert_eq!(mac.args[1], "backend");
        let windows = build_launch_spec(Path::new(r"C:\repo"), Platform::Windows, 2024);
        assert_eq!(windows.program, "powershell.exe");
        assert!(windows
            .args
            .iter()
            .any(|arg| arg.ends_with("start/desktop/windows-start.ps1")));
        assert_eq!(windows.args.last().map(String::as_str), Some("backend"));
    }

    #[test]
    fn builds_portable_windows_launch_command() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("web-agent-client-portable-{unique}"));
        for directory in [
            "runtime/app",
            "runtime/python",
            "runtime/node",
            "runtime/playwright/node_modules/@playwright/test",
            "runtime/playwright/node_modules/playwright",
            "runtime/browsers",
            "config",
        ] {
            fs::create_dir_all(root.join(directory)).unwrap();
        }
        for file in [
            "runtime/app/langgraph.json",
            "runtime/python/python.exe",
            "runtime/node/node.exe",
            "runtime/playwright/node_modules/@playwright/test/package.json",
            "runtime/playwright/node_modules/playwright/cli.js",
            "config/.env",
        ] {
            fs::write(root.join(file), "").unwrap();
        }

        let spec = build_launch_spec(&root, Platform::Windows, 3210);

        assert!(Path::new(&spec.program).ends_with(Path::new("runtime/python/python.exe")));
        assert_eq!(spec.args[0..3], ["-m", "langgraph_cli", "dev"]);
        assert!(spec.args.windows(2).any(|args| args == ["--port", "3210"]));
        assert!(spec
            .args
            .windows(2)
            .any(|args| args == ["--n-jobs-per-worker", "4"]));
        assert!(spec
            .environment
            .iter()
            .any(|(key, value)| key == "PLAYWRIGHT_BROWSERS_PATH"
                && Path::new(value).ends_with(Path::new("runtime/browsers"))));
        assert!(spec
            .process_log
            .ends_with(Path::new("data/logs/backend.log")));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn recognizes_only_langgraph_info_responses() {
        assert!(is_langgraph_info(
            r#"{"version":"0.11.0","langgraph_py_version":"1.1.9","flags":{"assistants":true}}"#
        ));
        assert!(!is_langgraph_info(r#"{"service":"something-else"}"#));
        assert!(!is_langgraph_info("not json"));
    }

    #[test]
    fn non_langgraph_payload_is_protected() {
        assert!(!is_langgraph_info(r#"{"status":"ok","flags":{}}"#));
    }

    #[test]
    fn recognizes_only_current_project_backend_commands() {
        let root = Path::new("/workspace/web-test-agent");
        assert!(is_project_backend_command(
            "/workspace/web-test-agent/web-agent/.venv/bin/python /workspace/web-test-agent/web-agent/.venv/bin/langgraph dev",
            root,
        ));
        assert!(!is_project_backend_command(
            "/workspace/other-agent/web-agent/.venv/bin/python /workspace/other-agent/web-agent/.venv/bin/langgraph dev",
            root,
        ));
        assert!(!is_project_backend_command(
            "/workspace/web-test-agent/web-agent/.venv/bin/python -m http.server",
            root,
        ));
    }

    #[test]
    fn rejects_privileged_backend_ports() {
        assert!(validate_backend_port(1023).is_err());
        assert!(validate_backend_port(1024).is_ok());
        assert!(validate_backend_port(65535).is_ok());
    }

    #[test]
    fn reads_only_requested_log_tail() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let log_path = std::env::temp_dir().join(format!("web-agent-client-log-{unique}.log"));
        fs::write(&log_path, "one\ntwo\nthree\nfour\n").unwrap();

        assert_eq!(read_log_tail(&log_path, 2).unwrap(), "three\nfour");

        fs::remove_file(log_path).unwrap();
    }

    #[test]
    fn identifies_only_processes_in_managed_tree() {
        let parents = HashMap::from([(20, 10), (30, 20), (40, 10), (50, 50)]);

        assert!(is_process_in_tree(10, 10, &parents));
        assert!(is_process_in_tree(30, 10, &parents));
        assert!(!is_process_in_tree(40, 20, &parents));
        assert!(!is_process_in_tree(50, 10, &parents));
        assert!(!is_process_in_tree(60, 10, &parents));
    }

    #[test]
    fn rejects_recorded_listener_pid_reused_outside_managed_tree() {
        let parents = HashMap::from([(30, 99), (40, 10), (50, 10)]);

        assert_eq!(
            currently_owned_listener_pids(vec![30, 40, 50], &[30, 40], 10, &parents),
            vec![40]
        );
    }
}
