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

test("appearance presets persist, cancel is inert, and board selection survives", async ({
  page,
}) => {
  await open(page, "board-selection");
  const board = page.locator(".arena");
  const card = board.locator(".tabletop-card.usable").first();
  await expect(board).toHaveCSS("background-color", "rgb(39, 52, 68)");
  await expect(card).toHaveCSS("box-shadow", /127, 231, 255/);
  await card.click();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(card).toHaveCSS("box-shadow", /163, 68, 6/);
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "桌面外观" });
  await dialog.getByRole("radio", { name: "暖沙", exact: true }).check();
  // Draft previews do not mutate the battle board or submit a command.
  await expect(board).toHaveCSS("background-color", "rgb(39, 52, 68)");
  await dialog.getByRole("button", { name: "保存外观" }).click();
  await expect(board).toHaveCSS("background-color", "rgb(216, 207, 185)");
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(card).toHaveCSS("box-shadow", /163, 68, 6/);
  await page.screenshot({
    path: "var/p55appearance/sand-selection.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  await dialog.getByRole("radio", { name: "经典赛场", exact: true }).check();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "桌面外观", exact: true }),
  ).toBeFocused();
  await expect(board).toHaveCSS("background-color", "rgb(216, 207, 185)");
  await open(page);
  await expect(board).toHaveCSS("background-color", "rgb(216, 207, 185)");
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(board).toHaveCSS("background-color", "rgb(216, 207, 185)");
});

test("all playmats work in fullscreen and the settings fit a narrow screen", async ({
  page,
}) => {
  await open(page, "board-selection");
  await page.getByRole("button", { name: "全屏显示", exact: true }).click();
  for (const name of [
    "墨绿",
    "深蓝",
    "暗紫",
    "经典赛场",
    "红蓝对阵",
    "蓝色竞技场",
  ]) {
    await page.getByRole("button", { name: "桌面外观", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "桌面外观" });
    expect(
      await dialog.evaluate((el) => document.fullscreenElement?.contains(el)),
    ).toBe(true);
    await dialog.getByRole("radio", { name, exact: true }).check();
    await dialog.getByRole("button", { name: "保存外观" }).click();
    if (["经典赛场", "红蓝对阵", "蓝色竞技场"].includes(name)) {
      await expect(page.locator(".arena")).toHaveCSS(
        "background-image",
        /url\(/,
      );
      await page.screenshot({
        path: `var/p55appearance/mat-${name}.png`,
        fullPage: true,
      });
    }
  }
  await page.getByRole("button", { name: "退出全屏", exact: true }).click();
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  await page.screenshot({
    path: "var/p55appearance/settings-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = page.getByRole("dialog", { name: "桌面外观" });
  const bounds = await dialog.evaluate((el) => ({
    width: el.clientWidth,
    scroll: el.scrollWidth,
  }));
  expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
  await page.screenshot({
    path: "var/p55appearance/settings-mobile.png",
    fullPage: true,
  });
  await dialog.getByRole("button", { name: "恢复默认" }).click();
  await dialog.getByRole("button", { name: "保存外观" }).click();
  await expect(page.locator(".arena")).toHaveCSS(
    "background-color",
    "rgb(39, 52, 68)",
  );
});

test("custom artwork is validated, stored locally, restored, and removable", async ({
  page,
}) => {
  const calls: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/|wss?:/.test(request.url())) calls.push(request.url());
  });
  await open(page);
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "桌面外观" });
  const input = dialog.getByLabel("导入桌垫图片", { exact: true });
  await input.setInputFiles({
    name: "invalid.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from("<svg/>"),
  });
  await expect(dialog.getByRole("alert")).toHaveText(
    "请选择 JPG、PNG 或 WebP 图片。",
  );
  await input.setInputFiles({
    name: "too-large.png",
    mimeType: "image/png",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
  });
  await expect(dialog.getByRole("alert")).toHaveText("图片不能超过 5 MB。");
  await input.setInputFiles({
    name: "broken.png",
    mimeType: "image/png",
    buffer: Buffer.from("broken"),
  });
  await expect(dialog.getByRole("alert")).toContainText("无法读取");
  const png = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 2000;
    canvas.height = 1000;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, 2000, 1000);
    ctx.fillStyle = "#efb050";
    ctx.fillRect(0, 0, 1000, 1000);
    return canvas.toDataURL("image/png").split(",")[1];
  });
  await input.setInputFiles({
    name: "my-mat.png",
    mimeType: "image/png",
    buffer: Buffer.from(png, "base64"),
  });
  await expect(
    dialog.getByRole("radio", { name: "使用已导入图片" }),
  ).toBeChecked();
  await dialog.getByRole("button", { name: "保存外观" }).click();
  await expect(page.locator(".arena")).toHaveCSS(
    "background-image",
    /data:image\/jpeg/,
  );
  await page.screenshot({
    path: "var/p55appearance/custom.png",
    fullPage: true,
  });
  const size = await page.evaluate(async () => {
    const saved = JSON.parse(
      localStorage.getItem("ptcg.battle.appearance.v1")!,
    );
    const img = new Image();
    img.src = saved.customImage;
    await img.decode();
    return { width: img.width, height: img.height };
  });
  expect(size).toEqual({ width: 1600, height: 800 });
  await open(page);
  await expect(page.locator(".arena")).toHaveCSS(
    "background-image",
    /data:image\/jpeg/,
  );
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  await dialog.getByRole("button", { name: "移除图片" }).click();
  await dialog.getByRole("button", { name: "保存外观" }).click();
  expect(
    await page.evaluate(() =>
      JSON.parse(localStorage.getItem("ptcg.battle.appearance.v1")!),
    ),
  ).toEqual({ preset: "slate" });
  expect(calls).toEqual([]);
});

