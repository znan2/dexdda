/* CAP comparison and deposit-stopped reference quote. Synthetic providers only. */
const assert=require("node:assert/strict"),path=require("node:path"),fs=require("node:fs"),net=require("node:net");
const {spawn}=require("node:child_process"),{chromium}=require("playwright");
const root=path.resolve(__dirname,".."),sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,"127.0.0.1",r));
 const port=socket.address().port;await new Promise(r=>socket.close(r));const url="http://127.0.0.1:"+port;
 const child=spawn(path.join(root,".venv/bin/python"),["-m","uvicorn","feedback_ui_server:app","--app-dir","tests","--host","127.0.0.1","--port",String(port),"--no-access-log","--log-level","critical"],{cwd:root,stdio:"ignore"});
 let browser,page;
 try{
  let ready=false;for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error("fixture exited");try{if((await fetch(url+"/api/health")).ok){ready=true;break;}}catch{}await sleep(100);}
  assert(ready);browser=await chromium.launch({channel:"chrome",headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1080},permissions:["clipboard-read","clipboard-write"]});const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.addInitScript(()=>window.addEventListener("dexdda:detail",e=>{window.__detailExpired=e.detail.expired;}));
  await page.goto(url);await page.waitForFunction(()=>document.querySelector("#main-count").textContent==="1");
  assert.equal(await page.locator(".liquidity-toggle,#liquidity-filter-note").count(),0);
  const rep=page.locator('tr.coin-representative[data-coin-id="cap"]');
  assert.equal(await rep.getAttribute("data-chain"),"56");
  assert.match(await rep.textContent(),/브릿지 확인 필요/);
  assert.match(await rep.textContent(),/비용·시간 미반영/);
  assert.equal(await page.locator('tr[data-coin-id="dust"]').count(),0);
  assert.equal(await page.inputValue("#liquidity-min"),"1.0");
  await rep.locator(".ca-copy").click();
  const bscCA=await rep.getAttribute("data-address");
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),bscCA);
  assert.equal(await page.locator("#detail-panel").isVisible(),false);
  await page.click("#blocked-tab");
  assert.match(await page.locator('.coin-representative[data-coin-id="snx"] .liquidity-value').textContent(),/\$7.7m/);
  assert.match(await page.locator('.coin-representative[data-coin-id="snx"] .liquidity-value').getAttribute("title"),/7732863.5533/);
  assert.equal(await page.locator("#liquidity-min").getAttribute("min"),"0.1");
  for(const valid of ["0.1","0.5","0.9"]){
    await page.fill("#liquidity-min",valid);
    assert.equal(await page.locator("#liquidity-min").getAttribute("aria-invalid"),"false");
    assert.equal(await page.locator("#main-visible").textContent(),"1");
  }
  await page.fill("#liquidity-min","0.5");await page.reload();await page.click("#blocked-tab");
  await page.waitForFunction(()=>document.querySelector("#main-visible").textContent==="1");
  assert.equal(await page.inputValue("#liquidity-min"),"0.5");
  const ages=await page.evaluate(()=>{
    const now=1800000000000;
    return [0,59999,60000,59*60000,3600000,23*3600000,86400000,3*86400000].map(age=>window.DexddaUI.observedTime(now-age,now));
  });
  assert.deepEqual(ages,["방금 전","방금 전","1분 전","59분 전","1시간 전","23시간 전","1일 전","3일 전"]);
  await page.fill("#liquidity-min","1.1");await page.reload();await page.click("#blocked-tab");
  await page.waitForFunction(()=>document.querySelector("#main-visible").textContent==="1");
  assert.equal(await page.inputValue("#liquidity-min"),"1.1");
  await page.fill("#liquidity-min","7.7");assert.equal(await page.locator("#main-visible").textContent(),"1");
  await page.fill("#liquidity-min","7.8");assert.equal(await page.locator("#main-visible").textContent(),"0");
  for(const invalid of ["", "0", "0.0", "0.01", "-0.1", "10.1", "1.11"]){
    await page.fill("#liquidity-min",invalid);
    assert.equal(await page.locator("#liquidity-min").getAttribute("aria-invalid"),"true");
    assert.match(await page.locator("#liquidity-input-note").textContent(),/7.8m/);
  }
  await page.fill("#liquidity-min","10.0");assert.equal(await page.locator("#liquidity-min").getAttribute("aria-invalid"),"false");
  await page.fill("#liquidity-min","1.0");await page.click("#candidates-tab");
  await rep.locator(".route-toggle").click();
  assert.equal(await page.locator('.route-alternative[data-coin-id="cap"][data-chain="1"]').count(),0);
  assert.equal(await page.locator('.route-alternative[data-coin-id="cap"][data-chain="56"]').count(),1);
  await sleep(1200);assert(await page.locator(".route-alternative").count()>0);
  await page.fill("#search","BNB");assert.equal(await page.locator("#main-body .coin-representative").count(),1);
  await page.fill("#search","");
  const out=path.join(root,"docs/screenshots");fs.mkdirSync(out,{recursive:true});
  await page.screenshot({path:path.join(out,"feedback-comparison-desktop.png"),fullPage:true});
  // Bridge routes show price impact without an empty effective-gap or gas/deposit panel.
  await rep.locator(".asset-select").click();
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector(".detail-result strong")?.textContent==="-0.20%");
  assert.match(await page.locator(".detail-result").textContent(),/가격 영향 · OKX 견적 기준/);
  assert.equal(await page.locator("#detail-content .detail-section h3").allTextContents().then(x=>x.join("|")),"금액별 스왑 견적");
  assert(!(await page.locator("#detail-content").textContent()).includes("실효갭"));
  assert(await page.locator("#detail-swap").isDisabled());
  await page.screenshot({path:path.join(out,"bridge-impact-desktop.png")});
  await page.fill("#detail-amount","100");
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector(".detail-result strong")?.textContent==="-0.20%");
  assert.match(await page.locator(".detail-values").textContent(),/USDT · 100/);
  await page.waitForFunction(()=>window.__detailExpired===true);
  assert.equal(await page.locator(".detail-result strong").textContent(),"-0.20%");
  assert.match(await page.locator("#detail-expiry").textContent(),/전 갱신/);
  await page.setViewportSize({width:390,height:844});
  assert(await page.locator("#detail-panel").evaluate(el=>el.scrollWidth<=el.clientWidth));
  await page.screenshot({path:path.join(out,"bridge-impact-mobile.png")});
  await page.route("**/api/detail",async route=>{
    const response=await route.fetch(),data=await response.json();
    data.quote=null;data.errors=[{stage:"quote",code:"rate_limited",message:"호출 제한 · 나중에 갱신하세요."}];
    await route.fulfill({json:data});
  });
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector("#detail-status").textContent.includes("호출 제한"));
  assert.equal(await page.locator(".detail-result strong").textContent(),"—");
  await page.unroute("**/api/detail");
  await page.fill("#detail-amount","1000");
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector(".detail-result strong")?.textContent==="-0.20%");
  await page.keyboard.press("Escape");
  await page.setViewportSize({width:1440,height:1080});
  await page.click("#blocked-tab");
  await page.locator('.coin-representative[data-coin-id="snx"] .asset-select').click();
  await page.selectOption("#detail-exchange","bithumb");
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector(".detail-result.reference strong")?.textContent!=="—"&&document.querySelector(".detail-result.reference strong"));
  const snxCA=await page.locator('.coin-representative[data-coin-id="snx"]').getAttribute("data-address");
  await page.click("#detail-copy-ca");
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),snxCA);
  assert.match(await page.locator("#detail-copy-status").textContent(),/복사 완료/);
  await page.evaluate(()=>{navigator.clipboard.writeText=async()=>{throw Error("denied");};});
  await page.click("#detail-copy-ca");
  assert.match(await page.locator("#detail-copy-status").textContent(),/자동 복사 실패/);
  assert((await page.locator("#detail-copy-status").textContent()).includes(snxCA));
  assert.match(await page.locator("#detail-status").textContent(),/입금 중단/);
  assert.match(await page.locator(".detail-result").textContent(),/입금 가능 가정/);
  assert(await page.locator("#detail-swap").isDisabled());
  assert.match(await page.locator("#detail-expiry").textContent(),/전 갱신/);
  const lastReference=await page.locator(".detail-result strong").textContent();
  await page.screenshot({path:path.join(out,"feedback-reference-desktop.png")});
  await page.waitForFunction(()=>window.__detailExpired===true);
  assert.match(await page.locator("#detail-status").textContent(),/입금 중단/,"expiry never masks the primary reason");
  assert.equal(await page.locator(".detail-result strong").textContent(),lastReference);
  assert(await page.locator("#detail-swap").isDisabled());
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await page.locator("#detail-panel").evaluate(el=>el.scrollWidth<=el.clientWidth));
  await page.screenshot({path:path.join(out,"feedback-reference-mobile.png")});
  await page.keyboard.press("Escape");
  await page.screenshot({path:path.join(out,"feedback-comparison-mobile.png"),fullPage:true});
  // A provider error before the first quote must not be presented as only expiration.
  await page.route("**/api/detail",async route=>{
    const response=await route.fetch(),data=await response.json();
    data.quote=null;data.result=null;data.reference_result=null;data.warnings=[];
    data.errors=[{stage:"quote",code:"rate_limited",message:"호출 제한 · 나중에 갱신하세요."}];
    data.deposit.wallet_state="working";data.expires_at_ms=data.generated_at_ms;
    await route.fulfill({json:data});
  });
  await page.locator('.coin-representative[data-coin-id="snx"] .asset-select').click();
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>document.querySelector("#detail-status").textContent.includes("호출 제한"));
  assert.match(await page.locator("#detail-expiry").textContent(),/견적 없음/);
  // Discovery keeps last observed liquidity, while unknown routes expose specific causes.
  await page.keyboard.press("Escape");
  await page.setViewportSize({width:1440,height:1080});
  const observedAt=Date.now()-2*24*60*60*1000;
  await page.route("**/api/gaps",async route=>{
    const response=await route.fetch(),snapshot=await response.json();
    for(const group of snapshot.coin_groups){
      if(group.coin_id==="snx"){
        group.liquidity_reused=true;
        for(const row of group.routes){
          Object.assign(row.liquidity_usd,{stale:true,usable:true,reused:true,received_at_ms:observedAt,source_at_ms:observedAt-1000,last_attempt_at_ms:Date.now(),error:"rate_limited",error_message:"API 호출 제한"});
          row.warnings.push("liquidity_cached");
        }
      }else{
        group.liquidity_comparison_complete=false;
        for(const [i,row] of group.routes.entries())Object.assign(row.liquidity_usd,{value:null,stale:true,usable:false,reused:false,received_at_ms:null,source_at_ms:null,last_attempt_at_ms:Date.now(),error:i?"value_missing":"token_not_returned",error_message:i?"API 응답에 유동성 값이 없음":"API 응답에 해당 토큰이 없음"});
      }
    }
    await route.fulfill({json:snapshot});
  });
  await page.reload();
  await page.click("#blocked-tab");
  assert.equal(await page.locator('.coin-representative[data-coin-id="cap"]').count(),0,"unknown liquidity hidden by default");
  const reusedRow=page.locator('.coin-representative[data-coin-id="snx"]');
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="snx"] .liquidity-info')?.textContent.includes("마지막 정상값 재사용"));
  assert.match(await reusedRow.locator(".liquidity-value").textContent(),/\$7.7m/);
  assert.match(await reusedRow.locator(".liquidity-info").textContent(),/API 호출 제한/);
  const dateText="2일 전";
  assert((await reusedRow.locator(".liquidity-info").textContent()).includes(dateText));
  assert.equal(await reusedRow.getAttribute("data-chain"),"1");
  await page.fill("#liquidity-min","7.8");
  assert.equal(await reusedRow.count(),0,"cached liquidity still obeys selected threshold");
  await page.fill("#liquidity-min","1.0");
  assert.equal(await page.locator('tr[data-coin-id="cap"],tr[data-coin-id="dust"]').count(),0);
  await page.locator(".table-scroll").evaluateAll(nodes=>nodes.forEach(n=>{n.scrollTop=0;n.scrollLeft=0;}));
  await page.screenshot({path:path.join(out,"liquidity-reference-desktop.png"),fullPage:true});
  await reusedRow.locator(".asset-select").click();
  assert.match(await page.locator("#detail-liquidity").textContent(),/마지막 정상값 재사용/);
  assert((await page.locator("#detail-liquidity").textContent()).includes(dateText));
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await page.locator("#detail-panel").evaluate(el=>el.scrollWidth<=el.clientWidth));
  await page.keyboard.press("Escape");
  await page.screenshot({path:path.join(out,"liquidity-reference-mobile.png"),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({feedbackChecks:"passed",coinComparison:"verified",depositTabs:"verified",blockedReference:"verified",expiryReason:"preserved",caCopy:"verified",compactLiquidity:"verified",millionFilter:"verified",liquidityReuse:"verified",liquidityReasons:"verified",relativeTime:"verified",minimumPointOne:"verified",consoleErrors:0}));
 }catch(error){
  if(page)await page.screenshot({path:path.join(root,"docs/screenshots/feedback-failure.png")}).catch(()=>{});
  throw error;
 }finally{
  if(browser)await browser.close();child.kill("SIGTERM");
  await Promise.race([new Promise(r=>child.once("exit",r)),sleep(5000)]);if(child.exitCode===null)child.kill("SIGKILL");
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
