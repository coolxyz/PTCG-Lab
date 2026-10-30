// Focused presentation checks only. No live API, player profile or full game.
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
const root=process.cwd(), output=path.resolve(process.argv[2]||"var/ui-fixes");
await mkdir(output,{recursive:true});
const result=await build({entryPoints:["tests/ui/tabletop.fixture.tsx"],bundle:true,write:false,outdir:"/virtual",jsx:"automatic",loader:{".png":"dataurl"}});
const files=new Map(result.outputFiles.map(f=>[path.basename(f.path),f.contents]));
const server=createServer(async(req,res)=>{
 try {
  const url=new URL(req.url,"http://localhost").pathname;
  if(url==="/") {res.setHeader("Content-Type","text/html");res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/tabletop.fixture.css"><style>body{margin:0;padding:16px;background:#eceaf0}*{box-sizing:border-box}</style><div id="root"></div><script src="/tabletop.fixture.js"></script>');}
  else if(url==="/tabletop.fixture.js"||url==="/tabletop.fixture.css") {res.setHeader("Content-Type",url.endsWith("css")?"text/css":"text/javascript");res.end(files.get(url.slice(1)));}
  else {const name=url==="/test-card.png"?"icon.png":url==="/splash"?"index.html":url.slice(1);if(!["index.html","style.css","icon.png"].includes(name))throw Error("not found");res.setHeader("Content-Type",name.endsWith("html")?"text/html":name.endsWith("css")?"text/css":"image/png");res.end(await readFile(path.join(root,"apps/native-shell/ui",name)));}
 } catch {res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({channel:"chrome",headless:true}),page=await browser.newPage({viewport:{width:1200,height:1600}});
const errors=[],checks=[];page.on("pageerror",e=>errors.push(e.message));
try {
 await page.goto(origin);await expect(page.locator(".tabletop-card").first()).toBeVisible();
 const card=n=>page.locator(".tabletop-hand .tabletop-card").nth(n);
 const reset=async(data={})=>{await page.evaluate(data=>window.dispatchEvent(new CustomEvent("fixture",{detail:data})),data);await page.mouse.move(0,0);await expect(page.locator("#played")).toHaveText("[]");};
 const start=async(n)=>{await card(n).scrollIntoViewIfNeeded();const r=await card(n).boundingBox();await page.mouse.move(r.x+r.width/2,r.y+r.height/2);await page.mouse.down();await page.mouse.move(r.x+r.width/2+12,r.y+r.height/2,{steps:4});await expect(page.locator(".tabletop-drag-preview")).toHaveCount(1);};
 const finish=async(target)=>{await target.scrollIntoViewIfNeeded();const b=await target.boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:12});await page.mouse.up();await expect(page.locator(".tabletop-drag-preview")).toHaveCount(0);};
 for(const width of [1200,900]) {
  await page.setViewportSize({width,height:1600});await card(0).hover();await page.waitForTimeout(220);
  const r=await card(0).evaluate(el=>{const a=el.getBoundingClientRect(),b=el.closest(".tabletop-hand").querySelector(":scope > div").getBoundingClientRect();return {top:a.top-b.top,left:a.left-b.left,bottom:b.bottom-a.bottom,scroll:el.closest(".tabletop-hand").querySelector(":scope > div").scrollLeft};});
  expect(r.top).toBeGreaterThan(6);expect(r.left).toBeGreaterThan(6);expect(r.bottom).toBeGreaterThan(6);
 }
 await page.screenshot({path:output+"/hand-hover.png",fullPage:true});checks.push("hover-within-scrollport-1200-and-900");
 await page.setViewportSize({width:1200,height:1600});
 await start(0);await finish(page.locator('.own [data-card-drop="bench"]'));await expect(page.locator("#played")).toHaveText('["bench"]');checks.push("basic-to-bench-exactly-once");
 await reset();await start(1);await finish(page.locator('[data-card-drop="test:self:active:0"]'));await expect(page.locator("#played")).toHaveText('["energy"]');checks.push("energy-to-exact-target");
 await reset();await start(2);await finish(page.locator('[data-card-drop="stadium"]'));await expect(page.locator("#played")).toHaveText('["stadium"]');checks.push("stadium-zone");
 await reset();await start(3);await finish(page.locator('[data-card-drop="play"]'));await expect(page.locator("#played")).toHaveText('["supporter"]');checks.push("targetless-trainer-zone");
 await reset();await start(4);await finish(page.locator('[data-card-drop="play"]'));await expect(page.locator("dialog")).toBeVisible();await expect(page.locator("#played")).toHaveText("[]");await page.getByRole("button",{name:"第二种特性"}).click();await expect(page.locator("#played")).toHaveText('["ability-2"]');checks.push("active-pokemon-source-ambiguous-choice");
 await reset();await start(1);await finish(page.locator('[data-card-drop="test:opponent:active:0"]'));await expect(page.locator("#played")).toHaveText("[]");await expect(page.locator("dialog")).not.toBeVisible();checks.push("invalid-drop-no-action-no-click");
 await reset();await start(0);await page.keyboard.press("Escape");await page.mouse.up();await expect(page.locator(".tabletop-drag-preview")).toHaveCount(0);await expect(page.locator("#played")).toHaveText("[]");checks.push("escape-cancel");
 await reset();await start(0);await reset({view:{stateVersion:2}});await finish(page.locator('.own [data-card-drop="bench"]'));await expect(page.locator("#played")).toHaveText("[]");checks.push("stale-decision-cancel");
 await reset();await start(0);await reset({locked:true});await finish(page.locator('.own [data-card-drop="bench"]'));await expect(page.locator("#played")).toHaveText("[]");checks.push("lock-or-replay-cancel");
 await reset({view:{phase:"active",decision:{id:"setup",kind:"selection",min:1,max:1,candidates:[{ref:"candidate",boardRef:"test:self:hand:0"}],options:[]}}});
 await start(0);await finish(page.locator('.own [data-card-drop="active"]'));await expect(page.locator("#selected")).toHaveText('["candidate"]');await expect(page.locator("#played")).toHaveText("[]");checks.push("setup-selects-without-auto-confirm");
 await reset();await card(0).click();await expect(page.locator("dialog")).toBeVisible();checks.push("click-remains-available");
 await page.setViewportSize({width:1280,height:850});await page.goto(origin+"/splash");await expect(page.getByRole("heading",{name:"正在准备你的牌桌"})).toBeVisible();await page.screenshot({path:output+"/startup.png"});
 for(const [width,height] of [[900,600],[640,600]]) {await page.setViewportSize({width,height});const dims=await page.evaluate(()=>({w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,vw:innerWidth,vh:innerHeight}));expect(dims.w,JSON.stringify(dims)).toBeLessThanOrEqual(width);expect(dims.h,JSON.stringify(dims)).toBeLessThanOrEqual(height);}
 await page.emulateMedia({reducedMotion:"reduce"});expect(await page.locator(".progress span").evaluate(el=>getComputedStyle(el).animationName)).toBe("none");checks.push("startup-responsive-reduced-motion");
 await page.evaluate(()=>{document.body.classList.add("is-error");document.querySelector("#startup-title").textContent="暂时无法启动牌桌";document.querySelector("#startup-message").textContent="本机服务启动失败。请关闭后重试；本机卡组和数据不会受影响。";});await expect(page.locator(".progress")).not.toBeVisible();await page.screenshot({path:output+"/startup-error.png"});checks.push("startup-error-state");
 expect(errors).toEqual([]);await writeFile(output+"/UI-CHECKS.json",JSON.stringify({schema:1,passed:true,engine:"Chromium",checks,scope:"focused-ui-only",fullRegression:false},null,2)+"\n");console.log(JSON.stringify({passed:true,checks:checks.length,output}));
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