test("invalid preferences and unavailable browser storage have a usable fallback", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "ptcg.battle.appearance.v1",
      JSON.stringify({
        preset: "custom",
        customImage: "https://example.invalid/tracker.png",
      }),
    );
    Storage.prototype.setItem = () => {
      throw new Error("storage unavailable");
    };
  });
  await open(page);
  await expect(page.locator(".arena")).toHaveCSS(
    "background-color",
    "rgb(39, 52, 68)",
  );
  await page.getByRole("button", { name: "桌面外观", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "桌面外观" });
  await dialog.getByRole("radio", { name: "墨绿", exact: true }).check();
  await dialog.getByRole("button", { name: "保存外观" }).click();
  await expect(page.locator(".arena")).toHaveCSS(
    "background-color",
    "rgb(33, 72, 60)",
  );
  await expect(page.locator(".battle-display-message")).toContainText(
    "浏览器未能保存设置",
  );
  await expect(
    page.getByRole("button", { name: "结束回合", exact: true }),
  ).toBeEnabled();
});

for (const seat of ["PLAYER1", "PLAYER2"])
  test(`${seat}: public information timeline, readable updates and card inspection`, async ({
    page,
  }) => {
    await open(page, "public-history", seat);
    const log = page.getByRole("region", { name: "对局信息", exact: true });
    await expect(
      log.getByText("使用招式 · 演示宝可梦乙 · 演示招式", { exact: true }),
    ).toBeVisible();
    await expect(log.getByText(/投币结果：1. 正面 · 2. 反面/)).toBeVisible();
    await expect(log.getByText(/HP：90 → 30/)).toBeVisible();
    await expect(log.getByText(/状态：中毒/)).toBeVisible();
    await log.getByRole("button", { name: /#1 · 对手/ }).click();
    await expect(
      log.getByText("对手 · 按效果展示卡牌", { exact: true }),
    ).toBeVisible();
    await log
      .getByRole("button", {
        name: "查看记录卡牌：演示公开宝可梦丙",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("dialog", { name: "记录卡牌详情" }),
    ).toContainText("不代表它仍在原区域或当前手牌中");
    await page.keyboard.press("Escape");
    await expect(
      log.getByRole("button", {
        name: "查看记录卡牌：演示公开宝可梦丙",
        exact: true,
      }),
    ).toBeFocused();
    await page.getByRole("button", { name: "刷新局面", exact: true }).click();
    await expect(
      log.getByRole("button", { name: "1 条新记录 · 查看最新", exact: true }),
    ).toBeVisible();
    await expect(
      log.getByText("对手 · 按效果展示卡牌", { exact: true }),
    ).toBeVisible();
    await expect(log.getByText("结束回合", { exact: true })).toHaveCount(0);
    // Inspecting another card while paused must not advance the frozen list.
    await log.getByRole("button", {
      name: "查看记录卡牌：演示公开宝可梦丙", exact: true,
    }).click();
    await page.keyboard.press("Escape");
    await expect(log.getByText("结束回合", { exact: true })).toHaveCount(0);
    await expect(log.getByRole("button", {
      name: "1 条新记录 · 查看最新", exact: true,
    })).toBeVisible();
    await log
      .getByRole("button", { name: "1 条新记录 · 查看最新", exact: true })
      .click();
    await expect(log.getByText("结束回合", { exact: true })).toBeVisible();
    await expect(
      log.getByRole("button", { name: "已阅", exact: true }),
    ).toBeDisabled();
    await log
      .getByLabel("对局记录范围", { exact: true })
      .selectOption("reveal");
    await expect(log.getByText("结束回合", { exact: true })).toHaveCount(0);
    await log.getByRole("button", { name: /#1 · 对手/ }).click();
    await page.screenshot({
      path: `var/p55events/information-${seat}.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await page
      .locator(".battle-screen")
      .evaluate((e) => ({ width: e.clientWidth, scroll: e.scrollWidth }));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
    await log.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `var/p55events/information-mobile-${seat}.png`,
      fullPage: true,
    });
  });


test("legacy records show useful collapsed headlines without inventing played cards", async ({ page }) => {
  await open(page, "legacy-history", "PLAYER1");
  const log = page.getByRole("region", { name: "对局信息", exact: true });
  const outcome = log.getByRole("button", { name: /#2 · 系统.*HP：90 → 30/ });
  await expect(outcome).toBeVisible();
  const reveal = log.getByRole("button", { name: /#1 · 系统.*按效果展示卡牌：演示公开宝可梦丙/ });
  await expect(reveal).toHaveAttribute("aria-expanded", "false");
  await expect(log.getByText("可见局面更新", { exact: true })).toHaveCount(0);
  await expect(log.getByText("使用物品 · 演示检索物品", { exact: true })).toHaveCount(0);
  await reveal.click();
  await expect(log.getByText("对手弃牌区新增", { exact: true })).toBeVisible();
  await expect(log.getByText("此步未保存动作声明，仅展示已确认的可见信息。", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "var/p55events/information-titles.png", fullPage: true });
});


test("stadium activation and its selection step retain public context without a visible delta", async ({ page }) => {
  await open(page, "stadium-history", "PLAYER2");
  const log = page.getByRole("region", { name: "对局信息", exact: true });
  await expect(log.getByRole("button", { name: /#1 · 对手.*使用竞技场效果 · 演示竞技场/ })).toBeVisible();
  await expect(log.getByRole("button", { name: /#2 · 对手.*竞技场效果 · 演示竞技场 · 完成选牌/ })).toBeVisible();
  await expect(log.getByRole("button", { name: "查看记录卡牌：演示竞技场", exact: true })).toBeVisible();
  await expect(log).not.toContainText("无新增公开结果");
  await page.screenshot({ path: "var/p55events/stadium-information.png", fullPage: true });
});
