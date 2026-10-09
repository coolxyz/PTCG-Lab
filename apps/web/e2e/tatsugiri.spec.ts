import { test, expect } from "@playwright/test";

for (const hasSupporter of [true, false]) {
  test(`Tatsugiri inspection identifies the exact six and their Supporters (${hasSupporter})`, async ({ page }) => {
    const side = { active: [], bench: [], hand: [], hand_count: 5, deck_count: 41, prize_count: 6, discard: [], lost_zone: [] };
    const cards = [
      {name: "Luminous Energy", superType: "ENERGY"},
      hasSupporter ? {name: "Iono", trainerType: "SUPPORTER"} : {name: "Psychic Energy", superType: "ENERGY"},
      {name: "Exp. Share", trainerType: "TOOL"},
      {name: "Nest Ball", trainerType: "ITEM"},
      {name: "Earthen Vessel", trainerType: "ITEM"},
      {name: "Ultra Ball", trainerType: "ITEM"},
    ];
    let view: any = {
      matchId: "tatsugiri-fixture", stateVersion: 35, status: "playing", done: false,
      phase: "playing", winner: null, events: [],
      observation: { self: side, opponent: {...side, hand: null}, stadium: [], turn: "PLAYER1", turn_number: 3 },
      decision: { id: "d35", actor: "PLAYER1", kind: "selection", source: "Tatsugiri", min: 0, max: 0, hidden: false,
        candidates: cards.map((card, i) => ({ref: `d35:c${i}`, card})), instruction: "米立龙：查看牌库顶 6 张牌。" },
    };
    const commands: any[] = [];
    await page.addInitScript(() => localStorage.setItem("ptcg-match", "tatsugiri-fixture"));
    await page.route("**/api/battle/matches/tatsugiri-fixture", route => route.fulfill({json: view}));
    await page.route("**/api/battle/matches/tatsugiri-fixture/commands", async route => {
      commands.push(route.request().postDataJSON());
      view = {...view, stateVersion: view.stateVersion + 1,
        decision: hasSupporter && commands.length === 1
          ? {...view.decision, id: "d36", max: 1, candidates: [{ref: "d36:c0", card: cards[1]}]}
          : null,
        done: !hasSupporter || commands.length === 2,
      };
      await route.fulfill({json: {view}});
    });
    await page.goto("/#battle");
    const panel = page.getByRole("region", {name: "当前决策"});
    await expect(panel.getByRole("heading", {name: "查看卡牌"})).toBeVisible();
    await expect(panel.getByRole("list", {name: "本次查看的卡牌"}).getByRole("listitem")).toHaveCount(6);
    await expect(panel).not.toContainText("请选择 0 张");
    await expect(panel.getByRole("status")).toContainText(hasSupporter ? "这 6 张中有 1 张支援者：奇树" : "这 6 张中有 0 张支援者");
    if (hasSupporter) {
      await expect(panel.locator(".inspection-supporter")).toHaveText("奇树支援者");
      await panel.screenshot({path: "../../var/tatsugiri-inspection.png"});
    } else {
      await expect(panel).not.toContainText("奇树");
    }
    await panel.getByRole("button", {name: "已查看，继续结算"}).click();
    await expect.poll(() => commands.length).toBe(1);
    expect(commands[0].choice.selectedRefs).toEqual([]);
    if (hasSupporter) {
      await expect(panel).toContainText("仅从刚才查看的卡牌中选择支援者");
      await expect(panel.locator(".decision-candidates button")).toHaveCount(1);
      await panel.getByRole("button", {name: "奇树", exact: true}).click();
      await panel.getByRole("button", {name: "确认选择 1 张"}).click();
      await expect.poll(() => commands.length).toBe(2);
      expect(commands[1].choice.selectedRefs).toEqual(["d36:c0"]);
    }
  });
}
