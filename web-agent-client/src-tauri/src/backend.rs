use serde::Serialize;
use serde_json::Value;
use std::{
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};

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
}

#[derive(Default)]
pub struct BackendManagerState(Mutex<Option<ManagedBackend>>);

#[derive(Debug, PartialEq)]
enum PortProbe {
    Available,
    LangGraph,
    Conflict(String),
}

#[derive(Debug, Clone, Copy, PartialEq)]
#[allow(dead_code)]
enum Platform {
    MacOs,
    Windows,
}

#[derive(Debug, PartialEq)]
struct LaunchSpec {
    program: String,
    args: Vec<String>,
}

fn api_url(port: u16) -> String {
    format!("http://{BACKEND_HOST}:{port}")
}

fn current_platform() -> Result<Platform, String> {
    #[cfg(target_os = "macos")]
    {
        return Ok(Platform::MacOs);
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(Platform::Windows);
    }
    #[allow(unreachable_code)]
    Err("桌面客户端仅支持 macOS 和 Windows。".to_string())
}

fn start_script(root: &Path, platform: Platform) -> PathBuf {
    match platform {
        Platform::MacOs => root.join("start/macos-start.command"),
        Platform::Windows => root.join("start/windows-start.ps1"),
    }
}

fn validate_project_root_for(root: &Path, platform: Platform) -> Result<PathBuf, String> {
    let canonical = root
        .canonicalize()
        .map_err(|_| format!("项目目录不存在：{}", root.display()))?;
    let graph_config = canonical.join("web-agent/langgraph.json");
    let script = start_script(&canonical, platform);
    if !graph_config.is_file() {
        return Err(format!("项目目录无效，缺少 {}", graph_config.display()));
    }
    if !script.is_file() {
        return Err(format!("项目目录无效，缺少 {}", script.display()));
    }
    Ok(canonical)
}

fn find_root_from(start: &Path, platform: Platform) -> Option<PathBuf> {
    let mut candidate = if start.is_file() {
        start.parent()?.to_path_buf()
    } else {
        start.to_path_buf()
    };
    loop {
        if let Ok(root) = validate_project_root_for(&candidate, platform) {
            return Some(root);
        }
        if !candidate.pop() {
            return None;
        }
    }
}

fn resolve_project_root(candidate: &str, platform: Platform) -> Result<PathBuf, String> {
    if !candidate.trim().is_empty() {
        return validate_project_root_for(Path::new(candidate), platform);
    }

    let mut starts = Vec::new();
    if let Ok(directory) = std::env::current_dir() {
        starts.push(directory);
    }
    if let Ok(executable) = std::env::current_exe() {
        starts.push(executable);
    }
    for start in starts {
        if let Some(root) = find_root_from(&start, platform) {
            return Ok(root);
        }
    }
    Err("无法自动定位项目目录，请选择 Web Test Agent 仓库根目录。".to_string())
}

fn is_langgraph_info(body: &str) -> bool {
    let Ok(value) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    value
        .get("langgraph_py_version")
        .and_then(Value::as_str)
        .is_some()
        && value.get("flags").and_then(Value::as_object).is_some()
}

fn probe_port(port: u16) -> PortProbe {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(700)) {
        Ok(stream) => stream,
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::ConnectionRefused | std::io::ErrorKind::TimedOut
            ) =>
        {
            return PortProbe::Available;
        }
        Err(error) => return PortProbe::Conflict(format!("无法检查端口：{error}")),
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "GET /info HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    if let Err(error) = stream.write_all(request.as_bytes()) {
        return PortProbe::Conflict(format!("端口服务无法响应 LangGraph 检查：{error}"));
    }
    let mut response = String::new();
    if let Err(error) = stream.read_to_string(&mut response) {
        return PortProbe::Conflict(format!("端口服务未返回有效的 LangGraph 信息：{error}"));
    }
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .unwrap_or((response.as_str(), ""));
    if headers
        .lines()
        .next()
        .is_some_and(|line| line.contains(" 200 "))
        && is_langgraph_info(body)
    {
        PortProbe::LangGraph
    } else {
        PortProbe::Conflict("该端口已被非 LangGraph 服务占用，客户端不会终止它。".to_string())
    }
}

