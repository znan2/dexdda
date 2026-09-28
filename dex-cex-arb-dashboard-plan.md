# DEX-CEX 차익거래 대시보드 구현 계획서

대상 독자: 이 저장소를 구현할 코딩 에이전트(Codex).
이 문서는 "무엇을, 왜, 어떤 순서로" 만들지를 정의한다. 마일스톤 단위로 구현하고, 각 마일스톤의 완료 기준을 통과한 뒤 멈춰서 검토를 받는다.

---

## 0. 작업 규칙 (먼저 읽을 것)

1. **마일스톤 순서대로 하나씩** 구현한다. 한 마일스톤이 끝나면 완료 기준 검증 결과를 요약하고 멈춘다. 다음 마일스톤은 지시가 있을 때 시작한다.
2. **API 필드를 추측하지 않는다.** 이 문서에 "확인됨"으로 표시된 항목만 그대로 쓰고, "확인 필요"로 표시된 항목은 8장의 공식 문서를 열어 필드명·파라미터를 확인한 뒤 구현한다. 문서와 이 계획서가 다르면 문서를 따르고 `DECISIONS.md`에 차이를 기록한다.
3. **실제 자금이 움직이는 코드는 기본 비활성**이다. 스왑 전송은 `DRY_RUN=true`가 기본값이며, 명시적으로 끄기 전에는 서명·전송을 하지 않는다.
4. **비밀값은 `.env`에서만 읽는다.** 프라이빗 키와 API 시크릿은 로그, 예외 메시지, API 응답, 프론트엔드 어디에도 노출하지 않는다. `.env`는 `.gitignore`에 포함한다.
5. 백엔드는 **`127.0.0.1`에만 바인딩**한다. 스왑 실행 엔드포인트가 있으므로 외부 노출과 와일드카드 CORS를 금지한다.
6. 외부 API 호출부는 모두 어댑터 모듈로 분리하고, 저장된 샘플 응답(fixture)으로 단위 테스트를 작성한다.
7. 단순함을 우선한다. 이 도구는 개인용 로컬 도구다. 인증, 멀티유저, 배포 구성은 만들지 않는다.

---

## 1. 목표와 범위

### 1.1 목표
OKX DEX(Onchain OS API)의 온체인 가격과 업비트·빗썸의 원화 가격을 비교해 **DEX 매수 → CEX 매도** 차익 기회를 보여주고, 선택한 코인을 클릭 한 번으로 DEX에서 스왑까지 실행하는 로컬 대시보드.

### 1.2 범위
- DEX: OKX DEX만. (추후 다른 DEX 통합 가능성이 있으므로 DEX 호출부는 인터페이스로 분리)
- CEX: 업비트, 빗썸. 주 매도 거래소는 업비트이며, 빗썸에서도 기회가 포착되면 매도 대상으로 삼는다.
- 방향: DEX 매수 → CEX 매도 단방향.
- 자동화 범위: **스왑까지.** 거래소로의 전송은 사용자가 수동으로 한다. 스왑 완료 화면에 해당 네트워크의 거래소 입금 주소를 복사용으로 표시한다.
- 체인: 1차는 구현 가능한 모든 EVM 체인. 특정 체인 목록으로 사전 제한하지 않고, OKX Market·Swap API의 지원 범위와 거래소 입금 네트워크를 대조해 대상을 구성한다. 브릿지 후보는 별도 조회 대상으로 둔다. Solana 등 비EVM은 M7 이후.
- 체인별 조회 가능 여부와 스왑 실행 준비 여부를 구분한다. 실행에는 해당 체인의 검증된 매수용 USDT 컨트랙트·decimals, RPC, 지갑 USDT·가스 잔고가 필요하며, 미준비 체인은 조회 가능한 데이터를 표시하되 실행을 비활성화하고 사유를 표시한다.

### 1.3 범위 밖
- CEX 주문·출금 자동화, 역방향(CEX 매수 → DEX 매도), 헤지, 자동 매매(사람 클릭 없이 실행).

---

## 2. 핵심 설계 결정과 근거

### D1. 코인 식별은 티커가 아니라 (체인, 컨트랙트 주소)로 한다
- 문제: 티커가 같아도 다른 코인일 수 있다. 예: 티커 `AI`는 업비트에서는 Gensyn이지만, DEX에는 같은 티커의 Artificial Inu도 있다. 티커로 매칭하면 엉뚱한 토큰의 갭이 계산된다.
- 풀네임 매칭은 보조 수단으로만 쓴다. 거래소마다 표기가 다르고, 스캠 토큰은 이름까지 복제하기 때문이다.
- 결정: 모든 가격 조회·견적·스왑은 `chainIndex + tokenContractAddress`로만 수행한다. 풀네임은 화면 표시와 육안 검증용이다.

