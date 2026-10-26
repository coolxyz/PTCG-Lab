import { test, expect, type Page } from "@playwright/test";
import { demoSnapshot, demoReplay } from "../src/demo-data";

async function lobby(
  page: Page,
  options: {
    empty?: boolean;
    paused?: boolean;
    pending?: boolean;
    reject?: boolean;
  } = {},
) {
  const calls: { op: string; body: any }[] = [];
  let room = {
    roomId: "room-1",
    roomVersion: 1,
    ruleReleaseId: "standard-1",
    viewerSeat: "PLAYER1",
    status: "waiting",
    inviteCode: "ABCD234567",
    members: [{ seat: "PLAYER1", ready: false }],
    matchId: null,
  };
  const decks = [
    {
      id: "grimmsnarl",
      name: "长毛巨魔 ex · 暗夜突袭",
      version: 1,
      revisions: [
        { id: "grim-1", number: 1, count: 60 },
        { id: "grim-2", number: 2, count: 60 },
      ],
    },
    {
      id: "gardevoir",
      name: "沙奈朵 ex",
      version: 1,
      revisions: [{ id: "gard-1", number: 1, count: 60 }],
    },
  ];
  const rows = Array.from({ length: 14 }, (_, i) => ({
    matchId: (i + 1).toString(16).padStart(32, "0"),
    viewerSeat: "PLAYER1",
    done: i >= 2,
    winner: i === 2 ? null : i % 2 ? "PLAYER2" : "PLAYER1",
    matchSeq: 10 + i,
  }));
  await page.route("**/session", (r) =>
    r.fulfill({ json: { capability: "test-only" } }),
  );
  await page.route("**/catalog", (r) =>
    r.fulfill({ json: { cards: [], missing: false } }),
  );
  await page.route("**/api/desktop/online/v1/**", async (r) => {
    const op = new URL(r.request().url()).pathname.split("/").at(-1)!;
    const body = r.request().postDataJSON();
    calls.push({ op, body });
    if (op === "product-deck" && options.reject) {
      await r.fulfill({
        status: 422,
        json: { error: { code: "INVALID_PAYLOAD" } },
      });
      return;
    }
    if (op === "ready")
      room = {
        ...room,
        roomVersion: room.roomVersion + 1,
        members: [{ seat: "PLAYER1", ready: true }],
      };
    if (op === "product-freeze")
      decks[0].revisions.push({ id: "grim-3", number: 3, count: 60 });
    const results: Record<string, unknown> = {
      profiles: [{ id: "lan", origin: "https://battle.example.test" }],
      status: {
        identity: { userId: "me" },
        reauthenticationRequired: false,
        pendingMatches: options.pending ? [rows[0].matchId] : [],
        productPending: [],
        recentMatches: options.empty ? [] : rows,
        recovery: { commands: [], controls: [] },
      },
      capabilities: {
        ruleReleaseIds: ["standard-1"],
        acceptingMatches: !options.paused,
        resources: {
          displayCatalogVersion: "catalog",
          assetManifestVersion: "assets",
        },
      },
      "product-decks": { decks: options.empty ? [] : decks },
      "product-check": { total: 60, issues: [] },
      "product-deck": { deckRevision: "remote-deck" },
      "create-room": room,
      "join-room": room,
      room,
      ready: room,
      "leave-room": {},
      snapshot: { ...demoSnapshot("PLAYER1"), matchId: body.matchId },
      watch: {},
      unwatch: {},
      events: { event: null, done: false, error: null },
      replay: {
        ...demoReplay("PLAYER1"),
        matchId: body.matchId,
        viewerSeat: "PLAYER1",
      },
    };
    await r.fulfill({ json: { result: results[op] ?? {} } });
  });
  await page.goto("/e2e/lobby.html");
  await expect(page.getByRole("heading", { name: "对战大厅" })).toBeVisible();
  await expect(page.getByLabel("平台已有卡组")).toBeEnabled();
  return { calls, rows };
}
async function selectDeck(page: Page) {
  await page.getByLabel("平台已有卡组").selectOption("grimmsnarl");
  await expect(page.getByLabel("冻结版本")).toHaveValue("grim-2");
  await page.getByRole("button", { name: "校验并使用此版本" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: /^卡组版本已就绪$/ }),
  ).toBeVisible();
}

