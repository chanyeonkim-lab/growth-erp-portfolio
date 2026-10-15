// User-flow test through the actual Python command logic and an in-memory DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {parseHTML} from 'linkedom';
import {tasks,remaining,stockBalance,metrics,globalResults,confirmedAmount,margin} from '../app/erp-domain.js';
import {today,money} from '../app/domain.js';
const fixture=JSON.parse(fs.readFileSync(new URL('./demo-fixture.json',import.meta.url),'utf8'));
const {window,document}=parseHTML(fs.readFileSync(new URL('../app/index.html',import.meta.url),'utf8'));
Object.assign(globalThis,{window,document,location:{hash:'#dashboard'}});
globalThis.localStorage={getItem:()=>null,setItem:()=>{}};
let data=structuredClone(fixture),revision=0;
const python=process.env.GROWTH_TEST_PYTHON||(fs.existsSync('.venv/bin/python')?'.venv/bin/python':'python3');
const commands=[];
globalThis.fetch=async(path,options)=>{
 if(path.startsWith('/api/guide'))return {ok:true,json:async()=>({enabled:false,articles:JSON.parse(fs.readFileSync('guide_knowledge.json','utf8')).articles,usage:{blocked:false}})};
 if(path.startsWith('/api/config'))return {ok:true,json:async()=>({cloud:true,erpVersion:2,liveEnabled:false,fileStorage:'supabase'})};
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


test('v2 upgrade requires explicit confirmation and exposes task-oriented pages',async()=>{
 assert.notEqual(data.ledgerVersion,2);assert.equal(commands.length,0);go('settings');click('[data-action="upgrade-ledger"]');dialog.querySelector('[name="confirmed"]').checked=true;await submit();go('dashboard');
 assert.equal(data.ledgerVersion,2);assert.equal(data.vouchers.length,72);
 assert.match(document.querySelector('main').textContent,/오늘의 업무/);
 assert.match(document.querySelector('main').textContent,/미수금/);
 assert.doesNotMatch(document.querySelector('main').textContent,/Top 5/);
 for(const r of ['tasks','accounts','products','transactions','payments','analysis','documents','settings','report']){go(r);assert.ok(document.querySelector('main'));}
 go('analysis');assert.match(document.querySelector('main').textContent,/Top 5/);
});

test('account row preview prioritizes tasks, details support two phones and inactive filtering',async()=>{
 go('accounts');assert.ok(document.querySelector('.data-table'));assert.equal(document.querySelector('.account-card'),null);
 click('[data-action="erp-account-preview"][data-id="ac-1"]');assert.match(document.querySelector('.account-preview').textContent,/상세 보기/);
 assert.ok(document.querySelector('.account-preview .todo-row'));
 go('account/ac-1');click('[data-action="erp-account-edit"]');set('[name="phone"]','010-1111-2222');set('[name="phone2"]','02-123-4567');set('[name="active"]','false');await submit();
 assert.equal(data.accounts.find(a=>a.id==='ac-1').phone2,'02-123-4567');
 go('accounts');assert.equal(document.querySelector('tr[data-id="ac-1"]'),null);
 click('[data-action="erp-inactive-toggle"]');assert.ok(document.querySelector('tr[data-id="ac-1"]'));
});

test('manual dated task appears on dashboard, all tasks, account preview and account detail',async()=>{
 go('account/ac-0');click('[data-action="erp-task-new"]');set('[name="title"]','도면 수정해서 보내기');set('[name="date"]','2026-10-05');await submit();
 const t=data.tasks.at(-1);assert.equal(t.accountId,'ac-0');assert.equal(t.dueDate,'2026-10-05');
 for(const r of ['dashboard','tasks','account/ac-0']){go(r);assert.match(document.querySelector('main').textContent,/도면 수정해서 보내기/);}
 go('accounts');click('[data-action="erp-account-preview"][data-id="ac-0"]');assert.match(document.querySelector('.account-preview').textContent,/도면 수정해서 보내기/);
 const task=tasks(data,'2026-10-06').find(x=>x.sourceId===t.id);assert.equal(task.tone,'red');assert.equal(task.href,'#task/'+t.id);
});

test('task editor deletes one task and all tasks supports bulk completion and deletion',async()=>{
 const accountId='ac-0',make=async title=>{go('account/'+accountId);click('[data-action="erp-task-new"]');set('[name="title"]',title);set('[name="date"]','2026-10-12');await submit();return data.tasks.at(-1).id;};
 const first=await make('일괄 처리 첫 번째'),second=await make('일괄 처리 두 번째'),single=await make('개별 삭제 대상');
 go('task/'+single);click('[data-action="erp-task-edit"]');assert.ok(dialog.querySelector('[data-action="erp-task-delete"]'));click('[data-action="erp-task-delete"]');assert.match(dialog.textContent,/할 일 삭제/);await submit();assert.equal(data.tasks.some(t=>t.id===single),false);
 go('tasks');const automatic=document.querySelector('.selectable-task-row input[disabled]');assert.ok(automatic,'automatic task has a disabled checkbox');
 for(const id of [first,second]){const box=document.querySelector(`[data-task-checkbox="${id}"]`);assert.ok(box);box.checked=true;box.dispatchEvent(new window.Event('change',{bubbles:true}));}
 assert.match(document.querySelector('.task-bulk-toolbar').textContent,/2개/);click('[data-action="erp-task-bulk-complete"]');await submit();
 for(const id of [first,second])assert.equal(data.tasks.find(t=>t.id===id).status,'completed');
 go('tasks/completed');click('[data-action="erp-task-select-all"]');assert.match(document.querySelector('.task-bulk-toolbar').textContent,/선택 해제/);click('[data-action="erp-task-bulk-delete"]');await submit();
 for(const id of [first,second])assert.equal(data.tasks.some(t=>t.id===id),false);
 assert.equal(data.tasks.some(t=>t.id===single),false);
});

test('product list supports selecting and deleting multiple records',async()=>{
 const make=async(code,name)=>{go('products');click('[data-action="erp-product-new"]');set('[name="code"]',code);set('[name="name"]',name);await submit();return data.products.at(-1).id;};
 const first=await make('BULK-UI-1','일괄 삭제 품목 1'),second=await make('BULK-UI-2','일괄 삭제 품목 2');
 go('products');
 for(const id of [first,second]){const box=document.querySelector(`[data-product-checkbox="${id}"]`);assert.ok(box);box.checked=true;box.dispatchEvent(new window.Event('change',{bubbles:true}));}
 assert.match(document.querySelector('.quote-bulk-toolbar').textContent,/2개/);
 click('[data-action="erp-product-bulk-delete"]');assert.match(dialog.textContent,/2개 품목/);await submit();
 for(const id of [first,second])assert.equal(data.products.some(p=>p.id===id),false);
 assert.equal(commands.at(-1).action,'erp.product.bulk-delete');
});

let pid,quote,order,purchase,sale;
test('catalog purchase, receipt and payable use item level prices and inventory',async()=>{
 go('products');click('[data-action="erp-product-new"]');
 set('[name="code"]','TC80150');set('[name="name"]','철제함');set('[name="spec"]','800*1500*350*1.6T STEEL');set('[name="purchasePrice"]',100000);set('[name="salePrice"]',150000);set('[name="supplierId"]','ac-5');await submit();pid=data.products.at(-1).id;
 go('product/'+pid);click('[data-action="erp-purchase-product"]');set('[name="title"]','철제함 10개 매입');set('[data-line="0"] [data-key="quantity"]',10);await submit();purchase=data.vouchers.at(-1);
 go('transaction/'+purchase.id);assert.equal(document.querySelector('[data-action="erp-approval"]'),null);click('[data-action="erp-confirm"]');await submit();
 assert.equal(data.vouchers.find(v=>v.id===purchase.id).status,'confirmed');assert.ok(document.querySelector('[data-action="erp-approval"]'));assert.equal(stockBalance(data,pid).quantity,0);
 click('[data-action="erp-fulfill"]');await submit();assert.equal(stockBalance(data,pid).quantity,10);
 assert.ok(tasks(data).some(t=>t.sourceId===purchase.id&&t.category==='지급할 돈'));
});

test('quote uses price book with editable proposal, BOX order can confirm before delivery and export xlsx',async()=>{
 go('quotes');click('[data-action="wf-quote-new"]');set('[name="accountId"]','ac-0');set('[name="title"]','A건 철제함 판매');
 assert.equal(dialog.querySelector('[data-catalog-search]'),null);assert.equal(dialog.querySelector('[data-key="section"]'),null);assert.ok(dialog.querySelector('[data-line="0"] [data-product-search]'));
 click('[data-line="0"] [data-product-search]');assert.ok(dialog.querySelector('[data-line="0"] [data-catalog-manual]'));click('[data-line="0"] [data-catalog-manual]');set('[data-line="0"] [data-key="name"]','직접 입력 품목');
 click('[data-line="0"] [data-product-search]');set('[data-line="0"] [data-line-catalog-search]','TC80150');click('[data-line="0"] [data-catalog-id="'+pid+'"]');
 assert.equal(dialog.querySelector('[data-key="unitPrice"]').value,'150000');set('[data-line="0"] [data-key="quantity"]',2);await submit();quote=data.quotes.at(-1);
 assert.equal(quote.items[0].section,'');
 assert.equal(quote.net-quote.items.reduce((n,r)=>n+r.estimatedCost,0),100000);
 go('quotes');click('[data-action="wf-quote-detail"][data-id="'+quote.id+'"]');assert.match(dialog.textContent,/예상 마진 100,000원/);assert.match(dialog.textContent,/예상 원가 \/ 마진/);assert.doesNotMatch(dialog.textContent,/구분 \/ 품목/);
 click('[data-action="wf-convert"]');set('[name="orderType"]','BOX');await submit();order=data.orders.at(-1);assert.match(order.number,/^BOX-\d{8}-001$/);
 go('order/'+order.id);click('[data-action="erp-order-voucher"]');await submit();sale=data.vouchers.at(-1);
 go('transaction/'+sale.id);click('[data-action="erp-confirm"]');await submit();assert.equal(data.orders.at(-1).status,'주문 접수');assert.equal(stockBalance(data,pid).quantity,10);
 click('[data-action="erp-approval"]');set('[name="approvalNumber"]','20261004-12345678-12345678');await submit();assert.equal(data.vouchers.at(-1).approvalNumber,'20261004-12345678-12345678');
 click('[data-action="erp-voucher-xlsx"]');await tick();assert.ok(dialog.querySelector('a[download]'));assert.match(data.documents.at(-1).name,/\.xlsx$/);dialog.close();
});

test('partial collections keep receivable task until paid and delivery reduces stock once',async()=>{
 go('transaction/'+sale.id);click('[data-action="erp-settle"]');set('[name="amount"]',100000);await submit();assert.equal(remaining(data,data.vouchers.at(-1)),230000);
 assert.ok(tasks(data).some(t=>t.sourceId===sale.id&&t.category==='미수금'));
 click('[data-action="erp-fulfill"]');await submit();assert.equal(stockBalance(data,pid).quantity,8);assert.equal(data.orders.at(-1).status,'납품 완료');
 click('[data-action="erp-settle"]');await submit();assert.equal(remaining(data,data.vouchers.at(-1)),0);assert.equal(tasks(data).some(t=>t.sourceId===sale.id&&t.category==='미수금'),false);
 go('transaction/'+purchase.id);click('[data-action="erp-settle"]');await submit();assert.equal(tasks(data).some(t=>t.sourceId===purchase.id&&t.category==='지급할 돈'),false);
 const journals=data.journalEntries.filter(j=>j.sourceId===data.fulfillments.at(-1).id);assert.ok(journals.flatMap(j=>j.rows).some(r=>r.account==='cogs'&&r.debit===200000));
});

test('an earlier payment linked to a future-dated sale is immediately shown as fully collected',()=>{
 const accountId='ux-future-paid-account',voucherId='ux-future-paid-sale',number='S-20991013-001',amount=2353000;
 const voucher={id:voucherId,accountId,kind:'sale',status:'confirmed',number,title:'선수금 완료 거래',date:'2099-10-13',confirmedDate:'2099-10-13',dueDate:'2099-10-13',amount,net:amount,vat:0,items:[],credits:[]};
 const payment={id:'ux-future-paid-payment',accountId,direction:'in',status:'confirmed',date:'2099-10-09',amount,memo:'전액 계좌이체',allocations:[{voucherId,amount,date:'2099-10-13'}]};
 data.accounts.push({id:accountId,name:'선수금 검증 고객',active:true});data.vouchers.push(voucher);data.payments.push(payment);
 try{
  assert.equal(remaining(data,voucher,today()),amount);assert.equal(remaining(data,voucher),0);
  go('transaction/'+voucherId);
  const cards=document.querySelectorAll('.transaction-summary > div');
  assert.equal(cards[1].querySelector('strong').textContent,money(amount));assert.match(cards[1].textContent,/결제 완료/);
  assert.equal(cards[2].querySelector('strong').textContent,money(0));assert.equal(document.querySelector('[data-action="erp-settle"]'),null);
  assert.match(document.querySelector('[data-operational-history]').textContent,/2099-10-09/);assert.match(document.querySelector('[data-operational-history]').textContent,/2,353,000원/);
  go('transactions');const row=[...document.querySelectorAll('tr')].find(row=>row.textContent.includes(number));assert.ok(row);assert.match(row.textContent,/결제 완료/);assert.doesNotMatch(row.textContent,/미결제/);
  go('payments');assert.doesNotMatch(document.querySelector('main').textContent,new RegExp(number));
  go('account/'+accountId);const accountCards=document.querySelectorAll('.erp-kpis > div');assert.equal(accountCards[1].querySelector('strong').textContent,money(0));
 }finally{
  data.payments=data.payments.filter(p=>p.id!==payment.id);data.vouchers=data.vouchers.filter(v=>v.id!==voucher.id);data.accounts=data.accounts.filter(a=>a.id!==accountId);go('dashboard');
 }
});

test('a draft with completed delivery exposes full correction and editable operational history',()=>{
 const id='ux-draft-with-delivery',lineId='ux-draft-line',fulfillmentId='ux-draft-fulfillment';
 const voucher={id,accountId:'ac-0',kind:'sale',status:'draft',number:'S-20991011-002',title:'납품 정정 검증',date:'2099-10-11',dueDate:'2099-10-11',confirmationDueDate:'2099-10-11',amount:100000,net:100000,vat:0,items:[{id:lineId,name:'검증 품목',spec:'',unit:'개',quantity:1,unitPrice:100000,net:100000,vat:0,vatRate:0}],credits:[]};
 const fulfillment={id:fulfillmentId,voucherId:id,date:'2099-10-11',status:'completed',items:[{voucherLineId:lineId,quantity:1,cost:0,tracked:false}],net:100000,vat:0,cost:0};
 data.vouchers.push(voucher);data.fulfillments.push(fulfillment);
 try{
  go('transaction/'+id);assert.equal(document.querySelector('[data-action="erp-voucher-edit"]').textContent,'전표 전체 수정');
  assert.equal(document.querySelectorAll('[data-action="erp-fulfillment-edit"]').length,1);
  assert.equal(document.querySelectorAll('[data-action="erp-fulfillment-delete"]').length,1);
  click('[data-action="erp-voucher-edit"]');assert.ok(dialog.querySelector('[data-item-editor]'));dialog.close();
 }finally{
  data.fulfillments=data.fulfillments.filter(f=>f.id!==fulfillmentId);data.vouchers=data.vouchers.filter(v=>v.id!==id);go('dashboard');
 }
});

test('global search finds parties, item codes, voucher approval numbers and order numbers',()=>{
 assert.ok(globalResults(data,'TC80150').some(r=>r.type==='품목'));
 assert.ok(globalResults(data,'루미나 캠퍼스').some(r=>r.type==='거래처'));
 assert.ok(globalResults(data,'20261004-12345678-12345678').some(r=>r.href==='#transaction/'+sale.id));
 go('dashboard');const form=document.querySelector('[data-global-form]');form.querySelector('input').value='TC80150';form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));assert.match(document.querySelector('main').textContent,/TC80150/);
});

