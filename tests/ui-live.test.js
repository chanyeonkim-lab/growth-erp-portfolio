// Production startup must not reuse a browser's old demo selection.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {parseHTML} from 'linkedom';
const {window,document}=parseHTML(await fs.readFile(new URL('../app/index.html',import.meta.url),'utf8'));
globalThis.window=window;globalThis.document=document;
globalThis.location={hash:'#dashboard'};
const stored=new Map([['growth-workspace','demo'],['growth-month','2026-10']]);
globalThis.localStorage={getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)};
const state={settings:{name:'그로스 일렉트릭',taxType:'일반과세자',taxReserves:{},bank:'KB국민은행'}};
for(const key of ['accounts','quotes','orders','invoices','expenses','receipts','allocations','documents','tasks','notes'])state[key]=[];
const requested=[];
globalThis.fetch=async path=>{
 requested.push(path);
 const payload=path.startsWith('/api/config')?{cloud:true,liveEnabled:true,workspace:'live',user:{username:'owner',displayName:'대표님'}}:{state:structuredClone(state),revision:0};
 return {ok:true,json:async()=>payload};
};
await import('./support/legacy-shell.js');
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
test('live startup overrides cached demo selection and shows zero figures',()=>{
 assert.ok(requested.includes('/api/state?workspace=live'));
 assert.ok(!requested.includes('/api/state?workspace=demo'));
 assert.deepEqual([...document.querySelectorAll('.metric-value')].map(el=>el.textContent),['0만원','0만원','0만원']);
 assert.equal(document.querySelector('.rank-table'),null);
 assert.doesNotMatch(document.querySelector('main').textContent,/체험|학습·테스트/);
});
test('live settings and reports have no demo switch or demo labels',()=>{
 for(const page of ['settings','report']){
  go(page);
  assert.equal(document.querySelector('[data-action="switch-workspace"]'),null);
  assert.doesNotMatch(document.querySelector('main').textContent,/체험|학습·테스트/);
 }
});
