import { expect, test } from "@playwright/test";


test.describe("H5 container runtime", () => {
  test("a maintenance gate rejects writes without exposing desktop controls", async ({ page }) => {
    let gatedWriteUrl: string | undefined;

    await page.route("**/api/langgraph/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace(/^\/api\/langgraph/, "") || "/";
      if (path === "/info") {
        await route.fulfill({
          json: { langgraph_py_version: "1.1.9", flags: { assistants: true } },
        });
        return;
      }
      if (path === "/threads/search") {
        await route.fulfill({ json: [] });
        return;
      }
      if (request.method() !== "GET") {
        gatedWriteUrl = request.url();
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "deployment maintenance" }),
        });
        return;
      }
      await route.fulfill({ status: 404, json: { detail: "not mocked" } });
    });

    await page.goto("/");
    await expect(page.getByText("H5", { exact: true })).toBeVisible();
    await expect(page.getByTitle("客户端设置")).toHaveCount(0);
    await expect(page.getByTitle("查看后端日志")).toHaveCount(0);

    const composer = page.getByRole("textbox", { name: "对话输入框" });
    await composer.fill("维护期写请求不应进入 Agent");
    await page.getByTitle("发送").click();

    await expect.poll(() => gatedWriteUrl).toContain("/api/langgraph/");
    await expect(page.getByRole("alert")).toContainText("Agent 执行失败");
    await expect(composer).toHaveValue("维护期写请求不应进入 Agent");
  });
});
