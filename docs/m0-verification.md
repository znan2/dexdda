# M0 검증 기록

상태: **M0 완료 — 4개 서비스와 설정된 RPC 5개 모두 실제 연결 검증 통과.**

## 최종 자동 검증

- `uv run pytest -q`: **62 passed**, 경고 없음.
- `uv run ruff check src tests scripts`: 통과.
- `uv run ruff format --check src tests scripts`: 24개 파일 통과.
- 필수 프로젝트·설정·어댑터·스모크 스크립트·fixture·문서 존재 확인.
- .env의 Git 제외 규칙 확인. API 수정은 사용자가 수행했고 검증 과정에서는 값을 수정하지 않았다.
- 앞선 실행 검증에서 실제 dexdda 프로세스의 /api/health HTTP 200과 127.0.0.1:8000 리스너를 확인했다. 검증 프로세스는 종료했다.

## 최종 실제 연결

실행 명령: `uv run scripts/smoke_test.py --json`  
종료 코드: **0**  
설정: **DRY_RUN=true**, M0 읽기 전용.

| 대상 | 결과 | 확인 범위 |
|---|---|---|
| OKX | PASS | 지원 체인 조회·인증 |
| 업비트 | PASS | 기존 입금 주소 목록 조회·인증 |
| 빗썸 | PASS | 기존 입금 주소 목록 조회·인증 |
| CoinGecko Demo | PASS | Crypto API ping |
| RPC 1 | PASS | eth_chainId 일치 |
| RPC 56 | PASS | eth_chainId 일치 |
| RPC 137 | PASS | eth_chainId 일치 |
| RPC 8453 | PASS | eth_chainId 일치 |
| RPC 42161 | PASS | eth_chainId 일치 |

이번 한 번의 통합 실행에서 **9/9 PASS**를 확인했다.

## 완료 기준 대조

| M0 요구사항 | 확인 근거 |
|---|---|
| uv 프로젝트와 설정·패키지 골격 | pyproject.toml, uv.lock, config/, src/app/, scripts/, tests/ |
| .env.example 및 settings.toml 기본값 | 해당 파일 존재, 설정 로더 테스트 통과 |
| 4종 인증 어댑터 | 서명/JWT 테스트 및 실제 4개 서비스 조회 성공 |
| 서비스별 읽기 전용 호출 1회 | MockTransport 요청 검증, 실제 스모크 성공 |
| 실패 원인 구분 | IP·권한·서명·시간·호출 제한·네트워크·RPC 오류 테스트 |
| 비밀값 비노출 | 합성 API 키·개인키·JWT·RPC URL·입금 주소·원문 오류의 출력/로그 비노출 테스트 |
| 로컬 서버만 사용 | 127.0.0.1 설정 강제, 실제 리스너 확인, Host/CORS 테스트 |
| 외부 어댑터 fixture 테스트 | tests/fixtures/responses.json 및 어댑터 테스트 |

## 해결 이력과 검증 한계

초기 OKX 검증 실패는 문서의 String 타입과 실제 chainIndex의 Integer 타입 차이였다.
두 형태를 허용하도록 수정하고 회귀 테스트 및 실제 재검증을 통과했다.

초기 빗썸 HTTP 401 jwt_verification 오류는 사용자가 API 설정을 수정한 뒤 최종 통합 실행에서 해소됐다.
키의 어느 부분이 변경됐는지는 출력·기록하지 않았다.

CoinGecko ping은 후속 레지스트리 API의 권한·쿼터를 보장하지 않는다.
RPC eth_chainId는 스왑 지원·잔고·USDT 컨트랙트 검증을 대체하지 않는다.
유료 API 필요성은 후속 구현에서 확인한다.

M0는 거래 서명·전송·approve·주소 생성·주문·출금을 수행하지 않았다.
M1은 시작하지 않았다. 다음 단계는 종목·네트워크·컨트랙트 레지스트리 구축이다.
비밀값·인증 토큰·RPC 전체 URL·실제 입금 주소·원문 응답은 이 기록에 포함하지 않는다.

최종 검증 기록 시각: 2026-09-19T20:49:38+09:00
