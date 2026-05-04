"""Generator 阶段业务提示词。"""

GENERATOR_BUSINESS_PROMPT = """\
# Generator 阶段移动端业务约束
本提示词只补充 Generator 阶段的业务约束；`generator.py` 已声明的通用生成流程、单文件单测试、`describe` / `test` 命名和步骤注释要求，此处不再重复。

---

## 阶段边界

- 输入是已经确认的测试计划；严格按计划中的场景、步骤和预期生成脚本，不臆造计划外功能。
- Generator 阶段只产出 `.spec.ts` 测试脚本，并在生成脚本后把对应测试计划 md 复制到正式目录、删除旧的 planning 目录。
- 不要在该阶段新增、改写或保存测试计划，也不要写入 `[UPDATED]` 用例记录。

---

## 目录与文件规则

- 测试脚本路径固定为 `test_case/{plan-name}/{case-name}.spec.ts`。
- 如果测试计划当前在 `test_case/aaaplanning_{plan-name}/aaa_{plan-name}.md`，生成脚本后，把这个测试计划 md 复制到 `test_case/{plan-name}/aaa_{plan-name}.md`。
- 复制完成后，删除原来的 `test_case/aaaplanning_{plan-name}/` 文件夹。
- `aaa_{plan-name}.md` 的内容不要改动，也不要再额外创建其他并行目录。

---

## 移动端交互规则

- 定位器优先使用语义化 API：`page.getByRole()`、`page.getByText()`、`page.locator()`。
- 所有点击类操作统一使用 `locator.tap()`；禁止使用 `click()`。
- 文本输入优先使用 `page.fill()`、`page.press()`、`page.keyboard.press()`；输入框发送文本必须点击页面发送按钮，不要用 Enter 代替发送。
- 同一种交互方式最多尝试两次；仍失败时应重新观察页面并改用新方案。
- 所有操作都要模拟真实用户行为；禁止使用 `page.evaluate()` 直接触发 DOM 行为。

---

## 业务实现规则

- 只有当同一操作在 5 个以上用例中重复时，才允许抽到 `shared/` 基础类；低频动作保留在业务用例中。

---

## 日志代码规则

- 日志代码以减少行数为准。
- 当前节点名必须通过 `log_title(..., node_name=...)` 写入日志开头 `【】` 标题前缀第一段，不单独打印 `node_name` 字段。
- `log_debug_event(...)` 等日志辅助方法调用保持单行。
- `logger.info`、`logger.warning`、`logger.exception` 等带 `msg + args` 的调用严格写成两行：第一行完整 `format string`，第二行写全部参数，且最后一个 `)` 放在第二行。
- 禁止在 `format string` 中写 `\\n`。

---

## 收尾规则

- 脚本生成完成后，必须按 `generator_write_test` -> `browser_run_code` 的顺序收尾。
- `browser_run_code` 必须执行以下函数表达式关闭整个浏览器进程：

```js
async (page) => { const b = page.context().browser(); await b.close(); }
```

> `browser_run_code` 需要函数表达式格式，不能传裸代码语句。
> `browser_close` 只会关闭页面，不会关闭浏览器进程。
> 关闭后若出现 `Target page, context or browser has been closed` 一类报错，可视为成功收尾。
"""
