import { test, expect, type Page } from "@playwright/test";
import { demoSnapshot, demoReplay } from "../src/demo-data";
import { snapshotView } from "../src/presentation";

async function fixtures(page: Page, remembered = false) {
  const ended = {
    ...demoSnapshot("PLAYER1"),
    done: true,
    status: "finished",
    decision: null,
  };
  const calls: string[] = [];
  if (remembered)
    await page.addInitScript((mid) => {
      sessionStorage.setItem("ptcg-online-profile", "lan");
      sessionStorage.setItem(
        "ptcg-online-lan",
        JSON.stringify({ userId: "me", matchId: mid }),
      );
    }, ended.matchId);
  await page.route("**/session", (r) =>
    r.fulfill({ json: { capability: "test-only" } }),
  );
  await page.route("**/catalog", (r) =>
    r.fulfill({ json: { cards: [], missing: false } }),
  );
  await page.route("**/api/desktop/online/v1/**", (r) => {
    const op = new URL(r.request().url()).pathname.split("/").at(-1)!;
    calls.push(op);
    const result =
      (
        {
          profiles: [{ id: "lan", origin: "https://battle.example.test" }],
          status: {
            identity: { userId: "me" },
            pendingMatches: [],
            recentMatches: [
              {
                matchId: ended.matchId,
                viewerSeat: "PLAYER1",
                done: true,
                winner: "PLAYER1",
                matchSeq: 2,
              },
            ],
            recovery: { commands: [], controls: [] },
          },
          capabilities: {
            acceptingMatches: true,
            ruleReleaseIds: ["standard-1"],
            resources: {
              displayCatalogVersion: "catalog",
              assetManifestVersion: "assets",
            },
          },
          "product-decks": { decks: [] },
          snapshot: ended,
          replay: demoReplay("PLAYER1"),
          unwatch: {},
        } as Record<string, unknown>
      )[op] ?? {};
    return r.fulfill({ json: { result } });
  });
  await page.route("**/api/sync/status", (r) =>
    r.fulfill({
      json: {
        repository: "https://github.com/example/cards",
        jobs: [],
        releases: [],
      },
    }),
  );
  const practice = { ...snapshotView(demoSnapshot()), decision: null };
  await page.route("**/api/battle/**", (r) => {
    const path = new URL(r.request().url()).pathname.replace("/api/battle", "");
    let data: unknown = practice;
    if (path === "/session")
      data = {
        mode: "local-practice",
        cards: [],
        engineVersion: "demo",
        presets: [{ id: "p", name: "推荐预设" }],
        opponents: [{ id: "demo-ai", name: "演示 AI" }],
      };
    else if (path === "/revisions")
      data = [
        { id: "my-deck", name: "长毛巨魔 ex", number: 2, playable: true },
      ];
    else if (path === "/matches" && r.request().method() === "GET")
      data = Array.from({ length: 8 }, (_, i) => ({
        id: `practice-${i}`,
        opponent: "demo-ai",
        status: i < 2 ? "playing" : "finished",
        seq: 10 + i,
      }));
    else if (path.endsWith("/replay/timeline"))
      data = { lastSeq: 1, checkpoints: [] };
    else if (path.endsWith("/replay"))
      data = { frames: [{ seq: 0, view: practice }] };
    else if (path.startsWith("/matches/"))
      data = { ...practice, matchId: path.split("/")[2], done: true };
    return r.fulfill({ json: data });
  });
  await page.goto("/e2e/workspace.html");
  return calls;
}

for (const width of [1440, 1024, 390]) {
  test(`workspace pages share the catalog gutter and headings at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await fixtures(page);
    const baseline = await page.locator(".page-heading").boundingBox();
    const headingStyle = await page.locator("h1").evaluate((e) => ({
      size: getComputedStyle(e).fontSize,
      weight: getComputedStyle(e).fontWeight,
    }));
    for (const [name, selector] of [
      ["网络对战", ".online-lobby"],
      ["数据与对战更新", ".sync-page"],
      ["人机练习", ".practice-lobby"],
    ]) {
      await page.getByRole("button", { name, exact: true }).click();
      const container = page.locator(selector);
      await expect(container).toBeVisible();
      if (name === "网络对战")
        await expect(
          container.getByText("游客身份已连接", { exact: true }),
        ).toBeVisible();
      const heading = container.locator(".page-heading");
      const box = await heading.boundingBox();
      expect(box!.x).toBe(baseline!.x);
      expect(box!.y).toBe(baseline!.y);
      expect(box!.width).toBe(baseline!.width);
      expect(
        await heading.locator("h1").evaluate((e) => ({
          size: getComputedStyle(e).fontSize,
          weight: getComputedStyle(e).fontWeight,
        })),
      ).toEqual(headingStyle);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width);
      expect(await page.locator("main main").count()).toBe(0);
      if (name !== "数据与对战更新") {
        const pagination = container.getByRole("navigation", {
          name: name === "网络对战" ? "对局记录分页" : "练习记录分页",
        });
        const buttons = await pagination.getByRole("button").all();
        expect((await buttons[0].boundingBox())!.y).toBe(
          (await buttons[1].boundingBox())!.y,
        );
      }
      await page.screenshot({
        path: `var/workspace-ui/${width}-${selector.slice(1)}.png`,
        fullPage: true,
      });
    }
  });
}

test("re-entering network play stays in the lobby after a completed match; replay stays explicit", async ({
  page,
}) => {
  const calls = await fixtures(page, true);
  const nav = page.getByRole("navigation", { name: "测试页面" });
  await nav.getByRole("button", { name: "网络对战", exact: true }).click();
  await expect(page.getByRole("heading", { name: "对战大厅" })).toBeVisible();
  expect(calls).not.toContain("replay");
  await expect(page.locator(".battle-screen")).toHaveCount(0);
  await page.getByRole("button", { name: "观看回放", exact: true }).click();
  await expect(
    page.getByText("只读回放", { exact: false }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await nav.getByRole("button", { name: "卡牌资料库", exact: true }).click();
  await nav.getByRole("button", { name: "网络对战", exact: true }).click();
  await expect(page.getByRole("heading", { name: "对战大厅" })).toBeVisible();
  await page.reload();
  await nav.getByRole("button", { name: "网络对战", exact: true }).click();
  await expect(page.getByRole("heading", { name: "对战大厅" })).toBeVisible();
});

test("practice history filters, pages and opens a finished game directly as replay", async ({
  page,
}) => {
  await fixtures(page);
  await page.getByRole("button", { name: "人机练习", exact: true }).click();
  await expect(page.getByLabel("对战卡组版本")).toHaveValue("my-deck");
  await expect(page.getByLabel("AI 策略", { exact: true })).toHaveValue("H2");
  const list = page.getByRole("list", { name: "练习对局记录" });
  await expect(list.getByRole("listitem")).toHaveCount(6);
  await page.getByRole("button", { name: "下一页练习" }).click();
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: /^进行中 2$/ }).click();
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: /^已结束 6$/ }).click();
  await page.getByLabel("搜索练习对局").fill("practice-7");
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await list.getByRole("button", { name: "观看回放" }).click();
  await expect(page.getByText("历史决策（只读）")).toBeVisible();
});
