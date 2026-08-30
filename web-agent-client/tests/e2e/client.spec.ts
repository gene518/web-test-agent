import { expect, test } from "@playwright/test";
import { PROMPT_TEMPLATES } from "../../src/lib/prompt-templates";

const historyTest = process.env.E2E_REAL_BACKEND === "1" ? test : test.skip;

test("four prompt shortcuts stay on one row and fill the complete prompt", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(globalThis, { isTauri: true });
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {
        invoke: async (command: string) => {
          if (command === "backend_status") {
            return {
              state: "running",
              apiUrl: "http://127.0.0.1:2024",
              projectRoot: "",
              message: "UI 验证模式",
            };
          }
          throw new Error(`未模拟 Tauri 命令：${command}`);
        },
        transformCallback: () => 0,
        unregisterCallback: () => undefined,
        convertFileSrc: (path: string) => path,
      },
    });
  });
  await page.goto("/");

  await expect(page.getByText("网页测试智能体专注自动化，可探索真实页面、规划测试用例、生成脚本，并自动调试修复失败，完成测试闭环。", { exact: true })).toBeVisible();
  const shortcuts = page.locator(".prompt-examples button");
  await expect(shortcuts).toHaveCount(4);
  await expect(shortcuts).toHaveText(PROMPT_TEMPLATES.map((template) => template.title));

  const boxes = await shortcuts.evaluateAll((buttons) =>
    buttons.map((button) => {
      const box = button.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height };
    }),
  );
  expect(new Set(boxes.map((box) => Math.round(box.y))).size).toBe(1);
  expect(boxes.every((box) => box.width > 100 && box.height === 42)).toBe(true);

  const composer = page.getByRole("textbox", { name: "对话输入框" });
  for (const template of PROMPT_TEMPLATES) {
    await page.getByRole("button", { name: template.title, exact: true }).click();
    await expect(composer).toHaveValue(template.content);
  }

  await composer.fill("第一行");
  const oneLineHeight = (await composer.boundingBox())?.height ?? 0;
  await composer.fill("第一行\n第二行\n第三行");
  const threeLineHeight = (await composer.boundingBox())?.height ?? 0;
  await composer.fill("第一行\n第二行\n第三行\n第四行\n第五行\n第六行\n第七行");
  const overflowState = await composer.evaluate((element) => {
    const textarea = element as HTMLTextAreaElement;
    return {
      height: textarea.getBoundingClientRect().height,
      clientHeight: textarea.clientHeight,
      scrollHeight: textarea.scrollHeight,
      overflowY: getComputedStyle(textarea).overflowY,
    };
  });
  expect(threeLineHeight).toBeGreaterThan(oneLineHeight);
  expect(overflowState.height).toBeGreaterThan(threeLineHeight);
  expect(overflowState.scrollHeight).toBeGreaterThan(overflowState.clientHeight);
  expect(overflowState.overflowY).toBe("auto");

  await page.getByRole("button", { name: "plan+generator+healer", exact: true }).click();
  await expect.poll(() => composer.evaluate((element) => (element as HTMLTextAreaElement).scrollTop)).toBe(0);
});

test("ANSI backend logs render with selectable persistent themes", async ({ page }) => {
  const rawLog = "\u001b[32m[info]\u001b[0m UI ANSI 颜色验证";

  await page.addInitScript((log) => {
    Object.assign(globalThis, { isTauri: true });
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {
        invoke: async (command: string) => {
          if (command === "backend_status") {
            return {
              state: "error",
              apiUrl: "http://127.0.0.1:2024",
              projectRoot: "",
              message: "UI 验证模式",
            };
          }
          if (command === "backend_log") return log;
          throw new Error(`未模拟 Tauri 命令：${command}`);
        },
        transformCallback: () => 0,
        unregisterCallback: () => undefined,
        convertFileSrc: (path: string) => path,
      },
    });
  }, rawLog);

  await page.goto("/");
  await page.getByTitle("查看后端日志").click();

  const viewer = page.getByRole("log", { name: "后端日志内容" });
  await expect(viewer).toBeVisible();
  await expect(viewer).not.toContainText("\u001b[");
  await expect(viewer.locator(".ansi-green-fg").first()).toBeVisible();

  const theme = page.getByRole("combobox", { name: "日志颜色主题" });
  const macColor = await viewer.locator(".ansi-green-fg").first().evaluate(
    (element) => getComputedStyle(element).color,
  );
  await theme.selectOption("light");
  const lightColor = await viewer.locator(".ansi-green-fg").first().evaluate(
    (element) => getComputedStyle(element).color,
  );
  expect(lightColor).not.toBe(macColor);
  expect(await page.evaluate(() => localStorage.getItem("web-test-agent.log-theme.v1"))).toBe("light");

  await theme.selectOption("macos");
});

