"""Plan 模式使用的移动端测试计划约定。"""

MOBILE_PLAN_CONVENTIONS_PROMPT = """\
# Plan 阶段移动端测试计划约定
本约定只补充 `plan.py` 之外的执行细则；`plan.py` 已明确的角色定位、探索目标、场景覆盖和文档总要求不在这里重复。

## 计划目录与命名

- 新建且尚未实现脚本的计划保存到 `test_case/aaaplanning_{plan-name}/`。
- 计划文档名统一为 `aaa_{plan-name}.md`，确保目录排序靠前。
- Plan 阶段只生成或更新 Markdown 测试计划，不创建 `.spec.ts`。
- `planner_save_plan.fileName` 必须使用相对 `project_dir` 的路径。
- `planner_save_plan.suites[].tests[].steps[].expect` 必须始终传字符串数组；即使只有一条预期，也要写成 `["..."]`。

## 移动端交互补充约定

- 如果用户提供了 `feature_points`，优先覆盖这些功能点，再结合页面探索补齐关键路径。
- 按移动端真实交互语义探索页面：点击按 `tap` 理解，发送文本优先点击页面发送按钮，不要用 Enter 代替。
- 同一种交互方式最多尝试两次；仍失败时先重新观察页面，再换一种方案。

## 计划文档格式

在 `plan.py` 已要求 Markdown 测试计划的前提下，文档结构补充如下：

```markdown
# {功能名}功能测试计划

## Application Overview
{基于页面探索得到的简要描述}

## Test Scenarios

### 1. {场景组名}
**Seed:** `seed.spec.ts`

#### 1.1. a_{具体场景名}
**File:** `{plan-name}/a_{case-name}.spec.ts`

**Steps:**
1. {操作描述}
   - expect:
     - {预期结果}
2. {操作描述}
   - expect:
     - {预期结果}
```

## 计划结构约定

- 每个具体场景前缀遵循 `a_` → `b_` → ... → `z_` → `aa_` → `ab_`...，全部小写。
- 场景标题前缀必须与未来脚本文件名前缀一致，例如 `a_login_success` 对应 `a_login_success.spec.ts`。
- 不要在计划中写实现代码、选择器细节、运行结果或命令。

## 工具错误恢复

- 当工具结果里出现 `ok=false` 或 `type=tool_error` 时，表示上一步工具调用失败，但任务不能立即中断。
- 必须基于已有操作历史继续推理，不能从头开始。
- 禁止重复完全相同的工具调用和参数。
- 先分析 `error_type`、`error_message` 和 `tool_name`。
- 能恢复时，优先重新观察页面、修正参数、换工具、等待页面稳定。
- 如果是点击被遮挡，不要重复相同的 `browser_click`；先 `browser_snapshot`，如果目标是输入框优先 `browser_type`。
- 如果是超时，不要立刻用同样参数重试；先观察页面状态，必要时跳过非关键探索步骤。
- 如果该步骤不是完成测试计划必需的，可以跳过并继续推进整体任务。
- 如果连续多次失败，再停止工具调用，并明确说明失败原因和已尝试方案。

## 保存与收尾

- 以 `planner_save_plan` 的成功结果作为计划完成信号。
- 禁止在 `planner_save_plan` 失败后使用 `create_file` 或其他文件工具绕过 planner 工作流。
- `planner_save_plan` 参数错误或保存失败时，必须先修正参数再重试。
- `planner_save_plan` 成功后，调用 `browser_run_code` 执行以下函数表达式关闭浏览器，然后停止：

```js
async (page) => { const b = page.context().browser(); await b.close(); }
```

> `browser_run_code` 需要函数表达式格式；关闭后出现 `Target page, context or browser has been closed` 一类报错可视为成功收尾。
"""
