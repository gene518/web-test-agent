use std::{
    path::{Path, PathBuf},
    process::Command,
};

use super::BACKEND_HOST;

#[derive(Debug, Clone, Copy, PartialEq)]
#[allow(dead_code)]
pub(super) enum Platform {
    MacOs,
    Windows,
}

#[derive(Debug, PartialEq)]
pub(super) struct LaunchSpec {
    pub(super) program: String,
    pub(super) args: Vec<String>,
    pub(super) working_dir: PathBuf,
    pub(super) environment: Vec<(String, String)>,
    pub(super) process_log: PathBuf,
}

#[derive(Debug, PartialEq)]
struct PortableRuntime {
    root: PathBuf,
    app_dir: PathBuf,
    python: PathBuf,
    node: PathBuf,
    playwright_cli: PathBuf,
    playwright_modules: PathBuf,
    browsers: PathBuf,
    env_file: PathBuf,
    backend_log: PathBuf,
}

pub(super) fn current_platform() -> Result<Platform, String> {
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

pub(super) fn start_script(root: &Path, platform: Platform) -> PathBuf {
    match platform {
        Platform::MacOs => root.join("start/macos-start.command"),
        Platform::Windows => root.join("start/windows-start.ps1"),
    }
}

fn portable_runtime(root: &Path) -> Option<PortableRuntime> {
    let runtime_dir = root.join("runtime");
    let app_dir = runtime_dir.join("app");
    let python = runtime_dir.join("python/python.exe");
    let node = runtime_dir.join("node/node.exe");
    let playwright_modules = runtime_dir.join("playwright/node_modules");
    let playwright_cli = playwright_modules.join("playwright/cli.js");
    let browsers = runtime_dir.join("browsers");
    let env_file = root.join("config/.env");
    if !app_dir.join("langgraph.json").is_file()
        || !python.is_file()
        || !node.is_file()
        || !playwright_cli.is_file()
        || !playwright_modules
            .join("@playwright/test/package.json")
            .is_file()
        || !browsers.is_dir()
        || !env_file.is_file()
    {
        return None;
    }
    Some(PortableRuntime {
        root: root.to_path_buf(),
        app_dir,
        python,
        node,
        playwright_cli,
        playwright_modules,
        browsers,
        env_file,
        backend_log: root.join("data/logs/backend.log"),
    })
}

fn portable_root_from_executable(platform: Platform) -> Option<PathBuf> {
    if platform != Platform::Windows {
        return None;
    }
    let executable = std::env::current_exe().ok()?;
    let root = executable.parent()?.canonicalize().ok()?;
    portable_runtime(&root).map(|runtime| runtime.root)
}

pub(super) fn validate_project_root_for(
    root: &Path,
    platform: Platform,
) -> Result<PathBuf, String> {
    let canonical = root
        .canonicalize()
        .map_err(|_| format!("项目目录不存在：{}", root.display()))?;
    if platform == Platform::Windows && portable_runtime(&canonical).is_some() {
        return Ok(canonical);
    }
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

pub(super) fn resolve_project_root(candidate: &str, platform: Platform) -> Result<PathBuf, String> {
    if let Some(root) = portable_root_from_executable(platform) {
        return Ok(root);
    }
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

pub(super) fn build_launch_spec(root: &Path, platform: Platform, port: u16) -> LaunchSpec {
    if platform == Platform::Windows {
        if let Some(runtime) = portable_runtime(root) {
            let python_path = format!(
                "{};{}",
                runtime.app_dir.display(),
                runtime
                    .python
                    .parent()
                    .unwrap_or(root)
                    .join("Lib/site-packages")
                    .display()
            );
            return LaunchSpec {
                program: runtime.python.to_string_lossy().into_owned(),
                args: vec![
                    "-m".to_string(),
                    "langgraph_cli".to_string(),
                    "dev".to_string(),
                    "--host".to_string(),
                    BACKEND_HOST.to_string(),
                    "--port".to_string(),
                    port.to_string(),
                    "--no-browser".to_string(),
                    "--allow-blocking".to_string(),
                    "--n-jobs-per-worker".to_string(),
                    "4".to_string(),
                    "--no-reload".to_string(),
                    "--server-log-level".to_string(),
                    "ERROR".to_string(),
                    "--config".to_string(),
                    runtime
                        .app_dir
                        .join("langgraph.json")
                        .to_string_lossy()
                        .into_owned(),
                ],
                working_dir: runtime.app_dir,
                environment: vec![
                    ("PYTHONPATH".to_string(), python_path),
                    ("PYTHONUTF8".to_string(), "1".to_string()),
                    ("PYTHONDONTWRITEBYTECODE".to_string(), "1".to_string()),
                    (
                        "WEB_TEST_AGENT_ENV_FILE".to_string(),
                        runtime.env_file.to_string_lossy().into_owned(),
                    ),
                    (
                        "WEB_TEST_AGENT_NODE_EXECUTABLE".to_string(),
                        runtime.node.to_string_lossy().into_owned(),
                    ),
                    (
                        "WEB_TEST_AGENT_PLAYWRIGHT_CLI".to_string(),
                        runtime.playwright_cli.to_string_lossy().into_owned(),
                    ),
                    (
                        "WEB_TEST_AGENT_PLAYWRIGHT_MODULES".to_string(),
                        runtime.playwright_modules.to_string_lossy().into_owned(),
                    ),
                    (
                        "PLAYWRIGHT_BROWSERS_PATH".to_string(),
                        runtime.browsers.to_string_lossy().into_owned(),
                    ),
                ],
                process_log: runtime.backend_log,
            };
        }
    }

    let script = start_script(root, platform).to_string_lossy().into_owned();
    match platform {
        Platform::MacOs => LaunchSpec {
            program: "bash".to_string(),
            args: vec![script, "backend".to_string()],
            working_dir: root.to_path_buf(),
            environment: vec![("BACKEND_PORT".to_string(), port.to_string())],
            process_log: root.join("start/backend-bootstrap.log"),
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
            working_dir: root.to_path_buf(),
            environment: vec![("BACKEND_PORT".to_string(), port.to_string())],
            process_log: root.join("start/backend-bootstrap.log"),
        },
    }
}

pub(super) fn backend_log_path(root: &Path) -> PathBuf {
    portable_runtime(root)
        .map(|runtime| runtime.backend_log)
        .unwrap_or_else(|| root.join("start/backend.log"))
}

pub(super) fn configure_background_process(command: &mut Command) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(target_os = "windows"))]
    let _ = command;
}
