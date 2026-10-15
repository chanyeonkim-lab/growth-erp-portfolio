import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {createLookup} from '../app/lookup.js';
const {window,document}=parseHTML('<div id="root"></div>');globalThis.window=window;
const root=document.querySelector('#root'),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const accounts=Array.from({length:85},(_,i)=>({id:'a'+i,name:i<2?'같은 상호':'고객 '+i,contact:'담당 '+i,phone:'010-1234-'+String(i).padStart(4,'0'),businessNumber:'123-45-'+String(i).padStart(5,'0')}));
const s={accounts,orders:[{id:'o1',number:'BOX-20261005-001',title:'함 제작',accountId:'a80',date:'2026-10-05',status:'주문 접수'}]};
const lookup=createLookup({state:()=>s,esc});lookup.bind(root);
function mount(name='accountId',value='',extra=''){root.innerHTML=lookup.field('거래처',name,(name==='orderId'?s.orders:s.accounts).map(r=>[r.id,r.name||r.number+' · '+r.title]),value,extra);lookup.prune(root);return root.querySelector('[data-lookup-input]');}
function input(el,value){el.value=value;el.dispatchEvent(new window.Event('input',{bubbles:true}));}
function click(el){el.dispatchEvent(new window.Event('click',{bubbles:true}));}
function key(el,key){const event=new window.Event('keydown',{bubbles:true,cancelable:true});Object.defineProperty(event,'key',{value:key});el.dispatchEvent(event);}

test('searches every record beyond visible limit, supports phone normalization and duplicate names',()=>{
 const el=mount();input(el,'');assert.equal(root.querySelectorAll('[role="option"]').length,30);assert.match(root.textContent,/85건 중 30건/);
 input(el,'01012340080');assert.equal(root.querySelectorAll('[role="option"]').length,1);click(root.querySelector('[data-lookup-choice="a80"]'));assert.equal(root.querySelector('[name="accountId"]').value,'a80');
 input(el,'같은 상호');assert.match(root.textContent,/담당 0/);assert.match(root.textContent,/담당 1/);key(el,'ArrowDown');key(el,'ArrowDown');key(el,'Enter');assert.equal(root.querySelector('[name="accountId"]').value,'a1');
});
test('editing clears old association, unmatched optional text cannot silently save, clear is explicit',()=>{
 const el=mount('accountId','a80');input(el,'없는 거래처');assert.equal(root.querySelector('[name="accountId"]').value,'');assert.match(root.textContent,/검색 결과가 없습니다/);assert.throws(()=>lookup.validate(root),/검색 결과에서/);
 click(root.querySelector('[data-lookup-clear]'));assert.equal(el.value,'');assert.doesNotThrow(()=>lookup.validate(root));
 mount('accountId','','required');assert.throws(()=>lookup.validate(root),/선택해 주세요/);
});
test('order search includes customer and keyboard Escape closes without selecting',()=>{
 const el=mount('orderId');input(el,'고객 80');key(el,'ArrowDown');assert.ok(el.getAttribute('aria-activedescendant'));key(el,'Escape');assert.equal(el.getAttribute('aria-expanded'),'false');assert.equal(root.querySelector('[name="orderId"]').value,'');
 key(el,'ArrowDown');key(el,'Enter');assert.equal(root.querySelector('[name="orderId"]').value,'o1');
});
test('selection emits change for dependent fields and dynamic repeated rows keep their own values',()=>{
 const el=mount(),first=root.querySelector('[data-lookup]');let changes=0;first.querySelector('input[type="hidden"]').addEventListener('change',()=>changes++);
 root.insertAdjacentHTML('beforeend',lookup.field('두 번째 거래처','accountId',accounts.map(a=>[a.id,a.name])));
 input(el,'담당 80');click(first.querySelector('[data-lookup-choice="a80"]'));assert.equal(changes,2);
 const second=root.querySelectorAll('[data-lookup]')[1];input(second.querySelector('[data-lookup-input]'),'담당 81');click(second.querySelector('[data-lookup-choice="a81"]'));
 assert.equal(first.querySelector('input[type="hidden"]').value,'a80');assert.equal(second.querySelector('input[type="hidden"]').value,'a81');
});

test('open lookup sees newly loaded accounts without replacing the form or typed query',()=>{
 const el=mount();input(el,'새 탭 고객');
 s.accounts.push({id:'new-tab',name:'새 탭 고객',phone:'02-999-1234'});
 lookup.refresh(root);assert.equal(el.value,'새 탭 고객');
 click(root.querySelector('[data-lookup-choice="new-tab"]'));
 assert.equal(root.querySelector('[name="accountId"]').value,'new-tab');
});
