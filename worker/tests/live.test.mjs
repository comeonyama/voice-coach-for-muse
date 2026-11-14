import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
const source = await readFile(new URL('../worker.js', import.meta.url), 'utf8');
const worker = (await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default;
const store = new Map();
const env = {UPLOAD_TOKEN:'upload-test', READ_TOKEN:'read-test', QUEUE_BUCKET:{
  async get(key) { const value=store.get(key); return value === undefined ? null : {body:value, text:async()=>value}; },
  async put(key,value) { store.set(key,value); }
}};
const call=(path,method='GET',body,headers={})=>worker.fetch(new Request('https://example.test'+path,{method,body,headers}),env);
test('live read and write authorization, validation, timestamps and regression', async()=>{
  store.clear();
  const valid={version:1,sessionId:'s',sentAt:'2026-01-01T00:00:00Z',latest:{seq:1},storedAt:'untrusted'};
  assert.equal((await call('/live-session.json')).status,401);
  assert.equal((await call('/live-session.json','GET',undefined,{'X-Read-Token':'read-test'})).status,404);
  assert.equal((await call('/upload?op=live','POST',JSON.stringify(valid))).status,401);
  assert.equal((await call('/upload?op=live','POST',JSON.stringify(valid),{'X-Upload-Token':'wrong'})).status,401);
  const h={'X-Upload-Token':'upload-test'};
  assert.equal((await call('/upload?op=live','POST',JSON.stringify(valid),h)).status,200);
  const saved=await (await call('/live-session.json','GET',undefined,{'X-Read-Token':'read-test'})).json();
  assert.equal(saved.sessionId,valid.sessionId); assert.deepEqual(saved.latest,valid.latest);
  assert.ok(Number.isFinite(Date.parse(saved.storedAt))); assert.notEqual(saved.storedAt,'untrusted');
  assert.equal((await call('/live-session.json','GET',undefined,h)).status,200);
  for (const body of ['{', '{}', 'null', '[]', JSON.stringify({version:1,sessionId:'s',sentAt:'t'})]) assert.equal((await call('/upload?op=live','POST',body,h)).status,400);
  assert.deepEqual(await (await call('/live-session.json','GET',undefined,h)).json(),saved);
  assert.equal((await call('/queue.json')).status,401);
  assert.deepEqual(await (await call('/queue.json','GET',undefined,{'X-Read-Token':'read-test'})).json(),{session:'standby',messages:[]});
  assert.equal((await call('/session.json','GET',undefined,{'X-Read-Token':'read-test'})).status,401);
});
