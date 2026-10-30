import { test } from 'node:test';
import assert from 'node:assert/strict';
import { artworkRetryDelay } from '../../packages/battle-ui/src/artwork-retry.ts';
test('transient retries are finite and increase their delays', () => {
  assert.deepEqual([0,1,2,3].map(i=>artworkRetryDelay(i)), [2000,5000,15000,null]);
});
test('permanent HTTP failures and rejected content never auto retry', () => {
  for (const status of [400,401,403,404,410,422]) assert.equal(artworkRetryDelay(0,new Response('',{status})),null);
  assert.equal(artworkRetryDelay(0,undefined,{retryable:false}),null);
});
test('Retry-After seconds, HTTP dates and API metadata are respected', () => {
  assert.equal(artworkRetryDelay(0,new Response('',{status:503,headers:{'Retry-After':'120'}})),120000);
  assert.equal(artworkRetryDelay(0,new Response('',{status:503,headers:{'Retry-After':'Thu, 01 Jan 1970 00:02:00 GMT'}}),undefined,0),120000);
  assert.equal(artworkRetryDelay(0,undefined,{retryAfterSeconds:60}),60000);
  assert.equal(artworkRetryDelay(0,undefined,{retryAfterSeconds:999999999}),null);
});