test('related customer order is searchable instead of a long select',()=>{
 go('transactions');click('[data-action="erp-expense-new"]');assert.ok(dialog.querySelector('[data-order-search]'));assert.equal(dialog.querySelector('select[name="orderId"]'),null);
 set('[data-order-search]',order.number);click('[data-order-choice="'+order.id+'"]');assert.equal(dialog.querySelector('[name="orderId"]').value,order.id);dialog.close();
});

test('direct order editor uses the same per-line search and has no category field',()=>{
 go('orders');click('[data-action="wf-order-new"]');
 assert.equal(dialog.querySelector('[data-catalog-search]'),null);assert.equal(dialog.querySelector('[data-key="section"]'),null);
 assert.ok(dialog.querySelector('[data-line="0"] [data-product-search]'));click('[data-line="0"] [data-product-search]');assert.ok(dialog.querySelector('[data-line="0"] [data-catalog-manual]'));dialog.close();
});

test('account, product and order tables expose edit and delete controls',()=>{
 go('accounts');assert.ok(document.querySelector('[data-action="erp-account-edit"]'));assert.ok(document.querySelector('[data-action="erp-account-delete"]'));
 go('products');assert.ok(document.querySelector('[data-action="erp-product-edit"]'));assert.ok(document.querySelector('[data-action="erp-product-delete"]'));
 go('orders');assert.ok(document.querySelector('[data-action="wf-order-edit"]'));assert.ok(document.querySelector('[data-action="wf-order-delete"]'));
 const target=data.orders.at(-1);go('order/'+target.id);assert.ok(document.querySelector('[data-action="erp-order-edit"]'));assert.ok(document.querySelector('[data-action="erp-order-delete"]'));
 click('[data-action="erp-order-delete"]');assert.match(dialog.textContent,/정말로 삭제하시겠습니까/);assert.match(dialog.textContent,/함께 영구 삭제/);assert.match(dialog.textContent,/재고 이동과 회계 장부도 자동/);dialog.close();
});

