import { test, expect } from "@playwright/test";

test("new collection entry appears without reload or inherited missing filter", async ({ page }) => {
  const pid = "CN:CSVM2cC:007";
  await page.route("https://asia.pokemon-card.com/**", r => r.abort());
  await page.goto("/");
  await page.getByLabel("系列", { exact: true }).selectOption("CSVM2cC");
  await page.getByLabel("搜索卡牌").fill("Gholdengo");
  await page.getByLabel("持有情况").selectOption("missing");
  await page.getByLabel("收藏 " + pid, { exact: true }).click();
  await expect(page.getByLabel("收藏 " + pid, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /我的收藏/ }).click();
  await expect(page.getByLabel("持有情况")).toHaveValue("");
  const row = page.locator(".table-row").filter({ has: page.getByLabel("选择 " + pid, { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row.locator("strong")).toHaveText("1");
  await row.getByRole("button", { name: "管理" }).click();
  await page.getByLabel("收藏数量").fill("3");
  await page.getByRole("button", { name: "保存收藏", exact: true }).click();
  await expect(page.getByText("未标注 3张", { exact: true })).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  await expect(row.locator("strong")).toHaveText("3");
});
