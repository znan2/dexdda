/* Offline only: asset switching, dry-run confirmation and balance cards. */
const assert=require("node:assert/strict"),path=require("node:path"),fs=require("node:fs"),net=require("node:net");
const {spawn}=require("node:child_process"),{chromium}=require("playwright");
const root=path.resolve(__dirname,".."),sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,"127.0.0.1",r));
 const port=socket.address().port;await new Promise(r=>socket.close(r));const url="http://127.0.0.1:"+port;
 const child=spawn(path.join(root,".venv/bin/python"),["-m","uvicorn","assets_ui_server:app","--app-dir","tests","--host","127.0.0.1","--port",String(port),"--no-access-log","--log-level","critical"],{cwd:root,stdio:"ignore"});
 let browser,page;
 try{
  let ready=false;for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error("fixture exited");try{if((await fetch(url+"/api/health")).ok){ready=true;break;}}catch{}await sleep(100);}assert(ready);
  browser=await chromium.launch({channel:"chrome",headless:true});page=await browser.newPage({viewport:{width:1440,height:1080}});
  let requests=0;page.on("request",r=>{if(new URL(r.url()).pathname==="/api/detail")requests++;});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.addInitScript(()=>{window.addEventListener("dexdda:detail",e=>window.__detail=e.detail);window.addEventListener("dexdda:detail-invalidated",()=>window.__detail=null);});
  await page.goto(url);await page.waitForFunction(()=>document.querySelectorAll(".balance-card").length===5);
  assert.equal(await page.locator('.balance-card[data-chain="4663"] .balance-label').textContent(),"USDG");
  assert.equal(await page.locator(".balance-row strong").first().textContent(),"123.45");
  const out=path.join(root,"docs/screenshots");fs.mkdirSync(out,{recursive:true});
  await page.screenshot({path:path.join(out,"assets-balances-desktop.png"),fullPage:true});
  const open=async(id,symbol)=>{const before=requests;await page.locator('tr.coin-representative[data-coin-id="'+id+'"] .asset-select').click();await page.waitForFunction(()=>!document.querySelector("#detail-refresh").disabled);await sleep(800);assert.equal(requests,before,"opening/reopening must stay idle");await page.click("#detail-refresh");await page.waitForFunction(s=>window.__detail?.quote.purchase_symbol===s,symbol);};
  await open("alpha","USDT");
  const baseline=await page.locator(".detail-result strong").textContent();
  await page.selectOption("#detail-asset","USDC");
  await sleep(800);assert.equal(requests,1,"asset edit must not requote");assert(await page.locator("#detail-swap").isDisabled());
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>window.__detail?.quote.purchase_symbol==="USDC");
  assert.equal(await page.locator(".detail-result strong").textContent(),baseline);
  assert.equal(await page.evaluate(()=>window.__detail.request.purchase_symbol),"USDC");
  assert.match(await page.locator("#detail-content").textContent(),/USDC · 1,000/);
  await page.click("#detail-swap");await page.waitForFunction(()=>!document.querySelector("#swap-confirm").hidden);
  assert.match(await page.locator("#swap-content").textContent(),/1000 USDC/);
  assert.match(await page.locator("#swap-content").textContent(),/5000 USDC/);
  assert((await page.locator("#swap-content").textContent()).includes("0x"+"e".repeat(40)));
  await page.click("#swap-confirm");await page.waitForFunction(()=>document.querySelector("#swap-status").textContent.includes("DRY_RUN 완료"));
  await page.click("#swap-close");
  // A slow old-asset response must not overwrite a newer selection.
  let oldStarted=false;
  await page.route("**/api/detail",async route=>{const response=await route.fetch();if(route.request().postDataJSON().purchase_symbol==="USDT"){oldStarted=true;await sleep(900);}await route.fulfill({response});});
  await page.selectOption("#detail-asset","USDT");
  await page.click("#detail-refresh");
  for(let i=0;i<30&&!oldStarted;i++)await sleep(50);assert(oldStarted);
  await page.selectOption("#detail-asset","USDC");
  const before=requests;
  await page.waitForFunction(()=>!document.querySelector("#detail-refresh").disabled);
  assert.equal(requests,before,"in-flight asset edit must not auto-retry");
  assert.equal(await page.evaluate(()=>window.__detail),null);
  await page.click("#detail-refresh");
  await page.waitForFunction(()=>window.__detail?.quote.purchase_symbol==="USDC");
  assert.equal(await page.inputValue("#detail-asset"),"USDC");
  await page.unroute("**/api/detail");await page.click("#detail-close");
  await open("cap","USDC");assert.match(await page.locator(".detail-result").textContent(),/가격 영향/);assert(await page.locator("#detail-swap").isDisabled());
  await page.click("#detail-close");await open("hood","USDG");
  assert.equal(await page.inputValue("#detail-asset"),"USDG");assert.match(await page.locator("#detail-content").textContent(),/USDG · 1,000/);
  await page.setViewportSize({width:390,height:844});assert(await page.locator("#detail-panel").evaluate(n=>n.scrollWidth<=n.clientWidth));
  await page.screenshot({path:path.join(out,"assets-usdg-mobile.png")});await page.click("#detail-close");
  // Refresh failure retains values and their original observation age.
  await page.route("**/api/balances/refresh",async route=>{const r=await route.fetch(),d=await r.json();for(const chain of d.chains)for(const token of chain.tokens){token.stale=true;token.error="rate_limited";token.observed_at_ms=Date.now()-3600000;}await route.fulfill({json:d});});
  await page.click("#balance-refresh");await page.waitForFunction(()=>document.querySelector(".balance-error")?.textContent.includes("1시간 전"));
  assert.equal(await page.locator(".balance-row strong").first().textContent(),"123.45");
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,"assets-balances-mobile.png"),fullPage:true});
  assert.deepEqual(errors,[]);console.log(JSON.stringify({assetSelection:"passed",dryRun:"passed",assetRace:"passed",bridgeUsdG:"passed",balanceCards:"passed",staleBalances:"passed",consoleErrors:0}));
 }catch(e){if(page)await page.screenshot({path:path.join(root,"docs/screenshots/assets-failure.png")}).catch(()=>{});throw e;}
 finally{if(browser)await browser.close();child.kill("SIGTERM");await Promise.race([new Promise(r=>child.once("exit",r)),sleep(3000)]);if(child.exitCode===null)child.kill("SIGKILL");}
})().catch(e=>{console.error(e);process.exitCode=1;});
