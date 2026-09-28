/* M4 browser integration checks; launch an isolated local fixture or read-only live app. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const root = path.resolve(__dirname, "..");
const live = process.argv.includes("--live");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const s=net.createServer(); await new Promise(r=>s.listen(0,"127.0.0.1",r));
  const p=s.address().port; await new Promise(r=>s.close(r)); return p;
}
(async()=>{
  const port=await freePort(), url="http://127.0.0.1:"+port;
  const args=["-m","uvicorn",live?"app.main:app":"detail_ui_server:app","--host","127.0.0.1","--port",String(port),"--no-access-log","--log-level","critical"];
  if(!live) args.push("--app-dir","tests");
  const child=spawn(path.join(root,".venv/bin/python"),args,{cwd:root,stdio:"ignore"});
  let browser;
  try {
    let ready=false;
    for(let i=0;i<100;i++) {
      if(child.exitCode!==null) throw Error("server exited");
      try{if((await fetch(url+"/api/health")).ok){ready=true;break;}}catch{}
      await sleep(100);
    }
    assert(ready);
    browser=await chromium.launch({channel:"chrome",headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1080}});
    let detailRequests=0;page.on("request",r=>{if(new URL(r.url()).pathname==="/api/detail")detailRequests++;});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    await page.addInitScript(()=>window.addEventListener("dexdda:detail",e=>{window.__detailExpired=e.detail.expired;}));
  await page.goto(url);
    await page.waitForFunction(()=>document.querySelector("#main-count").textContent!=="—");
    const out=path.join(root,"docs/screenshots");fs.mkdirSync(out,{recursive:true});
    const complete=()=>page.waitForFunction(()=>document.querySelector("#detail-content .detail-result strong"));
    const open=async id=>{await page.locator('#main-body tr[data-coin-id="'+id+'"] .asset-select').first().click();await page.click("#detail-refresh");await complete();};
    if(live){
      const locator=page.locator('#main-body tr[data-coin-id="ethereum"][data-chain="1"] .asset-select');
      await locator.click();
      await page.click("#detail-refresh");
      await complete();
      await page.waitForFunction(()=>!document.querySelector("#detail-refresh").disabled);
      assert.equal(await page.locator("#detail-swap").isDisabled(),true);
      assert.match(await page.locator("#detail-content").textContent(),/컨펌|미확인/);
      await page.screenshot({path:path.join(out,"m4-live-desktop.png"),fullPage:false});
      assert.notEqual(await page.locator(".detail-result strong").textContent(),"—","live ETH result is fresh and complete");
      const text=await page.locator("#detail-content").textContent();
      assert(!/sentinel-|PRIVATE_KEY|OK-ACCESS-KEY/.test(text));
      console.log(JSON.stringify({live:true, result:await page.locator(".detail-result strong").textContent(),consoleErrors:errors.length}));
    }else{
      await page.locator('#main-body tr[data-coin-id="zero"] .asset-select').first().click();
      await page.waitForFunction(()=>!document.querySelector("#detail-refresh").disabled);
      await sleep(800);
      assert.equal(detailRequests,0,"opening detail must not request a quote");
      assert.match(await page.locator("#detail-status").textContent(),/견적 갱신을 눌러/);
      await page.click("#detail-refresh");await complete();
      assert.equal(detailRequests,1,"one click sends one quote request");
      assert.match(await page.locator(".detail-result strong").textContent(),/9.867%/);
      assert.match(await page.locator("#detail-content").textContent(),/Fixture DEX/);
      assert.equal(await page.locator("#detail-swap").isDisabled(),true);
      await page.screenshot({path:path.join(out,"m4-fixture-desktop.png"),fullPage:false});
      await page.fill("#detail-amount","2000");
      await sleep(800);assert.equal(detailRequests,1,"amount edit does not requote");
      assert.equal(await page.locator(".detail-result").count(),0);
      await page.click("#detail-refresh");
      await complete();
      await page.waitForFunction(()=>document.querySelector(".detail-result strong").textContent.includes("4.93"));
      await page.selectOption("#detail-exchange","bithumb");
      await sleep(800);assert.equal(detailRequests,2,"exchange edit does not requote");
      await page.click("#detail-refresh");
      await complete();
      assert.match(await page.locator("#detail-content").textContent(),/API 미제공/);
      assert.match(await page.locator(".detail-result strong").textContent(),/9.93/);
      await page.fill("#detail-amount","NaN");
      assert.equal(await page.locator(".detail-result").count(),0);
      assert.match(await page.locator("#detail-status").textContent(),/0보다 크고/);
      await page.fill("#detail-amount","1000");
      await page.click("#detail-refresh");
      await complete();
      // A newer amount must win even if an old response completes later.
      let calls=0;
      await page.route("**/api/detail",async route=>{
        calls++;
        const response=await route.fetch();
        if(calls===1) await sleep(1000);
        await route.fulfill({response});
      });
      await page.fill("#detail-amount","1500");await page.click("#detail-refresh");await sleep(300);
      await page.fill("#detail-amount","2000");
      await page.waitForFunction(()=>!document.querySelector("#detail-refresh").disabled);
      assert.equal(calls,1,"editing an in-flight quote must not queue another request");
      assert.equal(await page.locator(".detail-result").count(),0,"old response must be discarded");
      await page.click("#detail-refresh");
      await complete();
      await page.waitForFunction(()=>document.querySelector(".detail-result strong").textContent.includes("9.93"));
      assert.equal(await page.inputValue("#detail-amount"),"2000");
      await page.unroute("**/api/detail");
      const lastResult=await page.locator(".detail-result strong").textContent();
      await page.waitForFunction(()=>window.__detailExpired===true);
      assert.equal(await page.locator(".detail-result strong").textContent(),lastResult);
      assert.notEqual(lastResult,"—");
      assert.match(await page.locator("#detail-expiry").textContent(),/\d+초 전 갱신/);
      assert(await page.locator("#detail-swap").isDisabled());
      await page.click("#detail-refresh");await complete();
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert(await page.locator("#detail-panel").evaluate(el=>el.scrollWidth<=el.clientWidth));
      await page.screenshot({path:path.join(out,"m4-fixture-mobile.png"),fullPage:false});
      await page.locator("#detail-panel").evaluate(el=>el.scrollTop=el.scrollHeight);
      await page.screenshot({path:path.join(out,"m4-fixture-deposit.png"),fullPage:false});
      await page.locator("#detail-panel").evaluate(el=>el.scrollTop=0);
      await page.click("#detail-close");assert.equal(await page.locator("#detail-panel").isVisible(),false);
      await page.setViewportSize({width:1440,height:1080});
      assert.equal(await page.locator('[data-coin-id="pending"]').count(),0,"unobserved liquidity is excluded");
      // Keyboard activation and a sanitized service failure.
      await page.route("**/api/detail",route=>route.fulfill({status:429,json:{error:"detail_busy"}}));
      await page.locator('[data-coin-id="zero"] .asset-select').press("Enter");
      await page.click("#detail-refresh");
      await page.waitForFunction(()=>document.querySelector("#detail-status").textContent.includes("이전 견적"));
      assert.equal(await page.locator(".detail-result").count(),0);
      await page.unroute("**/api/detail");
      await page.click("#detail-refresh");await complete();
      await page.keyboard.press("Escape");
      console.log(JSON.stringify({fixtureChecks:"passed",consoleErrors:errors.length,screenshots:3}));
    }
    assert.deepEqual(errors,[]);
  } finally {
    if(browser) await browser.close();
    child.kill("SIGTERM");
    await Promise.race([new Promise(r=>child.once("exit",r)),sleep(5000)]);
    if(child.exitCode===null) child.kill("SIGKILL");
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
