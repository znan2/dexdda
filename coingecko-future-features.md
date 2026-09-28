# CoinGecko API 선택과 향후 기능 후보

검토일: 2026-09-19
상태: 후속 기능 검토 메모. 이 문서의 항목은 현재 구현 범위에 자동으로 추가되지 않는다.

## 현재 사용할 API

**현재 준비할 것은 Crypto API의 CoinGecko Demo API 키다.** 발급한 키를 로컬 `.env`의 `COINGECKO_API_KEY`에 입력한다. 유료 사용 여부는 구현 중 또는 구현 후 검토에서 결정한다.

| 구분 | 데이터의 중심 | 현재 프로젝트에서의 역할 |
|---|---|---|
| Crypto API | CoinGecko 코인 ID, 거래소 종목, 체인별 컨트랙트, 코인 메타데이터 | M1 레지스트리 생성에 사용 |
| On-chain DEX API | 네트워크·토큰 주소·풀 주소별 가격, 유동성, 거래, 차트 | 아래 후속 기능 후보에 활용 |

CoinGecko는 두 데이터 영역을 같은 API 제품군에서 제공한다. Demo 문서에는 Crypto API의 거래소 티커와 On-chain의 토큰별 풀 조회 모두 `https://api.coingecko.com/api/v3` 및 `x-cg-demo-api-key` 인증을 사용한다. 따라서 메뉴 두 개를 서로 별개의 키를 반드시 구매해야 하는 상품으로 해석하지 않는다. 접근 가능한 엔드포인트와 제한은 요금제별로 확인한다. [API 개요](https://docs.coingecko.com/), [Demo 거래소 티커](https://docs.coingecko.com/demo/reference/exchanges-id-tickers), [Demo 토큰별 풀](https://docs.coingecko.com/demo/reference/top-pools-contract-address)

현재 데이터 흐름:
- CoinGecko Crypto API: `거래소 종목 → coin_id → 체인별 CA` 후보 매핑.
- 업비트·빗썸 직접 API: 실제 마켓, 호가, 입금 지원 네트워크와 상태.
- OKX: DEX 가격, 금액별 견적, 스왑 트랜잭션 데이터.
- 자체 RPC: 잔고, allowance, 로컬 서명한 트랜잭션 전송과 영수증.

Crypto API의 `/exchanges/{id}/tickers`는 `coin_id`를 제공하고, `/coins/list?include_platform=true`는 `platforms`의 CA를 제공한다. `/asset_platforms`에는 `chain_identifier`가 있지만 null도 허용되므로 자동 매핑의 완전성을 가정하지 않는다. CoinGecko 결과 자체는 거래소가 해당 CA의 입금을 받는다는 증명이 아니다. [코인 목록](https://docs.coingecko.com/reference/coins-list), [플랫폼 목록](https://docs.coingecko.com/reference/asset-platforms-list)

## 우선 검토할 기능

아래 활용 방법과 우선순위는 프로젝트에 맞춘 설계 제안이다. API가 해당 기능을 완성된 형태로 제공한다는 뜻은 아니다.

### 1. 토큰별 유동성 풀 비교 — 우선순위 높음

- 목적: 높은 갭이 관측된 토큰의 거래량과 풀 규모를 함께 확인하고, 조회 전용 브릿지 후보의 체인 선택 근거를 보강한다.
- 데이터: 풀 주소, DEX, `reserve_in_usd`, 기간별 `volume_usd`, 거래 건수.
- API: `GET /onchain/networks/{network}/tokens/{token_address}/pools`.
- UI 제안: 상세 패널에 상위 풀의 DEX명·유동성·거래량·가격을 나란히 표시.
- 호출 방식: 상세 화면을 열 때 또는 후보 체인을 비교할 때 조회하고 결과를 캐시한다.
- 제한: 풀 전체 유동성은 특정 금액을 현재 가격에 체결할 수 있는 깊이와 다르다. 실제 매수 가능 수량은 OKX 견적으로 확인한다. 조회된 풀이 OKX 라우팅에 포함된다고 가정하지 않는다.
- 도입 조건: Demo 접근과 계정별 제한, 기존 OKX 유동성 조회 대비 추가 가치 확인. 브릿지 실행은 계속 금지한다.
- 근거: [Demo Top Pools](https://docs.coingecko.com/demo/reference/top-pools-contract-address).

### 2. OKX 가격과 별도 소스의 가격 교차 확인 — 우선순위 높음

- 목적: 급격히 벌어진 갭에서 가격 출처 간 차이를 표시해 오래된 가격, 다른 풀의 가격 또는 매핑 문제를 조사하기 쉽게 한다.
- API: `GET /onchain/networks/{network}/tokens/multi/{addresses}`.
- 데이터: `price_usd`, `coingecko_coin_id`, `total_reserve_in_usd`, 필요 시 `include=top_pools`.
- UI 제안: OKX 가격·CoinGecko On-chain 가격·차이·조회 시각을 표시. 차이가 크다는 이유만으로 어느 쪽이 틀렸다고 확정하지 않는다.
- 호출 방식: 의심 목록이나 사용자가 선택한 종목 위주로 제한한다.
- 제한: 반드시 동일 체인·CA·가격 단위·조회 시점을 맞춘다. 누락된 주소와 null 가격은 미확인으로 처리한다. 참조 가격이 실행 가능한 스왑 견적을 대체하지 않는다.
- 도입 조건: Demo 문서는 요청당 최대 30개 주소, Analyst 이상은 50개로 안내한다. 구현 시 실제 계정에서 재확인한다. 응답 누락은 요청 목록과 대조한다.
- 근거: [Demo Multiple Tokens](https://docs.coingecko.com/demo/reference/tokens-data-contract-addresses).

### 3. 가격·거래량 차트와 갭 지속시간 — 우선순위 중간

- 목적: 잠깐 발생한 가격 이탈인지, 일정 시간 지속되는 차이인지 사용자가 판단할 자료를 제공한다.
- API: `GET /onchain/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}`.
- 데이터: 시각, 시가·고가·저가·종가, 거래량.
- UI 제안: 토큰 상세에 풀 가격과 거래량 차트를 표시하고, 별도로 수집한 DEX·CEX·업비트 USDT 데이터를 이용해 갭 추이를 표시.
- 제한: API가 프로젝트의 과거 갭이나 과거 실효갭을 제공하지는 않는다. 같은 시각의 CEX 호가와 금액별 DEX 견적은 직접 기록해야 한다. 캔들만으로 과거 실효갭을 복원하지 않는다.
- 도입 조건: 요금제별 이력 범위·시간 간격·호출 한도를 재확인한다. 풀의 base/quote와 요청한 토큰 방향을 명시하고, 무거래 구간을 실제 체결로 해석하지 않는다.
- 근거: [Pool OHLCV](https://docs.coingecko.com/reference/pool-ohlcv-contract-address).

### 4. 최근 대규모 거래·거래 공백 표시 — 우선순위 중간

- 목적: 갭 직전에 큰 거래가 있었는지, 실제 거래가 드문 풀인지 확인한다.
- API: `GET /onchain/networks/{network}/pools/{pool_address}/trades`.
- 데이터: `tx_hash`, `block_timestamp`, `volume_in_usd`, 입출력 토큰 주소·수량, 매수·매도 구분.
- UI 제안: 상세 화면에 최근 주요 거래와 마지막 거래 시각을 표시.
- 제한: 큰 거래나 매수·매도 편향만으로 조작·사기·향후 가격을 판정하지 않는다. API가 반환한 최근 구간에 데이터가 없다는 사실과 체인 전체의 무거래를 구분한다.
- 도입 조건: 현재 문서는 추가 옵션 없이 최근 24시간 내 마지막 300건을 반환하며, 기간 선택·커서 등은 Analyst 이상이 필요하다고 명시한다. 긴 이력을 필수 요건으로 만들기 전에 비용을 검토한다.
- 근거: [Pool Trades](https://docs.coingecko.com/reference/pool-trades-contract-address).

### 5. 토큰 검토 카드와 매핑 변경 감지 — 우선순위 중간

- 목적: 비슷한 티커의 토큰을 육안으로 구분하고, 프로젝트 링크·컨트랙트 변경을 검토하기 쉽게 한다.
- API: Crypto API의 `GET /coins/{id}`, 기존 `/coins/list?include_platform=true`.
- 데이터: 이름, 이미지, `platforms`, 홈페이지·탐색기 링크, `public_notice`, `additional_notices`.
- UI 제안: 상세 카드에 정보 출처·조회일과 링크를 표시하고, 저장된 CA와 새 CA가 다르면 재검증 대상으로 분류.
- 제한: 로고·이름·외부 링크·공지 필드는 보조 자료이며 자산의 안전성 또는 거래소 입금 수락을 증명하지 않는다. CA 변경을 자동으로 기존 실행 허용 목록에 반영하지 않는다.
- 도입 조건: 레지스트리 갱신 때 또는 상세 조회 때만 호출한다. 외부 텍스트·링크를 안전하게 표시한다.
- 근거: [Coin Data](https://docs.coingecko.com/reference/coins-id), [Coins List](https://docs.coingecko.com/reference/coins-list).

### 6. 거래소 시세 품질 보조 표시 — 우선순위 낮음

- 목적: 기존 레지스트리 구축 응답을 활용해 비정상·오래된 거래소 시세의 검토 근거를 남긴다.
- API: Crypto API의 `GET /exchanges/{id}/tickers`.
- 데이터: `is_stale`, `is_anomaly`, `last_traded_at`, `last_fetch_at`, `bid_ask_spread_percentage`.
- UI 제안: 레지스트리 리포트 또는 별도 검토 패널에 플래그와 출처·시각을 표시.
- 제한: 레지스트리 생성 시 받은 값을 실시간 거래소 상태로 쓰지 않는다. 실효갭은 계속 업비트·빗썸 직접 호가로 계산한다. CoinGecko의 스프레드·깊이 요약값은 실제 호가 소진 계산을 대체하지 않는다.
- 도입 조건: 기존 조회에서 얻는 필드부터 저장하며, 추가 고빈도 폴링은 필요성을 별도로 검토한다.
- 근거: [Demo Exchange Tickers](https://docs.coingecko.com/demo/reference/exchanges-id-tickers).

## 도입 시 공통 조건

- On-chain의 `network` ID, Crypto API의 platform ID, EVM chain ID는 별도 식별자다. 예를 들어 `eth`, `ethereum`, `1`을 같은 문자열로 취급하지 않는다. `/onchain/networks`의 `coingecko_asset_platform_id`를 연결 정보로 활용한다. [Networks List](https://docs.coingecko.com/reference/networks-list)
- Demo/유료 문서와 실제 계정 응답을 함께 확인한다. 유료 예제의 한도나 기능을 Demo에 그대로 적용하지 않는다.
- 과금 여부는 종목 수·조회 빈도·배치 크기·캐시 효과를 측정한 뒤 결정한다. 추가 API 키나 요금제를 지금 구매할 필요는 없다.
- 미확인 필드·누락 응답·지원하지 않는 체인은 미확인으로 표시한다. 기존 레지스트리의 체인·CA 검증 규칙을 우회하지 않는다.
- 갭 계산은 사용자 결정대로 업비트 USDT를 공통 기준으로 사용하고, 업비트·빗썸 거래 수수료는 제외한다.
- 브릿지 후보는 조회 전용이며, 이 문서의 분석 기능을 추가해도 브릿지 후보 스왑을 허용하지 않는다.
- 권장 검토 순서는 풀 비교 → 가격 교차 확인 → 차트·거래 활동 → 메타데이터 보강이다. 기능별 호출 비용과 표시 효용을 보고 선택한다.
