import { test, expect } from "@playwright/test";
import path from "node:path";
const output = path.resolve(import.meta.dirname, "../../../var/browser-artifacts/collection");
const P = "CN:CSVM2cC:007";
test.beforeEach(async ({ page }) => {
  await page.route("https://asia.pokemon-card.com/**", (r) => r.abort());
  // The isolated test database has no downloaded upstream image cache.
  // Exercise the image/zoom UI with a deterministic local response.
  await page.route("**/api/sync/images/**", r => r.fulfill({
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="560"><rect width="400" height="560" fill="#e9dcaf"/></svg>',
  }));
  await page.goto("/");
  await expect(page.locator(".card-tile")).toHaveCount(72);
});
test("search, condition inventory, deck autosave, immutable revision and export", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.screenshot({
    path: path.join(output, "catalog-desktop.png"),
    fullPage: false,
  });
  await page.getByLabel("系列").selectOption("CSVM2cC");
  await page.getByLabel("搜索卡牌").fill("Gholdengo");
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page
    .getByRole("button", { name: "查看 赛富豪ex 007", exact: true })
    .click();
  await page.getByLabel("收藏品相").selectOption("良好");
  await page.getByLabel("收藏数量").fill("4");
  await page.getByLabel("收藏备注").fill("P1 浏览器验证");
  await page.getByRole("button", { name: "保存收藏", exact: true }).click();
  await expect(page.getByText("良好 4张", { exact: false })).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  const co = await (await request.get("/api/collection")).json();
  expect(co.entries.find((e: any) => e.printingId === P && e.condition === "良好").notes).toBe(
    "P1 浏览器验证",
  );
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  await page.locator(".template").filter({ hasText: "赛富豪ex" }).click();
  await expect(page.getByText("对局 效果已发布", { exact: true })).toBeVisible();
  await page.getByLabel("卡组名称").fill("浏览器验证卡组");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  await page.screenshot({
    path: path.join(output, "deck-desktop.png"),
    fullPage: false,
  });
  await page.getByRole("button", { name: "保存卡组版本", exact: true }).click();
  await expect(
    page.getByText("版本 1", { exact: false }).first(),
  ).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  await page.getByLabel("卡组减少 " + P, { exact: true }).click();
  await expect(
    page.getByText("卡组必须恰好60张", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存卡组版本", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("卡组增加 " + P, { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "保存卡组版本", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("deck.csv");
  await page.reload();
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  await page
    .locator(".deck-list button")
    .filter({ hasText: "浏览器验证卡组" })
    .click();
  await expect(page.getByLabel("卡组名称")).toHaveValue("浏览器验证卡组");
  await expect(page.getByText("对局 效果已发布", { exact: true })).toBeVisible();
  const decks = await (await request.get("/api/decks")).json();
  const d = decks.find((d: any) => d.name === "浏览器验证卡组");
  const revisions = await (
    await request.get(`/api/decks/${d.id}/revisions`)
  ).json();
  expect(revisions).toHaveLength(1);
  expect(
    revisions[0].entries.reduce((s: number, e: any) => s + e.quantity, 0),
  ).toBe(60);
  await page.getByRole("button", { name: "版本记录", exact: true }).click();
  await page.getByRole("button", { name: "使用此版本对战", exact: true }).click();
  await expect(page.getByLabel("对战卡组版本")).toHaveValue(revisions[0].id);
  expect(errors).toEqual([]);
});
test("ambiguous import, idempotent repeat and batch undo", async ({
  page,
  request,
}) => {
  const cards = (await (await request.get("/api/cards")).json()).cards;
  const ambiguous = cards.find(
    (c: any) => cards.filter((x: any) => x.cnName === c.cnName).length > 1,
  );
  const pid = ambiguous.printingId;
  const amount = async () => {
    const c = await (await request.get("/api/collection")).json();
    return c.entries
      .filter((e: any) => e.printingId === pid)
      .reduce((s: number, e: any) => s + e.quantity, 0);
  };
  const before = await amount();
  await page.getByRole("button", { name: /我的收藏/ }).click();
  async function importOnce() {
    await page.getByRole("button", { name: "导入收藏", exact: true }).click();
    await page.getByLabel("导入内容").fill("2 " + ambiguous.cnName);
    await page.getByRole("button", { name: "解析并预览" }).click();
    await expect(page.getByRole("button", { name: /确认导入/ })).toBeDisabled();
    await page.getByLabel("确认第1行版本").selectOption(pid);
    await expect(
      page.getByRole("button", { name: "确认导入 2 张" }),
    ).toBeEnabled();
    await page.getByRole("button", { name: "确认导入 2 张" }).click();
    await expect(page.locator("dialog")).toHaveCount(0);
  }
  await importOnce();
  expect(await amount()).toBe(before + 2);
  await importOnce();
  expect(await amount()).toBe(before + 2);
  await page.getByRole("button", { name: "导入记录", exact: true }).click();
  await page.getByRole("button", { name: "撤销此批次" }).click();
  await expect(page.getByRole("button", { name: "撤销此批次" })).toBeDisabled();
  expect(await amount()).toBe(before);
});
test("multi-tab conflict can be recovered without overwriting server draft", async ({
  page,
  request,
}) => {
  await page.getByRole("button", { name: "开始构筑" }).click();
  await page.getByLabel("卡组名称").fill("冲突验证");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const decks = await (await request.get("/api/decks")).json();
  const d = decks.find((d: any) => d.name === "冲突验证");
  expect(d).toBeTruthy();
  await request.put("/api/decks/" + d.id, {
    data: { name: "其他页面保存", entries: [], expectedVersion: d.version },
  });
  await page.getByLabel("卡组名称").fill("保留本页修改");
  await expect(page.locator(".save-state")).toHaveText("保存失败");
  await page.getByRole("button", { name: "将本页内容另存为副本" }).click();
  await expect(page.getByLabel("卡组名称")).toHaveValue(
    "保留本页修改 · 冲突副本",
  );
  await expect(page.locator(".save-state")).toHaveText("已保存");
  expect((await (await request.get("/api/decks/" + d.id)).json()).name).toBe(
    "其他页面保存",
  );
});
test("mobile catalog and builder have no horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(output, "catalog-mobile.png"),
    fullPage: false,
  });
  await page.getByRole("button", { name: /卡组构筑/ }).click();
  await page.locator(".template").filter({ hasText: "多龙巴鲁托ex" }).click();
  await expect(page.getByText("对局 效果已发布", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(output, "deck-mobile.png"),
    fullPage: false,
  });
});