fn listener_pids(port: u16, platform: Platform) -> Result<Vec<u32>, String> {
    let output = match platform {
        Platform::MacOs => Command::new("lsof")
            .args([
                "-nP",
                &format!("-tiTCP:{port}"),
                "-sTCP:LISTEN",
            ])
            .output(),
        Platform::Windows => Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                &format!(
                    "Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"
                ),
            ])
            .output(),
    }
    .map_err(|error| format!("无法查询端口进程：{error}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(stdout
        .split_whitespace()
        .filter_map(|value| value.parse::<u32>().ok())
        .collect())
}

fn terminate_pid(pid: u32, platform: Platform, force: bool) {
    match platform {
        Platform::MacOs => {
            let signal = if force { "-KILL" } else { "-TERM" };
            let _ = Command::new("kill")
                .args([signal, &pid.to_string()])
                .status();
        }
        Platform::Windows => {
            let mut command = Command::new("taskkill");
            command.args(["/PID", &pid.to_string(), "/T"]);
            if force {
                command.arg("/F");
            }
            let _ = command.status();
        }
    }
}

fn wait_until_available(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if probe_port(port) == PortProbe::Available {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}

fn stop_langgraph_listener(port: u16, platform: Platform) -> Result<(), String> {
    match probe_port(port) {
        PortProbe::Available => return Ok(()),
        PortProbe::Conflict(message) => return Err(message),
        PortProbe::LangGraph => {}
    }
    let pids = listener_pids(port, platform)?;
    if pids.is_empty() {
        return Err(format!(
            "已识别 LangGraph 服务，但无法确定 {port} 端口的进程。"
        ));
    }
    for pid in &pids {
        terminate_pid(*pid, platform, false);
    }
    if !wait_until_available(port, Duration::from_secs(5)) {
        for pid in pids {
            terminate_pid(pid, platform, true);
        }
    }
    if wait_until_available(port, Duration::from_secs(3)) {
        Ok(())
    } else {
        Err(format!("无法停止 {port} 端口上的 LangGraph 服务。"))
    }
}

fn build_launch_spec(root: &Path, platform: Platform) -> LaunchSpec {
    let script = start_script(root, platform).to_string_lossy().into_owned();
    match platform {
        Platform::MacOs => LaunchSpec {
            program: "bash".to_string(),
            args: vec![script, "backend".to_string()],
        },
        Platform::Windows => LaunchSpec {
            program: "powershell.exe".to_string(),
            args: vec![
                "-NoProfile".to_string(),
                "-ExecutionPolicy".to_string(),
                "Bypass".to_string(),
                "-File".to_string(),
                script,
                "-Mode".to_string(),
                "backend".to_string(),
            ],
        },
    }
}

fn stop_managed_child(app: &AppHandle) {
    let state = app.state::<BackendManagerState>();
    let managed = state.0.lock().ok().and_then(|mut slot| slot.take());
    if let Some(mut managed) = managed {
        if let Ok(platform) = current_platform() {
            let _ = stop_langgraph_listener(managed.port, platform);
        }
        let _ = managed.child.kill();
        let _ = managed.child.wait();
    }
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
    let managed_pid = state.0.lock().ok().and_then(|mut slot| {
        slot.as_mut()
            .and_then(|managed| match managed.child.try_wait() {
                Ok(None) if managed.port == port => Some(managed.child.id()),
                _ => None,
            })
    });
    let (state_name, message) = match probe_port(port) {
        PortProbe::Available => ("stopped", Some("本地后端未启动。".to_string())),
        PortProbe::LangGraph => ("running", Some("LangGraph 后端已就绪。".to_string())),
        PortProbe::Conflict(message) => ("conflict", Some(message)),
    };
    BackendStatus {
        state: state_name.to_string(),
        api_url: api_url(port),
        project_root: root.to_string_lossy().into_owned(),
        pid: managed_pid.or_else(|| listener_pids(port, platform).ok()?.first().copied()),
        message,
    }
}

fn restart_impl(app: &AppHandle, project_root: &str, port: u16) -> Result<BackendStatus, String> {
    let platform = current_platform()?;
    let root = resolve_project_root(project_root, platform)?;
    stop_managed_child(app);
    stop_langgraph_listener(port, platform)?;

    let spec = build_launch_spec(&root, platform);
    let child = Command::new(&spec.program)
        .args(&spec.args)
        .current_dir(&root)
        .env("BACKEND_PORT", port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("无法启动后端脚本：{error}"))?;
    let child_id = child.id();
    {
        let state = app.state::<BackendManagerState>();
        let mut slot = state.0.lock().map_err(|_| "后端进程状态不可用。")?;
        *slot = Some(ManagedBackend { child, port });
    }

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        match probe_port(port) {
            PortProbe::LangGraph => {
                return Ok(BackendStatus {
                    state: "running".to_string(),
                    api_url: api_url(port),
                    project_root: root.to_string_lossy().into_owned(),
                    pid: listener_pids(port, platform)
                        .ok()
                        .and_then(|pids| pids.first().copied())
                        .or(Some(child_id)),
                    message: Some("LangGraph 后端已重新启动。".to_string()),
                })
            }
            PortProbe::Conflict(message) => {
                stop_managed_child(app);
                return Err(message);
            }
            PortProbe::Available => {}
        }
        let exited = {
            let state = app.state::<BackendManagerState>();
            let mut slot = state.0.lock().map_err(|_| "后端进程状态不可用。")?;
            slot.as_mut()
                .and_then(|managed| managed.child.try_wait().ok().flatten())
        };
        if let Some(status) = exited {
            return Err(format!(
                "后端启动脚本提前退出（{status}），请查看后端日志。"
            ));
        }
        thread::sleep(Duration::from_millis(500));
    }
    stop_managed_child(app);
    Err("后端未能在 90 秒内启动，请查看后端日志。".to_string())
}

