import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {createGuide} from '../app/guide.js';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const article={id:'accounts',title:'거래처 관리',route:'accounts',text:'거래처에 업무가 연결됩니다.',testSteps:['거래처를 검색합니다.','상세 보기를 엽니다.'],expected:'관련 기록을 확인합니다.'};
const usage={usedUsd:0,limitUsd:20,requests:0,requestLimit:2000,blocked:false};
const tick=()=>new Promise(r=>setTimeout(r,0));
function setup(api){
 const {document,window}=parseHTML('<html><body><div id="app"><button data-action="guide">AI 가이드</button><button id="workspace-action">업무 화면 버튼</button></div></body></html>');
 globalThis.document=document;Object.defineProperty(window,'innerWidth',{value:1440,writable:true,configurable:true});
 let current='account/private-record-id';
 const guide=createGuide({api,route:()=>current,esc});
 const event=(selector,type,values={})=>{const e=new window.Event(type,{bubbles:true,cancelable:true});Object.assign(e,values);document.querySelector(selector).dispatchEvent(e);return e;};
 const set=(selector,value)=>{document.querySelector(selector).value=value;event(selector,'input');};
 return {document,window,guide,event,set,go:value=>{current=value;window.dispatchEvent(new window.Event('hashchange'));}};
}
async function send(ui,question){ui.set('#guide-question',question);ui.event('[data-guide-form]','submit');await tick();}
const catalog=()=>({enabled:true,articles:[article],usage});

test('docked guide keeps workspace interactive, offers expansion and restores layout on close',async()=>{
 const ui=setup(async()=>catalog());await ui.guide.open();
 assert.equal(ui.document.querySelector('dialog'),null);
 assert.equal(ui.document.querySelector('#app').hasAttribute('inert'),false);
 assert.equal(ui.document.body.classList.contains('guide-docked'),true);
 let clicked=false;ui.document.querySelector('#workspace-action').addEventListener('click',()=>clicked=true);ui.event('#workspace-action','click');assert.equal(clicked,true);
 ui.event('[data-guide-expand]','click');assert.equal(ui.document.querySelector('#app').hasAttribute('inert'),true);
 ui.event('[data-guide-expand]','click');assert.equal(ui.document.querySelector('#app').hasAttribute('inert'),false);
 ui.event('[data-guide-close]','click');assert.equal(ui.document.querySelector('aside').hidden,true);assert.equal(ui.document.body.classList.contains('guide-docked'),false);
});

test('narrow layout uses full view and restores workspace when following a menu',async()=>{
 const ui=setup(async()=>catalog());ui.window.innerWidth=800;await ui.guide.open();
 assert.equal(ui.document.querySelector('aside').classList.contains('guide-full'),true);
 assert.equal(ui.document.querySelector('#app').hasAttribute('inert'),true);
 ui.event('[data-guide-route]','click');assert.equal(ui.document.querySelector('aside').hidden,true);assert.equal(ui.document.querySelector('#app').hasAttribute('inert'),false);
});

test('six examples support click-to-fill and clipboard copy without sending',async()=>{
 const requests=[];const ui=setup(async(path,body)=>{if(body)requests.push(body);return catalog();});await ui.guide.open();
 assert.equal(ui.document.querySelectorAll('[data-guide-copy]').length,6);
 const first=ui.document.querySelector('[data-guide-suggest]').dataset.guideSuggest;
 ui.event('[data-guide-suggest]','click');assert.equal(ui.document.querySelector('#guide-question').value,first);
 let copied='';Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async text=>{copied=text;}}}});
 ui.event('[data-guide-copy]','click');await tick();assert.equal(copied,first);assert.equal(requests.length,0);
});

test('missing key keeps guide search; technical usage and consent UI are absent',async()=>{
 const ui=setup(async()=>({...catalog(),enabled:false}));await ui.guide.open();
 assert.equal(ui.document.querySelector('[type="submit"]').disabled,true);
 assert.equal(ui.document.querySelector('[data-guide-consent]'),null);
 assert.doesNotMatch(ui.document.body.textContent,/\$20|2000회|UTC|토큰|동의합니다|예약 비용/);
 ui.event('[data-guide-tab="library"]','click');ui.set('[data-guide-search]','없는 단어');assert.match(ui.document.body.textContent,/일치하는 가이드가 없/);
 ui.set('[data-guide-search]','거래처');assert.ok(ui.document.querySelector('.guide-article'));
 ui.event('[data-guide-route]','click');assert.equal(ui.document.querySelector('aside').hidden,false);
});

