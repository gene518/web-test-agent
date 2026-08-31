import { expect, test, type Locator, type Page } from "@playwright/test";

const headers = {
  "access-control-allow-origin": "*",
  "content-type": "application/json",
};

const threads = [
  {
    thread_id: "timeline-position-a",
    created_at: "2026-08-31T09:00:00Z",
    updated_at: "2026-08-31T09:02:00Z",
    metadata: { graph_id: "web-autotest-agent" },
    status: "idle",
    extracted: { thread_title: "历史阅读位置 A" },
  },
  {
    thread_id: "timeline-position-b",
    created_at: "2026-08-31T08:00:00Z",
    updated_at: "2026-08-31T08:02:00Z",
    metadata: { graph_id: "web-autotest-agent" },
    status: "idle",
    extracted: { thread_title: "历史阅读位置 B" },
  },
] as const;

function apiPath(url: URL): string {
  return url.pathname.replace(/^\/api\/langgraph/, "") || "/";
}

function messagesFor(label: "A" | "B") {
  return Array.from({ length: 40 }, (_, index) => [
    {
      id: `timeline-${label.toLowerCase()}-human-${index}`,
      type: "human",
      content: `${label} 历史问题 ${index}：用于验证切换会话后的阅读位置恢复。`,
    },
    {
      id: `timeline-${label.toLowerCase()}-ai-${index}`,
      type: "ai",
      content: `${label} 历史回答 ${index}：用于验证切换会话后的阅读位置恢复。`,
    },
  ]).flat();
}

async function firstVisibleTurnKey(viewport: Locator): Promise<string | undefined> {
  return viewport.evaluate((element) => {
    const viewportTop = element.getBoundingClientRect().top;
    return Array.from(element.querySelectorAll<HTMLElement>("[data-turn-key]"))
      .find((turn) => turn.getBoundingClientRect().bottom > viewportTop)
      ?.dataset.turnKey;
  });
}

async function distanceFromLatest(viewport: Locator): Promise<number> {
  return viewport.evaluate((element) => (
    element.scrollHeight - element.clientHeight - element.scrollTop
  ));
}

async function detachAndRememberAnchor(page: Page, viewport: Locator): Promise<string> {
  await viewport.hover();
  await page.mouse.wheel(0, -620);
  await expect.poll(() => distanceFromLatest(viewport)).toBeGreaterThan(24);
  const anchor = await firstVisibleTurnKey(viewport);
  expect(anchor).toBeTruthy();
  return anchor!;
}

test("historical A/B timelines restore their detached reading positions and honor reduced motion", async ({
  page,
}) => {
  const messages = {
    "timeline-position-a": messagesFor("A"),
    "timeline-position-b": messagesFor("B"),
  };

  await page.route("**/api/langgraph/**", async (route) => {
    const request = route.request();
    const path = apiPath(new URL(request.url()));
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (path === "/info") {
      await route.fulfill({
        headers,
        json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
      });
      return;
    }
    if (path === "/threads/search") {
      await route.fulfill({ headers, json: threads });
      return;
    }
    const stateMatch = path.match(/^\/threads\/(timeline-position-[ab])\/state$/);
    if (stateMatch) {
      const threadId = stateMatch[1] as keyof typeof messages;
      await route.fulfill({
        headers,
        json: {
          values: { display_messages: messages[threadId] },
          next: [],
          tasks: [],
          checkpoint: { thread_id: threadId, checkpoint_ns: "", checkpoint_id: `checkpoint-${threadId}` },
          metadata: {},
          created_at: "2026-08-31T09:02:00Z",
          parent_checkpoint: null,
        },
      });
      return;
    }
    if (/^\/threads\/timeline-position-[ab]\/runs$/.test(path)) {
      await route.fulfill({ headers, json: [] });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { detail: "not mocked" } });
  });

  await page.goto("/");

  await page.getByRole("button", { name: /历史阅读位置 A/ }).click();
  await expect(page.getByText("A 历史回答 39：用于验证切换会话后的阅读位置恢复。", { exact: true })).toBeVisible();
  const aViewport = page.getByLabel("对话消息");
  await expect(page.locator(".timeline-turn")).toHaveCount(20);
  const aAnchor = await detachAndRememberAnchor(page, aViewport);

  await page.getByRole("button", { name: /历史阅读位置 B/ }).click();
  await expect(page.getByText("B 历史回答 39：用于验证切换会话后的阅读位置恢复。", { exact: true })).toBeVisible();
  const bViewport = page.getByLabel("对话消息");
  await expect(page.locator(".timeline-turn")).toHaveCount(20);
  const bAnchor = await detachAndRememberAnchor(page, bViewport);

  await page.getByRole("button", { name: /历史阅读位置 A/ }).click();
  await expect.poll(() => firstVisibleTurnKey(aViewport)).toBe(aAnchor);
  await expect.poll(() => distanceFromLatest(aViewport)).toBeGreaterThan(24);

  await page.getByRole("button", { name: /历史阅读位置 B/ }).click();
  await expect.poll(() => firstVisibleTurnKey(bViewport)).toBe(bAnchor);
  await expect.poll(() => distanceFromLatest(bViewport)).toBeGreaterThan(24);

  await page.emulateMedia({ reducedMotion: "reduce" });
  const backToLatest = page.getByRole("button", { name: "回到最新消息", exact: true });
  await expect(backToLatest).toBeVisible();
  await backToLatest.click();
  expect(await distanceFromLatest(bViewport)).toBeLessThanOrEqual(1);
});
