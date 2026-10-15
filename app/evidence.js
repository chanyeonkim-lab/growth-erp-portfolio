// Accountant review metadata never changes operational totals or customer files.
export const evidenceGroups=[
 ['증빙 종류와 상태',[
  ['evidenceType','증빙 종류',['전자세금계산서','종이세금계산서','전자계산서','종이계산서','카드전표','현금영수증','간이영수증','기타']],
  ['evidenceStatus','확인 상태',['미수취','확인 대기','수동 확인','수정·취소']],
  ['taxCategory','세무 과세 구분',['과세','영세율','면세','과세 대상 외']],
  ['externalId','외부 증빙 식별번호'],['provider','자료 출처'],
  ['entityType','거래처 유형',['개인사업자','법인','기타']],['taxpayerType','거래처 과세유형',['일반과세','간이과세','면세','확인 필요']]
 ]],
 ['일자와 귀속',[
  ['supplyDate','증빙상 공급일','date'],['accountingDate','세무사 검토용 귀속일','date'],
  ['writtenDate','세금계산서 작성일','date'],['issuedDate','발급일','date'],['receivedDate','수취일','date'],
  ['transmittedDate','국세청 전송일','date'],['taxPeriod','신고 귀속월','month']
 ]],
 ['원본 금액과 공제 검토',[
  ['sourceNet','원본 공급가액','number'],['sourceVat','원본 부가세','number'],['sourceGross','원본 합계','number'],
  ['roundingAdjustment','원본 단수 조정액','number'],
  ['deductionStatus','공제 검토',['미검토','공제','불공제','일부 공제']],
  ['deductibleVat','공제 가능 세액','number'],['nondeductibleVat','불공제 세액','number'],
  ['businessUseRatio','업무 사용 비율 · %','number'],['nondeductionReason','불공제 사유']
 ]],
 ['수정 증빙과 검토 메모',[
  ['originalVoucherId','원전표 번호 또는 ID'],['originalApprovalNumber','당초 승인번호'],
  ['correctionReason','수정 사유'],['correctionDate','수정 사유 발생일','date'],
  ['taxAccountCode','세무사 계정과목'],['costAllocationMemo','주문별 원가 배분 참고','textarea'],
  ['accountantMemo','세무사 전달 메모','textarea']
 ]],
 ['자금 증빙 참고',[
  ['paymentMethod','결제수단',['계좌이체','카드','현금','기타']],['bankAccountLabel','계좌 식별 · 예: 국민 사업계좌'],
  ['bankTransactionId','통장 거래번호'],['cashflowType','입출금 용도',['사업 거래','개인 인출','사업주 입금','차입금','대여금','세금 납부','기타']]
 ]]
];

export function createEvidenceUI(ctx,state,run,button){
 const {esc,field,select,area,modal,dialog,money}=ctx;
 const record=id=>state().vouchers.find(v=>v.id===id);
 function panel(v){
  const m=v.taxEvidence||{}, filled=evidenceGroups.flatMap(g=>g[1]).filter(([key])=>m[key]!==undefined&&m[key]!=='');
  const mismatch=m.sourceGross!==undefined&&m.sourceGross!==v.amount;
  const summary=filled.length||v.approvalNumber?`${filled.length}개 항목${v.approvalNumber?' · 승인번호 입력됨':''}`:'미입력';
  return `<details class="panel tax-evidence evidence-summary section-gap" data-evidence-section><summary><strong>증빙·세무 정보</strong><span>${esc(summary)} · 선택 사항</span>${mismatch?'<span class="evidence-mismatch">금액 차이 확인</span>':''}<span class="disclosure-label" aria-hidden="true"></span></summary><div class="evidence-body"><div class="head-actions">${v.status==='confirmed'?button('국세청 승인번호','approval',v.id):''}${button('증빙 정보 편집','evidence',v.id)}</div><p class="panel-caption">세무사에게 전달할 참고 정보입니다. 이곳의 금액·일자는 장부 금액과 고객용 PDF·엑셀을 변경하지 않습니다. 승인번호는 선택 메모이며 국세청 발급 기능이 아닙니다.</p>${mismatch?`<div class="notice amber">원본 증빙 합계 ${money(m.sourceGross)}와 전표 합계 ${money(v.amount)}가 다릅니다. 원본과 전표를 확인해 주세요.</div>`:''}<div class="evidence-values"><div><span>국세청 승인번호</span><strong>${esc(v.approvalNumber||'미입력')}</strong></div>${filled.map(([key,label,type])=>`<div><span>${esc(label)}</span><strong>${esc(type==='number'&&key!=='businessUseRatio'?money(m[key]):m[key])}</strong></div>`).join('')}</div></div></details>`;

 }
 function form(id){
  const v=record(id),m=v.taxEvidence||{};
  modal('세무사 증빙 정보 · 선택 입력',`<div class="tax-evidence tax-intro"><span class="tax-label">업무 수치와 별도 보관</span><p>필요한 항목만 작성하세요. 미입력 상태도 저장할 수 있습니다. 실제 납품일과 장부 금액은 해당 업무 기능에서 기록합니다.</p></div>${field('국세청 승인번호 · 선택','approvalNumber',v.approvalNumber||'','text','maxlength="100"')}${evidenceGroups.map(([title,fields])=>`<section class="tax-evidence evidence-group"><h3>${title}</h3><div class="form-grid">${fields.map(([key,label,type])=>Array.isArray(type)?select(label,'tax_'+key,[['','미입력'],...type],m[key]||''):type==='textarea'?area(label,'tax_'+key,m[key]||''):field(label,'tax_'+key,m[key]??'',type||'text',type==='number'?(key==='businessUseRatio'?'min="0" max="100" step="0.01"':'step="1"'):'')).join('')}</div></section>`).join('')}`,'증빙 정보 저장',async f=>{
   const taxEvidence={};
   for(const [,fields] of evidenceGroups)for(const [key,,type] of fields){const value=f.get('tax_'+key);if(value!==''&&value!==null)taxEvidence[key]=type==='number'?Number(value):value;}
   await run('voucher.evidence',{id,taxEvidence,approvalNumber:f.get('approvalNumber')});
  });dialog.classList.add('wide');
 }
 return {panel,form};
}
