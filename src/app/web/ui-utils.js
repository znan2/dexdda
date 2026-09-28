"use strict";
window.DexddaUI = (() => {
  const compact = new Intl.NumberFormat("en-US", {notation:"compact", maximumFractionDigits:1});
  function liquidity(value) {
    if(value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
    return "$" + compact.format(Number(value)).toLowerCase();
  }
  async function copyCA(address, status, label="") {
    status.hidden=false;
    if(!/^0x[0-9a-fA-F]{40}$/.test(address || "")) {
      status.textContent="복사할 CA가 없습니다.";return;
    }
    try {
      await navigator.clipboard.writeText(address);
      status.textContent=(label ? label+" · " : "")+"CA 복사 완료: "+address;
    } catch {
      status.textContent="자동 복사 실패 · 아래 CA를 직접 선택해 복사하세요: "+address;
    }
  }
  const liquidityUsable = q => !!q && (q.usable ?? (!q.stale && q.value !== null));
  function observedTime(value, now = Date.now()) {
    if (!Number.isFinite(value)) return "없음";
    const minutes = Math.floor(Math.max(0, now - value) / 60000);
    if (minutes < 1) return "방금 전";
    if (minutes < 60) return minutes + "분 전";
    const hours = Math.floor(minutes / 60);
    return hours < 24 ? hours + "시간 전" : Math.floor(hours / 24) + "일 전";
  }
  function liquidityInfo(q) {
    if (!q) return "정상 관측 이력 없음";
    const parts = [];
    if (q.reused) parts.push("마지막 정상값 재사용");
    parts.push(q.received_at_ms ? "마지막 정상 수신 " + observedTime(q.received_at_ms) : "정상 관측 이력 없음");
    if (q.source_at_ms) parts.push("API 관측 " + observedTime(q.source_at_ms));
    if (q.error) parts.push((q.error_message || "유동성 수집 오류") + " [" + q.error + "]");
    if (q.last_attempt_at_ms && q.error) parts.push("최근 조회 " + observedTime(q.last_attempt_at_ms));
    return parts.join(" · ");
  }
  return {liquidity,copyCA,liquidityUsable,liquidityInfo,observedTime};
})();