test("reverting edits while an older save is in flight persists the latest draft", async ({
  page,
  request,
}) => {
  await page.getByRole("button", { name: "开始构筑" }).click();
  await page.getByLabel("卡组名称").fill("延迟保存验证");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const d = (await (await request.get("/api/decks")).json()).find(
    (d: any) => d.name === "延迟保存验证",
  );
  let unblock!: () => void;
  const gate = new Promise<void>((resolve) => {
    unblock = resolve;
  });
  let intercepted!: () => void;
  const started = new Promise<void>((resolve) => {
    intercepted = resolve;
  });
  let once = true;
  await page.route("**/api/decks/" + d.id, async (route) => {
    if (route.request().method() === "PUT" && once) {
      once = false;
      intercepted();
      await gate;
    }
    await route.continue();
  });
  await page.getByLabel("加入卡组 " + P, { exact: true }).click();
  await started;
  await page.getByLabel("卡组减少 " + P, { exact: true }).click();
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/decks/" + d.id) && r.request().method() === "PUT",
  );
  unblock();
  await response;
  await expect
    .poll(
      async () =>
        (await (await request.get("/api/decks/" + d.id)).json()).entries.length,
    )
    .toBe(0);
  await expect(page.locator(".save-state")).toHaveText("已保存");
});

