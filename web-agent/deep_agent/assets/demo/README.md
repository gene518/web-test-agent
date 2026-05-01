# an-autotest-demo

这是一个最小化的 Playwright 移动端 H5 自动化 demo，用来约定 `plan -> generator -> healer` 产出的工程结构和脚本组织方式。

## 核心约定

1. 工程基于 `@playwright/test`。
2. 所有测试产物统一放在 `test_case/` 下。
3. 尚未生成脚本的计划目录使用 `test_case/aaaplanning_{plan-name}/`。
4. 开始生成脚本后，目录切换为 `test_case/{plan-name}/`，目录内同时保留计划文档和 `.spec.ts`。
5. 计划文档统一命名为 `aaa_{plan-name}.md`。
6. 一个 `.spec.ts` 文件只包含一个测试用例。
7. 公共能力放在 `test_case/shared/`，低频业务操作直接保留在各自 spec 中。
8. 移动端点击操作统一使用 `locator.tap()`，不要在业务用例里直接使用 `click()`。

## 最小生成示例

生成一个计划并落一条脚本后，目录通常如下：

```text
an-autotest-demo/
├── package.json
├── playwright.config.ts
└── test_case/
    ├── shared/
    │   └── base-test.ts
    └── demo_health_consultation/
        ├── aaa_demo_health_consultation.md
        └── a_send_text_message.spec.ts
```

## 使用方式

```bash
npm install
npm run test:headed -- test_case/demo_health_consultation/a_send_text_message.spec.ts
```

## 需要按项目实际调整的地方

- `playwright.config.ts`：按本地运行环境补充设备、超时和浏览器配置。
- `test_case/shared/base-test.ts`：按业务公共前置封装基础能力。
- `test_case/{plan-name}/aaa_{plan-name}.md`：保存测试计划。
- `test_case/{plan-name}/*.spec.ts`：保存按计划生成的测试脚本。
