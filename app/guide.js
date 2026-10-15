// Non-modal workspace companion. Keep the conversation while navigating the ERP.
export function createGuide({api,route,esc,refresh=()=>{}}) {
 let panel,catalog,loading=false,sending=false,error='',messages=[],question='',search='',trigger,expanded=false,tab='chat',contextSources=[];
 const suggestions={
  accounts:['거래처 관리 기능을 설명해 줘','거래처에 연결되는 기록은 어떤 것들이야?','거래처 화면을 안전하게 확인하려면 어떻게 해?'],
  tasks:['시작 전 할 일은 어떻게 등록해?','자동 할 일과 직접 등록한 할 일의 차이는?','완료한 할 일을 다시 진행 중으로 바꾸려면?'],
  payments:['미수금과 미지급금의 차이는?','이미 받은 선입금을 거래에 어떻게 연결해?','수금과 매출은 왜 따로 기록해?'],
  transactions:['전표 확정하면 세금계산서가 발급돼?','납품과 수금은 어떻게 연결돼?','증빙 정보 수정은 장부 금액도 바꿔?'],
  products:['품목의 기본 가격은 어디서 바꿔?','이동평균 단가를 쉬운 예로 설명해 줘','기초재고와 새 매입 입고는 어떻게 달라?'],
  analysis:['매출과 예상 순이익은 왜 달라?','공급가액과 부가세를 쉽게 설명해 줘','개인 인출은 왜 경비에서 제외돼?'],
  quotes:['견적부터 수금까지 순서를 알려줘','견적 PDF와 엑셀은 어디서 받아?','견적 단가와 품목 기본 가격은 어떻게 달라?']
 };
 const common=['거래처 관리 기능을 설명해 줘','견적부터 수금까지 순서를 알려줘','미수금과 매출은 어떻게 달라?','품목의 기본 가격은 어디서 바꿔?','시작 전 할 일을 등록하려면?','거래처 화면을 안전하게 확인하려면 어떻게 해?'];
 const page=()=>({account:'accounts',order:'orders',quote:'quotes',transaction:'transactions',product:'products',task:'tasks'}[route().split('/')[0]]||route().split('/')[0]);
 const normalize=s=>s.toLowerCase().replace(/\s/g,'');
 const formatted=text=>esc(text).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
 const testHref=a=>`#guide/${encodeURIComponent(a.id)}`;
 const testLink=a=>a.testSteps?.length?`<a href="${testHref(a)}" data-guide-route class="guide-test-link">${esc(a.title)} · 따라 해보기 →</a>`:'';
 const article=a=>`<details class="guide-article"><summary>${esc(a.title)}</summary><p>${esc(a.text)}</p><div class="guide-article-links"><a class="button small" href="#${esc(a.route)}" data-guide-route>관련 메뉴</a>${testLink(a)}</div></details>`;
 function layout(){
  if(!panel)return;
  const opened=!panel.hidden,full=expanded||(document.defaultView?.innerWidth||1440)<1100;
  panel.classList.toggle('guide-full',full);document.body.classList.toggle('guide-docked',opened&&!full);
  const app=document.querySelector('#app');if(app){if(opened&&full)app.setAttribute('inert','');else app.removeAttribute('inert');}
  const toggle=panel.querySelector('[data-guide-expand]');if(toggle)toggle.textContent=expanded?'나란히 보기':'크게 보기';
 }
 function render(){
  if(!panel)return;
  const scroll=panel.querySelector('.guide-body')?.scrollTop||0;
  const available=!!catalog?.enabled&&!catalog?.usage?.blocked;
  const articles=(catalog?.articles||[]).filter(a=>normalize(a.title+' '+a.text).includes(normalize(search)));
  const examples=[...new Set([...(suggestions[page()]||[]),...common])].slice(0,6);
  const followups=['방금 설명한 내용을 예를 들어 설명해 줘','그 기능은 어느 메뉴에서 사용해?','이 기능을 안전하게 확인하려면 어떻게 해?'];
  panel.innerHTML=`<div class="guide-header"><div><h2 id="guide-title">가이드 검색</h2><p>문서 검색 데모 · 외부 AI 호출 없음</p></div><div class="guide-header-actions"><button class="button small" data-guide-expand>${expanded?'나란히 보기':'크게 보기'}</button><button class="button small" data-guide-close aria-label="가이드 검색 닫기">닫기</button></div></div>
   <div class="guide-tabs" role="tablist" aria-label="가이드 보기"><button role="tab" id="guide-chat-tab" aria-controls="guide-chat-pane" data-guide-tab="chat" aria-selected="${tab==='chat'}" tabindex="${tab==='chat'?0:-1}">대화</button><button role="tab" id="guide-library-tab" aria-controls="guide-library-pane" data-guide-tab="library" aria-selected="${tab==='library'}" tabindex="${tab==='library'?0:-1}">사용 가이드</button></div>
   <div class="guide-body"><div role="tabpanel" id="guide-library-pane" aria-labelledby="guide-library-tab" ${tab==='library'?'':'hidden'}><label class="guide-search">가이드 검색<input data-guide-search type="search" value="${esc(search)}" placeholder="예: 거래처, 기본 가격, 선입금" maxlength="100"></label><div class="guide-articles">${articles.map(article).join('')||(!loading?'<p>일치하는 가이드가 없어요. 다른 용어로 검색해 주세요.</p>':'')}</div></div>
   <div role="tabpanel" id="guide-chat-pane" aria-labelledby="guide-chat-tab" ${tab==='chat'?'':'hidden'}>
   ${catalog&&!available?`<p class="notice">${catalog.usage?.blocked?'지금은 AI 답변을 사용할 수 없어요.':'AI 연결을 준비하고 있어요.'} 사용 가이드에서 업무 안내를 확인할 수 있습니다.</p>`:''}
   <div class="guide-chat-head"><h3>${messages.length?'이어서 물어보세요':'무엇이 궁금하세요?'}</h3><button class="button small" data-guide-reset ${sending?'disabled':''}>새 대화</button></div>
   <details class="guide-example-list" ${messages.length?'':'open'}><summary>예시 질문 · 눌러서 입력하거나 복사하세요</summary><div class="guide-suggestions">${examples.map(q=>`<div class="guide-example"><button type="button" data-guide-suggest="${esc(q)}" ${sending?'disabled':''}>${esc(q)}</button><button type="button" class="button small" data-guide-copy="${esc(q)}" aria-label="예시 질문 복사: ${esc(q)}">복사</button></div>`).join('')}</div></details>
   <div class="guide-messages" role="log" aria-live="polite">${messages.map(m=>`<div class="guide-message ${m.role}"><strong>${m.role==='user'?'나':'가이드 검색'}</strong><p>${formatted(m.content)}</p>${m.sources?`<div class="guide-sources"><details><summary>${m.fallback?'관련 사용 가이드':'참고 가이드'}</summary>${m.sources.map(article).join('')}</details>${m.sources.filter(a=>a.testSteps?.length).slice(0,2).map(testLink).join('')}</div>`:''}${m.truncated?'<small>궁금한 부분을 이어서 물어보세요.</small>':''}</div>`).join('')}${sending?'<p role="status">답변을 작성하고 있어요…</p>':''}</div>
   ${messages.length&&!sending?`<div class="guide-followups">${followups.map(q=>`<button class="button small" data-guide-suggest="${esc(q)}">${esc(q)}</button>`).join('')}</div>`:''}</div>
   ${loading?'<p role="status">가이드를 불러오는 중…</p>':''}<p class="guide-error" role="alert">${esc(error)}</p>${!catalog&&!loading?'<button class="button small" data-guide-reload>다시 불러오기</button>':''}</div>
   <form class="guide-composer" data-guide-form ${tab==='chat'?'':'hidden'}><label class="sr-only" for="guide-question">질문 입력</label><textarea id="guide-question" rows="2" maxlength="1000" required ${sending?'disabled':''} placeholder="궁금한 점이나 이어지는 질문을 입력하세요">${esc(question)}</textarea><div class="guide-submit"><small>Enter 전송 · Shift+Enter 줄바꿈</small><button type="submit" class="button primary" ${!available||sending?'disabled':''}>${sending?'답변 중…':'보내기'}</button></div></form>`;
  const body=panel.querySelector('.guide-body');if(body)body.scrollTop=scroll;layout();
 }
 function latest(){const body=panel?.querySelector('.guide-body');if(body)body.scrollTop=body.scrollHeight;}
 async function load(){
  if(loading)return;
  loading=true;render();
  try{catalog=await api('/api/guide');error='';}catch(e){error=e.message;}
  finally{loading=false;render();if(route().startsWith('guide'))refresh();}
 }
 async function submit(){
  if(sending||!catalog?.enabled||catalog.usage?.blocked)return;
  const text=question.trim();if(!text)return;
  const history=messages.slice(-12).map(m=>({role:m.role,content:m.content}));
  sending=true;error='';messages.push({role:'user',content:text});question='';render();latest();
  try{
   const result=await api('/api/guide/ask',{question:text,history,contextSources,page:page(),requestId:crypto.randomUUID()});
   messages.push({role:'assistant',content:result.answer,sources:result.sources,truncated:result.truncated,fallback:result.fallback});
   const currentSources=result.sources.map(a=>a.id);
   contextSources=[...contextSources.filter(id=>!currentSources.includes(id)),...currentSources.slice().reverse()].slice(-6);catalog.usage=result.usage;
  }catch(e){messages.pop();question=text;error=e.message;}
  finally{sending=false;render();latest();if(!panel.hidden)panel.querySelector('#guide-question')?.focus();}
 }
 function close(){panel.hidden=true;layout();(trigger?.isConnected?trigger:document.querySelector('[data-action="guide"]'))?.focus();}
 function fill(value){question=value;tab='chat';render();panel.querySelector('#guide-question')?.focus();}
 function init(){
  panel=document.createElement('aside');panel.className='guide-panel';panel.hidden=true;panel.setAttribute('aria-labelledby','guide-title');document.body.append(panel);
  panel.addEventListener('click',async e=>{
   if(e.target.closest('[data-guide-close]'))close();
   if(e.target.closest('[data-guide-expand]')){expanded=!expanded;layout();}
   if(e.target.closest('[data-guide-route]')){expanded=false;layout();if((document.defaultView?.innerWidth||1440)<1100)close();}
   const selected=e.target.closest('[data-guide-tab]');if(selected){tab=selected.dataset.guideTab;render();panel.querySelector(`[data-guide-tab="${tab}"]`)?.focus();}
   const suggest=e.target.closest('[data-guide-suggest]');if(suggest&&!sending)fill(suggest.dataset.guideSuggest);
   const copy=e.target.closest('[data-guide-copy]');if(copy){
    try{if(!globalThis.navigator?.clipboard?.writeText)throw Error('clipboard unavailable');await navigator.clipboard.writeText(copy.dataset.guideCopy);copy.textContent='복사됨';}
    catch{if(!sending){fill(copy.dataset.guideCopy);const input=panel.querySelector('#guide-question');input?.select?.();error='입력창에 질문을 넣었어요. 선택한 문장을 복사해 주세요.';panel.querySelector('.guide-error').textContent=error;}}
    return;
   }
   if(e.target.closest('[data-guide-reset]')&&!sending){messages=[];contextSources=[];question='';error='';render();panel.querySelector('#guide-question')?.focus();}
   if(e.target.closest('[data-guide-reload]'))load();
  });
  panel.addEventListener('input',e=>{
   if(e.target.id==='guide-question')question=e.target.value;
   if(e.target.matches('[data-guide-search]')){search=e.target.value;render();panel.querySelector('[data-guide-search]')?.focus();}
  });
  panel.addEventListener('keydown',e=>{
   if(e.key==='Escape'){e.preventDefault();close();}
   if(e.target.matches('[data-guide-tab]')&&['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();tab=tab==='chat'?'library':'chat';render();panel.querySelector(`[data-guide-tab="${tab}"]`)?.focus();}
   if(e.target.id==='guide-question'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();submit();}
  });
  panel.addEventListener('submit',e=>{if(e.target.matches('[data-guide-form]')){e.preventDefault();submit();}});
  document.defaultView?.addEventListener('resize',layout);
  document.defaultView?.addEventListener('hashchange',()=>{if(!panel.hidden)render();});
 }
 function guidePage(id){
  if(!catalog){if(!loading&&!error)load();return `<h1>사용 가이드</h1><p>${esc(error||'가이드를 불러오는 중…')}</p><button class="button" data-action="guide">가이드 검색 열기</button>`;}
  const a=catalog.articles.find(a=>a.id===id);
  if(!a)return `<h1>사용 가이드</h1><div class="guide-page">${catalog.articles.map(article).join('')}</div>`;
  return `<div class="page-head"><div><h1>${esc(a.title)}</h1><p class="page-sub">기능 이해와 따라 해보기</p></div><a class="button primary" href="#${esc(a.route)}">${esc(a.title)} · 관련 메뉴 열기</a></div><section class="panel guide-page"><h2>어떤 기능인가요?</h2><p>${esc(a.text)}</p>${a.testSteps?.length?`<h2>따라 해보기</h2><ol>${a.testSteps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><h3>확인할 결과</h3><p>${esc(a.expected)}</p>`:''}<button class="button" data-action="guide">이어서 질문하기</button></section>`;
 }
 return {open:async()=>{trigger=document.activeElement;if(!panel)init();panel.hidden=false;render();if(!sending)await load();},page:guidePage};
}
