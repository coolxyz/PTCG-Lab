import { test, expect } from "@playwright/test";
import { OnlineController } from "../src/online/controller";
import { OnlineError, type OnlineAPI } from "../src/online/transport";
import type { Remembered, OnlineStorage } from "../src/online/storage";

test("recovered product submission keeps its rule and rejection cannot reuse an earlier deck", async () => {
  let saved: Remembered = { userId: "user" };
  let roomBody: any;
  const storage: OnlineStorage = {
    windowId: () => "window",
    selected: () => "lan",
    read: () => saved,
    write: (_profile, value) => {
      saved = value;
    },
  };
  const api: OnlineAPI = {
    profiles: async () => [{ id: "lan", origin: "https://example.test" }],
    catalog: async () => ({ cards: [], missing: true }),
    close: async () => {},
    call: async <T>(_profile: string, operation: string, body: any = {}) => {
      const results: Record<string, unknown> = {
        status: {
          identity: { userId: "user" },
          reauthenticationRequired: false,
          pendingMatches: [],
          recovery: { commands: [], controls: [] },
        },
        capabilities: {
          ruleReleaseIds: ["new-rule", "old-rule"],
          acceptingMatches: true,
          resources: {
            displayCatalogVersion: "catalog",
            assetManifestVersion: "unavailable",
          },
        },
      };
      if (operation === "product-deck") {
        if (body.revisionId === "rejected")
          throw new OnlineError("INVALID_PAYLOAD", false, 422);
        return { deckRevision: "remote-version" } as T;
      }
      if (operation === "create-room") {
        roomBody = body;
        return {
          roomId: "room",
          ruleReleaseId: body.ruleReleaseId,
          status: "closed",
          members: [],
          matchId: null,
        } as T;
      }
      return results[operation] as T;
    },
  };
  const controller = new OnlineController(
    api,
    storage,
    () => "00000000-0000-4000-8000-000000000001",
  );
  await controller.start();
  expect(controller.snapshot().rule).toBe("new-rule");
  await controller.recoverProduct({
    revisionId: "immutable-version",
    ruleReleaseId: "old-rule",
    requestId: "original",
  });
  expect(controller.snapshot().rule).toBe("old-rule");
  await controller.create();
  expect(roomBody.ruleReleaseId).toBe("old-rule");
  expect(roomBody.deckRevision).toBe("remote-version");
  await controller.useRevision("rejected");
  expect(controller.snapshot().remembered.revision).toBeUndefined();
  expect(controller.snapshot().remembered.job).toBeUndefined();
  await controller.dispose();
});

import { demoSnapshot, demoReplay } from "../src/demo-data";
import { visibleHistory, replayTimeline } from "../src/online/history";
import { skillName } from "../../../packages/battle-ui/src/battle/presentation";

test("background polling preserves interaction state and complete replay paginates past 300 frames", async () => {
  const view = demoSnapshot("PLAYER1", "options");
  view.matchSeq = 303;
  const page = demoReplay("PLAYER1");
  const base = page.frames[0];
  const all = Array.from({ length: 304 }, (_, i) => ({
    ...base,
    frameSeq: i,
    matchSeq: i,
    done: i === 303,
    observation: { ...base.observation, turnNumber: Math.floor(i / 2) + 1 },
  }));
  let saved: Remembered = { userId: "user" };
  let controlAttempts = 0;
  let polling = 0,
    release: (() => void) | undefined;
  const storage: OnlineStorage = {
    windowId: () => "w",
    selected: () => "lan",
    read: () => saved,
    write: (_, v) => {
      saved = v;
    },
  };
  const api: OnlineAPI = {
    profiles: async () => [{ id: "lan", origin: "https://example.test" }],
    catalog: async () => ({ cards: [] }),
    close: async () => {},
    call: async <T>(_: string, op: string, body: any = {}) => {
      if (op === "status")
        return {
          identity: { userId: "user" },
          pendingMatches: [],
          recovery: { commands: [], controls: [] },
        } as T;
      if (op === "capabilities")
        return {
          ruleReleaseIds: ["rule"],
          resources: {
            displayCatalogVersion: "c",
            assetManifestVersion: "unavailable",
          },
        } as T;
      if (op === "snapshot") return structuredClone(view) as T;
      if (op === "control") {
        if (++controlAttempts < 3)
          throw new OnlineError("PROFILE_BUSY", false, 409);
        return { lease: "test-lease", snapshot: structuredClone(view) } as T;
      }
      if (op === "events") {
        polling++;
        await new Promise<void>((r) => {
          release = r;
        });
        return {
          event: { snapshot: structuredClone(view) },
          done: false,
          error: null,
        } as T;
      }
      if (op === "replay") {
        const offset = Number(body.cursor || 0);
        return {
          ...page,
          matchId: view.matchId,
          frames: all.slice(offset, offset + 3),
          nextCursor: offset + 3 < all.length ? String(offset + 3) : null,
        } as T;
      }
      return {} as T;
    },
  };
  const c = new OnlineController(api, storage);
  try {
    await c.start();
    await c.open(view.matchId);
    await c.control();
    expect(controlAttempts).toBe(3);
    expect(c.snapshot().lease).toBe("test-lease");
    await expect.poll(() => polling).toBe(1);
    expect(c.snapshot().busy).toBe(false);
    const original = c.snapshot().view;
    release!();
    await expect.poll(() => c.snapshot().history.length).toBeGreaterThan(4);
    expect(c.snapshot().view).toBe(original);
    view.done = true;
    await c.refresh();
    await c.replay();
    for (let i = 0; i < 102 && c.snapshot().nextCursor; i++) await c.more();
    expect(c.snapshot().replay?.frames).toHaveLength(304);
    c.seek(303);
    expect(c.snapshot().frame).toBe(303);
    expect(c.snapshot().replay?.frames[303].done).toBe(true);
    expect(replayTimeline(c.snapshot().replay!)).toHaveLength(152);
    expect(c.snapshot().error).toBe("");
  } finally {
    release?.();
    c.dispose();
  }
});

