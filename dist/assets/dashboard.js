"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {revision:0, data: null, received: 0, ok: false, loading: false, timer: null, interval: 2000};
  const expanded = new Set();
  let activeView = "candidates";
  const liquidityStorageKey = "dexdda.liquidityMinMillion";
  const validMinimum = value => /^(?:0\.[1-9]|[1-9](?:\.[0-9])?|10(?:\.0)?)$/.test(value);
  let minimumMillion = 1;
  try { const saved=localStorage.getItem(liquidityStorageKey); if(validMinimum(saved)) minimumMillion=Number(saved); } catch {}
  $("liquidity-min").value=minimumMillion.toFixed(1);

  const warnings = {
    low_liquidity: "유동성 낮음", liquidity_unknown_or_stale: "유동성 미확인",
    liquidity_cached: "이전 유동성 참고",
    community_unrecognized: "미인증 토큰", community_recognition_unknown: "인식 정보 미확인",
    liquidity_selection_pending: "체인 선택 보류", gap_out_of_range: "갭 상한 초과",
    bridge_required_view_only: "브릿지 필요",
  };
  const wallet = {
    working: ["입출금 정상", "good"], paused: ["입출금 중단", "bad"],
    withdraw_only: ["입금 중단", "bad"], deposit_only: ["출금 중단", "warn"],
    unsupported: ["미지원", "neutral"],
  };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const numeric = (value) => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
  const money = (value, usd = false) => {
    const n = numeric(value);
    if (n === null) return "—";
    if (usd && n > 0 && n < 0.000001) return "$" + n.toExponential(3);
    return (usd ? "$" : "") + n.toLocaleString("en-US", {maximumFractionDigits: usd ? (n >= 1 ? 4 : 8) : 2});
  };
  const elapsed = () => Math.max(0, performance.now() - state.received);
  const stale = (quote, sourceAge = false) => {
    if (!quote || quote.stale || quote.value === null || !state.data) return true;
    const now = state.data.generated_at_ms + elapsed();
    const ttl = (state.data.diagnostics?.stale_seconds || 15) * 1000;
    return !Number.isFinite(quote.received_at_ms) || now - quote.received_at_ms > ttl ||
      (sourceAge && (!Number.isFinite(quote.source_at_ms) || now - quote.source_at_ms > ttl));
  };
  const fxAvailable = () => numeric(state.data?.usdt_krw_ask?.value) > 0 && state.data.usdt_krw_ask.usable !== false;
  const fxReused = () => {
    const fx=state.data?.usdt_krw_ask;
    if(!fxAvailable())return false;
    const now=state.data.generated_at_ms+elapsed(),ttl=(fx.fresh_seconds||300)*1000;
    return fx.reused || fx.stale || now-fx.received_at_ms>ttl || now-fx.source_at_ms>ttl;
  };
  const gap = (row, exchange) => {
    if (!row.chain_index) return null;
    const quote = row.exchanges?.[exchange];
    if (!quote || quote.stale || stale(row.dex_usd, true) ||
        stale(quote.price_krw) || !fxAvailable()) return null;
    return numeric(quote.gap_percent);
  };
  const best = (row) => {
    const values = ["upbit", "bithumb"].map((ex) => gap(row, ex)).filter((v) => v !== null);
    return values.length ? Math.max(...values) : null;
  };
  const sortRows = (rows) => [...rows].sort((a, b) => {
    const sort = $("sort").value;
    if (sort !== "symbol") {
      const av = sort === "best" ? best(a) : gap(a, sort);
      const bv = sort === "best" ? best(b) : gap(b, sort);
      if (av === null && bv !== null) return 1;
      if (bv === null && av !== null) return -1;
      if (av !== null && bv !== null && av !== bv) return bv - av;
    }
    return a.symbol.localeCompare(b.symbol) || a.coin_id.localeCompare(b.coin_id) ||
      String(a.chain_index || "").localeCompare(String(b.chain_index || ""), undefined, {numeric: true});
  });
  const matches = (row) => {
    const search = $("search").value.trim().toLocaleLowerCase();
    return !search || [row.coin_id, row.symbol, row.token_name, row.chain_name,
      row.chain_index, row.address, ...Object.values(row.names || {})].join(" ").toLocaleLowerCase().includes(search);
  };
  const badge = (text, kind) => element("span", text, "badge " + kind);

  function networkCell(row) {
    const cell = element("td");
    for (const [ex, label] of [["upbit", "업비트"], ["bithumb", "빗썸"]]) {
      const quote = row.exchanges?.[ex];
      if (!quote) continue;
      const line = element("div", undefined, "network-line");
      line.append(element("span", label, "network-exchange"));
      const list = element("span", undefined, "network-badges");
      if (quote.route_status === "bridge_candidate") {
        const bridge=badge("","warn");
        bridge.append(window.DexddaLogos.chain(element("span",row.chain_name || row.chain_index || "매수 체인"),row.chain_index)," → ");
        (quote.deposit_networks || []).forEach((n,i)=>{if(i)bridge.append(" / ");bridge.append(window.DexddaLogos.network(element("span",n.network_name || n.net_type),ex,n.net_type));});
        bridge.append(" · 브릿지 확인 필요");list.append(bridge);
      }
      if (quote.route_status === "excluded") list.append(badge("경로 제외", "neutral"));
      for (const network of quote.deposit_networks || []) {
        const [text, tone] = network.manual_excluded ? ["임시 제외", "warn"] : wallet[network.wallet_state] || ["상태 확인 필요", "neutral"];
        const item = badge((network.network_name || network.net_type) + " · " + text, tone);
        item.title = "거래소 지원망 " + network.net_type + " · " + (network.checked_at_ms ? window.DexddaUI.observedTime(network.checked_at_ms)+" 정상 조회" : "조회 이력 없음") + (network.error ? " · 조회 오류: "+network.error : "");
        list.append(window.DexddaLogos.network(item,ex,network.net_type));

      }
      if (!list.childNodes.length) list.append(badge("네트워크 미확인", "neutral"));
      line.append(list); cell.append(line);
    }
    if (!cell.childNodes.length) cell.append(element("span", "네트워크 미확인", "muted"));
    return cell;
  }

  function renderRow(row, index) {
    const tr = element("tr");
    tr.dataset.coinId = row.coin_id;
    tr.dataset.chain = row.chain_index || "";
    tr.dataset.address = row.address || "";
    tr.append(element("td", String(index + 1).padStart(2, "0"), "rank"));
    const asset = element("td"), line = element("div", undefined, "asset-line");
    line.append(window.DexddaLogos.coin(row));
    const names = element("div");
    const select = element("button", row.symbol, "symbol asset-select");
    select.type = "button";
    select.setAttribute("aria-label", row.symbol + " 상세 견적 열기");
    names.append(select);
    tr.addEventListener("click", () => window.dispatchEvent(new CustomEvent("dexdda:select", {detail:{...row, default_amount_usdt:state.data.default_amount_usdt}})));
    const full = [row.names?.upbit_ko || row.names?.bithumb_ko,
      row.token_name || row.names?.okx || row.names?.coingecko].filter(Boolean);
    const label = element("span", [...new Set(full)].join(" · ") || row.coin_id, "name");
    label.title = label.textContent; names.append(label);
    line.append(names); asset.append(line);
    const identity = element("span", row.coin_id + (row.address ? " · " + row.address.slice(0, 6) + "…" + row.address.slice(-4) : ""), "identity");
    identity.title = row.coin_id + (row.address ? " / " + row.address : "");
    if(row.native)identity.textContent=row.coin_id+" · 네이티브 자산 · CA 없음";
    asset.append(identity);
    if(row.address && !row.native){
      const copy=element("button","CA 복사","ca-copy");copy.type="button";
      copy.title=row.address;copy.setAttribute("aria-label",row.symbol+" "+(row.chain_name||row.chain_index)+" CA 복사");
      copy.addEventListener("click",event=>{
        event.stopPropagation();
        window.DexddaUI.copyCA(row.address,$("copy-status"),row.symbol+" · "+(row.chain_name||row.chain_index));
      });
      asset.append(copy);
    }
    const marks = element("div", undefined, "warning-line");
    for (const key of row.warnings || []) if (warnings[key]) marks.append(badge(warnings[key], "warn"));
    if (marks.childNodes.length) asset.append(marks);
    tr.append(asset);
    const chain = element("td");
    chain.append(window.DexddaLogos.chain(element("span", row.chain_name || row.chain_index || "선택 보류", "chain"),row.chain_index));
    chain.append(element("span", row.chain_index ? "Chain " + row.chain_index : (row.candidate_chain_count || 0) + "개 체인 · 관측값 없음", "chain-id"));
    const q = row.liquidity_usd, usable = window.DexddaUI.liquidityUsable(q);
    const liquidity=element("span","유동성 "+(usable ? window.DexddaUI.liquidity(q.value) : "미확인"),"liquidity-value");
    liquidity.title=(usable ? "정확한 유동성: $"+q.value+" · " : "")+window.DexddaUI.liquidityInfo(q);
    chain.append(liquidity);
    if (row.chain_index) {
      const info=element("span",window.DexddaUI.liquidityInfo(q),"liquidity-info"+(q?.reused ? " reused" : ""));
      chain.append(info);
    } else {
      chain.append(element("span","정상 관측 이력 없음 · 경로 비교에서 체인별 원인 확인","liquidity-info"));
    }
    tr.append(chain);
    const dex = element("td", stale(row.dex_usd, true) ? "—" : money(row.dex_usd.value, true), "numeric");
    if (stale(row.dex_usd, true)) dex.append(element("span", "가격 갱신 대기", "gap-note"));
    tr.append(dex);
    for (const ex of ["upbit", "bithumb"]) {
      const value = gap(row, ex), quote = row.exchanges?.[ex];
      const cell = element("td", undefined, "numeric");
      cell.append(element("span", value === null ? "—" : (value > 0 ? "+" : "") + value.toFixed(2) + "%",
        "gap-value " + (value === null ? "muted" : value > 0 ? "up" : value < 0 ? "down" : "flat")));
      if (quote?.deposit_possible === false) cell.append(element("span","입금 중단 · 참고 갭","gap-note bridge-note"));
      if (quote?.deposit_possible === null) cell.append(element("span","입금 상태 확인 필요","gap-note"));
      if (value === null) {
        let reason = "갱신 대기";
        if (!row.chain_index) reason = "체인 선택 보류";
        else if (!quote) reason = row.hidden_exchanges?.[ex] || "미상장";
        else if (quote.unavailable_reasons?.includes("route_not_eligible")) reason = "직접 입금 불가";
        else if (!fxAvailable()) reason = "USDT 첫 수신 대기";
        cell.append(element("span", reason, "gap-note"));
      }
      if (quote?.route_status === "bridge_candidate") cell.append(element("span", "브릿지 비용·시간 미반영", "gap-note bridge-note"));
      tr.append(cell);
    }
    tr.append(networkCell(row));
    return tr;
  }

  function render() {
    if (!state.data) return;
    const focused = document.activeElement?.matches(".asset-select, .route-toggle, .ca-copy") ? document.activeElement.closest("tr") : null;
    const focusSelector = document.activeElement?.classList.contains("route-toggle") ? ".route-toggle" : document.activeElement?.classList.contains("ca-copy") ? ".ca-copy" : ".asset-select";
    const focusKey = focused ? [focused.dataset.coinId, focused.dataset.chain, focused.dataset.address].join("|") : null;
    const walletStale=s=>s.stale || !Number.isFinite(s.age_seconds) || !Number.isFinite(s.checked_at_ms) || Number(s.age_seconds)+elapsed()/1000>(state.data.wallet_stale_seconds||90);
    window.dispatchEvent(new CustomEvent("dexdda:snapshot",{detail:{...state.data,wallet_status:Object.fromEntries(Object.entries(state.data.wallet_status||{}).map(([ex,s])=>[ex,{...s,age_seconds:Number.isFinite(s.age_seconds)?s.age_seconds+elapsed()/1000:null,stale:walletStale(s)}]))}}));
    const data = state.data, fxStale = !fxAvailable(), reused = fxReused();
    $("fx-value").textContent = fxStale ? "—" : money(data.usdt_krw_ask.value);
    $("fx-note").textContent = fxStale ? "첫 정상 호가 수신 대기" :
      (reused ? "마지막 정상값 재사용" : "공통 환산 기준") + " · 마지막 수신 " + new Date(data.usdt_krw_ask.received_at_ms).toLocaleString("ko-KR",{hour12:false});
    $("dry-run").textContent = data.dry_run ? "DRY RUN 켜짐" : "DRY RUN 꺼짐";
    $("dry-run").className = "badge " + (data.dry_run ? "good" : "warn");
    $("main-count").textContent = data.counts.main.toLocaleString();
    $("bridge-count").textContent = data.counts.bridge_candidates.toLocaleString();
    $("suspect-count").textContent = data.counts.suspected.toLocaleString();
    $("fresh-count").textContent = "계산 가능 " + data.main.filter((row) => best(row) !== null).length + "개 경로";
    $("pending-count").textContent = "체인 선택 보류 " + (data.diagnostics?.pending_bridge_coins || 0) + "개";
    const max = data.filters?.max_abs_gap_percent;
    $("suspect-note").textContent = max ? "절댓값 " + max + "% 초과 · 별도 확인" : "갭 상한 초과 · 별도 확인";
    $("suspect-description").textContent = "갭이 " + (max ? "±" + max + "%를" : "상한을") + " 초과한 경로입니다. 가격·유동성과 토큰 정보를 별도로 확인하세요.";
    const delayed=elapsed()>90000;
    $("updated").textContent = delayed ? "갱신 지연 · "+window.DexddaUI.observedTime(Date.now()-elapsed()) : "방금 전";
    $("updated").classList.toggle("amber",delayed);
    $("poll-note").textContent = state.interval / 1000 + "초마다 자동 갱신";
    const healthy = state.ok && data.running;
    $("connection").replaceChildren(element("span", "", "dot " + (healthy ? "" : "offline")), document.createTextNode(healthy ? "자동 갱신 중" : "연결 확인 필요"));
    const notice = $("notice");
    notice.hidden = healthy && !fxStale && !reused;
    notice.className = "notice " + (state.ok ? "" : "error");
    notice.textContent = !state.ok ? "서버 연결이 끊겼습니다. 마지막 수신 데이터를 표시하며, 만료된 갭은 숨깁니다. 자동으로 다시 연결합니다." :
      !data.running ? "가격 수집기가 실행 중이 아닙니다. 서버 상태를 확인하세요." :
      fxStale ? "아직 정상 USDT 호가를 받은 적이 없습니다. 첫 수신 후 갭 계산을 시작합니다." :
      "USDT 갱신 지연 · 마지막 정상값 " + money(data.usdt_krw_ask.value) + " KRW로 갭을 계산 중입니다. 새 호가 수신 시 자동 반영합니다.";

    const buckets = {candidates:[],blocked:[]};
    const blacklisted=new Set((data.preferences?.exclusions||[]).filter(e=>e.kind==="coin").map(e=>e.coin_id));
    for (const mode of ["candidates","blocked"]) for (const group of data.coin_groups) {
      if(blacklisted.has(group.coin_id))continue;
      const routes = group.routes.map(original => {
        const low = window.DexddaUI.liquidityUsable(original.liquidity_usd) && numeric(original.liquidity_usd.value) !== null && Number(original.liquidity_usd.value) < Math.round(minimumMillion*1000000);
        const currentExchanges=Object.entries(original.exchanges||{}).map(([ex,q])=>{
          const networks=(q.deposit_networks||[]).map(n=>({...n,wallet_state:walletStale(n)?null:n.wallet_state}));
          const relevant=networks.filter(n=>n.applies_to_route && !n.manual_excluded);
          const possible=relevant.length ? (relevant.some(n=>["working","deposit_only"].includes(n.wallet_state))?true:relevant.every(n=>["paused","withdraw_only","unsupported"].includes(n.wallet_state))?false:null) : q.deposit_possible;
          return [ex,{...q,deposit_networks:networks,deposit_possible:possible}];
        });
        const exchanges=Object.fromEntries(currentExchanges.filter(([,q])=>q.route_status!=="excluded" && !q.manual_excluded && (mode==="blocked" ? q.deposit_possible===false : q.deposit_possible!==false)));
        const gaps=Object.values(exchanges).map(q=>numeric(q.gap_percent)).filter(v=>v!==null);
        const max=Number(data.filters?.max_abs_gap_percent);
        const isSuspect=gaps.some(v=>Math.abs(v)>max);
        const hidden_exchanges=Object.fromEntries(currentExchanges.filter(([ex])=>!exchanges[ex]).map(([ex,q])=>[ex,q.manual_excluded?"직접 임시 제외":q.route_status==="excluded"?"경로 제외":q.deposit_possible===false?"입금 중단 탭":"기본 목록 탭"]));
        return {...original,exchanges,hidden_exchanges,suspected:isSuspect,low_liquidity:low,warnings:[...(original.warnings||[]).filter(w=>w!=="low_liquidity" && w!=="gap_out_of_range"),...(low?["low_liquidity"]:[]),...(isSuspect?["gap_out_of_range"]:[])]};
      }).filter(row => {
        return Object.keys(row.exchanges).length > 0 && window.DexddaUI.liquidityUsable(row.liquidity_usd) && !row.low_liquidity;
      });
      if (!routes.length) continue;
      const known = routes.filter(r => window.DexddaUI.liquidityUsable(r.liquidity_usd));
      const selected = known[0];
      const row = selected || {...routes[0], chain_index:null, chain_name:null, address:null,
        dex_usd:null, best_gap_percent:null, suspected:false,
        warnings:["liquidity_selection_pending"], candidate_chain_count:routes.length};
      buckets[mode].push({group, routes, row});
    }
    const groups=buckets.candidates;
    const main = groups.filter(g => !g.row.suspected), suspect = groups.filter(g => g.row.suspected);
    $("main-count").textContent = main.length.toLocaleString();
    $("bridge-count").textContent = groups.filter(g => Object.values(g.row.exchanges).some(q => q.route_status === "bridge_candidate")).length.toLocaleString();
    $("suspect-count").textContent = suspect.length.toLocaleString();
    $("fresh-count").textContent = "대표 경로 갭 계산 가능 " + main.filter(g=>g.row.chain_index && best(g.row)!==null).length + "개";
    $("pending-count").textContent = "대표 경로 기준 · 비용·시간 미반영";
    const visibleMain=activeView==="blocked"?buckets.blocked:main;
    $("blocked-count").textContent=buckets.blocked.length;
    $("list-title").textContent=activeView==="blocked"?"입금 중단":"코인별 비교";
    $("suspect-section").hidden=activeView==="blocked";
    $("market-panel").setAttribute("aria-labelledby",activeView+"-tab");
    for(const mode of ["candidates","blocked"]){
      $(mode+"-tab").setAttribute("aria-selected",String(activeView===mode));
      $(mode+"-tab").tabIndex=activeView===mode?0:-1;
    }
    const byRow = new Map([...groups,...buckets.blocked].map(g => [g.row,g]));
    for (const [items,prefix] of [[visibleMain,"main"],[activeView==="blocked"?[]:suspect,"suspect"]]) {
      const visible = items.filter(g=>g.routes.some(matches));
      const sorted = sortRows(visible.map(g=>g.row)).map(row=>byRow.get(row));
      $(prefix+"-visible").textContent = sorted.length;
      const fragment = document.createDocumentFragment();
      sorted.forEach(({group,routes,row},i)=>{
        const tr=renderRow(row,i);
        tr.classList.add("coin-representative");
        const chainCell=tr.children[2];
        if(row.chain_index)chainCell.append(element("span",
          (group.liquidity_comparison_complete ? "관측값 기준 최대 체인" : "관측값 기준 최대 · 일부 체인 미확인") + (group.liquidity_reused ? " · 이전값 포함" : ""),"gap-note"));
        if(group.routes.length>1 || !row.chain_index){
          const button=element("button",(expanded.has(group.coin_id)?"접기":"경로 비교")+" · "+routes.length+"/"+group.routes.length,"route-toggle");
          button.type="button";button.setAttribute("aria-expanded",String(expanded.has(group.coin_id)));
          button.addEventListener("click",event=>{
            event.stopPropagation();
            if(expanded.has(group.coin_id))expanded.delete(group.coin_id);else expanded.add(group.coin_id);
            render();
          });
          tr.children[1].append(button);
        }
        fragment.append(tr);
        if(expanded.has(group.coin_id)){
          const note=element("tr"),cell=element("td","체인별 경로 · 브릿지의 실현 가능 여부는 별도 확인 · 비용·시간 미반영","route-comparison-note");
          cell.colSpan=7;note.append(cell);fragment.append(note);
          for(const route of routes){
            const child=renderRow(route,i);child.classList.add("route-alternative");
            child.children[0].textContent="↳";
            fragment.append(child);
          }
        }
      });
      if(!sorted.length){
        const tr=element("tr"),td=element("td",$("search").value.trim()?"검색 조건에 맞는 종목이 없습니다.":"현재 필터로 표시할 후보가 없습니다.","empty");
        td.colSpan=7;tr.append(td);fragment.append(tr);
      }
      $(prefix+"-body").replaceChildren(fragment);
    }
    if (focusKey && document.activeElement === document.body) {
      const replacement = [...document.querySelectorAll("tbody tr[data-coin-id]")].find(tr =>
        [tr.dataset.coinId, tr.dataset.chain, tr.dataset.address].join("|") === focusKey);
      replacement?.querySelector(focusSelector)?.focus({preventScroll:true});
    }
    $("main-foot").textContent = "검색 결과 " + $("main-visible").textContent + " / " + visibleMain.length +
      "개 · 코인별 대표 체인 기준";
  }

  function validate(data) {
    return data?.schema_version === 1 && Number.isFinite(data.generated_at_ms) &&
      ["main", "bridge_candidates", "suspected"].every((key) => Array.isArray(data[key]) &&
        data[key].every((row) => typeof row.coin_id === "string" && typeof row.symbol === "string")) &&
      Array.isArray(data.coin_groups) && data.coin_groups.every(g => Array.isArray(g.routes) && g.routes.length) &&
      data.counts && typeof data.dry_run === "boolean" && data.usdt_krw_ask;
  }
  async function poll() {
    if (state.loading) return;
    clearTimeout(state.timer);
    state.loading = true; $("refresh").disabled = true;
    const requestRevision=state.revision;
    const controller = new AbortController(), started = performance.now();
    const timeout = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch("/api/gaps", {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      if (requestRevision!==state.revision)return;
      if (!validate(data)) throw new Error("invalid_snapshot");
      state.data = data;
      state.received = started; // Include time spent in flight in the expiry calculation.
      state.interval = Math.max(500, Math.min(60000, (Number(data.ui_poll_seconds) || 2) * 1000));
      state.ok = true;
    } catch {
      state.ok = false;
      if (!state.data) {
        $("notice").hidden = false; $("notice").className = "notice error";
        $("notice").textContent = "가격 API에 연결할 수 없습니다. 서버 설정과 레지스트리를 확인하세요. 자동으로 다시 시도합니다.";
        $("connection").replaceChildren(element("span", "", "dot offline"), document.createTextNode("연결 대기"));
      }
    } finally {
      clearTimeout(timeout); state.loading = false; $("refresh").disabled = false;
      render(); state.timer = setTimeout(poll, requestRevision!==state.revision ? 0 : state.interval);
    }
  }
  function updateMinimum(){
    const input=$("liquidity-min"),valid=validMinimum(input.value);
    input.setAttribute("aria-invalid",String(!valid));
    $("liquidity-input-note").hidden=valid;
    $("liquidity-input-note").textContent=valid?"":
      "0.1–10.0m 범위에서 소수점 한 자리까지 입력하세요. 적용 중: "+minimumMillion.toFixed(1)+"m";
    if(!valid)return;
    if (minimumMillion === Number(input.value)) return;
    minimumMillion=Number(input.value);
    try{localStorage.setItem(liquidityStorageKey,minimumMillion.toFixed(1));}catch{}
    render();
  }
  $("liquidity-min").addEventListener("input",updateMinimum);
  $("liquidity-min").addEventListener("change",updateMinimum);
  $("liquidity-min").addEventListener("blur",()=>{if(validMinimum($("liquidity-min").value))$("liquidity-min").value=minimumMillion.toFixed(1);});
  for(const mode of ["candidates","blocked"]){
    const tab=$(mode+"-tab");
    tab.addEventListener("click",()=>{activeView=mode;render();});
    tab.addEventListener("keydown",event=>{
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
      event.preventDefault();
      const next=event.key==="Home"?"candidates":event.key==="End"?"blocked":mode==="blocked"?"candidates":"blocked";
      $(next+"-tab").click();$(next+"-tab").focus();
    });
  }
  window.addEventListener("dexdda:preferences-changed",()=>{state.revision++;state.data=null;$("main-body").replaceChildren();$("suspect-body").replaceChildren();poll();});
  window.addEventListener("dexdda:refresh",poll);
  $("search").addEventListener("input", render);
  $("sort").addEventListener("change", render);
  $("refresh").addEventListener("click", poll);
  // Expire visible gaps even while a network request hangs or the backend is stopped.
  setInterval(() => { if (state.data && elapsed() >= state.interval) render(); }, 1000);
  // Registry age comes from /api/health, separately from the gap poll: once on load, then each minute.
  async function pollRegistryAge() {
    const badge = $("registry-stale");
    try {
      const response = await fetch("/api/health", {cache: "no-store"});
      if (!response.ok) throw new Error("unavailable");
      const health = await response.json();
      const stale = health.registry_stale === true && Number.isFinite(health.registry_age_hours);
      badge.textContent = stale ? "레지스트리 오래됨 (" + Math.floor(health.registry_age_hours) + "시간)" : "";
      badge.hidden = !stale;
    } catch {
      badge.hidden = true;
    }
  }
  setInterval(pollRegistryAge, 60000);
  pollRegistryAge();
  poll();
})();