### D2. CEX 티커 → CA 매핑은 CoinGecko를 중간 소스로 쓴다
- 업비트·빗썸 API는 네트워크 식별자(`net_type`)까지만 제공하고 컨트랙트 주소는 제공하지 않는다.
- CoinGecko의 거래소 티커 응답에는 티커별 `coin_id`가 있고(확인됨), 코인 목록 API는 `coin_id`별 체인·CA(`platforms`)를 준다.
- 경로: `거래소 티커 → coin_id → platforms(체인별 CA) → 거래소가 입금을 지원하는 체인의 CA만 채택`.

### D3. 매핑은 실시간 검색이 아니라 사전 구축 레지스트리로 운영한다
- 런타임에 티커로 DEX를 검색하면 D1의 문제가 그대로 재발하고 호출량도 늘어난다.
- 레지스트리는 빌드 스크립트로 생성해 파일로 저장하고, 신규 상장 시 재빌드한다. CoinGecko 매핑 오류·누락은 **수동 오버라이드 파일**로 덮어쓴다.

### D4. 같은 코인의 여러 체인 버전 중 "입금 가능한 체인"만 거래 대상으로 삼는다
- CoinGecko `platforms`에는 브릿지된 버전까지 여러 체인의 CA가 함께 나온다. 거래소는 보통 그중 한두 개 네트워크로만 입금을 받는다.
- 입금 불가 체인의 토큰을 사면 거래소로 보낼 수 없다. 따라서 거래소 `net_type`에 대응하는 체인의 CA만 `tradable`로 분류한다.
- 단, **입금 불가 체인에서 갭이 보이는 코인은 버리지 않고 `bridge_candidate`로 분류해 별도 섹션에 간략히 표시**한다. 사용자가 브릿지 경로를 직접 조사해 "브릿지 후 전송"이 가능한지 검토하기 위함이다. (상세는 4.3)

### D5. 갭은 업비트 USDT/KRW로 환산해 계산한다 (김프 자동 반영)
- 실제 환율로 환산하면 김프만큼의 갭이 항상 떠 보인다. 김프가 1%일 때 1% 갭은 이익이 없다.
- **업비트 USDT/KRW 매도 1호가**를 두 거래소의 공통 비교 기준으로 사용한다(사용자 결정).
- 빗썸 갭도 동일하게 업비트 USDT/KRW 값으로 환산한다. 이는 빗썸 매도대금을 실제로 업비트로 옮겨 USDT를 재매수한다는 뜻이 아니다.
- 실효갭과 예상 손익은 이 공통 기준에 따른 추정치다. 업비트·빗썸 거래 수수료는 제외하며, 실제 자금 회수 경로의 비용이나 전송 중 가격 변동까지 반영한 확정 손익으로 표시하지 않는다.
- 실제 환율(USD/KRW)은 손익 계산에 쓰지 않는다. 주말에는 외환시장이 닫혀 값이 멈추기 때문에 계산에 넣으면 왜곡된다. 화면 상단에 "현재 김프 n%"를 참고용으로만 표시한다(M7, 선택).

### D6. 표면갭과 실효갭을 분리한다
- 표면갭: 전 종목을 싸게 훑기 위한 값. 현재가 기반이며 슬리피지를 반영하지 않는다.
- 실효갭: 사용자가 클릭한 코인에 대해서만, 실제 투입 금액 기준의 DEX 견적과 CEX 호가창을 반영해 계산한다. 호출 비용이 크므로 온디맨드로만 계산한다.

### D7. 안전장치
- **갭 상한 필터**: 표면갭이 임계값(기본 ±30%, 설정 가능)을 넘으면 매핑 오류로 간주해 순위에서 제외하고 "의심" 목록으로 보낸다. 잘못 매칭된 토큰일수록 갭이 커서 순위 1위로 올라오므로 가장 효과적인 필터다.
- **OKX 메타데이터**: `tagList.communityRecognized`(Top 10 CEX 상장 또는 커뮤니티 검증 여부, 확인됨), 유동성 하한(기본 $50,000, 설정 가능).
- **견적 단계 검사**: `isHoneyPot`, `taxRate`가 있으면 경고하고 스왑 버튼을 비활성화한다.

### D8. 서명은 로컬, 브로드캐스트는 자체 RPC
- OKX는 스왑용 트랜잭션 데이터만 만들어 준다. 서명은 로컬 프라이빗 키로 한다.
- OKX의 Simulate·Broadcast(MEV 보호) API는 화이트리스트 승인이 필요하므로 1차에서는 쓰지 않는다. 브로드캐스트 계층을 인터페이스로 분리해 나중에 교체할 수 있게만 한다.

