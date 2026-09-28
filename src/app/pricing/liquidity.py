"""Last observed liquidity is a reference, not a fresh execution quote."""

REASONS = {
    "missing": "아직 정상 유동성을 관측하지 못함",
    "stale": "유동성 갱신 기한 경과",
    "token_not_returned": "API 응답에 해당 토큰이 없음",
    "value_missing": "API 응답에 유동성 값이 없음",
    "value_invalid": "API 유동성 값의 형식 또는 범위 오류",
    "source_time_missing": "API 응답에 관측 시각이 없음",
    "source_time_invalid": "API 관측 시각 형식 오류",
    "timestamp_invalid": "API 관측 시각이 서버 시각보다 미래임",
    "missing_response": "토큰 응답 누락 또는 값 오류 · 이전 수집기 분류",
    "rate_limited": "API 호출 제한",
    "network_error": "API 연결 실패",
    "timeout": "API 응답 시간 초과",
    "unexpected_response": "API 응답 구조 오류",
    "authentication_failed": "API 인증 실패",
    "permission_denied": "API 조회 권한 부족",
    "ip_not_allowed": "API 허용 IP 제한",
    "signature_invalid": "API 서명 검증 실패",
    "payment_required": "API 요금제 또는 쿼터 확인 필요",
    "http_error": "API HTTP 요청 오류",
    "internal_error": "유동성 수집 내부 오류",
}


def liquidity_reference(quotes, key, ttl):
    data = quotes.public(key, ttl, source_age=False)
    receipt = quotes.values.get(key)
    usable = bool(receipt and receipt.price.value.is_finite() and receipt.price.value >= 0)
    return {
        **data,
        "usable": usable,
        "reused": usable and data["stale"],
        "age_seconds": max(0, quotes.clock.monotonic() - receipt.received_at) if receipt else None,
        "last_attempt_at_ms": quotes.attempted_at_ms.get(key),
        "error_message": REASONS.get(data["error"], "유동성 수집 오류") if data["error"] else None,
    }
