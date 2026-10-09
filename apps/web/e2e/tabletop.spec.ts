import { test, expect } from "@playwright/test";
import path from "node:path";
const out = path.resolve(import.meta.dirname, "../../../var/browser-artifacts/simulation");

test("custom revisions on both sides start a real match with tabletop and responsive zones", async ({
  page,
}) => {
  test.setTimeout(90000);
  const meta = await (await page.request.get("/api/meta")).json();
  const ids: string[] = [];
  for (let i = 0; i < 2; i++) {
    const entries = structuredClone(meta.templates[i].entries);
    const energy =
      entries.find((e: any) => e.printingId.includes("ENERGY")) ||
      entries[entries.length - 1];
    const other = entries.find((e: any) => e !== energy && e.quantity > 1);
    other.quantity--;
    energy.quantity++;
    const deck = await (
      await page.request.post("/api/decks", {
        data: { name: `P3 自由组合 ${i}`, entries },
      })
    ).json();
    const res = await page.request.post(`/api/decks/${deck.id}/revisions`, {
      data: { expectedVersion: deck.version },
    });
    expect(res.ok()).toBeTruthy();
    ids.push((await res.json()).id);
  }
  await page.goto("/#battle");
  await page.getByLabel("对战卡组版本").selectOption(ids[0]);
  await page
    .getByLabel("AI 卡组", { exact: true })
    .selectOption(`revision:${ids[1]}`);
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("我的场面")).toBeVisible();
  // Complete setup through the same server-issued choices as real players.
  for (let step = 0; step < 10; step++) {
    const id = await page.evaluate(() => localStorage.getItem("ptcg-match"));
    await expect
      .poll(async () => {
        const v = await (
          await page.request.get(`/api/battle/matches/${id}`)
        ).json();
        return !!v.decision || v.done;
      }, { timeout: 45000 })
      .toBe(true);
    const v = await (
      await page.request.get(`/api/battle/matches/${id}`)
    ).json();
    if (v.phase === "playing" || v.done) break;
    if (v.decision.kind === "selection") {
      for (let j = 0; j < v.decision.min; j++)
        await page.locator(".decision-candidates button").nth(j).click();
      await page
        .getByRole("button", {
          name: `确认选择 ${v.decision.min} 张`,
          exact: true,
        })
        .click();
    } else await page.locator(".decision-options button").first().click();
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`/api/battle/matches/${id}`)).json())
            .stateVersion,
      )
      .toBeGreaterThan(v.stateVersion);
  }
  await expect(page.locator(".tabletop-card").first()).toBeVisible();
  await page.locator(".tabletop-card").first().click();
  await expect(page.locator(".tabletop-dialog")).toBeVisible();
  await page.getByLabel("关闭卡牌").click();
  for (const [w, h] of [
    [1440, 900],
    [1280, 720],
    [1024, 768],
    [390, 844],
  ]) {
    await page.setViewportSize({ width: w, height: h });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: path.join(out, `tabletop-${w}.png`),
      fullPage: true,
    });
  }
});