test("browser preview rejects a non-LangGraph service on the configured port", async ({ page }) => {
  await page.route("http://127.0.0.1:2024/info", async (route) => {
    await route.fulfill({
      json: { status: "ok", flags: {} },
      headers: { "access-control-allow-origin": "*" },
    });
  });

  await page.goto("/");

  await expect(page.getByText("端口冲突", { exact: true })).toBeVisible();
  await expect(page.getByText("端口 2024 上的服务不是 LangGraph 后端。", { exact: true })).toBeVisible();
});

test("settings edits stay in a draft until a successful save", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "web-test-agent.client-config.v1",
      JSON.stringify({ projectRoot: "/repo", backendPort: 2024 }),
    );
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {
        invoke: async (command: string) => {
          if (command === "backend_status" || command === "restart_backend") {
            return {
              state: "running",
              apiUrl: "http://127.0.0.1:2024",
              projectRoot: "/repo",
              message: "UI 验证模式",
            };
          }
          throw new Error(`未模拟 Tauri 命令：${command}`);
        },
        transformCallback: () => 0,
        unregisterCallback: () => undefined,
        convertFileSrc: (path: string) => path,
      },
    });
  });

  await page.goto("/");
  await page.getByTitle("客户端设置").click();

  const port = page.getByLabel("后端端口");
  const save = page.getByRole("button", { name: "保存并重启" });
  await expect(port).toHaveValue("2024");
  await port.fill("70000");
  await expect(page.getByText("端口必须是 1024 到 65535 之间的整数")).toBeVisible();
  await expect(save).toBeDisabled();

  await port.fill("3030");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByTitle("客户端设置").click();
  await expect(page.getByLabel("后端端口")).toHaveValue("2024");
  await expect
    .poll(() =>
      page.evaluate(() => localStorage.getItem("web-test-agent.client-config.v1")),
    )
    .toBe(JSON.stringify({ projectRoot: "/repo", backendPort: 2024 }));
});

test("history titles fall back to messages and hydrate the selected conversation on demand", async ({ page }) => {
  let stateRequests = 0;
  let searchSelect: string[] = [];
  const threadId = "history-detail-regression";
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      searchSelect = (route.request().postDataJSON() as { select?: string[] }).select ?? [];
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: threadId,
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent" },
            status: "idle",
            values: {
              messages: [{ id: "history-title", type: "human", content: "历史详情回归" }],
            },
          },
        ],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/state`) {
      stateRequests += 1;
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        headers,
        json: {
          values: {
            display_messages: [
              { id: "human-history", type: "human", content: "历史问题正文" },
              { id: "ai-history", type: "ai", content: "历史回答正文" },
            ],
          },
          next: [],
          tasks: [],
          checkpoint: { thread_id: threadId, checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs`) {
      await route.fulfill({ headers, json: [] });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /历史详情回归/ }).click();

  await expect(page.getByLabel("正在加载对话")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "对话输入框" })).toBeDisabled();
  await expect(page.getByText("历史问题正文", { exact: true })).toBeVisible();
  await expect(page.getByText("历史回答正文", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "对话输入框" })).toBeEnabled();
  await expect.poll(() => stateRequests).toBe(1);
  expect(searchSelect).toContain("values");
  expect(searchSelect).not.toContain("interrupts");
  await expect(page.getByText("历史回答正文", { exact: true })).toBeVisible();
});

