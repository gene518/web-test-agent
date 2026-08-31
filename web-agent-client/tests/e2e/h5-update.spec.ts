import { expect, test } from "@playwright/test";

// The deployment flag is compiled by Vite, so run this spec with:
// VITE_DEPLOYMENT_MODE=container pnpm test:e2e tests/e2e/h5-update.spec.ts --project=chromium
test.describe("H5 container updates", () => {
  test.skip(
    process.env.VITE_DEPLOYMENT_MODE !== "container",
    "requires a Vite server compiled with VITE_DEPLOYMENT_MODE=container",
  );

  test("checks, applies, and follows an available update without desktop controls", async ({
    page,
  }) => {
    const currentRevision = "1111111111111111111111111111111111111111";
    const latestRevision = "2222222222222222222222222222222222222222";
    const operationId = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const csrfToken = "csrf-e2e-token";
    const updateRequests: string[] = [];
    let statusChecks = 0;
    let operationReads = 0;
    let applyRequest:
      | { method: string; csrfHeader?: string; cookie?: string }
      | undefined;

    await page.route("**/api/langgraph/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname.replace(/^\/api\/langgraph/, "") || "/";
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
      await route.fulfill({ status: 404, json: { detail: "not mocked" } });
    });

    await page.route("**/api/update/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      updateRequests.push(`${request.method()} ${path}`);

      if (path === "/api/update/status") {
        statusChecks += 1;
        await route.fulfill({
          json: {
            current_revision: currentRevision,
            latest_revision: latestRevision,
            has_update: true,
            operation_id: null,
          },
        });
        return;
      }
      if (path === "/api/update/csrf") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: {
            "set-cookie": `web_test_agent_update_csrf=${csrfToken}; Path=/; SameSite=Strict`,
          },
          body: JSON.stringify({ token: csrfToken }),
        });
        return;
      }
      if (path === "/api/update/apply") {
        const headers = request.headers();
        applyRequest = {
          method: request.method(),
          csrfHeader: headers["x-csrf-token"],
          cookie: headers.cookie,
        };
        await route.fulfill({
          status: 202,
          json: {
            operation_id: operationId,
            status: "queued",
            phase: "waiting_for_idle",
            current_revision: currentRevision,
            target_revision: latestRevision,
          },
        });
        return;
      }
      if (path === `/api/update/operations/${operationId}`) {
        operationReads += 1;
        await route.fulfill({
          json: operationReads === 1
            ? {
                operation_id: operationId,
                status: "running",
                phase: "pulling_images",
                current_revision: currentRevision,
                target_revision: latestRevision,
              }
            : {
                operation_id: operationId,
                status: "succeeded",
                phase: "completed",
                current_revision: latestRevision,
                target_revision: latestRevision,
              },
        });
        return;
      }
      await route.fulfill({ status: 404, json: { error: "not mocked" } });
    });

    await page.goto(process.env.H5_UPDATE_E2E_BASE_URL ?? "/");

    await expect.poll(() => statusChecks).toBeGreaterThan(0);
    const updateBadge = page.getByRole("button", { name: "有可用更新" });
    await expect(updateBadge).toBeVisible();
    await expect(updateBadge).toContainText("1111111");
    await expect(page.getByText("H5", { exact: true })).toBeVisible();
    await expect(page.getByTitle("客户端设置")).toHaveCount(0);
    await expect(page.getByTitle("查看后端日志")).toHaveCount(0);

    await updateBadge.click();
    const popover = page.getByRole("dialog", { name: "应用更新" });
    await expect(popover).toBeVisible();
    await expect(popover.locator("code")).toHaveText(["1111111", "2222222"]);

    await popover.getByRole("button", { name: "立即更新" }).click();

    await expect.poll(() => applyRequest).toEqual({
      method: "POST",
      csrfHeader: csrfToken,
      cookie: expect.stringContaining(`web_test_agent_update_csrf=${csrfToken}`),
    });
    expect(updateRequests.indexOf("GET /api/update/csrf")).toBeLessThan(
      updateRequests.indexOf("POST /api/update/apply"),
    );
    await expect(popover.getByRole("status")).toContainText("正在下载更新");
    await expect(popover.getByRole("status")).toContainText("更新完成，正在重新连接");
    await expect.poll(() => operationReads).toBeGreaterThanOrEqual(2);
  });
});
