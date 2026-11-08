import fs from "node:fs";
import assert from "node:assert/strict";
const code = fs.readFileSync(new URL("../../worker/worker.js", import.meta.url), "utf8");
const {default: worker} = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
let queue = JSON.stringify({session:"s",messages:[]});
const deleted=[];
const objects = [
  {key:"audio/new.mp3", uploaded:new Date()},
  {key:"audio/old.mp3", uploaded:new Date(Date.now()-3600000)}
];
const bucket={
  get:async()=>({text:async()=>queue}),
  list:async()=>({objects,truncated:false}),
  head:async key=>objects.find(o=>o.key===key),
  delete:async key=>deleted.push(key)
};
const request=()=>new Request("https://fixture/upload?op=cleanup",{
  method:"POST",headers:{"X-Upload-Token":"fixture"},body:'{"min_age_sec":0}'
});
assert.equal((await worker.fetch(request(),{QUEUE_BUCKET:bucket,UPLOAD_TOKEN:"fixture"})).status,200);
assert.deepEqual(deleted,["audio/old.mp3"]);
deleted.length=0;queue="{";
assert.equal((await worker.fetch(request(),{QUEUE_BUCKET:bucket,UPLOAD_TOKEN:"fixture"})).status,409);
assert.deepEqual(deleted,[]);
console.log("PASS cleanup grace and invalid queue");
const store = new Map();
const statusEnv = {UPLOAD_TOKEN:'fixture',READ_TOKEN:'reader',QUEUE_BUCKET:{put:async(k,v)=>store.set(k,v),get:async k=>store.has(k)?{text:async()=>store.get(k)}:null}};
const sid='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const payload={start_id:sid,state:'monitoring',at:new Date().toISOString(),token:'secret',session_id:'private'};
const writeStatus=token=>new Request('https://fixture/upload?op=start-status',{method:'POST',headers:{'X-Upload-Token':token},body:JSON.stringify(payload)});
assert.equal((await worker.fetch(writeStatus('wrong'),statusEnv)).status,401);
assert.equal((await worker.fetch(writeStatus('fixture'),statusEnv)).status,200);
assert.equal((await worker.fetch(new Request('https://fixture/start-status.json'),statusEnv)).status,401);
const read=await worker.fetch(new Request('https://fixture/start-status.json',{headers:{'X-Read-Token':'reader'}}),statusEnv);
assert.equal(read.headers.get('Cache-Control'),'no-store');
assert.deepEqual(Object.keys(await read.json()).sort(),['at','start_id','state']);
assert.equal((await worker.fetch(new Request('https://fixture/session.json'),statusEnv)).status,401);
payload.state='invalid';assert.equal((await worker.fetch(writeStatus('fixture'),statusEnv)).status,400);
console.log('PASS authenticated status writes and reads, private session');
for (const path of ['/queue.json','/history.json','/audio/example.mp3']) {
  assert.equal((await worker.fetch(new Request('https://fixture'+path),statusEnv)).status,401);
  assert.notEqual((await worker.fetch(new Request('https://fixture'+path,{headers:{'X-Read-Token':'reader'}}),statusEnv)).status,401);
}
assert.equal((await worker.fetch(new Request('https://fixture/session.json',{headers:{'X-Read-Token':'reader'}}),statusEnv)).status,401);
assert.equal((await worker.fetch(new Request('https://fixture/queue.json',{headers:{'X-Read-Token':'reader'}}),{...statusEnv,READ_TOKEN:undefined})).status,401);
assert.notEqual((await worker.fetch(new Request('https://fixture/queue.json',{headers:{'X-Upload-Token':'fixture'}}),statusEnv)).status,401);
console.log('PASS read authorization and read-only token isolation');

const upload=(op,body,token='fixture')=>worker.fetch(new Request('https://fixture/upload?op='+op,{
 method:'POST',headers:{'X-Upload-Token':token},body:JSON.stringify(body)}),statusEnv);
for(const preference of ['per_km','per_500m','interval:180','invalid',null]) {
 assert.equal((await upload('session',{session_id:'run',token:'private',start_id:sid,speech_interval:preference})).status,200);
 const stored=JSON.parse(store.get('session.json'));
 assert.equal(stored.speech_interval,preference==='invalid'||preference===null?undefined:preference);
}
await upload('session',{session_id:'run',token:'private',start_id:sid,speech_interval:'per_km'});
const before=JSON.parse(store.get('session.json'));
assert.equal((await upload('session-settings',{start_id:sid,speech_interval:'per_500m'},'wrong')).status,401);
assert.equal((await upload('session-settings',{start_id:'other',speech_interval:'per_500m'})).status,409);
assert.equal((await upload('session-settings',{start_id:sid,speech_interval:'per_500m'})).status,200);
assert.deepEqual(JSON.parse(store.get('session.json')),{...before,speech_interval:'per_500m'});
await upload('session-settings',{start_id:sid,speech_interval:'bad'});
assert.equal(JSON.parse(store.get('session.json')).speech_interval,'per_500m');
assert.equal((await worker.fetch(new Request('https://fixture/session.json'),statusEnv)).status,401);
console.log('PASS cadence allowlist, authenticated same-start update, preserved session and timestamp');

const conflictEnv={UPLOAD_TOKEN:'fixture',QUEUE_BUCKET:{
 get:async()=>({etag:'original',text:async()=>JSON.stringify(before)}),
 put:async(k,v,options)=>{assert.equal(options.onlyIf.etagMatches,'original');return null;}
}};
assert.equal((await worker.fetch(new Request('https://fixture/upload?op=session-settings',{
 method:'POST',headers:{'X-Upload-Token':'fixture'},body:JSON.stringify({start_id:sid,speech_interval:'per_500m'})
}),conflictEnv)).status,409);
console.log('PASS concurrent session replacement rejects stale settings write');
