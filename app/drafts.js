// Drafts are isolated by signed-in user/workspace and browser tab.
export function createDrafts({dialog,scope,storage=()=>sessionStorage}) {
 return function draft(key){
  const fullKey='growth-draft:'+scope()+':'+key;let saved=null,form,readItems;
  try{saved=JSON.parse(storage().getItem(fullKey)||'null');}catch{}
  function save(){
   if(!form?.isConnected||!dialog.open)return;
   const fields={};for(const el of form.querySelectorAll('[name]'))if(!el.disabled&&el.type!=='file')fields[el.name]=el.value;
   try{storage().setItem(fullKey,JSON.stringify({fields,items:readItems()}));status.textContent='임시 저장됨 · 이 탭에서 다시 열거나 새로고침해도 복원됩니다. 정식 등록은 저장 버튼을 눌러 주세요.';}
   catch{status.textContent='임시 저장을 사용할 수 없습니다. 창을 닫기 전에 저장해 주세요.';}
  }
  let status;
  return {items:saved?.items,clear(){try{storage().removeItem(fullKey);}catch{}},bind(read){
   form=dialog.querySelector('form');readItems=read;
   if(saved?.fields)for(const el of form.querySelectorAll('[name]'))if(!el.disabled&&Object.hasOwn(saved.fields,el.name)){
    el.value=saved.fields[el.name];
    if(el.type==='hidden'&&el.closest('[data-lookup]'))el.dispatchEvent(new window.Event('change',{bubbles:true}));
   }
   status=document.createElement('p');status.setAttribute('role','status');status.className='notice';status.textContent=saved?'작성 중이던 내용을 복원했어요. 정식 등록은 저장 버튼을 눌러 주세요.':'입력 내용은 이 탭에 자동 임시 저장됩니다.';form.prepend(status);
   const discard=document.createElement('button');discard.type='button';discard.className='button small';discard.textContent='임시 내용 버리고 닫기';
   discard.addEventListener('click',()=>{try{storage().removeItem(fullKey);dialog.close();}catch{status.textContent='임시 내용을 삭제하지 못했습니다.';}});status.after(discard);
   for(const event of ['input','change','click'])form.addEventListener(event,()=>queueMicrotask(save));
  }};
 };
}