#[tauri::command(rename_all = "camelCase")]
pub async fn backend_status(
    app: AppHandle,
    project_root: String,
    port: u16,
) -> Result<BackendStatus, String> {
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
pub async fn stop_backend(app: AppHandle, port: u16) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let platform = current_platform()?;
        stop_managed_child(&app);
        stop_langgraph_listener(port, platform)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command(rename_all = "camelCase")]
pub async fn backend_log(project_root: String, tail_lines: usize) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let root = resolve_project_root(&project_root, current_platform()?)?;
        let log_path = root.join("start/backend.log");
        let content = fs::read_to_string(&log_path)
            .map_err(|error| format!("无法读取 {}：{error}", log_path.display()))?;
        let lines: Vec<&str> = content.lines().collect();
        let start = lines.len().saturating_sub(tail_lines.clamp(1, 2000));
        Ok(lines[start..].join("\n"))
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn shutdown(app: &AppHandle) {
    stop_managed_child(app);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture_root(platform: Platform) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("web-agent-client-root-{unique}"));
        fs::create_dir_all(root.join("web-agent")).unwrap();
        fs::create_dir_all(root.join("start")).unwrap();
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
        let mac = build_launch_spec(root, Platform::MacOs);
        assert_eq!(mac.program, "bash");
        assert!(mac.args[0].ends_with("start/macos-start.command"));
        assert_eq!(mac.args[1], "backend");
        let windows = build_launch_spec(Path::new(r"C:\repo"), Platform::Windows);
        assert_eq!(windows.program, "powershell.exe");
        assert!(windows
            .args
            .iter()
            .any(|arg| arg.ends_with("windows-start.ps1")));
        assert_eq!(windows.args.last().map(String::as_str), Some("backend"));
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
}
