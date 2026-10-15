// Browser-owned state; the server only evaluates a supplied synthetic ledger.
const KEY='growth-portfolio-v1';
let snapshot,loading;
export const repository='https://github.com/chanyeonkim-lab/growth-erp-portfolio';
const clone=value=>structuredClone(value);
async function request(path,body){
 const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const value=await r.json();if(!r.ok)throw Error(value.error||'데모 요청을 완료하지 못했어요.');return value;
}
function read(){try{return JSON.parse(localStorage.getItem(KEY));}catch{return null;}}
function save(next){
 try{localStorage.setItem(KEY,JSON.stringify(next));}catch{throw Error('브라우저 저장 공간이 부족하거나 차단되어 있어요. 생성한 문서를 내려받은 뒤 데모를 초기화해 주세요.');}
 snapshot=next;
}
async function init(){
 if(snapshot)return;
 if(!loading)loading=(async()=>{const saved=read();if(saved?.state?.demoMeta?.version===1){snapshot=saved;return;}const seed=await request('/api/demo/seed');save({...seed,files:seed.files||{}});})().finally(()=>loading=null);
 await loading;
}
function check(revision){
 const latest=read();if(latest)snapshot=latest;
 if(revision!==snapshot.revision)throw Error('다른 창에서 데이터가 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.');
}
async function serialized(fn){
 if(navigator.locks)return navigator.locks.request('growth-portfolio-state',fn);
 return fn();
}
export async function demoApi(path,body){
 await init();
 if(path==='/api/config')return {portfolio:true,erpVersion:2,cloud:false,aiConnected:false,
   gmailConnected:false,bankConnected:false,tax:{mode:'disabled'},maxUploadBytes:1024*1024,user:{displayName:'김데모',username:'visitor'}};
 if(path==='/api/state'&&body===undefined){snapshot=read()||snapshot;return clone(snapshot);}
 if(path==='/api/state')return serialized(async()=>{
   check(body.revision);const previous=snapshot;
   await request('/api/demo/validate',{state:body.state,previous:previous.state});
   if((read()?.revision??-1)!==previous.revision)throw Error('다른 창에서 데이터가 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.');
   save({...previous,state:body.state,revision:previous.revision+1});return {revision:snapshot.revision};
 });
 if(path==='/api/workflow')return serialized(async()=>{
   check(body.revision);const previous=snapshot;
   const r=await request('/api/demo/command',{state:previous.state,action:body.action,data:body.data});
   if((read()?.revision??-1)!==previous.revision)throw Error('다른 창에서 데이터가 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.');
   const files={...previous.files};if(r.file)files[r.file.id]=r.file.content;
   for(const id of r.result.deletedDocumentIds||[])delete files[id];
   save({state:r.state,revision:previous.revision+1,files});
   return {...r.result,state:clone(r.state),revision:snapshot.revision};
 });
 if(path==='/api/upload')return serialized(async()=>{
   const r=await request('/api/demo/upload',body);const previous=read()||snapshot;
   const state=clone(previous.state);state.documents.push(r.document);
   save({state,revision:previous.revision+1,files:{...previous.files,[r.document.id]:body.content}});
   return r;
 });
 if(path==='/api/guide'||path==='/api/guide/ask')return request(path,body);
 throw Error('이 연결 기능은 공개 데모에서 제공하지 않습니다.');
}
export async function resetDemo(){
 const seed=await request('/api/demo/seed');
 await serialized(async()=>save({...seed,revision:(read()?.revision||0)+1,files:seed.files||{}}));
 for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('growth-draft:demo:'))sessionStorage.removeItem(key);}
 location.hash='dashboard';location.reload();
}
function download(content,name,type){
 const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
document.addEventListener('click',async event=>{
 const a=event.target.closest('a[href]');if(!a)return;
 const url=new URL(a.href,location.href);if(url.origin!==location.origin)return;
 const match=url.pathname.match(/^\/api\/documents\/([^/]+)\/file$/);
 if(!match&&url.pathname!=='/api/backup')return;
 event.preventDefault();event.stopImmediatePropagation();await init();snapshot=read()||snapshot;
 if(match){
  const doc=snapshot.state.documents.find(d=>d.id===decodeURIComponent(match[1]));
  const content=snapshot.files[doc?.id];if(!content)return;
  download(Uint8Array.from(atob(content),c=>c.charCodeAt(0)),doc.name,doc.name.endsWith('.pdf')?'application/pdf':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
 }else{
  const {zipSync,strToU8}=await import('./vendor/fflate.js');
  const files={'demo-ledger.json':strToU8(JSON.stringify(snapshot.state,null,2))};
  for(const doc of snapshot.state.documents){const file=snapshot.files[doc.id];if(file)files['documents/'+doc.id+'/'+doc.name.replaceAll('/','_')]=Uint8Array.from(atob(file),c=>c.charCodeAt(0));}
  download(zipSync(files),'growth-demo-backup.zip','application/zip');
 }
},true);
