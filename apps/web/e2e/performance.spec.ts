import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test("U3 local response and animation frame budget", async ({ page }) => {
  const root = path.resolve(import.meta.dirname, "../../..");
  const budget = JSON.parse(
    fs.readFileSync(
      path.join(root, "data/simulation/u3-performance-budget.json"),
      "utf8",
    ),
  );
  await page.setViewportSize({ width: 1440, height: 900 });
  const side = {
    active: [],
    bench: [],
    hand: [{ name: "Gimmighoul", ref: "v0:self:hand:0" }],
    hand_count: 1,
    deck_count: 30,
    prize_count: 6,
    discard: [],
    lost_zone: [],
  };
  let version = 0;
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "performance-fixture"),
  );
  await page.route("**/api/battle/matches/performance-fixture", (route) =>
    route.fulfill({
      json: {
        matchId: "performance-fixture",
        stateVersion: version,
        done: true,
        status: "finished",
        phase: "playing",
        winner: "PLAYER1",
        decision: null,
        events: Array.from({ length: version }, (_, i) => ({
          seq: i + 1,
          actor: "you",
          text: "抽牌",
          effects: Array.from({ length: 8 }, () => ({
            kind: "move",
            side: "opponent",
            fromZone: "deck",
            zone: "hand",
            cardName: null,
            label: "抽牌",
            before: 0,
            after: 1,
          })),
        })),
        observation: {
          self: side,
          opponent: { ...side, hand: null },
          stadium: [],
          turn: "PLAYER1",
          turn_number: 2,
          termination_reason: null,
        },
      },
    }),
  );
  await page.goto("/#battle");
  await expect(page.locator(".tabletop-hand .tabletop-card")).toBeVisible();
  version = 8;
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.locator(".card-flight")).toHaveCount(8);
  const rendering = page.evaluate(async () => {
    const intervals: number[] = [];
    let previous = 0,
      peak = 0;
    await new Promise<void>((resolve) => {
      const tick = (now: number) => {
        if (previous) intervals.push(now - previous);
        previous = now;
        peak = Math.max(peak, document.querySelectorAll(".card-flight").length);
        if (intervals.length >= 120) resolve();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    return { intervals, peak };
  });
  const interactions: number[] = [];
  for (let i = 0; i < 20; i++) {
    interactions.push(
      await page.evaluate(async () => {
        const began = performance.now();
        document
          .querySelector<HTMLButtonElement>(".tabletop-hand .tabletop-card")!
          .click();
        await new Promise<void>((resolve) => {
          const tick = () =>
            document.querySelector("dialog[open]")
              ? requestAnimationFrame(() => resolve())
              : requestAnimationFrame(tick);
          requestAnimationFrame(tick);
        });
        return performance.now() - began;
      }),
    );
    await page.getByRole("button", { name: "关闭卡牌", exact: true }).click();
  }
  const { intervals, peak } = await rendering;
  const p95 = (values: number[]) =>
    [...values].sort((a, b) => a - b)[Math.ceil(values.length * 0.95) - 1];
  const metrics = {
    schema: "u3-local-ui-measurement-v1",
    budget,
    interactionSamples: interactions.length,
    frameSamples: intervals.length,
    cardInteractionP95Ms: p95(interactions),
    animationFrameIntervalP95Ms: p95(intervals),
    maxVisibleFlyingCards: peak,
    interactions,
    intervals,
  };
  fs.writeFileSync(
    path.join(root, "var/u3-performance.json"),
    JSON.stringify(metrics, null, 2),
  );
  expect(metrics.cardInteractionP95Ms).toBeLessThanOrEqual(
    budget.cardInteractionP95Ms,
  );
  expect(metrics.animationFrameIntervalP95Ms).toBeLessThanOrEqual(
    budget.animationFrameIntervalP95Ms,
  );
  expect(peak).toBeLessThanOrEqual(budget.maxVisibleFlyingCards);
  await page.getByRole("button", { name: "跳过反馈" }).click();
  await expect(page.locator(".card-flight")).toHaveCount(0);
});
