// Exercise actual tax commands and rendered forms without sending external data.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {parseHTML} from 'linkedom';
import {adjustedGross,receivables,excessPayments,metrics,addAllocation} from '../app/domain.js';
import {costBasis,orderCost} from '../app/tax.js';
const python=process.env.GROWTH_TEST_PYTHON||(fs.existsSync('.venv/bin/python')?'.venv/bin/python':'python3');
const initial=spawnSync(python,['-c',`import json,server,workflow
s=server.empty_state();s['settings'].update(name='가상 공급사',businessNumber='8888800001',representative='테스트',email='sender@example.invalid');s['accounts']=[dict(id='a',name='가상 고객',kind='고객사',businessNumber='8888800003',representative='고객',email='recipient@example.invalid')];workflow.apply(s,dict(action='order.save',data=dict(accountId='a',title='세금계산서 UI 테스트',date='2026-10-03',items=[dict(name='분전반',quantity=2,unitPrice=250000)])),'demo');print(json.dumps(s))`],{encoding:'utf8'});
assert.equal(initial.status,0,initial.stderr);
let data=JSON.parse(initial.stdout),revision=0;
const {window,document}=parseHTML(fs.readFileSync(new URL('../app/index.html',import.meta.url),'utf8'));
Object.assign(globalThis,{window,document,location:{hash:'#tax-inbox'}});
globalThis.localStorage={getItem:k=>k==='growth-month'?'2026-10':null,setItem:()=>{}};
globalThis.fetch=async(path,options)=>{
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({cloud:true,liveEnabled:false,tax:{mode:'simulation',ready:true,label:'시뮬레이션 · 국세청 미연결'}})};
 if(options?.body){
  const body=JSON.parse(options.body);assert.equal(body.revision,revision);
  const external=path.startsWith('/api/tax');
  const run=spawnSync(python,['-c',`import sys,json,workflow,server,tax_workflow,tax_provider
r=json.load(sys.stdin);s=workflow.normalize(r['state'])
if r['external']:
 op=tax_workflow.prepare(s,r['command'],'demo');s,result=tax_workflow.complete(s,op,tax_provider.execute(op),server.validate_state)
else:
 result,content=workflow.apply(s,r['command'],'demo')
server.validate_state(s);print(json.dumps(dict(result,state=s)))`],{input:JSON.stringify({state:data,command:body,external}),encoding:'utf8',env:{...process.env,GROWTH_TAX_MODE:'simulation'}});
  if(run.status!==0)throw Error(run.stderr);
  const result=JSON.parse(run.stdout);data=result.state;revision++;return {ok:true,json:async()=>({...result,revision})};
 }
 return {ok:true,json:async()=>({revision,state:structuredClone(data)})};
};
const dialog=document.querySelector('#modal');dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
globalThis.FormData=class {constructor(form){this.values=new Map([...form.querySelectorAll('input,select,textarea')].filter(e=>e.name&&!e.disabled&&(e.type!=='checkbox'||e.checked)).map(e=>[e.name,e.tagName==='SELECT'?(e.querySelector('option[selected]')||e.querySelector('option'))?.getAttribute('value'):e.type==='checkbox'?(e.value||'on'):e.value]));}get(k){return this.values.get(k)??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}};
await import('./support/legacy-shell.js');
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
const click=selector=>{const el=document.querySelector(selector);assert.ok(el,selector);el.dispatchEvent(new window.Event('click',{bubbles:true}));};
const set=(name,value)=>{const el=dialog.querySelector(`[name="${name}"]`);assert.ok(el,name);if(el.tagName==='SELECT'){for(const op of el.querySelectorAll('option'))op.removeAttribute('selected');el.querySelector(`option[value="${value}"]`).setAttribute('selected','');}else if(el.type==='checkbox')el.checked=value;else el.value=String(value);el.dispatchEvent(new window.Event('change',{bubbles:true}));};
const tick=()=>new Promise(r=>setTimeout(r,10));
async function submit(){const form=dialog.querySelector('form'),e=new window.Event('submit',{bubbles:true,cancelable:true});Object.defineProperty(e,'submitter',{value:form.querySelector('[type="submit"]')});form.dispatchEvent(e);await tick();assert.equal(dialog.querySelector('.form-error')?.textContent||'','');assert.equal(dialog.open,false);}

