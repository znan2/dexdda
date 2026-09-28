/* Offline end-to-end filtering, live wallet refresh and saved exclusions. */
const assert=require("node:assert/strict"),path=require("node:path"),fs=require("node:fs"),net=require("node:net");
const {spawn}=require("node:child_process"),{chromium}=require("playwright");
const root=path.resolve(__dirname,".."),sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,"127.0.0.1",r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 const url="http://127.0.0.1:"+port;
 const child=spawn(path.join(root,".venv/bin/python"),["-m","uvicorn","controls_ui_server:app","--app-dir","tests","--host","127.0.0.1","--port",String(port),"--no-access-log","--log-level","critical"],{cwd:root,stdio:"ignore"});
 let browser,page;const out=path.join(root,"docs/screenshots");
 try{
  let ready=false;for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error("fixture exited");try{if((await fetch(url+"/api/health")).ok){ready=true;break;}}catch{}await sleep(100);}assert(ready);
  browser=await chromium.launch({channel:"chrome",headless:true});page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));await page.goto(url);
  const row=id=>page.locator('.coin-representative[data-coin-id="'+id+'"]');
  await page.waitForFunction(()=>document.querySelector("#main-count").textContent==="4");
  assert.equal(await row("pending").count(),0);assert.equal(await row("halted").count(),0);
  assert.equal(await page.locator("#reference-premium,.wallet-toolbar").count(),0);
  assert.equal(await page.locator(".sync #wallet-refresh").count(),1);
  assert.equal(await page.locator("#updated").textContent(),"방금 전");
  assert.equal(await page.locator("#wallet-status-summary").isVisible(),false);
  assert.equal(await row("outgoing").count(),1);assert.equal(await row("cached").count(),1);
  assert.match(await row("cached").textContent(),/마지막 정상값 재사용/);
  assert.equal(await page.locator(".liquidity-toggle,#liquidity-filter-note").count(),0);
  assert.equal(await page.locator("#liquidity-input-note").isVisible(),false);
  
  await page.click("#blocked-tab");assert.equal(await row("halted").count(),1);
  await page.click("#candidates-tab");
  await page.click("#wallet-refresh");
  await page.waitForFunction(()=>!document.querySelector('.coin-representative[data-coin-id="fet"]'));
  await page.click("#blocked-tab");assert.match(await row("fet").textContent(),/입출금 중단/);
  await page.click("#candidates-tab");
  assert.equal(await page.locator(".network-hide").count(),0);
  await page.locator("#exclusions-panel summary").click();
  await page.fill("#exclusion-search","dual");await page.selectOption("#exclusion-coin","dual");
  await page.selectOption("#exclusion-scope",JSON.stringify({exchange:"upbit",net_type:"ETH"}));
  await page.locator("#exclusion-form").getByRole("button",{name:"숨김 추가"}).click();
  await page.waitForFunction(()=>document.querySelector("#exclusions-count").textContent==="1");
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="dual"] .network-exchange')?.textContent==="빗썸");
  await page.selectOption("#exclusion-scope",JSON.stringify({exchange:"bithumb",net_type:"ETH"}));
  await page.locator("#exclusion-form").getByRole("button",{name:"숨김 추가"}).click();
  await page.waitForFunction(()=>!document.querySelector('.coin-representative[data-coin-id="dual"]'));
  await page.click("#blocked-tab");assert.equal(await row("dual").count(),0,"include blocked never overrides manual exclusions");
  await page.click("#candidates-tab");
  assert.equal(await page.locator(".coin-hide").count(),0);
  await page.fill("#exclusion-search","cached");await page.selectOption("#exclusion-coin","cached");
  await page.locator("#exclusion-form").getByRole("button",{name:"숨김 추가"}).click();
  await page.waitForFunction(()=>!document.querySelector('.coin-representative[data-coin-id="cached"]'));
  await page.reload();await page.waitForFunction(()=>document.querySelector("#main-count").textContent!=="—");
  assert.equal(await row("cached").count(),0);assert.equal(await row("dual").count(),0);
  await page.locator("#exclusions-panel summary").click();
  await page.getByRole("button",{name:"cached 전체 숨김 해제",exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="cached"]'));
  await page.fill("#exclusion-search","outgoing");await page.selectOption("#exclusion-coin","outgoing");
  await page.fill("#exclusion-reason",'<img src=x onerror="alert(1)"> 브릿지 불가');
  await page.locator("#exclusion-form").getByRole("button",{name:"숨김 추가"}).click();
  await page.waitForFunction(()=>!document.querySelector('.coin-representative[data-coin-id="outgoing"]'));
  assert.equal(await page.locator("#exclusion-list img").count(),0);
  assert.match(await page.locator("#exclusion-list").textContent(),/브릿지 불가/);
  // Reload confirms server persistence; all filtering checkboxes still cannot restore a blacklisted coin.
  await page.reload();await page.waitForFunction(()=>document.querySelector("#exclusions-count").textContent==="3");
  await page.click("#blocked-tab");
  assert.equal(await row("outgoing").count(),0);assert.equal(await row("dual").count(),0);
  await page.locator("#exclusions-panel summary").click();
  await page.getByRole("button",{name:"dual ETH 숨김 해제",exact:true}).first().click();
  await page.click("#candidates-tab");
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="dual"]'));
  fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,"controls-desktop.png"),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"mobile has no page overflow");
  await page.screenshot({path:path.join(out,"controls-mobile.png"),fullPage:true});
  
  assert.equal(await row("pending").count(),0);
  await page.route("**/api/gaps",async route=>{
    const response=await route.fetch(),data=await response.json();
    for(const status of Object.values(data.wallet_status))status.age_seconds=120;
    for(const group of data.coin_groups)for(const r of group.routes){
      if(group.coin_id==="pending")Object.assign(r.liquidity_usd,{value:"10000000",usable:true,reused:false,stale:false,error:null});
      for(const q of Object.values(r.exchanges))for(const n of q.deposit_networks)n.age_seconds=120;
    }
    await route.fulfill({json:data});
  });
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="pending"][data-chain="1"]'));
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="cached"] .network-badges')?.textContent.includes("상태 확인 필요"));
  assert.equal(await page.locator("#wallet-status-summary").isVisible(),true);
  assert.match(await page.locator("#wallet-status-summary").textContent(),/갱신 지연.*상태 확인 필요.*2분 전/);
  assert.equal(await row("cached").locator(".network-badges .badge.good").count(),0,"expired wallet snapshots never remain green");
  await page.unroute("**/api/gaps");
  let resumed=false;
  await page.route("**/api/gaps",async route=>{
    const data=await (await route.fetch()).json();
    for(const g of data.coin_groups)if(g.coin_id==="cached")for(const r of g.routes){
      for(const n of r.exchanges.upbit.deposit_networks)n.wallet_state=resumed?"working":"paused";
      r.exchanges.upbit.deposit_possible=resumed;
    }
    await route.fulfill({json:data});
  });
  await page.click("#refresh");
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="cached"] .network-exchange')?.textContent==="빗썸");
  await page.click("#blocked-tab");
  assert.equal(await row("cached").locator(".network-exchange").textContent(),"업비트");
  assert.equal(await row("outgoing").count(),0,"blacklist also applies to blocked tab");
  assert.equal(await row("pending").count(),0,"unknown liquidity remains hidden in blocked tab");
  assert.match(await row("cached").textContent(),/입금 중단 · 참고 갭/);
  await page.fill("#search","cached");assert.equal(await page.locator("#main-visible").textContent(),"1");await page.fill("#search","");
  await page.fill("#liquidity-min","10.0");
  assert.equal(await page.locator("#liquidity-input-note").isVisible(),false);
  await page.fill("#liquidity-min","0.05");assert.equal(await page.locator("#liquidity-input-note").isVisible(),true);
  await page.fill("#liquidity-min","1.0");
  await page.screenshot({path:path.join(out,"blocked-tab-mobile.png"),fullPage:true});
  await page.setViewportSize({width:1440,height:1100});
  await page.screenshot({path:path.join(out,"blocked-tab-desktop.png"),fullPage:true});
  resumed=true;await page.click("#refresh");
  await page.waitForFunction(()=>!document.querySelector('.coin-representative[data-coin-id="cached"]'));
  await page.locator("#blocked-tab").press("ArrowLeft");
  assert.equal(await page.locator("#candidates-tab").getAttribute("aria-selected"),"true");
  assert.equal(await row("cached").locator(".network-exchange").count(),2,"reopened deposit returns to the main tab");
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({controlsChecks:"passed",unknownHidden:true,blockedDepositsHidden:true,withdrawOnlyVsDepositOnly:true,manualRefresh:true,networkIsolation:true,persistentBlacklist:true,consoleErrors:0}));
 }catch(e){if(page)await page.screenshot({path:path.join(out,"controls-failure.png"),fullPage:true}).catch(()=>{});throw e;}
 finally{if(browser)await browser.close();child.kill("SIGTERM");await Promise.race([new Promise(r=>child.once("exit",r)),sleep(5000)]);if(child.exitCode===null)child.kill("SIGKILL");}
})();
