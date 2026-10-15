// Full statement -> sale -> manual tax confirmation and paired supplier document flow.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {parseHTML} from 'linkedom';
import {metrics,ranking,receivables} from '../app/domain.js';
import {orderCost} from '../app/tax.js';
import {hometaxTasks,hometaxDone} from '../app/statements.js';
const python=process.env.GROWTH_TEST_PYTHON||(fs.existsSync('.venv/bin/python')?'.venv/bin/python':'python3');
const prepared=spawnSync(python,['-c',`import json,server,workflow
s=server.empty_state();s['accounts']=[dict(id='a',name='가상 루미나 캠퍼스',kind='고객사'),dict(id='v',name='가상 볼트 서플라이',kind='매입처')]
r,_=workflow.apply(s,dict(action='order.save',data=dict(accountId='a',title='분전반 테스트',date='2026-10-01',items=[dict(name='분전반',quantity=2,unitPrice=500000)])),'demo')
o=s['orders'][0]
for dt in ('2026-10-01','2026-10-02'):
 workflow.apply(s,dict(action='delivery.create',data=dict(orderId=o['id'],date=dt,items=[dict(orderLineId=o['items'][0]['id'],quantity=1)])),'demo')
s['receipts']=[dict(id='r',accountId='a',orderId=o['id'],date='2026-09-30',amount=200000)]
for key,kind in [('doc','거래명세서'),('tax','세금계산서')]:
 s['documents'].append(dict(id=key,name=key+'.pdf',kind=kind,date='2026-10-01',status='확인 필요',extraction=dict(accountName='가상 볼트 서플라이',date='2026-10-01',net=300000,vat=30000,title='스텐함')))
print(json.dumps(s))`],{encoding:'utf8'});
assert.equal(prepared.status,0,prepared.stderr);
let data=JSON.parse(prepared.stdout),revision=0,rejectTask=false;
const {window,document}=parseHTML(fs.readFileSync(new URL('../app/index.html',import.meta.url),'utf8'));
Object.assign(globalThis,{window,document,location:{hash:'#statements'}});
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
globalThis.FormData=class {
 constructor(form){this.values=new Map([...form.querySelectorAll('input,select,textarea')].filter(e=>e.name&&!e.disabled&&(e.type!=='checkbox'||e.checked)).map(e=>[e.name,e.tagName==='SELECT'?(e.querySelector('option[selected]')||e.querySelector('option'))?.getAttribute('value'):e.value]));}
 get(k){return this.values.get(k)??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}
};
globalThis.fetch=async(path,options)=>{
 assert.ok(!path.startsWith('/api/tax'),'No tax-provider calls in the statement flow');
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({cloud:true,liveEnabled:false,fileStorage:'supabase',tax:{mode:'disabled',ready:false}})};
 if(options?.body){
  const command=JSON.parse(options.body);
  if(rejectTask&&command.action==='statement.hometax-task')return {ok:false,json:async()=>({error:'테스트 저장 실패'})};
  const run=spawnSync(python,['-c',`import sys,json,server,workflow
r=json.load(sys.stdin);s=workflow.normalize(r['state'])
try:
 if r['generic']:
  workflow.guard(s,r['command']['state']);s=r['command']['state'];result={}
 else:result,content=workflow.apply(s,r['command'],'demo')
 server.validate_state(s);print(json.dumps(dict(result,state=s)))
except ValueError as e:print(json.dumps(dict(error=str(e))))`],{input:JSON.stringify({state:data,command,generic:path.startsWith('/api/state')}),encoding:'utf8'});
  assert.equal(run.status,0,run.stderr);const result=JSON.parse(run.stdout);
  if(result.error)return {ok:false,json:async()=>result};
  data=result.state;revision++;return {ok:true,json:async()=>({...result,revision})};
 }
 return {ok:true,json:async()=>({revision,state:structuredClone(data)})};
};
const dialog=document.querySelector('#modal');dialog.showModal=function(){this.open=true;};dialog.close=function(){this.open=false;};
await import('./support/legacy-shell.js');
const tick=()=>new Promise(r=>setTimeout(r,10));
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
const click=selector=>{const el=document.querySelector(selector);assert.ok(el,selector);el.dispatchEvent(new window.Event('click',{bubbles:true}));};
const set=(name,value)=>{const el=dialog.querySelector(`[name="${name}"]`);assert.ok(el,name);if(el.type==='checkbox'){el.checked=value;el.value='on';}else if(el.tagName==='SELECT'){for(const op of el.querySelectorAll('option'))op.removeAttribute('selected');el.querySelector(`option[value="${value}"]`).setAttribute('selected','');}else el.value=String(value);el.dispatchEvent(new window.Event('input',{bubbles:true}));el.dispatchEvent(new window.Event('change',{bubbles:true}));};
async function submit(expectedError=''){const form=dialog.querySelector('form'),e=new window.Event('submit',{bubbles:true,cancelable:true});Object.defineProperty(e,'submitter',{value:form.querySelector('[type="submit"]')});form.dispatchEvent(e);await tick();const error=dialog.querySelector('.form-error')?.textContent||'';if(expectedError)assert.ok(error.includes(expectedError),error);else {assert.equal(error,'');assert.equal(dialog.open,false);}}
async function checkTask(id,done,scope='#app'){const el=document.querySelector(`${scope} [data-hometax-task="${id}"]`);assert.ok(el);el.checked=done;el.dispatchEvent(new window.Event('change',{bubbles:true}));await tick();}

