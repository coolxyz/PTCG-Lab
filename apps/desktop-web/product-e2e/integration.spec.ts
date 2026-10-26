import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve, basename } from "node:path";

test.beforeEach(async ({ page }) => {
  await page.route("https://asia.pokemon-card.com/**", (r) => r.abort());
});

test("product navigation retains practice and refreshes saved construction versions", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#battle");
  await expect(page.getByLabel("AI 策略", { exact: true })).toHaveValue("H2");
  // Own the second revision fixture; do not depend on another test's database writes.
  const bootstrap = await (
    await page.request.post("/api/battle/session", { data: {} })
  ).json();
  const seeded = await page.request.post(
    `/api/battle/presets/${bootstrap.presets[0].id}/revision`,
    { data: {} },
  );
  expect(seeded.ok()).toBe(true);
  const mutations: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/api\/battle\/matches(?:$|\/)/.test(r.url()))
      mutations.push(r.url());
  });
  await page.getByRole("button", { name: "前往构筑并保存版本" }).click();
  await page.locator(".template").filter({ hasText: "赛富豪ex" }).click();
  await page.getByLabel("卡组名称").fill("P3-C 构筑导航");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  await page.getByRole("button", { name: "保存卡组版本", exact: true }).click();
  await page
    .getByRole("button", { name: "使用此版本人机练习", exact: true })
    .click();
  await expect(
    page.getByLabel("对战卡组版本").locator("option:checked"),
  ).toContainText("P3-C 构筑导航");
  const revision = await page.getByLabel("对战卡组版本").inputValue();
  const other = await page
    .getByLabel("对战卡组版本")
    .locator("option")
    .evaluateAll(
      (options, selected) =>
        options
          .map((o) => o as HTMLOptionElement)
          .find((o) => o.value && o.value !== selected && !o.disabled)?.value,
      revision,
    );
  expect(other).toBeTruthy();
  await page.getByLabel("对战卡组版本").selectOption(other!);
  await page.getByRole("button", { name: "前往构筑并保存版本" }).click();
  await page.getByRole("button", { name: "版本记录", exact: true }).click();
  await page
    .getByRole("button", { name: "使用此版本人机练习", exact: true })
    .click();
  await expect(page.getByLabel("对战卡组版本")).toHaveValue(revision);
  expect(mutations).toHaveLength(0);
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.getByRole("button", { name: "暂停 AI", exact: true }).click();
  const id = await page.evaluate(() =>
    localStorage.getItem("ptcg-p3-practice:match"),
  );
  const before = await (
    await page.request.get(`/api/battle/matches/${id}`)
  ).json();
  mutations.length = 0;
  await page.evaluate(() => {
    location.hash = "collection";
  });
  await expect(page.locator(".sidebar nav .active")).toContainText("我的收藏");
  await expect(page.getByLabel("我的场面")).toHaveCount(0);
  await page.getByRole("button", { name: /数据与对战更新/ }).click();
  await expect(
    page.getByRole("heading", { name: "数据与对战更新", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await expect(page.locator(".sidebar nav .active")).toContainText("我的收藏");
  await page.evaluate(() => {
    location.hash = "practice";
  });
  await expect(page.getByLabel("我的场面")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "继续 AI", exact: true }),
  ).toBeVisible();
  const after = await (
    await page.request.get(`/api/battle/matches/${id}`)
  ).json();
  expect(after.stateVersion).toBe(before.stateVersion);
  expect(mutations).toHaveLength(0);
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await page.screenshot({
    path: resolve(import.meta.dirname, "../../../var/p3c/product-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: resolve(import.meta.dirname, "../../../var/p3c/product-narrow.png"),
  });
  expect(errors).toEqual([]);
});

test("same-origin legacy pointer restores and both GUI builds can render the same match", async ({
  page,
}) => {
  await page.goto("/#practice");
  const select = page.getByLabel("我的预设卡组", { exact: true });
  await expect(select.locator("option")).toHaveCount(6);
  await select.selectOption(
    (await select.locator("option").nth(1).getAttribute("value")) as string,
  );
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  // Terminal match makes rollback a display test without legacy AI mutations.
  await page.getByRole("button", { name: "认输", exact: true }).click();
  await page.getByRole("button", { name: "确认认输", exact: true }).click();
  await expect(page.locator(".battle-result")).toBeVisible();
  const id = await page.evaluate(() => {
    const id = localStorage.getItem("ptcg-match");
    localStorage.removeItem("ptcg-p3-practice:match");
    return id;
  });
  const before = await (
    await page.request.get(`/api/battle/matches/${id}`)
  ).json();
  await page.goto("/#battle");
  await page.reload();
  await expect(page.locator(".battle-result")).toBeVisible();
  expect(
    await page.evaluate(() => localStorage.getItem("ptcg-p3-practice:match")),
  ).toBe(id);
  const legacy = resolve(
    process.env.PTCG_PRACTICE_TEST_STAGE!,
    "frontend-legacy",
  );
  await page.route("**/assets/*", async (r) => {
    const file = basename(new URL(r.request().url()).pathname);
    await r.fulfill({
      path: resolve(legacy, "assets", file),
      contentType: file.endsWith(".js") ? "application/javascript" : "text/css",
    });
  });
  await page.route("http://127.0.0.1:8783/", async (r) => {
    await r.fulfill({
      contentType: "text/html",
      body: await readFile(resolve(legacy, "index.html"), "utf8"),
    });
  });
  await page.reload();
  await expect(page.locator(".battle-result")).toBeVisible();
  // Only the new renderer portals an independent battle screen outside the shell.
  await expect(page.locator(".battle-screen")).toHaveCount(0);
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.unroute("**/assets/*");
  await page.unroute("http://127.0.0.1:8783/");
  await page.reload();
  await expect(page.locator(".battle-screen")).toBeVisible();
  await expect(page.getByLabel("当前 AI 策略")).toBeVisible();
  await expect(page.locator(".battle-result")).toBeVisible();
  const after = await (
    await page.request.get(`/api/battle/matches/${id}`)
  ).json();
  expect(after).toEqual(before);
});
