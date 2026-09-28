/* Offline image delivery verifies rendering and graceful failure independently of providers. */
const assert=require('node:assert/strict'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 const url='http://127.0.0.1:'+port;
 const child=spawn(path.join(root,'.venv/bin/python'),['-m','uvicorn','ui_server:app','--app-dir','tests','--host','127.0.0.1','--port',String(port),'--no-access-log','--log-level','critical'],{cwd:root,stdio:'ignore'});
 let browser;
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok){ready=true;break;}}catch{}await sleep(100);}assert(ready);
  browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const good='https://coin-images.coingecko.com/test/good.svg',bad='https://coin-images.coingecko.com/test/bad.png';
  await page.route('https://coin-images.coingecko.com/**',route=>route.request().url()===bad?route.fulfill({status:404}):route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28"><circle cx="14" cy="14" r="14" fill="#55c8ae"/></svg>'}));
  await page.route('**/api/logos',route=>route.fulfill({json:{coins:{alpha:good,beta:bad,gensyn:good},chains:{'1':good,'42161':good,'8453':good,'10':'javascript:alert(1)'},networks:{upbit:{ETH:'1'},bithumb:{ETH:'1'}},tokens:{}}}));
  await page.goto(url);await page.click('#blocked-tab');
  const row=id=>page.locator('.coin-representative[data-coin-id="'+id+'"]');
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="alpha"] .asset-icon img')?.naturalWidth>0);

  assert.equal(await row('alpha').locator('.chain .chain-logo img').count(),1);
  await row('alpha').getByRole('button',{name:'ALPHA 상세 견적 열기'}).click();
  await page.waitForFunction(()=>document.querySelector('#detail-title .asset-icon img')?.naturalWidth>0);
  assert.equal(await page.locator('#detail-identity .chain-logo img').count(),1);
  await page.locator('#detail-close').click();
  await page.click('#candidates-tab');
  await page.waitForFunction(()=>document.querySelector('.coin-representative[data-coin-id="beta"] .asset-icon')?.textContent==='B');
  assert.equal(await row('zero').locator('.chain-logo img').count(),2);
  assert.match(await row('gensyn').textContent(),/브릿지 확인 필요/);
  assert(await row('gensyn').locator('.network-badges .chain-logo img').count()>=4);
  assert.equal(await page.getByText('직접 입금 경로',{exact:true}).count(),0);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({logoChecks:'passed',brokenImageFallback:true,identityMapping:true,bridgeAndDetail:true,mobileOverflow:false,consoleErrors:0}));
 }finally{if(browser)await browser.close();child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),sleep(5000)]);if(child.exitCode===null)child.kill('SIGKILL');}
})();
