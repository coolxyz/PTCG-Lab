(async () => {
  const result = {nonce: __NONCE__, appRendered: false, sameOriginSession: false,
    offlineBridge: false, cardCatalog: false, deckPersistence: false};
  try {
    const until = Date.now() + 25000;
    while (!document.getElementById('root')?.childElementCount && Date.now() < until)
      await new Promise(resolve => setTimeout(resolve, 100));
    result.appRendered = !!document.getElementById('root')?.childElementCount;
    const post = (url, body, extra = {}) => fetch(url, {method: 'POST', credentials: extra['X-PTCG-Bridge'] ? 'omit' : 'same-origin',
      headers: {'Content-Type': 'application/json', ...extra}, body: JSON.stringify(body)});
    const session = await post('/session', {windowId: crypto.randomUUID()});
    result.sameOriginSession = session.status === 200;
    const capability = (await session.json()).capability;
    const profiles = await post('/api/desktop/online/v1/profiles', {}, {'X-PTCG-Bridge': capability});
    result.offlineBridge = profiles.status === 200 && (await profiles.json()).result.length === 0;
    const {cards} = await (await fetch('/api/cards')).json();
    result.cardCatalog = Array.isArray(cards) && cards.length > 0;
    const name = 'B4 isolated desktop proof';
    if (__NEW_PROFILE__) {
      const saved = await post('/api/decks', {name, entries: [{printingId: cards[0].printingId, quantity: 1}]});
      result.deckPersistence = saved.status === 201;
    } else {
      const decks = await (await fetch('/api/decks')).json();
      result.deckPersistence = decks.filter(deck => deck.name === name).length === 1;
    }
  } catch (_) { /* Only fixed booleans leave the renderer, never tokens or errors. */ }
  document.title = 'PTCG-ACCEPTANCE:' + JSON.stringify(result);
})();