test("dragging an energy card submits exactly the server-issued target action", async ({
  page,
}) => {
  const own = {
    id: "player1",
    turnFlags: {itemsBlocked: true, attacksBlocked: true},
    active: [
      { name: "Gholdengo ex", hp: 260, maximumHp: 360, tool: ["Bravery Charm"], ref: "v7:self:active:0", energy: [], poisoned: true, poisonDamage: 30, limitedAttacks: ["Make It Rain"], attackProtection: { damage: true, effects: true }, abilitiesSuppressed: true, attackPowerReduction: 30, retaliationCounters: 60 },
    ],
    bench: [],
    hand: [{ name: "Metal Energy", ref: "v7:self:hand:0" }],
    hand_count: 1,
    deck_count: 20,
    prize_count: 6,
    discard: [],
    lost_zone: [],
  };
  const view: any = {
    matchId: "tabletop-fixture",
    stateVersion: 7,
    status: "playing",
    done: false,
    winner: null,
    phase: "playing",
    events: [],
    observation: {
      self: own,
      opponent: {
        ...own,
        id: "player2",
        active: [],
        hand: null,
        hand_count: 4,
      },
      stadium: [],
      turn: "player1",
      turn_number: 3,
      termination_reason: null,
    },
    decision: {
      id: "d7",
      kind: "options",
      options: [
        {
          id: "d7:o3",
          actionType: "AttachEnergyAction",
          source: "Metal Energy",
          sourceRef: "v7:self:hand:0",
          target: "Gholdengo ex",
          targetRef: "v7:self:active:0",
        },
      ],
    },
  };
  await page.addInitScript(() =>
    localStorage.setItem("ptcg-match", "tabletop-fixture"),
  );
  await page.route("**/api/battle/matches/tabletop-fixture", (r) =>
    r.fulfill({ json: view }),
  );
  const uploadedArtwork = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=";
  Object.assign(own.active[0], { id: "PAR-139" });
  await page.route("**/api/battle/session", async (r) => {
    const response = await r.fetch();
    const payload = await response.json();
    const original = payload.cards.find((card: any) => card.engineId === "PAR-139");
    expect(original).toBeTruthy();
    payload.cards.push({ ...original, image: { url: uploadedArtwork, label: "用户上传卡图", userUploaded: true } });
    payload.cards.push({ ...original, image: { url: null, label: "暂无卡图" } });
    payload.cards.push({ ...original, engineId: "fixture-other", image: { url: "/different-printing.jpg", userUploaded: true } });
    return r.fulfill({ response, json: payload });
  });
  const commands: any[] = [];
  await page.route("**/api/battle/matches/tabletop-fixture/commands", (r) => {
    commands.push(r.request().postDataJSON());
    return r.fulfill({
      json: {
        receipt: { accepted: true },
        view: { ...view, stateVersion: 8, done: true, decision: null },
      },
    });
  });
  await page.goto("/#battle");
  const source = page
    .getByLabel("我的手牌", { exact: true })
    .locator(".tabletop-card");
  const target = page
    .getByLabel("我的场面", { exact: true })
    .locator(".tabletop-active .tabletop-slot");
  await source.dragTo(target);
  await expect(target.locator(".tabletop-card img")).toHaveAttribute("src", uploadedArtwork);
  await expect(target.getByText("剩余 HP 260 / 360", {exact:true})).toBeVisible();
  await expect(target.getByText("特性已消除", {exact:true})).toBeVisible();
  await expect(target.getByText("招式伤害 -30", {exact:true})).toBeVisible();
  await expect(target.getByText("受击反击 6 个指示物", {exact:true})).toBeVisible();
  await expect(page.getByLabel("我的场面", {exact:true}).getByText("本回合无法使用招式")).toBeVisible();
  await expect(page.getByText("指定招式受限", {exact:true})).toBeVisible();
  await expect(page.getByText("招式伤害防护", {exact:true})).toBeVisible();
  await expect(page.getByText("招式效果防护", {exact:true})).toBeVisible();
  await expect(page.getByText("中毒 · 检查伤害 30", {exact:true})).toBeVisible();
  await expect(page.getByLabel("我的场面", {exact:true}).getByText("物品使用受限")).toBeVisible();
  await expect(target.locator(".tabletop-tool")).toContainText("勇气护符");
  const hp = await target.locator(".battle-hp").boundingBox();
  const tool = await target.locator(".tabletop-tool").boundingBox();
  expect(hp!.y + hp!.height).toBeLessThanOrEqual(tool!.y);
  await target.locator(".card-state-panel").click();
  await expect(page.getByLabel("卡牌当前状态")).toContainText("剩余 HP 260 / 360");
  await expect(page.getByLabel("卡牌当前状态")).toContainText("勇气护符");
  await page.getByLabel("关闭卡牌").click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0].choice).toEqual({ optionId: "d7:o3" });
  expect(commands[0].expectedStateVersion).toBe(7);
  expect(commands[0].decisionId).toBe("d7");
});
