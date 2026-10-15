// Real browser + real local API, with an isolated temporary database. Never uses production data.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,existsSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {chromium} from 'playwright';
const dataDir=mkdtempSync(path.join(tmpdir(),'growth-mobile-'));
const port=await new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
const base=`http://127.0.0.1:${port}`,shots=process.env.GROWTH_SCREENSHOTS;
if(shots)mkdirSync(shots,{recursive:true});
const python=existsSync('.venv/bin/python')?'.venv/bin/python':'python3';
const server=spawn(python,['server.py','--port',String(port)],{env:{...process.env,GROWTH_DATA_DIR:dataDir,ANTHROPIC_API_KEY:'',OPENAI_API_KEY:''},stdio:'pipe'});
let serverLog='';server.stderr.on('data',b=>serverLog+=b);
let browser;
async function state(){return (await fetch(base+'/api/state?workspace=demo')).json();}
async function command(action,data){const {revision}=await state();const response=await fetch(base+'/api/workflow?workspace=demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,data,revision})});const result=await response.json();assert.ok(response.ok,JSON.stringify(result));return result;}
try{
 for(let i=0;i<100;i++){try{await state();break;}catch{if(i===99)throw Error(serverLog);await new Promise(r=>setTimeout(r,100));}}
 await command('erp.initialize',{confirmed:true});
 await command('erp.account.save',{id:'ac-0',name:'루미나 캠퍼스',kind:'고객사',contact:'김담당',phone:'010-1234-5678',phone2:'02-987-6543',businessNumber:'123-45-67890',address:'서울특별시 종로구 아주 긴 주소도 줄바꿈되어 표시됩니다',active:true,paymentTermsDays:30});
 await command('erp.account.save',{name:'비활성 확인 고객',active:false,paymentTermsDays:30});
 browser=await chromium.launch({headless:true,...process.env.GROWTH_CHROMIUM_EXECUTABLE?{executablePath:process.env.GROWTH_CHROMIUM_EXECUTABLE}:{}});
 for(const width of [390,768]){
  const context=await browser.newContext({viewport:{width,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const go=async hash=>{await page.goto(base+'/#'+hash);await page.locator('.mobile-nav').waitFor();};
  const fits=async()=>assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width}px horizontal overflow`);
  const shot=async name=>{await fits();if(shots)await page.screenshot({path:path.join(shots,`${width}-${name}.png`),fullPage:true});};
  const dismiss=async()=>{await page.locator('#mobile-restriction [data-mobile-dismiss]').last().click();await page.locator('#mobile-restriction').waitFor({state:'hidden'});};
  const save=async()=>{await page.locator('#modal button[type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});};
  await page.route('**/api/auth/login',r=>r.fulfill({json:{ok:true}}));
  await page.goto(base+'/login.html#transaction/legacy-sale-invoice-10-0');
  await page.locator('[name=username]').fill('browser-test');await page.locator('[name=password]').fill('test-only-password');
  await page.locator('button[type=submit]').click();await page.locator('.mobile-handoff').waitFor();
  assert.ok(page.url().endsWith('#transaction/legacy-sale-invoice-10-0'));await page.locator('#mobile-restriction[open]').waitFor();await shot('login-return');await dismiss();
  await go('dashboard');assert.equal(await page.locator('h1').innerText(),'전체 할 일');await shot('tasks');
  assert.equal(await page.locator('.sidebar').count(),0);
  await page.locator('[data-action="erp-task-new"]').click();
  await page.locator('#modal [name=title]').fill(`모바일 ${width} 검증 할 일`);
  await page.locator('#modal [data-lookup-input]').fill('루미나');
  await page.locator('#modal [data-lookup-choice="ac-0"]').click();
  await page.locator('#modal [name=date]').fill('2026-10-10');
  await page.locator('#modal [name=notes]').fill('모바일에서 등록한 메모');
  await shot('task-form');
  if(width===390){
   await page.route('**/api/workflow?workspace=demo',r=>r.fulfill({status:503,json:{error:'테스트: 연결을 확인해 주세요.'}}),{times:1});
   await page.locator('#modal button[type=submit]').click();await page.locator('.form-error').filter({hasText:'테스트: 연결을 확인해 주세요.'}).waitFor();
   assert.equal(await page.locator('#modal [name=title]').inputValue(),`모바일 ${width} 검증 할 일`);
   assert.equal(await page.locator('#modal button[type=submit]').isEnabled(),true);
  }
  await save();
  let task=(await state()).state.tasks.find(t=>t.title===`모바일 ${width} 검증 할 일`);assert.equal(task.status,'not_started');assert.equal(task.accountId,'ac-0');
  await go('task/'+task.id);await page.locator('[data-action="erp-task-edit"]').click();
  await page.locator('#modal [name=title]').fill(`수정한 모바일 ${width} 할 일`);
  await page.locator('#modal [name=status]').selectOption('in_progress');await save();
  task=(await state()).state.tasks.find(t=>t.id===task.id);assert.equal(task.status,'in_progress');assert.equal(task.title,`수정한 모바일 ${width} 할 일`);
  await go('task/'+task.id);await shot('task-detail');await page.locator('[data-action="erp-task-edit"]').click();
  await page.locator('#modal [name=status]').selectOption('completed');await save();
  await page.locator('[data-action="erp-task-filter"][data-id=completed]').click();
  await page.locator(`a[href="#task/${task.id}"]`).click();await page.locator('[data-action="erp-task-edit"]').click();
  await page.locator('#modal [name=status]').selectOption('not_started');await save();
  assert.equal((await state()).state.tasks.find(t=>t.id===task.id).done,false);
  await go('accounts');await page.locator('[data-search]').fill('123-45-67890');assert.equal(await page.locator('.mobile-account').count(),1);await shot('account-search');
  await page.locator('.mobile-account').click();await page.locator('h1').filter({hasText:'루미나 캠퍼스'}).waitFor();
  assert.equal(await page.locator('a[href="tel:01012345678"]').count(),1);assert.equal(await page.locator('[data-action="erp-account-edit"]').count(),0);await shot('account-detail');
  await page.locator('[data-list-return]').click();assert.equal(await page.locator('[data-search]').inputValue(),'123-45-67890');
  await page.locator('[data-search]').fill('존재하지않는거래처');await page.getByText('검색 결과가 없어요.',{exact:false}).waitFor();
  await page.locator('[data-search]').fill('비활성 확인');assert.equal(await page.locator('.mobile-account').count(),0);await page.locator('[data-action="erp-inactive-toggle"]').click();assert.equal(await page.locator('.mobile-account').count(),1);
  await go('tasks');await page.locator('[data-action="erp-task-filter"][data-id="미수금"]').click();
  const sourceRow=page.locator('.todo-row').first(),sourceLink=sourceRow.locator('a.task-row-content');
  const source=await sourceLink.getAttribute('href');const before=(await state()).revision;
  const originUrl=page.url();
  await sourceLink.click();await page.locator('#mobile-restriction[open]').waitFor();
  assert.equal(page.url(),originUrl);assert.match(await page.locator('#mobile-restriction').innerText(),/직접 완료하지 않고/);
  assert.ok((await page.locator('#mobile-restriction [data-mobile-link]').inputValue()).endsWith(source));await shot('source-notice');
  await dismiss();assert.equal(page.url(),originUrl);assert.equal((await state()).revision,before);
  const attempt=await fetch(base+'/api/workflow?workspace=demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:before,action:'erp.task.save',data:{id:'payment-'+source.split('/')[1],title:'자동 할 일 직접 완료 시도',status:'completed',date:'2026-10-06'}})});
  assert.equal(attempt.ok,false);assert.equal((await state()).revision,before);
  await go(source.slice(1));
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Clipboard unavailable');}}}));
  await page.locator('#mobile-restriction [data-action=mobile-copy]').click();await page.locator('#mobile-restriction [data-copy-status]').filter({hasText:'길게 눌러 복사'}).waitFor();
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.copiedLink=value;}}}));
  await page.locator('#mobile-restriction [data-action=mobile-copy]').click();await page.locator('#mobile-restriction [data-copy-status]').filter({hasText:'링크를 복사했어요'}).waitFor();
  assert.equal(await page.evaluate(()=>window.copiedLink),await page.locator('#mobile-restriction [data-mobile-link]').inputValue());
  await dismiss();
  for(const route of ['quotes','quote/quote-0','documents','products','product/missing','order/order-10-0','settings','payments']){await go(route);await page.locator('.mobile-handoff').waitFor();await page.locator('#mobile-restriction[open]').waitFor();assert.equal(await page.locator('#modal[open]').count(),0);await fits();await dismiss();}
  await go('accounts');const accountCount=await page.locator('.mobile-account').count();await page.locator('[data-action=guide]').click();await page.locator('.guide-panel:not([hidden])').waitFor();await shot('guide');
  await page.locator('[data-guide-tab=library]').click();
  const quoteArticle=page.locator('.guide-article').filter({has:page.locator('a[href="#quotes"]')}).first();await quoteArticle.locator('summary').click();
  const guideOrigin=page.url();await quoteArticle.locator('a[href="#quotes"]').click();await page.locator('#mobile-restriction[open]').waitFor();assert.equal(page.url(),guideOrigin);
  await page.keyboard.press('Escape');await page.locator('#mobile-restriction').waitFor({state:'hidden'});assert.equal(await page.locator('.guide-panel').isVisible(),true);
  await page.locator('[data-guide-close]').click();assert.equal(await page.locator('.mobile-account').count(),accountCount);
  await go('guide/quote');await page.locator('main a[href="#quotes"]').click();await page.locator('#mobile-restriction[open]').waitFor();assert.ok(page.url().endsWith('#guide/quote'));await dismiss();
  const forbiddenRequests=[];page.on('request',r=>{if(r.url().includes('/api/templates/')||(r.method()==='POST'&&r.url().includes('/api/workflow')))forbiddenRequests.push(r.url());});
  await page.evaluate(()=>{const a=document.createElement('a');a.href='/api/templates/quote.pdf?workspace=demo';a.download='quote.pdf';a.textContent='이전 화면의 PDF 다운로드';a.id='stale-download';document.querySelector('main').append(a);});
  await page.locator('#stale-download').click();await page.locator('#mobile-restriction[open]').waitFor();await dismiss();assert.deepEqual(forbiddenRequests,[]);
  for(const action of ['wf-quote-new','erp-product-new','erp-voucher-pdf']){
   await page.evaluate(action=>{const b=document.createElement('button');b.dataset.action=action;b.id='stale-action';b.textContent='이전 화면 업무';document.querySelector('main').append(b);},action);
   await page.locator('#stale-action').click();await page.locator('#mobile-restriction[open]').waitFor();await dismiss();await page.locator('#stale-action').evaluate(el=>el.remove());
  }
  assert.deepEqual(forbiddenRequests,[]);
  // Resize chooses presentation only, leaving the same route and records intact.
  await go('accounts');await page.setViewportSize({width:1440,height:1000});await page.locator('.sidebar').waitFor();await page.locator('.data-table').waitFor();
  await page.setViewportSize({width,height:844});await page.locator('.mobile-account').first().waitFor();await fits();
  assert.deepEqual(errors,[]);await context.close();console.log(`PASS ${width}px: task CRUD/status/reopen, party search/detail, source handoff, guide, resize`);
 }
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const route of ['dashboard','accounts','account/ac-0','tasks','quotes','orders','transactions','payments','products','analysis']){await page.goto(base+'/#'+route);await page.locator('.sidebar').waitFor();assert.equal(await page.locator('.mobile-nav').count(),0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),route);if(shots&&['dashboard','accounts','account/ac-0'].includes(route))await page.screenshot({path:path.join(shots,'desktop-'+route.replace('/','-')+'.png'),fullPage:true});}
 await page.goto(base+'/#accounts');await page.locator('[data-action="erp-account-preview"][data-id="ac-0"]').click();await page.locator('.account-preview').waitFor();
 await page.locator('[data-action=guide]').click();await page.locator('body.guide-docked').waitFor();await page.locator('.sidebar a[href="#tasks"]').click();await page.locator('h1').filter({hasText:'전체 할 일'}).waitFor();await page.locator('[data-guide-close]').click();
 // A desktop draft survives a narrow viewport; mobile submits are blocked before any POST.
 await page.goto(base+'/#quotes');await page.locator('[data-action="wf-quote-new"]').click();await page.locator('#modal [name=title]').fill('PC 견적 입력 유지');
 await page.setViewportSize({width:390,height:844});await page.locator('#mobile-restriction[open]').waitFor();await page.locator('#mobile-restriction [data-mobile-dismiss]').last().click();
 await page.setViewportSize({width:1440,height:1000});await page.locator('#mobile-restriction').waitFor({state:'hidden'});assert.equal(await page.locator('#modal [name=title]').inputValue(),'PC 견적 입력 유지');await page.locator('#modal [data-action=close]').first().click();
 await page.goto(base+'/#products');await page.locator('[data-action="erp-product-new"]').click();await page.locator('#modal [name=code]').fill('PC-MOBILE-GUARD');await page.locator('#modal [name=name]').fill('PC 품목 입력 유지');
 const before=(await state()).revision;
 await page.setViewportSize({width:390,height:844});await page.locator('#mobile-restriction[open]').waitFor();await page.locator('#mobile-restriction [data-mobile-dismiss]').last().click();
 await page.locator('#modal [type=submit]').click();await page.locator('#mobile-restriction[open]').waitFor();assert.equal((await state()).revision,before);
 await page.setViewportSize({width:1440,height:1000});await page.locator('#mobile-restriction').waitFor({state:'hidden'});assert.equal(await page.locator('#modal [name=name]').inputValue(),'PC 품목 입력 유지');
 await page.locator('#modal [type=submit]').click();await page.locator('#modal').waitFor({state:'hidden'});assert.ok((await state()).state.products.some(p=>p.code==='PC-MOBILE-GUARD'));
 await page.goto(base+'/#quotes');await page.locator('[data-action="wf-quote-new"]').click();
 assert.equal(await page.locator('#modal [data-catalog-search]').count(),0);assert.equal(await page.locator('#modal [data-key="section"]').count(),0);assert.equal(await page.locator('#modal [data-line="0"] [data-product-search]').count(),1);
 await page.locator('#modal [data-line="0"] [data-product-search]').click();await page.getByRole('button',{name:'직접 입력'}).waitFor();
 await page.locator('#modal [data-line="0"] [data-line-catalog-search]').fill('PC-MOBILE-GUARD');await page.locator('#modal [data-line="0"] [data-catalog-id]').click();
 assert.equal(await page.locator('#modal [data-line="0"] [data-key="name"]').inputValue(),'PC 품목 입력 유지');if(shots)await page.screenshot({path:path.join(shots,'desktop-quote-line-search.png'),fullPage:true});await page.locator('#modal [data-action=close]').first().click();
 await page.goto(base+'/#orders');await page.locator('[data-action="wf-order-new"]').click();assert.equal(await page.locator('#modal [data-key="section"]').count(),0);assert.equal(await page.locator('#modal [data-line="0"] [data-product-search]').count(),1);await page.locator('#modal [data-action=close]').first().click();
 await page.goto(base+'/#settings');const download=page.waitForEvent('download');await page.locator('.template-downloads a[download]').first().click();assert.ok((await download).suggestedFilename().endsWith('.pdf'));
 assert.deepEqual(errors,[]);console.log('PASS 1440px: desktop routes, line item search, account preview and non-modal guide navigation');
}finally{await browser?.close();server.kill();await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));rmSync(dataDir,{recursive:true,force:true});}
