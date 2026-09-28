/* End-to-end M5–M7 against entirely synthetic providers. No .env or live transactions. */
const assert=require("node:assert/strict"),path=require("node:path"),fs=require("node:fs"),net=require("node:net");
const {spawn}=require("node:child_process"),{chromium}=require("playwright");
const root=path.resolve(__dirname,".."),sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,"127.0.0.1",r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 const url="http://127.0.0.1:"+port;
 const child=spawn(path.join(root,".venv/bin/python"),["-m","uvicorn","execution_ui_server:app","--app-dir","tests","--host","127.0.0.1","--port",String(port),"--no-access-log","--log-level","critical"],{cwd:root,stdio:"ignore"});
 let browser,page;
 try{
  let ready=false;for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error("fixture exited");try{if((await fetch(url+"/api/runtime")).ok){ready=true;break;}}catch{}await sleep(100);}
  assert(ready);browser=await chromium.launch({channel:"chrome",headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1080},permissions:["clipboard-read","clipboard-write"]});
  page=await context.newPage();const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto(url);
  await page.waitForFunction(()=>document.querySelector("#operations-notice").textContent.includes("KRW-NEW"));
  assert.equal(await page.locator("#reference-premium").count(),0);
  assert.doesNotMatch(await page.locator("#operations-notice").textContent(),/KRW-HELD|재빌드/);
  assert.equal(await page.locator("#registry-status").getAttribute("open"),null);
  await page.locator("#registry-status summary").click();
  assert.match(await page.locator("#registry-pending-list").textContent(),/KRW-HELD.*코인 ID 연결 정보 없음/);
  let shortQuote=true;
  await page.route("**/api/detail",async route=>{
    const data=await (await route.fetch()).json();
    if(shortQuote)data.expires_at_ms=data.generated_at_ms+2000;
    await route.fulfill({json:data});
  });
  await page.locator('[data-coin-id="alpha"] .asset-select').click();
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>!document.querySelector("#detail-swap").disabled);
  const retainedResult=await page.locator(".detail-result strong").textContent();
  await page.waitForFunction(()=>document.querySelector("#detail-swap").disabled);
  assert.notEqual(retainedResult,"—");
  assert.equal(await page.locator(".detail-result strong").textContent(),retainedResult,"expired display stays readable");
  assert.match(await page.locator("#detail-expiry").textContent(),/전 갱신/);
  shortQuote=false;await page.click("#detail-refresh");
  await page.waitForFunction(()=>!document.querySelector("#detail-swap").disabled);
  await page.click("#detail-swap");
  await page.waitForFunction(()=>!document.querySelector("#swap-confirm").hidden);
  assert.match(await page.locator("#swap-content").textContent(),/Ethereum.*5000/s);
  let history=await (await fetch(url+"/api/swaps")).json();
  assert(history.items.some(e=>e.status==="awaiting_confirmation"));
  assert.equal(history.items.filter(e=>e.status==="dry_run_complete").length,0,"opening modal never executes");
  const out=path.join(root,"docs/screenshots");fs.mkdirSync(out,{recursive:true});
  await page.screenshot({path:path.join(out,"m5-confirm-desktop.png")});
  await page.click("#swap-confirm");
  await page.waitForFunction(()=>document.querySelector("#swap-status").textContent.includes("DRY_RUN 완료"));
  assert.match(await page.locator("#swap-content").textContent(),/전송하지 않은 트랜잭션 계획/);
  history=await (await fetch(url+"/api/swaps")).json();
  const dry=history.items.find(e=>e.status==="dry_run_complete");assert(dry);assert.deepEqual(dry.transactions,[]);
  await page.screenshot({path:path.join(out,"m5-dry-run.png")});
  await page.click("#swap-close");await page.click("#detail-close");
  await page.locator("#history-section summary").click();
  await page.locator(".history-entry").filter({hasText:"스왑 성공"}).click();
  await page.waitForFunction(()=>document.querySelectorAll(".deposit-card").length===2);
  assert.match(await page.locator(".actual-received").textContent(),/999.5/);
  await page.getByRole("button",{name:"입금 주소 복사",exact:true}).first().click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),"0x"+"a".repeat(40));
  assert.match(await page.locator(".copy-verification").first().textContent(),/0xaaaaaa … aaaaaa/);
  await page.getByRole("button",{name:"메모·태그 복사",exact:true}).first().click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),"123456");
  await page.locator("#swap-dialog").evaluate(el=>el.scrollTop=el.scrollHeight);
  await page.screenshot({path:path.join(out,"m6-completion-desktop.png")});
  await page.setViewportSize({width:390,height:844});
  assert(await page.locator("#swap-dialog").evaluate(el=>el.scrollWidth<=el.clientWidth));
  await page.screenshot({path:path.join(out,"m6-completion-mobile.png")});
  await page.locator("#swap-dialog").evaluate(el=>el.scrollTop=0);
  await page.keyboard.press("Escape");
  await page.setViewportSize({width:1440,height:1080});
  await page.locator('[data-coin-id="alpha"] .asset-select').click();
  await page.fill("#detail-amount","1001");
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>!document.querySelector("#detail-swap").disabled);
  await page.click("#detail-swap");
  await page.waitForFunction(()=>document.querySelector("#swap-status").textContent.includes("최대"));
  assert.equal(await page.locator("#swap-confirm").isHidden(),true);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({fixtureChecks:"passed",dryRunTransactions:0,completionAddresses:2,clipboard:"verified",consoleErrors:errors.length}));
 }catch(error){
  if(page)await page.screenshot({path:path.join(root,"docs/screenshots/m5-test-failure.png")}).catch(()=>{});
  throw error;
 }finally{
  if(browser)await browser.close();child.kill("SIGTERM");
  await Promise.race([new Promise(r=>child.once("exit",r)),sleep(5000)]);if(child.exitCode===null)child.kill("SIGKILL");
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
