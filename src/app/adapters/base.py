"""Sanitized transport and error classification. No remote text in errors."""

from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx


class Failure(StrEnum):
    MISSING = "missing_config"
    IP = "ip_not_allowed"
    PERMISSION = "permission_denied"
    SIGNATURE = "signature_invalid"
    AUTH = "authentication_failed"
    CLOCK = "timestamp_invalid"
    LIMIT = "rate_limited"
    PAYMENT = "payment_required"
    NETWORK = "network_error"
    TIMEOUT = "timeout"
    SCHEMA = "unexpected_response"
    CHAIN = "chain_mismatch"
    RPC = "rpc_error"
    HTTP = "http_error"
    INTERNAL = "internal_error"
    CONFIG = "invalid_config"


MESSAGES = {
    Failure.MISSING: "필수 키가 없습니다. .env의 해당 서비스 항목을 확인하세요.",
    Failure.IP: "IP 미등록 또는 제한. 해당 서비스의 API 허용 IP를 확인하세요.",
    Failure.PERMISSION: "API 권한 부족. 해당 조회 API 권한과 서비스 접근 권한을 확인하세요.",
    Failure.SIGNATURE: "서명 검증 실패. 키 쌍과 서비스별 서명 방식을 확인하세요.",
    Failure.AUTH: "인증 실패. 키 유효성·만료·패스프레이즈를 확인하세요.",
    Failure.CLOCK: "타임스탬프/nonce 오류. 시스템 시각과 새 토큰 생성을 확인하세요.",
    Failure.LIMIT: "호출 제한. 요청 간격과 계정 한도를 확인하세요.",
    Failure.PAYMENT: "요금제 또는 쿼터 확인 필요. 자동 결제하지 않습니다.",
    Failure.NETWORK: "연결 실패. 네트워크·DNS·TLS·RPC URL 설정을 확인하세요.",
    Failure.TIMEOUT: "연결 또는 응답 시간 초과. 서비스 상태를 확인하세요.",
    Failure.SCHEMA: "예상한 응답 형식이 아닙니다. API 문서/버전을 확인하세요.",
    Failure.CHAIN: "RPC 체인 ID가 변수명의 체인 ID와 다릅니다.",
    Failure.RPC: "RPC가 조회 오류를 반환했습니다. 메서드 지원·계정 권한을 확인하세요.",
    Failure.HTTP: "HTTP 오류. 원인이 명시되지 않아 권한/IP 문제로 단정할 수 없습니다.",
    Failure.INTERNAL: "내부 처리 오류. 비밀값 보호를 위해 원문 예외는 출력하지 않습니다.",
    Failure.CONFIG: "RPC URL 형식을 확인하세요. HTTPS 또는 로컬 HTTP 주소가 필요합니다.",
}


class AdapterError(Exception):
    def __init__(self, category: Failure):
        self.category = category
        super().__init__(MESSAGES[category])


CODE_CATEGORIES = {
    "no_authorization_ip": Failure.IP,
    "NotAllowIP": Failure.IP,
    "50110": Failure.IP,
    "out_of_scope": Failure.PERMISSION,
    "50114": Failure.AUTH,
    "50125": Failure.PERMISSION,
    "jwt_verification": Failure.SIGNATURE,
    "invalid_query_payload": Failure.SIGNATURE,
    "50113": Failure.SIGNATURE,
    "expired_jwt": Failure.CLOCK,
    "nonce_used": Failure.CLOCK,
    "50102": Failure.CLOCK,
    "50112": Failure.CLOCK,
    "expired_access_key": Failure.AUTH,
    "no_authorization_token": Failure.AUTH,
    "50103": Failure.AUTH,
    "50104": Failure.AUTH,
    "50105": Failure.AUTH,
    "50106": Failure.AUTH,
    "50107": Failure.AUTH,
    "50111": Failure.AUTH,
    "10002": Failure.AUTH,
    "10010": Failure.AUTH,
    "10011": Failure.AUTH,
    "50011": Failure.LIMIT,
}


def classify(status: int, payload: Any) -> Failure:
    code = None
    if isinstance(payload, dict):
        error = payload.get("error")
        api_status = payload.get("status")
        code = payload.get("code")
        if isinstance(error, dict):
            code = error.get("name", error.get("code", code))
        if isinstance(api_status, dict):
            code = api_status.get("error_code", code)
    if isinstance(code, (str, int)) and str(code) in CODE_CATEGORIES:
        return CODE_CATEGORIES[str(code)]
    if status in (418, 429):
        return Failure.LIMIT
    if status == 402:
        return Failure.PAYMENT
    if status == 401:
        return Failure.AUTH
    return Failure.HTTP


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    check_api_error: bool = True,
    decimal_numbers: bool = False,
    **kwargs,
) -> Any:
    try:
        response = await client.request(method, url, follow_redirects=False, **kwargs)
    except httpx.TimeoutException:
        raise AdapterError(Failure.TIMEOUT) from None
    except httpx.RequestError:
        raise AdapterError(Failure.NETWORK) from None
    except (ValueError, httpx.InvalidURL):
        raise AdapterError(Failure.CONFIG) from None
    try:
        payload = response.json(**({"parse_float": Decimal} if decimal_numbers else {}))
    except ValueError:
        if not response.is_success:
            raise AdapterError(classify(response.status_code, None)) from None
        raise AdapterError(Failure.SCHEMA) from None
    if not response.is_success:
        raise AdapterError(classify(response.status_code, payload))
    if check_api_error and isinstance(payload, dict) and payload.get("error"):
        raise AdapterError(classify(response.status_code, payload))
    return payload


def require_config(credentials, *names):
    if not credentials.present(*names):
        raise AdapterError(Failure.MISSING)
