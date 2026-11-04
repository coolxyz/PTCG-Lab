import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const corpus = JSON.parse(
  readFileSync(
    new URL("../../../tests/synthetic/protocol/v2.json", import.meta.url),
    "utf8",
  ),
);
const initial = corpus.find(
  (r: any) => r.kind === "MatchSnapshot" && r.value.viewerSeat === "PLAYER1",
).value;
const replay = corpus.find((r: any) => r.kind === "ReplayPage").value;

async function fixture(page: Page, longLabels = false, battleFlow = false) {
  await page.addInitScript(
    ({ initial, replay, longLabels, battleFlow }) => {
      const profile = "p" + "1".repeat(24),
        mid = "a".repeat(32),
        rid = "b".repeat(32),
        revision = "c".repeat(32);
      const rule = longLabels
        ? "rules-core-v2-" + "f".repeat(64)
        : "synthetic-rules-1";
      const card = {
        printingId: "CN:TEST:001",
        cnName: "合成训练卡一",
        engineId: "synthetic-basic-1",
        category: "POKEMON",
        hasArtwork: false,
        englishName: "Fixture Card",
        chineseDetails: {
          sections: [
            {
              kind: "招式",
              name: "合成招式",
              text: "合成资料，仅用于界面测试。",
            },
          ],
        },
      };
      const identity = (id: string, name: string, ref?: string) => ({
        cardId: id,
        name,
        presentation: { name, id, ...(ref ? { ref } : {}) },
      });
      const state = () =>
        JSON.parse(localStorage.getItem("native-fixture-online") || "null") || {
          server: null,
          identity: null,
          room: null,
          view: null,
        };
      const save = (s: any) =>
        localStorage.setItem("native-fixture-online", JSON.stringify(s));
      (window as any).nativeCalls = [];
      const snapshot = () => {
        const s = state();
        if (!s.view) {
          s.view = structuredClone(initial);
          Object.assign(s.view, { matchId: mid, streamCursor: "0".repeat(32) });
          if (battleFlow) {
            const v = s.view;
            const ref = `v${v.stateVersion}:self:hand:0`;
            v.observation.self.hand = [
              identity("item", "合成检索物品", ref),
              ...Array.from({ length: 7 }, (_, i) =>
                identity(
                  `hand-${i}`,
                  `手牌 ${i + 2}`,
                  `v${v.stateVersion}:self:hand:${i + 1}`,
                ),
              ),
            ];
            v.observation.self.handCount = v.observation.self.hand.length;
            for (const side of ["self", "opponent"]) {
              const player = v.observation[side];
              player.active = [
                {
                  ref: `v${v.stateVersion}:${side}:active:0`,
                  card: identity(
                    side,
                    side === "self" ? "我方战斗宝可梦" : "对方战斗宝可梦",
                  ),
                  hp: side === "self" ? 80 : 110,
                  maximumHp: 120,
                  energy: ["PSYCHIC"],
                  conditions: ["POISONED"],
                },
              ];
              player.bench = Array.from(
                { length: side === "self" ? 5 : 4 },
                (_, i) => ({
                  ref: `v${v.stateVersion}:${side}:bench:${i}`,
                  card: identity(`bench-${i}`, `备战宝可梦 ${i + 1}`),
                  hp: 60,
                  maximumHp: 60,
                }),
              );
            }
            const ownActive = v.observation.self.active[0];
            ownActive.card.presentation.retreatCost = [];
            const opponentActive = v.observation.opponent.active[0];
            opponentActive.card.presentation.retreatCost = [
              "COLORLESS",
              "COLORLESS",
            ];
            opponentActive.energy = ["WATER", "WATER"];
            opponentActive.tools = [identity("tool-active", "合成战斗道具")];
            Object.assign(v.observation.opponent.bench[0].card.presentation, {
              retreatCost: ["COLORLESS"],
              energy: ["lightning"],
              tool: ["合成备战道具"],
            });
            v.decision.options.unshift({
              id: "d2:item",
              actionType: "UseItemAction",
              source: "合成检索物品",
              sourceId: "item",
              sourceRef: ref,
            });
            v.decision.options.unshift({
              id: "d2:attack",
              actionType: "AttackAction",
              source: "我方战斗宝可梦",
              sourceRef: v.observation.self.active[0].ref,
              attack: { name: "合成招式", damage: 20 },
            });
          }
          save(s);
        }
        return s.view;
      };
      const pageReplay = () => {
        const p = structuredClone(replay),
          v = snapshot();
        p.matchId = mid;
        for (const k of Object.keys(p.frames[0]))
          if (k in v) p.frames[0][k] = v[k];
        return p;
      };
      window.__TAURI__ = {
        core: {
          invoke: async (command: string, args: any) => {
            const s = state(),
              r = args.request;
            if (command === "mobile_artwork")
              throw { code: "IMAGE_MISSING", message: "无合成卡图" };
            if (command === "mobile_request") {
              switch (r.op) {
                case "capabilities":
                  return {
                    catalogVersion: "fixture",
                    implemented: {
                      localPractice: false,
                      localRuleVerification: false,
                      onlineBattle: true,
                    },
                  };
                case "catalog":
                  return { cards: [card], total: 1 };
                case "collection":
                  return { version: 0, entries: [] };
                case "decks":
                  return [
                    {
                      id: rid,
                      name: "手机测试卡组",
                      entries: [{ printingId: card.printingId, quantity: 60 }],
                      version: 1,
                    },
                  ];
                case "artwork-settings":
                  return {
                    settings: {
                      source: "github-raw",
                      baseUrl: "",
                      capacityMiB: 512,
                    },
                    cachedBytes: 0,
                  };
                default:
                  throw { code: "INVALID_REQUEST" };
              }
            }
            if (command !== "mobile_online") throw { code: "INVALID_REQUEST" };
            (window as any).nativeCalls.push(structuredClone(r));
            switch (r.op) {
              case "configuration":
                return s.server;
              case "configure":
                s.server = { origin: r.origin, caPem: r.caPem };
                save(s);
                return { id: profile, origin: r.origin };
              case "profiles":
                return [{ id: profile, origin: s.server.origin }];
              case "catalog":
                return {
                  cards: [{ ...card, image: { url: null, label: "占位" } }],
                  missing: false,
                };
              case "close":
              case "lifecycle":
                return {};
              case "call":
                break;
              default:
                throw { code: "INVALID_REQUEST" };
            }
            switch (r.operation) {
              case "status":
                return {
                  identity: s.identity,
                  reauthenticationRequired: false,
                  pendingMatches: [],
                  recovery: { commands: [], controls: [] },
                  productPending: [],
                  recentMatches:
                    s.recentMatches ??
                    (s.view
                      ? [
                          {
                            matchId: mid,
                            viewerSeat: "PLAYER1",
                            done: s.view.done,
                            winner: s.view.winner,
                            matchSeq: s.view.matchSeq,
                          },
                        ]
                      : []),
                };
              case "capabilities":
                return {
                  ruleReleaseIds: [rule],
                  acceptingMatches: true,
                  resources: {
                    displayCatalogVersion: "fixture",
                    assetManifestVersion: "unavailable",
                  },
                };
              case "guest":
                s.identity = {
                  userId: "1".repeat(32),
                  deviceId: "2".repeat(32),
                  sessionId: "3".repeat(32),
                  serverId: "fixture",
                  protocolVersion: "2.0",
                };
                save(s);
                return s.identity;
              case "product-decks":
                return {
                  decks: [
                    {
                      id: rid,
                      name: longLabels
                        ? "手机测试卡组".repeat(12)
                        : "手机测试卡组",
                      version: 1,
                      revisions: [{ id: revision, number: 1, count: 60 }],
                    },
                  ],
                };
              case "product-check":
                return { total: 60, issues: [], scope: "structure-only" };
              case "product-deck":
                return {
                  deckRevision: "d".repeat(32),
                  ruleReleaseId: rule,
                };
              case "create-room":
                s.room = {
                  roomId: rid,
                  roomVersion: 1,
                  inviteCode: "ABCDEFGH23",
                  ruleReleaseId: rule,
                  status: "waiting",
                  viewerSeat: "PLAYER1",
                  members: [
                    { seat: "PLAYER1", ready: false },
                    { seat: "PLAYER2", ready: false },
                  ],
                  matchId: null,
                };
                save(s);
                return s.room;
              case "room":
                return s.room;
              case "ready":
                s.room.members = [
                  { seat: "PLAYER1", ready: true },
                  { seat: "PLAYER2", ready: true },
                ];
                s.room.roomVersion++;
                s.room.status = "initialized";
                s.room.matchId = mid;
                save(s);
                return s.room;
              case "snapshot":
                return snapshot();
              case "control":
                return { lease: "native-local-lease", snapshot: snapshot() };
              case "watch":
              case "events":
                return {
                  revision: 1,
                  event: { type: "snapshot", snapshot: snapshot() },
                  done: false,
                  error: null,
                };
              case "unwatch":
                return { watching: false };
              case "command": {
                const v = snapshot();
                v.matchSeq++;
                v.stateVersion++;
                if (
                  battleFlow &&
                  r.body.command.choice.optionId === "d2:item"
                ) {
                  v.observation.self.hand.shift();
                  v.observation.self.handCount = v.observation.self.hand.length;
                  v.decision = {
                    id: "search",
                    kind: "selection",
                    actor: "PLAYER1",
                    min: 1,
                    max: 1,
                    hidden: false,
                    indexed: true,
                    instruction: "从牌库选择一张卡",
                    candidates: Array.from({ length: 24 }, (_, i) => ({
                      ref: `search:${i}`,
                      card: identity(`result-${i}`, `可检索卡牌 ${i + 1}`),
                    })),
                  };
                } else {
                  v.decision = null;
                  v.observation.turn = "PLAYER2";
                }
                s.view = v;
                save(s);
                return {};
              }
              case "resign": {
                const v = snapshot();
                Object.assign(v, {
                  matchSeq: v.matchSeq + 1,
                  done: true,
                  status: "finished",
                  phase: "finished",
                  winner: "PLAYER2",
                  decision: null,
                });
                s.view = v;
                save(s);
                return {};
              }
              case "replay":
                return pageReplay();
              default:
                throw { code: "UNKNOWN_OPERATION" };
            }
          },
        },
      } as any;
    },
    { initial, replay, longLabels, battleFlow },
  );
}

