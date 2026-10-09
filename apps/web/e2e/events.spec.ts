import { test, expect } from "@playwright/test";

test("ordered feedback consumes public events once and skips recovery gaps", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-09-30T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-09-30T12:00:00Z"));
  const side = {
    active: [],
    bench: [],
    hand: [],
    hand_count: 0,
    deck_count: 30,
    prize_count: 6,
    discard: [],
    lost_zone: [],
  };
  let version = 0;
  let events: any[] = [];
  let writes = 0;
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "event-fixture"),
  );
  await page.route("**/api/battle/matches/event-fixture", (route) => {
    if (route.request().method() !== "GET") writes++;
    return route.fulfill({
      json: {
        matchId: "event-fixture",
        stateVersion: version,
        done: true,
        status: "finished",
        phase: "playing",
        winner: "PLAYER1",
        decision: null,
        events,
        observation: {
          self: side,
          opponent: { ...side, hand: null },
          stadium: [],
          turn: "PLAYER1",
          turn_number: 2,
          termination_reason: null,
        },
      },
    });
  });
  await page.goto("/#battle");
  await expect(page.getByLabel("我的场面")).toBeVisible();
  await expect(page.getByLabel("结算反馈")).toHaveCount(0);
  const event = (seq: number) => ({
    seq,
    actor: "you",
    text: `公开动作${seq}`,
    coins: ["heads", "tails", "heads"],
    effects: [
      { kind: "count", side: "opponent", zone: "hand", before: 3, after: 4 },
    ],
  });
  version = 2;
  events = [event(1), event(2)];
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.getByLabel("结算反馈")).toContainText("公开动作1");
  await expect(page.getByLabel("牌桌当前动作")).toContainText("公开动作2");
  await expect(page.getByLabel("结算反馈")).toContainText("AI · 手牌：3 → 4");
  await expect(page.getByLabel("硬币投掷结果")).toContainText("第 1 / 3 次");
  await page.getByRole("button", {name:"显示全部结果"}).click();
  await expect(page.getByLabel("硬币投掷结果")).toContainText("3. 正面");
  await page.clock.runFor(300);
  await page.screenshot({
    path: "../../var/p33-events-desktop.png",
    fullPage: true,
  });
  await page.clock.runFor(460);
  await expect(page.getByLabel("结算反馈")).toContainText("公开动作2");
  await page.getByRole("button", { name: "跳过反馈" }).click();
  await expect(page.getByLabel("结算反馈")).toHaveCount(0);
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.getByLabel("结算反馈")).toHaveCount(0);
  version = 6;
  events = [event(6)];
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.getByLabel("结算反馈")).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  version = 7;
  events = [event(7)];
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.getByLabel("结算反馈")).toHaveCount(0);
  expect(writes).toBe(0);
  await expect(page.getByLabel("牌桌当前动作")).toContainText("公开动作7");
  await expect(page.getByLabel("牌桌当前动作")).toContainText("AI · 手牌 3 → 4");
});

test("card movements use public faces or anonymous backs and adapt to four sizes", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-09-30T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-09-30T12:00:00Z"));
  const side = {
    active: [],
    bench: [],
    hand: [],
    hand_count: 3,
    deck_count: 30,
    prize_count: 6,
    discard: [],
    lost_zone: [],
  };
  let version = 0;
  let writes = 0;
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "motion-fixture"),
  );
  await page.route("**/api/battle/matches/motion-fixture", (route) => {
    if (route.request().method() !== "GET") writes++;
    return route.fulfill({
      json: {
        matchId: "motion-fixture",
        stateVersion: version,
        done: true,
        status: "finished",
        phase: "playing",
        winner: "PLAYER1",
        decision: null,
        events: version
          ? [
              {
                seq: version,
                actor: "you",
                text: "抽牌与上场",
                effects: [
                  {
                    kind: "move",
                    side: "opponent",
                    fromZone: "deck",
                    zone: "hand",
                    cardName: null,
                    label: "抽牌",
                    before: 0,
                    after: 1,
                  },
                  {
                    kind: "move",
                    side: "self",
                    fromZone: "hand",
                    zone: "bench",
                    cardName: "Gimmighoul",
                    label: "上场",
                    before: 0,
                    after: 1,
                  },
                ],
              },
            ]
          : [],
        observation: {
          self: side,
          opponent: { ...side, hand: null },
          stadium: [],
          turn: "PLAYER1",
          turn_number: 2,
          termination_reason: null,
        },
      },
    });
  });
  await page.goto("/#battle");
  await expect(page.getByLabel("我的场面")).toBeVisible();
  for (const [width, height] of [
    [1440, 900],
    [1280, 720],
    [1024, 768],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    version++;
    await page.getByRole("button", { name: "刷新局面" }).click();
    await expect(page.locator(".card-flight")).toHaveCount(2);
    await expect(page.locator(".card-flight.cardback")).toHaveCount(1);
    await expect(page.locator(".card-flight.cardback img")).toHaveCount(0);
    await expect(page.getByLabel("结算反馈")).toContainText("卡背");
    await page.clock.runFor(180);
    await page.screenshot({
      path: `../../var/browser-artifacts/simulation3/motion-${width}.png`,
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.getByRole("button", { name: "跳过反馈" }).click();
    await expect(page.locator(".card-flight")).toHaveCount(0);
  }
  version++;
  await page.getByRole("button", { name: "刷新局面" }).click();
  await page.getByRole("button", { name: "动画速度" }).click();
  await page.clock.runFor(500);
  await expect(page.locator(".card-flight")).toHaveCount(0);
  expect(writes).toBe(0);
});
