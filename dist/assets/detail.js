"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const modal = $("detail-panel");
  let row = null, revision = 0, running = false, assetsReady = false, data = null, deadline = 0, updatedAt = 0;
  let assetCatalog = null, preferredAsset = "USDT";
  const assetCatalogReady = fetch("/api/purchase-assets", {cache:"no-store"})
    .then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{assetCatalog=d.assets;}).catch(()=>{});
  function syncAssets() {
    const available=(assetCatalog||[]).filter(a=>a.chain_index===row?.chain_index);
    for(const option of $("detail-asset").options){
      const asset=available.find(a=>a.purchase_symbol===option.value);
      option.disabled=available.length>0&&!asset;
      option.textContent=asset?.label||asset?.symbol||option.value;
    }
    $("detail-asset").value=available.some(a=>a.purchase_symbol===preferredAsset)||!available.length ? preferredAsset : available[0].purchase_symbol;
  }
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const format = (v, digits = 4) => v === null || v === undefined ? "—" :
    Number(v).toLocaleString("en-US", {maximumFractionDigits: digits});
  const addSection = (title, values) => {
    const section = node("section", undefined, "detail-section");
    section.append(node("h3", title));
    const dl = node("dl", undefined, "detail-values");
    for (const [label, value] of values) {
      const dd=node("dd",value);
      if(label==="매수 체인")window.DexddaLogos.chain(dd,data.chain_index);
      if(label==="일치하는 입금망")window.DexddaLogos.network(dd,data.exchange,data.deposit?.net_type);
      dl.append(node("dt",label),dd);
    }
    section.append(dl); $("detail-content").append(section);
  };
  function invalidate() {
    revision++; data = null; deadline = 0;
    window.dispatchEvent(new CustomEvent("dexdda:detail-invalidated"));
    $("detail-content").replaceChildren(); $("detail-expiry").textContent = "";
  }
  function render() {
    $("detail-content").replaceChildren();
    if (!data) return;
    const q = data.quote, g = data.gas, b = data.book, d = data.deposit;
    const bridge = data.route_status === "bridge_candidate";
    const expired = performance.now() >= deadline;
    const reference = !data.result && !!data.reference_basis;
    const result = data.result || data.reference_result;
    const codes = new Map((data.warnings || []).map(w => [w.code,w.message]));
    const blockers = ["deposit_unavailable","below_minimum","purchase_asset_missing","same_asset",
      "fx_unavailable","source_timestamp_invalid","rpc_or_wallet_missing","additional_chain_fee","gas_incomplete","insufficient_depth","tax_unknown","token_tax","honeypot"];
    let reason = blockers.map(code=>codes.get(code)).find(Boolean);
    if(codes.has("below_minimum")) reason = codes.get("below_minimum");
    else if(d?.wallet_state === "withdraw_only" || d?.wallet_state === "paused") reason = "거래소 입금 중단 · 현재 실행 불가";
    if(!reason && data.errors?.length) reason = data.errors.map(e=>"["+e.stage+"] "+e.message).join(" / ");
    $("detail-status").textContent = reason || (bridge ? "브릿지 필요 · 매수 견적의 가격 영향만 표시합니다." : result ? "조회 당시 호가 기준 추정 · 입금 완료 시점의 가격은 달라질 수 있습니다." : "계산 보류 · 아래 확인 항목을 확인하세요.");
    $("detail-status").classList.toggle("detail-blocker",!!reason);
    const hero = node("div", undefined, "detail-result" + (reference ? " reference" : ""));
    hero.append(node("span", bridge ? "가격 영향 · OKX 견적 기준" : reference ? "참고 실효갭 · 입금 가능 가정 · 현재 실행 불가" : "실효갭 · 가스 추정 반영", "muted"));
    const percent = bridge ? (q?.price_impact_percent == null ? null : Number(q.price_impact_percent)) : result ? Number(result.gap_percent) : null;
    hero.append(node("strong", percent === null ? "—" : (percent > 0 ? "+" : "") + percent.toFixed(bridge ? 2 : 3) + "%",
      percent === null ? "muted" : reference ? "amber" : percent > 0 ? "up" : percent < 0 ? "down" : "flat"));
    hero.append(node("span", bridge ? (reason || (q ? "브릿지 비용·가스비·거래소 매도 미반영" : "매수 견적을 확인할 수 없습니다.")) : result ? (reference ? "입금 가능 가정 손익 " : "예상 손익 ") + format(result.profit_usdt) + " USDT · " + format(result.profit_krw, 0) + "원" :
      reason || "실효갭 계산에 필요한 데이터를 확인하고 있습니다."));
    updateExpiry();
    $("detail-content").append(hero);
    addSection("금액별 스왑 견적", [
      ["매수 자산", data.purchase_asset ? data.purchase_asset.symbol + " · " + format(data.amount_usdt, 6) : "검증된 매수 자산 없음"],
      ["예상 수령량 (세금 전)", format(q?.quantity, 10)],
      ["매수세 보수 반영 수량", format(q?.net_quantity, 10)],
      ["토큰 매수세", q?.tax === null || q?.tax === undefined ? "미확인" : format(Number(q.tax) * 100) + "%"],
      ["가격 영향", q?.price_impact_percent === null || q?.price_impact_percent === undefined ? "미확인" : format(q.price_impact_percent) + "%"],
      ["경로", q?.routes?.length ? q.routes.slice(0, 3).join(" / ") + (q.routes.length > 3 ? " 외 " + (q.routes.length - 3) + "개" : "") : "경로 정보 없음"],
    ]);
    if (q?.routes?.length > 3) {
      const routes = node("details", undefined, "detail-routes");
      routes.append(node("summary", "전체 라우팅 " + q.routes.length + "개 보기"));
      const list = node("ul");
      for (const route of q.routes) list.append(node("li", route));
      routes.append(list); $("detail-content").append(routes);
    }
    if (!bridge) {
      addSection("거래소 매수 호가 소진", [
        ["매도 시장", data.market + " · " + (data.exchange === "upbit" ? "업비트" : "빗썸")],
        ["호가 내 체결 수량", format(b?.filled, 10)],
        ["전량 매도 가능", b ? b.complete ? "가능 · 조회 시점 기준" : "잔량 부족" : "미확인"],
        ["평균 매도 단가", format(b?.average_price_krw) + " KRW"],
        ["예상 매도 대금", format(b?.proceeds_krw) + " KRW"],
        ["공통 환산 호가", format(data.fx_krw) + " KRW / USDT" + (data.fx_reference?.reused ? " · 마지막 정상값 재사용" : "")],
        ["USDT 마지막 수신", data.fx_reference?.received_at_ms ? new Date(data.fx_reference.received_at_ms).toLocaleString("ko-KR",{hour12:false}) : "미확인"],
      ]);
      addSection("가스비 · USDT 환산 추정", [
        ["스왑", format(g?.swap_usdt, 6)],
        ["승인" + (g?.approval_count === null || g?.approval_count === undefined ? "" : " (" + g.approval_count + "회)"), format(g?.approve_usdt, 6)],
        ["전송 1회", format(g?.transfer_usdt, 6)],
        ["합계", format(g?.total_usdt, 6)],
        ["전송 gas 예산", format(g?.transfer_gas_units, 0)],
      ]);
      addSection("입금 경로 · 이번 조회에서 갱신", [
        ["매수 체인", (data.chain_name || data.chain_index) + " · " + data.chain_index],
        ["일치하는 입금망", d?.net_type || "없음 / 확인 필요"],
        ["입출금 상태", ({working:"입출금 정상",deposit_only:"입금 가능 · 출금 중단",withdraw_only:"입금 중단 · 출금 가능",paused:"입출금 중단"})[d?.wallet_state] || "미확인"],
        ["입금 가능", d?.possible === true ? "가능 (참고 정보)" : d?.possible === false ? "불가" : "미확인"],
        ["필요 컨펌 수", d?.confirmations === null || d?.confirmations === undefined ? "API 미제공 / 확인 필요" : String(d.confirmations)],
        ["블록 기준 예상 시간", d?.estimated_seconds === null || d?.estimated_seconds === undefined ? "미확인" : format(d.estimated_seconds, 0) + "초 · 거래소 처리 별도"],
        ["최소 입금 수량", format(d?.minimum, 10)],
        ["상태 조회 시각", d?.checked_at_ms ? new Date(d.checked_at_ms).toLocaleTimeString("ko-KR", {hour12:false}) : "미확인"],
      ]);
    }
    const warnings = node("section", undefined, "detail-warnings");
    warnings.append(node("h3", "확인 항목"));
    const list = node("ul");
    for (const item of [...(data.errors || []), ...(data.warnings || [])]) {
      list.append(node("li", (item.stage ? "[" + item.stage + "] " : "") + item.message));
    }
    if (!list.children.length) list.append(node("li", "입금 상태와 금액을 실행 전에 다시 확인합니다."));
    warnings.append(list); $("detail-content").append(warnings);
    $("detail-content").append(node("p", data.basis, "detail-note"));
    if (g) $("detail-content").append(node("p", g.method, "detail-note"));
    window.dispatchEvent(new CustomEvent("dexdda:detail", {detail:{quote:data, expired,
      request:{coin_id:data.coin_id,chain_index:data.chain_index,address:data.address,
               exchange:data.exchange,amount_usdt:data.amount_usdt,purchase_symbol:data.purchase_symbol||"USDT"}}}));
  }
  function updateExpiry() {
    if(!data)return;
    const seconds=Math.max(0,Math.floor((performance.now()-updatedAt)/1000));
    const age=seconds<60 ? seconds+"초" : seconds<3600 ? Math.floor(seconds/60)+"분" : seconds<86400 ? Math.floor(seconds/3600)+"시간" : Math.floor(seconds/86400)+"일";
    $("detail-expiry").textContent = age+" 전 갱신"+(!data.quote ? " · 유효한 스왑 견적 없음 · 아래 조회 사유를 확인하세요." : "");
  }
  const validAmount = () => /^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$/.test($("detail-amount").value) &&
    Number($("detail-amount").value) > 0 && Number($("detail-amount").value) <= 1000000;

  async function requestQuote() {
    if (running || !assetsReady || !modal.open || !prepare()) return;
    running = true;
    const version = revision;
    const input = {coin_id:row.coin_id, chain_index:row.chain_index, address:row.address,
      exchange:$("detail-exchange").value, amount_usdt:$("detail-amount").value, purchase_symbol:$("detail-asset").value};
    const controller = new AbortController(), started = performance.now();
    const timeout = setTimeout(() => controller.abort(), 48000);
    $("detail-status").textContent = row.exchanges?.[input.exchange]?.route_status === "bridge_candidate" ? "매수 견적·가격 영향 조회 중…" : "견적·호가·입금 상태·가스비 조회 중…";
    $("detail-refresh").disabled = true;
    try {
      const response = await fetch("/api/detail", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify(input), cache:"no-store", signal:controller.signal});
      const payload = await response.json();
      if (version !== revision || !modal.open) return;
      if (!response.ok) {
        const messages = {detail_busy:"이전 견적 조회가 진행 중입니다. 잠시 후 다시 갱신하세요.",detail_timeout:"조회 시간이 초과되었습니다. 다시 갱신하세요.",
          coin_blacklisted:"블랙리스트에 등록된 코인입니다.",network_manually_excluded:"직접 임시 제외한 입금 경로입니다.",route_not_registered:"등록된 거래 경로를 찾을 수 없습니다.",invalid_request:"금액이나 조회 항목을 확인하세요.",
          static_demo_unavailable:"정적 데모에는 기본 금액·USDT 견적만 들어 있습니다. 금액과 매수 자산을 기본값으로 되돌려 주세요."};
        $("detail-status").textContent = messages[payload.error] || "견적 조회에 실패했습니다. API 상태를 확인하고 다시 갱신하세요.";
        return;
      }
      if (payload.coin_id !== input.coin_id || payload.chain_index !== input.chain_index || payload.address !== input.address ||
        (payload.purchase_symbol || "USDT") !== input.purchase_symbol || payload.exchange !== input.exchange || Number(payload.amount_usdt) !== Number(input.amount_usdt) || payload.execution_enabled !== false ||
        !Number.isFinite(payload.expires_at_ms) || !Number.isFinite(payload.generated_at_ms)) throw Error("invalid");
      data = payload;
      // Backend expiry already subtracts server processing. Deduct only remaining transit time.
      const processing = Number.isFinite(payload.processing_ms) ? Math.max(0, payload.processing_ms) : 0;
      const transit = Math.max(0, performance.now() - started - processing);
      updatedAt = performance.now() - transit;
      deadline = performance.now() + Math.max(0, payload.expires_at_ms - payload.generated_at_ms - transit);
      render();
    } catch {
      if (version === revision && modal.open) $("detail-status").textContent = "견적 연결에 실패했습니다. 다시 갱신하세요.";
    } finally {
      clearTimeout(timeout); running = false; $("detail-refresh").disabled = !assetsReady;
    }
  }
  function updateLiquidity() {
    if (!row) return;
    $("detail-liquidity").textContent = "유동성 " + (window.DexddaUI.liquidityUsable(row.liquidity_usd) ? window.DexddaUI.liquidity(row.liquidity_usd.value) : "미확인") + " · " + window.DexddaUI.liquidityInfo(row.liquidity_usd);
  }
  function prepare() {
    invalidate();
    $("detail-refresh").disabled = running || !assetsReady;
    $("detail-status").classList.remove("detail-blocker");
    if (!row?.chain_index || !row?.address) {
      $("detail-status").textContent = "비교할 체인이 아직 선택되지 않았습니다. 후보 유동성 수집 후 다시 확인하세요."; return;
    }
    if (!validAmount()) {
      $("detail-status").textContent = "0보다 크고 1,000,000 이하인 금액을 소수점 6자리 이내로 입력하세요."; return;
    }
    $("detail-status").textContent = "설정을 확인한 후 견적 갱신을 눌러 주세요.";
    return true;
  }
  window.addEventListener("dexdda:select", async event => {
    invalidate();
    assetsReady = false;
    $("detail-refresh").disabled = true;
    $("detail-status").classList.remove("detail-blocker");
    $("detail-status").textContent = "매수 자산 설정 준비 중…";
    const firstOpen = row === null;
    row = event.detail;
    if (firstOpen && row.default_amount_usdt) $("detail-amount").value = row.default_amount_usdt;
    $("detail-title").replaceChildren(window.DexddaLogos.coin(row),document.createTextNode(row.symbol + " · 상세 견적"));
    $("detail-identity").replaceChildren(document.createTextNode((row.token_name || row.coin_id)+" / "),window.DexddaLogos.chain(node("span",row.chain_name || "체인 선택 보류"),row.chain_index),document.createTextNode(" / "+(row.native ? "네이티브 자산 · CA 없음" : row.address || "")));
    updateLiquidity();
    $("detail-copy-ca").hidden = !row.address || row.native;
    $("detail-copy-status").hidden = true;
    $("detail-copy-status").textContent = "";
    const exchanges = Object.entries(row.exchanges || {}).filter(([,q]) => q.route_status !== "excluded").map(([ex]) => ex);
    for (const option of $("detail-exchange").options) option.disabled = !exchanges.includes(option.value);
    $("detail-exchange").value = exchanges.includes("upbit") ? "upbit" : exchanges[0] || "upbit";
    if (!modal.open) modal.showModal();
    const selectedRow=row;
    await assetCatalogReady;
    if(row!==selectedRow||!modal.open)return;
    syncAssets();
    assetsReady = true;
    prepare();
  });
  window.addEventListener("dexdda:preferences-changed",()=>{if(modal.open)modal.close();});
  $("detail-copy-ca").addEventListener("click",()=>{if(row?.address && !row.native) window.DexddaUI.copyCA(row.address,$("detail-copy-status"),row.symbol+" · "+(row.chain_name||row.chain_index));});
  $("detail-form").addEventListener("submit", event => {event.preventDefault(); requestQuote();});
  $("detail-amount").addEventListener("input", () => prepare());
  $("detail-exchange").addEventListener("change", () => prepare());
  $("detail-asset").addEventListener("change", () => {preferredAsset=$("detail-asset").value;prepare();});
  $("detail-close").addEventListener("click", () => modal.close());
  modal.addEventListener("close", () => {
    // A queued native close event may arrive after the dialog has already reopened.
    if (modal.open) return;
    invalidate();
    const selected = [...document.querySelectorAll("tbody tr[data-coin-id]")].find(tr =>
      tr.dataset.coinId === row?.coin_id && tr.dataset.chain === (row?.chain_index || "") &&
      tr.dataset.address === (row?.address || ""));
    selected?.querySelector(".asset-select")?.focus({preventScroll:true});
  });
  setInterval(() => {if (modal.open) updateLiquidity(); if (modal.open && data) {if(deadline && performance.now() >= deadline) {deadline = 0; render();} updateExpiry();}}, 500);
})();
