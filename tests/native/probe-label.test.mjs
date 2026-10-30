// Exercise the actual native probe's DOM helpers. This is a browser regression,
// not a substitute for the packaged WKWebView full-game acceptance.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const source = await readFile('apps/native-shell/src-tauri/src/online_probe.js', 'utf8');
const prefix = source.slice(0, source.indexOf('  let view =')).replace('__NONCE__', '"synthetic-label-test"');
assert(prefix.includes('const button ='));
const browser = await chromium.launch({channel:'chrome', headless:true});
try {
  const page = await browser.newPage();
  await page.setContent(`<button disabled>校验并使用此版本</button>
    <button id="submit"> 校验并使用此版本 <span aria-hidden="true">→</span></button>
    <button id="invite">创建邀请房间 <span aria-hidden="true"><span>→</span></span></button>
    <div class="lobby-seat"><strong>等待朋友加入</strong></div>`);
  const result = await page.evaluate(prefix + `
    let submitted=0, invited=0;
    document.querySelector('#submit').onclick=()=>submitted++;
    document.querySelector('#invite').onclick=()=>invited++;
    await click('校验并使用此版本');
    await click('创建邀请房间');
    const emptyRoomRejected=!opponentJoined();
    document.querySelector('.lobby-seat').innerHTML='<strong>对手</strong><span>已准备</span>';
    const alreadyReadyAccepted=opponentJoined();
    document.querySelector('.lobby-seat span').textContent='未准备';
    const notReadyAccepted=opponentJoined();
    return {submitted, invited, decoratedLabelRejected:!button('校验并使用此版本 →'),
      originalDecorationRetained:document.querySelector('#submit span').textContent==='→',
      emptyRoomRejected,alreadyReadyAccepted,notReadyAccepted};
  })();`);
  assert.deepEqual(result, {submitted:1, invited:1, decoratedLabelRejected:true, originalDecorationRetained:true,
    emptyRoomRejected:true,alreadyReadyAccepted:true,notReadyAccepted:true});
  console.log('Native probe accessible labels, decoration and ready-state race regressions passed');
} finally {await browser.close();}
