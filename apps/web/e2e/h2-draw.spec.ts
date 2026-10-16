import { test, expect } from "@playwright/test";

test("replayed simultaneous ending is visibly a draw, not a win or unknown result", async ({ page }) => {
  const session = await (await page.request.post("/api/battle/session", { data: {} })).json();
  const preset = session.presets[2].id;
  const revision = await (await page.request.post(`/api/battle/presets/${preset}/revision`, { data: {} })).json();
  const view = await (await page.request.post("/api/battle/matches", { data: {
    requestId: "draw-ui-fixture", revisionId: revision.id, opponent: preset,
  } })).json();
  // Controlled presentation fixture; adjudication boundaries are covered in tests/simulation/test_results.py.
  Object.assign(view, { matchId: "simultaneous-draw-fixture", status: "finished", done: true, decision: null, winner: null,
    result: { reason: "simultaneous_draw", conditions: ["equal_simultaneous_win_conditions"], outcome: "draw" } });
  view.observation.self.prize_count = view.observation.opponent.prize_count = 0;
  await page.addInitScript(() => localStorage.setItem("ptcg-match", "simultaneous-draw-fixture"));
  await page.route("**/api/battle/matches/simultaneous-draw-fixture", route => route.fulfill({ json: view }));
  await page.goto("/#battle");
  await expect(page.locator(".battle-result")).toContainText("平局");
  await expect(page.locator(".battle-result")).toContainText("双方同时满足相同数量的胜利条件");
  await expect(page.locator(".battle-result")).not.toContainText("AI 获胜");
  await page.screenshot({ path: "../../var/browser-artifacts/h2-simultaneous-draw.png", fullPage: true });
});
