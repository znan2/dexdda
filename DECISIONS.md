# 구현 결정 기록

## 2026-09-19 — M0

### 인증과 공식 문서

- OKX: timestamp + 대문자 HTTP 메서드 + query 포함 경로 + 원문 body를 HMAC-SHA256으로 서명하고 Base64 인코딩한다. UTC 밀리초 timestamp를 사용한다. [API 인증](https://web3.okx.com/onchainos/dev-docs/home/api-access-and-usage)
- 업비트: HS512 JWT, raw secret, access_key + 새 UUID nonce. 쿼리가 없는 조회에는 query_hash를 넣지 않는다. [업비트 인증](https://docs.upbit.com/kr/reference/auth)
- 빗썸: HS256 JWT, access_key + 새 UUID nonce + 밀리초 timestamp. 업비트의 알고리즘을 그대로 재사용하지 않는다. [빗썸 인증](https://apidocs.bithumb.com/docs/인증-토큰-생성하기)
- CoinGecko: Crypto API Demo의 x-cg-demo-api-key 헤더와 /api/v3/ping. Pro는 명시적인 설정에만 따른다. 유료 전환을 자동으로 하지 않는다. [ping](https://docs.coingecko.com/demo/reference/ping-server)
- 서비스가 반환한 알려진 오류 코드로만 원인을 분류한다. [업비트 오류](https://docs.upbit.com/kr/reference/rest-api-guide), [OKX Swap 오류](https://web3.okx.com/onchainos/dev-docs/trade/dex-error-code), [OKX 공통 인증 오류](https://web3.okx.com/onchainos/dev-docs/xlayer/developer/data/error). 알 수 없는 403을 무조건 IP 문제로 표시하지 않는다.

### OKX 문서와 실제 응답의 차이

[지원 체인 문서](https://web3.okx.com/onchainos/dev-docs/trade/dex-get-aggregator-supported-chains)는 chainIndex를 String으로 설명하지만, M0의 실제 GET 조회는 JSON 정수를 반환했다.
문서의 숫자 문자열과 실제 양의 정수를 모두 허용한다. bool·실수·빈 값·음수는 거부한다.
원문 인증 헤더나 실제 계정 응답을 fixture에 저장하지 않았으며, 합성 회귀 테스트로 차이를 고정한다.

### 범위와 비밀값 처리

- .env 기존 내용은 유지한다. dotenv 보간과 ambient 환경 변수 fallback을 사용하지 않는다.
- CLI는 고정된 메시지만 출력한다. httpx/httpcore 로그는 RPC URL에 포함된 키를 노출할 수 있어 차단한다. 리다이렉트와 환경 프록시 자동 사용도 끈다.
- PyJWT의 Upbit 키 길이 권고 경고만 해당 서명 호출 안에서 제한적으로 숨긴다. 거래소가 발급한 키의 바이트를 변경하거나 HS512를 낮추지 않는다.
- 단위 테스트에서 private key를 포함한 합성 비밀값이 stdout/stderr/log/결과에 나타나지 않는지 검사한다.
- HTTP 서버는 127.0.0.1에만 바인딩한다. 원격 Host와 wildcard CORS를 허용하지 않는다.
- M0에는 트랜잭션 서명/전송 코드와 실행 라우트가 없다. DRY_RUN=false여도 읽기 전용이다.
- web3/eth-account는 실제로 사용하는 M5에서 추가한다. M0 RPC는 httpx로 eth_chainId만 호출한다.
- 후속 모듈용 패키지 디렉터리만 마련한다. 미구현 빌더·스왑 명령을 성공하는 것처럼 만들거나 빈 레지스트리 산출물을 생성하지 않는다.

### 검증의 의미와 남은 확인

- ping 통과는 CoinGecko 접속·응답 형식 확인이다. 레지스트리 API 접근 권한·쿼터·유료 필요성까지 보장하지 않는다. M1에서 실제 필요한 엔드포인트로 검증한다.
- RPC eth_chainId 통과는 URL 접속과 체인 일치만 보장한다. 스왑 가능 여부·잔고·네이티브 가스·USDT 주소/decimals는 후속 단계다.
- 업비트 최신 인증 문서의 권한 표는 입금 주소 생성도 입금조회 항목에 묶는다. 계획서의 입금하기 권한 전제와 차이가 있어 M6 구현 시 실제 생성 API의 최신 권한을 다시 대조한다. M0에서는 기존 주소 조회만 한다.
- M5 계획에는 approve가 DRY_RUN 설명보다 앞에 있고 확인 모달이 전송 뒤에 기재돼 있다. M5 구현 전에 모달 확인과 DRY_RUN 가드를 approve 및 swap의 모든 서명/전송보다 앞으로 정리해야 한다. M0에는 해당 실행 코드가 없다.

### M0 최종 연결 검증

사용자가 빗썸 API 설정을 수정한 뒤 통합 스모크에서 4개 서비스와 설정된 RPC 5개가 모두 통과했다.
자동 테스트 62개·린트·포맷 검사도 통과해 M0 완료 기준을 충족했다. 상세 근거는 [검증 기록](docs/m0-verification.md)에 남긴다.


## 2026-09-19 — M1

- 종목은 거래소별 CoinGecko coin_id로 식별한다. symbol만으로 전역 코인 목록에서 선택하지 않는다. 거래소별 ID 충돌과 누락은 리포트에 남기며 다른 거래소의 ID를 추측해 복사하지 않는다.
- [CoinGecko 거래소 티커](https://docs.coingecko.com/demo/reference/exchanges-id-tickers)는 order=base_target으로 빈 페이지까지 조회한다. [coins/list](https://docs.coingecko.com/demo/reference/coins-list)는 include_platform=true, [asset_platforms](https://docs.coingecko.com/demo/reference/asset-platforms-list)는 chain_identifier와 native_coin_id를 사용한다.
- EVM 범위는 CoinGecko 체인 ID와 OKX [Swap](https://web3.okx.com/onchainos/dev-docs/trade/dex-get-aggregator-supported-chains)·[Market](https://web3.okx.com/onchainos/dev-docs/market/market-price-chains) 지원 체인을 교차한다. RPC 미입력 체인을 조회 대상에서 제외하지 않는다. 테스트넷은 제외한다.
- 빗썸의 실제 net_type은 계획서의 Ethereum 예시와 달리 ETH이며, Arbitrum은 ARB_ETH 등으로 반환된다. [마켓](https://apidocs.bithumb.com/reference/거래-대상-목록-조회), [wallet](https://apidocs.bithumb.com/reference/입출금-현황)의 공식 OpenAPI와 실제 응답을 확인해 명시적 별칭을 작성했다. 업비트 [wallet](https://docs.upbit.com/kr/reference/get-service-status)도 같은 방식으로 확인했다.
- CRO처럼 Cronos POS와 EVM의 구분이 불명확한 네트워크는 임의로 연결하지 않는다. 미매핑 목록에서 검토한다. net_type이 알려졌더라도 paused 등 현재 입금 상태는 별도로 보존하며 unsupported 상태를 직접 입금 가능 네트워크로 확정하지 않는다.
- [OKX 네이티브 주소 FAQ](https://web3.okx.com/onchainos/dev-docs/trade/dex-aggregation-faq)에 따라 일반 EVM은 0xeeee…eeee, Arc는 0x3600…0000을 사용한다. Arc USDC decimals=6은 실제 메타데이터에서도 확인했다. Merlin의 native_coin_id=wrapped-bitcoin은 별도 검증 전까지 네이티브 자동 등록 대상에 넣지 않는다.
- [basic-info](https://web3.okx.com/onchainos/dev-docs/market/market-token-basic-info)는 정확히 서명한 JSON 배열을 전송한다. 실제 요청에서 20개 배치를 검증했다. 공식 최대 배치 개수는 문서에 명시돼 있지 않아 최대값으로 주장하지 않는다. 빈 이름을 반환하는 토큰은 미인식 CA로 보고하고 제외한다. 응답을 요청 순서로 연결하지 않고 (체인, 주소)로 매칭한다.
- AI는 실제 두 거래소 티커 모두 gensyn을 가리키며 Ethereum CA는 0x4d7078ddd6ccfed2f85db5b7d3ff16828d378d48이다. 두 거래소 입금 네트워크는 GENSYN으로 조회됐다. 현재 OKX 지원 교집합에서 Gensyn 체인을 확인하지 못해 Ethereum AI를 양 거래소 모두 bridge_candidate로 분류한다. 같은 티커 Artificial Inu로 바꾸거나 Ethereum 직접 입금을 허용하지 않는다.
- 오버라이드는 코인 ID·CA·네이티브 식별을 고치거나 경로를 제외할 수 있지만, tradable 강제 지정과 브릿지 스왑 허용은 제공하지 않는다. M1 실행 가능 플래그는 항상 false다.
- 필수 API 수집 실패 시 기존 산출물을 보존한다. 파일은 각각 임시 파일을 완성한 뒤 원자적으로 교체한다. JSON을 마지막에 교체하며, report의 입력 지문으로 일치 여부를 확인할 수 있다. 스냅샷은 명시적 재현 옵션으로만 사용한다.
- 동일 입력·설정에는 JSON/리포트가 결정적이다. 실시간 상장·입출금 상태가 변하면 결과도 바뀐다. 산출물에는 생성 시각을 넣지 않고 검증 문서에 실행 시각을 기록한다.


## 2026-09-19 — M2

- [OKX price](https://web3.okx.com/onchainos/dev-docs/market/market-price)는 가격·time만 제공한다. 유동성 필터/체인 비교는 [price-info](https://web3.okx.com/onchainos/dev-docs/market/market-token-price-info)의 liquidity를 별도 300초 주기로 수집한다. price-info 문서의 최대 100개와 price 100개 실제 요청을 확인했으며, price의 공식 최대값을 추정하지 않는다.
- /price 고빈도 조회는 메인 토큰과 coin_id별 선택된 브릿지 체인 하나로 제한한다. 초기 및 주기적인 최대 유동성 비교에는 전체 후보 조사가 필요하다. 일부 후보의 유동성이 없으면 최대 체인을 추정하지 않고 선택을 보류한다.
- [업비트 ticker](https://docs.upbit.com/kr/reference/websocket-ticker), [USDT orderbook](https://docs.upbit.com/kr/reference/websocket-orderbook), [빗썸 ticker](https://apidocs.bithumb.com/reference/현재가-ticker)를 사용한다. 빗썸은 wss://ws-api.bithumb.com/websocket/v1의 최신 프로토콜이다. 두 거래소의 공개 시세에는 인증키를 전송하지 않는다.
- REST는 [업비트 ticker](https://docs.upbit.com/kr/reference/list-tickers), [업비트 orderbook](https://docs.upbit.com/kr/reference/list-orderbooks), [빗썸 ticker](https://apidocs.bithumb.com/reference/현재가-조회)를 사용한다. WebSocket 누락·만료 종목만 보완한다. REST 20개·WS 100개는 보수적 운용값이며 공식 최대치 주장과 다르다.
- 계획대로 CEX 현재가는 수신 시각 TTL을 사용하며, REST 재조회로 신선도를 보완한다. REST timestamp는 마지막 체결 시각일 수 있으므로 최근 체결이 오래됐다는 이유로 정상 스냅샷을 차단하지 않는다. DEX 가격과 USDT 호가는 제공자 시각까지 확인한다. 유동성은 별도 수신 TTL을 사용한다.
- 빗썸 REST 숫자 timestamp가 UTC보다 9시간 앞서는 현상을 BTC/ETH에서 실측했다. 공식 응답의 trade_date·trade_time(UTC)과 trade_timestamp를 대조해 9시간 오프셋이 확인될 때만 정규화한다. 문서의 Unix timestamp 설명과 실측 차이를 어댑터에 한정해 처리하며, WS에는 적용하지 않는다. UTC 응답도 허용하고 불일치는 거부한다. REST 요청 중 새 WS 값이 왔으면 REST가 덮어쓰지 않는다.
- 직접 입금 불가능한 거래소의 갭을 메인 행의 최고 갭으로 삼지 않는다. 브릿지의 후보 그룹은 토큰 상태 기준이므로 다른 체인에 메인 토큰이 있는 코인도 포함한다.
- 유동성/인식 여부 미확정도 경고/숨김 대상이며, 의심 목록 분리가 숨김보다 먼저다. 체인 선택 보류 행은 사유를 표시한다. 모든 실행 플래그는 false다.
- Decimal 계산과 업비트 USDT 매도 1호가 공통 기준을 적용한다. 1 USD≈1 USDT 가정과 거래소 수수료 제외를 API 응답에 명시한다. 실제 fiat 환율과 거래소 수수료 설정은 추가하지 않는다.
- 공급자별 워커, 공통 OKX 요청 게이트, REST 보완, 1~30초 WS 재연결을 구현했다. 단순 가격 조회의 5초 주기는 목표이며 전체 순회가 길어지면 중첩 없이 다음 순회를 시작한다. 실제 소요·요청 실패·stale를 API에 노출한다.
- websockets 최소 버전을 15로 올려 proxy=None을 명시적으로 지원한다. HTTP는 trust_env=False, WS도 프록시 자동 사용을 끈다. 비밀값·원문 예외는 출력하지 않는다.
- 유료 API 결정은 계속 보류한다. M2 단기 실측과 운용 제약은 [검증 기록](docs/m2-verification.md), 실행·설정은 [사용 안내](docs/m2-pricing.md)에 기록한다.


## 2026-09-20 — M3

- 프론트 빌드 없이 HTML/CSS/바닐라 JavaScript로 목록을 구현했다. 서버의 /api/gaps만 폴링하며 기본 주기는 ui_poll_seconds=2다. 요청 중첩을 막고 브라우저 만료 타이머를 따로 두었다.
- 종목명은 개별 레지스트리 토큰의 OKX 이름과 거래소 한글명을 사용한다. 체인명·토큰명은 서버에서 등록된 정보로만 채운다. 티커를 키로 외부 검색하거나 다른 CA를 보충하지 않는다.
- 유동성 조사 보류 후보도 거래소가 지원하는 입금망은 이미 알려져 있으므로 API와 UI에 남긴다. 선택 체인·갭은 비어 있는 상태를 유지한다.
- 입출금 배지는 거래소 지원망별 레지스트리 스냅샷이다. 입금 중단을 경로 미지원과 구분하고, 중단된 경로도 메인에 남긴다. 실시간 상태 재조회는 M4 범위다.
- 갭은 서버가 계산한 값을 표시하며 프론트는 유효값 정렬·검색·만료 표시만 한다. 0과 null을 구분하고, 외부 문자열은 textContent로 넣는다.
- 브릿지·의심 접기, 선택한 정렬과 검색어를 자동 갱신 중 유지한다. 모바일은 전체 페이지 대신 표 내부를 가로 스크롤한다.
- 검증은 오프라인 fixture와 실제 시세를 구분했다. 브라우저 검증기는 독립 Chrome 프로필과 임시 포트의 서버를 사용하고 종료 시 정리한다. 근거는 [M3 검증 기록](docs/m3-verification.md)에 남긴다.


## 2026-09-20 — M4

- 사용자가 M4 상세 패널 구현을 직접 승인한 범위에서 읽기 전용 견적·호가 소진·입금 상태·가스 추정을 구현했다. M5의 실제 서명/전송과 M6의 입금 주소 생성은 추가하지 않았다.
- 정확한 레지스트리 식별과 거래소 네트워크 매핑이 선행한다. 거래소 선택이 달라도 환산 기준은 업비트 USDT 매도 1호가이며 거래소 매매 수수료는 제외한다.
- OKX quote의 tradeFee는 USD 네트워크 비용이다. estimateGasFee의 단위를 추측해 USD 비용으로 쓰지 않는다. 실제 응답의 dexRouterList는 dexProtocol 객체를 가진 홉 배열이므로 이에 맞춰 라우팅 요약을 구성했다.
- 공식 문서로 확인한 매수 자산 12개 체인은 config/quote_assets.toml에 근거 URL과 함께 둔다. USDT0는 이름을 구분한다. 목록 전체 체인을 특정 RPC 설정에 맞춰 줄이지 않는다.
- Ethereum/Polygon/Avalanche 외 체인은 추가 비용 검증 전까지 부분 가스만 제공한다. 고정 전송 gas 예산은 추정임을 명시하고 비용 미확인을 0으로 계산하지 않는다. allowance가 부족하나 0이 아니면 승인 reset을 포함해 2회 예산을 반영한다.
- 업비트 deposits/chance/coin의 currency/net_type 쿼리 SHA512 해시를 JWT에 추가한다. 컨펌/최소 입금량과 입금 가능 여부를 읽고 wallet 상태와 함께 확인한다. 빗썸 미제공 항목은 null/확인 필요로 표시한다.
- 세금 토큰은 base unit에서 매수세를 보수적으로 반영하되 전송세가 불명확하므로 실효갭을 보류한다. 브릿지도 비용 없는 실효갭을 만들지 않는다.
- 상세 요청은 45초 제한·동시 1개·공급자 공통 게이트를 사용한다. UI는 금액 변경 시 이전 결과를 지우고 응답 세대로 늦은 결과를 차단한다. 만료 시 서버 처리시간을 이중 차감하지 않고 전송 지연만 추가 차감한다.
- 가스 추정·지원 범위·향후 검증 항목은 [M4 안내](docs/m4-detail.md), 테스트 결과는 [M4 검증 기록](docs/m4-verification.md)에 정리한다. 유료 API 결정은 계속 보류한다.


## 2026-09-20 — M5~M7

- 전체 마일스톤 진행 요청에 따라 스왑 실행 계층, 전체 입금 주소 사전 준비, 운영 정보를 구현했다. 실제 .env의 DRY_RUN=true를 유지했다. 소액 실거래 검증은 계획서대로 사용자 몫으로 남긴다.
- 기존 M5 단계의 순서를 명확히 했다. 사용자 확인과 DRY_RUN 가드를 reset·approve·swap의 모든 서명/전송보다 앞에 둔다. 확인 요청은 서버가 저장한 intent ID만 받으며 클라이언트가 calldata·nonce·금액을 교체할 수 없다.
- [OKX swap](https://web3.okx.com/onchainos/dev-docs/trade/dex-swap)의 slippagePercent는 퍼센트 값이다. 0.5는 0.5%로 전달하고 exactIn, userWalletAddress, swapReceiverAddress를 명시한다. autoSlippage는 끄며 tx의 최소 수령량도 확인한다. 응답 from/router/chain/value/gas를 검증하고 가스는 문서의 추가 여유 안내에 따라 50% 예산을 둔다.
- [공식 컨트랙트 목록](https://web3.okx.com/onchainos/dev-docs/trade/dex-smart-contract)에서 28개 체인의 router/spender를 검토해 config/contracts.json에 근거와 날짜를 저장했다. API가 반환한 주소가 목록과 다르면 차단한다. 이전 주소로 임의 대체하지 않고 공식 변경 내용을 검토한 뒤 목록을 갱신한다.
- approve calldata는 selector, spender, 유한 uint256 금액까지 정확히 대조한다. 부족한 비영 allowance는 0 reset을 먼저 준비한다. capped의 기본 5,000 USDT는 무제한 승인이 아니다.
- 스왑 확인의 비용 계산은 OKX swap 네트워크 비용과 RPC 스왑 예산 중 큰 값에 승인·전송 비용을 더한다. 같은 스왑 비용을 두 번 더하지 않는다. 이미 지출한 승인 예산도 이후 전체 실효갭 검증에서 유지한다. 거래소 매매 수수료는 계속 제외한다.
- [eth-account](https://eth-account.readthedocs.io/en/stable/eth_account.html)로 로컬 서명하며 자체 RPC만 사용한다. 서명 직전 체인·nonce·가스·잔고를 확인한다. 전송 전에 hash/nonce를 원자적으로 기록하고, 결과가 불확실하면 같은 지갑·체인의 새 실행을 막는다. 재시작과 상태 재조회는 기존 해시만 확인하며 자동 재전송하지 않는다.
- [OKX history](https://web3.okx.com/onchainos/dev-docs/trade/dex-swap-history)의 data는 객체 응답을 지원하고 단일 원소 배열도 허용한다. toTokenDetails 객체/배열, 최소 단위 정수의 .000 표기를 처리한다. chain/hash/from/to/token을 대조한 실제 수량만 표시한다. API 집계 지연을 온체인 실패로 바꾸지 않는다.
- [업비트 주소 생성](https://docs.upbit.com/kr/reference/create-deposit-address)은 currency/net_type JSON 요청과 비동기 완료를 처리한다. 생성 엔드포인트 문서의 입금하기 권한 안내를 참고하고 실제 요청으로 현재 키의 허용 여부를 확인했다. [빗썸 주소 생성](https://apidocs.bithumb.com/reference/입금-주소-생성-요청)도 JSON 파라미터의 urlencode 바이트를 SHA512 해시해 JWT에 포함한다.
- 전체 tradable 거래소·코인·net_type 조합 472개 주소가 준비됐다. 재실행에서 생성 요청 0회 및 캐시 동일성을 검증했다. 전체 거래소 상장 코인 중 미검증·직접 입금 불가 경로까지 생성한다는 뜻은 아니다. 계정 키 식별자 변경 시 이전 주소를 무효화한다.
- [ECB 기준환율](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)의 일일 XML에서 KRW/EUR ÷ USD/EUR로 USD/KRW를 구한다. 일일 참고값이므로 날짜/지연 표시를 하며 미래 날짜·7일 초과 데이터는 거부한다. 갭 계산과 실행 가드에는 연결하지 않는다.
- 구조화 로그는 비밀값을 사후 정규식으로 지우는 대신 허용 필드·상태 코드만 기록한다. private cache/journal는 파일 권한 600과 Git 제외, 원자적 교체와 프로세스 잠금으로 관리한다.
- 현재 실행 체인은 자산·비용·컨트랙트가 모두 준비된 Ethereum/Polygon/Avalanche C다. 나머지 조회 체인에서 검증되지 않은 비용을 0으로 가정하지 않는다. 선택 항목 Solana/MEV/Broadcast/다른 DEX 및 유료 API 선택은 보류한다.
- 완료 근거는 [M5~M7 검증 기록](docs/m5-m7-verification.md), 설정·복구 절차는 [사용 안내](docs/m5-m7-usage.md)에 정리했다.


## 2026-09-20 — 사용자 대시보드 피드백

- 5만 달러 미만 체인 경로를 기본 숨김으로 변경했다. 코인이나 레지스트리를 삭제하지 않으며 저유동성 포함 토글을 제공한다. 의심 분류보다 유동성 필터를 먼저 적용한다.
- 코인별 대표 체인은 갭이 아닌 유동성으로 정한다. 일부 체인 미확인 시 확인된 최대라고 표시하고 대표 가격 수집을 유지한다. 브릿지의 가격 갭은 비용·시간 미반영 참고값으로 명시한다.
- 입금 중단 상태에서 수학적으로 계산 가능한 손익은 reference_result로 분리했다. 실행용 result는 null로 유지하므로 기존 스왑 가드는 계속 차단한다. 비용 불명·브릿지에는 참고 실효갭을 만들지 않는다.
- 만료와 계산 불가의 원인을 별도 표시하고 유효 시간 카운트다운을 추가했다. 상세 재견적의 자동 반복 호출은 추가하지 않았다.
- 구현·검증 근거: [피드백 반영 기록](docs/dashboard-feedback.md).

- 실제 SNX 검증에서 OKX 네이티브 가격의 source timestamp가 로컬보다 약 2초 빨라 상세만 즉시 만료되는 불일치를 확인했다. 기존 목록의 5초 허용 기준을 상세·실행에도 적용하고 미래 시각으로 TTL이 연장되지 않도록 clamp한다. 3초/6초 합성 경계 검증을 추가했다.

- 사용자 요청으로 USDT만 별도 300초 신선도와 마지막 정상값 재사용을 적용했다. 목록·상세 참고 계산은 갱신 실패로 중단하지 않으며 재사용과 수신 시각을 표시한다. 최초 정상 관측이 없으면 값은 만들지 않는다. 실제 스왑의 직전 최신 호가 검증은 유지한다.

- 사용자 요청에 따라 화면의 유동성 기준을 기본 1.0m, 1.0~10.0m 범위의 소수점 한 자리 입력으로 제공한다. 브라우저에 저장하며 API 원본의 고정 low_liquidity 플래그 대신 선택한 기준으로 화면 필터와 배지를 함께 계산한다. CA 복사는 체인별 전체 주소를 사용하고 네이티브 내부 조회 주소는 제외한다. 유동성만 k/m/b로 축약한다.


## 2026-09-20 유동성 관측값 재사용

사용자 요청에 따라 유동성 갱신 실패·신선도 초과 시 마지막 정상 관측값을 후보 비교와 필터에 재사용한다. 최초 미관측은 구분하고, 정상 수신/API 관측 시각 및 실패 원인을 표시한다. 값 0은 유효하다. 참고값의 수집 시각은 재시도 때 갱신하지 않는다. 현 단계 캐시는 서버 메모리에 보관한다. 가격·견적·실행의 신선도 기준은 유지한다.


## 2026-09-20 유동성 표시 단위 조정

사용자 요청으로 최소 유동성 범위를 0.1~10.0m로 확장한다(0.1m = 100,000 USD). 관측·수신·최근 조회 시각은 분/시간/일 단위 경과 시간으로 표시한다. 1분 미만은 ‘방금 전’으로 표시한다.


## 2026-09-20 대시보드 제외 및 실시간 입출금

사용자가 권고안을 승인했다. 미관측 유동성/입금 중단 경로는 기본 숨김(각각 포함 옵션 제공), 코인별 영구 블랙리스트와 코인·거래소·입금망별 수동 임시 제외를 지원한다. 제외 설정은 재시작 후에도 유지하고 자동 갱신으로 풀지 않는다. 입출금 상태는 30초 자동 조회+수동 새로고침으로 공유 갱신한다. 조회 실패/90초 만료 시 이전 정상 상태를 현재 정상으로 표시하지 않는다. 자세한 사용법은 docs/dashboard-controls.md.


## 2026-09-23 레지스트리 생성 시각과 stale 판정

레지스트리 생성 시각은 registry.json의 generated_at 필드 우선, 없으면 파일 mtime 폴백. 이유: registry.json이 git 추적이라 checkout 시 mtime 신뢰 불가. stale 기준은 settings.registry.max_age_hours(기본 72).

## 2026-09-28 포트폴리오 공개 준비: UI 리빌딩과 정적 데모

- UI는 portfolio DESIGN.md(다크 핀테크)를 따른다. 요소 id와 JS 동작은 그대로 두고 `dashboard.css`를 토큰 기반으로 새로 썼다. 기존 브라우저 검사가 id와 텍스트로 동작을 검증하므로 회귀 범위를 줄이기 위해서다.
- 갭 색은 한국식이다. 양(+) 갭은 빨강(`up`), 음(−) 갭은 파랑(`down`), 0은 회색(`flat`)이다. 색만으로 방향을 전달하지 않도록 CSS로 ▲▼–를 붙인다. 텍스트의 +/− 부호는 기존대로 유지해 텍스트 기반 검사가 바뀌지 않는다.
- 민트 강조색은 선택과 주 액션에만 쓴다. 입출금 상태 배지는 가능=무채색, 불가·경고=앰버로 바꿨다. 입출금 가능 여부를 초록으로 표시하면 손익 신호로 오인할 수 있기 때문이다.
- 정적 데모(`dist/`)는 별도 프런트엔드를 만들지 않는다. `scripts/build_static.py`가 실제 FastAPI 앱을 합성 provider로 돌려 응답을 `data/demo.json`에 캡처하고, `static-api.js`가 `/api/*` fetch를 그 파일로 응답한다. 이유: UI 코드를 한 벌로 유지하고, 데모 수치가 실제 계산 경로(갭·실효갭·DRY_RUN 가드)를 거치게 하기 위해서다.
- 데모 코인·가격·주소는 가상이다. 주소는 누가 봐도 합성인 `0xde70…`이다. 실제 티커에 가짜 CA를 붙이면 복사해 거래하는 사고로 이어질 수 있어 피했다.
- 캡처 시 DRY_RUN은 기본값 true이고, 서명 호출 0회를 빌드가 assert한다. 캡처 시각 기준 epoch ms 값은 shim이 응답할 때 현재 시각으로 옮긴다. 이렇게 해야 신선도·만료 로직이 실제와 같게 동작한다.
- CSP는 `default-src 'none'`, `script-src/style-src/connect-src 'self'`, `require-trusted-types-for 'script'`다. `_headers`와 meta 태그를 같은 목록에서 만든다. meta에는 무시되는 `frame-ancestors`를 뺀다. 외부 폰트·CDN을 쓰지 않으므로 서체는 로컬 Pretendard가 없으면 시스템 산세리프로 대체된다.
- `dist/`는 배포 편의를 위해 추적한다(빌드에 Python·uv가 필요하므로 정적 호스팅에서 바로 서빙할 수 있게). `tests/test_static_build.py`가 외부 URL 0건과 CSP 일치를 검증한다. `node scripts/check_static_ui.cjs`는 30초 동안 외부 요청 0건과 CSP 위반 0건을 검증한다.