test('confirmed voucher exposes full editing and cascading delete actions',async()=>{
 const v=sale;go('transaction/'+v.id);assert.ok(document.querySelector('[data-action="erp-voucher-edit"]'));assert.ok(document.querySelector('[data-action="erp-voucher-delete"]'));
 click('[data-action="erp-voucher-edit"]');assert.match(dialog.textContent,/거래명세서.*건명/);assert.ok(dialog.querySelector('[data-item-editor]'));dialog.close();
 go('transaction/'+v.id);click('[data-action="erp-voucher-delete"]');assert.match(dialog.textContent,/입출고.*수금·지급.*수기 세금자료/);dialog.close();
});

test('migrated sales, purchases and personal withdrawals expose delete in the page header',()=>{
 for(const kind of ['sale','purchase','withdrawal']){
  const voucher=data.vouchers.find(v=>v.legacy&&v.kind===kind);assert.ok(voucher,kind);
  go('transaction/'+voucher.id);const remove=document.querySelector('[data-action="erp-voucher-delete"]');assert.ok(remove,kind);assert.equal(remove.textContent,'전표 삭제');
  click('[data-action="erp-voucher-delete"]');assert.match(dialog.textContent,/연결된 입출고.*수금·지급/);dialog.close();
 }
});


test('manual task deep link supports completion without reopening a modal',async()=>{
 const t=data.tasks.find(t=>t.title==='도면 수정해서 보내기');go('task/'+t.id);assert.match(document.querySelector('main').textContent,/도면 수정해서 보내기/);
 click('[data-action="erp-task-edit"]');set('[name="status"]','completed');await submit();
 assert.equal(tasks(data).some(x=>x.sourceId===t.id),false);assert.equal(dialog.open,false);
});

