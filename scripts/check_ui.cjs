/* Browser integration checks. Requires Playwright and an installed Chrome browser. */
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
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, "127.0.0.1", resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

(async () => {
  const port = await freePort(), url = "http://127.0.0.1:" + port;
  const args = ["-m", "uvicorn", live ? "app.main:app" : "ui_server:app",
    "--host", "127.0.0.1", "--port", String(port), "--no-access-log", "--log-level", "critical"];
  if (!live) args.push("--app-dir", "tests");
  const child = spawn(path.join(root, ".venv/bin/python"), args, {cwd: root, stdio: "ignore"});
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error("test server exited");
      try { if ((await fetch(url + "/api/health")).ok) {ready = true; break;} } catch {}
      await sleep(100);
    }
    assert(ready, "local server ready");
    browser = await chromium.launch({channel: "chrome", headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1080}});
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(url);
    await page.waitForFunction(() => document.querySelector("#main-count").textContent !== "—");
    const out = path.join(root, "docs/screenshots");
    fs.mkdirSync(out, {recursive: true});
    if (live) {
      await sleep(32000);
      const snapshot = await (await fetch(url + "/api/gaps")).json();
      const registry = JSON.parse(fs.readFileSync(path.join(root, "data/registry.json")));
      const identities = new Set(registry.coins.flatMap(c => c.tokens.map(t => [c.coin_id, t.chain_index, t.address].join("|"))));
      const rendered = await page.locator("tbody tr[data-address]").evaluateAll(nodes => nodes
        .filter(n => n.dataset.address)
        .map(n => [n.dataset.coinId, n.dataset.chain, n.dataset.address].join("|")));
      assert(rendered.length > 0);
      assert(rendered.every(key => identities.has(key)), "all displayed identities are in registry");
      assert.equal(snapshot.execution_enabled, false);
      await page.screenshot({path: path.join(out, "m3-live.png"), fullPage: true});
      console.log(JSON.stringify({live: true, counts: snapshot.counts, renderedRegistryTokens: rendered.length, consoleErrors: errors.length}));
    } else {
      let requests = 0;
      page.on("response", r => {if (r.url().endsWith("/api/gaps") && r.ok()) requests++;});
      const ids = () => page.locator("#main-body tr[data-coin-id]").evaluateAll(nodes => nodes.map(n => n.dataset.coinId));
      assert.deepEqual(await ids(), ["beta", "gensyn", "zero", "negative", "stale"]);
      await page.click("#blocked-tab");
      assert.match(await page.locator('[data-coin-id="alpha"]').textContent(), /입금 중단/);
      await page.click("#candidates-tab");
      assert.match(await page.locator('[data-coin-id="beta"]').textContent(), /출금 중단/);
      assert.match(await page.locator('[data-coin-id="zero"] .gap-value').first().textContent(), /^0.00%$/);
      assert.equal(await page.locator('[data-coin-id="stale"] .gap-value').first().textContent(), "—");
      assert.match(await page.locator("#fx-value").textContent(), /1,300/);
      assert.match(await page.locator("#dry-run").textContent(), /켜짐/);
      assert.match(await page.locator('[data-coin-id="gensyn"] .name').textContent(), /젠신 · Gensyn/);
      assert.equal(await page.locator('[data-coin-id="artificial-inu"]').count(), 0);
      await page.selectOption("#sort", "upbit");
      assert.equal((await ids())[0], "gensyn");
      await page.selectOption("#sort", "bithumb");
      assert.equal((await ids())[0], "beta");
      await page.selectOption("#sort", "symbol");
      assert.equal((await ids())[0], "gensyn");
      await page.selectOption("#sort", "best");
      await page.locator("#suspect-section summary").click();
      assert.equal(await page.locator('[data-coin-id="pending"]').count(),0);
      await sleep(1200);
      assert(requests >= 1, "polling updates page");
      assert.equal(await page.locator("#suspect-section").getAttribute("open"), "");
      await page.fill("#search", "젠신");
      assert.equal(await page.locator("#main-body tr[data-coin-id]").count(), 1);
      await page.fill("#search", "0x" + "8453".padStart(40, "0"));
      assert.deepEqual(await ids(), ["beta"]);
      await page.fill("#search", "");
      await page.screenshot({path: path.join(out, "m3-fixture-desktop.png"), fullPage: true});
      await page.setViewportSize({width: 390, height: 844});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "mobile page does not overflow");
      assert(await page.locator(".table-scroll").first().evaluate(el => el.scrollWidth > el.clientWidth), "mobile table scrolls");
      await page.screenshot({path: path.join(out, "m3-fixture-mobile.png"), fullPage: true});
      await page.setViewportSize({width: 1440, height: 1080});
      const template = await (await fetch(url + "/api/gaps")).json();
      await page.route("**/api/gaps",async route=>{
        const data=await (await route.fetch()).json();
        data.usdt_krw_ask.stale=true;data.usdt_krw_ask.reused=true;
        data.usdt_krw_ask.received_at_ms=Date.now()-3600000;
        data.usdt_krw_ask.error="network_error";
        await route.fulfill({json:data});
      });
      await page.waitForFunction(()=>document.querySelector("#fx-note").textContent.includes("재사용"));
      assert.equal(await page.locator("#fx-value").textContent(),"1,300");
      assert.match(await page.locator('[data-coin-id="beta"] .gap-value').last().textContent(),/20.00%/);
      assert.match(await page.locator("#notice").textContent(),/마지막 정상값/);
      await page.unroute("**/api/gaps");
      await page.route("**/api/gaps", route => route.abort());
      await page.waitForFunction(() => document.querySelector("#notice").textContent.includes("끊겼"));
      await page.waitForFunction(() => [...document.querySelectorAll(".gap-value")].every(el => el.textContent === "—"));
      assert.equal(await page.locator("#fx-value").textContent(), "1,300");
      await page.unroute("**/api/gaps");
      await page.waitForFunction(() => document.querySelector("#notice").hidden);
      assert.equal((await ids())[0], "beta");
      await page.route("**/api/gaps", async route => {
        const data = structuredClone(template);
        data.generated_at_ms = Date.now();
        data.coin_groups = [];
        data.main = []; data.bridge_candidates = []; data.suspected = [];
        data.counts = {main: 0, bridge_candidates: 0, suspected: 0, hidden: 0};
        await route.fulfill({json: data});
      });
      await page.waitForFunction(() => document.querySelector("#main-count").textContent === "0");
      assert.match(await page.locator("#main-body").textContent(), /표시할 후보가 없습니다/);
      await page.unroute("**/api/gaps");
      await page.route("**/api/gaps", async route => {
        const data = structuredClone(template);
        data.dry_run = false;
        data.coin_groups.find(g=>g.coin_id==="beta").routes[0].token_name = '<img src=x onerror="window.unsafe=true">';
        await route.fulfill({json: data});
      });
      await page.waitForFunction(() => document.querySelector("#dry-run").textContent.includes("꺼짐"));
      assert.equal(await page.locator("tbody img").count(), 0);
      assert.equal(await page.evaluate(() => window.unsafe), undefined);
      assert.match(await page.locator("tbody").first().textContent(), /<img src=x/);
      await page.unroute("**/api/gaps");
      await page.route("**/api/gaps", route => route.fulfill({status: 503, json: {error: "invalid_config"}}));
      await page.reload();
      await page.waitForFunction(() => document.querySelector("#notice").textContent.includes("연결할 수 없습니다"));
      assert.equal(await page.locator("#main-count").textContent(), "—");
      await page.unroute("**/api/gaps");
      await page.waitForFunction(() => document.querySelector("#main-count").textContent === "5");
      console.log(JSON.stringify({fixtureChecks: "passed", consoleErrors: errors.length, screenshots: 2}));
    }
    assert.deepEqual(errors, []);
  } finally {
    if (browser) await browser.close();
    child.kill("SIGTERM");
    await Promise.race([new Promise(resolve => child.once("exit", resolve)), sleep(5000)]);
    if (child.exitCode === null) child.kill("SIGKILL");
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
