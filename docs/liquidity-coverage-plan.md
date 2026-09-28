# 유동성 조회 범위 개선안

2026-09-20 조사. 아래는 제안이며 아직 구현하지 않았다. 실행 코드·레지스트리·API 요금제는 변경하지 않았다.

## 관측 결과

현재 등록된 912개 경로 중 830개는 유동성을 조회하고 82개는 정상 관측 이력이 없다. 미조회 사유는 token_not_returned 81개, value_missing 1개다. 전체 등록 경로가 미조회인 코인은 NEOPIN coin_id로 연결된 MAY, META, MOC, PYTH, TEMCO, TOKAMAK 6개다. 여러 체인에서 동시에 정상/누락이 있으므로 특정 체인 전체 장애로 판단할 근거는 없다.

공개 DEX Screener API를 체인·CA가 정확히 일치하는 경우만 사용해 표본 조회했다. 82개 전체에 대한 보완율 검증은 아직 하지 않았다.

| 대상 | 표본 조회 결과 | 해석 및 다음 단계 |
|---|---|---|
| MAY/NPT Polygon | 등록 CA의 풀 응답 없음 | 빗썸의 NPT→MAY/Solana 이전 공지와 현 레지스트리의 구 NPT Polygon 경로가 충돌한다. 구 버전과 현 버전을 분리해야 한다. |
| MAY Solana | 저장된 CoinGecko mint에서 풀 1개, 약 $32.99 | 조회는 가능하지만 현재 $100k 최소 기준 미달. 이 결과가 모든 DEX의 유동성을 대표한다고 단정하지 않는다. |
| PYTH Manta | OKX 미조회, DEX Screener Manta 매핑은 이번 조사에서 미검증 | 현 EVM 경로만으로 주요 거래 풀을 포착하기 어렵다. |
| PYTH Solana | 저장된 CoinGecko mint에서 30개 풀 응답, 그중 최대 약 $452,130.26, 다음 약 $138,723.51 | 조회 전용 Solana 지원이 실제 후보 복구에 도움이 되는 사례. 모든 풀의 전수 조회 결과는 아니다. |
| TOKAMAK TON Ethereum | 등록 원본 CA의 풀 응답 없음 | 공식 문서상 DEX 사용 시 WTON 형태를 쓸 수 있다. 별도 변환 경로가 필요하다. |
| WTON Ethereum | 공식 CA에서 풀 2개, 약 $42,248.30 / $35,877.09 | 원본 TON과 동일 CA로 취급하지 않는다. 표시 후보로 연결할 때에도 래핑/언래핑 및 decimals 차이를 명시한다. 관측 풀들은 각각 $100k 미달. |
| META, MOC, TEMCO 등록 경로 | DEX Screener에서도 풀 응답 없음 | 풀 존재 여부·토큰 이전·현재 체인을 추가 확인해야 한다. 풀 응답 없음은 유동성 0 또는 실제 풀 부재의 증명이 아니다. |
| WETH 대조군 | Ethereum에서 다수의 정상 풀·유동성 응답 | 위 빈 응답이 모든 DEX Screener 조회 실패인 것은 아님을 확인했다. |

수치는 조사 시점의 응답이며 거래 가능 금액이나 확정 기회를 의미하지 않는다. 외부 공급자의 재조회가 필요하다.

## 권장 순서

### 1. 토큰 매핑과 상태 분류 정비

- coin_id/심볼 외에 chain_id, CA, token_version, 관계(direct/wrapped/migrated/bridged), 검증 출처를 보관한다.
- MAY의 구 NPT 경로는 현 MAY와 바로 연결해 갭을 계산하지 않는다. 이전이 현재도 가능한지 별도로 확인한다.
- 상태를 공급자 응답 누락, 지원 체인 아님, 복수 공급자에서 풀 미발견, 구 버전/변환 필요, 실제 저유동성, 일시 요청 실패로 구분한다.
- 빈 응답에 대해 유동성 0을 만들어 넣지 않는다. 거래소 24시간 거래대금 역시 DEX 유동성을 대신할 수 없다.

### 2. 미조회 경로에만 보조 공급자 사용

- 기본 OKX 수집은 유지한다. 미조회 경로에 DEX Screener, 이어 CoinGecko Onchain/GeckoTerminal 계열을 제한적으로 조회한다.
- DEX Screener는 무료 API가 있으며 토큰 풀 조회는 공식 문서상 분당 300회 제한이다. 실제 작업 속도는 이보다 낮게 제한한다.
- CoinGecko는 기존 Crypto 메타데이터 대신 Onchain 토큰/풀 API를 사용한다. 토큰 total_reserve_in_usd와 풀 reserve_in_usd를 구별한다. Demo API에도 해당 엔드포인트가 문서화돼 있으나 계정 권한·쿼터는 구현 시 확인한다. 자동 유료 전환은 하지 않는다.
- 먼저 82개 전수 조사 보고서를 생성해 체인 지원 여부, 보완 성공 수, $100k 이상 후보 수, 필요한 호출량을 평가한다. 값이 조회되는 비율과 실제 후보가 늘어나는 비율은 별도로 본다.
- 값은 chain+CA로만 결합하고 심볼만 같은 토큰은 배제한다. 공급자별 체인 이름은 명시적인 매핑표로 관리한다.

