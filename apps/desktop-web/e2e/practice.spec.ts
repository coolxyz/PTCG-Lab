import { test, expect } from "@playwright/test";
import { snapshotView } from "../src/presentation";
import { demoSnapshot } from "../src/demo-data";
test("practice scheduling, pause, single step, replay and navigation do not duplicate AI loops", async ({
  page,
}) => {
  await page.clock.install();
  const view = {
    ...snapshotView(demoSnapshot()),
    decision: null,
    aiStrategy: { id: "demo-h2", version: "1", name: "合成 H2 策略" },
  };
  let creates = 0,
    advances = 0,
    sessions = 0;
  await page.route("**/api/battle/**", async (route) => {
    const url = new URL(route.request().url()),
      p = url.pathname.replace("/api/battle", "");
    let data: unknown;
    if (p === "/session") {
      sessions++;
      data = {
        mode: "local-practice",
        cards: [],
        engineVersion: "demo",
        presets: Array.from({ length: 5 }, (_, i) => ({
          id: "preset-" + i,
          name: "演示预设 " + i,
        })),
        opponents: [{ id: "demo-ai", name: "演示 AI" }],
      };
    } else if (p === "/revisions")
      data = [{ id: "demo-rev", name: "演示卡组", number: 1, playable: true }];
    else if (p === "/matches" && route.request().method() === "POST") {
      creates++;
      expect(route.request().postDataJSON().aiLevel).toBe("H2");
      data = view;
    } else if (p === "/matches") data = [];
    else if (p.endsWith("/advance")) {
      advances++;
      data = { ...view, stateVersion: advances + 1 };
    } else if (p.endsWith("/replay/timeline"))
      data = { lastSeq: 1, checkpoints: [] };
    else if (p.endsWith("/replay")) data = { frames: [{ seq: 0, view }] };
    else data = view;
    await route.fulfill({ json: data });
  });
  await page.goto("/#practice");
  await expect(page.getByLabel("AI 策略", { exact: true })).toHaveValue("H2");
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(page.getByLabel("当前 AI 策略")).toHaveText("合成 H2 策略");
  await page.getByRole("button", { name: "暂停 AI", exact: true }).click();
  await page.clock.runFor(6000);
  expect(advances).toBe(0);
  await page.getByRole("button", { name: "AI 下一步", exact: true }).click();
  await expect.poll(() => advances).toBe(1);
  await page.getByRole("button", { name: "继续 AI", exact: true }).click();
  await page.getByLabel("AI 行动速度", { exact: true }).selectOption("1200");
  await page.clock.runFor(1250);
  await expect.poll(() => advances).toBe(2);
  await page.getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(page.getByText("历史决策（只读）")).toBeVisible();
  await page.clock.runFor(5000);
  expect(advances).toBe(2);
  await page.getByRole("button", { name: "返回大厅", exact: true }).click();
  await page.getByRole("link", { name: "网络界面演示", exact: true }).click();
  await page.clock.runFor(5000);
  expect(advances).toBe(2);
  await page.getByRole("link", { name: "本地人机练习", exact: true }).click();
  await page.getByRole("button", { name: "返回当前对局", exact: true }).click();
  expect(creates).toBe(1);
  expect(sessions).toBe(1);
});
test("lost command reply can be retried after a browser reload with exactly the same id", async ({
  page,
}) => {
  const view = snapshotView(demoSnapshot());
  const commands: unknown[] = [];
  let fail = true;
  await page.route("**/api/battle/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace(
      "/api/battle",
      "",
    );
    let data: unknown;
    if (p === "/session")
      data = {
        mode: "local-practice",
        cards: [],
        engineVersion: "demo",
        presets: [],
        opponents: [{ id: "demo-ai", name: "演示 AI" }],
      };
    else if (p === "/revisions")
      data = [{ id: "demo-rev", name: "演示卡组", number: 1, playable: true }];
    else if (p === "/matches" && route.request().method() === "GET") data = [];
    else if (p.endsWith("/commands")) {
      commands.push(route.request().postDataJSON());
      if (fail) {
        await route.abort();
        return;
      }
      data = { view: { ...view, stateVersion: 2 } };
    } else data = view;
    await route.fulfill({ json: data });
  });
  await page.goto("/#practice");
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await page.getByRole("button", { name: "结束回合", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试同一命令", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "重试同一命令", exact: true }),
  ).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "重试同一命令", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试同一命令", exact: true }),
  ).toHaveCount(0);
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
});
test("coin animation blocks AI until all results finish", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if(m.type()==="error" && m.text().includes("same key")) errors.push(m.text()); });
  await page.clock.install();
  const view = { ...snapshotView(demoSnapshot()), decision: null };
  view.events![0].coins = ["heads", "tails"];
  let advances = 0;
  await page.route("**/api/battle/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace(
      "/api/battle",
      "",
    );
    let data: unknown;
    if (p === "/session")
      data = {
        mode: "local-practice",
        cards: [],
        engineVersion: "demo",
        presets: [],
        opponents: [{ id: "demo-ai", name: "演示 AI" }],
      };
    else if (p === "/revisions")
      data = [{ id: "rev", name: "演示卡组", number: 1, playable: true }];
    else if (p === "/matches" && route.request().method() === "GET") data = [];
    else if (p.endsWith("/advance")) {
      advances++;
      data = {
        ...view,
        decision: demoSnapshot().decision,
        stateVersion: 2,
        events: [],
      };
    } else data = view;
    await route.fulfill({ json: data });
  });
  await page.goto("/#practice");
  await page.getByRole("button", { name: "开始人机对战", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "硬币投掷结果" }),
  ).toBeVisible();
  await page.getByLabel("AI 行动速度", { exact: true }).selectOption("1200");
  await page.clock.runFor(2000);
  expect(advances).toBe(0);
  await page.getByRole("button", { name: "显示全部结果", exact: true }).click();
  await page.clock.runFor(1250);
  await expect.poll(() => advances).toBe(1);
  expect(errors).toEqual([]);
});