test('personal withdrawal is saved without VAT and excluded from profit',async()=>{
 go('transactions');click('[data-action="erp-withdrawal-new"]');set('[name="title"]','개인 생활비');set('[data-key="name"]','생활비');set('[data-key="unitPrice"]',100000);await submit();
 const v=data.vouchers.at(-1);assert.equal(v.kind,'withdrawal');assert.equal(v.vat,0);go('transaction/'+v.id);click('[data-action="erp-confirm"]');await submit();
 assert.equal(data.journalEntries.at(-1).rows.find(r=>r.account==='drawings').debit,100000);
});

test('monthly confirmed turnover preserves original period and reverses cancellation period',()=>{
 const v={status:'void',date:'2026-09-01',confirmedDate:'2026-09-02',amount:110000,voidDate:'2026-10-01'};
 assert.equal(confirmedAmount(v,'2026-09'),110000);assert.equal(confirmedAmount(v,'2026-10'),-110000);
 assert.equal(confirmedAmount({...v,status:'void',confirmedDate:undefined},'2026-09'),0);
});


test('dashboard remains current-month even after browsing another analysis month',()=>{
 go('analysis');const picker=document.querySelector('[data-month]');picker.value='2026-09';picker.dispatchEvent(new window.Event('change',{bubbles:true}));
 go('dashboard');assert.equal(document.querySelector('.erp-kpis strong').textContent,money(metrics(data,today().slice(0,7)).sales));
});