test("deck selection, frozen versions, room invitation and stable polling", async ({
  page,
  context,
}) => {
  const { calls } = await lobby(page);
  await expect(
    page.getByRole("button", { name: "创建邀请房间" }),
  ).toBeDisabled();
  await selectDeck(page);
  await page.screenshot({
    path: "var/p55lobby/lobby-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("平台已有卡组").selectOption("gardevoir");
  await expect(page.getByText(/当前已提交：长毛巨魔/)).toBeVisible();
  expect(calls.filter((c) => c.op === "product-deck")).toHaveLength(1);
  await page.getByRole("button", { name: "校验并使用此版本" }).click();
  await expect(page.getByText(/当前已提交：沙奈朵/)).toBeVisible();
  await page.getByRole("button", { name: "创建邀请房间" }).click();
  await expect(page.getByTestId("invite-code")).toHaveText("ABCD234567");
  await expect(page.getByLabel("平台已有卡组")).toBeDisabled();
  await expect(page.getByText("等待朋友加入", { exact: true })).toBeVisible();
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制房间码" }).click();
  await expect(page.getByText("房间码已复制", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "ABCD234567",
  );
  const prepare = page.getByRole("button", { name: "准备对战", exact: true });
  await prepare.focus();
  const box = await prepare.boundingBox();
  await expect
    .poll(() => calls.filter((c) => c.op === "room").length)
    .toBeGreaterThan(1);
  await expect(prepare).toBeFocused();
  expect(await prepare.boundingBox()).toEqual(box);
  await page.screenshot({
    path: "var/p55lobby/lobby-room.png",
    fullPage: true,
  });
  await prepare.click();
  await expect(page.getByRole("button", { name: "取消准备" })).toBeVisible();
  await page.getByRole("button", { name: "离开房间" }).click();
  await page.getByLabel("房间码", { exact: true }).fill("abcd234567");
  await page.getByLabel("房间码", { exact: true }).press("Enter");
  await expect(page.getByTestId("invite-code")).toBeVisible();
  expect(calls.find((c) => c.op === "join-room")?.body.inviteCode).toBe(
    "ABCD234567",
  );
});

test("all local records can be filtered, searched and paged; replay opens the selected match", async ({
  page,
}) => {
  const { calls, rows } = await lobby(page);
  const list = page.getByRole("list", { name: "本机对局记录" });
  await expect(list.getByRole("listitem")).toHaveCount(6);
  await page.getByRole("button", { name: "下一页对局" }).click();
  await page.getByRole("button", { name: "下一页对局" }).click();
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: /^进行中 2$/ }).click();
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: /^已结束 12$/ }).click();
  await page.getByLabel("搜索对局编号").fill(rows[2].matchId);
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list).not.toContainText("落败");
  await expect(list).not.toContainText("胜利");
  await page.getByRole("button", { name: "观看回放", exact: true }).click();
  await expect
    .poll(() =>
      calls.some(
        (c) => c.op === "replay" && c.body.matchId === rows[2].matchId,
      ),
    )
    .toBeTruthy();
  await expect(
    page.getByText("只读回放", { exact: false }).first(),
  ).toBeVisible();
});

test("empty state, mobile layout and match ID recovery", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { calls } = await lobby(page, { empty: true });
  await expect(page.getByText("你的第一场对战，从这里开始")).toBeVisible();
  await expect(page.getByText(/还没有保存的卡组/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: "var/p55lobby/lobby-mobile-empty.png",
    fullPage: true,
  });
  await page.getByText("通过编号找回对局", { exact: true }).click();
  await page.getByLabel("已有对局编号", { exact: true }).fill("A".repeat(32));
  await page.getByRole("button", { name: "打开我的对局" }).click();
  await expect
    .poll(() =>
      calls.some(
        (c) => c.op === "snapshot" && c.body.matchId === "a".repeat(32),
      ),
    )
    .toBeTruthy();
});

test("new frozen version selected explicitly, rejection clears readiness, search has a reset", async ({
  page,
}) => {
  await lobby(page, { reject: true });
  await page.getByLabel("平台已有卡组").selectOption("grimmsnarl");
  await page.getByRole("button", { name: "冻结已保存卡表" }).click();
  await expect(page.getByLabel("冻结版本")).toHaveValue("grim-3");
  await page.getByRole("button", { name: "校验并使用此版本" }).click();
  await expect(page.getByRole("alert")).toContainText("服务器拒绝了所选卡组");
  await expect(
    page.getByRole("button", { name: "创建邀请房间" }),
  ).toBeDisabled();
  await page.getByLabel("搜索对局编号").fill("not-a-match");
  await expect(page.getByText("没有符合条件的对局")).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(
    page.getByRole("list", { name: "本机对局记录" }).getByRole("listitem"),
  ).toHaveCount(6);
});

test("maintenance blocks both room entry points and pending records remain recoverable", async ({
  page,
}) => {
  await lobby(page, { paused: true, pending: true });
  await selectDeck(page);
  await page.getByLabel("房间码", { exact: true }).fill("ABCD234567");
  await expect(
    page.getByRole("button", { name: "创建邀请房间" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "加入房间", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: /恢复待确认对局/ }),
  ).toBeEnabled();
});

test("lobby adapts to a narrower Web App content column", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await lobby(page);
  await page.locator("#root").evaluate((node) => {
    node.style.width = "650px";
    node.style.marginLeft = "190px";
  });
  const deck = await page.locator(".lobby-deck-panel").boundingBox();
  const room = await page.locator(".lobby-room-panel").boundingBox();
  expect(room!.y).toBeGreaterThan(deck!.y + deck!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    1024,
  );
});
