// DOM-only tests with in-memory API fixtures. No browser or local HTTP navigation.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {parseHTML} from 'linkedom';
const fixture=JSON.parse(await fs.readFile(new URL('./demo-fixture.json',import.meta.url),'utf8'));
const {window,document}=parseHTML(await fs.readFile(new URL('../app/index.html',import.meta.url),'utf8'));
globalThis.window=window;globalThis.document=document;globalThis.location={hash:'#dashboard'};
const storage=new Map([['growth-month','2026-10']]);
globalThis.localStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)};
let data=structuredClone(fixture),revision=0;
globalThis.fetch=async(path,options)=>{
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({aiConnected:false})};
 if(options?.body){const body=JSON.parse(options.body);data=body.state;revision++;return {ok:true,json:async()=>({revision})};}
 return {ok:true,json:async()=>({revision,state:structuredClone(data)})};
};
const dialog=document.querySelector('#modal');dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
globalThis.FormData=class {
 constructor(form){this.values=new Map([...form.querySelectorAll('input,select,textarea')].filter(e=>e.name&&!e.disabled&&(e.type!=='checkbox'||e.checked)).map(e=>[e.name,e.tagName==='SELECT'?(e.querySelector('option[selected]')||e.querySelector('option'))?.getAttribute('value'):e.value]));}
 get(k){return this.values.get(k)??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}
};
await import('./support/legacy-shell.js');
const main=()=>document.querySelector('main');
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
const click=el=>el.dispatchEvent(new window.Event('click',{bubbles:true}));
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
test('dashboard contains Top 5, but no receivables panel or amounts',()=>{assert.match(main().textContent,/매출 고객사 Top 5/);assert.doesNotMatch(main().textContent,/미수금|미연결 입금|받을 돈/);assert.equal(document.querySelectorAll('.rank-table tbody tr').length,5);assert.equal(document.querySelectorAll('#trend-chart polyline').length,3);});
test('all application routes render without a network or a browser',()=>{for(const route of ['accounts','account/ac-0','quotes','orders','sales','expenses','receivables','documents','settings','report']){go(route);assert.ok(main().textContent.length>30,route);assert.doesNotMatch(main().textContent,/undefined|NaN/);}});
test('receivables and receipt history are confined to their own screen',()=>{go('receivables');assert.match(main().textContent,/미수금 합계/);click(document.querySelector('[data-action="ar-tab"][data-id="입금 내역"]'));assert.match(main().textContent,/입금액/);go('dashboard');assert.doesNotMatch(main().textContent,/미수금 합계/);});
test('account creation saves through the state API and closes the dialog',async()=>{go('accounts');click(document.querySelector('[data-action="account"]'));document.querySelector('[name="name"]').value='DOM 테스트 고객';const form=dialog.querySelector('form'),event=new window.Event('submit',{bubbles:true,cancelable:true});Object.defineProperty(event,'submitter',{value:form.querySelector('[type="submit"]')});form.dispatchEvent(event);await tick();assert.ok(data.accounts.some(a=>a.name==='DOM 테스트 고객'));assert.equal(dialog.open,false);});
test('quote detail shows version history and conversion preserves source items',()=>{go('quotes');click(document.querySelector('[data-action="wf-quote-detail"]'));assert.match(dialog.textContent,/버전 이력/);click(dialog.querySelector('[data-action="wf-convert"]'));assert.match(dialog.textContent,/품목·금액을 주문에 복사/);assert.ok(dialog.querySelector('[name="date"]'));assert.equal(dialog.querySelector('[name="amount"]'),null);dialog.close();});
test('expense, receipt and invoice forms can be opened',()=>{for(const [route,action] of [['expenses','expense'],['receivables','receipt'],['sales','invoice']]){go(route);click(document.querySelector(`[data-action="${action}"]`));assert.ok(dialog.querySelector('form'));assert.ok(dialog.querySelector('[name="date"]'));dialog.close();}});
