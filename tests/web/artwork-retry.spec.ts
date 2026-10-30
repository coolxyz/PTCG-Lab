import { test, expect, type Page } from "@playwright/test";
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=", "base64");
async function fixture(page: Page, kind: string, succeedsAt = Infinity) {
  let calls = 0;
  await page.clock.install();
  await page.route("**/api/sync/artwork/**", async route => {
    const name = new URL(route.request().url()).pathname.split("/").at(-1);
    if (name !== kind || ++calls >= succeedsAt) return route.fulfill({ status: 200, contentType: "image/png", body: png });
    const status = kind === "missing" ? 404 : 503;
    await route.fulfill({ status, contentType: "application/json", headers: {"Retry-After": kind === "throttled" ? "120" : "2"},
      body: JSON.stringify({ retryable: kind !== "missing", message: "test failure", cooldownScope: kind === "throttled" ? "source" : "image" }) });
  });
  await page.goto(`/artwork-fixture.html?case=${kind}`);
  await expect.poll(() => calls).toBe(2); // native img failure, then policy probe
  return () => calls;
}

test("a transient image recovers automatically while another card stays visible", async ({page}) => {
  const calls = await fixture(page, "transient", 3);
  await expect(page.getByAltText("卡图 B")).toBeVisible();
  await expect(page.getByLabel("下载状态")).toContainText("retrying");
  await page.clock.fastForward(2100);
  await expect(page.getByLabel("下载状态")).toContainText("ready");
  await expect(page.getByAltText("卡图 A")).toBeVisible();
  await expect(page.getByAltText("卡图 A")).toHaveJSProperty("naturalWidth", 1);
  expect(calls()).toBeGreaterThanOrEqual(3);
});

test("404 remains local to the missing card and is never automatically retried", async ({page}) => {
  const calls = await fixture(page, "missing");
  await expect(page.getByLabel("下载状态")).toContainText("failed");
  await page.clock.fastForward(600000);
  expect(calls()).toBe(2);
  await expect(page.getByAltText("卡图 B")).toBeVisible();
});

test("automatic retries stop after three attempts", async ({page}) => {
  const calls = await fixture(page, "persistent");
  for (const [delay, count] of [[2100,3],[5100,4],[15100,5]]) {
    await page.clock.fastForward(delay);
    await expect.poll(calls).toBe(count);
  }
  await expect(page.getByLabel("下载状态")).toContainText("failed");
  await page.clock.fastForward(600000);
  expect(calls()).toBe(5);
});

test("source throttling respects Retry-After before attempting again", async ({page}) => {
  const calls = await fixture(page, "throttled", 3);
  await expect(page.getByLabel("下载状态")).toContainText("120 秒");
  await page.clock.fastForward(60000);
  expect(calls()).toBe(2);
  await page.clock.fastForward(61000);
  await expect(page.getByLabel("下载状态")).toContainText("ready");
});

test("changing an image cancels the old retry timer", async ({page}) => {
  const calls = await fixture(page, "changing");
  await expect(page.getByLabel("下载状态")).toContainText("retrying");
  await page.getByRole("button", {name:"切换图片"}).click();
  await expect(page.getByLabel("下载状态")).toContainText("ready");
  await page.clock.fastForward(60000);
  expect(calls()).toBe(2);
});
