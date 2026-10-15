import {margin,stockBalance,provisionalStock,costKnown} from './erp-domain.js';
import {statementSale} from './statements.js';
import {orderCost} from './tax.js';
import {today,money,accountName} from './domain.js';

// Mirrors server rounding for the preview; the server owns persisted amounts.
export function lineAmounts(row) {
 const scaled=Math.round(Number(row.quantity||0)*1000),price=Number(row.unitPrice||0);
 if(!Number.isSafeInteger(scaled)||!Number.isSafeInteger(price)||scaled<0||price<0)return {net:0,vat:0};
 const net=Number((BigInt(scaled)*BigInt(price)+500n)/1000n);
 return {net,vat:Number((BigInt(net)*BigInt(Number(row.vatRate??10))+50n)/100n)};
}
export const quoteFamily=q=>q.familyId||q.id;
export const quoteNeedsQuantityReview=q=>(q.items||[]).some(r=>Number(r.quantity)<=0||r.net<0);
export function remainingItems(state,order) {
 return (order.items||[]).map(item=>{
  const used=(state.deliveries||[]).filter(d=>d.orderId===order.id&&d.status!=='void').flatMap(d=>d.items).filter(r=>r.orderLineId===item.id).reduce((n,r)=>n+Math.round(r.quantity*1000),0);
  return {...item,delivered:used/1000,remaining:(Math.round(item.quantity*1000)-used)/1000};
 });
}

