use serde_json::Value;
use std::{
    collections::HashMap,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    process::Command,
    thread,
    time::{Duration, Instant},
};

use super::{launch::Platform, BACKEND_HOST};

const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_PROBE_RESPONSE_BYTES: usize = 64 * 1024;

#[derive(Debug, PartialEq)]
pub(super) enum PortProbe {
    Available,
    LangGraph,
    Conflict(String),
}

pub(super) fn is_langgraph_info(body: &str) -> bool {
    let Ok(value) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    value
        .get("langgraph_py_version")
        .and_then(Value::as_str)
        .is_some()
        && value.get("flags").and_then(Value::as_object).is_some()
}

pub(super) fn probe_port(port: u16) -> PortProbe {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(700)) {
        Ok(stream) => stream,
        Err(error) if error.kind() == std::io::ErrorKind::ConnectionRefused => {
            return PortProbe::Available;
        }
        Err(error) => return PortProbe::Conflict(format!("无法检查端口：{error}")),
    };
    let _ = stream.set_write_timeout(Some(PROBE_TIMEOUT));
    let request = format!(
        "GET /info HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    if let Err(error) = stream.write_all(request.as_bytes()) {
        return PortProbe::Conflict(format!("端口服务无法响应 LangGraph 检查：{error}"));
    }
    let deadline = Instant::now() + PROBE_TIMEOUT;
    let mut response_bytes = Vec::with_capacity(4096);
    let mut chunk = [0_u8; 4096];
    loop {
        if response_bytes.len() >= MAX_PROBE_RESPONSE_BYTES {
            return PortProbe::Conflict("端口服务响应超过 64 KiB 上限。".to_string());
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return PortProbe::Conflict("端口服务未在 2 秒内完成 LangGraph 检查。".to_string());
        }
        let _ = stream.set_read_timeout(Some(remaining));
        let read_limit = chunk
            .len()
            .min(MAX_PROBE_RESPONSE_BYTES - response_bytes.len());
        match stream.read(&mut chunk[..read_limit]) {
            Ok(0) => break,
            Ok(read) => response_bytes.extend_from_slice(&chunk[..read]),
            Err(error) => {
                return PortProbe::Conflict(format!(
                    "端口服务未返回有效的 LangGraph 信息：{error}"
                ));
            }
        }
    }
    let response = String::from_utf8_lossy(&response_bytes);
    let (headers, body) = response
        .split_once("\r\n\r\n")
        .unwrap_or((response.as_ref(), ""));
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

pub(super) fn listener_pids(port: u16, platform: Platform) -> Result<Vec<u32>, String> {
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

fn process_parent_map(platform: Platform) -> Result<HashMap<u32, u32>, String> {
    let output = match platform {
        Platform::MacOs => Command::new("ps").args(["-axo", "pid=,ppid="]).output(),
        Platform::Windows => Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }",
            ])
            .output(),
    }
    .map_err(|error| format!("无法查询进程关系：{error}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("无法查询进程关系：{}", stderr.trim()));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(stdout
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let pid = fields.next()?.parse::<u32>().ok()?;
            let parent_pid = fields.next()?.parse::<u32>().ok()?;
            Some((pid, parent_pid))
        })
        .collect())
}

pub(super) fn is_process_in_tree(pid: u32, root_pid: u32, parents: &HashMap<u32, u32>) -> bool {
    let mut current = pid;
    for _ in 0..128 {
        if current == root_pid {
            return true;
        }
        let Some(parent) = parents.get(&current).copied() else {
            return false;
        };
        if parent == 0 || parent == current {
            return false;
        }
        current = parent;
    }
    false
}

pub(super) fn currently_owned_listener_pids(
    current_listener_pids: Vec<u32>,
    recorded_listener_pids: &[u32],
    root_pid: u32,
    parents: &HashMap<u32, u32>,
) -> Vec<u32> {
    current_listener_pids
        .into_iter()
        .filter(|pid| {
            recorded_listener_pids.contains(pid) && is_process_in_tree(*pid, root_pid, parents)
        })
        .collect()
}

pub(super) fn currently_owned_listeners(
    port: u16,
    recorded_listener_pids: &[u32],
    root_pid: u32,
    platform: Platform,
) -> Vec<u32> {
    match (listener_pids(port, platform), process_parent_map(platform)) {
        (Ok(current), Ok(parents)) => {
            currently_owned_listener_pids(current, recorded_listener_pids, root_pid, &parents)
        }
        _ => Vec::new(),
    }
}

pub(super) fn verified_listener_pids(
    port: u16,
    root_pid: u32,
    platform: Platform,
) -> Result<Vec<u32>, String> {
    let listeners = listener_pids(port, platform)?;
    let parents = process_parent_map(platform)?;
    let owned: Vec<u32> = listeners
        .into_iter()
        .filter(|pid| is_process_in_tree(*pid, root_pid, &parents))
        .collect();
    if owned.is_empty() {
        return Err(format!(
            "端口 {port} 上的 LangGraph 服务不属于本次启动的后端进程，客户端不会接管它。"
        ));
    }
    Ok(owned)
}

pub(super) fn terminate_pid(pid: u32, platform: Platform, force: bool) {
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

pub(super) fn wait_until_available(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if probe_port(port) == PortProbe::Available {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}
