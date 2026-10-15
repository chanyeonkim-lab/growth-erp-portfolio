// Full public shell in a DOM with Flask test-client transport; no browser navigation.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
import {parseHTML} from 'linkedom';
const python=process.env.GROWTH_TEST_PYTHON||'python3';
function backend(path,body){
 const run=spawnSync(python,['-c',`import sys,json
from demo_cloud import app
p,b=json.load(sys.stdin)
c=app.test_client()
r=c.get(p) if b is None else c.post(p,json=b)
print(json.dumps(dict(ok=r.status_code<400,value=r.json)))`],{input:JSON.stringify([path,body??null]),encoding:'utf8',maxBuffer:5*1024*1024});
 assert.equal(run.status,0,run.stderr);return JSON.parse(run.stdout);
}
const {window,document}=parseHTML(fs.readFileSync('app/index.html','utf8'));
const location={hash:'#dashboard',href:'https://demo.example/#dashboard',origin:'https://demo.example',reload(){}};
Object.assign(globalThis,{window,document,location});
// Node 25's native LockManager retains event-loop handles; use a deterministic DOM-test lock.
Object.defineProperty(navigator,'locks',{value:{request:async(_name,fn)=>fn()},configurable:true});
const stored=new Map();
globalThis.localStorage={getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)};
globalThis.fetch=async(path,options)=>{const r=backend(path,options?.body?JSON.parse(options.body):undefined);return {ok:r.ok,json:async()=>r.value};};
const modal=document.querySelector('#modal');modal.showModal=function(){this.open=true;};modal.close=function(){this.open=false;};
globalThis.FormData=class{constructor(form){this.values=new Map([...form.querySelectorAll('input,select,textarea')].filter(e=>e.name&&!e.disabled).map(e=>[e.name,e.value]));}get(k){return this.values.get(k)??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}};
const go=route=>{location.hash='#'+route;window.dispatchEvent(new window.Event('hashchange'));};
await import('../app/app.js');
const snapshot=()=>JSON.parse(stored.get('growth-portfolio-v1'));

test('public app boots with real connected seed and a visible synthetic-data notice',()=>{
 assert.match(document.querySelector('main').textContent,/사업의 흐름을, 선명하게/);
 assert.match(document.querySelector('.demo-bar').textContent,/가상 데이터/);
 assert.equal(document.querySelectorAll('.portfolio-kpi').length,4);
 assert.equal(document.querySelectorAll('.portfolio-chart circle').length,6);
 assert.ok(snapshot().state.vouchers.length>=55);
 assert.equal(Object.keys(snapshot().files).length,3);
});
test('all core public routes render meaningful content without undefined or NaN',()=>{
 const s=snapshot().state;
 for(const route of ['tasks','accounts','quotes','orders','transactions','payments','products','analysis','documents','settings',
   'account/'+s.accounts[0].id,'quote/'+s.quotes.at(-1).id,'order/'+s.orders.at(-1).id,'transaction/'+s.vouchers[0].id]){
  go(route);const text=document.querySelector('main').textContent;
  assert.ok(text.length>30,route);assert.doesNotMatch(text,/undefined|NaN/,route);
 }
 go('dashboard');
});
test('public settings has reset and backup without production account or API key setup',()=>{
 go('settings');assert.match(document.querySelector('main').textContent,/이 브라우저/);
 assert.ok(document.querySelector('main [data-demo-reset]'));
 assert.ok(document.querySelector('main a[href="/api/backup"]'));
 assert.equal(document.querySelector('[data-action="switch-workspace"]'),null);
 assert.equal(document.querySelector('[data-action="password"]'),null);
});
test('payment command updates visitor storage while another seed remains unchanged',async()=>{
 const {demoApi}=await import('../app/demo-store.js');
 const before=await demoApi('/api/state');const v=[...before.state.vouchers].reverse().find(v=>v.kind==='sale'&&v.status==='confirmed');
 const r=await demoApi('/api/workflow',{action:'erp.payment.save',revision:before.revision,data:{direction:'in',accountId:v.accountId,date:v.date,amount:v.amount,voucherId:v.id}});
 assert.equal(r.revision,before.revision+1);
 assert.equal(r.state.payments.length,before.state.payments.length+1);
 assert.equal(snapshot().state.payments.at(-1).allocations[0].voucherId,v.id);
 const fresh=backend('/api/demo/seed').value;
 assert.equal(fresh.state.payments.length,before.state.payments.length);
});
test('stale tab revisions cannot overwrite a newer visitor ledger',async()=>{
 const {demoApi}=await import('../app/demo-store.js');const previous=snapshot();
 await assert.rejects(demoApi('/api/workflow',{action:'erp.task.save',revision:previous.revision-1,data:{title:'overwrite'}}),/다른 창/);
 assert.equal(snapshot().revision,previous.revision);
});
