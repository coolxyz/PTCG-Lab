// Focused public-record presentation checks. No live profile or remote server.
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
const output=path.resolve(process.argv[2]||"var/public-records-checks");
await mkdir(output,{recursive:true});
const result=await build({stdin:{resolveDir:process.cwd(),loader:"tsx",contents:`
import React from 'react';import {createRoot} from 'react-dom/client';
import {BattleLog} from './packages/battle-ui/src/battle/BattleLog';
import {BattleArena} from './packages/battle-ui/src/battle/BattleArena';
import {DeckBrowser} from './packages/battle-ui/src/DeckBrowser';
import {indexCards,lookupCard,eventText,skillName} from './packages/battle-ui/src/battle/presentation';
const info=(engineId,englishName,cnName)=>({engineId,englishName,cnName,effectStatus:'verified',reviewNote:'',image:{url:null,label:''},chineseDetails:{sections:[]}});
const nose=info('nose','', '朝北鼻'),gym=info('gym','Spikemuth Gym','尖钉镇道馆'),grim=info('grim',"Marnie's Grimmsnarl ex",'玛俐的长毛巨魔ex');
const evo=info('PAR-178','Technical Machine: Evolution','招式学习器 进化');
const de=info('P4T-DDB79F5FA206','Technical Machine: Devolution','招式学习器 退化');
const holder={id:'TWM-095',name:'Munkidori',hp:110,ref:'v21:self:active:0',attacks:[{name:'Mind Bend',damage:60,text:''}]};
const monkey=info(holder.id,holder.name,'愿增猿');
const rows=[gym,grim,monkey,evo,de,nose,info('   ','   ','无效空键')],names=indexCards(rows);
const poisoned={...names,'':nose};
window.results={blankKeysExcluded:!Object.hasOwn(names,'')&&!Object.hasOwn(names,'   '),missingIdFallsBackToName:lookupCard({name:gym.englishName},poisoned)===gym,whitespaceIdIgnored:lookupCard({id:' ',name:grim.englishName},poisoned)===grim,exactIdWins:lookupCard({id:'gym',name:grim.englishName},poisoned)===gym,unknownNameStaysUnknown:!lookupCard({name:'Unknown'},poisoned),prototypeIsNotCard:!lookupCard({name:'toString'},poisoned),namedToolTranslated:skillName('Evolution','attack',{id:evo.engineId,name:evo.englishName,attacks:[{name:'Evolution'}]},evo)==='进化'};
const tools=[evo,de].map(c=>({id:c.engineId,name:c.englishName,attacks:[{name:c.englishName.split(': ')[1],damage:0,text:''}]}));
const events=[{seq:23,actor:'ai',turn:2,text:'使用竞技场 · Spikemuth Gym',cardGroups:[{label:'使用 / 涉及的卡牌',cards:[{name:gym.englishName}]}]},{seq:24,actor:'ai',turn:2,text:'完成选牌（1 张）',cardGroups:[{label:'AI · 按效果展示卡牌',cards:[{name:grim.englishName,set_name:'P4P',number:'5485406C2C86'}]}]},...tools.map((tool,i)=>({seq:22+i,actor:'you',turn:1,text:'攻击 · 愿增猿 · '+tool.attacks[0].name,cardGroups:[{label:'使用 / 涉及的卡牌',cards:[holder]},{label:'招式来源 · 已附加道具',cards:[tool]}]}))];
const side={active:[holder],bench:[],hand:[],hand_count:0,deck_count:40,prize_count:6,discard:[],lost_zone:[]};
const view={matchId:'fixture',stateVersion:21,phase:'playing',status:'playing',done:false,winner:null,events:[events[3]],decision:{id:'d21',kind:'options',min:0,max:1,hidden:false,candidates:[],options:tools.map((tool,i)=>({id:'a'+i,actionType:'AttackAction',source:holder.name,sourceId:holder.id,sourceRef:holder.ref,attack:tool.attacks[0],effectSource:tool}))},observation:{turn:'player1',turn_number:1,self:side,opponent:{...side,active:[],hand:null},stadium:[]}};
const mode=new URLSearchParams(location.search).get('mode');
createRoot(document.getElementById('root')).render(mode==='actions'?<BattleArena view={view} cards={names} onSubmit={()=>{}} onRefresh={()=>{}} onLeave={()=>{}}/>:mode==='deck'?<DeckBrowser inspection={{owner:'self',total:1,ordered:false,cards:[{card:{name:gym.englishName},count:1}]}} names={poisoned} selectedCount={0}/>:<BattleLog view={{...view,events:[events[Number(mode||0)]]}} cards={poisoned} viewerSeat='PLAYER1' opponentLabel='AI' replay={false}/>);
`},bundle:true,format:"esm",write:false,outdir:"/virtual",jsx:"automatic",loader:{".svg":"dataurl"}});
const files=new Map(result.outputFiles.map(f=>[path.basename(f.path),f.contents]));
const server=createServer((req,res)=>{const name=req.url.split('?')[0].slice(1);if(files.has(name)){res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');res.end(files.get(name));}else{res.setHeader('Content-Type','text/html');res.end('<meta charset="utf-8"><link rel="stylesheet" href="/stdin.css"><div id="root"></div><script type="module" src="/stdin.js"></script>');}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({channel:'chrome',headless:true});
const checks=[],errors=[];
try{
 const page=await browser.newPage({viewport:{width:1200,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));
 const open=mode=>page.goto('http://127.0.0.1:'+server.address().port+'/?mode='+mode);
 for(const [i,name] of [[0,'尖钉镇道馆'],[1,'玛俐的长毛巨魔ex']]){
  await open(i);await expect(page.getByRole('button',{name:'查看记录卡牌：'+name})).toBeVisible();
  await expect(page.getByRole('button',{name:'查看记录卡牌：朝北鼻'})).toHaveCount(0);
  await page.getByRole('button',{name:'查看记录卡牌：'+name}).click();
  await expect(page.getByRole('dialog').getByRole('heading',{name})).toBeVisible();
  await page.screenshot({path:output+'/record-'+i+'.png'});checks.push('missing-id-card-and-dialog-'+i);
 }
 for(const [i,name] of [[2,'进化'],[3,'退化']]){
  await open(i);await expect(page.getByRole('button',{name:new RegExp('攻击 · 愿增猿 · '+name)})).toBeVisible();
  await expect(page.getByText('招式来源 · 已附加道具',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'查看记录卡牌：招式学习器 '+name})).toBeVisible();
  await page.screenshot({path:output+'/tool-'+name+'.png'});checks.push('tool-title-and-source-'+name);
 }
 await open('deck');await expect(page.locator('.deck-browser-list')).toContainText('尖钉镇道馆');await expect(page.locator('.deck-browser-list')).not.toContainText('朝北鼻');checks.push('deck-browser-missing-id');
 await open('actions');
 for(const name of ['进化','退化'])await expect(page.getByRole('button',{name:new RegExp('攻击 · 愿增猿.*'+name+'（来自招式学习器 '+name+'）')})).toBeVisible();
 checks.push('tool-action-labels-and-holder');await expect(page.getByLabel('牌桌当前动作')).toContainText('攻击 · 愿增猿 · 退化');checks.push('tabletop-current-action-chinese-name');
 const units=await page.evaluate(()=>window.results);expect(Object.values(units).every(Boolean)).toBe(true);checks.push(...Object.keys(units));
 expect(errors).toEqual([]);
 await writeFile(output+'/UI-CHECKS.json',JSON.stringify({schema:1,status:'passed',checks,engine:'Chromium',liveProfileChanged:false,lanServiceChanged:false,fullRegression:false},null,2)+'\n');
 console.log(JSON.stringify({status:'passed',checks:checks.length,output}));
}finally{await browser.close();await new Promise(r=>server.close(r));}
