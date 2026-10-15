import {today,accountName} from './domain.js';
export const taskStatusLabel={not_started:'시작 전',in_progress:'진행 중',completed:'완료'};
export const taskStatus=t=>Object.hasOwn(taskStatusLabel,t.status)?t.status:t.done?'completed':'in_progress';
export const typeLabel={sale:'매출',purchase:'매입',expense:'경비',withdrawal:'개인 인출'};
export const costKnown=r=>r.costKnown===true||(r.costKnown!==false&&(Number(r.costUnit)>0||Number(r.estimatedCost)>0));
export function orderMismatch(s,v){
 const o=s.orders.find(o=>o.id===v.orderId);if(!o||v.kind!=='sale')return null;
 const amount=o.amount??o.net+o.vat,diff=v.amount-amount;
 return diff?{order:o,orderAmount:amount,voucherAmount:v.amount,difference:diff}:null;
}
export const statusLabel={draft:'미확정',confirmed:'전표 확정',void:'취소'};
export const stockBalance=(s,pid)=>s.stockMoves.filter(m=>m.productId===pid).reduce((a,m)=>({quantity:Math.round((a.quantity+m.quantity)*1000)/1000,value:a.value+m.value}),{quantity:0,value:0});
export function provisionalStock(s,pid){
 const moves=s.stockMoves.filter(m=>m.productId===pid),reversed=new Set(moves.filter(m=>m.sourceId.startsWith('reverse-')).map(m=>m.sourceId.slice(8))),b=stockBalance(s,pid);
 return b.quantity<0||b.value<0||(b.quantity===0&&b.value!==0)||moves.some(m=>m.costProvisional&&!m.sourceId.startsWith('reverse-')&&!reversed.has(m.sourceId));
}
export const paymentUsed=p=>(p.allocations||[]).reduce((n,a)=>n+a.amount,0);
export const paid=(s,v,cutoff='9999-12-31')=>s.payments.filter(p=>(p.status==='confirmed'||p.voidDate>cutoff)&&p.date<=cutoff).reduce((n,p)=>n+p.allocations.filter(a=>a.voucherId===v.id&&(a.date||p.date)<=cutoff).reduce((n,a)=>n+a.amount,0),0);
export const remaining=(s,v,cutoff='9999-12-31')=>Math.max(0,v.amount-paid(s,v,cutoff)-s.vouchers.filter(c=>c.status==='confirmed'&&c.date<=cutoff).flatMap(c=>c.credits||[]).filter(c=>c.voucherId===v.id).reduce((n,c)=>n+c.amount,0));
export const lineRemaining=(s,v,r)=>Math.max(0,Math.round((r.quantity-s.fulfillments.filter(f=>f.voucherId===v.id&&f.status==='completed').flatMap(f=>f.items).filter(i=>i.voucherLineId===r.id).reduce((n,i)=>n+i.quantity,0))*1000)/1000);
export const tone=(due,asOf=today())=>due<asOf?'red':due===asOf?'orange':'blue';
export const dueLabel=(due,asOf=today())=>due<asOf?'기한 지남':due===asOf?'오늘':'예정';
export const confirmedAmount=(v,month)=>((v.status==='confirmed'||v.confirmedDate)?((v.confirmedDate||v.date).startsWith(month)?v.amount:0)-(v.voidDate?.startsWith(month)?v.amount:0):0);
export function tasks(s,asOf=today()) {
 const out=s.tasks.filter(t=>taskStatus(t)!=='completed').map(t=>({...t,id:'manual-'+t.id,sourceId:t.id,category:'직접 등록',dueDate:t.dueDate||t.date,href:'#task/'+t.id,manual:true}));
 for(const v of s.vouchers.filter(v=>v.status!=='void')){
  const common={accountId:v.accountId,sourceId:v.id,href:'#transaction/'+v.id};
  if(v.status==='draft'||v.legacyReviewNeeded)out.push({...common,id:'confirm-'+v.id,category:'전표 확정',title:accountName(s,v.accountId)+' · '+typeLabel[v.kind]+' 전표 확인',dueDate:v.confirmationDueDate||v.date,amount:v.amount});
  if(v.status==='confirmed'&&v.kind!=='withdrawal'&&remaining(s,v,asOf)>0&&v.date<=asOf)out.push({...common,id:'payment-'+v.id,category:v.kind==='sale'?'미수금':'지급할 돈',title:accountName(s,v.accountId)+(v.kind==='sale'?' · 수금 확인':' · 지급 확인'),dueDate:v.dueDate,amount:remaining(s,v,asOf)});
  if(!v.legacy&&v.kind==='purchase'&&v.items.some(r=>lineRemaining(s,v,r)>0))out.push({...common,id:'receive-'+v.id,category:'입고 확인',title:accountName(s,v.accountId)+' · 매입 품목 입고',dueDate:v.expectedDate||v.date});
 }
 for(const o of s.orders.filter(o=>!['납품 완료','취소'].includes(o.status))){
  const v=s.vouchers.find(v=>v.orderId===o.id&&v.kind==='sale'&&v.status!=='void');
  out.push({id:'delivery-'+o.id,sourceId:o.id,accountId:o.accountId,category:'주문·납품',title:o.title+' · '+o.status,dueDate:o.dueDate||o.date,href:v?'#transaction/'+v.id:'#order/'+o.id});
 }
 return out.map(t=>({...t,tone:tone(t.dueDate,asOf),dueLabel:dueLabel(t.dueDate,asOf)})).sort((a,b)=>a.dueDate.localeCompare(b.dueDate)||a.title.localeCompare(b.title,'ko'));
}
export function metrics(s,month){
 const vs=s.vouchers.filter(v=>(v.status==='confirmed'||v.confirmedDate)&&((v.confirmedDate||v.date).startsWith(month)||v.voidDate?.startsWith(month)));
 const periodAmount=v=>confirmedAmount(v,month);
 let revenue=0,cost=0;
 for(const j of s.journalEntries.filter(j=>j.date.startsWith(month)))for(const r of j.rows){if(r.account==='revenue')revenue+=r.credit-r.debit;if(['expense','cogs'].includes(r.account))cost+=r.debit-r.credit;}
 const personal=p=>p.allocations.length&&p.allocations.every(a=>s.vouchers.find(v=>v.id===a.voucherId)?.kind==='withdrawal');
 const outgoing=s.payments.filter(p=>p.direction==='out'&&!personal(p)).reduce((n,p)=>n+(p.date.startsWith(month)?p.amount:0)-(p.voidDate?.startsWith(month)?p.amount:0),0);
 const reserve=s.settings.taxReserves?.[month]??null;
 return {provisionalCost:s.stockMoves.some(m=>m.costProvisional&&m.date.startsWith(month)),sales:vs.filter(v=>v.kind==='sale').reduce((n,v)=>n+periodAmount(v),0),purchase:vs.filter(v=>v.kind==='purchase').reduce((n,v)=>n+v.net,0),outgoing,revenue,cost,profit:revenue-cost-(reserve||0),reserve,unreceived:s.vouchers.filter(v=>v.status==='confirmed'&&v.kind==='sale'&&!v.legacy&&v.items.some(r=>lineRemaining(s,v,r)>0)).reduce((n,v)=>n+v.net-s.fulfillments.filter(f=>f.voucherId===v.id&&f.status==='completed').reduce((n,f)=>n+f.net,0),0)};
}
export function margin(s,record){
 let estimated=0,unknown=0;
 for(const r of record.items||[]){if(!costKnown(r))unknown++;else estimated+=r.estimatedCost??Math.round((r.costUnit||0)*r.quantity);}
 const v=s.vouchers.find(v=>v.orderId===record.id&&v.kind==='sale'&&v.status!=='void');
 const fs=v?s.fulfillments.filter(f=>f.voucherId===v.id&&f.status==='completed'):[];
 const costs=s.vouchers.filter(v=>v.orderId===record.id&&['expense','purchase'].includes(v.kind)&&v.status==='confirmed');
 const extra=costs.filter(v=>v.kind==='expense'||v.legacy).reduce((n,v)=>n+v.net+(v.vatDeductible?0:v.vat),0)+s.fulfillments.filter(f=>f.status==='completed'&&s.vouchers.some(v=>v.id===f.voucherId&&v.orderId===record.id&&v.kind==='purchase')).flatMap(f=>f.items).filter(r=>!s.products.find(p=>p.id===r.productId)?.trackStock).reduce((n,r)=>n+r.cost,0);
 return {provisionalCost:fs.some(f=>f.costProvisional),estimated,gross:unknown?null:(record.net??record.amount??0)-estimated,unknown,actualCost:fs.reduce((n,f)=>n+f.cost,0)+extra,realized:fs.reduce((n,f)=>n+f.net,0)};
}
export function globalResults(s,query){
 const q=query.trim().toLowerCase();if(!q)return [];
 const hit=(...x)=>x.join(' ').toLowerCase().includes(q);
 return [
 ...s.accounts.filter(a=>hit(a.name,a.contact,a.phone,a.phone2)).map(a=>({type:'거래처',title:a.name,detail:a.active===false?'비활성 거래처':a.contact||a.kind,href:'#account/'+a.id})),
 ...s.products.filter(p=>hit(p.code,p.name,p.spec)).map(p=>({type:'품목',title:p.code+' · '+p.name,detail:p.spec,href:'#product/'+p.id})),
 ...s.vouchers.filter(v=>hit(v.number,v.title,v.approvalNumber,accountName(s,v.accountId),...v.items.map(r=>r.name))).map(v=>({type:typeLabel[v.kind]+' 전표',title:v.number+' · '+v.title,detail:accountName(s,v.accountId),href:'#transaction/'+v.id})),
 ...s.quotes.filter(v=>hit(v.number,v.title,accountName(s,v.accountId))).map(v=>({type:'견적',title:v.number+' · '+v.title,href:'#quote/'+v.id})),
 ...s.orders.filter(v=>hit(v.number,v.title,accountName(s,v.accountId))).map(v=>({type:'주문',title:(v.number||v.id)+' · '+v.title,href:'#order/'+v.id}))].slice(0,60);
}
