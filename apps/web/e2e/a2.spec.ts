import { test, expect } from "@playwright/test";

test("A2 can be selected, completes setup, and retains its version after reload", async ({
  page,
}) => {
  test.setTimeout(60000);
  const meta = await (await page.request.get("/api/meta")).json();
  const deck = await (
    await page.request.post("/api/decks", {
      data: { name: "A2 浏览器验收", entries: meta.templates[0].entries },
    })
  ).json();
  const revision = await (
    await page.request.post(`/api/decks/${deck.id}/revisions`, {
      data: { expectedVersion: deck.version },
    })
  ).json();
  await page.goto("/#battle");
  await page.getByLabel("对战卡组版本").selectOption(revision.id);
  await page.getByLabel("AI 策略").selectOption("A2");
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  const id = await page.evaluate(() => localStorage.getItem("ptcg-match"));
  const read = async () =>
    (await page.request.get(`/api/battle/matches/${id}`)).json();
  for (let step = 0; step < 12; step++) {
    await expect
      .poll(
        async () => {
          const v = await read();
          return !!v.decision || v.done || v.phase === "playing";
        },
        { timeout: 15000 },
      )
      .toBe(true);
    const v = await read();
    expect(v.aiVersion).toBe("a2-information-search-v1-experimental");
    expect(v.observation.opponent.hand).toBeNull();
    if (v.phase === "playing" || v.done) break;
    if (v.decision.kind === "selection") {
      for (let i = 0; i < v.decision.min; i++)
        await page.locator(".decision-candidates button").nth(i).click();
      await page
        .getByRole("button", {
          name: `确认选择 ${v.decision.min} 张`,
          exact: true,
        })
        .click();
    } else await page.locator(".decision-options button").first().click();
    await expect
      .poll(async () => (await read()).stateVersion)
      .toBeGreaterThan(v.stateVersion);
  }
  expect((await read()).phase).toBe("playing");
  // Freeze automatic advances before testing persistence; an in-flight A2
  // search otherwise competes with the reload for the match lock.
  await page.getByRole("button", { name: "暂停 AI", exact: true }).click();
  await page.reload();
  await expect(page.getByLabel("我的场面")).toBeVisible({ timeout: 20000 });
  expect((await read()).aiVersion).toBe(
    "a2-information-search-v1-experimental",
  );
});
