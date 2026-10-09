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

test("upload, reload and restore card artwork with Chinese rules", async ({ page }) => {
  await page.route("https://asia.pokemon-card.com/**", route => route.abort());
  await page.goto("/");
  await page.getByLabel("搜索卡牌").fill("CN:CSVM2cC:007");
  await page.getByRole("button", { name: "查看 赛富豪ex 007", exact: true }).click();
  const rules = page.getByLabel("中文卡牌说明");
  await expect(rules).toBeVisible();
  await expect(rules).toContainText("嘉奖硬币");
  await expect(rules).toContainText("淘金潮");
  await page.getByLabel("选择卡图文件").setInputFiles({ name: "card.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=", "base64") });
  await expect(page.getByAltText("待上传卡图预览")).toBeVisible();
  await page.getByRole("button", { name: "保存卡图", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复默认卡图" })).toBeVisible();
  await expect(page.locator(".real-image img")).toHaveAttribute("src", /uploaded-card-images/);
  await page.reload();
  await page.getByLabel("卡图状态").selectOption("uploaded");
  await page.getByRole("button", { name: "查看 赛富豪ex 007", exact: true }).click();
  await expect(page.locator(".real-image img")).toHaveAttribute("src", /uploaded-card-images/);
  await page.getByRole("button", { name: "恢复默认卡图" }).click();
  await expect(page.getByRole("button", { name: "恢复默认卡图" })).toHaveCount(0);
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
