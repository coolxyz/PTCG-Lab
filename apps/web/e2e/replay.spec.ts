import { test, expect } from "@playwright/test";

async function replayFixture(page: any) {
  const side = {
    active: [{ name: "Gholdengo ex", hp: 260, ref: "v0:self:active:0" }],
    bench: [],
    hand: [],
    hand_count: 0,
    deck_count: 20,
    prize_count: 6,
    discard: [],
    lost_zone: [],
  };
  const frames = Array.from({ length: 5 }, (_, seq) => ({
    seq,
    view: {
      matchId: "replay-fixture",
      stateVersion: seq,
      status: seq === 4 ? "finished" : "playing",
      done: seq === 4,
      winner: seq === 4 ? "PLAYER1" : null,
      phase: seq === 0 ? "active" : "playing",
      decision: null,
      observation: {
        self: {
          ...side,
          active: [{ ...side.active[0], ref: `v${seq}:self:active:0` }],
        },
        opponent: { ...side, hand: null },
        stadium: [],
        turn: seq < 3 ? "PLAYER1" : "PLAYER2",
        turn_number: seq === 0 ? 0 : seq < 3 ? 1 : 2,
        termination_reason: null,
      },
    },
    event: {
      seq,
      actor: "you",
      text: `动作 ${seq}`,
      sourceLocation: "self:active:0",
      actionType: "AttackAction",
    },
  }));
  const calls: number[] = [];
  const writes: string[] = [];
  let failure = false;
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "replay-fixture"),
  );
  await page.route(
    /\/api\/battle\/matches\/replay-fixture(?:[/?].*)?$/,
    async (route: any) => {
      const url = new URL(route.request().url());
      if (route.request().method() !== "GET") {
        writes.push(url.pathname);
        return route.fulfill({
          status: 400,
          json: { message: "Unexpected write" },
        });
      }
      if (url.pathname.endsWith("/timeline"))
        return route.fulfill({
          json: {
            matchId: "replay-fixture",
            lastSeq: 4,
            checkpoints: [
              { seq: 0, turn: 0, player: null, phase: "setup" },
              { seq: 1, turn: 1, player: "PLAYER1", phase: "playing" },
              { seq: 3, turn: 2, player: "PLAYER2", phase: "playing" },
            ],
          },
        });
      if (url.pathname.endsWith("/replay")) {
        const seq = Number(url.searchParams.get("after")) + 1;
        calls.push(seq);
        if (failure)
          return route.fulfill({
            status: 503,
            json: { message: "回放暂时不可用" },
          });
        return route.fulfill({ json: { frames: [frames[seq]], lastSeq: 4 } });
      }
      return route.fulfill({ json: frames[4].view });
    },
  );
  await page.goto("/#battle");
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toHaveValue("0");
  return {
    calls,
    writes,
    fail: () => {
      failure = true;
    },
  };
}

test("replay turn navigation pauses playback, resets effects, and never submits actions", async ({
  page,
}) => {
  const { calls, writes } = await replayFixture(page);
  await expect(
    page.getByRole("button", { name: "上一回合", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "下一回合", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toHaveValue("1");
  await page.getByLabel("回放回合").selectOption("3");
  await expect(page.getByLabel("回放步数")).toHaveValue("3");
  await expect(page.locator(".effect-ring")).toHaveCount(0);
  await page.getByRole("button", { name: "上一回合", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toHaveValue("1");
  await page.getByRole("button", { name: "播放回放", exact: true }).click();
  await page.getByLabel("回放回合").selectOption("0");
  await expect(
    page.getByRole("button", { name: "播放回放", exact: true }),
  ).toBeVisible();
  await page.getByLabel("回放速度").selectOption("4");
  await page.getByRole("button", { name: "播放回放", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toHaveValue("4");
  await expect(
    page.getByRole("button", { name: "播放回放", exact: true }),
  ).toBeDisabled();
  expect(calls.slice(-4)).toEqual([1, 2, 3, 4]);
  expect(writes).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../var/replay-mobile.png",
    fullPage: true,
  });
});

test("failed autoplay stops without retry loop and reopening stays paused", async ({
  page,
}) => {
  const fixture = await replayFixture(page);
  fixture.fail();
  await page.getByLabel("回放速度").selectOption("4");
  await page.getByRole("button", { name: "播放回放", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("回放暂时不可用");
  await expect(
    page.getByRole("button", { name: "播放回放", exact: true }),
  ).toBeDisabled();
  await page.waitForTimeout(800); // More than three playback ticks: no automatic retry.
  expect(fixture.calls).toEqual([0, 1]);
  expect(fixture.writes).toEqual([]);
  await page.getByRole("button", { name: "返回当前局面", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toHaveCount(0);
});