test("Healer validation targets are clickable and browser preview explains the limitation", async ({
  page,
}) => {
  const threadId = "artifact-path-links";
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };
  const validationTarget = "test_case/login/a_login.validation.spec.ts";
  const summary = `**Healer 阶段**
- 状态：成功
- 项目目录：\`/repo/web-agent/demo\`
- 调试目标脚本：共 1 个，\`test_case/login/a_login.spec.ts\`
- 实际变更文件：共 1 个，\`test_case/login/a_login.spec.ts\`
- 验证运行目标：共 1 个，\`${validationTarget}\`
- 脚本明细：共 1 条
- 调试对象 1：\`test_case/login/a_login.spec.ts\`，覆盖标题 \`登录流程\`
- 下一阶段建议输入：如需继续复测或追加修复，可继续提供 \`test_case/login/a_login.spec.ts\`。`;

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: threadId,
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "产物链接验证" },
            status: "idle",
            values: { display_messages: [{ id: "summary", type: "ai", content: summary }] },
          },
        ],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/state`) {
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: [{ id: "summary", type: "ai", content: summary }] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: threadId, checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs`) {
      await route.fulfill({ headers, json: [] });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /产物链接验证/ }).click();

  const links = page.locator(".artifact-path-link");
  await expect(links).toHaveCount(5);
  const validationTargetLink = page.getByRole("button", {
    name: `在文件管理器中打开 ${validationTarget}`,
  });
  await expect(validationTargetLink).toBeVisible();

  await validationTargetLink.click();
  await expect(page.getByRole("alert")).toContainText(
    "浏览器预览模式无法打开本地路径，请在桌面客户端中使用此功能。",
  );
});