test("visible logs use public changes and skills map only when source metadata matches", () => {
  const first = demoSnapshot("PLAYER2");
  first.events = [];
  first.visualEffects = [];
  first.matchSeq = 0;
  const next = structuredClone(first);
  next.matchSeq = 1;
  next.observation.opponent.handCount++;
  const events = visibleHistory([first, next], {});
  expect(events[1].changes).toEqual(["对手的手牌数量：1 → 2"]);
  expect(JSON.stringify(events)).not.toContain("演示宝可梦");
  const info: any = { attacks: [{ name: "中文招式" }] };
  expect(
    skillName(
      "English attack",
      "attack",
      {
        name: "card",
        attacks: [{ name: "English attack", damage: 10, text: "" }],
      },
      info,
    ),
  ).toBe("中文招式");
  expect(
    skillName(
      "Copied attack",
      "attack",
      {
        name: "card",
        attacks: [{ name: "English attack", damage: 10, text: "" }],
      },
      info,
    ),
  ).toBe("Copied attack");
});

test("opponent actions and revealed results survive history without revealing private searches", () => {
  const before = demoSnapshot("PLAYER2");
  before.matchSeq = 8;
  before.events = [];
  before.observation.viewer = "player2";
  const after = structuredClone(before);
  after.matchSeq = 9;
  after.events = [
    {
      seq: 9,
      actor: "PLAYER1",
      kind: "action",
      text: "使用物品 · Public Item",
      cards: [
        {
          cardId: "item",
          name: "Public Item",
          presentation: { name: "Public Item" },
        },
      ],
      coins: ["heads", "tails"],
    },
  ];
  after.publicReveals = [
    {
      kind: "search_reveal",
      actor: "PLAYER1",
      cards: [{ id: "hit", name: "Revealed hit" }],
    },
  ];
  after.observation.opponent.handCount = 2;
  after.observation.opponent.discard = [
    { cardId: "item", name: "Public Item" },
  ];
  after.observation.self.active[0].card.presentation!.hp = 30;
  after.observation.self.active[0].card.presentation!.poisoned = true;
  const catalog: any = {
    "Public Item": { cnName: "测试物品", englishName: "Public Item" },
    "Revealed hit": { cnName: "公开检索结果", englishName: "Revealed hit" },
  };
  const events = visibleHistory([before, after], catalog);
  expect(events[1].actor).toBe("PLAYER1");
  expect(events[1].text).toBe("使用物品 · 测试物品");
  expect(events[1].coins).toEqual(["heads", "tails"]);
  expect(events[1].cardGroups?.map((g) => g.label)).toEqual([
    "使用 / 涉及的卡牌",
    "对手 · 按效果展示卡牌",
    "对手弃牌区新增",
  ]);
  expect(events[1].changes).toContain("对手的手牌数量：1 → 2");
  expect(events[1].changes?.some((c) => c.includes("90 → 30"))).toBe(true);
  expect(events[1].changes?.some((c) => c.includes("中毒"))).toBe(true);
  const next = structuredClone(after);
  next.matchSeq = 10;
  next.events = [];
  next.observation.opponent.handCount = 3;
  expect(visibleHistory([before, after, next], catalog)[2].cardGroups).toEqual(
    [],
  );
  // Identity deliberately absent for an ordinary draw/private inspection.
  expect(
    JSON.stringify(visibleHistory([after, next], catalog)[1]),
  ).not.toContain("Revealed hit");
  const duplicate = visibleHistory([before, after, after], catalog);
  expect(duplicate).toHaveLength(2);
});

