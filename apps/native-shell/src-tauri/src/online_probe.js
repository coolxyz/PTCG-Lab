// Fixed opt-in P8-D journey. Runs only in a marked, fresh profile with an
// explicitly pinned loopback TLS server. No tokens or private cards leave JS.
(async () => {
  const nonce = __NONCE__;
  const emit = (stage, value = {}) => { document.title = 'PTCG-P8D:' + JSON.stringify({nonce, stage, ...value}); };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const wait = async (fn, timeout = 40000) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) { const value = fn(); if (value) return value; await sleep(80); }
    throw new Error('bounded-ui-timeout');
  };
  const buttons = () => Array.from(document.querySelectorAll('button'));
  const accessibleText = element => {
    const copy = element.cloneNode(true);
    copy.querySelectorAll('[aria-hidden="true"]').forEach(node => node.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };
  const button = text => buttons().find(b => accessibleText(b) === text && !b.disabled && b.getClientRects().length);
  const opponentJoined = () => Array.from(document.querySelectorAll('.lobby-seat')).some(element => element.textContent.includes('对手'));
  const click = async text => { (await wait(() => button(text))).click(); await sleep(100); };
  const select = async (label, test) => {
    const found = await wait(() => {
      const element = document.querySelector(`select[aria-label="${label}"]`);
      const option = element && !element.disabled && element.getClientRects().length && Array.from(element.options).find(o => !o.disabled && o.value && test(o));
      return option && {element, option};
    });
    found.element.value = found.option.value;
    found.element.dispatchEvent(new Event('change', {bubbles: true}));
    await sleep(150);
  };
  let view = null, phase = 'bootstrap';
  const replay = new Map();
  const original = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await original(...args);
    const url = typeof args[0] === 'string' ? args[0] : args[0].url;
    if (url.includes('/api/desktop/online/v1/') && response.ok) {
      response.clone().json().then(({result}) => {
        const v = result?.snapshot || result?.event?.snapshot || (result?.observation ? result : null);
        if (v && (!view || v.matchSeq >= view.matchSeq)) view = v;
        if (url.endsWith('/replay') && result?.frames)
          for (const frame of result.frames) replay.set(frame.matchSeq, frame);
      }).catch(() => {});
    }
    return response;
  };
  try {
    await wait(() => document.getElementById('root')?.childElementCount);
    const post = (url, body, headers = {}) => original(url, {method:'POST',credentials:headers['X-PTCG-Bridge'] ? 'omit' : 'same-origin',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)});
    const session = await post('/session', {windowId:crypto.randomUUID()});
    if (!session.ok) throw new Error('session');
    const capability = (await session.json()).capability;
    const profiles = await post('/api/desktop/online/v1/profiles', {}, {'X-PTCG-Bridge':capability});
    const list = (await profiles.json()).result;
    if (!profiles.ok || list.length !== 1 || list[0].id !== 'acceptance' || !list[0].origin.startsWith('https://127.0.0.1:')) throw new Error('loopback');
    const start = new Promise(resolve => window.addEventListener('ptcg-p8d-start', resolve, {once:true}));
    emit('ready');
    await start;
    phase = 'exports';
    emit('progress', {phase});
    location.hash = 'collection';
    for (const href of ['/api/collection/export', '/api/card-image-gaps']) {
      const link = await wait(() => document.querySelector(`a[download][href="${href}"]`));
      const completed = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('export-timeout')), 30000);
        window.addEventListener('ptcg-export-complete', event => {
          clearTimeout(timer);
          event.detail === true ? resolve() : reject(new Error('export-failed'));
        }, {once:true});
      });
      link.click();
      await completed;
      if (location.hash !== '#collection' || !document.getElementById('root')?.childElementCount)
        throw new Error('export-replaced-product');
    }
    phase = 'deck';
    emit('progress', {phase});
    location.hash = 'online';
    await click('创建游客身份');
    await wait(() => document.body.textContent.includes('游客身份已连接'));
    await select('平台已有卡组', o => o.textContent.includes('P8D Native'));
    await select('冻结版本', () => true);
    await click('校验并使用此版本');
    await wait(() => document.body.textContent.includes('卡组版本已就绪'));
    phase = 'room';
    emit('progress', {phase});
    await click('创建邀请房间');
    const code = await wait(() => document.querySelector('[data-testid="invite-code"]')?.textContent.trim());
    emit('invite', {inviteCode:code});
    await wait(opponentJoined, 90000);
    await click('准备对战');
    await wait(() => document.querySelector('main[aria-label="对战与回放"]'), 90000);
    await click('取得操作权');
    await wait(() => document.body.textContent.includes('本窗口可操作'));
    phase = 'commands';
    emit('progress', {phase});
    let ownCommands = 0;
    const deadline = Date.now() + 12 * 60000;
    while (!view?.done && Date.now() < deadline) {
      if (!view || !view.decision) { await sleep(80); continue; }
      if (view.viewerSeat !== 'PLAYER1' || view.observation.opponent.hand !== null) throw new Error('private-projection');
      const seq = view.matchSeq, decision = view.decision;
      await wait(() => document.body.textContent.includes(`第 ${seq} 步`));
      if (decision.kind === 'selection') {
        await wait(() => document.querySelectorAll('.decision-candidates button').length >= decision.min);
        for (let j=0; j<decision.min; j++) {
          document.querySelectorAll('.decision-candidates button')[j].click(); await sleep(100);
        }
        await click(decision.max === 0 ? '已查看，继续结算' : `确认选择 ${decision.min} 张`);
      } else {
        const index = Math.max(0, decision.options.findIndex(o => o.actionType === 'PassTurn'));
        const option = await wait(() => { const b = document.querySelectorAll('.decision-options button')[index]; return b && !b.disabled && b; });
        option.click();
      }
      await wait(() => view?.matchSeq > seq, 45000);
      ownCommands++;
    }
    if (!view?.done || !ownCommands || !['PLAYER1','PLAYER2'].includes(view.winner) || ['unknown','resigned','technical_abort','truncated'].includes(view.result?.reason)) throw new Error('natural-end');
    const finalSeq = view.matchSeq;
    phase = 'replay';
    emit('progress', {phase});
    replay.clear();
    await click('逐步回放');
    const range = await wait(() => document.body.textContent.includes('只读回放') && document.querySelector('input[aria-label="回放步数"]'));
    while (Number(range.max) < finalSeq) {
      const before = Number(range.max);
      (await wait(() => button('加载更多回放'), 100000)).click();
      await wait(() => Number(range.max) > before, 100000);
    }
    await wait(() => replay.size === finalSeq + 1);
    const frames = [...replay.values()].sort((a,b) => a.matchSeq-b.matchSeq);
    if (frames.length !== finalSeq+1 || frames.some((f,i) => f.matchSeq !== i || f.decision || f.observation.opponent.hand !== null)) throw new Error('private-replay');
    if (button('认输')) throw new Error('replay-writable');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(range, String(finalSeq));
    range.dispatchEvent(new Event('input', {bubbles:true}));
    range.dispatchEvent(new Event('change', {bubbles:true}));
    await wait(() => document.body.textContent.includes(`回放 · 第 ${finalSeq} 步`));
    emit('complete', {commands:finalSeq, checks:{nativeLobby:true,sharedDeck:true,invite:true,legalCommands:true,naturalEnd:true,privateReplay:true,replayReadOnly:true}});
  } catch (_) { emit('failed', {phase}); }
})();
