# 체인·코인 로고 연동

2026-09-20 구현 완료. 서버 재시작 후 브라우저를 새로고침하면 적용된다.

## 표시

- 코인 이름 옆의 첫 글자 아이콘을 28px 로고로 대체한다.
- 매수 체인, 거래소 입금망, 브릿지 양끝, 상세 패널 체인명에는 16px 로고를 표시한다.
- 검증된 체인 ID·입금망 매핑만 사용한다. 미확인 매핑에는 로고를 임의로 붙이지 않는다.
- 로고 없음/이미지 로딩 실패 시 코인은 첫 글자, 체인은 기존 이름만 표시한다. 조회 및 갭 계산에는 영향을 주지 않는다.
- 직접 입금 경로 배지는 제거했으며 브릿지 필요 표시만 유지한다.

## 수집과 재사용

- CoinGecko Crypto API `/asset_platforms`에서 체인 ID 및 플랫폼 ID가 모두 일치하는 로고를 받는다.
- `/coins/markets`에서 검증된 CoinGecko coin_id를 100개씩 묶어 코인 로고를 받는다. 동일 티커끼리 혼동하지 않도록 심볼 검색은 하지 않는다.
- 코인 로고가 없는 경우에만 OKX `/api/v6/dex/market/token/basic-info`에서 체인 ID + CA를 정확히 대조한다. 기존 OKX 호출 게이트를 공유한다.
- 먼저 CoinGecko 묶음 조회를 사용해 가격·유동성 수집에 쓰이는 OKX 호출량을 줄였다.
- 공개 URL만 `data/logos.json`에 원자적으로 저장한다. API 키는 응답/캐시에 포함하지 않는다. 서버 재시작 후에도 로고를 재사용한다.
- 정상 URL은 7일, 누락된 항목은 1일 이후 다시 조회한다. 백그라운드 작업은 매시간 갱신 필요 여부를 확인한다. API 실패 시 기존 URL을 유지하고 다음 작업에서 재시도한다.
- 브라우저는 로컬 `/api/logos`를 최초 로딩 및 60초마다 확인한다. 이 API는 캐시만 읽으며 외부 API를 호출하지 않는다. 이미지 자체는 브라우저 캐시와 지연 로딩을 이용한다.
- 허용된 제공자 HTTPS 이미지 URL만 표시한다. 실패한 이미지 URL의 재시도는 브라우저 세션에서 5분간 억제한다.

## 검증

- 실제 API 초기 수집: 등록된 체인 29/29, 코인 365/365 로고 URL 확보. OKX 보완 호출 없이 수집 완료.
- Python 266개 테스트 통과. 로고 API 장애, 캐시 재사용·재시작, 누락 재시도, 안전한 URL, 정확한 ID/CA 매칭 포함.
- `scripts/check_logos_ui.cjs`: 외부 API 없이 합성 이미지로 로고 렌더링·실패 시 첫 글자·브릿지/상세 패널·모바일 가로 넘침 검증.
- 실제 제공자 이미지도 별도 브라우저 확인. 로고 제공자나 네트워크 문제로 개별 이미지가 실패해도 대체 표시한다.

## 출처

- https://docs.coingecko.com/demo/reference/asset-platforms-list
- https://docs.coingecko.com/demo/reference/coins-markets
- https://web3.okx.com/ja/onchainos/dev-docs/market/market-token-basic-info