### D9. 매수 통화는 USDT
- 매수 통화와 공통 비교 기준을 USDT로 통일한다. 단, USD 표시 가격과 비용을 USDT로 취급하는 경우에는 1 USDT = 1 USD라는 근사 가정을 명시한다.

### D10. API 유료 사용 여부는 추후 결정한다
- 구현 과정 또는 구현 후 검토 과정에서 실제 대상 종목 수, 배치 크기, 갱신 주기, 호출량과 계정별 한도를 확인한 뒤 유료 사용 여부를 결정한다(사용자 결정).
- 현재 단계에서는 유료 요금제나 예산을 확정하지 않는다. 유료 사용을 결정하기 전에는 자동 결제·구독 전환을 수행하지 않는다.
- 무료 한도 또는 계정 권한에 막히면 제한 사유와 예상 필요 호출량을 보고하고, 영향을 받는 데이터는 갱신 실패 또는 stale 상태로 표시한다.

---

## 3. 기술 스택과 저장소 구조

- Python 3.12+, 패키지 관리는 `uv`. macOS(Apple Silicon) 로컬 실행.
- 백엔드: FastAPI + httpx(비동기) + websockets. 서명: `eth-account`/`web3.py`.
- 프론트: 빌드 과정 없는 단일 페이지(정적 HTML + 바닐라 JS 또는 HTMX). 1~2초 주기 폴링 또는 SSE.
- 저장소: 파일 기반. DB는 쓰지 않는다.

```
.
├── pyproject.toml
├── .env.example
├── DECISIONS.md                # 문서와 달랐던 점, 구현 중 판단 기록
├── config/
│   ├── settings.toml           # 임계값, 폴링 주기, 기본 투입금액 등
│   ├── chain_map.toml          # 거래소 net_type ↔ OKX chainIndex ↔ CoinGecko platform id
│   └── overrides.toml          # 수동 오버라이드(매핑 고정, 제외, 네이티브 등록)
├── data/
│   ├── registry.json           # 빌드 산출물
│   ├── registry_report.md      # 빌드 리포트(분류별 개수, 미매핑 목록)
│   └── deposit_addresses.json  # 입금 주소 캐시 (gitignore)
├── src/app/
│   ├── adapters/               # okx.py, upbit.py, bithumb.py, coingecko.py, rpc.py
│   ├── registry/               # builder.py, models.py, loader.py
│   ├── pricing/                # collector.py, gap.py, filters.py
│   ├── detail/                 # effective_gap.py
│   ├── swap/                   # executor.py, guards.py
│   ├── deposit/                # addresses.py
│   ├── api/                    # FastAPI 라우터
│   └── web/                    # 정적 프론트
├── scripts/
│   ├── smoke_test.py
│   ├── build_registry.py
│   └── sync_deposit_addresses.py
└── tests/
```

`.env` 항목(생성된 `.env.example`과 동일한 이름을 사용):
`OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE`, `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`, `BITHUMB_ACCESS_KEY`, `BITHUMB_SECRET_KEY`, `COINGECKO_API_KEY`, `WALLET_ADDRESS`, `WALLET_PRIVATE_KEY`, `RPC_URL_<chainIndex>`, `DRY_RUN`.

`WALLET_ADDRESS`는 개인키 없이 지갑 잔고·견적을 조회할 때 사용하는 공개 주소다. 실거래 시에는 개인키에서 유도한 주소와 일치하는지 검증한다. RPC 항목은 대상 체인에 맞게 추가하며, 예시 파일에 있는 체인만 지원하는 것으로 제한하지 않는다. 초기 `.env`의 비밀값은 비워 두고 `DRY_RUN=true`를 유지한다.

권한 전제: 거래소 키는 [입금조회]와 [입금하기] 권한만 가진다. 주문·출금 권한은 쓰지 않으며, 코드에서도 해당 API를 호출하지 않는다. 지갑 키는 소액만 든 전용 핫월렛의 키다.

---

## 4. 데이터 모델과 핵심 계산

### 4.1 레지스트리 항목

```json
{
  "coin_id": "gensyn",
  "symbol": "AI",
  "names": { "okx": "Gensyn", "upbit_ko": "...", "upbit_en": "...", "coingecko": "Gensyn" },
  "exchanges": {
    "upbit":   { "markets": ["KRW-AI"], "net_types": ["ETH"] },
    "bithumb": { "markets": ["KRW-AI"], "net_types": ["Ethereum"] }
  },
  "tokens": [
    { "chain_index": "1", "address": "0x4d70…8d48", "decimals": 18,
      "status": { "upbit": "tradable", "bithumb": "tradable" } },
    { "chain_index": "8453", "address": "0x…", "decimals": 18,
      "status": { "upbit": "bridge_candidate", "bithumb": "bridge_candidate" } }
  ],
  "source": "coingecko"
}
```

