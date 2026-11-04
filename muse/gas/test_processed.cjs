const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
function fixture(properties = {}) {
  const p = {WORKER_BASE:'https://fixture.workers.dev', KICK_SECRET:'fixture', ...properties};
  const state = {messages:[], posts:0, status:200, pages:0};
  const c = {
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>p[k],setProperty:(k,v)=>p[k]=v})},
    Logger:{log:()=>{}},
    ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})},
    GmailApp:{search:(q,offset,size)=>{state.pages++;return state.messages.slice(offset,offset+size).map(m=>({getMessages:()=>[m]}));}},
    UrlFetchApp:{fetch:()=>{state.posts++;return {getResponseCode:()=>state.status,getContentText:()=> 'fixture failure'};}}
  };
  vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/livetrack_detector.gs','utf8'),c);
  return {p,state,c};
}
function mail(id,age=1) {return {getId:()=>id,getDate:()=>new Date(Date.now()-age*60000),getBody:()=> 'https://livetrack.garmin.com/session/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/token/abcdef'};}
{
 const {p,state,c}=fixture({LAST_MAIL_ID:'old'});state.messages=[mail('old')];
 assert.equal(c.findAndPost_().found,false);assert.deepEqual(JSON.parse(p.PROCESSED_MAIL_IDS),['old']);assert.equal(state.posts,0);
 state.messages=[mail('old',0),mail('older',10),mail('new',2)];
 assert.equal(c.findAndPost_().found,true);assert.equal(p.LAST_MAIL_ID,'new');
 state.messages=[mail('old'),mail('new')];assert.equal(c.findAndPost_().found,false);assert.equal(state.posts,1);
}
{
 const {p,state,c}=fixture({PROCESSED_MAIL_IDS:JSON.stringify(Array.from({length:20},(_,i)=>'id'+i))});
 state.messages=[mail('next')];state.status=500;assert.equal(c.findAndPost_().found,false);assert.equal(JSON.parse(p.PROCESSED_MAIL_IDS).length,20);assert.equal(p.LAST_MAIL_ID,undefined);
 state.status=200;assert.equal(c.findAndPost_().found,true);const ids=JSON.parse(p.PROCESSED_MAIL_IDS);assert.equal(ids.length,20);assert.equal(ids[0],'id1');assert.equal(ids[19],'next');
}
{
 const {state,c}=fixture();state.messages=[mail('stale',31)];assert.equal(c.findAndPost_().found,false);assert.equal(state.posts,0);
 state.messages=Array.from({length:101},(_,i)=>mail('m'+i,i===100?1:31));assert.equal(c.findAndPost_().found,true);assert.ok(state.pages>=3);
}
{
 const {p,state,c}=fixture({PROCESSED_MAIL_IDS:'invalid'});assert.equal(c.doGet({parameter:{key:'fixture'}}).ok,false);assert.equal(state.posts,0);
 delete p.WORKER_BASE;assert.match(c.doGet({parameter:{key:'fixture'}}).error,/WORKER_BASE is not set/);
 assert.equal(c.doGet({parameter:{key:'wrong'}}).error,'unauthorized');
}
console.log('PASS migration, newest unprocessed, deduplication, 20-ID cap, retry, freshness, pagination, config/auth');
