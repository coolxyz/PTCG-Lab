import { readFile, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { chromium, expect } from "@playwright/test";

const input = JSON.parse(await readFile(process.argv[2], "utf8"));
const replies = createInterface({ input: process.stdin })[
  Symbol.asyncIterator
]();
async function checkpoint(action, device = 0, extra = {}) {
  console.log("P5D:" + JSON.stringify({ action, device, ...extra }));
  return JSON.parse((await replies.next()).value);
}
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PTCG_P5C_BROWSER,
});
const pages = [],
  contexts = [],
  views = [null, null],
  requests = [],
  errors = [];
const counts = [0, 0],
  choices = [],
  frames = [[], []];
function observe(page, i) {
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
      if (
        v &&
        (!views[i] ||
          (v.matchSeq >= views[i].matchSeq &&
            v.controllerEpoch >= views[i].controllerEpoch))
      ) {
        expect(v.viewerSeat).toBe(`PLAYER${i + 1}`);
        expect(v.observation.opponent.hand).toBeNull();
        expect(JSON.stringify(v)).not.toMatch(
          /accessToken|refreshToken|stateDigest|"seed"/,
        );
        views[i] = v;
      }
      if (r.url().endsWith("/command"))
        choices.push(r.request().postDataJSON().command.commandId);
    } catch (e) {
      if (!page.isClosed() && e?.name === "Error" && e.matcherResult)
        errors.push("Private projection assertion failed");
    }
  });
}
async function load(page, device) {
  await page.goto(pathToFileURL(device.launch).href, { waitUntil: "commit" });
  await page.waitForURL(device.origin + "/");
}
async function stepSeen(seq) {
  await expect
    .poll(() => views.every((v) => v && v.matchSeq === seq), { timeout: 20000 })
    .toBe(true);
  for (const p of pages)
    await expect(p.getByText(new RegExp(`第 ${seq} 步`)).first()).toBeVisible({
      timeout: 15000,
    });
}
async function control(page) {
  await page.getByRole("button", { name: "取得操作权", exact: true }).click();
  await expect(page.getByText(/本窗口可操作/)).toBeVisible();
}
async function choose(page, view) {
  const d = view.decision;
  if (d.kind === "selection") {
    for (let j = 0; j < d.min; j++)
      await page.locator(".decision-candidates button").nth(j).click();
    await page
      .getByRole("button", {
        name: d.max === 0 ? "已查看，继续结算" : `确认选择 ${d.min} 张`,
        exact: true,
      })
      .click();
  } else {
    let n = d.options.findIndex((o) => o.actionType === "PassTurn");
    await page
      .locator(".decision-options button")
      .nth(n < 0 ? 0 : n)
      .click();
  }
}
try {
  for (let i = 0; i < 2; i++) {
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 1100 },
    });
    contexts.push(ctx);
    const page = await ctx.newPage();
    pages.push(page);
    observe(page, i);
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    await load(page, input.devices[i]);
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
  await pages[0]
    .getByRole("button", { name: "创建邀请房间", exact: true })
    .click();
  const code = await pages[0].getByTestId("invite-code").innerText();
  await pages[1].getByLabel("房间码", { exact: true }).fill(code);
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
      timeout: 25000,
    });
    await control(page);
  }
  let lostActor;
  for (let step = 1; step <= 180; step++) {
    await stepSeen(step - 1);
    const actor = views.findIndex((v) => v.decision);
    expect(actor).toBeGreaterThanOrEqual(0);
    const page = pages[actor],
      view = structuredClone(views[actor]);
    if (step === 3) {
      lostActor = actor;
      await checkpoint("drop-command", actor);
    }
    if (step === 10) await checkpoint("drop-stream", 1);
    const reply = page.waitForResponse((r) => r.url().endsWith("/command"));
    await choose(page, view);
    const response = await reply;
    counts[actor]++;
    if (step === 3) {
      expect(response.status()).toBe(409);
      await expect(
        page.getByRole("button", { name: "恢复待确认操作", exact: true }),
      ).toBeVisible();
      const restarted = await checkpoint("restart", actor);
      await load(page, restarted);
      await expect(
        page.getByRole("button", { name: "恢复待确认操作", exact: true }),
      ).toBeVisible({ timeout: 15000 });
      await expect(
        page.getByRole("button", { name: "取得操作权", exact: true }),
      ).toBeDisabled();
      await page
        .getByRole("button", { name: "恢复待确认操作", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "恢复待确认操作", exact: true }),
      ).toHaveCount(0);
      await control(page);
    } else expect(response.status()).toBe(200);
    await stepSeen(step);
    if (step === 8) {
      for (const p of pages) {
        const img = p.locator(".tabletop-card img").first();
        await img.scrollIntoViewIfNeeded();
        await expect
          .poll(() => img.evaluate((x) => x.naturalWidth))
          .toBeGreaterThan(0);
      }
      await checkpoint("offline-images");
      for (const p of pages) {
        await p.reload();
        await expect(p.getByRole("main", { name: "对战与回放" })).toBeVisible();
        await control(p);
        const img = p.locator(".tabletop-card img").first();
        await img.scrollIntoViewIfNeeded();
        await expect
          .poll(() => img.evaluate((x) => x.naturalWidth))
          .toBeGreaterThan(0);
      }
    }
    if (step === 12) {
      const other = await contexts[1].newPage();
      observe(other, 1);
      await other.goto(input.devices[1].origin);
      await other
        .getByLabel("已有对局编号", { exact: true })
        .fill(views[1].matchId);
      await other
        .getByRole("button", { name: "打开我的对局", exact: true })
        .click();
      await control(other);
      await expect(pages[1].getByText(/玩家 2 · 只读观察/)).toBeVisible({
        timeout: 15000,
      });
      await other.close();
      await control(pages[1]);
    }
    if (step % 20 === 0) console.log(`P5-D GUI: ${step} commands accepted`);
    if (views.every((v) => v.done)) break;
  }
  expect(views.every((v) => v.done)).toBe(true);
  expect(counts.every((n) => n > 0)).toBe(true);
  expect(views[0].winner).toBe(views[1].winner);
  expect(["resigned", "technical_abort", "truncated", "unknown"]).not.toContain(
    views[0].result.reason,
  );
  const commands = counts[0] + counts[1];
  expect(views[0].matchSeq).toBe(commands);
  expect(new Set(choices).size).toBe(commands - 1); // the one lost native reply was recovered, never resubmitted
  for (let i = 0; i < 2; i++) {
    const page = pages[i];
    page.on("response", async (r) => {
      if (r.url().endsWith("/replay") && r.status() === 200) {
        const data = (await r.json()).result;
        expect(data.viewerSeat).toBe(`PLAYER${i + 1}`);
        frames[i].push(...data.frames);
      }
    });
    await page.getByRole("button", { name: "逐步回放", exact: true }).click();
    await expect(page.getByText(/只读回放/)).toBeVisible();
    await expect.poll(() => frames[i].length).toBeGreaterThan(0);
    while (frames[i].length < commands + 1) {
      const before = frames[i].length;
      const r = page.waitForResponse((r) => r.url().endsWith("/replay"));
      await page
        .getByRole("button", { name: "加载更多回放", exact: true })
        .click();
      expect((await r).status()).toBe(200);
      await expect.poll(() => frames[i].length).toBeGreaterThan(before);
    }
    await expect.poll(() => frames[i].length).toBe(commands + 1);
    expect(frames[i].map((f) => f.matchSeq)).toEqual(
      Array.from({ length: commands + 1 }, (_, n) => n),
    );
    expect(
      frames[i].every(
        (f) => !f.decision && f.observation.opponent.hand === null,
      ),
    ).toBe(true);
    expect(frames[i].at(-1).result.reason).toBe(views[i].result.reason);
    const slider = page.getByLabel("回放步数");
    await slider.fill(String(commands));
    await expect(
      page.getByRole("button", { name: "认输", exact: true }),
    ).toHaveCount(0);
    await page.screenshot({
      path: input.output + `/p5d-final-${i}.png`,
      fullPage: true,
    });
    const stored = await page.evaluate(() =>
      JSON.stringify({ ...sessionStorage, ...localStorage }),
    );
    expect(stored).not.toMatch(/accessToken|refreshToken|capability|lease/);
  }
  expect(
    requests.every((url) =>
      input.devices.some((d) => url.startsWith(d.origin + "/")),
    ),
  ).toBe(true);
  expect(requests.some((url) => /\/api\/battle|\/advance/.test(url))).toBe(
    false,
  );
  expect(errors).toEqual([]);
  const evidence = await checkpoint("evidence", 0, { commands });
  await writeFile(
    input.output + "/p5d-gui-report.json",
    JSON.stringify(
      {
        status: "passed",
        physicalDevices: false,
        nativeProcesses: 2,
        natural: true,
        reason: views[0].result.reason,
        commands,
        commandsBySeat: counts,
        privateReplayFrames: frames.map((x) => x.length),
        lostReplyRestartRecovered: true,
        restartedSeat: lostActor + 1,
        streamDisconnected: true,
        multiWindowFencing: true,
        offlineCacheWithBrowserCacheDisabled: true,
        browserRemoteRequests: 0,
        errors: 0,
        ...evidence,
      },
      null,
      2,
    ),
  );
} catch (e) {
  for (let i = 0; i < pages.length; i++)
    await pages[i]
      .screenshot({
        path: input.output + `/p5d-failure-${i}.png`,
        fullPage: true,
      })
      .catch(() => {});
  throw e;
} finally {
  await browser.close();
  process.stdin.destroy();
}