`status` 값(거래소별로 판정):
| 값 | 의미 | 화면 |
|---|---|---|
| `tradable` | OKX 지원 체인이면서 거래소가 그 네트워크 입금을 지원 | 메인 목록 |
| `bridge_candidate` | OKX 지원 체인에 CA가 있으나 거래소는 그 네트워크 입금을 지원하지 않음 | 브릿지 후보 섹션 |
| `excluded` | DEX 경로 없음(BTC, XRP 등), 또는 오버라이드로 제외 | 표시 안 함 |

네이티브 코인: CA가 없다. OKX DEX 지원 체인의 네이티브(ETH, BNB 등)는 `overrides.toml`에 네이티브 전용 주소로 등록해 `tradable`로 넣는다. 네이티브 주소 표기는 OKX 문서에서 체인별로 확인한다(확인 필요). 그 외 네이티브는 `excluded`.

### 4.2 갭 공식

```
usdt_krw      = 업비트 KRW-USDT 매도 1호가
표면갭        = CEX 현재가(KRW) ÷ (DEX 가격(USD) × usdt_krw) − 1
```

실효갭(업비트 USDT 공통 기준 추정치, 거래소 거래 수수료 제외, 투입 금액 U USDT, 기본 1,000, UI에서 변경):
```
1) OKX /quote(USDT → 토큰, amount=U)       → 예상 수령 수량 Q (decimals 환산, taxRate 있으면 차감)
2) CEX 매수 호가창을 Q만큼 위에서부터 소진  → 원화 수령액 K (호가 부족 시 "유동성 부족" 표시)
3) K ÷ usdt_krw                            → U'
4) 가스비 = 스왑 + (필요 시 approve) + 전송 1회 추정 (USDT 기준으로 환산)
실효갭 = (U' − 가스비 − U) ÷ U
```
업비트·빗썸의 코인 매도 수수료와 USDT 매수 수수료는 모두 계산에서 제외한다(사용자 결정). 거래소 수수료율 입력값이나 차감 로직을 만들지 않는다. DEX 견적에 반영된 비용과 위 가스비 추정은 유지한다. 위 전송 1회 외에 실제 자금 회수 경로에서 추가로 발생하는 비용은 계산에 포함하지 않는다. 전송 중 가격 변동 위험은 계산에 넣지 않고, 컨펌 수와 예상 소요를 표시하는 것으로 대신한다. UI에는 "업비트 USDT 기준 · 거래소 수수료 제외"라는 계산 기준을 명시한다.

### 4.3 브릿지 후보 섹션 (간략 표시)
- 대상: 어떤 거래소 기준으로도 `tradable` 토큰이 아닌 `bridge_candidate` 토큰. 같은 코인에 후보 체인이 여러 개면 **유동성이 가장 큰 체인 1개만** 조회한다(호출량 제한, 간략 표시 목적).
- 표시 항목: 티커, 풀네임, DEX 체인, 표면갭, 거래소가 지원하는 입금 네트워크. 메인 목록 아래 접이식 표로 둔다.
- 안전장치(D7)는 동일하게 적용한다.
- 1차에서는 후보 조회만 지원하고 스왑은 허용하지 않는다(사용자 결정). UI와 서버 실행 가드 모두에서 차단하며, 오버라이드로 실행을 허용하는 옵션은 제공하지 않는다. "이 체인은 거래소 직접 입금 불가 — 브릿지 경로 별도 확인 필요"를 표시한다.
- 주의: 다른 체인의 네이티브 코인을 페깅한 토큰(예: BSC의 페깅 XRP)도 여기에 잡힐 수 있다. 걸러내지 말고 그대로 표시하되, 판단은 사용자가 한다.

---

## 5. 외부 API 정리

### 5.1 OKX Onchain OS (base: `https://web3.okx.com`)
인증 헤더: `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-PASSPHRASE`, `OK-ACCESS-TIMESTAMP`. 서명은 `timestamp + METHOD + requestPath + (queryString 또는 body)`를 시크릿 키로 HMAC-SHA256 한 값이다(인코딩 방식은 문서에서 확인).

