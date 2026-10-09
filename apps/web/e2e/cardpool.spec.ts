import { test, expect } from "@playwright/test";

test("confirmed source exceptions become supported without the old pending notice", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("搜索卡牌").fill("CN:CHS:13927");
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page.locator(".card-tile .card-face").click();
  await expect(page.getByText("待核实例外：保留原始资料，暂不开放对战，不计入已支持卡牌。", {exact: true})).toHaveCount(0);
  await expect(page.getByText("效果已验证 · 有限组合", {exact: true})).toBeVisible();
});

test("face-up prizes remain visible after refresh without exposing facedown cards", async ({ page }) => {
  const side = { active: [], bench: [], hand: [], hand_count: 0, deck_count: 20,
    prize_count: 6, discard: [], lost_zone: [] };
  await page.addInitScript(() => localStorage.setItem("ptcg-match", "prize-fixture"));
  await page.route("**/api/battle/matches/prize-fixture", route => route.fulfill({ json: {
    matchId: "prize-fixture", stateVersion: 1, done: true, status: "finished",
    phase: "playing", winner: "PLAYER1", decision: null, events: [],
    observation: { self: side, opponent: { ...side, hand: null,
      faceUpPrizes: [{name: "Visible Prize", prizeIndex: 2}] }, stadium: [],
      turn: "PLAYER1", turn_number: 2, termination_reason: null }
  }}));
  await page.goto("/#battle");
  await expect(page.getByRole("button", {name: "正面奖赏 Visible Prize"})).toBeVisible();
  await expect(page.getByLabel("背面奖赏", {exact: true})).toHaveCount(11);
  await page.getByRole("button", {name: "刷新局面"}).click();
  await expect(page.getByRole("button", {name: "正面奖赏 Visible Prize"})).toBeVisible();
});

test("retreat restriction appears on the table and disappears after refresh", async ({ page }) => {
  let blocked = true;
  const side = { active: [], bench: [], hand: [], hand_count: 0, deck_count: 20,
    prize_count: 6, discard: [], lost_zone: [] };
  await page.addInitScript(() => localStorage.setItem("ptcg-match", "restriction-fixture"));
  await page.route("**/api/battle/matches/restriction-fixture", route => route.fulfill({ json: {
    matchId: "restriction-fixture", stateVersion: blocked ? 1 : 2, done: true,
    status: "finished", phase: "playing", winner: "PLAYER1", decision: null, events: [],
    observation: { self: { ...side, active: [{ name: "Bellsprout", hp: 60,
      ref: "v1:self:active:0", retreatBlocked: blocked, attackDamageReduction: blocked ? 30 : undefined }] }, opponent: { ...side, hand: null },
      stadium: [], turn: "PLAYER1", turn_number: 2, termination_reason: null }
  }}));
  await page.goto("/#battle");
  await expect(page.locator(".tabletop-condition")).toHaveText(["无法撤退", "招式伤害 −30"]);
  blocked = false;
  await page.getByRole("button", { name: "刷新局面" }).click();
  await expect(page.locator(".tabletop-condition")).toHaveCount(0);
});

test("GHIJ plain-rule deck and newly supported fighting energy enter practice", async ({ page, request }) => {
  const meta = await (await request.get("/api/meta")).json();
  expect(meta.battleScope.allowedMarks).toEqual(["G", "H", "I", "J"]);
  expect(meta.battleScope.basicEnergyTypes).toHaveLength(8);
  const created = await request.post("/api/decks", { data: { name: "GHIJ 玛沙那", entries: [
    { printingId: "CN:CSV7C:116", quantity: 4 }, { printingId: "CN:CSM2.1C:042", quantity: 56 }
  ] } });
  expect(created.ok()).toBeTruthy();
  const deck = await created.json();
  const saved = await request.post(`/api/decks/${deck.id}/revisions`, { data: { expectedVersion: deck.version } });
  expect(saved.ok()).toBeTruthy();
  await page.goto("/#battle");
  await expect(page.getByTestId("battle-pool-status")).toContainText("G/H/I/J");
  await page.getByLabel("对战卡组版本").selectOption((await saved.json()).id);
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
});

test("new preconstructed cards and unnumbered source records are searchable and collectable", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("搜索卡牌").fill("CN:CS4DaC:001");
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page.getByRole("button", { name: "查看 妙蛙花V 001", exact: true }).click();
  await expect(page.getByText("效果待验证 · 暂不可对战", { exact: true })).toBeVisible();
  await page.getByLabel("收藏数量").fill("2");
  await page.getByRole("button", { name: "保存收藏", exact: true }).click();
  await expect(page.getByText("未标注 2张", { exact: true })).toBeVisible();
  await page.getByLabel("关闭窗口").click();
  const cards = (await (await page.request.get("/api/cards", { params: { product: "CS3DC" } })).json()).cards;
  const prize = cards.find((card: any) => card.identityKind === "source-record");
  expect(prize).toBeTruthy();
  await page.getByLabel("搜索卡牌").fill(prize.printingId);
  await expect(page.locator(".card-tile")).toHaveCount(1);
  await page.locator(".card-tile .card-face").click();
  await expect(page.getByText(/此条目按来源区分/)).toBeVisible();
});

test("reprint deck can enter A1 practice and lobby describes the actual pool", async ({ page, request }) => {
  const meta = await (await request.get("/api/meta")).json();
  expect(meta.verifiedCount).toBeGreaterThanOrEqual(264);
  const template = structuredClone(meta.templates[0]);
  template.entries.find((entry: any) => entry.printingId === "CN:CSVM2cC:007").printingId = "CN:CSV4C:089";
  const created = await request.post("/api/decks", { data: { name: "P4 重印对局", entries: template.entries } });
  expect(created.ok()).toBeTruthy();
  const deck = await created.json();
  const saved = await request.post(`/api/decks/${deck.id}/revisions`, { data: { expectedVersion: deck.version } });
  expect(saved.ok()).toBeTruthy();
  await page.goto("/#battle");
  await expect(page.getByTestId("battle-pool-status")).toContainText(String(meta.verifiedCount));
  await expect(page.getByTestId("battle-pool-status")).toContainText(`待核实例外 ${meta.pendingSourceExceptionCount} 个`);
  await page.getByLabel("对战卡组版本").selectOption((await saved.json()).id);
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
});
