"use strict";
/* 정적 데모 전용 백엔드 대역. 대시보드의 /api/* 호출을 data/demo.json(빌드 시 캡처한 합성 응답)으로
   응답한다. 외부 요청·서명·전송은 없다. 숨김 설정과 DRY_RUN 이력은 이 탭의 메모리에만 남는다. */
(() => {
  const realFetch = window.fetch.bind(window);
  const bundle = realFetch("data/demo.json", {cache: "no-store"})
    .then(r => { if (!r.ok) throw new Error("demo_data_unavailable"); return r.json(); });
  const state = {exclusions: [], history: null, flows: new Map()};
  const clone = value => JSON.parse(JSON.stringify(value));

  // 캡처 시각 기준 epoch ms 값을 지금 기준으로 옮겨 "방금 전" 수신처럼 보이게 한다.
  function shift(value, delta) {
    if (Array.isArray(value)) return value.map(v => shift(v, delta));
    if (!value || typeof value !== "object") return value;
    const out = {};
    for (const [key, v] of Object.entries(value)) {
      out[key] = key.endsWith("_ms") && typeof v === "number" && v > 1e12 ? v + delta : shift(v, delta);
    }
    return out;
  }
  const reply = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: {"Content-Type": "application/json", "Cache-Control": "no-store"},
  });
  const pause = (ms, signal) => new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, {once: true});
  });
  // 번들 값은 요청 시점의 "지금"으로 옮긴다. 탭 안 상태(이력·숨김)는 넣을 때 한 번만 고정한다.
  const live = (value, data) => shift(clone(value), Date.now() - data.captured_at_ms);
  const requestKey = r => [r.coin_id, r.chain_index, r.address, r.exchange, r.purchase_symbol || "USDT",
    String(Number(r.amount_usdt))].join("|");
  const sameEntry = (a, b) => a.kind === b.kind && a.coin_id === b.coin_id &&
    (a.exchange || null) === (b.exchange || null) && (a.net_type || null) === (b.net_type || null);

  // 서버의 숨김 규칙(collector)과 같게: 코인 블랙리스트는 preferences로, 네트워크 제외는 경로별로 반영.
  function withPreferences(gaps) {
    gaps.preferences = {version: 1, exclusions: clone(state.exclusions)};
    const networks = state.exclusions.filter(e => e.kind === "network");
    for (const group of gaps.coin_groups) for (const route of group.routes) {
      for (const [exchange, quote] of Object.entries(route.exchanges || {})) {
        for (const n of quote.deposit_networks || []) {
          const hit = networks.find(e => e.coin_id === route.coin_id && e.exchange === exchange && e.net_type === n.net_type);
          n.manual_excluded = !!hit;
          n.exclusion_reason = hit ? hit.reason || null : null;
        }
        const relevant = (quote.deposit_networks || []).filter(n => n.applies_to_route);
        quote.manual_excluded = relevant.length > 0 && relevant.every(n => n.manual_excluded);
      }
    }
    return gaps;
  }

  function answer(path, method, body, data) {
    const get = data.get;
    if (method === "GET") {
      if (path === "/api/gaps") return reply(withPreferences(live(get[path], data)));
      if (path === "/api/preferences") return reply({...get[path], exclusions: state.exclusions});
      if (path === "/api/swaps") return reply({items: state.history});
      const swap = path.match(/^\/api\/swap\/([0-9a-f]+)(\/completion)?$/);
      if (swap) {
        if (swap[2]) return reply({addresses: []});
        const entry = state.history.find(e => e.id === swap[1]);
        return entry ? reply(entry) : reply({error: "not_found", message: "요청을 찾을 수 없습니다."}, 404);
      }
      return get[path] ? reply(live(get[path], data)) : reply({error: "not_found"}, 404);
    }
    if (path === "/api/preferences") {
      const {excluded, ...entry} = body;
      if (entry.kind === "coin") { delete entry.exchange; delete entry.net_type; }
      state.exclusions = state.exclusions.filter(e => !sameEntry(e, entry));
      if (excluded) state.exclusions.push({...entry, reason: entry.reason || "", created_at_ms: Date.now()});
      return reply({version: 1, exclusions: state.exclusions});
    }
    if (path === "/api/wallet-status/refresh") return reply({ok: true});
    if (path === "/api/balances/refresh") return reply(live(get["/api/balances"], data));
    if (path === "/api/detail") {
      const detail = data.detail[requestKey(body)];
      return detail ? reply(live(detail, data)) : reply({error: "static_demo_unavailable", execution_enabled: false}, 422);
    }
    if (path === "/api/swap/prepare") {
      const flow = data.swap[requestKey(body)];
      if (!flow) return reply({error: "static_demo_unavailable", message: "정적 데모에는 이 경로의 DRY_RUN 흐름이 없습니다."}, 409);
      const intent = live(flow.prepare, data);
      state.flows.set(intent.id, {flow, expires: intent.expires_at_ms, used: false});
      return reply(intent);
    }
    if (path === "/api/swap/confirm") {
      // 서버와 같게: 만료됐거나 이미 확인한 intent는 새 확인이 필요하다.
      const pending = state.flows.get(body.intent_id);
      if (!pending || pending.used || body.confirmed !== true || Date.now() > pending.expires)
        return reply({error: "new_confirmation_required", message: "새 확인이 필요합니다."}, 409);
      pending.used = true;
      const final = live(pending.flow.final, data);
      state.history = [final, ...state.history.filter(e => e.id !== final.id)];
      return reply(live(pending.flow.confirm, data));
    }
    return reply({error: "not_found"}, 404);
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return realFetch(input, init);
    init.signal?.throwIfAborted();
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : null;
    const data = await bundle;
    init.signal?.throwIfAborted();
    state.history ??= live(data.get["/api/swaps"].items, data);
    // 느린 조회(견적·실행 준비)는 실제처럼 잠깐 기다린다.
    await pause(/^\/api\/(detail|swap)/.test(url.pathname) ? 450 : 40, init.signal);
    return answer(url.pathname, method, body, data);
  };
})();
