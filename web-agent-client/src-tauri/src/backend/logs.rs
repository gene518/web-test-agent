use std::{
    fs,
    io::{Read, Seek, SeekFrom},
    path::Path,
};

const MAX_LOG_TAIL_BYTES: u64 = 2 * 1024 * 1024;

pub(super) fn read_log_tail(log_path: &Path, tail_lines: usize) -> Result<String, String> {
    let mut file = fs::File::open(log_path)
        .map_err(|error| format!("无法读取 {}：{error}", log_path.display()))?;
    let file_len = file
        .metadata()
        .map_err(|error| format!("无法读取 {} 元数据：{error}", log_path.display()))?
        .len();
    let start = file_len.saturating_sub(MAX_LOG_TAIL_BYTES);
    file.seek(SeekFrom::Start(start))
        .map_err(|error| format!("无法定位 {}：{error}", log_path.display()))?;
    let mut bytes = Vec::with_capacity((file_len - start) as usize);
    file.take(MAX_LOG_TAIL_BYTES)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("无法读取 {}：{error}", log_path.display()))?;
    let content = String::from_utf8_lossy(&bytes);
    let mut lines: Vec<&str> = content.lines().rev().take(tail_lines).collect();
    lines.reverse();
    let mut result = lines.join("\n");
    if start > 0 {
        result.insert_str(0, "[日志前部已截断]\n");
    }
    Ok(result)
}
