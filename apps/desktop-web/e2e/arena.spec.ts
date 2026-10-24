import { test, expect, type Page } from "@playwright/test";
async function open(page: Page, scenario = "options", seat = "PLAYER1") {
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "座位", exact: true })
    .selectOption(seat);
  await page
    .getByRole("combobox", { name: "演示场景", exact: true })
    .selectOption(scenario);
  await page.getByRole("button", { name: "打开对战界面", exact: true }).click();
  await expect(page.getByRole("main", { name: "对战与回放" })).toBeVisible();
}
for (const seat of ["PLAYER1", "PLAYER2"]) {
  test(`${seat}: standalone window, missing artwork and command callback`, async ({
    page,
  }) => {
    const calls: string[] = [];
    page.on("request", (r) => {
      if (/\/api\/|wss?:/.test(r.url())) calls.push(r.url());
    });
    await open(page, "non-turn", seat);
    await expect(page.getByText("合成演示 · 尚未连接服务器")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "结束回合", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByRole("button", { name: /AI|单步|暂停AI/ }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "结束回合", exact: true }).click();
    await page.getByRole("button", { name: "返回大厅", exact: true }).click();
    await expect(page.getByLabel("演示选择回调")).toHaveText(
      '{"optionId":"demo-pass"}',
    );
    await expect(page.locator("body")).not.toHaveClass(/battle-screen-open/);
    await page
      .getByRole("button", { name: "打开对战界面", exact: true })
      .click();
    await expect(
      page.getByText("对战中 · 回合 2 · 第 1 步", { exact: true }),
    ).toBeVisible();
    expect(calls).toEqual([]);
  });
}
test("search selection persists across browsing, refresh and resize", async ({
  page,
}) => {
  await open(page, "selection");
  const panel = page.getByRole("region", { name: "当前决策" });
  await panel.locator(".decision-candidates button").first().click();
  await page.getByText("浏览我方完整牌库 · 2 张", { exact: true }).click();
  await page.getByRole("searchbox", { name: "查找卡牌" }).fill("不存在");
  await expect(page.getByText("没有符合筛选条件的卡牌。")).toBeVisible();
  await page.getByRole("button", { name: "刷新局面", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    panel.getByRole("button", { name: "确认选择 1 张", exact: true }),
  ).toBeEnabled();
  await expect(
    panel.locator(".decision-candidates button").first(),
  ).toHaveAttribute("aria-pressed", "true");
  const width = await page
    .locator(".battle-screen")
    .evaluate((e) => ({ scroll: e.scrollWidth, width: e.clientWidth }));
  expect(width.scroll).toBeLessThanOrEqual(width.width + 1);
  await panel
    .getByRole("button", { name: "确认选择 1 张", exact: true })
    .click();
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await expect(page.getByLabel("演示选择回调")).toHaveText(
    '{"selectedRefs":["demo-choice-a"]}',
  );
});
test("visible prizes stay visible while hidden prizes have no identity", async ({
  page,
}) => {
  await open(page, "prize", "PLAYER2");
  const panel = page.getByRole("region", { name: "当前决策" });
  await expect(
    panel.getByRole("button", { name: "背面卡牌 1", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", {
      name: "明示奖赏 3 · 演示宝可梦甲",
      exact: true,
    }),
  ).toBeVisible();
});
test("replay navigation is read-only and timer stays in mode host", async ({
  page,
}) => {
  await open(page);
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByText("历史决策（只读）")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "结束回合", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "播放回放", exact: true }).click();
  await expect(page.getByLabel("回放步数", { exact: true })).toHaveValue("2");
  await page.getByRole("button", { name: "返回当前局面", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "结束回合", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await expect(page.getByLabel("演示选择回调")).toHaveText("尚未选择");
});
test("fullscreen failure falls back and card dialog stays usable", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(document, "fullscreenEnabled", { get: () => true });
    Element.prototype.requestFullscreen = () =>
      Promise.reject(new Error("denied"));
  });
  await open(page);
  await page.getByRole("button", { name: "全屏显示", exact: true }).click();
  await expect(
    page.getByText("暂时无法进入系统全屏，仍可在完整窗口中对战。"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查看状态：演示宝可梦甲", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(
    await page
      .locator(".battle-log")
      .evaluate((el) => getComputedStyle(el).overflowY),
  ).not.toBe("scroll");
  expect(
    await page
      .locator(".battle-log ol")
      .evaluate((el) => getComputedStyle(el).overflowY),
  ).toBe("auto");
});
test("real browser fullscreen enter, escape and cleanup", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "全屏显示", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => !!document.fullscreenElement))
    .toBe(true);
  await page.getByRole("button", { name: "退出全屏", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => !!document.fullscreenElement))
    .toBe(false);
  await page.getByRole("button", { name: "全屏显示", exact: true }).click();
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => !!document.fullscreenElement))
    .toBe(false);
});
test("PLAYER2 terminal winner and waiting state", async ({ page }) => {
  await open(page, "finished", "PLAYER2");
  await expect(page.getByText("你赢了！", { exact: false })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "结束回合", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await page
    .getByRole("combobox", { name: "演示场景", exact: true })
    .selectOption("waiting");
  await page.getByRole("button", { name: "打开对战界面", exact: true }).click();
  await expect(page.getByText("对手 正在操作…", { exact: true })).toBeVisible();
});
test("standalone desktop and narrow screenshots", async ({ page }) => {
  await open(page, "selection", "PLAYER2");
  await page.screenshot({
    path: "../../var/p3b/arena-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../../var/p3b/arena-narrow.png",
    fullPage: true,
  });
});