test("old frames show public discard identities but never claim they were played", () => {
  const before = demoSnapshot("PLAYER1");
  before.events = [];
  before.matchSeq = 5;
  const after = structuredClone(before);
  after.matchSeq = 6;
  after.observation.opponent.discard = [
    { cardId: "discarded", name: "Cost or played card" },
  ];
  after.observation.opponent.handCount = 0;
  const entry = visibleHistory([before, after], {})[1];
  expect(entry.text).toBe("对手弃牌区新增：Cost or played card");
  expect(entry.cardGroups?.[0].label).toBe("对手弃牌区新增");
  expect(entry.cardGroups?.[0].cards[0].name).toBe("Cost or played card");
  expect(entry.text).not.toContain("使用");
  after.matchSeq = 9;
  const gap = visibleHistory([before, after], {})[1];
  expect(gap.text).toBe("恢复对局记录 · 第 9 步");
  expect(gap.cardGroups).toEqual([]);
  expect(gap.changes).toEqual(["前序记录尚未补齐，暂不推断跨步变化。"]);
  after.events = [{ seq: 9, actor: "PLAYER2", kind: "action", text: "完成一次效果选择" }];
  after.publicReveals = [{ kind: "search_reveal", actor: "PLAYER2", cards: [{ name: "Public result" }] }];
  const recovered = visibleHistory([before, after], {})[1];
  expect(recovered.turn).toBeUndefined();
  expect(recovered.cardGroups?.[0].label).toContain("此前已公开，具体步骤待补齐");
  expect(recovered.changes).toEqual(["前序记录尚未补齐，暂不推断跨步变化。"]);
});


test("headlines prefer named actions and otherwise summarize authorized outcomes", () => {
  const before = demoSnapshot("PLAYER1");
  before.matchSeq = 0;
  before.events = [];
  const next = structuredClone(before);
  next.matchSeq = 1;
  next.events = [];
  next.observation.opponent.handCount = 2;
  expect(visibleHistory([before, next], {})[1].text).toBe("对手的手牌数量：1 → 2");
  next.observation.self.active[0].card.presentation!.hp = 30;
  expect(visibleHistory([before, next], {})[1].text).toContain("HP：90 → 30");
  next.publicReveals = [{ kind: "search_reveal", actor: "PLAYER2", cards: [
    { name: "Public hit" }, { name: "Public second" }, { name: "Public third" },
  ] }];
  const catalog: any = {
    "Public hit": { englishName: "Public hit", cnName: "公开卡甲" },
    "Source": { englishName: "Source", cnName: "来源宝可梦", chineseDetails: {
      sections: [{ kind: "特性", name: "测试特性" }],
    } },
  };
  next.events = [{ seq: 1, actor: "PLAYER2", kind: "action", text: "完成一次效果选择" }];
  expect(visibleHistory([before, next], catalog)[1].text)
    .toBe("对手 · 按效果展示卡牌：公开卡甲、Public second 等 3 张");
  next.events = [{ seq: 1, actor: "PLAYER2", kind: "action",
    text: "使用特性 · Source · Public Ability", cards: [{ cardId: "source", name: "Source",
      presentation: { name: "Source", abilities: [{ name: "Public Ability", text: "" }] },
    }],
  }];
  expect(visibleHistory([before, next], catalog)[1].text)
    .toBe("使用特性 · 来源宝可梦 · 测试特性");
  const privateChoice = structuredClone(before);
  privateChoice.matchSeq = 1;
  privateChoice.events = [{ seq: 1, actor: "PLAYER2", kind: "action", text: "完成一次效果选择" }];
  expect(visibleHistory([before, privateChoice], catalog)[1].text)
    .toBe("已完成选牌");
  privateChoice.events[0].coins = ["heads", "tails"];
  expect(visibleHistory([before, privateChoice], catalog)[1].text)
    .toBe("投币结果：正面、反面");
  // Replay rendering uses the same summaries and does not need the latest frame.
  const { viewerSeat: _seat, ...replayBefore } = before;
  const { viewerSeat: _nextSeat, ...replayNext } = next;
  expect(visibleHistory([{ ...replayBefore, frameSeq: 0 }, { ...replayNext, frameSeq: 1 }], catalog)[1].text)
    .toBe("使用特性 · 来源宝可梦 · 测试特性");
});


