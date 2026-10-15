// User-flow test through the actual Python command logic and an in-memory DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {parseHTML} from 'linkedom';
import {lineAmounts,remainingItems} from '../app/workflow.js';
const fixture=JSON.parse(fs.readFileSync(new URL('./demo-fixture.json',import.meta.url),'utf8'));
const {window,document}=parseHTML(fs.readFileSync(new URL('../app/index.html',import.meta.url),'utf8'));
Object.assign(globalThis,{window,document,location:{hash:'#quotes'}});
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
let data=structuredClone(fixture),revision=0;
const python=process.env.GROWTH_TEST_PYTHON||(fs.existsSync('.venv/bin/python')?'.venv/bin/python':'python3');
const commands=[];
globalThis.fetch=async(path,options)=>{
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({cloud:true,liveEnabled:false,fileStorage:'supabase'})};
 if(options?.body){
  const body=JSON.parse(options.body);
  if(path.startsWith('/api/workflow')){
   commands.push(body);
   assert.equal(body.revision,revision);
   const run=spawnSync(python,['-c',`import sys,json,workflow,server
r=json.load(sys.stdin);s=workflow.normalize(r['state']);result,content=workflow.apply(s,r['command'],'demo');server.validate_state(s);print(json.dumps(dict(result,state=s)))`],{input:JSON.stringify({state:data,command:body}),encoding:'utf8'});
   if(run.status!==0)throw Error(run.stderr);
   const result=JSON.parse(run.stdout);data=result.state;revision++;
   return {ok:true,json:async()=>({...result,revision})};
  }
  data=body.state;revision++;return {ok:true,json:async()=>({revision})};
 }
 return {ok:true,json:async()=>({revision,state:structuredClone(data)})};
};
const dialog=document.querySelector('#modal');dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
globalThis.FormData=class {
 constructor(form){this.values=new Map([...form.querySelectorAll('input,select,textarea')].filter(e=>e.name&&!e.disabled&&(e.type!=='checkbox'||e.checked)).map(e=>[e.name,e.tagName==='SELECT'?(e.querySelector('option[selected]')||e.querySelector('option'))?.getAttribute('value'):e.value]));}
 get(k){return this.values.get(k)??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}
};
await import('./support/legacy-shell.js');
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
const click=selector=>{const el=document.querySelector(selector);assert.ok(el,selector);el.dispatchEvent(new window.Event('click',{bubbles:true}));};
const set=(selector,value)=>{const el=dialog.querySelector(selector);assert.ok(el,selector);if(el.tagName==='SELECT'){for(const op of el.querySelectorAll('option'))op.removeAttribute('selected');el.querySelector(`option[value="${value}"]`).setAttribute('selected','');}else el.value=String(value);el.dispatchEvent(new window.Event('input',{bubbles:true}));};
const tick=()=>new Promise(r=>setTimeout(r,10));
async function submit(){const form=dialog.querySelector('form'),e=new window.Event('submit',{bubbles:true,cancelable:true});Object.defineProperty(e,'submitter',{value:form.querySelector('[type="submit"]')});form.dispatchEvent(e);await tick();assert.equal(dialog.querySelector('.form-error')?.textContent||'','');assert.equal(dialog.open,false);}