test("expanded pool pagination, unverified detail and local CN artwork", async ({
  page,
  request,
}) => {
  const series = (await (await request.get("/api/cards", { params: { product: "CSV1C" } })).json()).cards;
  expect(series.length).toBeGreaterThan(144);
  expect(series.length).toBeLessThanOrEqual(216);
  await page.getByLabel("系列").selectOption("CSV1C");
  await expect(page.locator(".card-tile")).toHaveCount(72);
  await page.getByRole("button", { name: "下一页", exact: true }).first().click();
  await expect(page.getByText("第 2 / 3 页 · 每页 72 张").first()).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).first().click();
  await expect(page.locator(".card-tile")).toHaveCount(series.length - 144);
  await page.getByLabel("系列").selectOption("CSM1aC");
  await page.getByLabel("搜索卡牌").fill("CN:CSM1aC:001");
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page
    .getByRole("button", { name: "查看 飞天螳螂 001", exact: true })
    .click();
  await expect(
    page.getByText("效果待验证 · 暂不可对战", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  await page.getByLabel("系列").selectOption("151C");
  await page.getByLabel("搜索卡牌").fill("CN:151C:001");
  const image = page.locator(".tile-original");
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(300);
  await expect(page.locator(".image-label")).toContainText("简中卡图");
  await page.getByLabel("系列").selectOption("CSM1aC");
  await page.getByLabel("搜索卡牌").fill("CN:CSM1aC:001");
  await page.getByLabel("效果状态").selectOption("verified");
  await expect(page.locator(".card-tile")).toHaveCount(0);
});

test("expansion pack selection distinguishes shared series and resets pagination", async ({
  page,
  request,
}) => {
  const meta = await (await request.get("/api/meta")).json();
  const trip = meta.products.find((p: any) => p.name === "收集啦151 旅").id;
  const hope = meta.products.find((p: any) => p.name === "收集啦151 望").id;
  await page.getByLabel("扩充包", { exact: true }).selectOption(trip);
  await expect(page.getByText("找到 186 个卡牌身份")).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).first().click();
  await expect(page.getByText("第 2 / 3 页 · 每页 72 张").first()).toBeVisible();
  await page.getByLabel("扩充包", { exact: true }).selectOption(hope);
  await expect(page.getByText("第 1 / 3 页 · 每页 72 张").first()).toBeVisible();
  const tripCards = (
    await (await request.get("/api/cards", { params: { pack: trip } })).json()
  ).cards;
  const hopeCards = (
    await (await request.get("/api/cards", { params: { pack: hope } })).json()
  ).cards;
  const onlyTrip = tripCards.find(
    (c: any) => !hopeCards.some((d: any) => d.printingId === c.printingId),
  );
  await page.getByLabel("搜索卡牌").fill(onlyTrip.printingId);
  await expect(page.locator(".card-tile")).toHaveCount(0);
  await page.getByLabel("扩充包", { exact: true }).selectOption(trip);
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page.getByRole("button", { name: "清除扩充包", exact: true }).click();
  await expect(page.getByLabel("扩充包", { exact: true })).toHaveValue("");
});

test("detail artwork opens a responsive preview and returns focus on close", async ({
  page,
}) => {
  await page.getByLabel("系列").selectOption("151C");
  await page.getByLabel("搜索卡牌").fill("CN:151C:001");
  await page
    .getByRole("button", { name: "查看 妙蛙种子 001", exact: true })
    .click();
  const trigger = page.getByRole("button", {
    name: "放大卡图：妙蛙种子",
    exact: true,
  });
  const thumbnail = await trigger.locator("img").boundingBox();
  const preview = page.getByRole("dialog", {
    name: "放大卡图：妙蛙种子",
    exact: true,
  });
  await trigger.click();
  await expect(preview).toBeVisible();
  await expect
    .poll(() =>
      preview
        .locator("img")
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(300);
  expect((await preview.locator("img").boundingBox())!.width).toBeGreaterThan(
    thumbnail!.width,
  );
  await preview.locator("img").click();
  await expect(preview).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page.getByLabel("收藏数量")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(preview).toBeVisible();
  await preview.click({ position: { x: 5, y: 5 } });
  await expect(preview).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await trigger.click();
  await expect(preview).toBeVisible();
  await expect
    .poll(async () => (await preview.locator("img").boundingBox())?.width || 0)
    .toBeGreaterThan(thumbnail!.width);
  const mobileImage = (await preview.locator("img").boundingBox())!;
  expect(mobileImage.width).toBeGreaterThan(thumbnail!.width);
  expect(mobileImage.x).toBeGreaterThanOrEqual(0);
  expect(mobileImage.x + mobileImage.width).toBeLessThanOrEqual(390);
  expect(mobileImage.y + mobileImage.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: path.join(output, "card-preview-mobile.png") });
  await page.getByRole("button", { name: "关闭放大卡图" }).click();
  await expect(preview).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await page.getByLabel("关闭窗口").click();
  await expect(page.locator("dialog")).toHaveCount(0);
});
