import { test, expect } from "@playwright/test";
import { fixture } from "./fixture";
test("foreground refresh preserves a collection draft and stale version cannot overwrite another edit", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "查看测试宝可梦 CN:TEST:001" })
    .click();
  await page.getByLabel("持有数量").fill("7");
  await page.getByLabel("收藏备注").fill("尚未保存的备注");
  await page.evaluate(() => {
    const state = {
      decks: [],
      collection: {
        version: 1,
        entries: [
          {
            printingId: "CN:TEST:001",
            quantity: 5,
            condition: "未标注",
            notes: "另一处保存的新备注",
            wishlist: false,
          },
        ],
      },
    };
    localStorage.setItem("fixture-owner", JSON.stringify(state));
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
  });
  await expect(
    page.getByText("我的收藏 · 共 5 张", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("持有数量")).toHaveValue("7");
  await expect(page.getByLabel("收藏备注")).toHaveValue("尚未保存的备注");
  await page.getByRole("button", { name: "保存此品相" }).click();
  await expect(page.getByRole("alert")).toContainText("收藏已变化");
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("fixture-owner")!).collection.entries[0]
          .notes,
    ),
  ).toBe("另一处保存的新备注");
  await page.getByRole("button", { name: "重新载入此品相" }).click();
  await expect(page.getByLabel("收藏备注")).toHaveValue("另一处保存的新备注");
});
test("foreground refresh retains an unsaved deck and conflict can be preserved as a new deck", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建卡组" }).click();
  await page.getByLabel("卡组名称").fill("未保存的手机草稿");
  await page.evaluate(() => {
    const state = JSON.parse(localStorage.getItem("fixture-owner")!);
    state.decks[0].name = "另一处的新名称";
    state.decks[0].version = 2;
    localStorage.setItem("fixture-owner", JSON.stringify(state));
    window.dispatchEvent(
      new PageTransitionEvent("pageshow", { persisted: true }),
    );
  });
  await expect(
    page.getByRole("button", { name: /另一处的新名称.*0 张/ }),
  ).toBeVisible();
  await expect(page.getByLabel("卡组名称")).toHaveValue("未保存的手机草稿");
  await page.getByRole("button", { name: "保存卡组", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("卡组已变化");
  await page.getByRole("button", { name: "将草稿另存为新卡组" }).click();
  await expect(page.getByLabel("卡组名称")).toHaveValue(
    "未保存的手机草稿（副本）",
  );
  const state = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("fixture-owner")!),
  );
  expect(state.decks).toHaveLength(2);
  expect(
    state.decks.some(
      (d: any) => d.name === "另一处的新名称" && d.version === 2,
    ),
  ).toBe(true);
});
test("phone edits collection condition/notes and imports, shares and undoes a desktop CSV batch", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "查看测试宝可梦 CN:TEST:001" })
    .click();
  await page.getByLabel("收藏品相", { exact: true }).selectOption("全新");
  await page.getByLabel("持有数量").fill("3");
  await page.getByLabel("收藏备注").fill("手机保存的备注");
  await page.getByLabel("加入愿望单", { exact: true }).check();
  await page.getByRole("button", { name: "保存此品相" }).click();
  await expect(page.getByText("此品相已保存")).toBeVisible();
  await page.getByRole("button", { name: "关闭卡牌详情" }).click();
  await page.getByRole("button", { name: "收藏", exact: true }).click();
  await page.getByRole("button", { name: "选择收藏 CSV" }).click();
  await page.getByRole("button", { name: "预览收藏导入" }).click();
  await expect(page.getByText(/将新增 2 张卡牌/)).toBeVisible();
  await page.getByRole("button", { name: "确认增加到收藏" }).click();
  await page.getByRole("button", { name: "分享收藏 CSV" }).click();
  await expect(page.getByText("已完成系统分享")).toBeVisible();
  await page.getByText("导入批次 · 1", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "查看测试能量 CN:TEST:002" }),
  ).toHaveCount(0);
  await page.screenshot({
    path: "var/p8e-ios/e3/phone-collection.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "撤销此批次" }).click();
  await expect(page.getByText(/已撤销导入数量/)).toBeVisible();
  const state = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("fixture-owner")!),
  );
  expect(state.collection.entries).toEqual([
    {
      printingId: "CN:TEST:001",
      condition: "全新",
      quantity: 3,
      notes: "手机保存的备注",
      wishlist: true,
    },
  ]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
test("file cancellation creates nothing; selecting a deck requires an explicit import and share contains only its ID", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByText("导入卡组文本", { exact: true }).click();
  await page.evaluate(() => localStorage.setItem("cancel-file", "1"));
  await page.getByRole("button", { name: "选择卡组文件" }).click();
  await expect(page.getByLabel("导入卡组文本")).toHaveValue("");
  await page.evaluate(() => localStorage.removeItem("cancel-file"));
  await page.getByRole("button", { name: "选择卡组文件" }).click();
  await expect(page.getByLabel("导入卡组文本")).toHaveValue(/文件卡组/);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("fixture-owner") || '{"decks":[]}')
          .decks.length,
    ),
  ).toBe(0);
  await page.getByRole("button", { name: "检查并新建卡组" }).click();
  await expect(page.getByLabel("卡组名称")).toHaveValue("文件卡组");
  await page.getByRole("button", { name: "分享卡组文件" }).click();
  const calls = await page.evaluate(() => (window as any).documentCalls);
  const share = calls.find((r: any) => r.op === "share-deck");
  expect(Object.keys(share).sort()).toEqual(["format", "id", "op"]);
  expect(share.format).toBe("csv");
  expect(JSON.stringify(calls)).not.toMatch(/atk_|rtk_|controllerId|file:\/\//);
});
test("iPad previews a display release and applies only after explicit confirmation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "选择资料 JSON 文件" }),
  ).toBeDisabled();
  await page.getByLabel("资料文件 SHA-256").fill("a".repeat(64));
  await page.getByRole("button", { name: "选择资料 JSON 文件" }).click();
  await expect(page.getByText(/新增 1 · 移除 0/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认应用目录更新" }),
  ).toBeDisabled();
  await page.getByLabel("我已核对资料来源、校验值及变更范围").check();
  await page.screenshot({
    path: "var/p8e-ios/e3/ipad-update.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认应用目录更新" }).click();
  await expect(page.getByText(/展示资料已更新/)).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByText("目录版本", { exact: true }).click();
  await expect(page.getByText("test-version-2", { exact: true })).toBeVisible();
});
test("pending online outcome blocks activation and leaves the installed display identity unchanged", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("资料文件 SHA-256").fill("a".repeat(64));
  await page.getByRole("button", { name: "选择资料 JSON 文件" }).click();
  await page.getByLabel("我已核对资料来源、校验值及变更范围").check();
  await page.evaluate(() => localStorage.setItem("block-update", "1"));
  await page.getByRole("button", { name: "确认应用目录更新" }).click();
  await expect(page.getByRole("alert")).toContainText("先恢复待确认的对战操作");
  const state = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("fixture-owner")!),
  );
  expect(state.catalogVersion).toBeUndefined();
  expect(state.candidate).toBeTruthy();
});
