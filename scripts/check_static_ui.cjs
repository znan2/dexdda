/* Static demo check: serves dist/ with its _headers (CSP) on 127.0.0.1, drives the page for at
   least 30 seconds, and fails on any request that leaves the local origin, any CSP violation,
   page error or console error. Requires Playwright and an installed Chrome browser.
   Usage: node scripts/check_static_ui.cjs [--dist dist] [--shots DIR] [--seconds 30] */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const {chromium} = require("playwright");

const root = path.resolve(__dirname, "..");
const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : fallback;
};
const dist = path.resolve(root, arg("--dist", "dist"));
const shots = arg("--shots", null);
const seconds = Number(arg("--seconds", "30"));
const types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"};

function headers() {
  // Only the "/*" block is emitted by scripts/build_static.py.
  const lines = fs.readFileSync(path.join(dist, "_headers"), "utf8").split("\n");
  assert.equal(lines[0], "/*");
  return Object.fromEntries(lines.slice(1).filter(Boolean).map(line => {
    const at = line.indexOf(":");
    return [line.slice(0, at).trim(), line.slice(at + 1).trim()];
  }));
}

function serve() {
  const extra = headers();
  assert.match(extra["Content-Security-Policy"] || "", /default-src 'none'/);
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const file = path.normalize(path.join(dist, url.pathname === "/" ? "index.html" : url.pathname));
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404, extra); res.end("not found"); return;
    }
    res.writeHead(200, {...extra, "Content-Type": types[path.extname(file)] || "application/octet-stream"});
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(resolve => server.listen(0, "127.0.0.1", () => resolve(server)));
}