| 용도 | 엔드포인트 | 비고 |
|---|---|---|
| 가격(배치) | `POST /api/v6/dex/market/price` | body는 `[{chainIndex, tokenContractAddress}]` 배열. EVM 주소는 소문자. 배치 최대 개수는 확인 필요 |
| 토큰 검색 | `GET /api/v6/dex/market/token/search` | `chains` 필수. 응답에 `tokenName`, `tokenSymbol`, `tokenContractAddress`, `decimal`, `liquidity`, `marketCap`, `price`, `tagList.communityRecognized` (확인됨) |
| 토큰 기본 정보(배치) | `POST /api/v6/dex/market/token/basic-info` | `tokenName`, `tokenSymbol`, `decimal`, `tagList` (확인됨) |
| 지원 체인 | `GET /api/v6/dex/aggregator/supported/chain` | |
| 견적 | `GET /api/v6/dex/aggregator/quote` | 응답 `toTokenAmount`, `priceImpactPercent`, `estimateGasFee`, `tradeFee`, `dexRouterList`, 토큰 정보의 `isHoneyPot`·`taxRate`. 필드 전체는 레퍼런스에서 확인 필요 |
| approve 데이터 | `GET /api/v6/dex/aggregator/approve-transaction` | `chainIndex`, `tokenContractAddress`, `approveAmount` |
| 스왑 데이터 | `GET /api/v6/dex/aggregator/swap` | 견적 파라미터 + `userWalletAddress`. 응답 `tx.from/to/data/value` |
| 체결 조회 | `GET /api/v6/dex/aggregator/history` | `txHash` 기준 |

주의: approve 대상(spender) 주소는 **체인마다 다르다.** 공식 가이드 예제에는 Base 예제에 이더리움 주소 주석이 붙어 있는 등 불일치가 있으므로 하드코딩하지 말고, approve-transaction 응답 또는 문서의 체인별 컨트랙트 표에서 가져온다(확인 필요).

### 5.2 업비트 (base: `https://api.upbit.com`)
| 용도 | 엔드포인트 | 비고 |
|---|---|---|
| 마켓 목록 | `GET /v1/market/all` | `korean_name`, `english_name` |
| 현재가 / 호가 | `GET /v1/ticker`, `GET /v1/orderbook` 또는 WebSocket | 복수 마켓 일괄 조회 |
| 입출금 상태 | `GET /v1/status/wallet` | 인증 필요. **수 분 지연될 수 있고 참고용**이라고 문서에 명시됨(확인됨). `net_type`, `network_name` |
| 입금 주소 목록 | `GET /v1/deposits/coin_addresses` | [입금조회] 권한(확인됨) |
| 입금 주소 생성 | `POST /v1/deposits/generate_coin_address` | `currency`, `net_type`. [입금하기] 권한. **비동기**: 첫 응답은 `success/message`만 오고, 완료 후 재호출 시 주소 반환. 이미 있으면 기존 주소 반환(확인됨) |

`net_type`은 식별자, `network_name`은 표시용이다. 매핑 키는 `net_type`을 쓴다. 인증은 문서의 JWT 가이드를 따른다.

### 5.3 빗썸 (base: `https://api.bithumb.com`)
`GET /v1/deposits/coin_addresses`, `POST /v1/deposits/generate_coin_address`, `GET /v1/deposits/coin_address`가 존재한다(확인됨). 마켓 목록·현재가·호가·입출금 상태 API의 경로와 필드, 인증 방식은 구현 시 문서에서 확인한다(확인 필요). 업비트와 네트워크 표기가 다르다(예: 업비트 `ETH`, 빗썸 `Ethereum`).

### 5.4 CoinGecko
현재는 **Crypto API의 Demo 키**를 사용해 거래소 티커·코인 ID·체인별 CA 매핑을 구축한다. On-chain DEX API는 풀 유동성·가격 교차 확인·차트 등 후속 분석 기능 후보이며, 별도 메모 [coingecko-future-features.md](coingecko-future-features.md)에 정리한다. Demo 문서의 기본 URL은 `https://api.coingecko.com/api/v3`, 인증 헤더는 `x-cg-demo-api-key`다. 유료 사용 여부는 D10에 따라 추후 결정한다.

| 용도 | 엔드포인트 | 비고 |
|---|---|---|
| 거래소 id 확인 | `GET /exchanges/list` | `upbit`, `bithumb` id 확인 |
| 거래소 티커 | `GET /exchanges/{id}/tickers?page=N&order=base_target` | 페이지당 100개. 각 티커에 `base`, `target`, `coin_id`, `target_coin_id` (확인됨). `order=base_target`은 페이지 간 중복·누락을 줄인다 |
| 코인별 CA | `GET /coins/list?include_platform=true` | `platforms: {platform_id: address}` (확인 필요) |
| 플랫폼 정보 | `GET /asset_platforms` | platform id의 EVM chain id 제공 여부 확인 후, `chain_map.toml` 자동 채움에 활용(확인 필요) |

Demo(무료) 키 기준으로 호출 간격을 둔다. 레지스트리 빌드는 수십 회 호출이면 끝난다.

---

## 6. 마일스톤