test('create, edit, copy, generate PDFs, convert and partially deliver without changing sales',async()=>{
 const original=structuredClone(data.invoices);
 click('[data-action="wf-quote-new"]');
 set('[name="accountId"]','ac-0');set('[name="title"]','루미나 캠퍼스 통합 테스트');set('[name="date"]','2026-10-03');
 set('[name="attention"]','시설 담당자');assert.equal(dialog.querySelector('[name="deliveryPlace"]'),null);
 set('[data-line="0"] [data-key="name"]','교육관 분전반');set('[data-line="0"] [data-key="quantity"]',3);set('[data-line="0"] [data-key="unitPrice"]',150000);
 click('[data-row="copy"]');set('[data-line="1"] [data-key="name"]','조립 공임');set('[data-line="1"] [data-key="quantity"]',1);set('[data-line="1"] [data-key="unitPrice"]',200000);
 assert.match(dialog.querySelector('[data-editor-totals]').textContent,/715,000/);
 await submit();go('quotes');const q1=data.quotes.at(-1);assert.equal(q1.amount,715000);assert.equal(q1.attention,'시설 담당자');assert.equal(q1.deliveryPlace,undefined);
	click(`[data-action="wf-quote-detail"][data-id="${q1.id}"]`);click('[data-action="wf-quote-pdf"]');await tick();assert.ok(data.quotes.at(-1).pdfDocumentId);assert.ok(dialog.querySelector('a[download]'));
	click('[data-action="wf-quote-update"]');assert.equal(dialog.querySelector('[data-key="quantity"]').getAttribute('step'),'1');set('[name="title"]','루미나 캠퍼스 작성 중 저장');set('[data-line="0"] [data-key="quantity"]',2);await submit();go('quotes');
	const saved=data.quotes.find(q=>q.id===q1.id);assert.equal(saved.version,1);assert.equal(saved.amount,550000);assert.equal(data.quotes.filter(q=>q.id===q1.id).length,1);
	click(`[data-action="wf-quote-detail"][data-id="${q1.id}"]`);assert.ok(dialog.querySelector('[data-action="wf-quote-update"]'));assert.ok(dialog.querySelector('[data-action="wf-quote-delete"]'));
	click('[data-action="wf-quote-edit"]');set('[data-line="0"] [data-key="quantity"]',4);set('[name="changeNote"]','수량 변경');await submit();go('quotes');
	const q2=data.quotes.at(-1);assert.equal(q2.version,2);assert.equal(q2.attention,'시설 담당자');assert.equal(q2.amount,880000);assert.equal(data.quotes.find(q=>q.id===q1.id).amount,550000);
 click(`[data-action="wf-quote-detail"][data-id="${q2.id}"]`);assert.match(dialog.textContent,/v1/);assert.match(dialog.textContent,/v2/);
 click('[data-action="wf-quote-copy"]');set('[name="title"]','재주문 복사');await submit();go('quotes');const copy=data.quotes.at(-1);assert.equal(copy.version,1);assert.notEqual(copy.familyId,q2.familyId);
 click(`[data-action="wf-quote-detail"][data-id="${q2.id}"]`);click('[data-action="wf-convert"]');set('[name="date"]','2026-10-03');await submit();go('orders');const order=data.orders.at(-1);assert.equal(order.quoteVersion,2);
 go('quotes');click(`[data-action="wf-quote-detail"][data-id="${q2.id}"]`);const orderLink=dialog.querySelector(`a[href="#order/${order.id}"]`);assert.ok(orderLink);assert.equal(orderLink.getAttribute('target'),'_blank');assert.match(orderLink.textContent,/새 탭/);dialog.close();go('orders');
 click(`[data-action="wf-order-detail"][data-id="${order.id}"]`);click('[data-action="wf-delivery-new"]');assert.equal(dialog.querySelector('[name="attention"]').value,'시설 담당자');set('[name="attention"]','납품 담당자');set('[name="date"]','2026-10-04');set('[name="qty-0"]',1);set('[name="qty-1"]',0);await submit();
 click(`[data-action="wf-order-detail"][data-id="${order.id}"]`);const d=data.deliveries.at(-1);assert.equal(d.amount,165000);assert.equal(d.attention,'납품 담당자');assert.equal(data.orders.at(-1).status,'일부 납품');
 click(`[data-action="wf-delivery-detail"][data-id="${d.id}"]`);click('[data-action="wf-delivery-pdf"]');await tick();assert.ok(dialog.querySelector('a[download]'));assert.equal(data.documents.filter(d=>d.generated).length,2);
 assert.deepEqual(data.invoices,original);assert.equal(remainingItems(data,data.orders.at(-1))[0].remaining,3);
 go('account/ac-0');assert.match(document.querySelector('main').textContent,/납품 · D-/);
 assert.ok(commands.some(c=>c.action==='document.generate'&&c.data.kind==='delivery'));
});