(async () => {
  const server = await serve();
  const origin = "http://127.0.0.1:" + server.address().port;
  const requests = [], external = [], failed = [], errors = [], consoleErrors = [];
  let browser;
  const watch = (page, label) => {
    page.on("request", request => {
      const url = request.url();
      requests.push(url);
      if (!url.startsWith(origin + "/") && !url.startsWith("data:")) external.push(label + " " + url);
    });
    page.on("requestfailed", request => failed.push(label + " " + request.url()));
    page.on("websocket", ws => external.push(label + " websocket " + ws.url()));
    page.on("pageerror", error => errors.push(label + " " + error.message));
    page.on("console", message => { if (message.type() === "error") consoleErrors.push(label + " " + message.text()); });
  };
  const csp = page => page.evaluate(() => window.__cspViolations || []);
  const shot = async (page, name, options = {}) => {
    if (!shots) return;
    fs.mkdirSync(shots, {recursive: true});
    await page.screenshot({path: path.join(shots, name), ...options});
  };
  try {
    browser = await chromium.launch({channel: "chrome", headless: true});
    const context = await browser.newContext({viewport: {width: 1440, height: 900}, deviceScaleFactor: 2,
      colorScheme: "dark", permissions: ["clipboard-read", "clipboard-write"]});
    await context.addInitScript(() => {
      window.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", e =>
        window.__cspViolations.push(e.violatedDirective + " " + e.blockedURI));
    });
    const started = Date.now();
    const page = await context.newPage();
    watch(page, "desktop");
    await page.goto(origin + "/");
    await page.waitForFunction(() => document.querySelector("#main-count").textContent !== "—");
    await page.waitForFunction(() => document.querySelectorAll("#main-body tr[data-coin-id]").length >= 5);
    assert.match(await page.locator("#fx-value").textContent(), /1,391/);
    assert.match(await page.locator(".demo-banner").textContent(), /정적 데모/);
    assert.match(await page.locator("#connection").textContent(), /자동 갱신 중/);
    assert.equal(await page.locator("#notice").isHidden(), true, "healthy snapshot hides the notice");
    assert.match(await page.locator('[data-coin-id="lumen"] .gap-value.up').first().textContent(), /^\+3\.\d\d%$/);
    assert.match(await page.locator('[data-coin-id="tidal"] .gap-value.down').first().textContent(), /^-/);
    assert.equal(await page.locator('#balance-cards [data-chain="1"] [data-symbol="USDT"] strong').textContent(), "12,480.50");
    await shot(page, "overview-desktop.png");
    await shot(page, "overview-full-desktop.png", {fullPage: true});
    if (shots) await page.locator("#market-panel").screenshot({path: path.join(shots, "table-desktop.png")});

    // 저유동성 필터: 0.5m로 낮추면 SABL(0.64m)이 나타난다.
    assert.equal(await page.locator('#main-body [data-coin-id="sable"]').count(), 0);
    await page.fill("#liquidity-min", "0.5");
    await page.waitForSelector('#main-body [data-coin-id="sable"]');
    await page.fill("#liquidity-min", "1.0");
    await page.fill("#search", "LUMN");
    await page.waitForFunction(() => document.querySelector("#main-visible").textContent === "1");
    await page.fill("#search", "");

    // 상세 견적 → DRY_RUN 확인 → 완료. 서명·전송 없이 끝나야 한다.
    await page.click('#main-body [data-coin-id="lumen"] .asset-select');
    await page.waitForFunction(() => !document.querySelector("#detail-refresh").disabled);
    await page.click("#detail-refresh");
    await page.waitForSelector(".detail-result strong.up");
    assert.match(await page.locator(".detail-result strong").textContent(), /^\+2\.\d{3}%$/);
    await page.waitForFunction(() => !document.querySelector("#detail-swap").disabled);
    await shot(page, "detail-desktop.png");
    await page.click("#detail-swap");
    await page.waitForFunction(() => !document.querySelector("#swap-confirm").hidden);
    await shot(page, "dry-run-confirm-desktop.png");
    await page.click("#swap-confirm");
    await page.waitForFunction(() => document.querySelector("#swap-status").textContent.includes("DRY_RUN 완료"));
    assert.match(await page.locator("#swap-content").textContent(), /전송하지 않은 트랜잭션 계획/);
    await page.click("#swap-close");
    await page.click("#detail-close");
    await page.waitForFunction(() => document.querySelector("#swap-history").textContent.includes("LUMN"));
    const history = await page.locator("#swap-history").textContent();

    // 입금 중단 탭과 의심 목록
    await page.click("#blocked-tab");
    await page.waitForSelector('#main-body [data-coin-id="ember"]');
    await page.click("#candidates-tab");
    await page.click("#suspect-section summary");
    await page.waitForSelector('#suspect-body [data-coin-id="prism"]');

    const mobile = await context.newPage();
    watch(mobile, "mobile");
    await mobile.setViewportSize({width: 390, height: 844});
    await mobile.goto(origin + "/");
    await mobile.waitForFunction(() => document.querySelectorAll("#main-body tr[data-coin-id]").length >= 5);
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "no horizontal page scroll");
    await shot(mobile, "overview-mobile.png");

    // 남은 시간 동안 자동 갱신(2초 주기)을 그대로 둔 채 외부 요청을 관찰한다.
    const remaining = seconds * 1000 - (Date.now() - started);
    if (remaining > 0) await page.waitForTimeout(remaining);
    const elapsed = Date.now() - started;
    const violations = [...await csp(page), ...await csp(mobile)];
    assert(elapsed >= seconds * 1000, "observed for the full window");
    // 이력은 15초마다 다시 그려진다. 실행 시각은 확인한 순간에 고정돼야 한다.
    assert.equal(await page.locator("#swap-history").textContent(), history, "history time stays fixed");
    assert(requests.filter(u => u.includes("/data/demo.json")).length >= 2, "demo data loaded");
    assert.deepEqual(external, [], "no request leaves the local origin");
    assert.deepEqual(violations, [], "no CSP violation");
    assert.deepEqual(failed, [], "no failed request");
    assert.deepEqual(errors, [], "no page error");
    assert.deepEqual(consoleErrors, [], "no console error");
    console.log(JSON.stringify({staticChecks: "passed", observedSeconds: Math.floor(elapsed / 1000),
      requests: requests.length, externalRequests: external.length, cspViolations: violations.length,
      pageErrors: errors.length, consoleErrors: consoleErrors.length, screenshots: shots ? fs.readdirSync(shots).length : 0}));
  } catch (error) {
    console.error(JSON.stringify({external, failed, errors, consoleErrors}, null, 1));
    throw error;
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