### M0. 골격과 연결 검증
목표: 저장소 골격, 설정 로딩, 모든 외부 API의 인증이 실제로 통하는지 확인.
- `uv` 프로젝트 초기화, 3장의 구조 생성, `.env.example`, `settings.toml` 기본값.
- 어댑터 4종의 최소 구현: OKX 서명 헤더, 업비트·빗썸 JWT, CoinGecko 키 헤더.
- `scripts/smoke_test.py`: 서비스별로 읽기 전용 호출 1회씩(OKX 지원 체인, 업비트 입금 주소 목록, 빗썸 입금 주소 목록, CoinGecko ping) 후 성공/실패 표 출력.

완료 기준
- 스모크 테스트가 4개 서비스 모두 성공을 출력한다.
- 실패 시 원인(권한 부족, IP 미등록, 서명 오류)을 구분해 출력한다.
- 어떤 출력에도 시크릿 값이 나타나지 않는다(테스트로 검증).

### M1. 레지스트리 빌더
목표: `data/registry.json`과 `data/registry_report.md` 생성. 근거는 D1~D4.
1. 업비트·빗썸 마켓 목록 수집(KRW 마켓만 대상, 풀네임 포함).
2. CoinGecko 거래소 티커를 전 페이지 수집해 `티커 → coin_id` 확정.
3. `coins/list?include_platform=true`로 `coin_id → {platform: CA}` 조인.
4. 거래소 입출금 상태 API에서 코인별 `net_type` 수집.
5. `chain_map.toml`로 `net_type ↔ chainIndex ↔ CoinGecko platform id`를 정규화. OKX 지원 체인과 교차.
6. 4.1의 규칙대로 토큰별·거래소별 `status` 판정.
7. OKX basic-info를 배치 호출해 `tokenName`, `decimals`, `communityRecognized`를 채운다. OKX가 모르는 CA는 리포트에 기록하고 제외.
8. `overrides.toml` 적용(매핑 고정, 제외, 네이티브 등록). 매핑 데이터에는 오버라이드가 우선하지만, 브릿지 후보 실행 금지 등 실행 안전 규칙은 우회할 수 없다.

리포트 내용: 분류별 개수(`tradable`/`bridge_candidate`/`excluded`/네이티브), **`chain_map.toml`에 없는 미매핑 `net_type` 목록**, CoinGecko에 `coin_id`가 없는 티커, 같은 티커에 `coin_id`가 둘 이상 걸린 경우, OKX 미인식 CA.

완료 기준
- `uv run scripts/build_registry.py` 한 번으로 두 파일이 생성된다. 재실행 시 결과가 결정적이다(정렬 고정).
- 티커 `AI`가 Gensyn의 CA로 매핑되고 Artificial Inu의 CA가 아님을 테스트로 고정한다.
- 미매핑 `net_type`이 있으면 빌드는 성공하되 리포트 상단에 경고로 나온다.
- fixture 기반 단위 테스트: 다중 체인 코인의 `tradable`/`bridge_candidate` 분리, 네이티브 처리, 오버라이드 우선순위.

### M2. 가격 수집과 표면갭
목표: 레지스트리의 모든 대상 토큰에 대해 표면갭을 주기적으로 계산. 근거는 D5~D7.
- OKX 가격을 배치로 폴링(주기와 배치 크기는 설정값, 레이트리밋에 맞춰 조정).
- 업비트·빗썸 현재가 수집(WebSocket 우선, REST 폴백). 업비트 `KRW-USDT` 호가에서 매도 1호가 수집.
- 4.2의 표면갭 계산, 거래소별로 각각 산출.
- 필터: 갭 상한 초과 → "의심" 목록, 유동성 하한 미달·`communityRecognized=false` → 설정에 따라 숨김 또는 경고 표시.
- 브릿지 후보는 4.3 규칙(코인당 유동성 최대 체인 1개)으로 같은 파이프라인에서 계산.
- 데이터 신선도: 각 가격에 수신 시각을 붙이고, 기준 시간보다 오래되면 갭을 계산하지 않고 "stale"로 표시.

완료 기준
- `GET /api/gaps`가 메인 목록·브릿지 후보·의심 목록을 구분해 반환한다.
- 갭 공식 단위 테스트: 김프 1% 상황에서 실제 환율 기준 1% 갭인 코인이 0%로 계산된다.
- OKX 또는 거래소 한쪽이 끊겨도 프로세스가 죽지 않고 해당 항목만 stale 처리된다.

### M3. 목록 UI
목표: 갭 순위 대시보드.
- 메인 표: 티커, 풀네임(OKX `tokenName` + 업비트 한글명), 체인, DEX 가격, 업비트 갭, 빗썸 갭, 입출금 상태 뱃지. 기본 정렬은 두 거래소 갭 중 큰 값 내림차순.
- 아래에 접이식 섹션 2개: "브릿지 후보"(4.3의 간략 표), "의심 목록"(갭 상한 초과).
- 상단: 업비트 USDT/KRW, 마지막 갱신 시각, DRY_RUN 상태 표시.
- 입금이 중단된 코인은 메인 목록에 남기되 뱃지로 구분한다(네트워크 미지원과는 다른 상태다).

