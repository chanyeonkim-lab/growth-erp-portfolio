// Shared record search. Only a selected record ID is submitted, never typed text.
export function createLookup({state,esc,createAccount}) {
 const entries=new Map();let sequence=0;
 const normalize=value=>String(value??'').normalize('NFKC').toLocaleLowerCase().replace(/[\s()-]/g,'');
 function field(label,name,options,value='',extra=''){
  const key='lookup-'+(++sequence),order=name==='orderId',records=state()[order?'orders':'accounts']||[],byId=new Map(records.map(r=>[r.id,r])),accounts=new Map(state().accounts.map(a=>[a.id,a]));
  const rows=options.map(o=>Array.isArray(o)?o:[o,o]).filter(([id])=>id).map(([id,title])=>{
   const r=byId.get(id)||{},a=order?accounts.get(r.accountId)||{}:r;
   const detail=order?[a.name,r.date,r.status]:[a.contact,a.phone,a.phone2,a.businessNumber,a.active===false?'비활성':''];
   return {id:String(id),title:String(title),detail:detail.filter(Boolean).join(' · '),search:normalize([title,r.number,r.title,...detail].join(' '))};
  });
  entries.set(key,rows);const selected=rows.find(r=>r.id===String(value));
  return `<div class="field record-lookup" data-lookup="${key}"><label for="${key}">${esc(label)}</label><div class="lookup-control"><input id="${key}" data-lookup-input ${order?'data-order-search':''} type="text" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${key}-results" autocomplete="off" placeholder="${order?'주문번호·거래처·주문명 검색':'거래처명·담당자·전화번호 검색'}" value="${esc(selected?.title||'')}" ${extra}><button type="button" data-lookup-clear aria-label="${esc(label)} 선택 지우기">×</button><div class="lookup-results" id="${key}-results" role="listbox" aria-label="${esc(label)} 검색 결과" hidden></div></div><input type="hidden" name="${name}" value="${esc(selected?.id||'')}"><small data-lookup-status role="status" aria-live="polite">${selected?esc(selected.detail||'선택됨'):'검색 후 결과를 선택하세요.'}</small>${!order&&createAccount&&!extra.includes('disabled')?'<button type="button" class="button small" data-lookup-new>+ 거래처 바로 등록</button>':''}</div>`;
 }
 const parts=el=>({input:el.querySelector('[data-lookup-input]'),hidden:el.querySelector('input[type="hidden"]'),list:el.querySelector('[role="listbox"]'),status:el.querySelector('[data-lookup-status]')});
 function close(el){const {input,list}=parts(el);list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');}
 function draw(el){
  const {input,hidden,list,status}=parts(el);if(input.disabled)return;
  refreshRows(el);const rows=entries.get(el.dataset.lookup)||[],q=hidden.value?'':normalize(input.value),matches=q?rows.filter(r=>r.search.includes(q)):rows,visible=matches.slice(0,30);
  list.innerHTML=visible.map((r,i)=>`<button type="button" tabindex="-1" role="option" aria-selected="false" id="${input.id}-option-${i}" data-lookup-choice="${esc(r.id)}" ${hidden.name==='orderId'?`data-order-choice="${esc(r.id)}"`:''}><strong>${esc(r.title)}</strong><small>${esc(r.detail)}</small></button>`).join('')||'<div class="lookup-empty">검색 결과가 없습니다.</div>';
  list.hidden=false;input.setAttribute('aria-expanded','true');input.removeAttribute('aria-activedescendant');
  status.textContent=matches.length>30?`${matches.length}건 중 30건 표시 · 검색어를 더 입력하세요.`:matches.length?`${matches.length}건 · ↑ ↓ 이동, Enter 선택`:'다른 이름·번호로 검색해 주세요.';
 }
 function refreshRows(el){
  const {hidden}=parts(el);if(hidden.name==='orderId')return;
  entries.set(el.dataset.lookup,(state().accounts||[]).filter(a=>a.active!==false||a.id===hidden.value).map(a=>{
   const detail=[a.contact,a.phone,a.phone2,a.businessNumber].filter(Boolean).join(' · ');
   return {id:String(a.id),title:a.name,detail,search:normalize(a.name+' '+detail)};
  }));
 }
 function refresh(root){for(const el of root.querySelectorAll('[data-lookup]')){
  refreshRows(el);const {input,hidden,list}=parts(el),row=(entries.get(el.dataset.lookup)||[]).find(r=>r.id===hidden.value);
  if(row)input.value=row.title;if(!list.hidden)draw(el);
 }}
 function inlineCreate(el){
  if(el.querySelector('[data-quick-account]'))return;
  const box=document.createElement('div');box.dataset.quickAccount='';box.className='notice';
  box.innerHTML=`<label>거래처명<input data-quick-name value="${esc(parts(el).input.value)}" maxlength="160"></label><label>구분<select data-quick-kind><option>고객사·매입처</option><option>고객사</option><option>매입처</option></select></label><label>전화번호<input data-quick-phone></label><button type="button" class="button primary" data-quick-save>등록하고 선택</button><button type="button" class="button" data-quick-cancel>취소</button><p role="status" data-quick-status></p>`;
  el.append(box);
  box.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();e.stopPropagation();box.querySelector('[data-quick-save]').click();}});
  box.querySelector('[data-quick-cancel]').addEventListener('click',()=>box.remove());
  box.querySelector('[data-quick-save]').addEventListener('click',async e=>{
   const name=box.querySelector('[data-quick-name]').value.trim(),status=box.querySelector('[data-quick-status]');
   if(!name){status.textContent='거래처명을 입력해 주세요.';return;}
   e.target.disabled=true;
   try{const id=await createAccount({name,kind:box.querySelector('[data-quick-kind]').value,phone:box.querySelector('[data-quick-phone]').value});refreshRows(el);choose(el,id);box.remove();}
   catch(error){status.textContent=error.message;}finally{e.target.disabled=false;}
  });
 }
 function choose(el,id){
  const r=(entries.get(el.dataset.lookup)||[]).find(r=>r.id===id);if(!r)return;
  const {input,hidden,status}=parts(el);if(input.disabled)return;hidden.value=r.id;input.value=r.title;input.setCustomValidity?.('');status.textContent=r.detail||'선택됨';close(el);
  hidden.dispatchEvent(new window.Event('change',{bubbles:true}));
 }
 function bind(root){
  root.addEventListener('change',e=>{if(e.target.matches('input[type="hidden"]')&&e.target.closest('[data-lookup]')){const el=e.target.closest('[data-lookup]');refreshRows(el);const row=(entries.get(el.dataset.lookup)||[]).find(r=>r.id===e.target.value);if(row)parts(el).input.value=row.title;}});
  root.addEventListener('focusin',e=>{if(e.target.matches('[data-lookup-input]'))draw(e.target.closest('[data-lookup]'));});
  root.addEventListener('input',e=>{if(!e.target.matches('[data-lookup-input]'))return;const el=e.target.closest('[data-lookup]'),{input,hidden}=parts(el);hidden.value='';input.setCustomValidity?.('');hidden.dispatchEvent(new window.Event('change',{bubbles:true}));draw(el);});
  root.addEventListener('pointerdown',e=>{if(e.target.closest('[data-lookup-choice]'))e.preventDefault();});
  root.addEventListener('click',e=>{
   const el=e.target.closest('[data-lookup]');if(!el)return;
   if(e.target.closest('[data-lookup-new]'))inlineCreate(el);
   const choice=e.target.closest('[data-lookup-choice]');if(choice)choose(el,choice.dataset.lookupChoice);
   if(e.target.closest('[data-lookup-clear]')){const {input,hidden,status}=parts(el);if(input.disabled)return;input.value='';hidden.value='';input.setCustomValidity?.('');status.textContent='검색 후 결과를 선택하세요.';hidden.dispatchEvent(new window.Event('change',{bubbles:true}));input.focus();draw(el);}
  });
  root.addEventListener('focusout',e=>{const el=e.target.closest('[data-lookup]');if(el&&!el.contains(e.relatedTarget))close(el);});
  root.addEventListener('keydown',e=>{
   if(!e.target.matches('[data-lookup-input]')||e.isComposing)return;
   const el=e.target.closest('[data-lookup]'),{input,list}=parts(el);
   if(e.key==='Escape'&&!list.hidden){e.preventDefault();e.stopPropagation();close(el);return;}
   if(!['ArrowDown','ArrowUp','Enter'].includes(e.key))return;
   if(e.key==='Enter'){if(list.hidden)return;e.preventDefault();const active=list.querySelector('[aria-selected="true"]');if(active)choose(el,active.dataset.lookupChoice);return;}
   e.preventDefault();if(list.hidden)draw(el);const rows=[...list.querySelectorAll('[role="option"]')];if(!rows.length)return;
   const current=rows.findIndex(r=>r.getAttribute('aria-selected')==='true'),next=current<0?(e.key==='ArrowDown'?0:rows.length-1):(current+(e.key==='ArrowDown'?1:-1)+rows.length)%rows.length;
   rows.forEach((r,i)=>r.setAttribute('aria-selected',String(i===next)));input.setAttribute('aria-activedescendant',rows[next].id);rows[next].scrollIntoView?.({block:'nearest'});
  });
 }
 function validate(root){for(const el of root.querySelectorAll('[data-lookup]')){
  const {input,hidden}=parts(el);if(input.disabled)continue;
  if(!hidden.value&&(input.hasAttribute('required')||input.value.trim())){const message='검색 결과에서 '+el.querySelector('label').textContent+'을(를) 선택해 주세요.';input.setCustomValidity?.(message);input.focus();throw Error(message);}
 }}
 return {field,bind,validate,choose,refresh,prune:root=>{const active=new Set([...root.querySelectorAll('[data-lookup]')].map(el=>el.dataset.lookup));for(const key of entries.keys())if(!active.has(key))entries.delete(key);}};
}
