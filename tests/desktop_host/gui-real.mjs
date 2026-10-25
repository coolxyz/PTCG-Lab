import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { chromium, expect } from "@playwright/test";
const input = JSON.parse(await readFile(process.argv[2], "utf8"));
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PTCG_P5C_BROWSER,
});
const contexts = [],
  pages = [],
  requests = [],
  errors = [];
try {
  for (let i = 0; i < 2; i++) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    contexts.push(ctx);
    const page = await ctx.newPage();
    pages.push(page);
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (/^https?:/.test(r.url()))
        requests.push({ url: r.url(), body: r.postData() || "" });
    });
    await page.goto(pathToFileURL(input.devices[i].launch).href, {
      waitUntil: "commit",
    });
    await page.waitForURL(input.devices[i].origin + "/");
    await page
      .getByRole("button", { name: "创建游客身份", exact: true })
      .click();
    await expect(
      page.getByText("游客身份已连接", { exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("卡组清单", { exact: true })
      .fill(JSON.stringify(input.decks[i]));
    await page
      .getByRole("button", { name: "校验并提交卡组", exact: true })
      .click();
    await expect(page.getByText("卡组版本已就绪", { exact: true })).toBeVisible(
      { timeout: 20000 },
    );
  }
  await pages[0].screenshot({
    path: input.output + "/lobby.png",
    fullPage: true,
  });
  await pages[0]
    .getByRole("button", { name: "创建邀请房间", exact: true })
    .click();
  const code = await pages[0].getByTestId("invite-code").innerText();
  await pages[1].getByLabel("房间码", { exact: true }).fill(code);
  await pages[1].getByRole("button", { name: "加入房间", exact: true }).click();
  await expect(pages[1].getByTestId("invite-code")).toHaveText(code);
  await expect(
    pages[0].getByText("对手：未准备", { exact: true }),
  ).toBeVisible();
  await pages[0].getByRole("button", { name: "准备对战", exact: true }).click();
  await expect(
    pages[1].getByText("对手：已准备", { exact: true }),
  ).toBeVisible();
  await pages[1].getByRole("button", { name: "准备对战", exact: true }).click();
  for (const page of pages)
    await expect(page.getByRole("main", { name: "对战与回放" })).toBeVisible({
      timeout: 25000,
    });
  await expect(pages[1].getByText(/玩家 2 · 只读观察/)).toBeVisible();
  await pages[1].getByRole("button", { name: "全屏显示", exact: true }).click();
  await expect
    .poll(() => pages[1].evaluate(() => !!document.fullscreenElement))
    .toBe(true);
  await pages[1].evaluate(() => document.exitFullscreen());
  // Commit one real decision through the shared Arena before testing recovery/replay.
  for (const page of pages) {
    await page.getByRole("button", { name: "取得操作权", exact: true }).click();
    await expect(page.getByText(/本窗口可操作/)).toBeVisible();
  }
  let actor;
  for (const page of pages)
    if (
      await page.getByRole("button", { name: "选择先攻", exact: true }).count()
    )
      actor = page;
  expect(actor).toBeTruthy();
  const committed = actor.waitForResponse(
    (r) => r.url().endsWith("/command") && r.status() === 200,
  );
  await actor.getByRole("button", { name: "选择先攻", exact: true }).click();
  await committed;
  await expect(pages[1].getByText(/第 1 步/).first()).toBeVisible({
    timeout: 15000,
  });
  await pages[1]
    .getByRole("button", { name: "取得操作权", exact: true })
    .click();
  await expect(pages[1].getByText(/玩家 2 · 本窗口可操作/)).toBeVisible();
  await pages[1].screenshot({
    path: input.output + "/arena-player2.png",
    fullPage: true,
  });
  await pages[1].reload();
  await expect(pages[1].getByRole("main", { name: "对战与回放" })).toBeVisible({
    timeout: 15000,
  });
  await expect(pages[1].getByText(/玩家 2 · 只读观察/)).toBeVisible();
  await pages[1]
    .getByRole("button", { name: "取得操作权", exact: true })
    .click();
  await expect(pages[1].getByText(/本窗口可操作/)).toBeVisible();
  await pages[1].getByRole("button", { name: "认输", exact: true }).click();
  await pages[1].getByRole("button", { name: "确认认输", exact: true }).click();
  await expect(pages[1].getByText(/认输结束/).first()).toBeVisible({
    timeout: 20000,
  });
  await pages[1].getByRole("button", { name: "逐步回放", exact: true }).click();
  await expect(pages[1].getByLabel("回放步数")).toBeVisible();
  await expect(pages[1].getByText(/玩家 2 · 只读回放/)).toBeVisible();
  await expect(
    pages[1].getByRole("button", { name: "认输", exact: true }),
  ).toHaveCount(0);
  await pages[1].setViewportSize({ width: 900, height: 700 });
  await pages[1].screenshot({
    path: input.output + "/replay.png",
    fullPage: true,
  });
  // No browser connection to remote battle service, image origin, AI or old practice API.
  expect(
    requests.every((r) =>
      input.devices.some((d) => r.url.startsWith(d.origin + "/")),
    ),
  ).toBeTruthy();
  expect(
    requests.some((r) => /\/api\/battle|\/advance/.test(r.url)),
  ).toBeFalsy();
  expect(
    requests.some((r) => /accessToken|refreshToken/.test(r.body)),
  ).toBeFalsy();
  for (const page of pages) {
    const values = await page.evaluate(() =>
      JSON.stringify({
        session: { ...sessionStorage },
        local: { ...localStorage },
      }),
    );
    expect(values).not.toMatch(/accessToken|refreshToken|capability|lease/);
  }
  expect(errors).toEqual([]);
  await writeFile(
    input.output + "/browser-report.json",
    JSON.stringify(
      {
        status: "passed",
        devices: 2,
        physicalDevices: false,
        invite: true,
        player2: true,
        fullscreen: true,
        reload: true,
        command: true,
        resign: true,
        readOnlyReplay: true,
        browserRemoteRequests: 0,
        errors: errors.length,
      },
      null,
      2,
    ),
  );
  console.log(
    "Two local browser windows: invitation, PLAYER2, refresh, resign, private replay and credential boundaries passed.",
  );
} catch (e) {
  for (let i = 0; i < pages.length; i++)
    await pages[i]
      .screenshot({ path: input.output + `/failure-${i}.png`, fullPage: true })
      .catch(() => {});
  await writeFile(
    input.output + "/failure-codes.json",
    JSON.stringify({ errors, urls: pages.map((p) => p.url()) }),
  );
  throw e;
} finally {
  await browser.close();
}