test('first manual VAT zero, rapid changes and submit without an event preserve selected values',async()=>{
 go('transactions');click('[data-action="erp-sale-new"]');set('[name="accountId"]','ac-0');set('[name="title"]','VAT 선택 회귀');
 set('[data-key="name"]','VAT 검증');set('[data-key="unitPrice"]',100000);
 set('[data-key="vatRate"]','0');assert.match(dialog.querySelector('[data-editor-totals]').textContent,/합계 100,000원/);
 set('[data-key="quantity"]',2);set('[data-key="vatRate"]','10');set('[data-key="vatRate"]','0');
 // Native controls can change immediately before submit, without a delivered input event.
 dialog.querySelector('[data-key="unitPrice"]').value='125000';
 await submit();const v=data.vouchers.at(-1);assert.equal(v.vat,0);assert.equal(v.net,250000);assert.equal(v.items[0].vatRate,0);
 go('transaction/'+v.id);click('[data-action="erp-voucher-edit"]');
 assert.equal(dialog.querySelector('[data-key="vatRate"]').value,'0');
 set('[data-key="vatRate"]','10');set('[data-key="vatRate"]','0');click('[data-row="copy"]');
 assert.equal(dialog.querySelector('[data-line="1"] [data-key="vatRate"]').value,'0');
 click('[data-line="1"] [data-row="remove"]');await submit();assert.equal(data.vouchers.at(-1).amount,250000);
});

test('missing cost shows uncomputable margin in editor quotation and order',async()=>{
 go('quotes');click('[data-action="wf-quote-new"]');set('[name="accountId"]','ac-0');set('[name="title"]','원가 미입력 검증');
 set('[data-key="name"]','미정 원가');set('[data-key="unitPrice"]',100000);
 assert.match(dialog.querySelector('[data-editor-totals]').textContent,/마진 산정 불가/);
 assert.doesNotMatch(dialog.querySelector('[data-editor-totals]').textContent,/예상 마진 100,000원/);
 await submit();const q=data.quotes.at(-1);assert.equal(margin(data,q).gross,null);
 go('quotes');click('[data-action="wf-quote-detail"][data-id="'+q.id+'"]');assert.match(dialog.textContent,/마진 산정 불가/);
 click('[data-action="wf-convert"]');set('[name="orderType"]','BOX');await submit();
 const o=data.orders.at(-1);go('order/'+o.id);assert.match(document.querySelector('main').textContent,/마진 산정 불가/);
});

