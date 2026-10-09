import { test, expect } from "@playwright/test";

test("delete a deck: cancel, confirm and refresh", async ({ page, request }) => {
  const name = "删除流程测试";
  const created = await request.post("/api/decks", { data: { name, entries: [] } });
  const deck = await created.json();
  await page.route("https://asia.pokemon-card.com/**", r => r.abort());
  await page.goto("/");
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  const remove = page.getByRole("button", { name: "删除卡组：" + name, exact: true });
  await expect(remove).toBeVisible();
  page.once("dialog", d => d.dismiss());
  await remove.click();
  await expect(remove).toBeVisible();
  expect((await request.get(`/api/decks/${deck.id}`)).status()).toBe(200);
  page.once("dialog", d => d.accept());
  await remove.click();
  await expect(remove).toHaveCount(0);
  await expect(page.getByText("卡组已删除", { exact: true })).toBeVisible();
  expect((await request.get(`/api/decks/${deck.id}`)).status()).toBe(404);
  await page.reload();
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  await expect(remove).toHaveCount(0);
});

test("delete revision: cancel, confirm, then restore same card list", async ({ page }) => {
  await page.route("https://asia.pokemon-card.com/**", r => r.abort());
  await page.goto("/");
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  await page.locator(".template").filter({ hasText: "赛富豪ex" }).click();
  await page.getByRole("button", { name: "保存卡组版本", exact: true }).click();
  const remove = page.getByRole("button", { name: "删除版本 1", exact: true });
  await expect(remove).toBeVisible();
  page.once("dialog", d => d.dismiss());
  await remove.click();
  await expect(remove).toBeVisible();
  page.once("dialog", d => d.accept());
  await remove.click();
  await expect(remove).toHaveCount(0);
  await expect(page.getByText("版本已删除，历史对局已保留", { exact: true })).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  await page.getByRole("button", { name: "保存卡组版本", exact: true }).click();
  await expect(remove).toBeVisible();
  await expect(page.getByRole("button", { name: "删除版本 2", exact: true })).toHaveCount(0);
});
