// Operator-only test harness. Secrets arrive on private stdin and are never logged.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
let input='';for await(const block of process.stdin)input+=block;
const start=JSON.parse(input);
const browser=await chromium.launch({headless:true,...(start.browserChannel ? {channel:start.browserChannel} : {})});
try {
  const context=await browser.newContext();
  await context.addCookies([{name:'ptcg-local',value:start.cookie,url:start.origin,httpOnly:true,sameSite:'Strict'}]);
  const page=await context.newPage();
  const state=()=>page.evaluate(async()=> (await fetch('/api/sync/resources')).json());
  const sync=async()=>{
    await page.getByRole('button',{name:'数据与对战更新',exact:true}).click();
    await page.getByRole('heading',{name:'卡图与展示资源',exact:true}).waitFor();
  };
  await page.goto(start.origin,{waitUntil:'networkidle'});
  await sync();
  await page.getByLabel('资源 ZIP',{exact:true}).setInputFiles(start.pack);
  await page.getByLabel('资源 SHA-256',{exact:true}).fill(start.pin);
  await page.getByRole('button',{name:'校验并安装资源',exact:true}).click();
  await page.getByRole('button',{name:'启用此展示',exact:true}).waitFor();
  let value=await state();assert.equal(value.packs.length,1);assert.equal(value.activePack,null);
  const id=value.packs[0].id;
  await Promise.all([page.waitForEvent('load'), page.getByRole('button',{name:'启用此展示',exact:true}).click()]);
  await page.waitForLoadState('networkidle');
  await sync();
  await page.getByText('使用中',{exact:true}).waitFor();
  value=await state();assert.equal(value.activePack,id);
  const selected=await page.evaluate(async pid=>(await (await fetch('/api/cards')).json()).cards.find(c=>c.printingId===pid),start.printingId);
  assert.equal(selected.cnName,'C2 isolated display proof');
  assert.equal(selected.engineId,start.engineId);assert.equal(selected.effectStatus,start.effectStatus);
  const image=selected.image.url;
  const imageStatus=await page.evaluate(async url=>(await fetch(url)).status,image);assert.equal(imageStatus,200);
  await Promise.all([page.waitForEvent('load'), page.getByRole('button',{name:'恢复内置展示',exact:true}).click()]);
  await page.waitForLoadState('networkidle');
  await sync();
  value=await state();assert.equal(value.activePack,null);
  assert.equal(await page.evaluate(async url=>(await fetch(url)).status,image),200);
  await Promise.all([page.waitForEvent('load'), page.getByRole('button',{name:'启用此展示',exact:true}).click()]);
  await page.waitForLoadState('networkidle');
  await sync();await page.getByText('使用中',{exact:true}).waitFor();
  if(start.screenshot)await page.screenshot({path:start.screenshot,fullPage:true});
  console.log(JSON.stringify({installedGUI:true,atomicSwitch:true,historicalDisplay:true,noRuleGrant:true,packId:id,image}));
  await context.close();
} finally {await browser.close();}
