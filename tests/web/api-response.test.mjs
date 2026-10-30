import { test } from 'node:test';
import assert from 'node:assert/strict';
import { apiResponse } from '../../apps/web/src/api-response.ts';

test('plain-text 500 is actionable instead of a WebKit JSON syntax error', async () => {
  await assert.rejects(apiResponse(new Response('Internal Server Error', {status: 500}), '/api/cards'), /无效响应（\/api\/cards，HTTP 500）/);
});
test('structured error retains the domain message', async () => {
  await assert.rejects(apiResponse(Response.json({message: '展示资源校验失败'}, {status: 422}), '/api/cards'), /展示资源校验失败/);
});
test('valid payload is returned', async () => {
  assert.deepEqual(await apiResponse(Response.json({cards: []}), '/api/cards'), {cards: []});
});
