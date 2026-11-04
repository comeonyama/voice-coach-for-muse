import fs from "node:fs";
import assert from "node:assert/strict";
const code = fs.readFileSync(new URL("../worker/worker.js", import.meta.url), "utf8");
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