test('single version deletes directly while family deletion confirms and linked orders open in a new tab',async()=>{
 const base=structuredClone(data.quotes[0]),make=(id,familyId,version,title)=>({...base,id,familyId,version,title,number:'Q-'+familyId,date:'2026-10-05',sourceId:version>1?familyId:'',pdfDocumentId:''});
 data.quotes.push(make('single-v1','single',1,'버전 삭제'),make('single-v2','single',2,'버전 삭제'),make('bulk-a-v1','bulk-a',1,'일괄 삭제 A'),make('bulk-a-v2','bulk-a',2,'일괄 삭제 A'),make('bulk-b-v1','bulk-b',1,'일괄 삭제 B'),make('detail-c-v1','detail-c',1,'상세 삭제 C'));
 go('quotes');click('[data-action="wf-quote-detail"][data-id="single-v2"]');click('[data-action="wf-quote-delete"][data-id="single-v1"]');await tick();assert.equal(data.quotes.some(q=>q.id==='single-v1'),false);assert.ok(data.quotes.some(q=>q.id==='single-v2'));assert.equal(dialog.open,false);
 go('quotes');assert.ok(document.querySelector('[data-action="wf-quote-select"][data-id="bulk-a"]'));
 click('[data-action="wf-quote-select"][data-id="bulk-a"]');click('[data-action="wf-quote-select"][data-id="bulk-b"]');
 assert.match(document.querySelector('.quote-bulk-toolbar').textContent,/2건/);click('[data-action="wf-quote-family-bulk-delete"]');assert.match(dialog.textContent,/정말로 삭제하시겠습니까/);assert.match(dialog.textContent,/모든 버전 3개/);await submit();
 assert.equal(data.quotes.some(q=>['bulk-a','bulk-b'].includes(q.familyId)),false);assert.ok(data.quotes.some(q=>q.familyId==='detail-c'));
 click('[data-action="wf-quote-detail"][data-id="detail-c-v1"]');assert.ok(dialog.querySelector('[data-action="wf-quote-family-delete"]'));
 click('[data-action="wf-quote-family-delete"]');assert.match(dialog.textContent,/이 견적 건 삭제.*모든 버전/);await submit();assert.equal(data.quotes.some(q=>q.familyId==='detail-c'),false);
});

test('preview uses half-up won rounding and order availability is exact for fractional quantities',()=>{
 assert.deepEqual(lineAmounts({quantity:.1,unitPrice:5,vatRate:10}),{net:1,vat:0});
 const order={id:'o',items:[{id:'l',quantity:.3}]};
 assert.equal(remainingItems({deliveries:[{orderId:'o',status:'completed',items:[{orderLineId:'l',quantity:.1},{orderLineId:'l',quantity:.2}]}]},order)[0].remaining,0);
});

test('historical deduction displays signed amounts and blocks direct order conversion',async()=>{
 const account=data.accounts[0],id='historical-signed-quote';
 data.quotes.push({id,familyId:id,version:1,workflowVersion:2,accountId:account.id,number:'HIST-REVIEW',date:'2026-06-09',title:'과거 차감 견적',net:900000,vat:90000,items:[
  {name:'주문함',quantity:4,unitPrice:600000,net:2400000,vat:240000,unit:'EA'},
  {name:'기존품 차감',quantity:-5,unitPrice:300000,net:-1500000,vat:-150000,unit:'EA'}]});
 go('quotes');
 click(`[data-action="wf-quote-detail"][data-id="${id}"]`);
 assert.match(dialog.textContent,/실제 납품 품목·수량/);
 assert.match(dialog.textContent,/-1,500,000/);
 assert.equal(dialog.querySelector('[data-action="wf-convert"]'),null);
 assert.ok(dialog.querySelector('[data-action="wf-quote-pdf"]'));
 assert.ok(dialog.querySelector('[data-action="wf-quote-edit"]'));
});
