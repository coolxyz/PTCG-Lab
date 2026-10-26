// Full product integration against explicitly supplied private test instances.
import { readFile, writeFile } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";
const input = JSON.parse(await readFile(process.argv[2], "utf8"));
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PTCG_P5C_BROWSER,
});
const pages = [],
  contexts = [],
  views = [null, null],
  errors = [],
  requests = [];
try {
  for (let i = 0; i < 2; i++) {
    const device = input.devices[i];
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1100 },
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    contexts.push(context);
    pages.push(page);
    console.log("Product device", i, "authorization");
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (/^https?:/.test(r.url())) requests.push(r.url());
    });
    page.on("response", async (r) => {
      if (!r.url().includes("/api/desktop/online/v1/") || r.status() !== 200)
        return;
      try {
        const result = (await r.json()).result;
        const v =
          result?.snapshot ||
          result?.event?.snapshot ||
          (result?.observation ? result : null);
        if (v && (!views[i] || v.matchSeq >= views[i].matchSeq)) views[i] = v;
      } catch {}
    });
    await page.goto(device.origin + "/#online");
    await page.getByLabel("本机授权码", { exact: true }).fill(device.code);
    await page
      .getByRole("button", { name: "授权本机浏览器", exact: true })
      .click();
    await page
      .getByRole("button", { name: "创建游客身份", exact: true })
      .click();
    console.log("Product device", i, "shared deck");
    await expect(
      page.getByText("游客身份已连接", { exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("平台已有卡组", { exact: true })
      .selectOption(device.deck);
    await page
      .getByLabel("冻结版本", { exact: true })
      .selectOption(device.revision);
    await expect(page.getByLabel("卡组清单", { exact: true })).toHaveCount(0);
    await page
      .getByRole("button", { name: "校验并使用此版本", exact: true })
      .click();
    await expect(page.getByText("卡组版本已就绪", { exact: true })).toBeVisible(
      { timeout: 30000 },
    );
    // A tab can close/reopen after native authorization without another HTML or code.
    await page.reload();
    await expect(
      page.getByText("游客身份已连接", { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("本机授权码", { exact: true })).toHaveCount(0);
  }
  console.log("Product room invitation");
  await pages[0]
    .getByRole("button", { name: "创建邀请房间", exact: true })
    .click();
  const invite = await pages[0].getByTestId("invite-code").innerText();
  await pages[1].getByLabel("房间码", { exact: true }).fill(invite);
  await pages[1].getByRole("button", { name: "加入房间", exact: true }).click();
  await expect(
    pages[0].getByText("对手：未准备", { exact: true }),
  ).toBeVisible();
  await pages[0].getByRole("button", { name: "准备对战", exact: true }).click();
  await expect(
    pages[1].getByText("对手：已准备", { exact: true }),
  ).toBeVisible();
  await pages[1].getByRole("button", { name: "准备对战", exact: true }).click();
  for (const page of pages) {
    await expect(page.getByRole("main", { name: "对战与回放" })).toBeVisible({
      timeout: 30000,
    });
    await page.getByRole("button", { name: "取得操作权", exact: true }).click();
    await expect(page.getByText(/本窗口可操作/)).toBeVisible();
  }
  for (let seq = 0; seq < 8; seq++) {
    await expect
      .poll(() => views.every((v) => v && v.matchSeq === seq), {
        timeout: 20000,
      })
      .toBe(true);
    const actor = views.findIndex((v) => v.decision);
    expect(actor).toBeGreaterThanOrEqual(0);
    const page = pages[actor],
      decision = views[actor].decision;
    const response = page.waitForResponse((r) => r.url().endsWith("/command"));
    if (decision.kind === "selection") {
      for (let j = 0; j < decision.min; j++)
        await page.locator(".decision-candidates button").nth(j).click();
      await page
        .getByRole("button", {
          name:
            decision.max === 0
              ? "已查看，继续结算"
              : `确认选择 ${decision.min} 张`,
          exact: true,
        })
        .click();
    } else {
      let n = decision.options.findIndex((o) => o.actionType === "PassTurn");
      await page
        .locator(".decision-options button")
        .nth(n < 0 ? 0 : n)
        .click();
    }
    expect((await response).status()).toBe(200);
    expect(views[actor].observation.opponent.hand).toBeNull();
  }
  for (const page of pages) {
    await expect
      .poll(
        () =>
          page
            .locator(".tabletop-card img")
            .evaluateAll(
              (images) => images.filter((img) => img.naturalWidth > 0).length,
            ),
        { timeout: 15000 },
      )
      .toBeGreaterThan(0);
  }
  // Actual approved artwork is served by the local product media service.
  const imageResponse = await contexts[0].request.get(
    input.devices[0].origin + input.devices[0].image,
  );
  expect(imageResponse.status()).toBe(200);
  const hash = await import("node:crypto");
  expect(
    hash
      .createHash("sha256")
      .update(await imageResponse.body())
      .digest("hex"),
  ).toBe(input.devices[0].imageHash);
  await pages[0].screenshot({
    path: input.output + "/product-arena.png",
    fullPage: true,
  });
  // Navigation away does not resign; both product management and practice remain usable.
  await pages[0].goto(input.devices[0].origin + "/#decks");
  await expect(
    pages[0].getByRole("button", { name: /网络对战/ }).first(),
  ).toBeVisible();
  await pages[0].goto(input.devices[0].origin + "/#online");
  await expect(pages[0].getByRole("main", { name: "对战与回放" })).toBeVisible({
    timeout: 20000,
  });
  const mid = views[0].matchId;
  // New window uses the existing HttpOnly product authorization.
  const extra = await contexts[0].newPage();
  await extra.goto(input.devices[0].origin + "/#online");
  await expect(
    extra.getByText("游客身份已连接", { exact: true }),
  ).toBeVisible();
  await extra.getByLabel("已有对局编号", { exact: true }).fill(mid);
  await extra
    .getByRole("button", { name: "打开我的对局", exact: true })
    .click();
  await expect(extra.getByRole("main", { name: "对战与回放" })).toBeVisible();
  await extra.close();
  await pages[0]
    .getByRole("button", { name: "取得操作权", exact: true })
    .click();
  await pages[0].getByRole("button", { name: "认输", exact: true }).click();
  await pages[0].getByRole("button", { name: "确认认输", exact: true }).click();
  await expect.poll(() => views[0]?.done, { timeout: 15000 }).toBe(true);
  expect(errors).toEqual([]);
  expect(
    requests.every((url) =>
      input.devices.some((d) => url.startsWith(d.origin + "/")),
    ),
  ).toBe(true);
  await writeFile(
    input.output + "/browser-result.json",
    JSON.stringify(
      {
        passed: true,
        singleOriginPerApp: true,
        sharedDecks: true,
        invitation: true,
        commands: 8,
        existingAuthorizationNewWindow: true,
        realArtwork: true,
        browserRemoteRequests: 0,
        physicalDevices: false,
        naturalEnd: false,
      },
      null,
      2,
    ),
  );
  console.log("P5.5 product GUI integration passed");
} catch (e) {
  for (let i = 0; i < pages.length; i++)
    await pages[i]
      .screenshot({ path: input.output + `/failure-${i}.png`, fullPage: true })
      .catch(() => {});
  throw e;
} finally {
  await browser.close();
}
