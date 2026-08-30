use std::{
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    process::Command,
};

const AUTOMATION_ROOT_KEY: &str = "DEFAULT_AUTOMATION_PROJECT_ROOT";
const DEFAULT_AUTOMATION_ROOT: &str = "~/webautotest";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
enum DesktopPlatform {
    MacOs,
    Windows,
    Linux,
}

#[derive(Debug, PartialEq, Eq)]
struct FileManagerCommand {
    program: &'static str,
    args: Vec<OsString>,
}

fn current_desktop_platform() -> Result<DesktopPlatform, String> {
    #[cfg(target_os = "macos")]
    {
        return Ok(DesktopPlatform::MacOs);
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(DesktopPlatform::Windows);
    }
    #[cfg(target_os = "linux")]
    {
        return Ok(DesktopPlatform::Linux);
    }
    #[allow(unreachable_code)]
    Err("当前系统不支持打开文件管理器。".to_string())
}

fn strip_source_location(path: &str) -> &str {
    let Some((without_last, last)) = path.rsplit_once(':') else {
        return path;
    };
    if last.is_empty() || !last.bytes().all(|value| value.is_ascii_digit()) {
        return path;
    }
    if let Some((without_line, line)) = without_last.rsplit_once(':') {
        if !line.is_empty() && line.bytes().all(|value| value.is_ascii_digit()) {
            return without_line;
        }
    }
    without_last
}

