import { test, expect } from "@playwright/test";
for (let index = 0; index < 5; index++) {
  test(`H2 preset ${index + 1}: import, setup, reload and resign`, async ({ page }) => {
    test.setTimeout(120000);
    const errors: string[] = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto("/#battle");
    await expect(page.getByLabel("我的预设卡组").locator("option")).toHaveCount(6);
    const preset = await page.getByLabel("我的预设卡组").locator("option").nth(index + 1).getAttribute("value");
    await expect(page.getByLabel("AI 策略")).toHaveValue("H2");
    await page.getByLabel("我的预设卡组").selectOption(preset!);
    await expect(page.getByLabel("对战卡组版本")).not.toHaveValue("");
    await page.getByLabel("AI 卡组").selectOption(preset!);
    await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
    await expect(page.getByLabel("我的场面")).toBeVisible();
    await page.getByLabel("AI 行动速度").selectOption("1200");
    await expect(page.getByLabel("当前 AI 策略")).toHaveText(["长毛巨魔专属 v22", "沙奈朵专属 v5", "多龙专属 v9", "N 索罗亚克专属 v3", "电电虫专属 v1"][index]);
    const id = await page.evaluate(() => localStorage.getItem("ptcg-match"));
    const read = async () => (await page.request.get(`/api/battle/matches/${id}`)).json();
    for (let step = 0; step < 18; step++) {
      await expect.poll(async () => { const v = await read(); return !!v.decision || v.done || v.phase === "playing"; }, { timeout: 20000 }).toBe(true);
      const v = await read();
      expect(v.aiVersion).toBe("h2-visible-tempo-v4-app-v1");
      expect(v.observation.opponent.hand).toBeNull();
      if (v.phase === "playing" || v.done) break;
      if (v.decision.kind === "selection") {
        for (let k = 0; k < v.decision.min; k++) await page.locator(".decision-candidates button").nth(k).click();
        await page.getByRole("button", { name: `确认选择 ${v.decision.min} 张`, exact: true }).click();
      } else await page.locator(".decision-options button").first().click();
      await expect.poll(async () => (await read()).stateVersion).toBeGreaterThan(v.stateVersion);
    }
    expect((await read()).phase).toBe("playing");
    await page.reload();
    await expect(page.getByLabel("我的场面")).toBeVisible();
    await page.screenshot({ path: `../../var/browser-artifacts/h2-five-expert-${index + 1}.png`, fullPage: true });
    await page.getByRole("button", { name: "认输", exact: true }).click();
    await page.getByRole("button", { name: "确认认输", exact: true }).click();
    await expect(page.locator(".battle-result")).toContainText("你已认输");
    expect(errors).toEqual([]);
  });
}
