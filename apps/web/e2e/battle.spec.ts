import { test, expect } from "@playwright/test";
import path from "node:path";
const out = path.resolve(import.meta.dirname, "../../../var/browser-artifacts/battle");
test("practice admission, prompt choices, refresh recovery and private replay", async ({
  page,
}) => {
  const request = page.request;
  test.setTimeout(300000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const meta = await (await request.get("/api/meta")).json();
  const template = meta.templates[0];
  const deck = await (
    await request.post("/api/decks", {
      data: { name: "P2 浏览器练习", entries: template.entries },
    })
  ).json();
  await request.post(`/api/decks/${deck.id}/revisions`, {
    data: { expectedVersion: deck.version },
  });
  await page.goto("/#battle");
  await page
    .getByLabel("对战卡组版本")
    .selectOption({ label: "P2 浏览器练习 · 版本 1" });
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  await page.getByLabel("AI 行动速度").selectOption("1200");
  let id = await page.evaluate(() => localStorage.getItem("ptcg-match"));
  expect(id).toBeTruthy();
  let selectedPrompt = false;
  for (let step = 0; step < 9; step++) {
    // A turn can contain many individually animated AI actions. Detect a
    // stalled action instead of assuming the whole turn takes five seconds.
    for (let aiStep = 0; aiStep < 80; aiStep++) {
      const before = await (await request.get(`/api/battle/matches/${id}`)).json();
      if (before.decision || before.done) break;
      await expect.poll(async () => {
        const next = await (await request.get(`/api/battle/matches/${id}`)).json();
        return next.stateVersion > before.stateVersion || !!next.decision || next.done;
      }, { timeout: 10000 }).toBe(true);
    }
    const v = await (await request.get(`/api/battle/matches/${id}`)).json();
    expect(!!v.decision || v.done).toBe(true);
    expect(v.observation.opponent.hand).toBeNull();
    if (v.done) break;
    const d = v.decision;
    if (d.kind === "selection") {
      selectedPrompt = true;
      if (d.min > 0)
        await expect(
          page.getByRole("button", { name: "确认选择 0 张" }),
        ).toBeDisabled();
      for (let i = 0; i < d.min; i++)
        await page.locator(".decision-candidates button").nth(i).click();
      await page.getByRole("button", { name: `确认选择 ${d.min} 张` }).click();
    } else {
      const end = page.getByRole("button", { name: "结束回合", exact: true });
      if (await end.count()) await end.click();
      else await page.locator(".decision-options button").first().click();
    }
    await expect
      .poll(
        async () =>
          (await (await request.get(`/api/battle/matches/${id}`)).json())
            .stateVersion,
      )
      .toBeGreaterThan(v.stateVersion);
  }
  expect(selectedPrompt).toBe(true);
  await page.reload();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("ptcg-match"))).toBe(
    id,
  );
  await page.screenshot({
    path: path.join(out, "battle-active-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByText("回放 · 第 0 步", { exact: true })).toBeVisible();
  await page.screenshot({
    path: path.join(out, "battle-desktop.png"),
    fullPage: true,
  });
  await page.getByLabel("回放下一步").click();
  await expect(page.getByText("回放 · 第 1 步", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: path.join(out, "battle-mobile.png"),
    fullPage: true,
  });
  const replay = await (
    await request.get(`/api/battle/matches/${id}/replay`)
  ).json();
  expect(
    replay.frames.every((f: any) => f.view.observation.opponent.hand === null),
  ).toBe(true);
  expect(JSON.stringify(replay)).not.toContain('"seed"');
  await page.getByRole("button", { name: "返回当前局面", exact: true }).click();
  if (await page.getByRole("button", { name: "认输", exact: true }).count()) {
    await page.getByRole("button", { name: "认输", exact: true }).click();
    await page.getByRole("button", { name: "确认认输", exact: true }).click();
    await expect(page.locator(".battle-result")).toContainText("你已认输");
  }
  expect(errors).toEqual([]);
});