fn current_home_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        if let Some(profile) = std::env::var_os("USERPROFILE").filter(|value| !value.is_empty()) {
            return Some(PathBuf::from(profile));
        }
        let drive = std::env::var_os("HOMEDRIVE")?;
        let path = std::env::var_os("HOMEPATH")?;
        return Some(PathBuf::from(drive).join(path));
    }
    #[cfg(not(target_os = "windows"))]
    {
        std::env::var_os("HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    }
}

fn dotenv_value(contents: &str, key: &str) -> Option<String> {
    let mut found = None;
    for raw_line in contents.lines() {
        let mut line = raw_line.trim_start_matches('\u{feff}').trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("export") {
            if rest.chars().next().is_some_and(char::is_whitespace) {
                line = rest.trim_start();
            }
        }

        let Some((raw_key, raw_value)) = line.split_once('=') else {
            continue;
        };
        if raw_key.trim() != key {
            continue;
        }

        let value = raw_value.trim();
        if let Some(quote) = value
            .chars()
            .next()
            .filter(|value| matches!(value, '\'' | '"'))
        {
            let Some(relative_closing) = value[quote.len_utf8()..].rfind(quote) else {
                continue;
            };
            let closing = relative_closing + quote.len_utf8();
            let trailing = value[closing + quote.len_utf8()..].trim();
            if !trailing.is_empty() && !trailing.starts_with('#') {
                continue;
            }
            found = Some(value[quote.len_utf8()..closing].to_string());
            continue;
        }

        let comment = value.char_indices().find_map(|(index, character)| {
            if character == '#'
                && value[..index]
                    .chars()
                    .next_back()
                    .is_some_and(char::is_whitespace)
            {
                Some(index)
            } else {
                None
            }
        });
        found = Some(
            value[..comment.unwrap_or(value.len())]
                .trim_end()
                .to_string(),
        );
    }
    found
}

fn expand_home_path(value: &str, home_dir: Option<&Path>) -> Option<PathBuf> {
    if value == "~" {
        return home_dir.map(Path::to_path_buf);
    }
    if let Some(relative) = value
        .strip_prefix("~/")
        .or_else(|| value.strip_prefix("~\\"))
    {
        return home_dir.map(|home| home.join(relative));
    }
    if value.starts_with('~') {
        return None;
    }
    Some(PathBuf::from(value))
}

fn read_default_automation_root(
    project_root: &Path,
    home_dir: Option<&Path>,
) -> Result<Option<PathBuf>, String> {
    let source_env = project_root.join("web-agent/.env");
    let portable_env = project_root.join("config/.env");
    let (env_file, relative_root) = if source_env.is_file() {
        (Some(source_env), project_root.join("web-agent"))
    } else if portable_env.is_file() {
        (Some(portable_env), project_root.to_path_buf())
    } else {
        (None, project_root.to_path_buf())
    };

    let configured = match env_file {
        Some(path) => {
            let contents = fs::read_to_string(&path)
                .map_err(|error| format!("无法读取自动化项目配置 {}：{error}", path.display()))?;
            dotenv_value(&contents, AUTOMATION_ROOT_KEY).filter(|value| !value.trim().is_empty())
        }
        None => None,
    };
    let raw_root = configured.as_deref().unwrap_or(DEFAULT_AUTOMATION_ROOT);
    let Some(expanded) = expand_home_path(raw_root, home_dir) else {
        return Ok(None);
    };
    Ok(Some(if expanded.is_absolute() {
        expanded
    } else {
        relative_root.join(expanded)
    }))
}

fn trusted_roots(project_root: &Path, home_dir: Option<&Path>) -> Result<Vec<PathBuf>, String> {
    let canonical_project_root = project_root.canonicalize().map_err(|error| {
        format!(
            "项目根目录不存在或无法访问：{}（{error}）",
            project_root.display()
        )
    })?;
    if !canonical_project_root.is_dir() {
        return Err(format!(
            "项目根目录不是文件夹：{}",
            canonical_project_root.display()
        ));
    }

    let mut roots = vec![canonical_project_root.clone()];
    if let Some(automation_root) = read_default_automation_root(&canonical_project_root, home_dir)?
    {
        if let Ok(canonical_automation_root) = automation_root.canonicalize() {
            if canonical_automation_root.is_dir() && !roots.contains(&canonical_automation_root) {
                roots.push(canonical_automation_root);
            }
        }
    }
    Ok(roots)
}

fn contains_parent_component(value: &str) -> bool {
    value.split(['/', '\\']).any(|component| component == "..")
}

fn is_within_trusted_root(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

fn canonicalize_target(candidate: &Path, raw_path: &str) -> Result<PathBuf, String> {
    match candidate.canonicalize() {
        Ok(target) => Ok(target),
        Err(original_error) => {
            let stripped = strip_source_location(raw_path);
            if stripped == raw_path {
                return Err(format!(
                    "路径不存在或无法访问：{}（{original_error}）",
                    candidate.display()
                ));
            }
            let stripped_candidate = candidate
                .parent()
                .unwrap_or_else(|| Path::new(""))
                .join(Path::new(stripped).file_name().unwrap_or_default());
            stripped_candidate.canonicalize().map_err(|error| {
                format!(
                    "路径不存在或无法访问：{}（{error}）",
                    stripped_candidate.display()
                )
            })
        }
    }
}

fn resolve_safe_target(
    roots: &[PathBuf],
    base_dir: Option<&str>,
    raw_path: &str,
) -> Result<PathBuf, String> {
    let requested = raw_path.trim();
    if requested.is_empty() || requested.contains('\0') {
        return Err("要打开的路径不能为空。".to_string());
    }
    if contains_parent_component(strip_source_location(requested)) {
        return Err("路径不能包含上级目录（..）。".to_string());
    }
    let project_root = roots
        .first()
        .ok_or_else(|| "没有可用的可信项目目录。".to_string())?;

    let canonical_base = match base_dir {
        Some(base) => {
            let base = base.trim();
            if base.is_empty() || base.contains('\0') || contains_parent_component(base) {
                return Err("路径基准目录无效。".to_string());
            }
            let base_path = Path::new(base);
            let candidate = if base_path.is_absolute() {
                base_path.to_path_buf()
            } else {
                project_root.join(base_path)
            };
            let resolved = candidate.canonicalize().map_err(|error| {
                format!(
                    "路径基准目录不存在或无法访问：{}（{error}）",
                    candidate.display()
                )
            })?;
            if !resolved.is_dir() || !is_within_trusted_root(&resolved, roots) {
                return Err("路径基准目录必须位于可信项目目录内。".to_string());
            }
            resolved
        }
        None => project_root.clone(),
    };

    let requested_path = Path::new(requested);
    let candidate = if requested_path.is_absolute() {
        requested_path.to_path_buf()
    } else {
        canonical_base.join(requested_path)
    };
    let target = canonicalize_target(&candidate, requested)?;
    if !is_within_trusted_root(&target, roots) {
        return Err("只能打开仓库或已配置自动化项目目录内的文件和目录。".to_string());
    }
    Ok(target)
}

fn build_file_manager_command(
    target: &Path,
    is_directory: bool,
    platform: DesktopPlatform,
) -> Result<FileManagerCommand, String> {
    match platform {
        DesktopPlatform::MacOs => Ok(FileManagerCommand {
            program: "open",
            args: if is_directory {
                vec![target.as_os_str().to_os_string()]
            } else {
                vec![OsString::from("-R"), target.as_os_str().to_os_string()]
            },
        }),
        DesktopPlatform::Windows => {
            let explorer_target = explorer_compatible_path(target);
            let args = if is_directory {
                vec![explorer_target]
            } else {
                let mut select_argument = OsString::from("/select,");
                select_argument.push(explorer_target);
                vec![select_argument]
            };
            Ok(FileManagerCommand {
                program: "explorer.exe",
                args,
            })
        }
        DesktopPlatform::Linux => {
            let destination = if is_directory {
                target
            } else {
                target
                    .parent()
                    .ok_or_else(|| "无法确定文件所在目录。".to_string())?
            };
            Ok(FileManagerCommand {
                program: "xdg-open",
                args: vec![destination.as_os_str().to_os_string()],
            })
        }
    }
}

#[cfg(target_os = "windows")]
fn explorer_compatible_path(path: &Path) -> OsString {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};

    const VERBATIM_PREFIX: &[u16] = &[b'\\' as u16, b'\\' as u16, b'?' as u16, b'\\' as u16];
    const VERBATIM_UNC_PREFIX: &[u16] = &[
        b'\\' as u16,
        b'\\' as u16,
        b'?' as u16,
        b'\\' as u16,
        b'U' as u16,
        b'N' as u16,
        b'C' as u16,
        b'\\' as u16,
    ];
    let encoded = path.as_os_str().encode_wide().collect::<Vec<_>>();
    if let Some(relative) = encoded.strip_prefix(VERBATIM_UNC_PREFIX) {
        return OsString::from_wide(
            &[b'\\' as u16, b'\\' as u16]
                .into_iter()
                .chain(relative.iter().copied())
                .collect::<Vec<_>>(),
        );
    }
    if let Some(relative) = encoded.strip_prefix(VERBATIM_PREFIX) {
        return OsString::from_wide(relative);
    }
    path.as_os_str().to_os_string()
}

#[cfg(not(target_os = "windows"))]
fn explorer_compatible_path(path: &Path) -> OsString {
    let display_path = path.as_os_str().to_string_lossy();
    if let Some(relative) = display_path.strip_prefix(r"\\?\UNC\") {
        return OsString::from(format!(r"\\{relative}"));
    }
    if let Some(relative) = display_path.strip_prefix(r"\\?\") {
        return OsString::from(relative);
    }
    path.as_os_str().to_os_string()
}

pub(super) fn reveal_path(
    project_root: &Path,
    base_dir: Option<&str>,
    path: &str,
) -> Result<(), String> {
    let roots = trusted_roots(project_root, current_home_dir().as_deref())?;
    let target = resolve_safe_target(
        &roots,
        base_dir.filter(|value| !value.trim().is_empty()),
        path,
    )?;
    let command_spec =
        build_file_manager_command(&target, target.is_dir(), current_desktop_platform()?)?;
    let status = Command::new(command_spec.program)
        .args(command_spec.args)
        .status()
        .map_err(|error| format!("无法启动系统文件管理器：{error}"))?;
    if !status.success() {
        return Err(format!("系统文件管理器打开失败（退出状态：{status}）。"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static FIXTURE_COUNTER: AtomicU64 = AtomicU64::new(0);

    fn fixture_root() -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let sequence = FIXTURE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "web-agent-file-manager-{}-{unique}-{sequence}",
            std::process::id(),
        ));
        fs::create_dir_all(root.join("project/test_case/demo")).unwrap();
        fs::write(root.join("project/test_case/demo/case.spec.ts"), "test").unwrap();
        root
    }

    #[test]
    fn resolves_relative_paths_from_the_summary_project_directory() {
        let root = fixture_root();
        let roots = trusted_roots(&root, None).unwrap();
        let target =
            resolve_safe_target(&roots, Some("project"), "test_case/demo/case.spec.ts:20:4")
                .unwrap();

        assert_eq!(
            target,
            root.join("project/test_case/demo/case.spec.ts")
                .canonicalize()
                .unwrap()
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_parent_directory_escape() {
        let root = fixture_root();
        let outside = root.with_extension("outside.txt");
        fs::write(&outside, "outside").unwrap();
        let requested = format!("../{}", outside.file_name().unwrap().to_string_lossy());
        let roots = trusted_roots(&root, None).unwrap();

        let error = resolve_safe_target(&roots, None, &requested).unwrap_err();

        assert!(error.contains("上级目录"));
        fs::remove_file(outside).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_nul_and_missing_paths() {
        let root = fixture_root();
        let roots = trusted_roots(&root, None).unwrap();

        assert!(resolve_safe_target(&roots, None, "test_case\0secret").is_err());
        let missing_error =
            resolve_safe_target(&roots, Some("project"), "test-results/missing.json").unwrap_err();

        assert!(missing_error.contains("路径不存在或无法访问"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn trusts_the_source_env_automation_root_with_export_quotes_and_home_expansion() {
        let root = fixture_root();
        let home = root.join("home");
        let automation_root = home.join("webautotest");
        let project = automation_root.join("login-project");
        fs::create_dir_all(project.join("test-results")).unwrap();
        fs::write(project.join("test-results/report.json"), "{}").unwrap();
        fs::create_dir_all(root.join("web-agent")).unwrap();
        fs::write(
            root.join("web-agent/.env"),
            "\u{feff}  export DEFAULT_AUTOMATION_PROJECT_ROOT = \"~/webautotest\" # local artifacts\n",
        )
        .unwrap();

        let roots = trusted_roots(&root, Some(&home)).unwrap();
        let base = project.to_string_lossy();
        let target = resolve_safe_target(&roots, Some(&base), "test-results/report.json").unwrap();

        assert_eq!(
            target,
            project
                .join("test-results/report.json")
                .canonicalize()
                .unwrap()
        );
        assert!(roots.contains(&automation_root.canonicalize().unwrap()));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn resolves_a_relative_portable_automation_root_from_the_package_root() {
        let root = fixture_root();
        let automation_root = root.join("automation");
        fs::create_dir_all(automation_root.join("reports/20260829")).unwrap();
        fs::create_dir_all(root.join("config")).unwrap();
        fs::write(
            root.join("config/.env"),
            "DEFAULT_AUTOMATION_PROJECT_ROOT = 'automation'\n",
        )
        .unwrap();

        let roots = trusted_roots(&root, None).unwrap();
        let target_path = automation_root.join("reports/20260829");
        let target = resolve_safe_target(&roots, None, &target_path.to_string_lossy()).unwrap();

        assert_eq!(target, target_path.canonicalize().unwrap());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn uses_webautotest_under_home_when_the_setting_is_missing() {
        let root = fixture_root();
        let home = root.join("home");
        let automation_root = home.join("webautotest");
        fs::create_dir_all(&automation_root).unwrap();

        let roots = trusted_roots(&root, Some(&home)).unwrap();

        assert!(roots.contains(&automation_root.canonicalize().unwrap()));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ignores_a_configured_root_that_does_not_exist() {
        let root = fixture_root();
        fs::create_dir_all(root.join("web-agent")).unwrap();
        fs::write(
            root.join("web-agent/.env"),
            "DEFAULT_AUTOMATION_PROJECT_ROOT=missing-artifacts\n",
        )
        .unwrap();

        let roots = trusted_roots(&root, None).unwrap();

        assert_eq!(roots, vec![root.canonicalize().unwrap()]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_an_arbitrary_external_summary_base_directory() {
        let root = fixture_root();
        let outside = root.with_extension("outside-base");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("secret.txt"), "secret").unwrap();
        let roots = trusted_roots(&root, None).unwrap();

        let error = resolve_safe_target(&roots, Some(&outside.to_string_lossy()), "secret.txt")
            .unwrap_err();

        assert!(error.contains("可信项目目录"));
        fs::remove_dir_all(outside).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_escape() {
        use std::os::unix::fs::symlink;

        let root = fixture_root();
        let outside = root.with_extension("outside-dir");
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("secret.txt"), "secret").unwrap();
        symlink(&outside, root.join("linked-outside")).unwrap();
        let roots = trusted_roots(&root, None).unwrap();

        let error = resolve_safe_target(&roots, None, "linked-outside/secret.txt").unwrap_err();

        assert!(error.contains("已配置自动化项目目录"));
        fs::remove_file(root.join("linked-outside")).unwrap();
        fs::remove_dir_all(outside).unwrap();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builds_platform_commands_without_a_shell() {
        let file = Path::new("/repo/test_case/a.spec.ts");
        let directory = Path::new("/repo/test_case");

        assert_eq!(
            build_file_manager_command(file, false, DesktopPlatform::MacOs).unwrap(),
            FileManagerCommand {
                program: "open",
                args: vec![OsString::from("-R"), file.as_os_str().to_os_string()],
            }
        );
        assert_eq!(
            build_file_manager_command(file, false, DesktopPlatform::Windows).unwrap(),
            FileManagerCommand {
                program: "explorer.exe",
                args: vec![OsString::from("/select,/repo/test_case/a.spec.ts")],
            }
        );
        assert_eq!(
            build_file_manager_command(file, false, DesktopPlatform::Linux).unwrap(),
            FileManagerCommand {
                program: "xdg-open",
                args: vec![directory.as_os_str().to_os_string()],
            }
        );
    }

    #[test]
    fn strips_windows_verbatim_prefixes_before_calling_explorer() {
        let drive_file = Path::new(r"\\?\C:\repo\test_case\中文用例.spec.ts");
        let unc_directory = Path::new(r"\\?\UNC\server\share\test-results");

        assert_eq!(
            build_file_manager_command(drive_file, false, DesktopPlatform::Windows).unwrap(),
            FileManagerCommand {
                program: "explorer.exe",
                args: vec![OsString::from(
                    r"/select,C:\repo\test_case\中文用例.spec.ts"
                )],
            }
        );
        assert_eq!(
            build_file_manager_command(unc_directory, true, DesktopPlatform::Windows).unwrap(),
            FileManagerCommand {
                program: "explorer.exe",
                args: vec![OsString::from(r"\\server\share\test-results")],
            }
        );
    }
}
