export interface PromptTemplate {
  id: "full" | "plan" | "generator" | "healer";
  title: string;
  content: string;
}

export const AGENT_INTRO = "网页测试智能体专注自动化，可探索真实页面、规划测试用例、生成脚本，并自动调试修复失败，完成测试闭环。";

export const PROMPT_TEMPLATES: readonly PromptTemplate[] = [
  {
    id: "full",
    title: "plan+generator+healer",
    content: `请执行完整的 Web UI 自动化测试闭环，严格按 plan → generator → healer 顺序连续执行。阶段之间自动继承上一阶段产物，不要等待我再次确认。

- 自动化工程名称：{project_name}
- 被测页面 URL：{url}
- 功能范围：{feature_points}
- 用例数量及优先级：{例如：选择优先级最高的 3 条用例}
- 其他业务约束：{constraints}

执行要求：
1. plan：使用真实页面完成探索，生成并保存 Markdown 测试计划。
2. generator：读取刚保存的 Markdown，为全部目标用例生成 Playwright .spec.ts 文件。
3. healer：只处理本轮产出的 .spec.ts 文件，逐个运行、定位问题、修复并复测，直到全部通过。
4. 遵守项目内置的移动端 UI 和北大医疗 IM 业务规范。
5. 如果业务步骤、断言语义或用户可见文案发生变化，同步更新关联 Markdown。
6. 最终汇总测试计划文件、生成或修改的 spec 文件、验证范围和执行结果。`,
  },
  {
    id: "plan",
    title: "独立 Plan",
    content: `执行 plan 单阶段任务，为以下页面生成并保存测试计划。

- 自动化工程名称：{project_name}
- 被测页面 URL：{url}
- 重点功能：{feature_points}
- 用例数量：{case_count}
- 优先级要求：{例如：优先覆盖 P0、P1 场景}
- 业务约束：遵守项目内置的移动端 UI 和北大医疗 IM 规范

执行要求：
1. 必须先初始化并真实探索页面，不得根据描述臆造页面元素。
2. 覆盖核心正向路径、关键状态变化、边界条件和高风险异常分支。
3. 每条用例只覆盖一个业务目标，并包含清晰的操作步骤和可观察预期。
4. 将 Markdown 保存到项目规范要求的 aaaplanning 目录。
5. 文件实际落盘后关闭浏览器，最后返回文件路径和用例摘要。`,
  },
  {
    id: "generator",
    title: "独立 Generator",
    content: `执行 generator 单阶段任务，根据指定的 Markdown 用例文档生成 Playwright 自动化代码。

- 自动化工程目录：{project_dir}
- 来源 Markdown 文件或目录：{推荐填写 test_case，或使用本对话最近一次保存的 Markdown 产物}
- 仅处理这些用例名称：{case_names；留空表示全部}
- 业务约束：遵守项目内置的移动端 UI 和北大医疗 IM 规范

执行要求：
1. 按来源文档逐条处理目标用例，不得遗漏。
2. 每个步骤都必须在真实页面中操作并验证，不得只根据文档臆写代码。
3. 北大医疗 IM 固定前置统一调用 IMBaseFlow.openNewConversation(page)。
4. 所有触摸操作使用 tap()，发送消息必须点击发送按钮。
5. 每个 .spec.ts 文件只包含一个 test，名称、describe 和步骤注释与来源文档一致。
6. 全部目标文件实际落盘后关闭浏览器，最后返回产生的文件列表。

注意：当前 Master 使用简单字符串匹配兜底。独立 Generator 请求中不要出现 test_plan_files 或包含 aaaplanning_* 的原始路径，否则其中的 plan 可能被误识别成额外阶段。相同对话优先使用最近的 Markdown 产物；新对话可传 test_case 目录，并通过用例名称缩小范围。`,
  },
  {
    id: "healer",
    title: "独立 Healer",
    content: `执行 healer 单阶段任务，运行并调试以下失败用例文件，逐个修复和复测，直到全部通过。

- 自动化工程目录：{project_dir}
- 待处理的 .spec.ts 文件或目录：{test_scripts}
- 已知错误信息：{error_message；没有可留空}
- 业务约束：遵守项目内置的移动端 UI 和北大医疗 IM 规范

执行要求：
1. 仅处理指定范围，不要默认扩大到整个工程。
2. 先运行目标文件，再对每个问题执行调试和根因分析。
3. 不得跳过原有操作步骤和断言；每次修改后必须重新验证。
4. 优先使用条件等待，不使用 networkidle，尽量避免固定时间等待。
5. 如果业务步骤、断言语义、交互路径或用户可见文案发生变化，在关联 Markdown 中维护同名 [UPDATED] 记录。
6. 最终输出修改过的 spec、关联 Markdown 的同步情况、执行范围和最终结果。`,
  },
] as const;
