import { test, expect } from "@playwright/test";
import path from "node:path";
const savedKey = "ptcg-p3-practice:match";
test("all five presets use the unchanged H2 backend and can be resumed", async ({
  page,
}) => {
  await page.goto("/#practice");
  const select = page.getByLabel("我的预设卡组", { exact: true });
  await expect(select.locator("option")).toHaveCount(6);
  const presets = await select
    .locator("option")
    .evaluateAll((options) =>
      options.map((o) => (o as HTMLOptionElement).value).filter(Boolean),
    );
  for (const preset of presets) {
    await select.selectOption(preset);
    await expect(
      page.getByRole("button", { name: "开始人机对战", exact: true }),
    ).toBeEnabled();
    await page.getByLabel("AI 卡组", { exact: true }).selectOption(preset);
    await expect(page.getByLabel("AI 策略", { exact: true })).toHaveValue("H2");
    await page
      .getByRole("button", { name: "开始人机对战", exact: true })
      .click();
    await expect(page.getByLabel("当前 AI 策略")).toBeVisible();
    const id = await page.evaluate(
      (key) => localStorage.getItem(key),
      savedKey,
    );
    const snapshot = await (
      await page.request.get(`/api/battle/matches/${id}`)
    ).json();
    expect(snapshot.aiStrategy.id).toBe(preset);
    await expect(page.getByLabel("当前 AI 策略")).toHaveText(
      snapshot.aiStrategy.name,
    );
    expect(snapshot.observation.opponent.hand).toBeNull();
    await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  }
  await expect(
    page.getByRole("region", { name: "我的对局" }).getByRole("button"),
  ).toHaveCount(5);
});
test("real commands, receipt retry, refresh, single step, resign and historical replay", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#practice");
  const select = page.getByLabel("我的预设卡组", { exact: true });
  await expect(select.locator("option")).toHaveCount(6);
  const preset = await select.locator("option").nth(1).getAttribute("value");
  await select.selectOption(preset!);
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await page.getByRole("button", { name: "暂停 AI", exact: true }).click();
  const id = await page.evaluate((key) => localStorage.getItem(key), savedKey);
  const get = async () =>
    await (await page.request.get(`/api/battle/matches/${id}`)).json();
  let receiptChecked = false;
  let decisions = 0;
  for (let i = 0; i < 80 && decisions < 6; i++) {
    const v = await get();
    if (v.done) break;
    if (!v.decision) {
      await page
        .getByRole("button", { name: "AI 下一步", exact: true })
        .click();
    } else {
      if (!receiptChecked) {
        await page.route(
          `**/api/battle/matches/${id}/commands`,
          async (route) => {
            await route.fetch();
            await route.abort();
          },
          { times: 1 },
        );
      }
      const d = v.decision;
      if (d.kind === "selection") {
        for (let n = 0; n < d.min; n++)
          await page.locator(".decision-candidates button").nth(n).click();
        await page
          .getByRole("button", {
            name: d.max === 0 ? "已查看，继续结算" : `确认选择 ${d.min} 张`,
            exact: true,
          })
          .click();
      } else await page.locator(".decision-options button").first().click();
      decisions++;
      if (!receiptChecked) {
        await expect(
          page.getByRole("button", { name: "重试同一命令", exact: true }),
        ).toBeVisible();
        const accepted = await get();
        await page.reload();
        await expect(
          page.getByRole("button", { name: "重试同一命令", exact: true }),
        ).toBeVisible();
        await page
          .getByRole("button", { name: "暂停 AI", exact: true })
          .click();
        await page
          .getByRole("button", { name: "重试同一命令", exact: true })
          .click();
        await expect(
          page.getByRole("button", { name: "重试同一命令", exact: true }),
        ).toHaveCount(0);
        expect((await get()).stateVersion).toBe(accepted.stateVersion);
        receiptChecked = true;
      }
    }
    await expect
      .poll(async () => (await get()).stateVersion)
      .toBeGreaterThan(v.stateVersion);
  }
  expect(decisions).toBeGreaterThanOrEqual(3);
  expect(receiptChecked).toBe(true);
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByText("历史决策（只读）")).toBeVisible();
  await page.getByLabel("回放下一步", { exact: true }).click();
  await expect(page.getByText("回放 · 第 1 步", { exact: true })).toBeVisible();
  const frames = await (
    await page.request.get(`/api/battle/matches/${id}/replay`)
  ).json();
  expect(
    frames.frames.every((f: any) => f.view.observation.opponent.hand === null),
  ).toBe(true);
  expect(JSON.stringify(frames)).not.toContain('"seed"');
  await page.getByRole("button", { name: "返回当前局面", exact: true }).click();
  await page.getByRole("button", { name: "认输", exact: true }).click();
  await page.getByRole("button", { name: "确认认输", exact: true }).click();
  await expect(page.locator(".battle-result")).toContainText("你已认输");
  await page.screenshot({
    path: path.resolve(
      import.meta.dirname,
      "../../../var/p3b/practice-result.png",
    ),
  });
  expect(errors).toEqual([]);
});
