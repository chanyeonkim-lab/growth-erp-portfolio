// Hosted UI checks with in-memory fixtures, without browser automation.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {parseHTML} from 'linkedom';
const fixture=JSON.parse(await fs.readFile(new URL('./demo-fixture.json',import.meta.url),'utf8'));
const {window,document}=parseHTML(await fs.readFile(new URL('../app/index.html',import.meta.url),'utf8'));
globalThis.window=window;globalThis.document=document;
let redirected='',requested=[];
globalThis.location={hash:'#dashboard',replace:url=>redirected=url};
const storage=new Map([['growth-month','2026-10'],['growth-workspace','live']]);
globalThis.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
globalThis.fetch=async path=>{
 requested.push(path);
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({cloud:true,fileStorage:'supabase',liveEnabled:false,maxUploadBytes:3*1024*1024,user:{displayName:'관리자',username:'admin'}})};
 if(path.startsWith('/api/auth/logout'))return {ok:true,json:async()=>({ok:true})};
 return {ok:true,json:async()=>({revision:0,state:structuredClone(fixture)})};
};
const dialog=document.querySelector('#modal');dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
await import('./support/legacy-shell.js');
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
const click=selector=>document.querySelector(selector).dispatchEvent(new window.Event('click',{bubbles:true}));
test('learning deployment loads demo even if a previous browser selected live',()=>{
 assert.ok(requested.includes('/api/state?workspace=demo'));
 assert.equal(document.querySelector('.topbar [data-action="switch-workspace"]'),null);
 assert.match(document.querySelector('.user-name').textContent,/관리자/);
 assert.doesNotMatch(document.querySelector('main').textContent,/미수금/);
});
test('cloud settings show persistent storage, backup and account controls',()=>{
 go('settings');
 assert.ok(document.querySelector('[data-action="cloud-backup"]'));
 assert.ok(document.querySelector('[data-action="password"]'));
 assert.match(document.querySelector('main').textContent,/Supabase Storage · 비공개 저장소/);
 assert.doesNotMatch(document.querySelector('main').textContent,/PC 단독 사용|로컬 앱/);
});
test('settings offer both durable templates in PDF and Excel without creating a record',()=>{
 go('settings');
 const links=[...document.querySelectorAll('.template-downloads a')].map(a=>a.getAttribute('href'));
 assert.deepEqual(links,['/api/templates/quote.pdf?workspace=demo','/api/templates/quote.xlsx?workspace=demo','/api/templates/statement.pdf?workspace=demo','/api/templates/statement.xlsx?workspace=demo']);
 assert.match(document.querySelector('main').textContent,/거래 데이터 초기화 후에도/);
});
test('cloud upload shows the effective 3 MB limit',()=>{
 go('documents');click('[data-action="upload"]');
 assert.match(dialog.textContent,/최대 3MB/);
 assert.match(dialog.textContent,/비공개 저장소에 원본 보관/);
 dialog.close();
});
test('inactive production ledger cannot switch into live via the interface',()=>{
 const before=requested.length;
 go('settings');
 assert.equal(document.querySelector('[data-action="switch-workspace"]'),null);
 assert.match(document.querySelector('main').textContent,/운영 장부 비활성/);
 assert.equal(requested.length,before);
});
test('logout requests server revocation and returns to login',async()=>{
 click('[data-action="logout"]');
 await new Promise(r=>setTimeout(r,5));
 assert.equal(redirected,'/login');
 assert.ok(requested.some(p=>p.startsWith('/api/auth/logout')));
});
