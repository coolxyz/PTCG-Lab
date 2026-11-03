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

async function fixture(page: Page, longLabels = false) {
  await page.addInitScript(
    ({ initial, replay, longLabels }) => {
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
                v.decision = null;
                v.observation.turn = "PLAYER2";
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
    { initial, replay, longLabels },
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

test("phone reuses the shared lobby, platform deck, Arena and private replay via native IPC", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
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
  await page.getByRole("button", { name: /结束回合/ }).click();
  await page.getByRole("button", { name: "认输", exact: true }).click();
  await page.getByRole("button", { name: "确认认输", exact: true }).click();
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByLabel("回放步数")).toBeVisible();
  await page.screenshot({
    path: "var/p8e-ios/e2/phone-replay.png",
    fullPage: true,
  });
  const calls = await page.evaluate(() => (window as any).nativeCalls);
  expect(calls.some((r: any) => r.operation === "command")).toBe(true);
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
