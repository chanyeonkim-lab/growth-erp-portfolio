export const today = () => new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
export const id = prefix => `${prefix}-${crypto.randomUUID()}`;
export const money = value => `${Math.round(value || 0).toLocaleString('ko-KR')}원`;
export const shortMoney = value => `${((value || 0)/10000).toLocaleString('ko-KR',{maximumFractionDigits:2})}`;
export const monthEnd = month => {const [y,m]=month.split('-').map(Number);return `${month}-${new Date(y,m,0).getDate()}`;};
export const cutoffFor = month => month===today().slice(0,7) ? today() : monthEnd(month);
export const accountName = (state,id) => state.accounts.find(a=>a.id===id)?.name || '거래처 미지정';
export const gross = item => (item.net || 0)+(item.vat || 0);
export const categories = ['자재비','외주비','월세','차량비','통신비','배송비','기타 사업 비용','개인 인출'];
export const stages = ['주문 접수','제작 중','납품 완료','취소'];

export function paidOnInvoice(state,invoiceId,cutoff=today()) {
 return state.allocations.filter(a=>a.invoiceId===invoiceId && a.date<=cutoff).reduce((sum,a)=>{
  const r=state.receipts.find(r=>r.id===a.receiptId);
  return sum+(r && r.date<=cutoff ? a.amount : 0);
 },0);
}
export function receiptAvailable(state,receiptId,cutoff=today()) {
 const receipt=state.receipts.find(r=>r.id===receiptId);
 if(!receipt || receipt.date>cutoff)return 0;
 return receipt.amount-state.allocations.filter(a=>a.receiptId===receiptId && a.date<=cutoff).reduce((n,a)=>n+a.amount,0);
}
export function adjustedGross(state,invoice,cutoff='9999-12-31') {
 return gross(invoice)-state.invoices.filter(i=>i.status!=='void'&&i.date<=cutoff).reduce((n,i)=>n+(i.creditApplications?i.creditApplications.filter(c=>c.invoiceId===invoice.id).reduce((n,c)=>n+c.amount,0):i.creditForId===invoice.id?-gross(i):0),0);
}
export function excessPayments(state,cutoff=today()) {
 return state.invoices.filter(i=>i.date<=cutoff&&i.status!=='void'&&gross(i)>0).map(i=>({...i,excess:Math.max(0,paidOnInvoice(state,i.id,cutoff)-adjustedGross(state,i,cutoff))})).filter(i=>i.excess>0);
}
export function receivables(state,cutoff=today()) {
 return state.invoices.filter(i=>i.date<=cutoff && i.status!=='void' && gross(i)>0).map(i=>{
  const paid=paidOnInvoice(state,i.id,cutoff);
  const adjusted=adjustedGross(state,i,cutoff);
  return {...i,paid,adjusted,balance:Math.max(0,adjusted-paid),overdue:!!i.dueDate && i.dueDate<cutoff && adjusted>paid};
 }).filter(i=>i.balance>0).sort((a,b)=>a.date.localeCompare(b.date));
}
export function ranking(state,month) {
 const totals=new Map();
 state.invoices.filter(i=>i.date.startsWith(month)&&i.status!=='void').forEach(i=>totals.set(i.accountId,(totals.get(i.accountId)||0)+gross(i)));
 return [...totals].map(([accountId,amount])=>({accountId,amount,name:accountName(state,accountId)})).sort((a,b)=>b.amount-a.amount||a.name.localeCompare(b.name,'ko')).slice(0,5);
}
export function metrics(state,month) {
 const invoices=state.invoices.filter(i=>i.date.startsWith(month)&&i.status!=='void');
 const incurred=state.expenses.filter(e=>e.date.startsWith(month)&&e.category!=='개인 인출');
 const net=invoices.reduce((n,i)=>n+i.net,0),vat=invoices.reduce((n,i)=>n+i.vat,0);
 const cost=incurred.reduce((n,e)=>n+e.net+(e.vatDeductible?0:e.vat),0);
 const paid=state.expenses.filter(e=>e.paidDate?.startsWith(month)&&e.category!=='개인 인출').reduce((n,e)=>n+gross(e),0);
 const personal=state.expenses.filter(e=>e.paidDate?.startsWith(month)&&e.category==='개인 인출').reduce((n,e)=>n+gross(e),0);
 const reserve=state.settings.taxReserves?.[month];
 return {sales:net+vat,net,vat,cost,paid,personal,preTax:net-cost,reserve:reserve??null,profit:reserve==null?net-cost:net-cost-reserve,inputVat:incurred.filter(e=>e.vatDeductible).reduce((n,e)=>n+e.vat,0)};
}
export function addAllocation(state,{receiptId,invoiceId,amount,date}) {
 const receipt=state.receipts.find(r=>r.id===receiptId),invoice=state.invoices.find(i=>i.id===invoiceId);
 if(!receipt||!invoice||invoice.status==='void')throw Error('입금 또는 세금계산서를 확인해 주세요.');
 if(receipt.accountId!==invoice.accountId)throw Error('같은 고객의 입금과 청구금액만 연결할 수 있어요.');
 if(!Number.isSafeInteger(amount)||amount<=0)throw Error('연결 금액은 1원 이상의 정수여야 해요.');
 if(date<receipt.date || date<invoice.date)throw Error('연결일은 입금일·세금계산서 작성일보다 빠를 수 없어요.');
 // Capacity checks include future allocations so historical edits cannot spend money twice.
 if(amount>receiptAvailable(state,receiptId,'9999-12-31'))throw Error('이 입금에서 연결할 수 있는 금액을 초과했어요.');
 if(amount>adjustedGross(state,invoice)-paidOnInvoice(state,invoiceId,'9999-12-31'))throw Error('수정 내역을 반영한 남은 청구금액을 초과했어요.');
 const allocation={id:id('allocation'),receiptId,invoiceId,amount,date};
 state.allocations.push(allocation);return allocation;
}
export function orderCreditCandidates(state,orderId) {
 return state.receipts.filter(r=>r.orderId===orderId && receiptAvailable(state,r.id,'9999-12-31')>0);
}
export function duplicateQuote(state,quote) {
 return state.quotes.find(q=>q.accountId===quote.accountId && q.number===quote.number && q.date===quote.date);
}