test('VAT change warns on voucher and order while preserving snapshots and optional reason',async()=>{
 const o=data.orders.at(-1),original=structuredClone(o);
 go('order/'+o.id);click('[data-action="erp-order-voucher"]');await submit();const v=data.vouchers.at(-1);
 go('transaction/'+v.id);click('[data-action="erp-voucher-edit"]');set('[data-key="vatRate"]','0');await submit();
 assert.deepEqual(data.orders.at(-1),original);
 for(const route of ['transaction/'+v.id,'order/'+o.id]){go(route);const warn=document.querySelector('.order-difference');assert.match(warn.textContent,/110,000원/);assert.match(warn.textContent,/100,000원/);}
 go('transaction/'+v.id);click('[data-action="erp-difference"]');set('[name="reason"]','세율 검토 후 금액 조정');await submit();
 assert.match(document.querySelector('.order-difference').textContent,/세율 검토 후 금액 조정/);
});

test('accountant evidence section is optional, separate, editable and keeps financial amounts',async()=>{
 const v=data.vouchers.at(-1);go('transaction/'+v.id);
 assert.ok(document.querySelector('[data-evidence-section].tax-evidence'));
 click('[data-action="erp-evidence"]');
 assert.equal(dialog.querySelectorAll('[required]').length,0);await submit();
 click('[data-action="erp-evidence"]');set('[name="tax_evidenceType"]','전자세금계산서');set('[name="tax_writtenDate"]','2026-09-30');set('[name="tax_sourceGross"]',110000);set('[name="tax_accountantMemo"]','검토 부탁드립니다');
 const amount=v.amount,before=structuredClone(data.journalEntries);await submit();
 assert.equal(data.vouchers.at(-1).amount,amount);assert.deepEqual(data.journalEntries,before);
 assert.match(document.querySelector('[data-evidence-section]').textContent,/원본 증빙 합계 110,000원/);
 click('[data-action="erp-evidence"]');assert.equal(dialog.querySelector('[name="tax_writtenDate"]').value,'2026-09-30');dialog.close();
});

test('new manual work starts before progress and completed work can be found and reopened',async()=>{
 go('tasks');click('[data-action="erp-task-new"]');
 assert.equal(dialog.querySelector('[name="status"] option[selected]').value,'not_started');
 const lookup=dialog.querySelector('[name="accountId"]').closest('[data-lookup]');
 assert.equal(lookup.querySelector('select'),null);
 set('[data-lookup-input]','루미나');click('[data-lookup-choice="ac-0"]');
 set('[name="title"]','상태 전환 확인');await submit();let t=data.tasks.at(-1);
 assert.equal(t.status,'not_started');assert.equal(t.done,false);
 go('task/'+t.id);assert.match(document.querySelector('main').textContent,/시작 전/);
 for(const status of ['in_progress','completed']){go('task/'+t.id);click('[data-action="erp-task-edit"]');set('[name="status"]',status);await submit();assert.equal(data.tasks.at(-1).status,status);}
 go('tasks');assert.doesNotMatch(document.querySelector('main').textContent,/상태 전환 확인/);
 click('[data-action="erp-task-filter"][data-id="completed"]');assert.match(document.querySelector('main').textContent,/상태 전환 확인/);
 go('task/'+t.id);click('[data-action="erp-task-edit"]');set('[name="status"]','not_started');await submit();
 go('tasks');click('[data-action="erp-task-filter"][data-id="all"]');assert.match(document.querySelector('main').textContent,/상태 전환 확인/);
});

test('every account and order entry point uses a shared searchable record field',()=>{
 const forms=[['tasks','erp-task-new'],['account/ac-0','erp-note'],['products','erp-product-new'],['quotes','wf-quote-new'],['orders','wf-order-new'],['transactions','erp-sale-new'],['transactions','erp-purchase-new'],['transactions','erp-expense-new'],['payments','erp-receipt-new'],['payments','erp-payment-new']];
 for(const [route,action] of forms){go(route);click(`[data-action="${action}"]`);assert.equal(dialog.querySelector('select[name="accountId"],select[name="supplierId"],select[name="orderId"]'),null);assert.ok(dialog.querySelector('[data-lookup-input]'));dialog.close();}
});

test('cash direction and search apply to unallocated advances as well as vouchers',async()=>{
 for(const [action,memo] of [['erp-receipt-new','검색검증선입금'],['erp-payment-new','검색검증선지급']]){
  go('payments');click(`[data-action="${action}"]`);set('[data-lookup-input]','루미나');click('[data-lookup-choice="ac-0"]');set('[name="amount"]',12345);set('[name="memo"]',memo);await submit();
 }
 go('payments');click('[data-action="erp-cash-filter"][data-id="in"]');assert.match(document.querySelector('main').textContent,/검색검증선입금/);assert.doesNotMatch(document.querySelector('main').textContent,/검색검증선지급/);
 click('[data-action="erp-cash-filter"][data-id="out"]');assert.match(document.querySelector('main').textContent,/검색검증선지급/);assert.doesNotMatch(document.querySelector('main').textContent,/검색검증선입금/);
 window.HTMLInputElement.prototype.setSelectionRange=()=>{};const search=document.querySelector('[data-search]');search.value='없는검색결과';search.dispatchEvent(new window.Event('input',{bubbles:true}));assert.doesNotMatch(document.querySelector('main').textContent,/검색검증선지급/);
});


