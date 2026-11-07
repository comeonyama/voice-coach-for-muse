const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const start_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const since=()=>new Date().toISOString();
function fixture(properties = {}) {
  const p = {WORKER_BASE:'https://fixture.workers.dev', KICK_SECRET:'fixture', ...properties};
  const state = {messages:[], posts:0, status:200, pages:0, requests:[]};
  const c = {
    LockService:{getScriptLock:()=>({waitLock:()=>{},releaseLock:()=>{}})},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>p[k],setProperty:(k,v)=>p[k]=v})},
    Logger:{log:()=>{}},
    ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})},
    GmailApp:{search:(q,offset,size)=>{state.pages++;return state.messages.slice(offset,offset+size).map(m=>({getMessages:()=>[m]}));}},
    UrlFetchApp:{fetch:(url,options)=>{state.requests.push({url,body:JSON.parse(options.payload)});state.posts++;return {getResponseCode:()=>state.status,getContentText:()=> 'fixture failure'};}}
  };
  vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/livetrack_detector.gs','utf8'),c);
  return {p,state,c};
}
function mail(id,age=1) {return {getId:()=>id,getDate:()=>new Date(Date.now()-age*60000),getBody:()=> 'https://livetrack.garmin.com/session/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/token/abcdef'};}
{
 const {p,state,c}=fixture({LAST_MAIL_ID:'old'});state.messages=[mail('old')];
 assert.equal(c.findAndPost_(start_id,since()).found,false);assert.deepEqual(JSON.parse(p.PROCESSED_MAIL_IDS),['old']);assert.equal(state.posts,0);
 state.messages=[mail('old',0),mail('older',10),mail('new',1)];
 assert.equal(c.findAndPost_(start_id,since()).found,true);assert.equal(p.LAST_MAIL_ID,'new');
 state.messages=[mail('old'),mail('new')];assert.equal(c.findAndPost_(start_id,since()).already,true);assert.equal(state.posts,1);
}
{
 const {p,state,c}=fixture({PROCESSED_MAIL_IDS:JSON.stringify(Array.from({length:20},(_,i)=>'id'+i))});
 state.messages=[mail('next')];state.status=500;assert.equal(c.findAndPost_(start_id,since()).found,false);assert.equal(JSON.parse(p.PROCESSED_MAIL_IDS).length,20);assert.equal(p.LAST_MAIL_ID,undefined);
 state.status=200;assert.equal(c.findAndPost_(start_id,since()).found,true);const ids=JSON.parse(p.PROCESSED_MAIL_IDS);assert.equal(ids.length,20);assert.equal(ids[0],'id1');assert.equal(ids[19],'next');
}
{
 const {state,c}=fixture();state.messages=[mail('stale',31)];assert.equal(c.findAndPost_(start_id,since()).found,false);assert.equal(state.posts,0);
 state.messages=Array.from({length:101},(_,i)=>mail('m'+i,i===100?1:31));assert.equal(c.findAndPost_(start_id,since()).found,true);assert.ok(state.pages>=3);
}
{
 const {p,state,c}=fixture({PROCESSED_MAIL_IDS:'invalid'});assert.equal(c.doGet({parameter:{key:'fixture',start_id,since:since()}}).ok,false);assert.equal(state.posts,0);
 delete p.WORKER_BASE;assert.match(c.doGet({parameter:{key:'fixture',start_id,since:since()}}).error,/WORKER_BASE is not set/);
 assert.equal(c.doGet({parameter:{key:'wrong'}}).error,'unauthorized');
}
console.log('PASS migration, newest unprocessed, deduplication, 20-ID cap, retry, freshness, pagination, config/auth');

{
 const {state,c}=fixture();state.messages=[mail('old',12)];
 assert.equal(c.doGet({parameter:{key:'fixture'}}).ok,false);
 assert.equal(c.doGet({parameter:{key:'fixture',start_id}}).ok,false);
 assert.equal(c.doGet({parameter:{key:'fixture',since:since()}}).ok,false);
 assert.equal(c.findAndPost_(start_id,since()).found,false);assert.equal(state.posts,0);
 state.messages=[mail('fresh',0)];assert.equal(c.findAndPost_(start_id,since()).found,true);
 assert.equal(c.findAndPost_(start_id,since()).already,true);assert.equal(state.posts,1);
}
console.log('PASS required start_id/since, start-time gate, same-start idempotency');
{
 const {state,c}=fixture();state.messages=[mail('fresh',0)];
 const old=c.doGet({parameter:{key:'fixture',sid:start_id,since:since()}});
 assert.equal(old.ok,false);assert.equal(state.posts,0);
 const reply=c.doGet({parameter:{key:'fixture',start_id,since:since()}});
 assert.equal(reply.ok,true);assert.equal(reply.found,true);assert.equal(state.posts,1);
}
console.log('PASS doGet accepts start_id and rejects legacy sid');

{
 const {p,state,c}=fixture();state.messages=[mail('fresh',0)];
 const kick=value=>c.doGet({parameter:{key:'fixture',start_id,since:since(), ...(value===undefined?{}:{speech_interval:value})}});
 assert.equal(kick('per_500m').ok,true);
 assert.equal(state.requests[0].body.speech_interval,'per_500m');
 const pages=state.pages, processed=p.PROCESSED_MAIL_IDS;
 assert.equal(kick('interval:180').ok,true);
 assert.ok(state.requests[1].url.endsWith('op=session-settings'));
 assert.deepEqual(state.requests[1].body,{start_id,speech_interval:'interval:180'});
 assert.equal(state.pages,pages);assert.equal(p.PROCESSED_MAIL_IDS,processed);
 state.status=500;assert.equal(kick('per_km').ok,false);
 state.status=200;assert.equal(kick('per_km').ok,true);
 const posts=state.posts;assert.equal(kick(undefined).ok,true);assert.equal(state.posts,posts);
}
{
 const {state,c}=fixture();state.messages=[mail('fresh',0)];
 c.findAndPost_(start_id,since());assert.equal('speech_interval' in state.requests[0].body,false);
}
console.log('PASS cadence initial forwarding, same-start update without mail or session repost, retry and legacy omission');