for (const width of [320, 375, 390, 414, 844, 1024]) {
  test(`lobby fits ${width}px with full rule hash, long deck name and invitation`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await fixture(page, true);
    await page.goto("/");
    await page.getByRole("button", { name: "对战", exact: true }).click();
    await page.getByLabel("服务器地址").fill("https://battle.example.test");
    await page.getByRole("button", { name: "保存服务器", exact: true }).click();
    await page
      .getByRole("button", { name: "创建游客身份", exact: true })
      .click();
    const assertFits = async () => {
      const bounds = await page.evaluate(() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        lobby: document
          .querySelector(".online-lobby")!
          .getBoundingClientRect()
          .toJSON(),
        overflowing: [...document.querySelectorAll(".mobile-network *")]
          .filter((e) => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.right > innerWidth + 1;
          })
          .map((e) => ({
            tag: e.tagName,
            cls: e.className,
            right: e.getBoundingClientRect().right,
          }))
          .slice(0, 12),
        scrollOverflows: [...document.querySelectorAll("body *")]
          .filter((e) => e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1)
          .map((e) => ({
            tag: e.tagName,
            cls: e.className,
            width: e.clientWidth,
            scroll: e.scrollWidth,
            overflow: getComputedStyle(e).overflowX,
            rect: e.getBoundingClientRect().toJSON(),
          }))
          .slice(0, 18),
      }));
      if (bounds.document > width)
        await page.screenshot({
          path: `var/p8e-ios/lobby-overflow-${width}.png`,
          fullPage: true,
        });
      expect(bounds.document, JSON.stringify(bounds)).toBeLessThanOrEqual(
        width,
      );
      expect(bounds.lobby.right).toBeLessThanOrEqual(width - 17);
    };
    await expect(page.getByLabel("规则版本")).toBeVisible();
    await assertFits();
    await page.getByLabel("平台已有卡组").selectOption("b".repeat(32));
    await assertFits();
    await page.getByRole("button", { name: /校验并使用此版本/ }).click();
    await page.getByRole("button", { name: /创建邀请房间/ }).click();
    await expect(page.getByText("ABCDEFGH23", { exact: true })).toBeVisible();
    await assertFits();
    await page.screenshot({
      path: `var/p8e-ios/lobby-${width}.png`,
      fullPage: true,
    });
  });
}

