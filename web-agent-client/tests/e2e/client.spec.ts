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

test("historical details stay visible while the latest checkpoint hydrates", async ({ page }) => {
  let stateRequests = 0;
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
      await route.fulfill({
        headers,
        json: [
          {
            thread_id: threadId,
            created_at: "2026-07-17T08:00:00Z",
            updated_at: "2026-07-17T08:01:00Z",
            metadata: { graph_id: "web-autotest-agent", thread_title: "历史详情回归" },
            status: "idle",
            values: {
              display_messages: [
                { id: "human-history", type: "human", content: "历史问题正文" },
                { id: "ai-history", type: "ai", content: "历史回答正文" },
              ],
            },
            interrupts: {},
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
          values: {},
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

  await expect(page.getByText("历史问题正文", { exact: true })).toBeVisible();
  await expect(page.getByText("历史回答正文", { exact: true })).toBeVisible();
  await expect.poll(() => stateRequests).toBe(1);
  await expect(page.getByText("历史回答正文", { exact: true })).toBeVisible();
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