test('tax collection, review and purchase cost allocation work through real command handlers',async()=>{
 assert.match(document.querySelector('main').textContent,/시뮬레이션 · 국세청 미연결/);
 click('[data-action="tax-collect"]');set('direction','BUY');set('start','2026-10-01');set('end','2026-10-31');await submit();
 click('[data-action="tax-poll"]');await tick();assert.equal(data.taxRecords.length,2);assert.equal(data.expenses.length,0);
 const record=data.taxRecords[0];click(`[data-action="tax-record"][data-id="${record.id}"]`);click('[data-action="tax-book"]');
 set('confirmParty',true);set('createAccount',true);set('vatDeductible',true);await submit();assert.equal(data.expenses.length,1);
 click(`[data-action="tax-record"][data-id="${record.id}"]`);click('[data-action="tax-cost"]');set('orderId',data.orders[0].id);set('amount',200000);await submit();
 assert.equal(data.costAllocations.length,1);assert.equal(orderCost(data,data.orders[0].id),200000);assert.equal(metrics(data,'2026-10').cost,300000);
 go('orders');click('[data-action="wf-order-detail"]');assert.match(dialog.textContent,/연결된 원가/);assert.match(dialog.textContent,/200,000/);dialog.close();
});

test('draft issue, status refresh and return correction update ledger and AR exactly once',async()=>{
 go('tax-issue');click('[data-action="tax-new"]');set('orderId',data.orders[0].id);set('date','2026-10-03');await submit();go('tax-issue');
 let draft=data.taxDrafts[0];click(`[data-action="tax-draft"][data-id="${draft.id}"]`);click('[data-action="tax-edit"]');await submit();
 click(`[data-action="tax-draft"][data-id="${draft.id}"]`);click('[data-action="tax-issue"]');set('confirmed',true);await submit();
 assert.equal(data.invoices.length,1);assert.equal(data.invoices[0].net,500000);
 click(`[data-action="tax-draft"][data-id="${draft.id}"]`);click('[data-action="tax-status"]');await tick();assert.match(dialog.textContent,/모의 전송 성공/);assert.equal(data.invoices.length,1);
 click('[data-action="tax-correct"]');set('modifyCode','3');set('date','2026-10-03');set('net',-100000);set('vat',-10000);await submit();
 const correction=data.taxDrafts.at(-1);click(`[data-action="tax-draft"][data-id="${correction.id}"]`);click('[data-action="tax-issue"]');set('confirmed',true);await submit();
 assert.equal(data.invoices.length,2);assert.equal(metrics(data,'2026-10').sales,440000);assert.equal(receivables(data,'2026-10-31')[0].balance,440000);
 go('receivables');assert.match(document.querySelector('main').textContent,/440,000/);assert.match(document.querySelector('main').textContent,/원본 550,000/);
 go('sales');const original=data.invoices[0];click(`[data-action="invoice-detail"][data-id="${original.id}"]`);assert.equal(dialog.querySelector('[data-action="void-invoice"]'),null);assert.ok(dialog.querySelector('[data-action="tax-record"]'));
});

test('correction credit uses its own date, preserves paid cash and displays overpayment separately',()=>{
 const s={accounts:[{id:'a',name:'Test'}],invoices:[{id:'i',accountId:'a',date:'2026-09-30',net:1000000,vat:100000,status:'issued'},{id:'c',accountId:'a',date:'2026-10-03',net:-200000,vat:-20000,status:'issued',creditApplications:[{invoiceId:'i',amount:220000}]}],receipts:[{id:'r',accountId:'a',date:'2026-09-30',amount:1100000}],allocations:[{id:'al',invoiceId:'i',receiptId:'r',date:'2026-09-30',amount:1100000}],expenses:[],settings:{taxReserves:{}}};
 assert.equal(adjustedGross(s,s.invoices[0],'2026-09-30'),1100000);assert.equal(adjustedGross(s,s.invoices[0],'2026-10-03'),880000);
 assert.equal(excessPayments(s,'2026-09-30').length,0);assert.equal(excessPayments(s,'2026-10-03')[0].excess,220000);assert.equal(metrics(s,'2026-10').sales,-220000);
 assert.equal(s.receipts[0].amount,1100000);s.allocations=[];addAllocation(s,{invoiceId:'i',receiptId:'r',date:'2026-10-03',amount:880000});assert.throws(()=>addAllocation(s,{invoiceId:'i',receiptId:'r',date:'2026-10-03',amount:1}));
 assert.equal(costBasis({detailLoaded:false,items:[],net:100,vat:10},{vatDeductible:false})[0].amount,110);
});
