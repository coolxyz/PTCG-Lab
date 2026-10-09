import { test, expect } from "@playwright/test";

// Synthetic player-view fixture: no production replay or hidden card identities.
test("P3 shows pending knockout without negative HP and explains prize victory", async ({
  page,
}) => {
  const side = (id: string) => ({
    id,
    active: [],
    bench: [],
    hand: id === "player1" ? [] : null,
    hand_count: 0,
    deck_count: 10,
    prize_count: 6,
    discard: [],
    lost_zone: [],
    turnFlags: {},
  });
  const view: any = {
    matchId: "p3-ui-fixture",
    stateVersion: 1,
    status: "playing",
    done: false,
    winner: null,
    phase: "playing",
    events: [],
    setupEvents: [],
    publicReveals: [],
    decision: {
      id: "d1",
      kind: "selection",
      source: "Dragapult ex",
      hidden: false,
      min: 0,
      max: 0,
      candidates: [],
      zones: [],
      instruction: "继续分配伤害指示物",
    },
    observation: {
      self: side("player1"),
      opponent: side("player2"),
      turn: "player1",
      turn_number: 4,
      stadium: [],
      termination_reason: null,
    },
  };
  view.observation.opponent.active = [
    { name: "Gholdengo ex", hp: -160, energy: [], tool: [], attacks: [] },
  ];
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "p3-ui-fixture"),
  );
  await page.route("**/api/battle/matches/p3-ui-fixture", (route) =>
    route.fulfill({ json: view }),
  );
  await page.goto("/#battle");
  await expect(page.locator(".battle-hp")).toContainText("剩余 HP 0");
  await expect(page.locator(".battle-hp")).toContainText("等待本次效果结算");
  await expect(page.locator(".battle-hp")).not.toContainText("-160");
  view.done = true;
  view.status = "finished";
  view.winner = "PLAYER1";
  view.decision = null;
  view.observation.self.prize_count = 0;
  view.observation.opponent.active = [];
  view.result = { reason: "prizes_taken", conditions: ["prizes_taken"] };
  await page.reload();
  await expect(page.locator(".battle-result")).toContainText(
    "已拿完全部奖赏卡",
  );
  await expect(page.locator(".battle-result")).toContainText(
    "剩余奖赏：你 0 / AI 6",
  );
});

test("Munkidori counter choices show distinct amounts and submit the selected option", async ({ page }) => {
  const side = { active: [], bench: [], hand: [], hand_count: 0, deck_count: 20, prize_count: 6, discard: [], lost_zone: [], turnFlags: {} };
  const view = {
    matchId: "counter-fixture", stateVersion: 114, status: "playing", done: false,
    winner: null, phase: "playing", events: [], setupEvents: [], publicReveals: [],
    observation: { self: side, opponent: { ...side, hand: null }, turn: "player1", turn_number: 8, stadium: [] },
    decision: { id: "d114", actor: "PLAYER1", kind: "options", options: [1, 2, 3].map((value, index) => ({ id: `d114:o${index}`, actionType: "NumberOption", value, label: `移动 ${value} 个伤害指示物（${value * 10} 点）` })) },
  };
  await page.addInitScript(() => localStorage.setItem("ptcg-match", "counter-fixture"));
  await page.route("**/api/battle/matches/counter-fixture", route => route.fulfill({ json: view }));
  let submitted: any;
  await page.route("**/api/battle/matches/counter-fixture/commands", async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { ...view, done: true, status: "finished", decision: null } });
  });
  await page.goto("/#battle");
  for (const n of [1, 2, 3]) await expect(page.getByRole("button", { name: `移动 ${n} 个伤害指示物（${n * 10} 点）`, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "移动 3 个伤害指示物（30 点）", exact: true }).click();
  await expect.poll(() => submitted?.choice?.optionId).toBe("d114:o2");
  expect(submitted.expectedStateVersion).toBe(114);
  expect(submitted.decisionId).toBe("d114");
});
