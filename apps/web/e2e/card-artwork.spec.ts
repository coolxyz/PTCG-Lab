import { test, expect } from "@playwright/test";

test("bottom pagination stays in sync and card details preserve background scroll", async ({ page }) => {
  await page.route("https://asia.pokemon-card.com/**", route => route.abort());
  await page.goto("/");
  const pagination = page.getByLabel("卡牌分页");
  await expect(pagination).toHaveCount(2);
  await pagination.last().getByRole("button", { name: "下一页" }).click();
  await expect(pagination.first()).toContainText("第 2 /");
  await expect(pagination.last()).toContainText("第 2 /");
  await pagination.last().getByRole("button", { name: "上一页" }).click();
  await expect(pagination.first()).toContainText("第 1 /");
  const card = page.locator(".cards-grid button[aria-label^='查看 ']").nth(30);
  await card.scrollIntoViewIfNeeded();
  const scrollY = await page.evaluate(() => window.scrollY);
  expect(scrollY).toBeGreaterThan(300);
  await card.click();
  await expect(page.locator("dialog.modal")).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
  await page.locator("dialog.modal").hover();
  await page.mouse.wheel(0, 800);
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog.modal")).toHaveCount(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
  await card.click();
  await page.getByRole("button", { name: "关闭窗口" }).click();
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
});

test("card details show Chinese rules without retired upload controls", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("搜索卡牌").fill("CN:CSVM2cC:007");
  await page.getByRole("button", {name:"查看 赛富豪ex 007",exact:true}).click();
  await expect(page.getByLabel("中文卡牌说明")).toContainText("淘金潮");
  await expect(page.getByLabel("选择卡图文件")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"恢复默认卡图"})).toHaveCount(0);
});

test("card image filters and Chinese details fit a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("https://asia.pokemon-card.com/**", route => route.abort());
  await page.goto("/");
  await expect(page.getByLabel("卡图状态")).toBeVisible();
  await page.getByLabel("搜索卡牌").fill("CN:CSVM2cC:007");
  await expect(page.getByRole("button", { name: "查看 赛富豪ex 007", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
  await page.getByRole("button", { name: "查看 赛富豪ex 007", exact: true }).click();
  await expect(page.getByLabel("中文卡牌说明")).toContainText("淘金潮");
  await page.getByLabel("中文卡牌说明").scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391);
});