export function createWorkflow(ctx) {
 const {esc,field,select,area,modal,dialog,table,badge,empty,pageHead,searchBox,match,toast}=ctx;
 const state=()=>ctx.state();
 const button=(label,action,id='',style='')=>`<button type="button" class="button ${style}" data-action="wf-${action}" data-id="${esc(id)}">${label}</button>`;
 const customer=id=>esc(accountName(state(),id));
 const accounts=()=>[['','선택해 주세요'],...state().accounts.filter(a=>a.active!==false).map(a=>[a.id,a.name])];
 const familyVersions=q=>state().quotes.filter(r=>quoteFamily(r)===quoteFamily(q)).sort((a,b)=>(b.version||1)-(a.version||1));
 const orderFor=q=>state().orders.find(o=>(o.quoteFamilyId||o.quoteId)===quoteFamily(q));
 const pdfLink=docId=>docId?`<a class="button" href="/api/documents/${encodeURIComponent(docId)}/file${ctx.query()}" target="_blank" rel="noopener">PDF 보기</a><a class="button" href="/api/documents/${encodeURIComponent(docId)}/file${ctx.query()}&download=1" download>PDF 다운로드</a>`:'';
 const lookup=(kind,id)=>state()[kind].find(r=>r.id===id);
 let history=false,visibleQuoteFamilies=[];
 const selectedQuoteFamilies=new Set();
 function wide(title,body,submit,onSubmit) {modal(title,body,submit,onSubmit);dialog.classList.add('wide');}
 function itemTable(rows,internal=false) {const costs=internal&&state().ledgerVersion===2;return table(['품목 · 규격','수량','단위','단가','공급가액','부가세',...(costs?['예상 원가 / 마진']:[])],rows.map(r=>`<tr><td>${esc(r.name)}<small>${esc(r.spec||'')}</small></td><td class="number">${esc(r.quantity)}</td><td>${esc(r.unit||'')}</td><td class="number">${money(r.unitPrice)}</td><td class="number">${money(r.net)}</td><td class="number">${money(r.vat)}</td>${costs?`<td class="number">${costKnown(r)?money(r.estimatedCost??Math.round(r.costUnit*r.quantity)):'원가 미입력'}<small>${costKnown(r)?'마진 '+money(r.net-(r.estimatedCost??Math.round(r.costUnit*r.quantity))):'마진 산정 불가'}</small></td>`:''}</tr>`));}

 function totalBox(r) {return `<div class="workflow-totals"><span>공급가액 <strong>${money(r.net||0)}</strong></span><span>부가세 <strong>${money(r.vat||0)}</strong></span><span>합계 <strong>${money((r.net||0)+(r.vat||0))}</strong></span></div>`;}
 function quotesPage() {
  const latest=state().quotes.filter(q=>familyVersions(q)[0].id===q.id);
  const list=(history?state().quotes:latest).filter(q=>match(customer(q.accountId),q.title,q.number)).slice().reverse();
  const existingFamilies=new Set(state().quotes.map(quoteFamily));for(const id of [...selectedQuoteFamilies])if(!existingFamilies.has(id))selectedQuoteFamilies.delete(id);
  visibleQuoteFamilies=[...new Set(list.map(quoteFamily))];const chosen=[...selectedQuoteFamilies],allVisible=visibleQuoteFamilies.length&&visibleQuoteFamilies.every(id=>selectedQuoteFamilies.has(id));
  const bulkButton=(label,action,style='',disabled=false)=>`<button type="button" class="button ${style}" data-action="wf-${action}" ${disabled?'disabled':''}>${label}</button>`;
  return pageHead('견적 관리','품목을 입력하면 견적서까지. 수정한 견적은 새 버전으로 보관해요.',button('견적 작성','quote-new','','primary'))+
   `<div class="toolbar"><span class="panel-caption">${history?'전체 버전':'최신 버전'} ${list.length}건</span>${button(history?'최신 견적만 보기':'이전 버전 포함','history','','small')}${searchBox()}</div><div class="quote-bulk-toolbar" role="region" aria-label="선택한 견적 건 일괄 삭제"><span><strong>${chosen.length}건</strong> 선택</span>${bulkButton(allVisible?'현재 목록 선택 해제':'현재 목록 전체 선택','quote-select-all','',!visibleQuoteFamilies.length)}${bulkButton('선택한 견적 건 삭제','quote-family-bulk-delete','danger',!chosen.length)}</div><p class="panel-caption quote-bulk-help">선택한 견적 건은 같은 견적번호의 모든 버전이 함께 삭제됩니다. 연결된 주문과 생성 문서는 보존됩니다.</p><section class="panel">`+
   (list.length?table(['선택','고객사 / 견적명','견적번호 / 날짜','버전','합계 · VAT 포함','상태',''],list.map(q=>{const group=quoteFamily(q),selected=selectedQuoteFamilies.has(group);return `<tr class="${selected?'selected':''}"><td><label class="record-checkbox"><input type="checkbox" data-action="wf-quote-select" data-id="${esc(group)}" ${selected?'checked':''} aria-label="${esc(q.title)} 견적 건 선택"></label></td><td>${customer(q.accountId)}<small>${esc(q.title)}</small></td><td>${esc(q.number)}<small>${esc(q.date)}</small></td><td>v${q.version||1}</td><td class="number">${money(q.net+q.vat)}</td><td>${badge(orderFor(q)?'주문 연결':q.pdfDocumentId?'PDF 보관':q.status||'작성 완료','blue')}</td><td>${button('상세','quote-detail',q.id,'small')}</td></tr>`;})):empty('첫 견적을 작성해 보세요.'))+'</section>';
 }
 function editor(initial,pricing='sale',wholeQuantity=false) {
  let rows=structuredClone(initial?.length?initial:[{name:'',quantity:1,unit:'개',unitPrice:0,vatRate:10}]).map(r=>({...r,section:'',unit:r.unit||'개',vatRate:Number(r.vatRate??(r.vat===0?0:10)),costKnown:costKnown(r)}));
  let openCatalogRow=-1,catalogQuery='';
  const host=dialog.querySelector('[data-item-editor]');
  const input=(r,key,attrs='')=>`<input data-key="${key}" aria-label="${{name:'품목명',spec:'규격',quantity:'수량',unit:'단위',unitPrice:'단가',costUnit:'내부 예상 원가'}[key]}" value="${esc(key==='costUnit'&&!costKnown(r)?'':r[key]??'')}" ${attrs} ${r.productId&&['name','spec','unit','costUnit'].includes(key)?'readonly':''}>`;
  const productOptions=i=>{
   const q=catalogQuery.trim().toLowerCase();
   const matches=state().products.filter(p=>p.active!==false&&(!q||[p.code,p.name,p.spec].join(' ').toLowerCase().includes(q))).slice(0,15);
   return `<button type="button" class="catalog-manual" data-catalog-manual data-catalog-line="${i}"><strong>직접 입력</strong><span>등록되지 않은 품목을 직접 작성합니다.</span></button>${matches.map(p=>`<button type="button" data-catalog-id="${esc(p.id)}" data-catalog-line="${i}"><strong>${esc(p.code)} · ${esc(p.name)}</strong><span>${esc(p.spec)} · ${money(pricing==='purchase'?p.purchasePrice:p.salePrice)} · 재고 ${stockBalance(state(),p.id).quantity} ${esc(p.unit)}</span></button>`).join('')||'<p>일치하는 등록 품목이 없어요. 직접 입력을 선택해 주세요.</p>'}`;
  };
  function draw() {
   host.innerHTML=`<div class="editor-scroll"><table class="item-editor"><thead><tr><th>품목 / 규격</th><th>수량 / 단위</th><th>단가 · 원 / VAT</th><th>공급가액 / 부가세</th><th>행 관리</th></tr></thead><tbody>${rows.map((r,i)=>`<tr data-line="${i}"><td><div class="line-product-picker">${input(r,'name','placeholder="품목명" required maxlength="140"')}<button type="button" class="product-search-button" data-product-search data-catalog-line="${i}" aria-label="${i+1}번째 행 등록 품목 검색" aria-expanded="${openCatalogRow===i}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"></circle><path d="m16 16 4 4"></path></svg></button>${openCatalogRow===i?`<div class="line-catalog-results"><input data-line-catalog-search aria-label="등록 품목 검색어" placeholder="품목 코드·이름·규격 검색" value="${esc(catalogQuery)}" autocomplete="off"><div data-line-catalog-options>${productOptions(i)}</div></div>`:''}</div>${input(r,'spec','placeholder="규격 (선택)" maxlength="160"')}${state().ledgerVersion===2?`<label class="line-cost"><span>내부 예상 원가 / 개</span>${input(r,'costUnit','type="number" min="0" step="1"')}</label>`:''}</td><td>${input(r,'quantity',`type="number" min="${wholeQuantity?1:0.001}" max="1000000000" step="${wholeQuantity?1:0.001}" required`)}${input(r,'unit','placeholder="개 / 식 / m" maxlength="12"')}</td><td>${input(r,'unitPrice','type="number" min="0" max="10000000000" step="1" required')}<select data-key="vatRate" aria-label="부가세율"><option value="10" ${r.vatRate===10?'selected':''}>VAT 10%</option><option value="0" ${r.vatRate===0?'selected':''}>VAT 0%</option></select></td><td class="number" data-line-total></td><td><div class="row-tools"><button type="button" data-row="copy">복사</button><button type="button" data-row="remove">삭제</button><button type="button" data-row="up" aria-label="품목 위로">↑</button><button type="button" data-row="down" aria-label="품목 아래로">↓</button></div></td></tr>`).join('')}</tbody></table></div><div class="editor-bottom"><button type="button" class="button small" data-add-line>+ 품목 추가</button><span class="panel-caption">${wholeQuantity?'수량은 1개 단위 · ':''}원 단위 반올림 · VAT 0%는 적용 여부를 확인해 선택해 주세요.</span></div><div data-editor-totals></div>`;
   update();
  }
  function update() {
   let net=0,vat=0;
   rows.forEach((r,i)=>{const value=lineAmounts(r);net+=value.net;vat+=value.vat;host.querySelector(`[data-line="${i}"] [data-line-total]`).innerHTML=`${money(value.net)}<small>${money(value.vat)}</small>${state().ledgerVersion===2&&pricing==='sale'?`<small class="internal-cost">${costKnown(r)?'마진 '+money(value.net-Math.round((r.quantity||0)*(r.costUnit||0))):'마진 산정 불가 · 원가 미입력'}</small>`:''}`;});
   const unknown=rows.filter(r=>!costKnown(r)).length;
   const cost=rows.reduce((n,r)=>n+Math.round((r.quantity||0)*(r.costUnit||0)),0);
   host.querySelector('[data-editor-totals]').innerHTML=totalBox({net,vat})+(state().ledgerVersion===2?`<p class="internal-cost">내부 예상 원가 ${money(cost)}${unknown?' (입력된 품목만) · 마진 산정 불가 · 원가 미입력 '+unknown+'개':' · 예상 마진 '+money(net-cost)+' (VAT 제외)'}</p>`:'');
  }
  host.addEventListener('input',e=>{
   if(!e.target.matches('[data-line-catalog-search]'))return;
   catalogQuery=e.target.value;
   e.target.closest('.line-catalog-results').querySelector('[data-line-catalog-options]').innerHTML=productOptions(openCatalogRow);
  });
  function capture(){
   host.querySelectorAll('[data-line]').forEach(tr=>{
    const row=rows[Number(tr.dataset.line)];
    tr.querySelectorAll('[data-key]').forEach(el=>{const key=el.dataset.key;row[key]=['quantity','unitPrice','vatRate','costUnit'].includes(key)?Number(el.value):el.value;if(key==='costUnit'&&!row.productId)row.costKnown=el.value!=='';});
   });
  }
  const changed=e=>{if(!e.target.dataset.key)return;capture();update();};
  host.addEventListener('input',changed);
  host.addEventListener('change',changed);
  host.addEventListener('click',e=>{
   const search=e.target.closest('[data-product-search]'),manual=e.target.closest('[data-catalog-manual]'),choice=e.target.closest('[data-catalog-id]'),add=e.target.closest('[data-add-line]'),control=e.target.closest('[data-row]');
   if(!search&&!manual&&!choice&&!add&&!control)return;capture();
   if(search){const i=Number(search.dataset.catalogLine);openCatalogRow=openCatalogRow===i?-1:i;catalogQuery='';draw();if(openCatalogRow===i)host.querySelector(`[data-line="${i}"] [data-line-catalog-search]`)?.focus();return;}
   if(manual){const i=Number(manual.dataset.catalogLine);delete rows[i].productId;rows[i].section='';rows[i].costKnown=Number(rows[i].costUnit||0)>0;openCatalogRow=-1;catalogQuery='';draw();host.querySelector(`[data-line="${i}"] [data-key="name"]`)?.focus();return;}
   if(choice){const i=Number(choice.dataset.catalogLine),p=state().products.find(p=>p.id===choice.dataset.catalogId);if(!p)return;const bstock=stockBalance(state(),p.id),cost=bstock.quantity>0&&bstock.value>=0&&!provisionalStock(state(),p.id)?Math.round(bstock.value/bstock.quantity):p.purchasePrice;
    rows[i]={productId:p.id,section:'',name:p.name,spec:p.spec,unit:p.unit,quantity:rows[i].quantity||1,unitPrice:pricing==='purchase'?p.purchasePrice:p.salePrice,costUnit:cost,costKnown:(bstock.quantity>0&&bstock.value>=0&&!provisionalStock(state(),p.id))||p.purchasePrice>0,vatRate:Number(rows[i].vatRate??10)};openCatalogRow=-1;catalogQuery='';draw();return;
   }
   if(add){if(rows.length>=150)return toast('품목은 최대 150개까지 입력할 수 있어요.');rows.push({section:'',name:'',spec:'',quantity:1,unit:'개',unitPrice:0,vatRate:10});}
   else {const i=Number(control.closest('[data-line]').dataset.line),action=control.dataset.row;
    if(action==='copy'){if(rows.length>=150)return;rows.splice(i+1,0,{...rows[i]});}
    if(action==='remove'){if(rows.length===1)return toast('최소 한 개 품목이 필요해요.');rows.splice(i,1);}
    if(action==='up'&&i>0)[rows[i-1],rows[i]]=[rows[i],rows[i-1]];
    if(action==='down'&&i<rows.length-1)[rows[i+1],rows[i]]=[rows[i],rows[i+1]];
   } draw();
  });
  draw(); return ()=>{capture();update();return structuredClone(rows);};
 }
 function quoteForm(q={},mode='new',doc=null) {
  const revision=mode==='revise',update=mode==='update',copy=mode==='copy',editing=revision||update;
  const draft=ctx.draft('quote:'+mode+':'+(q.id||'new')+':'+(doc?.id||''));
  let readItems;
  wide(revision?'견적 수정 · 새 버전':update?'견적 수정 · 현재 버전 저장':copy?'견적 복사 · 새 견적':'견적 작성',
   `<div class="notice">${revision?'저장하면 새 버전이 생겨요. 이전 버전과 PDF, 연결된 주문은 그대로 보존됩니다.':update?'새 버전을 만들지 않고 현재 버전에 저장해요. 기존에 만든 PDF·엑셀 파일은 보존되며, 수정 후 새 파일을 다시 만들 수 있어요.':copy?'기존 품목을 가져와 별도 견적을 작성해요.':'품목을 입력하면 금액이 자동 계산돼요. 견적 저장과 PDF 생성은 매출에 반영되지 않아요.'}</div>`+
   `<div class="form-grid">${select('고객사','accountId',accounts(),q.accountId,editing?'required disabled':'required')}${field('견적명','title',q.title||'','text','required maxlength="160"')}${field('견적번호 · 비워두면 자동 생성','number',copy?'':q.number||'','text',editing?'readonly':'maxlength="160"')}${field('견적일','date',copy?today():q.date||today(),'date','required')}${field('견적 유효일','validUntil',copy?'':q.validUntil||'','date')}${field('참조 · 담당자','attention',q.attention||'','text','maxlength="160"')}${field('납기 · 납품 조건','deliveryTerms',q.deliveryTerms||'','text','maxlength="500"')}${field('결제 조건','paymentTerms',q.paymentTerms||'','text','maxlength="500"')}${revision?field('변경 내용','changeNote','','text','placeholder="예: 규격 변경, 품목 추가" maxlength="200"'):update?field('저장 메모','changeNote',q.changeNote||'','text','placeholder="예: 작성 중 저장" maxlength="200"'):''}</div><div class="section-gap" data-item-editor></div><div class="form-grid section-gap">${area('비고','notes',q.notes||'')}</div>`,update?'저장':'견적 저장',async f=>{
    await ctx.run('quote.save',{...Object.fromEntries(f),mode,sourceId:q.id,documentId:doc?.id,items:readItems()});
    draft.clear();location.hash='#quotes';toast(revision?'새 버전을 저장했어요.':update?'현재 버전에 저장했어요.':'견적을 저장했어요. 상세에서 PDF를 만들 수 있어요.');
   });
  if(editing){dialog.querySelector('[name="accountId"]').setAttribute('disabled','');dialog.querySelector('[data-lookup-input]')?.setAttribute('disabled','');dialog.querySelector('[data-lookup-clear]')?.setAttribute('disabled','');}
  // A disabled select is not included in FormData; retain its frozen customer explicitly.
  if(editing){const input=document.createElement('input');input.type='hidden';input.name='accountId';input.value=q.accountId;dialog.querySelector('form').append(input);}
  readItems=editor(draft.items||q.items,'sale',true);draft.bind(readItems);
 }
 async function deleteQuote(id) {
  const q=lookup('quotes',id);if(!q)return;
  await ctx.run('quote.delete',{id});dialog.close();location.hash='#quotes';toast(`견적 v${q.version||1}을 삭제했어요.`);
 }
 function deleteQuoteFamilies(ids) {
  const groups=[...new Set(ids)].filter(id=>state().quotes.some(q=>quoteFamily(q)===id));if(!groups.length)return;
  const versions=state().quotes.filter(q=>groups.includes(quoteFamily(q))),examples=groups.map(id=>versions.find(q=>quoteFamily(q)===id)).filter(Boolean).slice(0,5);
  modal(groups.length===1?'이 견적 건 삭제 · 모든 버전':`선택한 견적 ${groups.length}건 삭제`,`<div class="notice amber"><strong>정말로 삭제하시겠습니까?</strong><br>선택한 견적 ${groups.length}건에 포함된 모든 버전 ${versions.length}개를 삭제합니다. 연결된 주문은 그대로 유지하고 견적 연결만 해제합니다. 이미 생성한 PDF·엑셀 파일도 문서함에 보존됩니다. 이 작업은 되돌릴 수 없습니다.</div><p>${examples.map(q=>`<strong>${esc(q.number)}</strong> · ${esc(q.title)}`).join('<br>')}${groups.length>examples.length?`<br>외 ${groups.length-examples.length}건`:''}</p>`,groups.length===1?'이 견적 건 삭제':'선택한 견적 건 삭제',async()=>{const r=await ctx.run('quote.family.delete',{ids:groups});for(const id of groups)selectedQuoteFamilies.delete(id);location.hash='#quotes';toast(`견적 ${r.familyCount||groups.length}건과 버전 ${r.versionCount||versions.length}개를 삭제했어요.`);});
 }
 function quoteDetail(id) {
  const q=lookup('quotes',id);if(!q)return;
  const versions=familyVersions(q),latest=versions[0].id===q.id,order=orderFor(q),quantityReview=quoteNeedsQuantityReview(q);
  wide(`${esc(q.number)} · v${q.version||1}`,
   `<div class="workflow-intro"><div><h3>${esc(q.title)}</h3><p>${esc(q.customer?.name||accountName(state(),q.accountId))} · ${esc(q.date)}</p></div>${badge(latest?'최신 버전':'이전 버전',latest?'blue':'muted')}</div>`+
   (quantityReview?'<div class="notice amber">차감 품목을 원본대로 보존한 과거 견적입니다. 실제 납품 품목·수량을 확인해 새 버전으로 작성한 뒤 주문으로 전환하세요.</div>':'')+
   itemTable(q.items||[],true)+totalBox(q)+(state().ledgerVersion===2?`<p class="internal-cost">내부 예상 원가 ${money(margin(state(),q).estimated)} · 예상 마진 ${margin(state(),q).unknown?'마진 산정 불가':money(margin(state(),q).gross)}${margin(state(),q).unknown?' · 원가 미입력 '+margin(state(),q).unknown+'개 품목':''}</p>`:'')+`<p class="panel-caption section-gap">${esc(q.deliveryTerms||'납품 조건 미설정')} · ${esc(q.paymentTerms||'결제 조건 미설정')}</p>${q.notes?`<p class="workflow-notes">${esc(q.notes)}</p>`:''}`+
   `<div class="form-actions wrap">${button('현재 버전 수정','quote-update',id)}${latest?button('수정 · 새 버전','quote-edit',id):''}${button('새 견적으로 복사','quote-copy',id)}${q.workflowVersion===2?(button('견적 PDF 생성','quote-pdf',id,'primary')+(q.pdfDocumentId?pdfLink(q.pdfDocumentId):'')):'<span class="panel-caption">기존 견적은 품목 수정 후 PDF 생성 가능</span>'}${state().ledgerVersion===2?`<button class="button" data-action="erp-quote-xlsx" data-id="${esc(id)}">엑셀 다운로드</button>`:''}${order?`<a class="button" href="#order/${esc(order.id)}" target="_blank" rel="noopener">연결 주문 새 탭에서 보기</a>`:latest&&!quantityReview?button('주문으로 전환','convert',id,'primary'):''}${button('이 견적 건 삭제 · 모든 버전','quote-family-delete',quoteFamily(q),'danger')}</div><div class="section-gap"><h3>버전 이력</h3>${table(['버전','저장일 / 변경 내용','금액','관리'],versions.map(v=>`<tr><td>v${v.version||1}</td><td>${esc((v.updatedAt||v.createdAt||v.date).slice(0,10))}<small>${esc(v.changeNote||'최초 견적')}</small></td><td class="number">${money(v.net+v.vat)}</td><td><div class="form-actions wrap">${button('보기','quote-detail',v.id,'small')}${button('수정','quote-update',v.id,'small')}${button('삭제','quote-delete',v.id,'small')}</div></td></tr>`))}</div>`);
 }
 function convert(id) {
  const q=lookup('quotes',id);
  modal('견적을 주문으로 연결',`<div class="notice">${esc(q.number)} v${q.version||1}의 품목·금액을 주문에 복사해요. 이후 견적을 수정해도 이 주문의 내용은 유지됩니다.</div><div class="form-grid">${select('주문 구분','orderType',[['BOX','박스 유통'],['DIS','분전반 제작']],'DIS')}${field('주문일','date',today(),'date','required')}${field('납품 예정일','dueDate','','date')}${area('주문 메모','notes')}</div>`,'주문 생성',async f=>{await ctx.run('quote.convert',{quoteId:id,...Object.fromEntries(f)});location.hash='#orders';toast('고유 번호가 있는 주문을 생성했어요. 상세에서 납품을 기록하세요.');});
 }
 function ordersPage() {
  const rows=state().orders.filter(o=>match(customer(o.accountId),o.title,o.number,o.id)).slice().reverse();
  return pageHead('주문·납품','주문별 품목과 실제 납품을 연결해요.',button('직접 주문 등록','order-new','','primary'))+`<div class="toolbar"><span class="panel-caption">${rows.length}건</span>${searchBox()}</div><section class="panel">`+
   (rows.length?table(['고객사 / 주문명','주문번호 / 날짜','납품 예정','주문금액','진행','관리'],rows.map(o=>`<tr><td>${customer(o.accountId)}<small>${esc(o.title)}</small></td><td>${esc(o.number||o.id)}<small>${esc(o.date)}</small></td><td>${esc(o.dueDate||'미정')}</td><td class="number">${money(o.amount)}</td><td>${badge(o.status,['납품 완료','취소'].includes(o.status)?'muted':o.dueDate&&o.dueDate<today()?'danger':o.status==='주문 접수'?'blue':'amber')}</td><td><div class="form-actions wrap">${button('보기','order-detail',o.id,'small')}${button('수정','order-edit',o.id,'small')}${button('삭제','order-delete',o.id,'small danger')}</div></td></tr>`)):empty('견적을 주문으로 전환하거나 전화 주문을 직접 등록해 보세요.'))+'</section>';
 }
 function orderForm(id) {
  const o=lookup('orders',id)||{};let readItems;
  wide(o.id?'주문 수정':'직접 주문 등록',`${o.id&&!o.items?.length?'<div class="notice">이전 주문은 합계만 저장되어 있어요. 품목을 입력하면 주문금액이 품목 합계로 바뀝니다. 기존 매출·입금 금액은 변경되지 않아요.</div>':''}${o.id?'<div class="notice">주문명·일정·메모는 언제든 수정할 수 있습니다. 이미 견적·전표·납품에 연결된 주문은 금액 불일치를 막기 위해 품목과 고객사 변경이 제한될 수 있습니다.</div>':''}<div class="form-grid">${select('고객사','accountId',accounts(),o.accountId,'required')}${select('주문 구분','orderType',[['BOX','박스 유통'],['DIS','분전반 제작']],o.orderType||'DIS')}${field('주문명','title',o.title||'','text','required maxlength="160"')}${field('주문일','date',o.date||today(),'date','required')}${field('납품 예정일','dueDate',o.dueDate||'','date')}</div><div class="section-gap" data-item-editor></div><div class="form-grid section-gap">${area('주문 메모','notes',o.notes||'')}</div>`,'주문 저장',async f=>{await ctx.run('order.save',{...Object.fromEntries(f),id:o.id,items:readItems()});location.hash='#orders';toast('주문을 저장했어요.');});
  readItems=editor(o.items);
 }
 function orderDetail(id) {
  if(state().ledgerVersion===2){location.hash='#order/'+id;return;}
  const o=lookup('orders',id);if(!o)return;
  const rows=remainingItems(state(),o),deliveries=(state().deliveries||[]).filter(d=>d.orderId===id),hasRemaining=rows.some(r=>r.remaining>0);
  wide(`${esc(o.number||o.id)} · 주문 상세`,
   `<div class="workflow-intro"><div><h3>${esc(o.title)}</h3><p>${customer(o.accountId)} · 주문 ${esc(o.date)} · 납품 예정 ${esc(o.dueDate||'미정')}</p></div>${badge(o.status,['납품 완료','취소'].includes(o.status)?'muted':o.dueDate&&o.dueDate<today()?'danger':o.status==='주문 접수'?'blue':'amber')}</div>`+
   (rows.length?table(['품목 / 규격','주문 수량','납품 수량','남은 수량','공급가액'],rows.map(r=>`<tr><td>${esc(r.name)}<small>${esc(r.spec||'')}</small></td><td class="number">${r.quantity} ${esc(r.unit||'')}</td><td class="number">${r.delivered}</td><td class="number"><strong>${r.remaining}</strong></td><td class="number">${money(r.net)}</td></tr>`)):'<div class="notice">상세 품목이 없는 기존 주문입니다. 품목을 입력한 뒤 납품을 기록할 수 있어요.</div>')+
   `<div class="workflow-totals"><span>주문금액 · VAT 포함 <strong>${money(o.amount)}</strong></span><span>연결된 원가 <strong>${money(orderCost(state(),id))}</strong></span></div><p class="panel-caption">주문 원가는 연결한 비용만 포함합니다. 전체 비용·최종 이익과 다를 수 있어요.</p>${o.notes?`<p class="workflow-notes">${esc(o.notes)}</p>`:''}<div class="form-actions wrap">${o.quoteId?button(`원본 견적 v${o.quoteVersion||1}`,'quote-detail',o.quoteId):''}${button('주문 수정','order-edit',id)}${button('일정·메모 수정','schedule',id)}${button('주문 삭제','order-delete',id,'danger')}<a class="button" href="#statements">명세서·발급 기록</a>${hasRemaining?button('납품 기록','delivery-new',id,'primary'):''}</div><div class="section-gap"><h3>납품 이력 · 거래명세서</h3>${deliveries.length?table(['납품일 / 번호','금액','상태',''],deliveries.map(d=>`<tr><td>${esc(d.date)}<small>${esc(d.number)}</small></td><td class="number">${money(d.amount)}</td><td>${badge(d.status==='void'?'취소':'납품 완료',d.status==='void'?'muted':'blue')}</td><td>${button('상세 · PDF','delivery-detail',d.id,'small')}</td></tr>`)):empty('아직 납품 기록이 없어요.')}</div><p class="bottom-note">거래명세서 PDF 생성 후 매출 확정을 눌러 납품일 기준 매출에 반영하세요. 홈택스 발급 표시와 고객 입금은 별도로 관리합니다.</p>`);
 }
 function deleteOrder(id) {
  const o=lookup('orders',id);if(!o)return;
  const voucherIds=new Set(state().vouchers.filter(v=>v.orderId===id).map(v=>v.id));
  const fulfillmentCount=state().fulfillments.filter(f=>f.orderId===id||voucherIds.has(f.voucherId)).length+(state().deliveries||[]).filter(d=>d.orderId===id).length;
  const paymentCount=state().payments.filter(p=>p.orderId===id||(p.allocations||[]).some(a=>voucherIds.has(a.voucherId))).length+(state().receipts||[]).filter(r=>r.orderId===id).length;
  const summary=[voucherIds.size&&`전표 ${voucherIds.size}건`,fulfillmentCount&&`납품 이력 ${fulfillmentCount}건`,paymentCount&&`수금·지급 ${paymentCount}건`].filter(Boolean).join(' · ')||'연결 기록 없음';
  modal('주문과 연결 기록 일괄 삭제',`<div class="notice amber"><strong>정말로 삭제하시겠습니까?</strong><br><strong>함께 영구 삭제: ${summary}</strong><br>재고 이동과 회계 장부도 자동으로 다시 계산됩니다. 원본 견적은 남고, 사용자가 올린 첨부파일은 연결만 해제하여 보존합니다. 발급 요청 전 전자세금계산서 초안과 모의 기록은 함께 정리하며, 실제 발급·수집 또는 결과 확인이 필요한 기록만 삭제가 중단됩니다.</div><p><strong>${esc(o.title)}</strong><br>${esc(o.number||o.id)} · ${customer(o.accountId)}</p>`,'연결 기록 포함 삭제',async()=>{const r=await ctx.run('order.delete',{id});location.hash='#orders';const d=r.deleted||{};toast(`주문과 연결 기록을 삭제했어요. 전표 ${d.vouchers||0}건 · 납품 ${d.fulfillments||0}건 · 수금·지급 ${d.payments||0}건${d.taxLinks?` · 미발행·모의 세금계산서 ${d.taxLinks}건`:''}`);});
 }
 function schedule(id) {
  const o=lookup('orders',id);modal('납품 일정·메모',`<div class="form-grid">${field('납품 예정일','dueDate',o.dueDate||'','date')}${area('주문 메모','notes',o.notes||'')}</div>`,'저장',async f=>{await ctx.run('order.schedule',{id,...Object.fromEntries(f)});toast('일정을 저장했어요.');});
 }
 function deliveryForm(id) {
  const o=lookup('orders',id),rows=remainingItems(state(),o).filter(r=>r.remaining>0);
  wide('납품 기록',`<div class="notice">${esc(o.number)} · ${customer(o.accountId)}<br>이번에 납품한 수량만 입력하세요. 다음에 납품할 품목은 0으로 두세요.</div><div class="form-grid">${field('납품일','date',today(),'date','required')}${field('참조 · 담당자','attention',o.quoteSnapshot?.attention||'','text','maxlength="160"')}</div><div class="section-gap">${table(['품목 / 규격','남은 수량','이번 납품 수량'],rows.map((r,i)=>`<tr><td>${esc(r.name)}<small>${esc(r.spec||'')}</small></td><td class="number">${r.remaining} ${esc(r.unit||'')}</td><td><input aria-label="${esc(r.name)} 납품 수량" name="qty-${i}" type="number" min="0" max="${r.remaining}" step="0.001" value="${r.remaining}" required></td></tr>`))}</div><div class="form-grid section-gap">${area('납품 비고','notes')}</div>`,'납품 저장',async f=>{await ctx.run('delivery.create',{orderId:id,date:f.get('date'),attention:f.get('attention'),notes:f.get('notes'),items:rows.map((r,i)=>({orderLineId:r.id,quantity:Number(f.get('qty-'+i))}))});toast('납품을 저장했어요. 주문 상세에서 거래명세서 PDF를 만들 수 있어요.');});
 }
 function deliveryDetail(id) {
  const d=lookup('deliveries',id);if(!d)return;const sale=statementSale(state(),id);
  wide(`${esc(d.number)} · 거래명세서`,`${d.status==='void'?`<div class="notice amber">취소된 납품입니다. ${esc(d.voidReason)}<br>보관된 PDF는 취소 전 원본이므로 고객에게 사용하지 마세요.</div>`:''}<div class="workflow-intro"><div><h3>${esc(d.title)}</h3><p>${esc(d.customer.name)} · 납품일 ${esc(d.date)}</p></div></div>${itemTable(d.items)}${totalBox(d)}${d.notes?`<p class="workflow-notes">${esc(d.notes)}</p>`:''}<div class="form-actions wrap">${button('주문 보기','order-detail',d.orderId)}${d.pdfDocumentId?pdfLink(d.pdfDocumentId):d.status!=='void'?button('거래명세서 PDF 생성','delivery-pdf',id,'primary'):''}${sale?`<button class="button primary" data-action="stmt-sale" data-id="${esc(sale.id)}">매출·발급 기록</button>`:d.status!=='void'&&d.pdfDocumentId?`<button class="button primary" data-action="stmt-confirm" data-id="${esc(id)}">매출 확정</button>`:''}${d.status!=='void'&&!sale?button('납품 기록 취소','delivery-void',id):''}</div>`);
 }
 function voidDelivery(id) {
  modal('납품 기록 취소',`<div class="notice">같은 주문의 최근 납품부터 취소할 수 있어요. 원본 납품과 PDF는 이력으로 보존하고, 납품 수량을 되돌립니다. 매출·입금 기록은 별도로 확인해 주세요.</div><div class="form-grid">${field('취소 사유','reason','','text','required maxlength="200"')}</div>`,'납품 취소',async f=>{await ctx.run('delivery.void',{id,reason:f.get('reason')});toast('납품을 취소했어요. 주문에서 수정된 납품을 다시 등록할 수 있어요.');});
 }
 async function generate(kind,id) {
  const element=dialog.querySelector(`[data-action="wf-${kind}-pdf"]`);if(element){element.disabled=true;element.textContent='PDF 만드는 중…';}
  try {await ctx.run('document.generate',{kind,id});kind==='quote'?quoteDetail(id):deliveryDetail(id);toast('PDF를 비공개 문서함에 보관했어요.');}
  finally {if(element){element.disabled=false;element.textContent='PDF 생성';}}
 }
 function companyForm() {
  const c=state().settings;
  modal('회사 정보 · 문서 표시',`<div class="notice">새로 생성하는 견적서·거래명세서와 빈 양식에 적용됩니다. 기존에 보관한 파일과 거래 원본은 그대로 유지됩니다. 그로스 일렉트릭 견적서·거래명세서에는 등록번호와 대표자가 일치할 때 회사 도장이 표시됩니다.</div><div class="form-grid">${field('회사명','name',c.name,'text','required maxlength="160"')}${field('사업자등록번호','businessNumber',c.businessNumber||'')}${field('대표자','representative',c.representative||'')}${field('주소','address',c.address||'')}${field('업태','businessType',c.businessType||'')}${field('종목','businessItem',c.businessItem||'')}${field('전화','phone',c.phone||'')}${field('팩스','fax',c.fax||'')}${field('이메일','email',c.email||'','email')}${field('은행','bank',c.bank||'')}${field('입금 계좌번호','bankAccount',c.bankAccount||'')}${field('예금주','bankHolder',c.bankHolder||'')}</div>`,'저장',async f=>ctx.commit(s=>Object.assign(s.settings,Object.fromEntries(f))));
 }
 async function action(name,id) {
  const handlers={history:()=>{history=!history;selectedQuoteFamilies.clear();ctx.render();},'quote-new':()=>quoteForm(), 'quote-detail':()=>quoteDetail(id),'quote-update':()=>quoteForm(lookup('quotes',id),'update'),'quote-edit':()=>quoteForm(lookup('quotes',id),'revise'),'quote-delete':()=>deleteQuote(id),'quote-family-delete':()=>deleteQuoteFamilies([id]),'quote-select':()=>{selectedQuoteFamilies.has(id)?selectedQuoteFamilies.delete(id):selectedQuoteFamilies.add(id);ctx.render();},'quote-select-all':()=>{const allSelected=visibleQuoteFamilies.length&&visibleQuoteFamilies.every(id=>selectedQuoteFamilies.has(id));for(const group of visibleQuoteFamilies)allSelected?selectedQuoteFamilies.delete(group):selectedQuoteFamilies.add(group);ctx.render();},'quote-family-bulk-delete':()=>deleteQuoteFamilies([...selectedQuoteFamilies]),'quote-copy':()=>quoteForm(lookup('quotes',id),'copy'),'quote-pdf':()=>generate('quote',id),convert:()=>convert(id),'order-new':()=>orderForm(),'order-edit':()=>orderForm(id),'order-delete':()=>deleteOrder(id),'order-detail':()=>orderDetail(id),schedule:()=>schedule(id),'delivery-new':()=>deliveryForm(id),'delivery-detail':()=>deliveryDetail(id),'delivery-pdf':()=>generate('delivery',id),'delivery-void':()=>voidDelivery(id)};
  if(handlers[name])await handlers[name]();
 }
 return {editor,itemTable,quotesPage,ordersPage,quoteForm,quoteDetail,orderForm,orderDetail,convert,companyForm,action};
}
