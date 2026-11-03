import { test, expect, type Page } from "@playwright/test";

import { fixture } from "./fixture";

test("phone touch details, collection, deck save and reopen; no practice or rule update entry", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "卡牌资料库" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /人机练习|规则验证/ }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "查看测试宝可梦 CN:TEST:001" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("抽一张牌。")).toBeVisible();
  await page.getByLabel("持有数量").fill("1");
  await page.getByRole("button", { name: "保存此品相", exact: true }).click();
  await expect(page.getByText("此品相已保存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭卡牌详情" }).click();
  await page.getByRole("button", { name: "收藏", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "查看测试宝可梦 CN:TEST:001" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查看测试能量 CN:TEST:002" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建卡组" }).click();
  await page.getByLabel("卡组名称").fill("手机卡组");
  await page.getByRole("button", { name: "保存卡组", exact: true }).click();
  await page.getByRole("button", { name: "去卡库选牌" }).click();
  await page.getByRole("button", { name: "加入卡组测试宝可梦" }).click();
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "保存卡组", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: /手机卡组.*1 张/ }).click();
  await expect(page.getByLabel("CN:TEST:001数量")).toHaveValue("1");
  await expect(page.getByRole("button", { name: "冻结版本" })).toBeEnabled();
  await page.getByRole("button", { name: "冻结版本" }).click();
  await expect(page.getByRole("status")).toContainText(
    "实际准入由对战服务器确认",
  );
  await page.screenshot({ path: "var/p8e-ios/phone-deck.png", fullPage: true });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("iPad landscape layout and source settings stay inside the native bridge", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await fixture(page);
  const remote: string[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith("http://127.0.0.1:5193"))
      remote.push(request.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "卡牌资料库" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查看测试宝可梦 CN:TEST:001" }),
  ).toBeVisible();
  await page.screenshot({
    path: "var/p8e-ios/ipad-library.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("在线图源").selectOption("cdn");
  await page.getByLabel("HTTPS 基础地址").fill("https://images.example.com");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("图片来源已保存");
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(page.getByLabel("HTTPS 基础地址")).toHaveValue(
    "https://images.example.com",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(remote).toEqual([]);
});

test("invalid imports leave saved deck visible and report the domain error", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建卡组" }).click();
  await page.getByText("导入卡组文本", { exact: true }).click();
  await page.getByLabel("导入卡组文本").fill("1 wrong-id");
  await page.getByRole("button", { name: "检查并新建卡组" }).click();
  await expect(page.getByRole("alert")).toContainText("无法识别的卡牌版本");
  await expect(page.getByLabel("卡组名称")).toHaveValue("我的新卡组");
});
