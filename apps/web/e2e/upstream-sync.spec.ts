import { test, expect } from "@playwright/test";

test("sync page exposes isolated preview, failures and battle gate", async ({ page }) => {
  let jobs: any[] = [];
  await page.route("**/api/sync/status", route => route.fulfill({ json: { currentRelease: null, jobs, releases: [] } }));
  await page.route("**/api/sync/jobs", async route => {
    expect(route.request().postDataJSON()).toEqual({ kind: "plan" });
    jobs = [{ id: "fixture", state: "ready", stage: "planned", commit: "a".repeat(40), asOf: "2026-10-07", updatedAt: new Date().toISOString(), proposals: 2, tasks: 1, report: { after: 100, added: 2, variants: 102, conflicts: 1 }, battleReleaseStatus: "pending" }];
    await route.fulfill({ status: 202, json: jobs[0] });
  });
  await page.route("**/api/sync/jobs/fixture/verify-battle", route => route.fulfill({ status: 409, json: { code: "SYNC_BUSY", message: "已有同步任务运行中" } }));
  await page.route("**/api/sync/jobs/fixture/tasks", route => route.fulfill({ json: [{ id: "task", face: { name: "待核验卡" }, cardIds: ["1"] }] }));
  await page.goto("/#sync");
  await expect(page.getByRole("heading", { name: "数据与对战更新", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "迁移预演", exact: true }).click();
  await expect(page.getByText("上游提交 aaaaaaaaaaaa", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "验收并发布对战效果" })).toHaveCount(0);
  await page.getByRole("button", { name: "验证候选对战效果" }).click();
  await expect(page.getByRole("alert")).toContainText("已有同步任务运行中");
  await page.getByRole("button", { name: /查看待实现机制/ }).click();
  await expect(page.getByText("待核验卡 · 1 个版本")).toBeVisible();
});

test("sync status API is available and rejects arbitrary sources", async ({ request }) => {
  const status = await request.get("/api/sync/status");
  expect(status.ok()).toBeTruthy();
  expect((await status.json()).repository).toMatch(/^https:\/\/github\.com\/[^/]+\/[^/]+$/);
  const invalid = await request.post("/api/sync/jobs", { data: { kind: "sync", repository: "https://example.com" } });
  expect(invalid.status()).toBe(422);
});

test("repository setting persists and invalid source is rejected without network", async ({ page, request }) => {
  await page.goto("/#sync");
  const field = page.getByLabel("上游仓库地址", { exact: true });
  await expect(field).not.toHaveValue("");
  await field.fill("https://github.com/example/CompatibleCards.git");
  await page.getByRole("button", { name: "保存仓库地址", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("仓库地址已保存");
  await page.reload();
  await expect(field).toHaveValue("https://github.com/example/CompatibleCards");
  await expect(page.getByRole("link", { name: "原仓库：duanxr/PTCG-CHS-Datasets ↗" })).toBeVisible();
  await field.fill("https://github.com.evil.test/a/b");
  await page.getByRole("button", { name: "保存仓库地址", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("仅支持公开 GitHub 仓库");
  expect((await (await request.get("/api/sync/status")).json()).repository).toBe("https://github.com/example/CompatibleCards");
});
