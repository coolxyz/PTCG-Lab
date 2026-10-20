import { test, expect } from "@playwright/test";

test("full deck browsing preserves selected refs and cannot select ineligible cards", async ({page}) => {
  const side = {active: [], bench: [], hand: [], hand_count: 0, deck_count: 4, prize_count: 6, discard: [], lost_zone: []};
  const energy = {id: "SVE-005", name: "Psychic Energy", superType: "ENERGY"};
  const ball = {id: "PAF-091", name: "Ultra Ball", superType: "TRAINER", trainerType: "ITEM"};
  const poke = {id: "fixture-pokemon", name: "Munkidori", superType: "POKEMON"};
  let view: any = {matchId: "deck-browser-fixture", stateVersion: 21, status: "playing", done: false, phase: "playing", winner: null, events: [],
    observation: {self: side, opponent: {...side, hand: null}, stadium: [], turn: "PLAYER1", turn_number: 3},
    decision: {id: "d21", actor: "PLAYER1", kind: "selection", source: "Earthen Vessel", min: 0, max: 2, hidden: false,
      candidates: [{ref:"d21:c0",card:energy},{ref:"d21:c1",card:energy}],
      deckInspection: {owner:"self",total:4,ordered:false,cards:[{card:energy,count:2},{card:ball,count:1},{card:poke,count:1}]} }};
  const commands: any[] = [];
  await page.addInitScript(() => localStorage.setItem("ptcg-match", "deck-browser-fixture"));
  await page.route("**/api/battle/matches/deck-browser-fixture", route => route.fulfill({json:view}));
  await page.route("**/api/battle/matches/deck-browser-fixture/commands", async route => {
    commands.push(route.request().postDataJSON());
    view={...view,done:true,decision:null}; await route.fulfill({json:{view}});
  });
  await page.goto("/#battle");
  const panel=page.getByRole("region",{name:"当前决策"});
  const selected=panel.locator(".decision-candidates button").first();
  await selected.click();
  const browser=panel.locator(".deck-browser");
  await browser.locator(":scope > summary").click();
  await expect(browser).toContainText("下方已选 1 张会保留");
  await expect(browser.getByRole("listitem")).toHaveCount(3);
  await browser.getByRole("button",{name:"能量",exact:true}).click();
  await expect(browser.getByRole("listitem")).toHaveCount(1);
  await browser.getByRole("button",{name:"全部",exact:true}).click();
  await browser.getByRole("searchbox").fill("Ultra Ball");
  await expect(browser.getByRole("listitem")).toHaveCount(1);
  await browser.locator("li summary").click();
  await expect(selected).toHaveAttribute("aria-pressed","true");
  await browser.getByRole("searchbox").fill("");
  await panel.screenshot({path:"../../var/browser-artifacts/deck-browser.png"});
  await browser.locator(":scope > summary").click();
  await expect(selected).toHaveAttribute("aria-pressed","true");
  await panel.getByRole("button",{name:"确认选择 1 张"}).click();
  await expect.poll(()=>commands.length).toBe(1);
  expect(commands[0].choice.selectedRefs).toEqual(["d21:c0"]);
});

test("match history names presets and custom revisions without mislabelling old decks", async ({page}) => {
  await page.route("**/api/battle/session", route => route.fulfill({json: {
    opponents: [{id: "h2-joltik-box", name: "电电虫 Box"}], presets: [],
  }}));
  await page.route("**/api/battle/revisions", route => route.fulfill({json: [
    {id: "custom", name: "我的测试卡组", number: 3, playable: true},
  ]}));
  await page.route("**/api/battle/matches", route => route.fulfill({json: [
    {id: "preset", opponent: "h2-joltik-box", status: "finished", seq: 10},
    {id: "custom", opponent: "revision:custom", status: "finished", seq: 20},
    {id: "removed", opponent: "revision:removed", status: "finished", seq: 30},
    {id: "legacy", opponent: "unknown-legacy", status: "finished", seq: 40},
  ]}));
  await page.goto("/#battle");
  const history = page.locator(".battle-history");
  await expect(history.getByRole("button")).toHaveCount(4);
  await expect(history).toContainText("电电虫 Box");
  await expect(history).toContainText("我的测试卡组 · 版本 3");
  await expect(history).toContainText("自定义 AI 卡组（原版本已移除）");
  await expect(history).toContainText("历史 AI 卡组");
  await expect(history).not.toContainText("赛富豪");
});