test('confirmed statements become Hometax tasks across dashboard, customer and statement views without changing money',async()=>{
 assert.ok(document.querySelector('nav a[href="#statements"]'));
 assert.equal(document.querySelector('nav a[href="#tax-inbox"]'),null);
 for(const [idx,d] of data.deliveries.entries()){
  click(`[data-action="wf-delivery-detail"][data-id="${d.id}"]`);
  click('[data-action="wf-delivery-pdf"]');await tick();assert.equal(data.invoices.length,idx);
  click('[data-action="stmt-confirm"]');set('confirmed',true);set('useCredit',true);await submit();go('statements');
 }
 assert.equal(data.invoices.length,2);assert.equal(metrics(data,'2026-10').sales,1100000);
 assert.equal(ranking(data,'2026-10')[0].amount,1100000);
 assert.equal(receivables(data,'2026-10-03').reduce((n,i)=>n+i.balance,0),900000);
 assert.equal(data.receipts[0].amount,200000);
 const financial=()=>({metrics:metrics(data,'2026-10'),ranking:ranking(data,'2026-10'),receivables:receivables(data,'2026-10-03').map(i=>({id:i.id,balance:i.balance})),receipts:data.receipts,allocations:data.allocations});
 const before=structuredClone(financial()),first=data.invoices[0].id,second=data.invoices[1].id;
 dialog.close();go('dashboard');assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,2);
 assert.match(document.querySelector('#app .task-scroll').textContent,/가상 루미나 캠퍼스.*홈택스/);
 await checkTask(first,true);assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,1);
 assert.ok(hometaxDone(data.invoices[0]));
 go('account/a');assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,2);
 assert.ok(document.querySelector(`#app [data-hometax-task="${first}"]`).hasAttribute('checked'));
 await checkTask(first,false);assert.ok(!hometaxDone(data.invoices[0]));
 go('statements');click('[data-action="stmt-pending"]');assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,2);
 await checkTask(second,true);assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,1);
 click(`[data-action="stmt-sale"][data-id="${first}"]`);
 assert.equal(dialog.querySelector('[name="approval"]'),null);assert.equal(dialog.querySelector('[name="date"]'),null);
 await checkTask(first,true,'#modal');assert.match(dialog.textContent,/완료 체크일/);
 dialog.close();assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,0);
 go('dashboard');assert.equal(document.querySelectorAll('#app [data-hometax-task]').length,0);
 assert.deepEqual(financial(),before);
 go('account/a');rejectTask=true;await checkTask(first,false);rejectTask=false;
 assert.ok(document.querySelector(`#app [data-hometax-task="${first}"]`).hasAttribute('checked'));assert.ok(hometaxDone(data.invoices[0]));
 assert.match(document.querySelector('#toast').textContent,/저장 실패/);
 assert.equal(data.tasks.length,0,'Derived tasks do not create duplicate generic task records');
 go('sales');click(`[data-action="invoice-detail"][data-id="${data.invoices[0].id}"]`);assert.equal(dialog.querySelector('[data-action="void-invoice"]'),null);
 go('settings');assert.match(document.querySelector('#app').textContent,/홈택스는 수동 발급/);assert.equal(document.querySelector('[data-action="tax-setup"]'),null);
});

test('old completion records are compatible and cancelled or unconfirmed sales do not produce tasks',()=>{
 const sample=structuredClone(data);const old=sample.invoices[0];delete old.hometaxTask;old.manualTax={confirmedAt:'2026-10-03T12:00:00+09:00'};
 assert.ok(hometaxTasks(sample).find(t=>t.invoiceId===old.id).done);
 old.hometaxTask={done:false};assert.ok(!hometaxTasks(sample).find(t=>t.invoiceId===old.id).done);
 old.status='void';delete sample.invoices[1].sourceDeliveryId;
 assert.equal(hometaxTasks(sample).length,0);
});

test('supplier statement and invoice attach to one cost; allocation preserves total expense',async()=>{
 go('statements');click('[data-action="stmt-in"]');
 click('[data-action="stmt-purchase"][data-id="doc"]');set('accountId','v');set('confirmed',true);set('vatDeductible',true);await submit();
 assert.equal(data.expenses.length,1);const expense=data.expenses[0];assert.equal(metrics(data,'2026-10').cost,300000);assert.equal(metrics(data,'2026-10').paid,0);
 click('[data-action="stmt-purchase"][data-id="tax"]');set('accountId','v');set('confirmed',true);await submit('같은 매입처');
 set('existingExpenseId',expense.id);await submit();assert.equal(data.expenses.length,1);assert.equal(data.documents.filter(d=>d.expenseId===expense.id).length,2);
 click(`[data-action="stmt-expense"][data-id="${expense.id}"]`);click('[data-action="stmt-cost"]');set('orderId',data.orders[0].id);set('amount',200000);await submit();
 assert.equal(orderCost(data,data.orders[0].id),200000);assert.equal(metrics(data,'2026-10').cost,300000);
});
