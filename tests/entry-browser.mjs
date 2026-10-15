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

 browser=await chromium.launch({headless:true});
 console.log('browser ready');
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.setDefaultTimeout(10000);await page.goto(base+'/#quotes');console.log('quotes loaded');
 await page.locator('[data-action="wf-quote-new"]').click();
 await page.locator('#modal [name="title"]').fill('임시 견적 복원 검사');
 await page.locator('#modal [data-key="name"]').first().fill('테스트 품목');
 await page.locator('#modal [data-key="unitPrice"]').first().fill('12000');
 await page.keyboard.press('Control+V');
 assert.equal(await page.locator('#modal').evaluate(el=>el.open),true);
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#modal').evaluate(el=>el.open),true);
 await page.mouse.click(2,2);
 assert.equal(await page.locator('#modal').evaluate(el=>el.open),true);
 await page.locator('#modal [data-action="close"]').first().click();
 await page.locator('[data-action="wf-quote-new"]').click();
 assert.equal(await page.locator('#modal [name="title"]').inputValue(),'임시 견적 복원 검사');
 assert.equal(await page.locator('#modal [data-key="unitPrice"]').first().inputValue(),'12000');
 await page.reload();await page.locator('[data-action="wf-quote-new"]').click();
 assert.equal(await page.locator('#modal [name="title"]').inputValue(),'임시 견적 복원 검사');
 await page.locator('[data-lookup-new]').click();
 await page.locator('[data-quick-name]').fill('입력창 신규 고객');
 await page.locator('[data-quick-save]').click();
 await page.locator('[data-quick-account]').waitFor({state:'detached'});
 assert.equal(await page.locator('#modal [data-lookup-input]').inputValue(),'입력창 신규 고객');
 const newId=await page.locator('#modal [name="accountId"]').inputValue();assert.ok(newId);
 await page.locator('#modal [type="submit"]').click();await page.locator('#modal').waitFor({state:'hidden'});
 assert.equal((await state()).state.quotes.at(-1).accountId,newId);
 await page.locator('[data-action="wf-quote-new"]').click();assert.equal(await page.locator('#modal [name="title"]').inputValue(),'');
 // Another tab changes the shared ledger; focus refresh must keep the unsaved form.
 await page.locator('#modal [name="title"]').fill('탭 이동 중 보존');
 const second=await context.newPage();await second.goto(base+'/#accounts');
 await second.locator('[data-action="erp-account-new"]').click();
 await second.locator('#modal [name="name"]').fill('다른 탭 신규 고객');
 await second.locator('#modal [type="submit"]').click();await second.locator('#modal').waitFor({state:'hidden'});
 await page.bringToFront();await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await page.waitForTimeout(300);
 await page.locator('#modal [data-lookup-input]').fill('다른 탭 신규 고객');
 await page.getByRole('option').filter({hasText:'다른 탭 신규 고객'}).click();
 assert.equal(await page.locator('#modal [name="title"]').inputValue(),'탭 이동 중 보존');
 await page.locator('#modal [data-action="close"]').first().click();
 await page.goto(base+'/#transactions');await page.locator('[data-action="erp-purchase-new"]').click();
 await page.locator('#modal [name="title"]').fill('매입 복원 검사');
 await page.locator('#modal [data-key="name"]').first().fill('매입 품목');
 await page.locator('#modal [data-key="quantity"]').first().fill('3');
 await page.locator('#modal [data-add-line]').click();
 await page.locator('#modal [data-key="name"]').nth(1).fill('두 번째 품목');
 await page.reload();await page.locator('[data-action="erp-purchase-new"]').click();
 assert.equal(await page.locator('#modal [name="title"]').inputValue(),'매입 복원 검사');
 assert.equal(await page.locator('#modal [data-key="name"]').count(),2);
 assert.equal(await page.locator('#modal [data-key="quantity"]').first().inputValue(),'3');
 assert.equal(await page.locator('#modal [data-key="name"]').nth(1).inputValue(),'두 번째 품목');
 assert.deepEqual(errors,[]);console.log('PASS: paste, backdrop, Escape, draft reopen/reload/clear, inline account, cross-tab lookup, purchase lines');
}finally{await browser?.close();server.kill();rmSync(dataDir,{recursive:true,force:true});}