test('deep-linked guide renders walkthrough and related menu through actual app routing',async()=>{
 const previous=JSON.stringify(data),before=commands.length;
 go('guide/accounts');await tick();
 assert.match(document.querySelector('main').textContent,/따라 해보기/);
 assert.match(document.querySelector('main').textContent,/거래처를 검색/);
 assert.ok(document.querySelector('main a[href="#accounts"]'));
 assert.equal(JSON.stringify(data),previous);assert.equal(commands.length,before);
 go('accounts');assert.ok(document.querySelector('.data-table'));
});

test('delivery succeeds with no stock, closes order, shows provisional cost and can be deleted',async()=>{
 go('products');click('[data-action="erp-product-new"]');
 set('[name="code"]','ADVISORY');set('[name="name"]','재고 누락 납품');set('[name="purchasePrice"]',50000);set('[name="salePrice"]',100000);await submit();
 const product=data.products.at(-1);
 go('quotes');click('[data-action="wf-quote-new"]');set('[name="accountId"]','ac-0');set('[name="title"]','재고 없이 납품');click('[data-line="0"] [data-product-search]');set('[data-line="0"] [data-line-catalog-search]','ADVISORY');click('[data-line="0"] [data-catalog-id="'+product.id+'"]');await submit();
 const q=data.quotes.at(-1);go('quotes');click('[data-action="wf-quote-detail"][data-id="'+q.id+'"]');click('[data-action="wf-convert"]');await submit();
 const o=data.orders.at(-1);go('order/'+o.id);click('[data-action="erp-order-voucher"]');await submit();
 const v=data.vouchers.at(-1);go('transaction/'+v.id);click('[data-action="erp-fulfill"]');
 assert.match(dialog.textContent,/부족해도 납품 완료를 기록/);await submit();
 assert.equal(stockBalance(data,product.id).quantity,-1);assert.equal(data.orders.find(x=>x.id===o.id).status,'납품 완료');
 assert.match(document.querySelector('main').textContent,/출고 원가·손익은 잠정값/);
 assert.equal(document.querySelector('[data-action="erp-fulfill"]'),null);
 go('order/'+o.id);assert.match(document.querySelector('main').textContent,/원가·손익 잠정/);
 go('product/'+product.id);assert.match(document.querySelector('main').textContent,/산정 보류/);
 go('analysis');const picker=document.querySelector('[data-month]');picker.value=today().slice(0,7);picker.dispatchEvent(new window.Event('change',{bubbles:true}));assert.match(document.querySelector('main').textContent,/원가·손익 잠정/);
 go('transaction/'+v.id);click('[data-action="erp-fulfillment-delete"]');await submit();
 assert.deepEqual(stockBalance(data,product.id),{quantity:0,value:0});
 assert.equal(data.orders.find(x=>x.id===o.id).status,'주문 접수');
 assert.ok(document.querySelector('[data-action="erp-fulfill"]'));
});

test('evidence is collapsed after operational history and approval lives with optional metadata',()=>{
 go('transaction/'+sale.id);
 const main=document.querySelector('main'),section=main.querySelector('[data-evidence-section]');
 assert.equal(section.tagName,'DETAILS');assert.equal(section.hasAttribute('open'),false);
 assert.ok(section.querySelector('[data-action="erp-approval"]'));
 assert.equal(main.querySelector('.page-head [data-action="erp-approval"]'),null);
 const children=[...main.children];assert.ok(children.indexOf(main.querySelector('[data-operational-history]'))<children.indexOf(section));
 const before=structuredClone(data),count=commands.length;
 section.setAttribute('open','');section.removeAttribute('open');
 assert.deepEqual(data,before);assert.equal(commands.length,count);
});

test('account history is full width, paginated beyond 100 records and retains prior quotation versions',()=>{
 const notes=data.notes,quotes=data.quotes;
 data.notes=[...notes,...Array.from({length:105},(_,i)=>({id:'ux-note-'+i,accountId:'ac-0',date:'2026-10-06',body:'검증 이력 '+i}))];
 const q=quotes.find(q=>q.accountId==='ac-0');
 data.quotes=[...quotes,{...q,id:'ux-old',familyId:'ux-family',version:1,title:'버전 검증',date:'2026-10-06'},{...q,id:'ux-new',familyId:'ux-family',version:2,title:'버전 검증',date:'2026-10-06'}];
 go('account/ac-0');
 let history=document.querySelector('[data-account-history]');
 assert.equal(history.closest('.detail-grid'),null);assert.equal(history.querySelectorAll('.activity-row').length,10);
 click('[data-action="erp-history-filter"][data-kind="note"]');
 for(let i=0;i<5;i++)click('[data-action="erp-history-more"]');
 history=document.querySelector('[data-account-history]');assert.ok(history.querySelectorAll('.activity-row').length>=105);
 click('[data-action="erp-history-filter"][data-kind="quote"]');
 assert.equal(document.querySelector('[data-account-history] a[href="#quote/ux-old"]'),null);
 assert.ok(document.querySelector('[data-account-history] a[href="#quote/ux-new"]'));
 click('[data-action="erp-history-versions"]');assert.match(document.querySelector('[data-account-history] a[href="#quote/ux-old"]').textContent,/v1.*이전 버전/);
 click('[data-action="erp-history-filter"][data-kind="payment"]');
 assert.ok(document.querySelector('[data-account-history]').textContent.match(/수금|지급/));
 data.notes=notes;data.quotes=quotes;go('dashboard');
});

