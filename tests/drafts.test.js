import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {createDrafts} from '../app/drafts.js';
const {window,document}=parseHTML('<dialog open><form><input name="title"><input name="accountId" type="hidden"></form></dialog>');
Object.assign(globalThis,{window,document});const dialog=document.querySelector('dialog');dialog.open=true;
const data=new Map(),storage=()=>({getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)});
let scope='live:user-a';const drafts=createDrafts({dialog,scope:()=>scope,storage});
const mount=()=>{dialog.innerHTML='<form><input name="title"><input name="accountId" type="hidden"></form>';};
test('incomplete draft restores fields and line items, isolates users, and clears only explicitly',async()=>{
 const first=drafts('quote:new');first.bind(()=>[{name:'미완성',quantity:3,unitPrice:100}]);
 dialog.querySelector('[name=title]').value='거래처 없이 작성';dialog.querySelector('[name=title]').dispatchEvent(new window.Event('input',{bubbles:true}));await Promise.resolve();
 mount();const restored=drafts('quote:new');assert.equal(restored.items[0].quantity,3);restored.bind(()=>restored.items);
 assert.equal(dialog.querySelector('[name=title]').value,'거래처 없이 작성');
 scope='live:user-b';assert.equal(drafts('quote:new').items,undefined);
 scope='live:user-a';assert.equal(drafts('voucher:purchase:new').items,undefined);
 restored.clear();assert.equal(drafts('quote:new').items,undefined);
});
test('storage failure is visible rather than falsely claiming a saved draft',async()=>{
 mount();const draft=createDrafts({dialog,scope:()=>'',storage:()=>{throw Error('blocked');}})('q');draft.bind(()=>[]);
 dialog.querySelector('input').dispatchEvent(new window.Event('input',{bubbles:true}));await Promise.resolve();
 assert.match(dialog.textContent,/ 임시 저장|임시 저장을 사용할 수 없습니다/);
});