test("public markers, protections, prizes and turn changes produce meaningful results", () => {
  const before = demoSnapshot("PLAYER1");
  before.matchSeq = 0;
  before.events = [];
  const scenarios: [string, (frame: typeof before) => void][] = [
    ["特性使用标记：已使用", (f) => { f.observation.opponent.active[0].flags = ["abilityUsed"]; }],
    ["特性无效标记：生效", (f) => { f.observation.opponent.active[0].card.presentation!.abilitiesSuppressed = true; }],
    ["撤退限制：生效", (f) => { f.observation.opponent.active[0].card.presentation!.retreatBlocked = true; }],
    ["攻击限制：生效", (f) => { f.observation.opponent.active[0].card.presentation!.attackBlocked = true; }],
    ["招式伤害增加：30", (f) => { f.observation.opponent.active[0].card.presentation!.attackPowerBonus = 30; }],
    ["受到招式伤害减少：20", (f) => { f.observation.opponent.active[0].card.presentation!.attackDamageReduction = 20; }],
    ["防止招式伤害：生效", (f) => { f.observation.opponent.active[0].card.presentation!.attackProtection = { damage: true }; }],
    ["防止招式效果：生效", (f) => { f.observation.opponent.active[0].card.presentation!.attackProtection = { effects: true }; }],
    ["受限招式：测试招式", (f) => { f.observation.opponent.active[0].card.presentation!.limitedAttacks = ["测试招式"]; }],
    ["回复效果：回复 20 HP，解除特殊状态", (f) => { f.observation.opponent.active[0].card.presentation!.recovery = { amount: 20, cure: true }; }],
    ["受击反击：3 个伤害指示物", (f) => { f.observation.opponent.active[0].card.presentation!.retaliationCounters = 30; }],
    ["HP 上限：100 → 130", (f) => { f.observation.opponent.active[0].maximumHp = 130; }],
    ["备战区容量：5 → 8", (f) => { f.observation.opponent.benchCapacity = 8; }],
    ["进入回合 3", (f) => { f.observation.turnNumber = 3; f.visualEffects = []; }],
  ];
  for (const [expected, change] of scenarios) {
    const next = structuredClone(before);
    next.matchSeq = 1;
    change(next);
    const result = visibleHistory([before, next], {})[1];
    expect(result.text).toContain(expected);
    expect(result.explanation).toContain("未保存动作声明");
    next.matchSeq = 4;
    expect(visibleHistory([before, next], {})[1].changes?.join()).not.toContain(expected);
  }
  const next = structuredClone(before);
  next.matchSeq = 1;
  next.observation.opponent.faceUpPrizes = [{ index: 2, card: { cardId: "public", name: "公开奖赏甲" } }];
  const event = visibleHistory([before, next], {})[1];
  expect(event.text).toBe("对手公开的奖赏卡：公开奖赏甲");
  expect(event.cardGroups?.[0].cards).toHaveLength(1);
  // Slot replacement is not evidence that the replacement used an ability.
  next.observation.opponent.active[0].card.cardId = "replacement";
  next.observation.opponent.active[0].flags = ["abilityUsed"];
  expect(visibleHistory([before, next], {})[1].changes?.join()).not.toContain("特性使用标记");
});


test("stadium declarations and follow-up context survive even with identical visible boards", () => {
  const before = demoSnapshot("PLAYER2");
  before.matchSeq = 0;
  before.events = [];
  const stadium = { cardId: "SYN-STADIUM", name: "Test Stadium" };
  before.observation.stadium = [{ ref: "stadium", card: stadium }];
  const use = structuredClone(before);
  use.matchSeq = 1;
  use.events = [{ seq: 1, actor: "PLAYER1", kind: "action", text: "使用竞技场效果 · Test Stadium", cards: [stadium] }];
  const choice = structuredClone(use);
  choice.matchSeq = 2;
  choice.events = [{ seq: 2, actor: "PLAYER1", kind: "action", text: "竞技场效果 · Test Stadium · 完成选牌", cards: [stadium] }];
  const catalog: any = { "Test Stadium": { englishName: "Test Stadium", cnName: "测试竞技场" } };
  const entries = visibleHistory([before, use, choice], catalog);
  expect(entries[1].text).toBe("使用竞技场效果 · 测试竞技场");
  expect(entries[2].text).toBe("竞技场效果 · 测试竞技场 · 完成选牌");
  expect(entries[2].changes).toEqual([]);
  choice.events = [{ seq: 2, actor: "PLAYER1", kind: "action", text: "完成效果选项选择" }];
  const unknown = visibleHistory([before, use, choice], catalog)[2];
  expect(unknown.text).toBe("已完成效果选项选择");
  expect(unknown.text).not.toContain("竞技场"); // Do not attribute an unrelated reaction to the last source.
  expect(unknown.explanation).toContain("不能据此判断效果未发生");
});