완료 기준
- 브라우저에서 목록이 주기적으로 갱신되고 정렬·접기가 동작한다.
- 같은 티커의 다른 토큰이 목록에 나타나지 않는다(레지스트리 밖의 토큰은 표시 경로가 없음을 코드 구조로 보장).

### M4. 상세 패널(실효갭)
목표: 행을 클릭하면 실효갭과 실행 전 점검 정보를 보여준다. 근거는 D6.
- 투입 금액 입력(기본 1,000 USDT) → OKX `/quote` → CEX 호가창 소진 계산 → 4.2의 실효갭.
- 표시: 예상 수령 수량, 가격 영향(`priceImpactPercent`), 라우팅 요약, 예상 가스비, CEX 평균 매도가, 실효갭(%)과 예상 손익(USDT·KRW, 업비트 USDT 기준 · 거래소 수수료 제외 추정치), 네트워크와 컨펌 수, 입출금 상태.
- 경고: `isHoneyPot`, `taxRate > 0`, 호가 유동성 부족, 입금 중단. 경고가 있으면 스왑 버튼 비활성화.
- 입출금 상태는 패널을 열 때와 스왑 직전에 다시 조회한다(업비트 상태 API는 지연될 수 있으므로 "참고용" 문구를 함께 표시).

완료 기준
- 호가창 소진 계산 단위 테스트(부분 소진, 호가 부족).
- 금액을 바꾸면 실효갭이 다시 계산된다. 견적 실패 시 패널에 원인이 표시된다.

### M5. 스왑 실행
목표: 버튼 한 번으로 USDT → 대상 토큰 스왑. 근거는 D8.

흐름
1. 가드 검사: DRY_RUN 여부, 1회 최대 금액(설정), 지갑 USDT 잔고, 네이티브 가스 잔고, M4의 경고 없음, 레지스트리의 CA와 요청 CA 일치, 선택한 매도 거래소 기준 직접 입금 가능한 `tradable` 토큰인지 여부, 해당 체인의 실행 준비 완료 여부. 브릿지 후보 실행 요청은 서버에서 거부한다.
2. 직전 재견적: 실효갭이 설정한 최소값 아래로 떨어졌으면 중단하고 사용자에게 알린다.
3. allowance 조회(RPC). 부족하면 approve-transaction 데이터를 준비한다. 아래 확인 모달에서 확정하고 DRY_RUN 검사를 통과한 뒤에만 서명·전송하고 컨펌을 기다린다.
   - 이더리움 USDT는 기존 allowance가 0이 아니면 먼저 0으로 approve 해야 변경된다. 이 경우를 처리한다.
   - approve 금액 정책은 설정값(`exact` 또는 `capped`, 기본은 상한 금액 방식)으로 둔다. 매번 approve하면 기회가 사라지므로 상한 방식을 기본으로 하되 무제한은 쓰지 않는다.
4. `/swap`으로 tx 데이터를 받아 가스·nonce를 채우고 로컬 서명 → 자체 RPC로 전송.
5. 영수증 대기 → 성공/revert 판정 → `/history`로 실제 수령 수량 조회.
6. 확인 모달: 실행 전 체인, 풀네임, CA 앞뒤 6자리, 투입 금액, 슬리피지, 예상 수령 수량을 보여주고 사용자가 확정해야 진행한다.

확인 모달은 모든 approve reset·approve·swap 서명/전송보다 먼저 표시한다. DRY_RUN=true면 세 단계 모두 서명·전송하지 않고 계획 요약만 보여준다. (M5 구현 시 순서를 명확히 함; DECISIONS.md 참고)

완료 기준
- DRY_RUN 모드에서 전체 흐름이 끝까지 동작하고 전송이 일어나지 않음을 테스트로 보장한다.
- 가드 각각에 대한 단위 테스트(한도 초과, 잔고 부족, CA 불일치, 경고 존재, 브릿지 후보 실행 요청, 체인 실행 준비 미완료).
- 실제 전송 검증은 사용자가 소액으로 직접 수행한다. 에이전트는 DRY_RUN을 끄지 않는다.
- revert, 타임아웃, nonce 충돌 시 상태가 화면에 명확히 표시되고 재시도가 중복 전송을 만들지 않는다.