test("XR portrait enters a restored lobby with history without horizontal blank space", async ({
  page,
}) => {
  await page.setViewportSize({ width: 414, height: 896 });
  await fixture(page, true);
  await page.addInitScript(() => {
    localStorage.setItem(
      "native-fixture-online",
      JSON.stringify({
        server: { origin: "https://battle.example.test", caPem: "" },
        identity: {
          userId: "1".repeat(32),
          deviceId: "2".repeat(32),
          sessionId: "3".repeat(32),
          serverId: "fixture",
          protocolVersion: "2.0",
        },
        room: null,
        view: null,
        recentMatches: Array.from({ length: 8 }, (_, i) => ({
          matchId: String(i + 1).padStart(32, "a"),
          viewerSeat: "PLAYER1",
          done: true,
          winner: "PLAYER1",
          matchSeq: 100 + i,
        })),
      }),
    );
  });
  await page.goto("/");
  await page.getByRole("button", { name: "对战", exact: true }).click();
  await expect(page.getByText("游客身份已连接", { exact: true })).toBeVisible();
  await expect(page.getByLabel("规则版本")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "下一页对局", exact: true }),
  ).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    414,
  );
  await page.screenshot({
    path: "var/p8e-ios/lobby-XR-restored.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "下一页对局", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    414,
  );
  await page.reload();
  await page.getByRole("button", { name: "对战", exact: true }).click();
  await expect(page.getByText("游客身份已连接", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    414,
  );
});

