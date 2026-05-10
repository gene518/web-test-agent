#!/usr/bin/env python3
"""提交本轮改动到 GitHub"""

import subprocess
import os
import sys

os.chdir('/Users/jin/Documents/code/github/web-test-agent')

# 第一步：暂存所有改动
print("=== 第一步：暂存所有改动 ===")
result = subprocess.run(['git', 'add', '-A'], capture_output=True, text=True)
if result.returncode != 0:
    print(f"错误：git add 失败")
    print(result.stderr)
    sys.exit(1)
print("✓ 所有改动已暂存")

# 第二步：查看暂存的改动统计
print("\n=== 第二步：查看暂存的改动统计 ===")
result = subprocess.run(['git', 'diff', '--cached', '--stat'], capture_output=True, text=True)
lines = result.stdout.split('\n')
print('\n'.join(lines[-10:]))

# 第三步：提交改动
print("\n=== 第三步：提交改动 ===")
commit_message = """优化：Plan/Generator/Healer 分层、MCP 工具业务规则下沉、Specialist 通用输入解析、浏览器关闭兜底、Finalize 单阶段优化

主要改动：

1. Specialist 分层对齐 Master
   - Plan/Generator/Healer 的 *_agent.py 只做静态配置入口
   - 事件流循环、工具状态机、产物抽取挪到 runtime.py
   - 新增 PlanRuntimeHelper / GeneratorRuntimeHelper / HealerRuntimeHelper

2. MCP 工具业务规则下沉到 provider
   - MCPToolsManager 删除 planner_save_plan 特例代码
   - 新增 tools/tool_invocation.py 通用工具辅助
   - 新增 tools/playwright/planner_save_plan_wrapper.py 业务包装
   - PlaywrightTestMCPProvider 实现 post_process_tool 钩子

3. Specialist 通用输入解析与异常识别
   - 新增 specialist_helpers/input_resolution.py
   - 新增 specialist_helpers/browser_close.py
   - Plan/Generator/Healer 复用通用函数，删除重复实现

4. 浏览器关闭兜底与 MCP 会话精准释放
   - MCPToolsManager 新增 close_session(server, workspace)
   - BaseSpecialistAgent 新增 _close_playwright_mcp_session
   - Plan/Generator/Healer runtime 在 finally 里兜底关闭

5. Finalize 节点单阶段优化
   - _workflow_managed_pipeline 改为 len(pipeline) >= 2
   - IntentJudgeNode 单阶段直通 end，避免重复总结
   - 多阶段仍走 finalize_turn 统一汇总

6. 代码质量
   - 所有英文注释/docstring 改写为中文
   - TODO(重点流程) 标签统一改为 主链路
   - 更新 README 目录结构与核心类说明
   - 补充 DEVELOPMENT_GUIDE 分层约定

7. 测试与回归
   - 所有测试全部通过
   - 新增 close_session 专项测试
   - 更新单阶段相关断言
   - 补充 MCP 关闭验证"""

result = subprocess.run(
    ['git', 'commit', '-m', commit_message],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print(f"错误：git commit 失败")
    print(result.stderr)
    sys.exit(1)

print("✓ 改动已提交")
print(result.stdout)

# 第四步：推送到 GitHub
print("\n=== 第四步：推送到 GitHub ===")
result = subprocess.run(['git', 'push', 'origin', 'main'], capture_output=True, text=True)

if result.returncode != 0:
    print(f"错误：git push 失败")
    print(result.stderr)
    sys.exit(1)

print("✓ 改动已推送到 GitHub")
print(result.stdout)

# 第五步：验证
print("\n=== 第五步：验证 ===")
result = subprocess.run(['git', 'status'], capture_output=True, text=True)
print(result.stdout)

print("\n✓ 所有操作完成！")