test('multi-turn topics, menu changes and reopening preserve dialogue; unsafe HTML stays inert',async()=>{
 const requests=[];const ui=setup(async(path,body)=>{
  if(!body)return catalog();requests.push(body);return {answer:'<img src=x onerror=alert(1)> **개념** [accounts]',sources:[article],usage};
 });await ui.guide.open();
 for(let i=0;i<8;i++)await send(ui,'질문 '+i);
 assert.equal(requests[7].history.length,12);assert.deepEqual(requests[7].contextSources,['accounts']);assert.equal(requests[7].allowExternal,undefined);
 assert.equal(requests[0].page,'accounts');assert.equal(requests[0].state,undefined);
 assert.equal(ui.document.querySelector('.guide-message img'),null);assert.equal(ui.document.querySelector('.guide-message.assistant p strong').textContent,'개념');
 ui.go('products');assert.match(ui.document.querySelector('.guide-suggestions').textContent,/기본 가격/);
 ui.event('[data-guide-close]','click');await ui.guide.open();assert.equal(ui.document.querySelectorAll('.guide-message').length,16);
 ui.event('[data-guide-reset]','click');await send(ui,'새 질문');assert.deepEqual(requests.at(-1).history,[]);assert.deepEqual(requests.at(-1).contextSources,[]);
});

test('double submit is blocked; failed followup retains draft and valid previous history',async()=>{
 let rejectRequest,calls=0;const ui=setup(async(path,body)=>{if(!body)return catalog();calls++;return new Promise((_,reject)=>{rejectRequest=reject;});});
 await ui.guide.open();await send(ui,'원래 질문');ui.event('[data-guide-form]','submit');assert.equal(calls,1);
 assert.equal(ui.document.querySelector('[data-guide-reset]').disabled,true);
 rejectRequest(Error('연결 오류'));await tick();assert.equal(ui.document.querySelector('#guide-question').value,'원래 질문');assert.equal(ui.document.querySelectorAll('.guide-message').length,0);
});

test('Enter submits, Shift+Enter and Korean composition do not',async()=>{
 let calls=0;const ui=setup(async(path,body)=>{if(!body)return catalog();calls++;return {answer:'설명 [accounts]',sources:[article],usage};});await ui.guide.open();
 ui.set('#guide-question','한글 질문');ui.event('#guide-question','keydown',{key:'Enter',isComposing:true});assert.equal(calls,0);
 ui.event('#guide-question','keydown',{key:'Enter',shiftKey:true});assert.equal(calls,0);
 ui.event('#guide-question','keydown',{key:'Enter'});await tick();assert.equal(calls,1);
});

test('test guide links have a renderable destination with instructions and expected result',async()=>{
 const ui=setup(async()=>catalog());await ui.guide.open();
 assert.equal(ui.document.querySelector('.guide-test-link').getAttribute('href'),'#guide/accounts');
 const html=ui.guide.page('accounts');assert.match(html,/따라 해보기/);assert.match(html,/거래처를 검색합니다/);assert.match(html,/관련 기록을 확인합니다/);assert.match(html,/href="#accounts"/);
});

test('budget exhaustion hides counters, allows guide library; failed load can retry',async()=>{
 let failed=true;const ui=setup(async()=>{if(failed)throw Error('연결 오류');return {...catalog(),usage:{...usage,blocked:true,warning:true}};});await ui.guide.open();
 assert.ok(ui.document.querySelector('[data-guide-reload]'));failed=false;ui.event('[data-guide-reload]','click');await tick();
 assert.equal(ui.document.querySelector('[type="submit"]').disabled,true);assert.ok(ui.document.querySelector('.guide-article'));assert.doesNotMatch(ui.document.body.textContent,/80%|\$|2000/);
});
