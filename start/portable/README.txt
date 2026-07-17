Web Test Agent - Windows 11 x64 免安装版

使用方法

1. 必须先完整解压 ZIP，不能在压缩软件中直接运行。
2. 双击“Web Test Agent.exe”。
3. 模型配置位于 config\.env。本包未预置模型密钥时，请先填写该文件。
4. 默认测试工程位于当前 Windows 用户目录下的 webautotest 文件夹。

本包已经包含 Python、Node.js、Playwright、Chromium 和固定版 WebView2，
不需要安装 Git、Python、Node.js、Rust、uv、pnpm 或浏览器依赖。

目录说明

- config\.env：模型与 Agent 配置。
- data\logs\backend.log：本地后端日志。
- runtime\：程序运行时，请勿删除或移动其中的文件。

注意事项

- 仅支持 Windows 11 x64，不支持 Windows ARM64。
- 首次运行或未签名版本可能触发 Windows SmartScreen 提示。
- 不要把包含真实 API Key 的压缩包发送给不可信人员。
- 关闭桌面客户端会同时停止本次客户端启动的本地后端和浏览器进程。
