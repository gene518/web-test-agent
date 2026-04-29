# an-autotest-demo

这是一个按照 `an-autotest` 项目规范整理出的 Playwright 移动端 H5 自动化 demo 工程。

## 我按原项目提炼出的核心标准

1. **工程类型**：基于 `@playwright/test` 的移动端 H5 自动化项目。
2. **用例根目录**：所有测试实现放在 `test_case/`。
3. **计划生命周期**：
   - `test_case/aaaplanning_xxx/`：仅有计划文档，尚未生成脚本。
   - `test_case/xxx/`：计划已开始实现，目录内包含 `aaa_xxx.md` 和 `.spec.ts`。
4. **计划文档命名**：统一使用 `aaa_{plan-name}.md`，确保在目录中排序靠前。
5. **移动端交互**：业务点击统一使用 `locator.tap()`，禁止在用例里直接使用 `click()`。
6. **前置复用**：登录、进入 IM、打开新对话、基础页面断言统一收敛到 `test_case/shared/im-base.ts`。
7. **低频动作不封装**：不到 5 个以上用例复用的操作，保留在对应 spec 中。
8. **每文件单用例**：一个 `.spec.ts` 只包含一个 `test()`。
9. **提示词保持默认**：`.github/skills/beida_medical_ui_test/` 中保留 Plan / Generator / Heal 三类默认指令与两层规范。

## 目录结构

```text
an-autotest-demo/
├── .github/skills/beida_medical_ui_test/
│   ├── SKILL.md
│   └── references/
│       ├── playwright-test-planner-agent.md
│       ├── playwright-test-generator-agent.md
│       ├── playwright-test-healer-agent.md
│       ├── mobile-ui-conventions.md
│       └── beida-medical-conventions.md
├── test_case/
│   ├── shared/
│   │   ├── base-test.ts
│   │   └── im-base.ts
│   ├── welcome_message/
│   │   └── welcome_message.spec.ts
│   ├── demo_health_consultation/
│   │   ├── aaa_demo_health_consultation.md
│   │   ├── a_send_text_message.spec.ts
│   │   └── b_profile_tab_load.spec.ts
│   └── aaaplanning_demo_sidebar/
│       └── aaa_demo_sidebar.md
├── test_data/
│   └── .gitkeep
├── .env.example
├── package.json
└── playwright.config.ts
```

## 使用方式

```bash
npm install
cp .env.example .env
# 修改 .env 中的 IM_LOGIN_URL 和本地 Chrome 路径
npm run test:headed -- test_case/demo_health_consultation/a_send_text_message.spec.ts
```

## 你需要替换的位置

- `IM_LOGIN_URL`：放真实登录跳转 URL，不建议把账号、验证码直接提交到仓库。
- `test_case/shared/im-base.ts`：如果登录方式、页面标题、输入框、发送按钮、新对话按钮定位发生变化，只改这里。
- `references/beida-medical-conventions.md`：如果要迁移到其他业务，把这里换成新业务规范即可，通用移动端规范不用动。
