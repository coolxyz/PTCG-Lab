import { test, expect } from "@playwright/test";
import { fixture } from "./fixture";

for (const [format, contents] of [
  ["CSV", "\uFEFFprintingId,quantity\r\nCN:TEST:001,2\r\nCN:TEST:002,3\r\n"],
  ["TXT", "2 CN:TEST:001\n3 CN:TEST:002"],
]) {
  test(`phone selects desktop ${format}, imports explicitly and shares both desktop formats`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await fixture(page);
    await page.goto("/");
    await page.getByRole("button", { name: "卡组", exact: true }).click();
    await page.evaluate(
      (text) => localStorage.setItem("deck-file", text),
      contents,
    );
    await page.getByRole("button", { name: "选择卡组文件" }).click();
    await expect(page.getByLabel("导入卡组文本")).toHaveValue(
      contents.replace(/\r\n/g, "\n"),
    );
    await expect(page.getByLabel("卡组名称")).toHaveCount(0);
    await page.getByRole("button", { name: "检查并新建卡组" }).click();
    await expect(page.getByText("5 / 60 张", { exact: true })).toBeVisible();
    await page.getByLabel("卡组名称").fill(`桌面 ${format} 卡组`);
    await expect(
      page.getByRole("button", { name: "选择卡组文件" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "保存卡组", exact: true }).click();
    await page.getByRole("button", { name: "查看卡组导出文本" }).click();
    await expect(page.getByLabel("复制此卡组内容到另一台设备")).toHaveValue(
      /printingId,quantity/,
    );
    await page.getByRole("button", { name: "分享卡组文件" }).click();
    await page.getByLabel("卡组文件格式").selectOption("text");
    await page.getByRole("button", { name: "查看卡组导出文本" }).click();
    await expect(page.getByLabel("复制此卡组内容到另一台设备")).toHaveValue(
      "2 CN:TEST:001\n3 CN:TEST:002",
    );
    await page.getByRole("button", { name: "分享卡组文件" }).click();
    const calls = await page.evaluate(() =>
      (window as any).documentCalls.filter((r: any) => r.op === "share-deck"),
    );
    expect(calls.map((r: any) => r.format)).toEqual(["csv", "text"]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.reload();
    await page.getByRole("button", { name: "卡组", exact: true }).click();
    await page
      .getByRole("button", { name: new RegExp(`桌面 ${format} 卡组.*5 张`) })
      .click();
    await expect(page.getByLabel("CN:TEST:002数量")).toHaveValue("3");
  });
}

test("duplicate, search and confirmed deletion preserve other decks; cancellation is harmless", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建卡组" }).click();
  await page.getByLabel("卡组名称").fill("我的主卡组");
  await expect(
    page.getByRole("button", { name: "删除卡组", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存卡组", exact: true }).click();
  await page.getByRole("button", { name: "将草稿另存为新卡组" }).click();
  await page.getByLabel("查找卡组").fill("副本");
  await expect(
    page.getByRole("button", { name: /我的主卡组（副本）.*0 张/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /我的主卡组0 张/ }),
  ).toHaveCount(0);
  await page.getByLabel("查找卡组").fill("");
  await page.getByRole("button", { name: "删除卡组", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText(
    "冻结的对战版本与历史对局仍保留",
  );
  await page.getByRole("button", { name: "取消删除" }).click();
  await expect(page.getByLabel("卡组名称")).toHaveValue("我的主卡组（副本）");
  await page.getByRole("button", { name: "删除卡组", exact: true }).click();
  await page.getByRole("button", { name: "确认删除卡组" }).click();
  await expect(page.getByRole("status")).toContainText("卡组已删除");
  await page.reload();
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  const decks = await page.evaluate(
    () => JSON.parse(localStorage.getItem("fixture-owner")!).decks,
  );
  expect(decks.map((d: any) => d.name)).toEqual(["我的主卡组"]);
});

test("a stale confirmation cannot delete a more recent edit", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "卡组", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新建卡组" }).click();
  await page.getByRole("button", { name: "删除卡组", exact: true }).click();
  await page.evaluate(() => {
    const s = JSON.parse(localStorage.getItem("fixture-owner")!);
    s.decks[0].version++;
    s.decks[0].name = "已在别处更新";
    localStorage.setItem("fixture-owner", JSON.stringify(s));
  });
  await page.getByRole("button", { name: "确认删除卡组" }).click();
  await expect(page.getByRole("alert")).toContainText("重新加载后再删除");
  await page.getByRole("button", { name: "取消删除" }).click();
  await page.getByRole("button", { name: "重新载入（放弃草稿）" }).click();
  await expect(page.getByLabel("卡组名称")).toHaveValue("已在别处更新");
});
