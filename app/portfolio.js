import {today,money,accountName} from './domain.js';
import {metrics,tasks,remaining,stockBalance} from './erp-domain.js';
import {resetDemo,repository} from './demo-store.js';

const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const arrow='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>';
const compact=n=>n>=100000000?(n/100000000).toFixed(2)+'억':(n/10000).toLocaleString('ko-KR',{maximumFractionDigits:0})+'만';
const months=()=>Array.from({length:6},(_,i)=>{const d=new Date(today().slice(0,7)+'-01T12:00:00');d.setMonth(d.getMonth()-5+i);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0');});
export function demoBar(){return `<div class="demo-bar"><span><i></i> PORTFOLIO DEMO <b>가상 데이터로 자유롭게 체험하세요</b></span><div><button data-demo-tour>데모 투어 ${arrow}</button><button data-demo-reset aria-label="데모 데이터 초기화">↺ 초기화</button><a href="${repository}" target="_blank" rel="noopener" aria-label="GitHub 소스 보기">GitHub ↗</a></div></div>`;}
export function portfolioDashboard(s){
 const month=today().slice(0,7),m=metrics(s,month),previous=metrics(s,months()[4]);
 const change=previous.sales?Math.round((m.sales-previous.sales)/previous.sales*100):0;
 const ts=tasks(s),due=ts.filter(t=>t.dueDate<=today());
 const ar=s.vouchers.filter(v=>v.kind==='sale'&&v.status==='confirmed').reduce((n,v)=>n+remaining(s,v),0);
 const open=s.orders.filter(o=>!['납품 완료','취소'].includes(o.status));
 const activeQuotes=s.quotes.filter(q=>!s.orders.some(o=>o.quoteId===q.id));
 const low=s.products.filter(p=>p.trackStock&&stockBalance(s,p.id).quantity<p.minStock);
 const series=months().map(month=>({month,...metrics(s,month)}));
 const max=Math.max(...series.map(x=>Math.max(x.sales,x.profit)),1),W=680,H=180;
 const coords=(key)=>series.map((v,i)=>[40+i*120,H-12-v[key]/max*(H-25)]);
 const line=points=>points.map(([x,y],i)=>(i?'L':'M')+x+','+y).join(' ');
 const sales=coords('sales'),profit=coords('profit');
 const revenueChart=`<svg class="portfolio-chart" viewBox="0 0 680 225" role="img" aria-label="최근 6개월 확정 매출과 공급 완료 기준 예상 순이익 추이"><defs><linearGradient id="chart-area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#568374" stop-opacity=".18"/><stop offset="1" stop-color="#568374" stop-opacity="0"/></linearGradient></defs>${[0,.25,.5,.75,1].map(v=>`<g><line x1="40" x2="640" y1="${H-12-v*(H-25)}" y2="${H-12-v*(H-25)}" stroke="#e9eeeb" stroke-dasharray="3 5"/><text x="0" y="${H-8-v*(H-25)}">${compact(max*v)}</text></g>`).join('')}<path d="${line(sales)} L640,${H-12} L40,${H-12} Z" fill="url(#chart-area)"/><path d="${line(sales)}" fill="none" stroke="#306a57" stroke-width="3" stroke-linejoin="round"/><path d="${line(profit)}" fill="none" stroke="#aca579" stroke-width="2" stroke-dasharray="5 5"/>${sales.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="4" fill="#fff" stroke="#306a57" stroke-width="2"><title>${series[i].month} · 확정 매출 ${money(series[i].sales)} · 예상 순이익 ${money(series[i].profit)}</title></circle><text x="${x}" y="205" text-anchor="middle">${Number(series[i].month.slice(-2))}월</text>`).join('')}</svg>`;
 const currentProjects=open.slice().sort((a,b)=>b.date.localeCompare(a.date)).slice(0,4);
 return `<div class="portfolio-intro"><div><div class="eyebrow">YOUR BUSINESS, IN FOCUS</div><h1>사업의 흐름을, 선명하게<span>.</span></h1><p>견적부터 수금까지. 흩어진 업무가 하나의 흐름이 됩니다.</p></div><span class="portfolio-date">${Number(today().slice(5,7))}월 ${Number(today().slice(8))}일 <i></i> 오늘의 워크스페이스</span></div>
 <section class="portfolio-hero"><div class="hero-copy"><div class="eyebrow"><span class="status-light"></span> GROWTH AT A GLANCE</div><h2>작은 비즈니스의<br>다음 성장을 설계합니다.</h2><p>숫자는 명확하게, 다음 할 일은 자연스럽게.<br>그로스 일렉트릭의 오늘을 만나보세요.</p><button class="hero-button" data-demo-tour>3분 데모 투어 ${arrow}</button></div><div class="hero-orbit" aria-hidden="true"><div class="orbit-ring r1"></div><div class="orbit-ring r2"></div><div class="orbit-ring r3"></div><div class="orbit-center"><svg viewBox="0 0 64 64"><path d="M12 49V34h12V24h12V13h16v36Z" fill="currentColor"/></svg></div><span class="orbit-label l1">견적 · 주문</span><span class="orbit-label l2">납품 · 재고</span><span class="orbit-label l3">전표 · 수금</span><span class="orbit-dot d1"></span><span class="orbit-dot d2"></span></div><div class="hero-note"><span>BUILT FOR THE EVERYDAY</span><strong>하나의 연결된 업무 경험</strong></div></section>
 <div class="portfolio-kpis">${[
 ['확정 매출',m.sales,`${change>=0?'↗ +':'↘ '}${change}% 전월 대비`,'green', 'VAT 포함 · 이달'],
 ['예상 순이익',m.profit,'실제 공급 완료 기준','', '원가·경비·세금 추정 반영'],
 ['받을 돈',ar,'미수금 확인 →','', '확정 매출 · 미결제 잔액'],
 ['진행 중인 주문',open.length,`오늘까지 할 일 ${due.length}건`,'', '납품 완료·취소 제외']
 ].map(([label,value,foot,tone,note],i)=>`<a class="portfolio-kpi" href="#${['analysis','analysis','payments','orders'][i]}"><div class="kpi-label">${label}<span>↗</span></div><strong>${i===3?value+'<small>건</small>':compact(value)+'<small>원</small>'}</strong><p>${note}</p><div class="kpi-foot ${tone}">${foot}</div></a>`).join('')}</div>
 <div class="portfolio-middle"><section class="portfolio-panel"><div class="portfolio-panel-head"><div><span class="eyebrow">PERFORMANCE</span><h2>성장의 궤적</h2></div><a href="#analysis">매출 분석 ${arrow}</a></div><div class="portfolio-chart-legend"><span><i></i>확정 매출 · VAT 포함</span><span><i></i>예상 순이익 · 공급 기준</span><small>최근 6개월 · 단위: 원</small></div>${revenueChart}</section>
 <section class="portfolio-panel priorities"><div class="portfolio-panel-head"><div><span class="eyebrow">UP NEXT</span><h2>지금, 챙겨야 할 일 <em>${due.length}</em></h2></div><a href="#tasks" aria-label="전체 할 일 보기">${arrow}</a></div><div class="priority-list">${ts.slice(0,4).map((t,i)=>`<a href="${esc(t.href)}"><span class="priority-number">0${i+1}</span><span><strong>${esc(t.title)}</strong><small>${esc(t.category)} · ${esc(accountName(s,t.accountId))}</small></span><b class="priority-due ${t.tone}">${t.dueLabel}</b></a>`).join('')||'<p>오늘의 할 일을 모두 마쳤어요.</p>'}</div><a class="priority-bottom" href="#tasks">나의 업무 전체 보기 ${arrow}</a></section></div>
 <section class="portfolio-panel projects"><div class="portfolio-panel-head"><div><span class="eyebrow">CONNECTED WORKFLOW</span><h2>다음 단계로 이어지는 프로젝트</h2></div><a href="#orders">주문·납품 보기 ${arrow}</a></div><div class="pipeline-strip">${[['제안 중인 견적',activeQuotes.length,'#quotes'],['진행 중인 주문',open.length,'#orders'],['확인할 미수금',s.vouchers.filter(v=>v.kind==='sale'&&v.status==='confirmed'&&remaining(s,v)>0).length,'#payments'],['재고 보충 품목',low.length,'#products']].map(([label,n,href],i)=>`<a href="${href}"><span>0${i+1}</span><strong>${label}</strong><b>${n}</b>${i<3?arrow:''}</a>`).join('')}</div><div class="project-cards">${currentProjects.map((o,i)=>{const v=s.vouchers.find(v=>v.orderId===o.id&&v.status!=='void');return `<a class="project-card" href="#order/${esc(o.id)}"><div class="project-card-top"><span class="project-initial c${i}">${esc(accountName(s,o.accountId).slice(0,1))}</span><span class="project-status">${esc(o.status)}</span></div><h3>${esc(o.title)}</h3><p>${esc(accountName(s,o.accountId))}</p><strong>${money(o.amount)}</strong><div class="project-progress"><i style="width:${o.status==='일부 납품'?75:o.status==='제작 중'?50:25}%"></i></div><small>납품 예정 ${esc(o.dueDate)} ${arrow}</small></a>`;}).join('')||'<p class="empty">진행 중인 주문이 없어요. 새 견적을 주문으로 전환해 보세요.</p>'}</div></section>
 <footer class="portfolio-footer"><span><b>Growth ERP</b> &nbsp; 복잡한 업무를, 명료한 흐름으로.</span><span>모든 회사·거래·금액은 가상입니다. <a href="${repository}" target="_blank" rel="noopener">View source ↗</a></span></footer>`;
}

export function portfolioSettings(){return `<div class="page-head"><div><div class="eyebrow">ABOUT THIS WORKSPACE</div><h1>데모 워크스페이스</h1><p class="page-sub">마음껏 둘러보고, 직접 바꿔보세요.</p></div></div><section class="panel panel-pad"><h2>방문자별 체험 공간</h2><p class="demo-setting-copy">체험한 내용은 이 브라우저에 저장됩니다. 다른 방문자의 기록과 섞이지 않으며, 초기화하면 최신 날짜를 기준으로 가상 데이터가 다시 준비됩니다. 실제 개인정보나 업무 문서는 입력하지 마세요.</p><button class="button primary" data-demo-reset>데모 데이터 초기화</button></section><section class="panel panel-pad section-gap"><h2>실제로 동작하는 업무 흐름</h2><p class="demo-setting-copy">견적 작성·버전 관리, 주문 전환, 매출·매입 전표, 부분 납품·입고, 수금·지급, 재고 원가, PDF·엑셀 출력은 기존 ERP 로직으로 처리합니다. 공개 데모의 가이드는 문서 검색 방식이며, 외부 생성형 AI·은행·세금계산서 서비스에는 연결하지 않습니다.</p><button class="button" data-demo-tour>업무 흐름 따라 해보기</button><a class="button" href="/api/backup">체험 데이터 백업</a><a class="button" href="${repository}" target="_blank" rel="noopener">GitHub 소스 ↗</a></section>`;}

let getState;
export function bindPortfolio(state){getState=state;}
let tourDialog;
document.addEventListener('click',async e=>{
 if(e.target.closest('[data-demo-reset]')){
  if(!confirm('이 브라우저의 체험 기록과 생성한 문서를 지우고 새 데모로 돌아갈까요?'))return;
  const button=e.target.closest('button');button.disabled=true;
  try{await resetDemo();}catch(error){button.disabled=false;alert(error.message);}
 }
 if(e.target.closest('[data-demo-tour]')){
  const s=getState?.();if(!s)return;
  if(!tourDialog){tourDialog=document.createElement('dialog');tourDialog.className='demo-tour';tourDialog.setAttribute('aria-labelledby','tour-title');document.body.append(tourDialog);}
  const q=s.quotes.find(q=>!s.orders.some(o=>o.quoteId===q.id))||s.quotes.at(-1);
  const o=s.orders.find(o=>o.status==='일부 납품')||s.orders.at(-1);
  const v=s.vouchers.find(v=>v.kind==='sale'&&v.status==='confirmed'&&remaining(s,v)>0);
  const steps=[['01','관계를 살펴보세요','거래처에서 견적·주문·결제를 한 번에 확인합니다.',`account/${s.accounts[0]?.id}`],
   ['02','견적을 주문으로 연결하세요','견적을 열고 품목과 마진, PDF·엑셀 출력을 확인하세요.',q?'quote/'+q.id:'quotes'],
   ['03','부분 납품을 경험하세요','주문 상세에서 전표와 실제 납품의 연결을 확인하세요.',o?'order/'+o.id:'orders'],
   ['04','남은 수금을 기록하세요','거래 상세에서 수금을 기록하면 잔액과 할 일이 바뀝니다.',v?'transaction/'+v.id:'payments']];
  tourDialog.innerHTML=`<div class="tour-heading"><span class="eyebrow">A LITTLE TOUR</span><button data-tour-close aria-label="데모 투어 닫기">×</button><h2 id="tour-title">한 건의 거래가<br>비즈니스의 흐름이 되기까지.</h2><p>원하는 단계부터 체험해 보세요. 모든 변경은 이 브라우저에만 보관됩니다.</p></div><div class="tour-steps">${steps.map(([n,title,text,href])=>`<a href="#${esc(href)}" data-tour-go><span>${n}</span><div><strong>${title}</strong><p>${text}</p></div>${arrow}</a>`).join('')}</div><p class="tour-foot">모바일에서는 할 일·거래처 조회·가이드를 지원합니다.<br>견적·전표·문서 체험은 PC에서 이용하세요.</p>`;
  tourDialog.onclick=e=>{if(e.target.closest('[data-tour-close],[data-tour-go]'))tourDialog.close();};
  tourDialog.showModal();
 }
});