### M6. 완료 화면과 입금 주소
목표: 스왑 성공 시 해당 네트워크의 거래소 입금 주소를 복사용으로 표시.
- 주소 준비 정책: 사용자가 종목을 선택할 때 생성하는 방식이 아니라, 거래 시작 전에 업비트·빗썸 각각의 전체 대상 종목·지원 입금 네트워크 주소를 사전 생성한다(사용자 결정). 대상은 레지스트리의 거래소별 `tradable` 항목이며, 브릿지 후보의 직접 입금 불가 체인은 포함하지 않는다.
- `scripts/sync_deposit_addresses.py`: 레지스트리의 `tradable` (거래소, 코인, `net_type`) 전체에 대해 주소 목록을 조회하고, 없는 것은 생성 요청 후 폴링해 `data/deposit_addresses.json`에 캐시한다. 업비트 생성 API는 비동기이므로 간격을 두고 재조회한다. 이미 있는 주소는 다시 만들지 않는다. 생성 대기·실패 항목은 사유와 함께 보고하고 재실행 시 이어서 처리한다.
- 완료 화면: 실제 수령 수량, 거래소별 입금 주소, 네트워크명, 메모·태그(`secondary_address`가 있는 코인만), 복사 버튼, 입금 상태 재조회 결과.
- 주소는 스왑한 체인과 `net_type`이 일치하는 것만 표시한다. 일치하는 주소가 없으면 주소를 비워 두고 사유를 표시한다.
- 브릿지 후보는 실행 대상이 아니므로 스왑 완료 화면으로 진입하지 않는다.
- 복사 후 주소 앞뒤 6자리를 크게 보여줘 붙여넣은 값과 눈으로 대조할 수 있게 한다.

완료 기준
- 동기화 스크립트가 멱등이다(두 번 돌려도 결과 동일, 불필요한 생성 요청 없음).
- 체인-네트워크 불일치 주소가 표시되지 않음을 테스트로 고정한다.

### M7. 운영과 확장
- 신규 상장 감지: 거래소 마켓 목록과 레지스트리를 비교해 새 티커가 있으면 화면에 "레지스트리 재빌드 필요" 알림.
- 구조화 로깅(시크릿 마스킹), 스왑 이력 파일 기록.
- 참고용 김프 표시(실제 환율 소스는 비공식·지연 소스가 많으므로 실패해도 다른 기능에 영향이 없게 격리).
- 선택: Solana 지원(서명·전송 계층 추가), OKX Broadcast/MEV 보호(화이트리스트 승인 후), 다른 DEX 통합.

---

## 7. 열려 있는 확인 항목
구현 중 확인해 `DECISIONS.md`에 결과를 남길 것.
1. OKX 가격 배치의 최대 개수와 레이트리밋, Market API 과금 정책과 실제 예상 호출량. 유료 사용 여부는 D10에 따라 구현 과정 또는 구현 후 검토에서 결정한다.
2. OKX `/quote`·`/swap`의 슬리피지 파라미터명과 단위, 네이티브 토큰 주소 표기, 체인별 spender 주소를 얻는 공식 경로.
3. CoinGecko `coins/list` 응답의 `platforms` 구조, `asset_platforms`의 chain id 필드, Demo 키 호출 한도.
4. 빗썸 API의 인증 방식과 마켓·호가·입출금 상태 엔드포인트의 필드.
5. 업비트·빗썸의 실제 `net_type` 값 목록(M1 리포트로 수집해 `chain_map.toml`을 채운다).

## 8. 참고 문서
- OKX Onchain OS: https://web3.okx.com/onchainos/dev-docs/home/what-is-onchainos
  - 가격: https://web3.okx.com/onchainos/dev-docs/market/market-price
  - 토큰 검색: https://web3.okx.com/onchainos/dev-docs/market/market-token-search
  - 토큰 기본 정보: https://web3.okx.com/onchainos/dev-docs/market/market-token-basic-info
  - EVM 스왑 가이드: https://web3.okx.com/onchainos/dev-docs/trade/dex-use-swap-quick-start
- 업비트: https://docs.upbit.com/kr/reference/get-service-status , https://docs.upbit.com/kr/reference/list-deposit-addresses , https://docs.upbit.com/kr/reference/create-deposit-address
- 빗썸: https://apidocs.bithumb.com
- CoinGecko: https://docs.coingecko.com/reference/exchanges-id-tickers

## 구현 완료 기록 (2026-09-20)

M0~M7 필수 기능과 DRY_RUN 검증을 완료했다. 전체 대상 입금 주소 472개 준비 완료.
실제 소액 스왑은 위 M5 기준대로 사용자가 직접 검증하며 에이전트는 DRY_RUN을 끄지 않았다.
지원 체인·검증 범위·운영 방법은 [사용 안내](docs/m5-m7-usage.md), 완료 근거는 [검증 기록](docs/m5-m7-verification.md)를 참조한다.