test('list search, type filter and scroll survive detail navigation and nested return',()=>{
 window.scrollY=0;window.scrollTo=(_x,y)=>{window.scrollY=y;};
 // Match real input selection APIs absent from the test DOM.
 window.HTMLElement.prototype.setSelectionRange=function(){};
 go('transactions');click('[data-action="erp-trade-filter"][data-id="sale"]');
 const input=document.querySelector('[data-search]');input.value='철제함';input.dispatchEvent(new window.Event('input',{bubbles:true}));
 window.scrollY=420;window.dispatchEvent(new window.Event('scroll'));
 go('transaction/'+sale.id);assert.equal(document.querySelector('[data-list-return]').getAttribute('href'),'#transactions');
 go('order/'+order.id);assert.equal(document.querySelector('[data-context-return]').getAttribute('href'),'#transaction/'+sale.id);
 go('transaction/'+sale.id);go('transactions');
 assert.equal(document.querySelector('[data-search]').value,'철제함');assert.equal(window.scrollY,420);
 assert.ok(document.querySelector('[data-action="erp-trade-filter"][data-id="sale"]').classList.contains('active'));
 const reset=document.querySelector('[data-search]');reset.value='';reset.dispatchEvent(new window.Event('input',{bubbles:true}));
 click('[data-action="erp-trade-filter"][data-id="all"]');
});

test('one primary transaction action follows draft, unpaid and unfulfilled states',async()=>{
 go('transactions');click('[data-action="erp-sale-new"]');set('[name="accountId"]','ac-0');set('[name="title"]','UX 상태 검증');set('[data-key="name"]','검증 품목');set('[data-key="unitPrice"]',10000);await submit();
 const v=data.vouchers.at(-1);go('transaction/'+v.id);
 const primary=()=>[...document.querySelectorAll('.page-head .primary')].map(e=>e.dataset.action);
 assert.deepEqual(primary(),['erp-confirm']);
 click('[data-action="erp-confirm"]');await submit();assert.deepEqual(primary(),['erp-settle']);
 click('[data-action="erp-settle"]');await submit();assert.deepEqual(primary(),['erp-fulfill']);
 click('[data-action="erp-fulfill"]');await submit();assert.deepEqual(primary(),[]);
 assert.match(document.querySelector('.transaction-summary').textContent,/결제 완료/);
 assert.match(document.querySelector('.transaction-summary').textContent,/납품 완료/);
});

test('order delivery button uses the connected voucher once and completed order changes its guidance',async()=>{
 const o=data.orders.at(-1);go('order/'+o.id);
 const button=document.querySelector('[data-action="erp-fulfill"]');assert.ok(button);
 const linked=data.vouchers.find(v=>v.orderId===o.id&&v.status!=='void');assert.equal(button.dataset.id,linked.id);
 const before=data.fulfillments.length;click('[data-action="erp-fulfill"]');await submit();
 assert.equal(data.fulfillments.length,before+1);assert.equal(data.fulfillments.at(-1).voucherId,linked.id);
 assert.equal(document.querySelector('[data-action="erp-fulfill"]'),null);
 assert.match(document.querySelector('.bottom-note').textContent,/납품을 모두 기록/);
});

test('quote line controls preserve copy, move and remove with a separate cost field container',()=>{
 go('quotes');click('[data-action="wf-quote-new"]');set('[data-key="name"]','첫째');set('[data-key="unitPrice"]',100);
 assert.ok(dialog.querySelector('.line-cost>span'));assert.equal(dialog.querySelector('.line-cost').tagName,'LABEL');
 click('[data-row="copy"]');set('[data-line="1"] [data-key="name"]','둘째');
 click('[data-line="1"] [data-row="up"]');assert.equal(dialog.querySelector('[data-line="0"] [data-key="name"]').value,'둘째');
 click('[data-line="0"] [data-row="down"]');assert.equal(dialog.querySelector('[data-line="0"] [data-key="name"]').value,'첫째');
 click('[data-line="1"] [data-row="remove"]');assert.equal(dialog.querySelectorAll('[data-line]').length,1);
 assert.equal(dialog.querySelectorAll('.row-tools button').length,4);dialog.close();
});