### 3. 유동성의 의미와 가격 출처를 함께 관리

- 공급자 원본 값, 출처, 수신 시각, 공급자 시각(있을 때만), 풀 식별자, 단일 풀/합계 구분을 보관한다.
- OKX의 token liquidity와 다른 API의 단일 풀 reserve를 검증 없이 같은 지표로 단정하지 않는다. 공급자 값을 더하거나 가장 큰 공급자 값을 골라 낙관적으로 표시하지 않는다.
- 비교용으로 확인된 대표 풀의 유동성을 별도 관리하는 방향을 권장한다. 전환기에는 지표 범위와 출처를 표시하고 기존 값과의 차이를 측정한다.
- 같은 풀을 여러 API에서 찾아도 중복 합산하지 않는다. 반환된 풀 목록이 전체 목록이라는 가정을 하지 않는다.
- 유동성을 복구해도 OKX 가격이 없으면 갭은 여전히 계산되지 않는다. 보조 풀의 정확한 대상 토큰 가격도 함께 수집하는 설계가 필요하다. base/quote 방향과 decimals를 확인하고 현재 가격의 신선도는 별도로 검사한다.
- 보조 풀 가격은 참고 갭에만 사용하고 실제 실행은 기존 실시간 견적 검사를 통과해야 한다. 풀 TVL이 곧 매수 가능 깊이는 아니다.

### 4. 정상 관측값 영구 보관 및 재조회 조절

- SQLite 등 로컬 저장소에 검증된 마지막 정상값을 보관해 재시작 후에도 관측 시각과 함께 복원한다. 저장된 이전값은 자동으로 신선한 값이 되지 않는다.
- 정상 유동성 갱신은 기존 5분 주기를 유지하는 초기안을 권장한다.
- 보조 공급자의 풀 탐색과 이미 찾은 풀의 수치 갱신을 분리한다.
- 풀 미발견은 예를 들어 30분→2시간→6시간으로 재탐색 간격을 늘린다. 일시 연결 실패·429는 별도 backoff를 사용한다. 사용자가 특정 코인을 새로 조사할 수 있는 기능을 둔다.
- 이전값 재사용은 시작 시점부터 사라지는 문제만 해결하며, 처음부터 미관측인 82개를 복구하지는 못한다.
- 예산 계산: 토큰별 1회 호출 방식으로 82개를 5분마다 조회하면 30일 약 708,480회다. 같은 방식의 6시간 재탐색은 9,840회다. 배치/풀 조회/다른 작업을 제외한 단순 계산이므로 실제 계정 쿼터 내 별도 호출 예산이 필요하다.

### 5. 지원 경로 확장

- PYTH 사례를 근거로 Solana는 조회 전용 지원부터 검토한다. 현재 EVM 전용 레지스트리·주소 정규화와 분리하며 Solana 주소는 대소문자를 유지한다. 조회 지원이 곧 스왑 지원은 아니다.
- TEMCO 등의 Kaia 경로는 공식 CA와 실제 풀 존재를 확인한 뒤 조회 어댑터 지원을 검토한다.
- WTON→TON처럼 검증된 변환 관계는 이후 별도 경로로 추가한다. 변환 비용·소수점·실행 가능 여부 검증 없이 직접 입금 경로로 승격하지 않는다.
- RPC 직접 조회는 알려진 풀 주소를 수동 등록하는 보완책부터 시작한다. CA 하나로 모든 DEX 풀을 자동 발견하는 대체 수단으로 보지 않는다. V2/V3 등 풀 구조별 계산이 달라 후순위로 둔다.

## 1차 구현 범위 제안

토큰 버전/구 경로 정리 → 미조회 82개 보완 조사 → DEX Screener 보조 수집과 출처 표시 → 마지막 정상값 영구 보관 → 재탐색 간격 조절 순서다. CoinGecko 보조 수집은 전수 표본 결과와 쿼터를 보고 붙인다. Solana 조회 및 래핑 변환은 별도 단계로 진행한다.

## 근거

- [DEX Screener API](https://docs.dexscreener.com/api/reference), [무료 API 안내](https://docs.dexscreener.com/)
- [CoinGecko Demo 토큰 데이터](https://docs.coingecko.com/demo/reference/token-data-contract-address), [토큰별 풀](https://docs.coingecko.com/demo/reference/top-pools-contract-address)
- [OKX 토큰 거래 정보](https://web3.okx.com/onchainos/dev-docs/market/market-token-price-info)
- [빗썸 NPT→MAY/Solana 전환 공지](https://feed.bithumb.com/notice/1648818)
- [Tokamak TON↔WTON 공식 가이드](https://docs.tokamak.network/home/information/ton-wton)
- [관측된 PYTH Solana 풀](https://dexscreener.com/solana/9n3dslrerzqp95dhxywft7xv8d8xngflauhtehqvaxac)
- [관측된 WTON Ethereum 풀](https://dexscreener.com/ethereum/0x610468b2c5d1bd72c2093c47a6d2da68037c34e2)