test("a partial cancellation failure remains visible and keeps the run active", async ({ page }) => {
  const threadId = "cancel-failure-thread";
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };
  const cancellationTargets: string[] = [];

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: threadId,
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "取消失败回归" },
            status: "busy",
          },
        ],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/state`) {
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: [] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: threadId, checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs` && request.method() === "GET") {
      await route.fulfill({
        headers,
        json: url.searchParams.get("status") === "running"
          ? [{ run_id: "run-ok" }, { run_id: "run-fail" }]
          : [],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs/run-ok/stream`) {
      await route.fulfill({
        headers: {
          "access-control-allow-origin": "*",
          "content-type": "text/event-stream",
        },
        body: "",
      });
      return;
    }
    if (url.pathname.endsWith("/cancel") && request.method() === "POST") {
      cancellationTargets.push(url.pathname);
      if (url.pathname.includes("run-fail")) {
        await route.fulfill({ status: 500, headers, json: { detail: "取消被拒绝" } });
      } else {
        await route.fulfill({ headers, json: null });
      }
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /取消失败回归/ }).click();
  const cancel = page.getByRole("button", { name: "取消任务" });
  await expect(cancel).toBeVisible();
  await cancel.click();

  await expect(page.getByRole("alert")).toContainText("1/2 个运行取消失败");
  await expect(cancel).toBeVisible();
  expect([...new Set(cancellationTargets)].sort()).toEqual([
    `/threads/${threadId}/runs/run-fail/cancel`,
    `/threads/${threadId}/runs/run-ok/cancel`,
  ]);
});

test("a failed stream rejoin is retried and clears its recovery notice", async ({ page }) => {
  const threadId = "reconnect-retry-thread";
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };
  let joinAttempts = 0;

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: threadId,
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "恢复重试回归" },
            status: "busy",
          },
        ],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/state`) {
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: [] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: threadId, checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs` && request.method() === "GET") {
      await route.fulfill({
        headers,
        json: url.searchParams.get("status") === "running" ? [{ run_id: "run-retry" }] : [],
      });
      return;
    }
    if (url.pathname === `/threads/${threadId}/runs/run-retry/stream`) {
      joinAttempts += 1;
      if (joinAttempts <= 2) {
        await route.fulfill({ status: 500, headers, json: { detail: "temporary failure" } });
      } else {
        await route.fulfill({
          headers: {
            "access-control-allow-origin": "*",
            "content-type": "text/event-stream",
          },
          body: "",
        });
      }
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /恢复重试回归/ }).click();

  await expect(page.getByRole("alert")).toContainText("恢复执行流失败，将自动重试", {
    timeout: 10_000,
  });
  await expect.poll(() => joinAttempts, { timeout: 15_000 }).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a late error from thread A does not leak into selected thread B", async ({ page }) => {
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };
  let joinStarted = false;

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: "thread-a",
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:02:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "会话 A" },
            status: "busy",
          },
          {
            thread_id: "thread-b",
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "会话 B" },
            status: "idle",
          },
        ],
      });
      return;
    }
    if (/\/threads\/thread-[ab]\/state$/.test(url.pathname)) {
      const selectedId = url.pathname.includes("thread-a") ? "thread-a" : "thread-b";
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: [] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: selectedId, checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === "/threads/thread-a/runs" && request.method() === "GET") {
      await route.fulfill({
        headers,
        json: url.searchParams.get("status") === "running" ? [{ run_id: "run-a" }] : [],
      });
      return;
    }
    if (url.pathname === "/threads/thread-b/runs" && request.method() === "GET") {
      await route.fulfill({ headers, json: [] });
      return;
    }
    if (url.pathname === "/threads/thread-a/runs/run-a/stream") {
      joinStarted = true;
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({ status: 500, headers, json: { detail: "late A failure" } });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /会话 A/ }).click();
  await expect.poll(() => joinStarted).toBe(true);
  await page.getByRole("button", { name: /会话 B/ }).click();

  await expect(page.locator(".conversation-heading strong")).toHaveText("会话 B");
  await expect(page.getByRole("button", { name: "取消任务" })).toHaveCount(0);
  await page.waitForTimeout(1_000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".conversation-heading span")).toHaveText("Web 自动化测试 Agent");
});

test("a late new-thread submit failure does not pull thread B back into thread A", async ({ page }) => {
  const headers = {
    "access-control-allow-origin": "*",
    "content-type": "application/json",
  };
  let threadCreateStarted = false;

  await page.route("http://127.0.0.1:2024/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (url.pathname === "/threads/search") {
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: "thread-b",
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "提交会话 B" },
            status: "idle",
          },
        ],
      });
      return;
    }
    if (url.pathname === "/threads/thread-b/state") {
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: [] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: "thread-b", checkpoint_ns: "", checkpoint_id: "checkpoint-1" },
          metadata: {},
          created_at: "2026-07-17T08:01:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (url.pathname === "/threads/thread-b/runs" && request.method() === "GET") {
      await route.fulfill({ headers, json: [] });
      return;
    }
    if (url.pathname === "/threads" && request.method() === "POST") {
      threadCreateStarted = true;
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({ headers, json: { thread_id: "thread-a" } });
      return;
    }
    if (url.pathname === "/threads/thread-a/runs/stream" && request.method() === "POST") {
      await route.fulfill({ status: 400, headers, json: { detail: "late A submit failure" } });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "对话输入框" });
  await composer.fill("只属于新会话 A 的失败输入");
  await page.getByTitle("发送").click();
  await expect.poll(() => threadCreateStarted).toBe(true);
  await page.getByRole("button", { name: /提交会话 B/ }).click();

  await expect(page.locator(".conversation-heading strong")).toHaveText("提交会话 B");
  await page.waitForTimeout(1_200);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(composer).toHaveValue("");
  await expect(page.locator(".conversation-heading span")).toHaveText("Web 自动化测试 Agent");
});

historyTest("a selected historical conversation can continue", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /工具展示开关验证/ }).click();
  await expect(page.locator(".conversation-heading strong")).toHaveText("工具展示开关验证");

  const verificationToken = `UI 历史续聊验证 ${Date.now()}`;
  const composer = page.getByRole("textbox", { name: "对话输入框" });
  await composer.fill(`请只回复“${verificationToken}”，不要调用工具。`);
  await page.getByTitle("发送").click();

  await expect(page.locator(".timeline-ai .message-content").filter({ hasText: verificationToken })).toBeVisible({
    timeout: 180_000,
  });
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".conversation-heading span")).toHaveText("Web 自动化测试 Agent", {
    timeout: 30_000,
  });
  await expect(page.getByRole("button", { name: "取消任务" })).toHaveCount(0);
  await expect(page.locator(".thread-item.selected .thread-copy span")).not.toHaveText("正在运行", {
    timeout: 30_000,
  });
});