test("phone plays from the hand, confirms a search and opens private replay via native IPC", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await fixture(page, false, true);
  const remote: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith("http://127.0.0.1:5193")) remote.push(r.url());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "对战", exact: true }).click();
  await page.getByLabel("服务器地址").fill("https://battle.example.test");
  await page.getByRole("button", { name: "保存服务器", exact: true }).click();
  await page.getByLabel("玩家名称").fill("移动训练家");
  await page.getByRole("button", { name: "创建游客身份", exact: true }).click();
  await page.getByLabel("平台已有卡组").selectOption("b".repeat(32));
  await page.getByRole("button", { name: /校验并使用此版本/ }).click();
  await page.getByRole("button", { name: /创建邀请房间/ }).click();
  await expect(page.getByText("ABCDEFGH23", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-lobby.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "准备对战", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "取得操作权", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取得操作权", exact: true }).click();
  const arena = page.getByRole("main", { name: "对战与回放" });
  const nav = arena.getByRole("navigation", { name: "对战视图" });
  await expect(arena.getByText("轮到你操作", { exact: true })).toBeVisible();
  await expect(
    arena.getByRole("button", { name: /我方战斗宝可梦 · HP 80/ }),
  ).toBeInViewport();
  await expect(
    arena.getByRole("button", { name: /对方战斗宝可梦 · HP 110/ }),
  ).toBeInViewport();
  await expect(
    arena.getByText("中毒", { exact: true }).first(),
  ).toBeInViewport();
  expect(
    await arena.locator(".mobile-battle-body").evaluate((e) => e.scrollWidth),
  ).toBeLessThanOrEqual(320);
  const hand = arena.getByRole("region", { name: "我的手牌" });
  const item = hand.getByRole("button", { name: "合成检索物品" });
  await expect(item).toBeInViewport();
  const ownHP = arena.getByText("剩余 HP 80 / 120", { exact: true });
  const hpBounds = (await ownHP.boundingBox())!;
  const handBounds = (await hand.boundingBox())!;
  expect(hpBounds.y + hpBounds.height).toBeLessThanOrEqual(handBounds.y);
  const strip = hand.locator(":scope > div");
  expect(await strip.evaluate((e) => e.scrollWidth)).toBeGreaterThan(
    await strip.evaluate((e) => e.clientWidth),
  );
  await page.screenshot({ path: "var/p8e-ios/e2/phone-battle-board.png" });
  const opponent = arena.getByRole("region", { name: "对手 场面" });
  await expect(opponent.getByText("水×2", { exact: true })).toBeInViewport();
  await expect(
    opponent.getByText("道具：合成战斗道具", { exact: true }),
  ).toBeInViewport();
  const bench = opponent.getByRole("button", {
    name: /备战宝可梦 1 · HP 60 · 附着能量 雷 · 道具 合成备战道具/,
  });
  await expect(bench.getByText("雷1", { exact: true })).toBeInViewport();
  await expect(bench.getByText("具1", { exact: true })).toBeInViewport();
  await bench.click();
  const benchDetails = arena.getByRole("dialog", {
    name: "备战宝可梦 1",
    exact: true,
  });
  await expect(
    benchDetails.getByText("道具：合成备战道具", { exact: true }),
  ).toBeVisible();
  await benchDetails.getByRole("button", { name: "关闭卡牌" }).click();
  await opponent
    .getByRole("button", { name: /对方战斗宝可梦 · HP 110/ })
    .click();
  const opponentDetails = arena.getByRole("dialog", {
    name: "对方战斗宝可梦",
    exact: true,
  });
  await expect(
    opponentDetails.getByText("撤退所需能量：无色 ×2", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-battle-card-retreat.png",
  });
  await opponentDetails.getByRole("button", { name: "关闭卡牌" }).click();
  await arena
    .getByRole("button", { name: "查看状态：我方战斗宝可梦", exact: true })
    .click();
  const ownDetails = arena.getByRole("dialog", {
    name: "我方战斗宝可梦",
    exact: true,
  });
  await expect(
    ownDetails.getByText("撤退所需能量：无需能量", { exact: true }),
  ).toBeVisible();
  await ownDetails.getByRole("button", { name: "关闭卡牌" }).click();
  await nav.getByRole("button", { name: /^操作/ }).click();
  const actions = arena.getByRole("dialog", { name: "对局操作" });
  await expect(actions).toBeVisible();
  await expect(
    actions.getByRole("button", { name: "结束回合", exact: true }),
  ).toBeInViewport();
  await actions.getByRole("button", { name: "收起操作" }).click();
  await expect(arena.getByRole("region", { name: "我的场面" })).toBeVisible();
  await arena.getByRole("button", { name: /我方战斗宝可梦 · HP 80/ }).click();
  const attackMenu = arena.getByRole("region", {
    name: "我方战斗宝可梦的操作",
  });
  await expect(
    attackMenu.getByRole("button", { name: /合成招式/ }),
  ).toBeVisible();
  await item.click();
  await expect(attackMenu).toBeHidden();
  const cardActions = arena.getByRole("region", { name: "合成检索物品的操作" });
  await expect(item).toHaveAttribute("aria-expanded", "true");
  await expect(item).toBeInViewport();
  await expect(cardActions).toBeInViewport();
  const cardBounds = (await item.boundingBox())!;
  const actionBounds = (await cardActions.boundingBox())!;
  expect(cardBounds.y - actionBounds.y - actionBounds.height).toBeCloseTo(8, 0);
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-battle-card-actions.png",
  });
  await cardActions.getByRole("button", { name: "查看卡牌详情" }).click();
  const details = arena.getByRole("dialog", { name: "合成检索物品" });
  await expect(details).toBeVisible();
  await details.getByRole("button", { name: "关闭卡牌" }).click();
  await item.click();
  await cardActions
    .getByRole("button", { name: "使用物品 · 合成检索物品" })
    .click();
  await expect(actions).toBeVisible();
  const confirm = actions.getByRole("button", { name: /^确认选择/ });
  await expect(confirm).toBeInViewport();
  await expect(confirm).toBeDisabled();
  await page.screenshot({ path: "var/p8e-ios/e2/phone-battle-selection.png" });
  await arena
    .getByRole("button", { name: "可检索卡牌 24", exact: true })
    .click();
  await actions.getByRole("button", { name: "收起操作" }).click();
  await expect(actions).toBeHidden();
  await expect(arena.getByRole("region", { name: "我的场面" })).toBeVisible();
  await nav.getByRole("button", { name: /^操作/ }).click();
  await expect(confirm).toHaveText("确认选择 1 张");
  await expect(confirm).toBeInViewport();
  await confirm.click();
  await expect(arena.getByText("对手回合", { exact: true })).toBeVisible();
  const opponentStep = (
    text: string,
    action: "enter" | "attack" | "pass" | "heal",
  ) =>
    page.evaluate(
      ({ text, action }) => {
        const key = "native-fixture-online";
        const state = JSON.parse(localStorage.getItem(key)!);
        const view = state.view;
        view.matchSeq++;
        view.stateVersion++;
        view.events = [
          { seq: view.matchSeq, actor: "PLAYER2", kind: "action", text },
        ];
        if (action === "pass") {
          view.observation.turn = "PLAYER1";
          view.observation.turnNumber++;
          view.visualEffects = [];
        } else if (action === "enter") {
          const name = "合成进场宝可梦";
          view.observation.opponent.bench.push({
            ref: `v${view.stateVersion}:opponent:bench:4`,
            card: { cardId: "arrival", name },
            hp: 70,
            maximumHp: 70,
          });
          view.visualEffects = [
            {
              kind: "move",
              side: "opponent",
              fromZone: "hand",
              zone: "bench",
              cardName: name,
              label: "上场",
              before: 0,
              after: 1,
            },
          ];
        } else if (action === "attack") {
          view.events[0].cards = [view.observation.opponent.active[0].card];
          view.observation.self.active[0].hp = 60;
          view.visualEffects = [
            {
              kind: "hp",
              side: "self",
              zone: "active",
              index: 0,
              before: 80,
              after: 60,
            },
          ];
        } else {
          view.observation.opponent.active[0].hp = 120;
          view.visualEffects = [
            {
              kind: "hp",
              side: "opponent",
              zone: "active",
              index: 0,
              before: 110,
              after: 120,
            },
          ];
        }
        localStorage.setItem(key, JSON.stringify(state));
      },
      { text, action },
    );
  const attackText = "攻击 · 对方战斗宝可梦 · 合成反击";
  const activity = arena.getByRole("button", {
    name: new RegExp(`对手动作.*${attackText}`),
  });
  await opponentStep("放置宝可梦 · 合成进场宝可梦", "enter");
  await expect(arena.locator(".battle-impact-arrival")).toHaveCSS(
    "animation-name",
    "battle-impact-ring",
  );
  await expect(arena.locator(".card-flight")).toHaveCount(1);
  await opponentStep(attackText, "attack");
  await expect(activity).toBeInViewport();
  await expect(arena.locator(".battle-strike-trail")).toHaveCSS(
    "animation-name",
    "battle-strike",
  );
  await expect(arena.locator(".battle-impact-hit")).toHaveCount(1);
  await expect(arena.locator(".battle-hp-float")).toHaveText("-20 HP");
  await opponentStep("结束回合", "pass");
  await expect(arena.getByText("等待局面更新", { exact: true })).toBeVisible();
  await expect(activity).toBeInViewport();
  const settledHP = (await arena
    .getByText("剩余 HP 60 / 120", { exact: true })
    .boundingBox())!;
  expect(settledHP.y + settledHP.height).toBeLessThanOrEqual(
    (await hand.boundingBox())!.y,
  );
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-battle-opponent-action.png",
  });
  await arena.locator(".mobile-battle-body").evaluate((body) => {
    body.scrollTop = body.scrollHeight;
  });
  await expect(activity).toBeInViewport();
  await activity.click();
  const log = arena.getByRole("region", { name: "对局信息" });
  await expect(log.getByLabel("对局记录范围")).toHaveValue("opponent");
  await expect(
    log.getByRole("button", { name: new RegExp(attackText) }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(log.getByText(/HP：80 → 60/)).toBeVisible();
  await nav.getByRole("button", { name: "场面" }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await opponentStep("回复体力", "heal");
  await expect(
    arena.getByText("剩余 HP 120 / 120", { exact: true }),
  ).toBeVisible();
  await expect(arena.locator(".card-flight-layer")).toHaveCount(0);
  await arena.getByRole("button", { name: "对局菜单" }).click();
  await page.getByRole("button", { name: "认输", exact: true }).click();
  await page.getByRole("button", { name: "确认认输", exact: true }).click();
  await arena.getByRole("button", { name: "对局菜单" }).click();
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toBeVisible();
  await expect(nav.getByRole("button", { name: /^操作/ })).toHaveCount(0);
  await expect(arena.getByText("只读回放", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-replay.png",
    fullPage: true,
  });
  const calls = await page.evaluate(() => (window as any).nativeCalls);
  expect(
    calls
      .filter((r: any) => r.operation === "command")
      .map((r: any) => r.body.command.choice),
  ).toEqual([{ optionId: "d2:item" }, { selectedRefs: ["search:23"] }]);
  expect(calls.some((r: any) => r.operation === "replay")).toBe(true);
  expect(JSON.stringify(calls)).not.toMatch(
    /atk_|rtk_|Authorization|X-PTCG-Controller/,
  );
  expect(remote).toEqual([]);
});

test("iPad keeps server configuration across reload and never offers terminal authorization", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await fixture(page);
  await page.goto("/");
  await page.getByRole("button", { name: "对战", exact: true }).click();
  await page.getByLabel("服务器地址").fill("https://battle.example.test");
  await page.getByRole("button", { name: "保存服务器", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "对战", exact: true }).click();
  await expect(
    page.getByText("对战服务器 · 已配置", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("本机授权码")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
